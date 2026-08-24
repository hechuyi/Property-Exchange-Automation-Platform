"""Low-rate public-resource deal supplement collector.

Reads listing records from the local streaming SQLite database in read-only
mode, searches the national public resource trading disclosure page for
state-owned asset transaction results, and writes candidate supplement files.

This script intentionally does not write to the PEAP database.
Search collection is deliberately limited to the first response page; the
script does not advertise or silently emulate pagination beyond that page.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import hashlib
import io
import json
import random
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from desktop_backend.app_config import AppConfig

try:
    import pandas as pd
except Exception:  # pragma: no cover - optional final workbook convenience
    pd = None  # type: ignore[assignment]


BASE_URL = "https://www.ggzy.gov.cn"
LIST_URL = f"{BASE_URL}/deal/dealList.html"
SEARCH_URL = f"{BASE_URL}/information/pubTradingInfo/getTradList"
DEFAULT_STATES = ("ready", "pending_mapping", "mapping_conflict", "conflict")

ATTEMPT_FIELDS = [
    "run_id",
    "attempted_at",
    "record_id",
    "record_state",
    "exchange",
    "business_id",
    "project_code",
    "project_name",
    "listing_date",
    "search_keyword",
    "search_time_begin",
    "search_time_end",
    "status",
    "http_status",
    "result_total",
    "result_count",
    "matched_count",
    "error_type",
    "error_message",
]

MATCH_FIELDS = [
    "run_id",
    "matched_at",
    "record_id",
    "record_state",
    "exchange",
    "business_id",
    "internal_project_code",
    "internal_project_name",
    "listing_date",
    "search_keyword",
    "match_rank",
    "match_rule",
    "match_confidence",
    "official_title",
    "official_publish_time",
    "official_source_platform",
    "official_business_type",
    "official_information_type",
    "official_province",
    "official_list_url",
    "official_outer_url",
    "official_iframe_url",
    "official_original_link",
    "official_project_code",
    "official_project_name",
    "trade_mode",
    "buyer_name",
    "valuation",
    "deal_amount",
    "deal_date",
    "outer_html_sha256",
    "inner_html_sha256",
    "outer_html_path",
    "inner_html_path",
    "detail_error_type",
    "detail_error_message",
]


class StopRun(RuntimeError):
    pass


def _default_streaming_db_path() -> str:
    return AppConfig.from_env(
        ensure_dirs=False,
        migrate_legacy=False,
    ).STREAMING_DB_PATH


@dataclass(frozen=True)
class ListingCandidate:
    record_id: str
    state: str
    exchange: str
    business_id: str
    project_code: str
    project_name: str
    listing_date: str


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _normalize_for_match(value: object) -> str:
    text = _clean_text(value)
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text).casefold()


def _safe_filename(value: object, *, max_len: int = 120) -> str:
    text = _clean_text(value) or "unnamed"
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", text)
    text = text.strip(" ._")
    if len(text) > max_len:
        text = text[:max_len].rstrip(" ._")
    return text or "unnamed"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _evidence_sidecar_path(evidence_path: Path) -> Path:
    if evidence_path.suffix.lower() == ".html":
        return evidence_path.with_suffix(".json")
    return Path(str(evidence_path) + ".sidecar.json")


def _is_public_resource_evidence_path(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() == ".html":
        return True
    if path.name.endswith(".sidecar.json"):
        return False
    if path.suffix.lower() == ".json" and path.name.endswith("-search-response.json"):
        return True
    return False


def write_public_resource_evidence_response(
    path: Path,
    body: str,
    *,
    source_url: str,
    http_status: int,
    evidence_role: str,
    run_id: str = "",
    media_type: str = "application/json",
    request_method: str = "",
    request_params: dict[str, str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    encoded = body.encode("utf-8")
    payload = {
        "source_id": "public_resource",
        "record_family": "deal",
        "business_id": "deal_equity_transfer",
        "task_id": "public_resource:deal:deal_equity_transfer",
        "evidence_role": str(evidence_role or ""),
        "run_id": str(run_id or ""),
        "source_url": str(source_url or ""),
        "media_type": str(media_type or ""),
        "http_status": int(http_status or 0),
        "save_status": "complete",
        "archive_content_sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "archive_content_bytes": len(encoded),
        "collected_at": _now(),
    }
    if request_method:
        payload["request_method"] = str(request_method)
    if request_params is not None:
        payload["request_params"] = {str(key): str(value) for key, value in request_params.items()}
    _evidence_sidecar_path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_public_resource_evidence_html(
    path: Path,
    html: str,
    *,
    source_url: str,
    http_status: int,
    evidence_role: str,
    run_id: str = "",
) -> None:
    write_public_resource_evidence_response(
        path,
        html,
        source_url=source_url,
        http_status=http_status,
        evidence_role=evidence_role,
        run_id=run_id,
        media_type="text/html",
    )


def audit_public_resource_evidence_dir(root: Path) -> dict[str, object]:
    root = Path(root)
    issues: list[dict[str, object]] = []
    evidence_paths = sorted(path for path in root.rglob("*") if _is_public_resource_evidence_path(path))
    html_paths = [path for path in evidence_paths if path.suffix.lower() == ".html"]
    json_paths = [path for path in evidence_paths if path.suffix.lower() == ".json"]
    sidecar_count = 0
    required_fields = {
        "source_id": "public_resource",
        "record_family": "deal",
        "business_id": "deal_equity_transfer",
        "task_id": "public_resource:deal:deal_equity_transfer",
    }
    nonempty_fields = ("evidence_role", "source_url", "media_type")
    for evidence_path in evidence_paths:
        sidecar_path = _evidence_sidecar_path(evidence_path)
        if not sidecar_path.is_file():
            issues.append(
                {
                    "code": "missing_sidecar",
                    "path": str(evidence_path),
                    "message": "public resource evidence is missing sidecar",
                }
            )
            continue
        sidecar_count += 1
        try:
            payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            issues.append(
                {
                    "code": "invalid_sidecar_json",
                    "path": str(sidecar_path),
                    "message": str(exc),
                }
            )
            continue
        if not isinstance(payload, dict):
            issues.append(
                {
                    "code": "invalid_sidecar_json",
                    "path": str(sidecar_path),
                    "message": "sidecar payload must be an object",
                }
            )
            continue
        raw_bytes = evidence_path.read_bytes()
        expected_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
        expected_bytes = len(raw_bytes)
        for field, expected_value in required_fields.items():
            if str(payload.get(field) or "") != expected_value:
                issues.append(
                    {
                        "code": "missing_required_sidecar_field",
                        "path": str(sidecar_path),
                        "field": field,
                        "message": f"sidecar {field} must be {expected_value}",
                    }
                )
        for field in nonempty_fields:
            if payload.get(field) in (None, ""):
                issues.append(
                    {
                        "code": "missing_required_sidecar_field",
                        "path": str(sidecar_path),
                        "field": field,
                        "message": f"sidecar {field} is required",
                    }
                )
        try:
            http_status = int(payload.get("http_status"))
        except (TypeError, ValueError):
            http_status = 0
        if http_status < 100 or http_status > 599:
            issues.append(
                {
                    "code": "invalid_http_status",
                    "path": str(sidecar_path),
                    "field": "http_status",
                    "message": "sidecar http_status must be an HTTP status code",
                }
            )
        if str(payload.get("save_status") or "") != "complete":
            issues.append(
                {
                    "code": "incomplete_save_status",
                    "path": str(sidecar_path),
                    "message": "sidecar save_status must be complete",
                }
            )
        if str(payload.get("archive_content_sha256") or "") != expected_hash:
            issues.append(
                {
                    "code": "archive_hash_mismatch",
                    "path": str(sidecar_path),
                    "message": "sidecar archive_content_sha256 does not match evidence bytes",
                }
            )
        raw_archive_bytes = payload.get("archive_content_bytes")
        try:
            actual_archive_bytes = (
                int(raw_archive_bytes)
                if not isinstance(raw_archive_bytes, bool)
                else -1
            )
        except (TypeError, ValueError, OverflowError):
            actual_archive_bytes = -1
        if actual_archive_bytes != expected_bytes:
            issues.append(
                {
                    "code": "archive_size_mismatch",
                    "path": str(sidecar_path),
                    "field": "archive_content_bytes",
                    "actual": raw_archive_bytes,
                    "expected": expected_bytes,
                    "message": "sidecar archive_content_bytes does not match evidence bytes",
                }
            )
    return {
        "ok": not issues,
        "evidence_count": len(evidence_paths),
        "html_count": len(html_paths),
        "json_count": len(json_paths),
        "sidecar_count": sidecar_count,
        "issue_count": len(issues),
        "issues": issues,
    }


def _today() -> str:
    return dt.date.today().isoformat()


def _one_year_ago_plus_one() -> str:
    return (dt.date.today() - dt.timedelta(days=364)).isoformat()


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _open_db_readonly(db_path: str) -> sqlite3.Connection:
    resolved = Path(db_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"database not found: {resolved}")
    conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _split_csv_arg(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def load_candidates(
    *,
    db_path: str,
    states: list[str],
    business_ids: list[str],
    exchanges: list[str],
    include_existing_deals: bool,
    limit: int,
    order: str,
) -> list[ListingCandidate]:
    where = [
        "r.record_family = 'listing'",
        "r.project_code <> ''",
        "r.project_name <> ''",
    ]
    params: list[Any] = []
    if states:
        where.append(f"r.state IN ({','.join('?' for _ in states)})")
        params.extend(states)
    if business_ids:
        where.append(f"r.business_id IN ({','.join('?' for _ in business_ids)})")
        params.extend(business_ids)
    if exchanges:
        where.append(f"r.exchange IN ({','.join('?' for _ in exchanges)})")
        params.extend(exchanges)
    if not include_existing_deals:
        where.append(
            """
            NOT EXISTS (
                SELECT 1
                FROM records d
                WHERE d.record_family = 'deal'
                  AND d.project_code = r.project_code
            )
            """
        )
    limit_sql = "LIMIT ?" if limit > 0 else ""
    if limit > 0:
        params.append(limit)
    sort_direction = "ASC" if str(order or "").strip().lower() == "asc" else "DESC"
    sql = f"""
        SELECT
            r.record_id,
            r.state,
            r.exchange,
            r.business_id,
            r.project_code,
            r.project_name,
            r.listing_date
        FROM records r
        WHERE {' AND '.join(where)}
        ORDER BY
            COALESCE(NULLIF(r.listing_date, ''), '9999-99-99') {sort_direction},
            r.exchange,
            r.business_id,
            r.project_code
        {limit_sql}
    """
    with _open_db_readonly(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        ListingCandidate(
            record_id=str(row["record_id"] or ""),
            state=str(row["state"] or ""),
            exchange=str(row["exchange"] or ""),
            business_id=str(row["business_id"] or ""),
            project_code=str(row["project_code"] or ""),
            project_name=str(row["project_name"] or ""),
            listing_date=str(row["listing_date"] or ""),
        )
        for row in rows
    ]


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        ),
        "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        "Connection": "close",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": BASE_URL,
        "Referer": LIST_URL,
    }


async def _request_via_browser_transport_async(
    url: str,
    *,
    data: dict[str, str] | None,
    timeout: int,
) -> tuple[int, str]:
    timeout_ms = max(1, int(timeout)) * 1000
    async with async_playwright() as pw:
        request_context = await pw.request.new_context(
            extra_http_headers=_request_headers(),
        )
        try:
            try:
                if data is None:
                    response = await request_context.get(url, timeout=timeout_ms)
                else:
                    response = await request_context.post(
                        url,
                        form=data,
                        timeout=timeout_ms,
                    )
            except PlaywrightTimeoutError as exc:
                # Normalize Playwright's distinct timeout class for collector error accounting.
                raise TimeoutError(str(exc)) from exc
            status = int(response.status)
            text = await response.text()
            if status >= 400:
                # Match urllib: callers must see HTTP errors, including rate limits.
                raise HTTPError(
                    url,
                    status,
                    f"HTTP Error {status}",
                    hdrs=None,
                    fp=io.BytesIO(text.encode("utf-8", "replace")),
                )
            return status, text
        finally:
            await request_context.dispose()


def _request_via_browser_transport(
    url: str,
    *,
    data: dict[str, str] | None,
    timeout: int,
) -> tuple[int, str]:
    return asyncio.run(
        _request_via_browser_transport_async(
            url,
            data=data,
            timeout=timeout,
        )
    )


def _request(
    url: str,
    *,
    data: dict[str, str] | None = None,
    timeout: int,
) -> tuple[int, str]:
    encoded = urlencode(data or {}).encode("utf-8") if data is not None else None
    request = Request(
        url,
        data=encoded,
        headers=_request_headers(),
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            encoding = response.headers.get_content_charset() or "utf-8"
            return int(response.status), raw.decode(encoding, "replace")
    except HTTPError:
        # HTTP status responses are authoritative; do not hide them behind a transport retry.
        raise
    except (URLError, TimeoutError, OSError):
        # ssl.SSLError is an OSError subclass; retry only transport-layer failures.
        return _request_via_browser_transport(url, data=data, timeout=timeout)


def _public_resource_search_params(
    *,
    keyword: str,
    time_begin: str,
    time_end: str,
    page_number: int,
) -> dict[str, str]:
    return {
        "SOURCE_TYPE": "1",
        "DEAL_TIME": "06",
        "TIMEBEGIN": time_begin,
        "TIMEEND": time_end,
        "DEAL_CLASSIFY": "05",
        "DEAL_STAGE": "0502",
        "FINDTXT": keyword,
        "PAGENUMBER": str(page_number),
    }


def fetch_public_resource_search_response(
    *,
    keyword: str,
    time_begin: str,
    time_end: str,
    page_number: int,
    timeout: int,
) -> tuple[int, str, dict[str, str]]:
    params = _public_resource_search_params(
        keyword=keyword,
        time_begin=time_begin,
        time_end=time_end,
        page_number=page_number,
    )
    status, text = _request(
        SEARCH_URL,
        data=params,
        timeout=timeout,
    )
    return status, text, params


def search_public_resource(
    *,
    keyword: str,
    time_begin: str,
    time_end: str,
    page_number: int,
    timeout: int,
) -> tuple[int, dict[str, Any], str, dict[str, str]]:
    status, text, params = fetch_public_resource_search_response(
        keyword=keyword,
        time_begin=time_begin,
        time_end=time_end,
        page_number=page_number,
        timeout=timeout,
    )
    return status, json.loads(text), text, params


def _extract_table_rows(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    rows: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        cells = [_clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
        if len(cells) >= 2 and cells[0]:
            rows[cells[0]] = cells[1]
    return rows


def _extract_original_link(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        text = _clean_text(anchor.get_text(" ", strip=True))
        href = _clean_text(anchor.get("href"))
        if not href:
            continue
        if "原文" in text or href.startswith("http"):
            return urljoin(base_url, href)
    return ""


def _extract_next_detail_url(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    iframe = soup.find("iframe")
    if iframe is not None:
        src = _clean_text(iframe.get("src"))
        if src:
            return urljoin(base_url, src)
    patterns = [
        r"firstLastUrl\s*=\s*['\"]([^'\"]+)['\"]",
        r"showDetail\([^)]*['\"](/information/deal/html/b/[^'\"]+)['\"]",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, html)
        for match in matches:
            text = _clean_text(match)
            if "/0502/" in text and text:
                return urljoin(base_url, text)
        if matches:
            return urljoin(base_url, _clean_text(matches[-1]))
    return ""


def fetch_detail(
    *,
    relative_url: str,
    evidence_dir: Path,
    candidate: ListingCandidate,
    match_index: int,
    timeout: int,
    run_id: str = "",
) -> dict[str, str]:
    outer_url = urljoin(BASE_URL, relative_url)
    stem = (
        f"{_safe_filename(candidate.exchange, max_len=20)}-"
        f"{_safe_filename(candidate.project_code, max_len=50)}-"
        f"{match_index:02d}"
    )
    status, outer_html = _request(outer_url, timeout=timeout)
    outer_sha = _sha256_text(outer_html)
    outer_path = evidence_dir / f"{stem}-outer.html"
    write_public_resource_evidence_html(
        outer_path,
        outer_html,
        source_url=outer_url,
        http_status=status,
        evidence_role="outer_detail",
        run_id=run_id,
    )

    if status != 200:
        raise RuntimeError(f"outer detail http status {status}: {outer_url}")

    inner_html = ""
    inner_sha = ""
    inner_url = _extract_next_detail_url(outer_html, outer_url)
    inner_path = evidence_dir / f"{stem}-inner.html"
    rows: dict[str, str] = {}
    original_link = ""
    seen_urls = {outer_url}
    for depth in range(1, 4):
        if not inner_url or inner_url in seen_urls:
            break
        seen_urls.add(inner_url)
        inner_status, candidate_html = _request(inner_url, timeout=timeout)
        if inner_status != 200:
            raise RuntimeError(f"inner detail http status {inner_status}: {inner_url}")
        candidate_rows = _extract_table_rows(candidate_html)
        if candidate_rows:
            inner_html = candidate_html
            inner_sha = _sha256_text(inner_html)
            write_public_resource_evidence_html(
                inner_path,
                inner_html,
                source_url=inner_url,
                http_status=inner_status,
                evidence_role="inner_detail",
                run_id=run_id,
            )
            rows = candidate_rows
            original_link = _extract_original_link(inner_html, inner_url)
            break
        container_path = evidence_dir / f"{stem}-container-{depth}.html"
        write_public_resource_evidence_html(
            container_path,
            candidate_html,
            source_url=inner_url,
            http_status=inner_status,
            evidence_role="container_detail",
            run_id=run_id,
        )
        next_url = _extract_next_detail_url(candidate_html, inner_url)
        if not next_url:
            inner_html = candidate_html
            inner_sha = _sha256_text(inner_html)
            write_public_resource_evidence_html(
                inner_path,
                inner_html,
                source_url=inner_url,
                http_status=inner_status,
                evidence_role="inner_detail",
                run_id=run_id,
            )
            original_link = _extract_original_link(inner_html, inner_url)
            break
        inner_url = next_url

    return {
        "official_outer_url": outer_url,
        "official_iframe_url": inner_url,
        "official_original_link": original_link,
        "official_project_code": rows.get("项目编号", ""),
        "official_project_name": rows.get("项目名称", ""),
        "trade_mode": rows.get("交易方式", ""),
        "buyer_name": rows.get("受让方名称", ""),
        "valuation": rows.get("转让标的评估值或账面净值", ""),
        "deal_amount": rows.get("成交金额", ""),
        "deal_date": rows.get("成交日期", ""),
        "outer_html_sha256": outer_sha,
        "inner_html_sha256": inner_sha,
        "outer_html_path": str(outer_path),
        "inner_html_path": str(inner_path if inner_html else ""),
    }


def _match_rule(candidate: ListingCandidate, record: dict[str, Any]) -> tuple[str, str]:
    expected = _normalize_for_match(candidate.project_name)
    title = _normalize_for_match(record.get("title"))
    if title == expected:
        return "exact_title", "1.00"
    if expected and (expected in title or title in expected):
        return "title_contains", "0.82"
    if candidate.project_code and candidate.project_code in _clean_text(record.get("title")):
        return "code_in_title", "0.75"
    return "keyword_result", "0.50"


def _ensure_csv(path: Path, fields: list[str]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()


def _append_csv(path: Path, fields: list[str], row: dict[str, object]) -> None:
    with path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writerow({field: row.get(field, "") for field in fields})
        handle.flush()


def _load_attempted_record_ids(path: Path, *, retry_errors: bool = False) -> set[str]:
    if not path.exists():
        return set()
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return {
            str(row.get("record_id") or "")
            for row in csv.DictReader(handle)
            if str(row.get("record_id") or "").strip()
            and not (retry_errors and str(row.get("status") or "") == "error")
        }


def _write_workbook(output_dir: Path) -> str:
    if pd is None:
        return ""
    attempts_path = output_dir / "attempts.csv"
    matches_path = output_dir / "matches.csv"
    workbook_path = output_dir / "public_resource_deal_supplement.xlsx"
    attempts = pd.read_csv(attempts_path, dtype=str).fillna("") if attempts_path.exists() else pd.DataFrame()
    matches = pd.read_csv(matches_path, dtype=str).fillna("") if matches_path.exists() else pd.DataFrame()
    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        matches.to_excel(writer, index=False, sheet_name="matches")
        attempts.to_excel(writer, index=False, sheet_name="attempts")
    return str(workbook_path)


def rebuild_matches_from_existing(output_dir: Path, *, timeout: int, delay: float) -> dict[str, object]:
    matches_path = output_dir / "matches.csv"
    if not matches_path.exists():
        raise FileNotFoundError(f"matches csv not found: {matches_path}")

    with matches_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    rebuilt_rows: list[dict[str, object]] = []
    refetched = 0
    filled = 0
    for row in rows:
        rebuilt = dict(row)
        if _clean_text(rebuilt.get("deal_amount")) and _clean_text(rebuilt.get("deal_date")):
            rebuilt_rows.append(rebuilt)
            continue

        outer_path = Path(str(rebuilt.get("outer_html_path") or ""))
        outer_url = str(rebuilt.get("official_outer_url") or "")
        if not outer_path.is_file() or not outer_url:
            rebuilt["detail_error_type"] = "MissingOuterEvidence"
            rebuilt["detail_error_message"] = f"outer evidence missing: {outer_path}"
            rebuilt_rows.append(rebuilt)
            continue

        current_url = _extract_next_detail_url(
            outer_path.read_text(encoding="utf-8", errors="replace"),
            outer_url,
        )
        seen_urls = {outer_url}
        detail_html = ""
        detail_url = current_url
        detail_status = 0
        detail_rows: dict[str, str] = {}
        try:
            for _depth in range(1, 4):
                if not current_url or current_url in seen_urls:
                    break
                seen_urls.add(current_url)
                time.sleep(delay)
                current_status, current_html = _request(current_url, timeout=timeout)
                detail_status = current_status
                refetched += 1
                rows_found = _extract_table_rows(current_html)
                if rows_found:
                    detail_html = current_html
                    detail_url = current_url
                    detail_rows = rows_found
                    break
                next_url = _extract_next_detail_url(current_html, current_url)
                if not next_url:
                    detail_html = current_html
                    detail_url = current_url
                    break
                current_url = next_url

            if detail_html:
                inner_path = Path(str(rebuilt.get("inner_html_path") or ""))
                if inner_path:
                    write_public_resource_evidence_html(
                        inner_path,
                        detail_html,
                        source_url=detail_url,
                        http_status=detail_status,
                        evidence_role="inner_detail_rebuild",
                        run_id=str(rebuilt.get("run_id") or ""),
                    )
                rebuilt.update(
                    {
                        "official_iframe_url": detail_url,
                        "official_original_link": _extract_original_link(detail_html, detail_url),
                        "official_project_code": detail_rows.get("项目编号", ""),
                        "official_project_name": detail_rows.get("项目名称", ""),
                        "trade_mode": detail_rows.get("交易方式", ""),
                        "buyer_name": detail_rows.get("受让方名称", ""),
                        "valuation": detail_rows.get("转让标的评估值或账面净值", ""),
                        "deal_amount": detail_rows.get("成交金额", ""),
                        "deal_date": detail_rows.get("成交日期", ""),
                        "inner_html_sha256": _sha256_text(detail_html),
                        "detail_error_type": "",
                        "detail_error_message": "",
                    }
                )
                if _clean_text(rebuilt.get("deal_amount")) or _clean_text(rebuilt.get("deal_date")):
                    filled += 1
            else:
                rebuilt["detail_error_type"] = "DetailTableNotFound"
                rebuilt["detail_error_message"] = "could not resolve table detail page"
        except Exception as exc:
            rebuilt["detail_error_type"] = exc.__class__.__name__
            rebuilt["detail_error_message"] = str(exc)
        rebuilt_rows.append(rebuilt)

    backup_path = matches_path.with_suffix(".before_rebuild.csv")
    if not backup_path.exists():
        backup_path.write_text(matches_path.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
    with matches_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATCH_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rebuilt_rows:
            writer.writerow({field: row.get(field, "") for field in MATCH_FIELDS})

    workbook_path = _write_workbook(output_dir)
    evidence_audit = audit_public_resource_evidence_dir(output_dir)
    return {
        "matches": len(rebuilt_rows),
        "refetched": refetched,
        "filled": filled,
        "workbook": workbook_path,
        "evidence_audit": evidence_audit,
    }


def _sleep_between(min_delay: float, max_delay: float) -> None:
    delay = random.uniform(min_delay, max_delay)
    time.sleep(delay)


def collect(args: argparse.Namespace) -> dict[str, object]:
    run_id = args.run_id or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir or Path("output") / "public_resource_deal_supplement" / run_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = output_dir / "evidence_html"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    attempts_path = output_dir / "attempts.csv"
    matches_path = output_dir / "matches.csv"
    _ensure_csv(attempts_path, ATTEMPT_FIELDS)
    _ensure_csv(matches_path, MATCH_FIELDS)

    business_ids = _split_csv_arg(args.business_ids)
    exchanges = _split_csv_arg(args.exchanges)
    candidates = load_candidates(
        db_path=args.db,
        states=_split_csv_arg(args.states),
        business_ids=business_ids,
        exchanges=exchanges,
        include_existing_deals=args.include_existing_deals,
        limit=args.limit,
        order=args.order,
    )
    attempted = _load_attempted_record_ids(attempts_path, retry_errors=args.retry_errors) if args.resume else set()

    processed = 0
    skipped_resume = 0
    matched_rows = 0
    consecutive_errors = 0
    started_at = time.time()

    for index, candidate in enumerate(candidates, start=1):
        if candidate.record_id in attempted:
            skipped_resume += 1
            continue

        if processed > 0:
            _sleep_between(args.min_delay, args.max_delay)

        attempted_at = _now()
        attempt_row: dict[str, object] = {
            "run_id": run_id,
            "attempted_at": attempted_at,
            "record_id": candidate.record_id,
            "record_state": candidate.state,
            "exchange": candidate.exchange,
            "business_id": candidate.business_id,
            "project_code": candidate.project_code,
            "project_name": candidate.project_name,
            "listing_date": candidate.listing_date,
            "search_keyword": candidate.project_name,
            "search_time_begin": args.time_begin,
            "search_time_end": args.time_end,
            "status": "",
            "http_status": "",
            "result_total": "",
            "result_count": "",
            "matched_count": "",
            "error_type": "",
            "error_message": "",
        }
        try:
            http_status, raw_search_response, search_params = fetch_public_resource_search_response(
                keyword=candidate.project_name,
                time_begin=args.time_begin,
                time_end=args.time_end,
                page_number=1,
                timeout=args.timeout,
            )
            search_stem = (
                f"{_safe_filename(candidate.exchange, max_len=20)}-"
                f"{_safe_filename(candidate.project_code, max_len=50)}-"
                f"{index:04d}-search-response.json"
            )
            write_public_resource_evidence_response(
                evidence_dir / search_stem,
                raw_search_response,
                source_url=SEARCH_URL,
                http_status=http_status,
                evidence_role="search_result",
                run_id=run_id,
                media_type="application/json",
                request_method="POST",
                request_params=search_params,
            )
            payload = json.loads(raw_search_response)
            if http_status in {403, 429}:
                raise StopRun(f"server returned {http_status}; stopping to avoid rate-limit escalation")
            if int(payload.get("code") or 0) != 200:
                raise RuntimeError(f"search code={payload.get('code')} message={payload.get('message')}")
            data = payload.get("data") or {}
            records = list(data.get("records") or [])
            try:
                result_total = int(data.get("total") or len(records))
            except (TypeError, ValueError):
                result_total = len(records)
            match_count = 0
            for match_index, record in enumerate(records, start=1):
                rule, confidence = _match_rule(candidate, record)
                if args.only_exact and rule != "exact_title":
                    continue
                detail: dict[str, str] = {}
                detail_error_type = ""
                detail_error_message = ""
                if record.get("url"):
                    if match_count > 0:
                        _sleep_between(args.detail_min_delay, args.detail_max_delay)
                    try:
                        detail = fetch_detail(
                            relative_url=str(record.get("url") or ""),
                            evidence_dir=evidence_dir,
                            candidate=candidate,
                            match_index=match_index,
                            timeout=args.timeout,
                            run_id=run_id,
                        )
                    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
                        detail_error_type = exc.__class__.__name__
                        detail_error_message = str(exc)
                match_count += 1
                matched_rows += 1
                _append_csv(
                    matches_path,
                    MATCH_FIELDS,
                    {
                        "run_id": run_id,
                        "matched_at": _now(),
                        "record_id": candidate.record_id,
                        "record_state": candidate.state,
                        "exchange": candidate.exchange,
                        "business_id": candidate.business_id,
                        "internal_project_code": candidate.project_code,
                        "internal_project_name": candidate.project_name,
                        "listing_date": candidate.listing_date,
                        "search_keyword": candidate.project_name,
                        "match_rank": match_index,
                        "match_rule": rule,
                        "match_confidence": confidence,
                        "official_title": record.get("title", ""),
                        "official_publish_time": record.get("publishTime", ""),
                        "official_source_platform": record.get("transactionSourcesPlatformText", ""),
                        "official_business_type": record.get("businessTypeText", ""),
                        "official_information_type": record.get("informationTypeText", ""),
                        "official_province": record.get("provinceText", ""),
                        "official_list_url": LIST_URL,
                        "detail_error_type": detail_error_type,
                        "detail_error_message": detail_error_message,
                        **detail,
                    },
                )

            attempt_row.update(
                {
                    "status": "matched" if match_count else "not_found",
                    "http_status": http_status,
                        "result_total": result_total,
                    "result_count": len(records),
                    "matched_count": match_count,
                }
            )
            consecutive_errors = 0
        except StopRun:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError) as exc:
            consecutive_errors += 1
            if isinstance(exc, HTTPError) and getattr(exc, "fp", None) is not None:
                try:
                    error_body = exc.read().decode("utf-8", "replace")
                    error_stem = (
                        f"{_safe_filename(candidate.exchange, max_len=20)}-"
                        f"{_safe_filename(candidate.project_code, max_len=50)}-"
                        f"{index:04d}-search-response.json"
                    )
                    write_public_resource_evidence_response(
                        evidence_dir / error_stem,
                        error_body,
                        source_url=SEARCH_URL,
                        http_status=int(exc.code),
                        evidence_role="search_http_error",
                        run_id=run_id,
                        media_type="application/json",
                        request_method="POST",
                        request_params=_public_resource_search_params(
                            keyword=candidate.project_name,
                            time_begin=args.time_begin,
                            time_end=args.time_end,
                            page_number=1,
                        ),
                    )
                except (OSError, UnicodeError):
                    pass
            attempt_row.update(
                {
                    "status": "error",
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                }
            )
            if isinstance(exc, HTTPError) and exc.code in {403, 429}:
                raise StopRun(f"server returned {exc.code}; stopping to avoid rate-limit escalation") from exc
            if consecutive_errors >= args.max_consecutive_errors:
                raise StopRun(
                    f"stopping after {consecutive_errors} consecutive errors; last={exc!r}"
                ) from exc
        finally:
            if attempt_row.get("status"):
                _append_csv(attempts_path, ATTEMPT_FIELDS, attempt_row)
                processed += 1
                if processed % args.progress_every == 0:
                    elapsed = int(time.time() - started_at)
                    print(
                        f"progress processed={processed} skipped_resume={skipped_resume} "
                        f"matches={matched_rows} current={index}/{len(candidates)} elapsed={elapsed}s",
                        flush=True,
                    )

    workbook_path = _write_workbook(output_dir)
    evidence_audit = audit_public_resource_evidence_dir(evidence_dir)
    return {
        "run_id": run_id,
        "output_dir": str(output_dir),
        "attempts_csv": str(attempts_path),
        "matches_csv": str(matches_path),
        "workbook": workbook_path,
        "candidates_loaded": len(candidates),
        "processed": processed,
        "skipped_resume": skipped_resume,
        "matched_rows": matched_rows,
        "evidence_audit": evidence_audit,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=_default_streaming_db_path(),
        help="Streaming SQLite DB path, opened read-only",
    )
    parser.add_argument("--output-dir", default="", help="Output directory; defaults under output/")
    parser.add_argument("--run-id", default="", help="Run id used when output-dir is omitted")
    parser.add_argument("--states", default=",".join(DEFAULT_STATES))
    parser.add_argument("--business-ids", default="", help="Comma-separated business ids; empty means all listing business ids")
    parser.add_argument("--exchanges", default="", help="Comma-separated exchange labels; empty means all")
    parser.add_argument("--time-begin", default=_one_year_ago_plus_one())
    parser.add_argument("--time-end", default=_today())
    parser.add_argument("--limit", type=int, default=0, help="0 means no candidate limit")
    parser.add_argument("--order", choices=["asc", "desc"], default="asc", help="listing_date processing order")
    parser.add_argument(
        "--missing-only",
        dest="include_existing_deals",
        action="store_false",
        help="Skip listings whose project_code already has a local deal record",
    )
    parser.set_defaults(include_existing_deals=True)
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.set_defaults(resume=True)
    parser.add_argument("--retry-errors", action="store_true", help="Do not treat previous error attempts as completed")
    parser.add_argument("--only-exact", action="store_true", help="Only write exact title matches")
    parser.add_argument("--min-delay", type=float, default=3.0)
    parser.add_argument("--max-delay", type=float, default=6.0)
    parser.add_argument("--detail-min-delay", type=float, default=1.0)
    parser.add_argument("--detail-max-delay", type=float, default=3.0)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--max-consecutive-errors", type=int, default=3)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--rebuild-existing", action="store_true", help="Rebuild existing match detail fields")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rebuild_existing:
        run_id = args.run_id or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = Path(args.output_dir or Path("output") / "public_resource_deal_supplement" / run_id)
        summary = rebuild_matches_from_existing(
            output_dir,
            timeout=args.timeout,
            delay=args.detail_min_delay,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        audit = summary.get("evidence_audit")
        return 0 if not isinstance(audit, dict) or bool(audit.get("ok")) else 1
    try:
        summary = collect(args)
    except StopRun as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    audit = summary.get("evidence_audit")
    return 0 if not isinstance(audit, dict) or bool(audit.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
