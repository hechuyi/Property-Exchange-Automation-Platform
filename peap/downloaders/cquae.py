"""Chongqing exchange downloader."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import http.client
import json
import logging
import math
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from bs4 import BeautifulSoup

from peap_core.source_business_contract import get_source_business_requirement

from ..browser_runtime import launch_chromium_browser
from ..constants import (
    TYPE_CAPITAL_INCREASE,
    TYPE_EQUITY_TRANSFER,
    TYPE_PHYSICAL_ASSET,
    TYPE_PRE_DISCLOSURE,
)
from ..download_errors import (
    execute_failed_error,
    invalid_candidate_error,
    list_failed_error,
    save_failed_error,
)
from ..submission_layout import resolve_submission_snapshot_target
from .common import (
    DetailUnavailableError,
    DownloadSummary,
    HttpFetchedText,
    ProgressLogThrottle,
    archive_integrity_fields,
    business_id_key,
    complete_resume_sidecar_exists,
    detail_accounted_count,
    in_date_range,
    mark_artifact_save_failed,
    parse_bound,
    parse_loose_date,
    record_downloaded_target,
    reserve_download_target,
    runtime_task_id,
    successful_http_evidence,
)
from .discovery_evidence import DiscoveryEvidenceError, DiscoveryTaskEvidence
from .snapshot_utils import SnapshotSaver, remove_snapshot

BASE_URL = "https://www.cquae.com"
CQUAE_PROJECT_PAGE_API_URL = f"{BASE_URL}/api/trade/project/projectPageQuery"

REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
}
CQUAE_PHYSICAL_ASSET_PRICE_BUCKETS = get_source_business_requirement(
    "cquae",
    "listing",
    "physical_asset",
).required_query_filters["price"]

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
window.chrome = window.chrome || { runtime: {} };
"""

DISCLOSURE_START_PATTERNS = (
    r"(?:\u4fe1\u606f\u62ab\u9732\u8d77\u59cb\u65e5\u671f|\u6302\u724c\u5f00\u59cb\u65e5\u671f|\u6302\u724c\u8d77\u59cb\u65e5\u671f|\u62ab\u9732\u5f00\u59cb\u65e5\u671f|\u62ab\u9732\u8d77\u6b62\u65e5\u671f|\u6302\u724c\u8d77\u6b62\u65e5\u671f)\s*[:\uff1a]?\s*(20\d{2}[\u5e74./-]\d{1,2}[\u6708./-]\d{1,2}\u65e5?)",
    r"(20\d{2}[\u5e74./-]\d{1,2}[\u6708./-]\d{1,2}\u65e5?)\s*(?:\u81f3|\u5230|-|\u2014|~|\uff5e)\s*20\d{2}[\u5e74./-]\d{1,2}[\u6708./-]\d{1,2}\u65e5?",
)
LIST_START_LABEL_RE = re.compile(
    r"\u6302\u724c\u5f00\u59cb\u65e5\u671f[:\uff1a]?\s*(20\d{2}-\d{2}-\d{2})"
)
LIST_END_LABEL_RE = re.compile(
    r"\u6302\u724c\u671f\u6ee1\u65e5\u671f[:\uff1a]?\s*(20\d{2}-\d{2}-\d{2})"
)
LIST_PRICE_RE = re.compile(
    r"(?:\u8f6c\u8ba9\u5e95\u4ef7|\u52df\u96c6\u8d44\u91d1)[:\uff1a]?\s*([0-9][0-9,]*(?:\.\d+)?)\s*\u4e07\u5143?"
)
LIST_TOTAL_RE = re.compile(r"\u5171\s*\u627e\s*\u5230\s*(\d+)\s*\u6761\s*[\(\uff08]?\u9879\u76ee[\)\uff09]?\s*\u8bb0\u5f55")
PROJECT_CODE_RE = re.compile(
    r"((?:G3|Q3|P3|G6|Q6|P6|GR|QR|PR|TR)\d{4}CQ\d+(?:-\d+)?)",
    flags=re.IGNORECASE,
)
FALLBACK_PROJECT_CODE_RE = re.compile(r"\b(20\d{10})\b")
RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


@dataclass(frozen=True)
class _ListSource:
    label: str
    list_url: str
    # The public project page was replaced by a Vue shell in August 2026.  Keep
    # the legacy URL as the declared source contract, while carrying the
    # equivalent first-party API filters for the transport fallback.
    api_params: tuple[tuple[str, str], ...] = ()


@dataclass
class _DownloadCandidate:
    project_id: str
    project_name: str
    page_url: str
    html_path: str
    list_url: str
    row: Dict[str, Any]
    project_code: str = ""


@dataclass(frozen=True)
class _ListPageObservation:
    status: str
    declared_total: Optional[int]
    parsed_items: int
    blocked: bool
    identity_valid: bool


class _ListUrlContractError(ValueError):
    pass


def _json_file_is_object(json_path: str) -> bool:
    if not os.path.isfile(json_path):
        return False
    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict)


def _has_blocking_evidence_sidecar(html_path: str) -> bool:
    evidence_path = f"{html_path}.peap-evidence.json"
    if not os.path.exists(evidence_path):
        return False
    try:
        with open(evidence_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    return str(payload.get("page_kind") or "").strip() == "invalid_shell"


def _is_resume_complete(
    html_path: str,
    *,
    save_json: bool,
    task_id: str,
    source_id: str,
    business_id: str,
) -> bool:
    json_path = os.path.splitext(html_path)[0] + ".json"
    marker_path = _resume_status_path(html_path)
    return complete_resume_sidecar_exists(
        html_path,
        sidecar_path=json_path if save_json else marker_path,
        require_integrity=True,
        require_assets_dir=True,
        expected_fields={
            "task_id": task_id,
            "source_id": source_id,
            "record_family": "listing",
            "business_id": business_id,
        },
    )


def _resume_status_path(html_path: str) -> str:
    return f"{html_path}.peap-save-status.json"


def _load_resume_status_payload(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _parse_price(value: str) -> Optional[float]:
    text = str(value or "").strip()
    if not text or text in {"-", "\u9762\u8bae", "\u53e6\u884c\u516c\u544a"}:
        return None
    text = text.replace("\u4e07\u5143", "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _build_list_url(params: Dict[str, Any]) -> str:
    return _normalize_list_url(f"{BASE_URL}/project?{urllib.parse.urlencode(params)}")


def _cquae_list_endpoint() -> str:
    return get_source_business_requirement("cquae", "listing", "equity_transfer").list_endpoint


def _cquae_list_sources(business_id: str) -> List[_ListSource]:
    sources: List[_ListSource] = []
    for spec in get_source_business_requirement("cquae", "listing", business_id).list_query_specs:
        label = str(spec["label"])
        legacy_params = {key: value for key, value in spec.items() if key != "label"}
        api_params: Dict[str, str] = {}
        project_id = str(spec.get("projectID") or "").strip()
        notice_type = str(spec.get("nt") or "").strip()
        if project_id == "1":
            api_params["projectType"] = "12" if notice_type == "3" else "11"
        elif project_id == "2":
            api_params["projectType"] = "22" if notice_type == "3" else "21"
        elif project_id == "3":
            api_params["projectType"] = "3"
            price = str(spec.get("price") or "").strip()
            price_bucket = {
                "5000万-1亿": "5000-10000",
                "1亿以上": "10000-",
            }.get(price)
            if price_bucket:
                api_params["priceListStr"] = price_bucket
        sources.append(
            _ListSource(
                label=label,
                list_url=_build_list_url(legacy_params),
                api_params=tuple(api_params.items()),
            )
        )
    return sources


def _normalize_list_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return raw
    parsed = urllib.parse.urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw

    path = parsed.path or "/"
    if path.lower() == "/project":
        path = "/project"
    path = urllib.parse.quote(path, safe="/%:@")

    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = urllib.parse.urlencode(query_pairs, doseq=True)
    fragment = urllib.parse.quote(parsed.fragment, safe="")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, fragment))


def _url_query_values(url: str) -> Dict[str, List[str]]:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    values: Dict[str, List[str]] = {}
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        values.setdefault(key, []).append(value)
    return values


def _url_signature(url: str) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
    parsed = urllib.parse.urlsplit(_normalize_list_url(url))
    return (
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path or "/",
        tuple(sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))),
    )


def _list_source_signature(
    source: _ListSource,
) -> tuple[str, tuple[str, str, str, tuple[tuple[str, str], ...]], tuple[tuple[str, str], ...]]:
    raw_params = getattr(source, "api_params", ()) or ()
    api_items = raw_params.items() if isinstance(raw_params, Mapping) else raw_params
    return (
        source.label,
        _url_signature(source.list_url),
        tuple((str(key), str(value)) for key, value in api_items),
    )


def _same_list_endpoint(left: str, right: str) -> bool:
    left_url = urllib.parse.urlsplit(str(left or "").strip())
    right_url = urllib.parse.urlsplit(str(right or "").strip())
    return (
        left_url.scheme.lower() == right_url.scheme.lower()
        and left_url.netloc.lower() == right_url.netloc.lower()
        and (left_url.path or "/") == (right_url.path or "/")
        and not left_url.fragment
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().upper()


def _looks_usable_expected_name(value: str) -> bool:
    normalized = _normalize_text(value)
    if len(normalized) < 4:
        return False
    if "\ufffd" in normalized:
        return False
    return normalized.count("?") * 2 < len(normalized)


def _list_row_identity(row: Dict[str, Any]) -> str:
    page_url = str(row.get("page_url") or "").strip()
    project_id = urllib.parse.parse_qs(urllib.parse.urlsplit(page_url).query).get("id", [""])[
        0
    ].strip()
    if project_id:
        return f"project_id:{project_id}"
    identity_text = " ".join(
        str(row.get(field_name) or "")
        for field_name in ("project_code", "project_name")
    )
    code_match = PROJECT_CODE_RE.search(identity_text) or FALLBACK_PROJECT_CODE_RE.search(
        identity_text
    )
    if code_match:
        return f"project_code:{code_match.group(1).upper()}"
    raise ValueError("list row lacks an official project id/code")


def _parse_list_declared_total(soup: BeautifulSoup) -> Optional[int]:
    text = soup.get_text(" ", strip=True)
    match = LIST_TOTAL_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _observe_list_page(*, soup: BeautifulSoup, html: str, parsed_items: int) -> _ListPageObservation:
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    blocked = "__jsl_clearance_s" in str(html or "") or "访问验证" in title or "访问验证" in soup.get_text(" ", strip=True)
    identity_valid = "重庆产权交易网" in title or "重庆产权交易网" in soup.get_text(" ", strip=True)
    declared_total = _parse_list_declared_total(soup)
    status = "items"
    if blocked:
        status = "blocked"
    elif not identity_valid:
        status = "identity-mismatch"
    elif parsed_items > 0:
        status = "items"
    elif declared_total == 0:
        status = "empty"
    elif declared_total and declared_total > 0:
        status = "positive-total-without-items"
    else:
        status = "no-list-items"
    return _ListPageObservation(
        status=status,
        declared_total=declared_total,
        parsed_items=parsed_items,
        blocked=blocked,
        identity_valid=identity_valid,
    )


def _decode_html(raw: bytes, charset_hint: Optional[str]) -> str:
    tried: List[str] = []
    last_error: UnicodeDecodeError | None = None
    for encoding in (charset_hint, "utf-8", "gb18030"):
        if not encoding:
            continue
        norm = str(encoding).strip().lower()
        if not norm or norm in tried:
            continue
        tried.append(norm)
        try:
            return raw.decode(norm)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return raw.decode("utf-8")


def _charset_from_content_type(value: object) -> Optional[str]:
    match = re.search(r"charset\s*=\s*['\"]?([^;'\"\s]+)", str(value or ""), flags=re.I)
    return match.group(1).strip() if match else None


def _fetched_list_html(
    *,
    raw_bytes: bytes,
    charset_hint: Optional[str],
    source_url: str,
    final_url: str,
    http_status: object,
) -> HttpFetchedText:
    decode_error = ""
    try:
        text = _decode_html(raw_bytes, charset_hint)
    except (LookupError, UnicodeDecodeError) as exc:
        text = raw_bytes.decode("utf-8", errors="replace")
        decode_error = str(exc)
    fetched = HttpFetchedText(
        text,
        source_url=source_url,
        final_url=final_url,
        http_status=http_status,
        raw_bytes=raw_bytes,
    )
    if decode_error:
        fetched.decode_error = decode_error
    fetched.charset_hint = charset_hint
    return fetched


class ChongqingProjectDownloader:
    """Download Chongqing detail pages after parsing list pages."""

    manifest_list_endpoint = _cquae_list_endpoint()
    manifest_detail_route = "/Project/Show"
    manifest_date_field_candidates = ("disclosure_start",)
    list_fetch_attempts = 3
    list_browser_request_fallback_enabled = True

    def __init__(
        self,
        *,
        html_root: str,
        page_size: int = 10,
        max_pages: Optional[int] = None,
        concurrency: int = 2,
        resume: bool = False,
        timeout: int = 30,
        save_json: bool = False,
        output_type: str = TYPE_EQUITY_TRANSFER,
        list_sources: Optional[List[_ListSource]] = None,
        logger: Optional[logging.Logger] = None,
        item_saved_callback=None,
        run_id: Optional[str] = None,
    ):
        self.html_root = html_root
        self.page_size = max(1, int(page_size))
        self.max_pages = max_pages if max_pages is None else max(1, int(max_pages))
        self.concurrency = max(1, int(concurrency))
        self.resume = bool(resume)
        self.timeout = max(10, int(timeout))
        self.save_json = bool(save_json)
        self.output_type = str(output_type or TYPE_EQUITY_TRANSFER)
        if list_sources is None:
            self.list_sources = []
        elif not isinstance(list_sources, list):
            raise TypeError("list_sources must be a list or None")
        else:
            self.list_sources = list(list_sources)
        self.logger = logger or logging.getLogger("parser_v2")
        self.item_saved_callback = item_saved_callback
        self.run_id = str(run_id or "").strip() or f"run-{int(time.time() * 1000)}"
        self._render_timeout_ms = max(90, self.timeout) * 1000
        self._detail_retries = 2
        self._ssl_context_insecure = ssl._create_unverified_context()
        self._snapshot_saver = SnapshotSaver(
            user_agent=REQUEST_HEADERS["User-Agent"],
            timeout=self.timeout,
            ssl_context=self._ssl_context_insecure,
        )
        self._resume_index: Dict[str, Dict[str, str]] = {}
        self._resume_index_path: Optional[str] = None

    def run(
        self,
        *,
        start_date: Optional[str],
        end_date: Optional[str],
        list_only: bool = False,
        prefetched_candidates: Optional[List[Dict[str, Any]]] = None,
    ) -> DownloadSummary:
        start = parse_bound(start_date, "start-date")
        end = parse_bound(end_date, "end-date")
        if start and end and start > end:
            raise ValueError(f"start-date {start_date!r} is after end-date {end_date!r}")

        output_dir = os.path.abspath(self.html_root)
        os.makedirs(output_dir, exist_ok=True)
        self._resume_index_path = os.path.join(output_dir, ".cquae_resume_index.json")
        self._resume_index = self._load_resume_index(self._resume_index_path)

        summary = DownloadSummary()
        candidates: List[_DownloadCandidate] = []
        self.logger.info(
            "Start CQUAE download: type=%s start_date=%s end_date=%s max_pages=%s concurrency=%s resume=%s output=%s",
            self.output_type,
            start.isoformat() if start else "-",
            end.isoformat() if end else "-",
            self.max_pages if self.max_pages is not None else "unlimited",
            self.concurrency,
            self.resume,
            output_dir,
        )

        if prefetched_candidates is None:
            self._collect_list_candidates(
                output_dir=output_dir,
                summary=summary,
                candidates=candidates,
                start=start,
                end=end,
            )
        else:
            self._build_prefetched_candidates(
                prefetched_candidates=prefetched_candidates,
                output_dir=output_dir,
                summary=summary,
                candidates=candidates,
                start=start,
                end=end,
            )

        summary.detail_candidates = len(candidates)
        list_resume_skipped = int(summary.skipped_by_resume)
        if list_only:
            self.logger.info("List-only mode: skip detail download for type=%s", self.output_type)
        elif candidates:
            asyncio.run(
                self._download_candidates_concurrently(
                    candidates=candidates,
                    summary=summary,
                    start=start,
                    end=end,
                )
            )
        detail_resume_skipped = max(int(summary.skipped_by_resume) - list_resume_skipped, 0)

        list_accounted = (
            summary.skipped_by_list_date
            + list_resume_skipped
            + summary.skipped_by_duplicate
            + summary.skipped_by_missing_xmid
            + summary.detail_candidates
        )
        detail_accounted = detail_accounted_count(summary, detail_resume_skipped=detail_resume_skipped)
        summary.list_unaccounted = summary.listed_items - list_accounted
        summary.detail_unaccounted = 0 if list_only else (summary.detail_candidates - detail_accounted)
        self._save_resume_index()
        return summary

    def _collect_list_candidates(
        self,
        *,
        output_dir: str,
        summary: DownloadSummary,
        candidates: List[_DownloadCandidate],
        start: Optional[dt.date],
        end: Optional[dt.date],
    ) -> None:
        business_id = business_id_key(self.output_type)
        authoritative_sources = _cquae_list_sources(business_id)
        task_id = runtime_task_id("cquae", self.output_type)
        expected_query_ids = tuple(
            f"{source_number:03d}-{source.label or 'listing'}"
            for source_number, source in enumerate(authoritative_sources, start=1)
        )
        configured_signature = tuple(
            _list_source_signature(source) for source in self.list_sources
        )
        authoritative_signature = tuple(
            _list_source_signature(source) for source in authoritative_sources
        )
        task_evidence = DiscoveryTaskEvidence(
            root=output_dir,
            source_id="cquae",
            task_id=task_id,
            run_id=self.run_id,
            expected_query_ids=expected_query_ids,
        )
        if configured_signature != authoritative_signature:
            raw_reason = (
                "authoritative-list-config-mismatch: "
                f"expected={len(authoritative_sources)} configured={len(self.list_sources)}"
            )
            summary.typed_errors.append(
                list_failed_error(
                    source_id="cquae",
                    task_id=task_id,
                    raw_reason=raw_reason,
                )
            )
            task_evidence.fail(
                termination_reason="authoritative_config_mismatch",
                details={
                    "expected_source_count": len(authoritative_sources),
                    "configured_source_count": len(self.list_sources),
                },
                missing_query_ids=expected_query_ids,
            )
            summary.discovery_task_manifest = task_evidence.manifest_reference()
            return

        staged_summary = DownloadSummary()
        staged_candidates: List[_DownloadCandidate] = []
        with task_evidence:
            try:
                discovered = self._collect_list_source_pages(
                    summary=summary,
                    task_evidence=task_evidence,
                )
            except Exception as exc:  # noqa: BLE001
                task_evidence.fail(
                    termination_reason="exception",
                    details={"error": str(exc)},
                )
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="cquae",
                        task_id=task_id,
                        raw_reason=f"discovery-task-failed: {exc}",
                    )
                )
                summary.discovery_task_manifest = task_evidence.manifest_reference()
                return
            if discovered is None:
                task_evidence.fail(termination_reason="query_failed")
                summary.discovery_task_manifest = task_evidence.manifest_reference()
                return

            seen_ids: Set[str] = set()
            try:
                for source, current_url, rows in discovered:
                    self._list_rows_to_candidates(
                        rows=rows,
                        source=source,
                        current_url=current_url,
                        output_dir=output_dir,
                        summary=staged_summary,
                        candidates=staged_candidates,
                        seen_ids=seen_ids,
                        start=start,
                        end=end,
                    )
            except Exception as exc:  # noqa: BLE001
                task_evidence.fail(
                    termination_reason="candidate_conversion_failed",
                    details={"error": str(exc)},
                )
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="cquae",
                        task_id=task_id,
                        raw_reason=f"candidate-conversion-failed: {exc}",
                    )
                )
                summary.discovery_task_manifest = task_evidence.manifest_reference()
                return

            if staged_summary.typed_errors:
                summary.typed_errors.extend(staged_summary.typed_errors)
                task_evidence.fail(
                    termination_reason="candidate_conversion_failed",
                    details={"candidate_error_count": len(staged_summary.typed_errors)},
                )
                summary.discovery_task_manifest = task_evidence.manifest_reference()
                return

            try:
                task_evidence.complete(
                    candidate_entries=staged_summary.candidate_entries
                )
                summary.discovery_task_manifest = task_evidence.manifest_reference()
            except DiscoveryEvidenceError as exc:
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="cquae",
                        task_id=task_id,
                        raw_reason=f"discovery-task-coverage-failed: {exc}",
                    )
                )
                summary.discovery_task_manifest = task_evidence.manifest_reference()
                return

        summary.listed_items += staged_summary.listed_items
        summary.skipped_by_list_date += staged_summary.skipped_by_list_date
        summary.skipped_by_resume += staged_summary.skipped_by_resume
        summary.skipped_by_duplicate += staged_summary.skipped_by_duplicate
        summary.skipped_by_business_filter += staged_summary.skipped_by_business_filter
        summary.skipped_by_missing_xmid += staged_summary.skipped_by_missing_xmid
        summary.candidate_entries.extend(staged_summary.candidate_entries)
        summary.candidate_dates.extend(staged_summary.candidate_dates)
        candidates.extend(staged_candidates)

    @staticmethod
    def _source_api_params(source: _ListSource) -> Dict[str, str]:
        """Return the immutable API filter declaration as a plain mapping."""
        raw_params = getattr(source, "api_params", ()) or ()
        items = raw_params.items() if isinstance(raw_params, Mapping) else raw_params
        return {
            str(key): str(value)
            for key, value in tuple(items)
            if str(key).strip()
        }

    def _cquae_api_page_url(self, source: _ListSource, page_index: int) -> str:
        if page_index < 1:
            raise ValueError("CQUAE API page index must be positive")
        params: Dict[str, str] = {
            "page": str(page_index),
            "size": str(self.page_size),
            "sort": "d",
        }
        params.update(self._source_api_params(source))
        return _normalize_list_url(
            f"{CQUAE_PROJECT_PAGE_API_URL}?{urllib.parse.urlencode(params)}"
        )

    @staticmethod
    def _looks_like_vue_project_shell(response: HttpFetchedText) -> bool:
        raw = getattr(response, "raw_bytes", None)
        if not isinstance(raw, bytes) or not raw:
            return False
        text = raw.decode("utf-8", errors="replace")
        if "<div id=app>" not in text and 'id="app"' not in text:
            return False
        if "static/js/app." not in text:
            return False
        return "n2_List" not in text

    def _fetch_cquae_api_page(
        self,
        *,
        source: _ListSource,
        page_index: int,
        fallback_url: str = "",
    ) -> HttpFetchedText:
        api_url = self._cquae_api_page_url(source, page_index)
        response = self._fetch_list_html(api_url)
        if not isinstance(response, HttpFetchedText):
            return response
        response.body_format = "json"
        response.transport = "project_page_api"
        if fallback_url:
            response.fallback_source_url = str(fallback_url)
        return response

    @staticmethod
    def _validate_api_response_urls(
        *,
        response: HttpFetchedText,
        expected_url: str,
        page_index: int,
    ) -> None:
        expected = urllib.parse.urlsplit(_normalize_list_url(expected_url))
        for field_name, value in (
            ("response.source_url", getattr(response, "source_url", "")),
            ("response.final_url", getattr(response, "final_url", "")),
        ):
            parsed = urllib.parse.urlsplit(str(value or ""))
            if (
                parsed.scheme.lower() != expected.scheme.lower()
                or parsed.netloc.lower() != expected.netloc.lower()
                or parsed.path != expected.path
            ):
                raise _ListUrlContractError(
                    f"{field_name} must use the CQUAE projectPageQuery endpoint"
                )
            expected_pairs = dict(urllib.parse.parse_qsl(expected.query, keep_blank_values=True))
            actual_pairs = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
            for key, expected_value in expected_pairs.items():
                if actual_pairs.get(key) != expected_value:
                    raise _ListUrlContractError(
                        f"{field_name} changed API filter {key!r}"
                    )
            extras = set(actual_pairs) - set(expected_pairs)
            if extras - {"transport", "served", "edge"}:
                raise _ListUrlContractError(
                    f"{field_name} added API filters: {sorted(extras)}"
                )
            try:
                actual_page = int(actual_pairs.get("page", ""))
            except ValueError as exc:
                raise _ListUrlContractError(
                    f"{field_name} page must be an integer"
                ) from exc
            if actual_page != page_index:
                raise _ListUrlContractError(
                    f"{field_name} page={actual_page} is not {page_index}"
                )

    @staticmethod
    def _api_int(value: Any, *, field_name: str, minimum: int = 0) -> int:
        if isinstance(value, bool):
            raise ValueError(f"API field {field_name} must be an integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"API field {field_name} must be an integer") from exc
        if parsed < minimum:
            raise ValueError(f"API field {field_name} must be >= {minimum}")
        return parsed

    def _parse_api_list_page(
        self,
        *,
        response: HttpFetchedText,
        source: _ListSource,
        page_index: int,
    ) -> tuple[List[Dict[str, Any]], Optional[int], Optional[int], _ListPageObservation]:
        raw = getattr(response, "raw_bytes", None)
        if not isinstance(raw, bytes):
            raise ValueError("CQUAE projectPageQuery response has no raw bytes")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid projectPageQuery JSON: {exc}") from exc
        if not isinstance(payload, dict) or str(payload.get("state") or "").upper() != "SUCCESS":
            raise ValueError("projectPageQuery did not return state=SUCCESS")
        target = payload.get("target")
        if not isinstance(target, dict):
            raise ValueError("projectPageQuery response has no target object")
        response_page = self._api_int(target.get("page"), field_name="target.page", minimum=1)
        response_size = self._api_int(target.get("size"), field_name="target.size", minimum=1)
        if response_page != page_index:
            raise ValueError(
                f"projectPageQuery returned page={response_page}, expected {page_index}"
            )
        if response_size != self.page_size:
            raise ValueError(
                f"projectPageQuery returned size={response_size}, expected {self.page_size}"
            )
        total = self._api_int(target.get("totalClause"), field_name="target.totalClause")
        total_pages = self._api_int(target.get("totalPage"), field_name="target.totalPage")
        expected_pages = math.ceil(total / response_size) if total else 0
        if total_pages != expected_pages:
            raise ValueError(
                f"projectPageQuery totalPage={total_pages} disagrees with totalClause={total}"
            )
        result = target.get("result")
        # The current service omits ``result`` entirely for a successful empty
        # query (notably projectType=21), while non-empty responses always use
        # an array.  Treat only that exact zero-total shape as official empty.
        if result is None and total == 0:
            result = []
        if not isinstance(result, list):
            raise ValueError("projectPageQuery target.result must be a list")
        if len(result) > response_size:
            raise ValueError("projectPageQuery returned more rows than target.size")
        rows = [
            self._parse_api_list_item(item, source=source, page_index=page_index)
            for item in result
        ]
        status = "empty" if total == 0 and not rows else ("items" if rows else "positive-total-without-items")
        observation = _ListPageObservation(
            status=status,
            declared_total=total,
            parsed_items=len(rows),
            blocked=False,
            identity_valid=True,
        )
        # Discovery evidence represents a captured page with a positive page
        # count; normalize the service's zero-page empty response to one
        # observed (empty) page without changing the empty-result decision.
        return rows, total, max(1, total_pages), observation

    @staticmethod
    def _parse_api_list_item(
        item: Any,
        *,
        source: _ListSource,
        page_index: int,
    ) -> Dict[str, Any]:
        if not isinstance(item, dict):
            raise ValueError(f"projectPageQuery page {page_index} contains a non-object row")
        project_id = str(item.get("projectId") or "").strip()
        project_name = str(item.get("noticeName") or item.get("noticeSubName") or "").strip()
        if not project_id:
            raise ValueError(f"projectPageQuery page {page_index} row has no projectId")
        if not project_name:
            raise ValueError(f"projectPageQuery page {page_index} row has no noticeName")
        page_url = (
            f"{BASE_URL}/projectCenter/detail?"
            + urllib.parse.urlencode({"id": project_id, "code": "PROJECT_CENTER"})
        )
        listing_price = item.get("listingPrice")
        if listing_price is None:
            listing_price = item.get("listingPriceMemo")
        return {
            "project_id": project_id,
            "project_name": project_name,
            "page_url": page_url,
            "list_url": CQUAE_PROJECT_PAGE_API_URL,
            "navigation_list_url": f"{BASE_URL}/projectCenter?code=PROJECT_CENTER",
            "disclosure_start": item.get("listStartDate"),
            "disclosure_end": item.get("listEndDate"),
            "list_price": str(listing_price or ""),
            "list_price_value": _parse_price(str(listing_price or "")),
            "project_type": item.get("projectTypeSubjectRemark"),
            "listing_type": item.get("listingType"),
            "api_row": item,
            "list_page": page_index,
            "list_source": source.label,
        }

    def _collect_list_source_pages(
        self,
        *,
        summary: DownloadSummary,
        task_evidence: DiscoveryTaskEvidence,
    ) -> Optional[List[tuple[_ListSource, str, List[Dict[str, Any]]]]]:
        task_id = runtime_task_id("cquae", self.output_type)
        pending_pages: List[tuple[_ListSource, str, List[Dict[str, Any]]]] = []
        all_queries_complete = True
        for source_number, source in enumerate(self.list_sources, start=1):
            current_url = source.list_url
            api_mode = False
            seen_urls: Set[str] = set()
            page_index = 0
            query_complete = False
            query_pages: List[tuple[_ListSource, str, List[Dict[str, Any]]]] = []
            evidence_enabled = True
            query_evidence = task_evidence.query(
                f"{source_number:03d}-{source.label or 'listing'}",
                authoritative_total=True,
                page_size=self.page_size,
            )
            with query_evidence:
                while current_url:
                    if current_url in seen_urls:
                        raw_reason = (
                            f"list-{source.label}-page-{page_index + 1}-repeated-page-url: "
                            f"url={current_url}"
                        )
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id="cquae",
                                task_id=task_id,
                                raw_reason=raw_reason,
                            )
                        )
                        if evidence_enabled:
                            query_evidence.fail(
                                termination_reason="repeated_page_url",
                                details={"page_index": page_index + 1, "url": current_url},
                            )
                        break
                    if self.max_pages is not None and page_index >= self.max_pages:
                        raw_reason = (
                            f"list-{source.label}-page-{page_index + 1}-max-pages-truncated: "
                            f"max_pages={self.max_pages}"
                        )
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id="cquae",
                                task_id=task_id,
                                raw_reason=raw_reason,
                            )
                        )
                        if evidence_enabled:
                            query_evidence.fail(
                                termination_reason="explicit_max_pages",
                                details={"max_pages": self.max_pages, "next_page": page_index + 1},
                            )
                        break

                    seen_urls.add(current_url)
                    page_index += 1
                    try:
                        if api_mode:
                            html = self._fetch_cquae_api_page(
                                source=source,
                                page_index=page_index,
                            )
                            current_url = html.source_url
                        else:
                            html = self._fetch_list_html(current_url)
                            if (
                                self._source_api_params(source)
                                and isinstance(html, HttpFetchedText)
                                and self._looks_like_vue_project_shell(html)
                            ):
                                api_mode = True
                                html = self._fetch_cquae_api_page(
                                    source=source,
                                    page_index=page_index,
                                    fallback_url=current_url,
                                )
                                current_url = html.source_url
                    except Exception as exc:  # noqa: BLE001
                        summary.pages_requested += 1
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id="cquae",
                                task_id=task_id,
                                raw_reason=(
                                    f"list-{source.label}-page-{page_index}-request-failed: {exc}"
                                ),
                            )
                        )
                        if evidence_enabled:
                            query_evidence.fail(
                                termination_reason="request_failed",
                                details={"page_index": page_index, "error": str(exc)},
                            )
                        break

                    summary.pages_requested += 1
                    if not isinstance(html, HttpFetchedText):
                        raw_reason = (
                            f"list-{source.label}-page-{page_index}-transport-evidence-unavailable"
                        )
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id="cquae",
                                task_id=task_id,
                                raw_reason=raw_reason,
                            )
                        )
                        if evidence_enabled:
                            query_evidence.fail(
                                termination_reason="transport_evidence_unavailable",
                                details={
                                    "page_index": page_index,
                                    "response_type": type(html).__name__,
                                },
                            )
                        break
                    try:
                        query_evidence.capture_page(
                            page_index=page_index,
                            response=html,
                            body_format="json" if api_mode else "html",
                            request_metadata={
                                "method": "GET",
                                "url": current_url,
                                "transport": "project_page_api" if api_mode else "legacy_html",
                                **(
                                    {"fallback_source_url": html.fallback_source_url}
                                    if getattr(html, "fallback_source_url", "")
                                    else {}
                                ),
                            },
                        )
                    except DiscoveryEvidenceError as exc:
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id="cquae",
                                task_id=task_id,
                                raw_reason=(
                                    f"list-{source.label}-page-{page_index}-capture-failed: {exc}"
                                ),
                            )
                        )
                        query_evidence.fail(
                            termination_reason="evidence_capture_failed",
                            details={"page_index": page_index, "error": str(exc)},
                        )
                        break

                    try:
                        if api_mode:
                            self._validate_api_response_urls(
                                response=html,
                                expected_url=self._cquae_api_page_url(source, page_index),
                                page_index=page_index,
                            )
                        else:
                            self._validate_list_response_urls(
                                response=html,
                                initial_url=source.list_url,
                                current_url=current_url,
                                page_index=page_index,
                            )
                    except _ListUrlContractError as exc:
                        query_evidence.fail_page(
                            page_index=page_index,
                            reason="transport_url_invalid",
                            details={"error": str(exc)},
                        )
                        query_evidence.fail(
                            termination_reason="response_invalid",
                            details={"page_index": page_index, "error": str(exc)},
                        )
                        summary.typed_errors.append(
                            invalid_candidate_error(
                                source_id="cquae",
                                task_id=task_id,
                                raw_reason=(
                                    f"list-{source.label}-page-{page_index}-url-invalid: {exc}"
                                ),
                            )
                        )
                        break

                    decode_error = ""
                    try:
                        decoded_html = _decode_html(
                            html.raw_bytes,
                            getattr(html, "charset_hint", None),
                        )
                    except (LookupError, UnicodeDecodeError) as exc:
                        decoded_html = html.raw_bytes.decode("utf-8", errors="replace")
                        decode_error = str(exc)
                    decode_error = decode_error or str(
                        getattr(html, "decode_error", "") or ""
                    ).strip()
                    if decode_error:
                        query_evidence.fail_page(
                            page_index=page_index,
                            reason="response_decode_failed",
                            details={"error": decode_error},
                        )
                        query_evidence.fail(
                            termination_reason="response_invalid",
                            details={"page_index": page_index, "error": decode_error},
                        )
                        summary.typed_errors.append(
                            invalid_candidate_error(
                                source_id="cquae",
                                task_id=task_id,
                                raw_reason=(
                                    f"list-{source.label}-page-{page_index}-decode-failed: "
                                    f"{decode_error}"
                                ),
                            )
                        )
                        break

                    soup: Optional[BeautifulSoup] = None
                    declared_pages: Optional[int] = None
                    try:
                        if api_mode:
                            parsed_rows, _declared_total, declared_pages, observation = (
                                self._parse_api_list_page(
                                    response=html,
                                    source=source,
                                    page_index=page_index,
                                )
                            )
                        else:
                            soup = BeautifulSoup(decoded_html, "html.parser")
                            item_nodes = soup.select("div.n2_List.itcon")
                            observation = _observe_list_page(
                                soup=soup,
                                html=decoded_html,
                                parsed_items=len(item_nodes),
                            )
                            parsed_rows = [
                                self._parse_list_item(item, current_url=current_url)
                                for item in item_nodes
                            ]
                    except Exception as exc:  # noqa: BLE001
                        if evidence_enabled:
                            query_evidence.fail_page(
                                page_index=page_index,
                                reason="html_parse_failed",
                                details={"error": str(exc)},
                            )
                            query_evidence.fail(
                                termination_reason="response_invalid",
                                details={"page_index": page_index, "error": str(exc)},
                            )
                        summary.typed_errors.append(
                            invalid_candidate_error(
                                source_id="cquae",
                                task_id=task_id,
                                raw_reason=(
                                    f"list-{source.label}-page-{page_index}-"
                                    f"{'api' if api_mode else 'html'}-parse-failed: {exc}"
                                ),
                            )
                        )
                        break

                    summary.list_page_observations.append(
                        {
                            "source_id": "cquae",
                            "source_label": source.label,
                            "page_index": page_index,
                            "status": observation.status,
                            "declared_total": observation.declared_total,
                            "parsed_items": observation.parsed_items,
                            "blocked": observation.blocked,
                            "identity_valid": observation.identity_valid,
                        }
                    )
                    if observation.status not in {"items", "empty"}:
                        title = (
                            soup.title.get_text(" ", strip=True)
                            if soup is not None and soup.title
                            else ""
                        )
                        reason_code = (
                            "no-list-items-blocked"
                            if observation.status == "blocked"
                            else observation.status or "no-list-items"
                        )
                        raw_reason = (
                            f"list-{source.label}-page-{page_index}-{reason_code}: "
                            f"url={current_url} title={title!r} html_len={len(decoded_html)} "
                            f"declared_total={observation.declared_total!r}"
                        )
                        if evidence_enabled:
                            query_evidence.fail_page(
                                page_index=page_index,
                                reason="html_structure_invalid",
                                details={"status": observation.status},
                            )
                            query_evidence.fail(
                                termination_reason="response_invalid",
                                details={"page_index": page_index, "status": observation.status},
                            )
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id="cquae",
                                task_id=task_id,
                                raw_reason=raw_reason,
                            )
                        )
                        self.logger.warning(
                            "No list items found for source=%s url=%s status=%s declared_total=%r",
                            source.label,
                            current_url,
                            observation.status,
                            observation.declared_total,
                        )
                        break
                    if any(row is None for row in parsed_rows):
                        raw_reason = (
                            f"list-{source.label}-page-{page_index}-unparseable-list-item"
                        )
                        if evidence_enabled:
                            query_evidence.fail_page(
                                page_index=page_index,
                                reason="html_item_schema_invalid",
                            )
                            query_evidence.fail(
                                termination_reason="response_invalid",
                                details={"page_index": page_index},
                            )
                        summary.typed_errors.append(
                            invalid_candidate_error(
                                source_id="cquae",
                                task_id=task_id,
                                raw_reason=raw_reason,
                            )
                        )
                        break

                    rows: List[Dict[str, Any]] = [
                        row for row in parsed_rows if row is not None
                    ]
                    try:
                        row_identities = [_list_row_identity(row) for row in rows]
                    except ValueError as exc:
                        if evidence_enabled:
                            query_evidence.fail_page(
                                page_index=page_index,
                                reason="html_item_identity_invalid",
                                details={"error": str(exc)},
                            )
                            query_evidence.fail(
                                termination_reason="response_invalid",
                                details={"page_index": page_index, "error": str(exc)},
                            )
                        summary.typed_errors.append(
                            invalid_candidate_error(
                                source_id="cquae",
                                task_id=task_id,
                                raw_reason=(
                                    f"list-{source.label}-page-{page_index}-invalid-row-identity: "
                                    f"{exc}"
                                ),
                            )
                        )
                        break
                    if not api_mode:
                        declared_pages = (
                            None
                            if observation.declared_total is None
                            else max(1, math.ceil(observation.declared_total / self.page_size))
                        )
                    if evidence_enabled:
                        try:
                            query_evidence.complete_page(
                                page_index=page_index,
                                extracted_row_count=len(rows),
                                row_identity_values=row_identities,
                                declared_total_items=observation.declared_total,
                                declared_total_pages=declared_pages,
                            )
                        except DiscoveryEvidenceError as exc:
                            query_evidence.fail(
                                termination_reason="repeated_page",
                                details={"page_index": page_index, "error": str(exc)},
                            )
                            summary.typed_errors.append(
                                list_failed_error(
                                    source_id="cquae",
                                    task_id=task_id,
                                    raw_reason=(
                                        f"list-{source.label}-page-{page_index}-repeated-page: {exc}"
                                    ),
                                )
                            )
                            break

                    query_pages.append((source, current_url, rows))
                    if api_mode:
                        next_url = (
                            self._cquae_api_page_url(source, page_index + 1)
                            if declared_pages is not None and page_index < declared_pages
                            else None
                        )
                    else:
                        next_url = self._extract_next_page_url(
                            soup=soup,
                            current_url=current_url,
                        )
                    if next_url:
                        try:
                            if api_mode:
                                self._validate_api_response_urls(
                                    response=HttpFetchedText(
                                        "",
                                        source_url=next_url,
                                        final_url=next_url,
                                        http_status=200,
                                    ),
                                    expected_url=next_url,
                                    page_index=page_index + 1,
                                )
                            else:
                                self._validate_next_page_url(
                                    next_url=next_url,
                                    initial_url=source.list_url,
                                    page_index=page_index,
                                )
                        except _ListUrlContractError as exc:
                            summary.typed_errors.append(
                                list_failed_error(
                                    source_id="cquae",
                                    task_id=task_id,
                                    raw_reason=(
                                        f"list-{source.label}-page-{page_index}-next-url-invalid: "
                                        f"{exc}"
                                    ),
                                )
                            )
                            if evidence_enabled:
                                query_evidence.fail(
                                    termination_reason="next_url_invalid",
                                    details={
                                        "page_index": page_index,
                                        "next_url": next_url,
                                        "error": str(exc),
                                    },
                                )
                            break
                    if observation.status == "empty":
                        if next_url:
                            summary.typed_errors.append(
                                list_failed_error(
                                    source_id="cquae",
                                    task_id=task_id,
                                    raw_reason=(
                                        f"list-{source.label}-page-{page_index}-empty-has-next-link"
                                    ),
                                )
                            )
                            if evidence_enabled:
                                query_evidence.fail(
                                    termination_reason="invalid_empty_pagination",
                                    details={"page_index": page_index},
                                )
                            break
                        if evidence_enabled:
                            try:
                                query_evidence.complete(termination_reason="official_empty")
                            except DiscoveryEvidenceError as exc:
                                summary.typed_errors.append(
                                    list_failed_error(
                                        source_id="cquae",
                                        task_id=task_id,
                                        raw_reason=f"list-{source.label}-coverage-failed: {exc}",
                                    )
                                )
                                break
                        query_complete = True
                        break
                    if declared_pages is not None and page_index >= declared_pages:
                        if next_url:
                            summary.typed_errors.append(
                                list_failed_error(
                                    source_id="cquae",
                                    task_id=task_id,
                                    raw_reason=(
                                        f"list-{source.label}-page-{page_index}-next-after-declared-end"
                                    ),
                                )
                            )
                            if evidence_enabled:
                                query_evidence.fail(
                                    termination_reason="declared_pages_not_terminal",
                                    details={"page_index": page_index, "next_url": next_url},
                                )
                            break
                        if evidence_enabled:
                            try:
                                query_evidence.complete(
                                    termination_reason="declared_pages_exhausted"
                                )
                            except DiscoveryEvidenceError as exc:
                                summary.typed_errors.append(
                                    list_failed_error(
                                        source_id="cquae",
                                        task_id=task_id,
                                        raw_reason=f"list-{source.label}-coverage-failed: {exc}",
                                    )
                                )
                                break
                        query_complete = True
                        break
                    if not next_url:
                        if declared_pages is None and len(rows) < self.page_size:
                            if evidence_enabled:
                                try:
                                    query_evidence.complete(
                                        termination_reason="short_page",
                                    )
                                except DiscoveryEvidenceError as exc:
                                    summary.typed_errors.append(
                                        list_failed_error(
                                            source_id="cquae",
                                            task_id=task_id,
                                            raw_reason=(
                                                f"list-{source.label}-coverage-failed: {exc}"
                                            ),
                                        )
                                    )
                                    break
                            query_complete = True
                            break
                        reason = (
                            "full-page-without-next"
                            if declared_pages is None
                            else "next-missing-before-declared-end"
                        )
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id="cquae",
                                task_id=task_id,
                                raw_reason=(
                                    f"list-{source.label}-page-{page_index}-{reason}"
                                ),
                            )
                        )
                        if evidence_enabled:
                            query_evidence.fail(
                                termination_reason=reason.replace("-", "_"),
                                details={
                                    "page_index": page_index,
                                    "parsed_items": len(rows),
                                    "page_size": self.page_size,
                                    "declared_pages": declared_pages,
                                },
                            )
                        break
                    current_url = next_url

            if not query_complete:
                all_queries_complete = False
                continue
            pending_pages.extend(query_pages)
        if not all_queries_complete:
            return None
        return pending_pages

    def _list_rows_to_candidates(
        self,
        *,
        rows: List[Dict[str, Any]],
        source: _ListSource,
        current_url: str,
        output_dir: str,
        summary: DownloadSummary,
        candidates: List[_DownloadCandidate],
        seen_ids: Set[str],
        start: Optional[dt.date],
        end: Optional[dt.date],
    ) -> None:
        for row in rows:
            summary.listed_items += 1

            project_id = str(row.get("project_id") or "").strip()
            if not project_id:
                summary.skipped_by_missing_xmid += 1
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="cquae",
                        task_id=runtime_task_id("cquae", self.output_type),
                        raw_reason=f"list-{source.label}-missing-project-id",
                    )
                )
                continue
            if project_id in seen_ids:
                summary.skipped_by_duplicate += 1
                continue
            seen_ids.add(project_id)

            list_disclosure_start = parse_loose_date(row.get("disclosure_start"))
            if start or end:
                if list_disclosure_start is None:
                    summary.skipped_by_list_date += 1
                    continue
                if not in_date_range(list_disclosure_start, start, end):
                    summary.skipped_by_list_date += 1
                    continue

            project_code = str(row.get("project_code") or "").strip().upper()
            project_name = str(row.get("project_name") or "").strip()
            html_seed = project_code or project_id
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=output_dir,
                project_code=html_seed,
                project_name=project_name,
                listing_date=str(row.get("disclosure_start") or ""),
            )
            resume_html_path = self._resolve_resume_html_path(
                output_dir=output_dir,
                project_id=project_id,
                project_code=project_code,
                project_name=project_name,
                listing_date=str(row.get("disclosure_start") or ""),
            )
            if self.resume and resume_html_path and _is_resume_complete(
                resume_html_path,
                save_json=self.save_json,
                task_id=runtime_task_id("cquae", self.output_type),
                source_id="cquae",
                business_id=business_id_key(self.output_type),
            ):
                summary.skipped_by_resume += 1
                continue

            row_with_source = {**row, "list_source": source.label, "list_url": current_url}
            if row.get("navigation_list_url"):
                row_with_source["navigation_list_url"] = str(row["navigation_list_url"])
            candidate = _DownloadCandidate(
                project_id=project_id,
                project_name=project_name,
                page_url=str(row.get("page_url") or "").strip(),
                html_path=html_path,
                list_url=current_url,
                row=row_with_source,
                project_code=project_code,
            )
            candidates.append(candidate)
            summary.candidate_entries.append(
                {
                    "project_id": candidate.project_id,
                    "project_code": candidate.project_code or None,
                    "project_name": candidate.project_name,
                    "page_url": candidate.page_url,
                    "list_url": candidate.list_url,
                    "row": row_with_source,
                    "disclosure_start": row.get("disclosure_start"),
                }
            )
            if row.get("disclosure_start"):
                summary.candidate_dates.append(str(row["disclosure_start"]))

    def _build_prefetched_candidates(
        self,
        *,
        prefetched_candidates: List[Dict[str, Any]],
        output_dir: str,
        summary: DownloadSummary,
        candidates: List[_DownloadCandidate],
        start: Optional[dt.date],
        end: Optional[dt.date],
    ) -> None:
        seen_ids: Set[str] = set()
        for index, raw in enumerate(prefetched_candidates, start=1):
            if not isinstance(raw, dict):
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="cquae",
                        task_id=runtime_task_id("cquae", self.output_type),
                        raw_reason=f"prefetched-entry-{index}-invalid-format",
                    )
                )
                continue
            summary.listed_items += 1

            project_id = str(raw.get("project_id") or "").strip()
            if not project_id:
                summary.skipped_by_missing_xmid += 1
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="cquae",
                        task_id=runtime_task_id("cquae", self.output_type),
                        raw_reason=f"prefetched-entry-{index}-missing-project-id",
                    )
                )
                continue
            if project_id in seen_ids:
                summary.skipped_by_duplicate += 1
                continue
            seen_ids.add(project_id)

            row_raw = raw.get("row")
            row = row_raw if isinstance(row_raw, dict) else {}
            list_disclosure_start = parse_loose_date(
                raw.get("disclosure_start") or row.get("disclosure_start")
            )
            if start or end:
                if list_disclosure_start is None:
                    summary.skipped_by_list_date += 1
                    continue
                if not in_date_range(list_disclosure_start, start, end):
                    summary.skipped_by_list_date += 1
                    continue

            page_url = str(raw.get("page_url") or row.get("page_url") or "").strip()
            if not page_url:
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="cquae",
                        task_id=runtime_task_id("cquae", self.output_type),
                        raw_reason=f"prefetched-entry-{index}-missing-page-url: project_id={project_id}",
                    )
                )
                continue

            list_url = str(raw.get("list_url") or row.get("list_url") or BASE_URL).strip()
            project_code = str(raw.get("project_code") or row.get("project_code") or "").strip().upper()
            project_name = str(raw.get("project_name") or row.get("project_name") or "").strip()
            html_seed = project_code or project_id
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=output_dir,
                project_code=html_seed,
                project_name=project_name,
                listing_date=list_disclosure_start.isoformat() if list_disclosure_start else "",
            )
            resume_html_path = self._resolve_resume_html_path(
                output_dir=output_dir,
                project_id=project_id,
                project_code=project_code,
                project_name=project_name,
                listing_date=list_disclosure_start.isoformat() if list_disclosure_start else "",
            )
            if self.resume and resume_html_path and _is_resume_complete(
                resume_html_path,
                save_json=self.save_json,
                task_id=runtime_task_id("cquae", self.output_type),
                source_id="cquae",
                business_id=business_id_key(self.output_type),
            ):
                summary.skipped_by_resume += 1
                continue

            row_with_source = dict(row)
            row_with_source["disclosure_start"] = (
                list_disclosure_start.isoformat() if list_disclosure_start else None
            )
            row_with_source["list_url"] = list_url
            candidate = _DownloadCandidate(
                project_id=project_id,
                project_name=project_name,
                page_url=page_url,
                html_path=html_path,
                list_url=list_url,
                row=row_with_source,
                project_code=project_code,
            )
            candidates.append(candidate)
            summary.candidate_entries.append(
                {
                    "project_id": candidate.project_id,
                    "project_code": candidate.project_code or None,
                    "project_name": candidate.project_name,
                    "page_url": candidate.page_url,
                    "list_url": candidate.list_url,
                    "row": row_with_source,
                    "disclosure_start": list_disclosure_start.isoformat()
                    if list_disclosure_start
                    else None,
                }
            )
            if list_disclosure_start:
                summary.candidate_dates.append(list_disclosure_start.isoformat())

    @staticmethod
    def _validate_url_filters(
        *,
        url: str,
        initial_url: str,
        field_name: str,
        allow_extra: bool,
    ) -> None:
        if not _same_list_endpoint(url, initial_url):
            raise _ListUrlContractError(
                f"{field_name} must use the initial list host and path"
            )
        initial_values = {
            key: values
            for key, values in _url_query_values(initial_url).items()
            if key != "page"
        }
        actual_values = {
            key: values
            for key, values in _url_query_values(url).items()
            if key != "page"
        }
        for key, values in initial_values.items():
            if actual_values.get(key) != values:
                raise _ListUrlContractError(
                    f"{field_name} changed initial filter {key!r}"
                )
        if not allow_extra and set(actual_values) != set(initial_values):
            raise _ListUrlContractError(
                f"{field_name} added non-pagination filters"
            )

    @staticmethod
    def _validate_url_page(
        *,
        url: str,
        field_name: str,
        expected_page: int,
        required: bool,
    ) -> bool:
        values = _url_query_values(url).get("page")
        if values is None:
            if required:
                raise _ListUrlContractError(f"{field_name} must include page={expected_page}")
            return False
        if len(values) != 1:
            raise _ListUrlContractError(f"{field_name} page must occur exactly once")
        try:
            actual_page = int(values[0])
        except ValueError as exc:
            raise _ListUrlContractError(f"{field_name} page must be an integer") from exc
        if actual_page != expected_page:
            raise _ListUrlContractError(
                f"{field_name} page={actual_page} is not contiguous; expected {expected_page}"
            )
        return True

    def _validate_list_response_urls(
        self,
        *,
        response: HttpFetchedText,
        initial_url: str,
        current_url: str,
        page_index: int,
    ) -> None:
        if _url_signature(response.source_url) != _url_signature(current_url):
            raise _ListUrlContractError("source_url does not match the requested list URL")
        self._validate_url_filters(
            url=response.source_url,
            initial_url=initial_url,
            field_name="source_url",
            allow_extra=False,
        )
        source_has_page = self._validate_url_page(
            url=response.source_url,
            field_name="source_url",
            expected_page=page_index,
            required=page_index > 1,
        )
        self._validate_url_filters(
            url=response.final_url,
            initial_url=initial_url,
            field_name="final_url",
            allow_extra=True,
        )
        self._validate_url_page(
            url=response.final_url,
            field_name="final_url",
            expected_page=page_index,
            required=source_has_page,
        )

    def _validate_next_page_url(
        self,
        *,
        next_url: str,
        initial_url: str,
        page_index: int,
    ) -> None:
        self._validate_url_filters(
            url=next_url,
            initial_url=initial_url,
            field_name="next_url",
            allow_extra=False,
        )
        self._validate_url_page(
            url=next_url,
            field_name="next_url",
            expected_page=page_index + 1,
            required=True,
        )

    def _fetch_list_html(self, url: str) -> HttpFetchedText:
        normalized_url = _normalize_list_url(url)
        last_exc: Exception | None = None
        attempts = max(1, int(self.list_fetch_attempts))
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(
                url=normalized_url,
                headers={**REQUEST_HEADERS, "Referer": BASE_URL + "/"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout,
                    context=self._ssl_context_insecure,
                ) as response:
                    raw = response.read()
                    headers = getattr(response, "headers", None)
                    charset = (
                        headers.get_content_charset()
                        if headers is not None and hasattr(headers, "get_content_charset")
                        else None
                    )
                    http_status = getattr(response, "status", None)
                    if http_status is None and hasattr(response, "getcode"):
                        http_status = response.getcode()
                    if http_status is None:
                        http_status = 200
                    final_url = response.geturl() if hasattr(response, "geturl") else normalized_url
                return _fetched_list_html(
                    raw_bytes=raw,
                    charset_hint=charset,
                    source_url=normalized_url,
                    final_url=final_url,
                    http_status=http_status,
                )
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if self._is_retryable_list_fetch_error(exc) and attempt < attempts:
                    time.sleep(0.8 * attempt)
                    continue
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if self._is_retryable_list_fetch_error(exc) and attempt < attempts:
                    time.sleep(0.8 * attempt)
                    continue
                break
        if last_exc is not None and self._should_use_browser_request_fallback(last_exc):
            self.logger.warning(
                "CQUAE list fetch exhausted urllib retries; trying browser request fallback: url=%s error=%s",
                normalized_url,
                last_exc,
            )
            return self._fetch_list_html_via_browser_request(normalized_url, last_exc)
        raise RuntimeError(f"GET {normalized_url} failed: {last_exc}") from last_exc

    @staticmethod
    def _is_retryable_list_fetch_error(exc: Exception) -> bool:
        if isinstance(exc, urllib.error.HTTPError):
            return exc.code in RETRYABLE_HTTP_STATUS_CODES
        if isinstance(
            exc,
            (
                urllib.error.URLError,
                http.client.HTTPException,
                TimeoutError,
                ssl.SSLError,
                OSError,
            ),
        ):
            return True
        text = str(exc).upper()
        retryable_markers = (
            "INCOMPLETEREAD",
            "INCOMPLETE READ",
            "REMOTE END CLOSED",
            "CONNECTION RESET",
            "CONNECTIONRESET",
            "CONNECTION ABORTED",
            "CONNECTION CLOSED",
            "EOF OCCURRED",
            "TIMEOUT",
            "TIMED OUT",
            "SSL",
        )
        return any(marker in text for marker in retryable_markers)

    def _should_use_browser_request_fallback(self, exc: Exception) -> bool:
        return bool(self.list_browser_request_fallback_enabled) and self._is_retryable_list_fetch_error(exc)

    def _fetch_list_html_via_browser_request(
        self,
        url: str,
        last_error: Exception,
    ) -> HttpFetchedText:
        return asyncio.run(self._fetch_list_html_via_browser_request_async(url, last_error))

    async def _fetch_list_html_via_browser_request_async(
        self,
        url: str,
        last_error: Exception,
    ) -> HttpFetchedText:
        from playwright.async_api import async_playwright

        timeout_ms = max(1, int(self.timeout)) * 1000
        async with async_playwright() as pw:
            request_context = await pw.request.new_context(
                extra_http_headers={**REQUEST_HEADERS, "Referer": BASE_URL + "/"},
                ignore_https_errors=True,
            )
            try:
                response = await request_context.get(url, timeout=timeout_ms)
                status = int(response.status)
                raw_bytes = await response.body()
                if not 200 <= status <= 299:
                    raise RuntimeError(
                        "browser-request-list-failed "
                        f"status={status} source=cquae url={url} previous_error={last_error}"
                    )
                headers = getattr(response, "headers", {}) or {}
                content_type = headers.get("content-type", "") if isinstance(headers, dict) else ""
                final_url = str(getattr(response, "url", "") or url)
                return _fetched_list_html(
                    raw_bytes=raw_bytes,
                    charset_hint=_charset_from_content_type(content_type),
                    source_url=url,
                    final_url=final_url,
                    http_status=status,
                )
            finally:
                await request_context.dispose()

    def _parse_list_item(self, item_node, *, current_url: str) -> Optional[Dict[str, Any]]:
        anchor = item_node.select_one("a.P_List_A[href]")
        if anchor is None:
            return None

        href = str(anchor.get("href") or "").replace("&amp;", "&").strip()
        if not href:
            return None

        anchor_id = str(anchor.get("id") or "").strip()
        fallback_id_match = re.search(r"A_snbn_(\d+)", anchor_id)
        if href.startswith("/Project?sn1=") and fallback_id_match:
            href = f"/Project/Show?id={fallback_id_match.group(1)}"

        page_url = urllib.parse.urljoin(BASE_URL, href)
        title = anchor.get_text(" ", strip=True)
        project_id = urllib.parse.parse_qs(urllib.parse.urlparse(page_url).query).get("id", [""])[0].strip()
        if not project_id:
            project_id = hashlib.md5(page_url.encode("utf-8")).hexdigest()[:16]

        text = item_node.get_text(" ", strip=True)
        start_match = LIST_START_LABEL_RE.search(text)
        end_match = LIST_END_LABEL_RE.search(text)
        price_match = LIST_PRICE_RE.search(text)
        raw_price = price_match.group(1) if price_match else ""

        return {
            "project_id": project_id,
            "project_name": title,
            "page_url": page_url,
            "list_url": current_url,
            "disclosure_start": start_match.group(1) if start_match else None,
            "disclosure_end": end_match.group(1) if end_match else None,
            "list_price": raw_price,
            "list_price_value": _parse_price(raw_price),
        }

    def _extract_next_page_url(self, *, soup: BeautifulSoup, current_url: str) -> Optional[str]:
        marker = "\u4e0b\u4e00\u9875"
        for anchor in soup.find_all("a", href=True):
            text = anchor.get_text(" ", strip=True)
            if marker not in text:
                continue
            href = str(anchor.get("href") or "").replace("&amp;", "&").strip()
            if not href or href.lower().startswith("javascript:"):
                continue
            return _normalize_list_url(urllib.parse.urljoin(current_url, href))
        return None

    async def _download_candidates_concurrently(
        self,
        *,
        candidates: List[_DownloadCandidate],
        summary: DownloadSummary,
        start: Optional[dt.date],
        end: Optional[dt.date],
    ) -> None:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright

        semaphore = asyncio.Semaphore(self.concurrency)
        total = len(candidates)
        completed = 0
        started_at = time.monotonic()
        progress_lock = asyncio.Lock()
        progress_log = ProgressLogThrottle(total=total)

        async with async_playwright() as pw:
            browser = await launch_chromium_browser(
                pw,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                ignore_https_errors=True,
                user_agent=REQUEST_HEADERS["User-Agent"],
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            await context.add_init_script(STEALTH_JS)
            try:

                async def worker(candidate: _DownloadCandidate) -> None:
                    nonlocal completed
                    async with semaphore:
                        await self._process_candidate(
                            candidate=candidate,
                            context=context,
                            summary=summary,
                            start=start,
                            end=end,
                            timeout_error_cls=PlaywrightTimeoutError,
                        )
                    async with progress_lock:
                        completed += 1
                        if progress_log.should_log(completed):
                            elapsed = max(0.001, time.monotonic() - started_at)
                            self.logger.info(
                                "Detail progress: %s/%s saved=%s detail_date_skipped=%s unavailable_skipped=%s errors=%s speed=%.2f/min",
                                completed,
                                total,
                                summary.saved,
                                summary.skipped_by_detail_date,
                                summary.skipped_by_detail_unavailable,
                                len(summary.typed_errors),
                                completed / elapsed * 60.0,
                            )

                await asyncio.gather(*(asyncio.create_task(worker(x)) for x in candidates))
            finally:
                await context.close()
                await browser.close()

    async def _process_candidate(
        self,
        *,
        candidate: _DownloadCandidate,
        context,
        summary: DownloadSummary,
        start: Optional[dt.date],
        end: Optional[dt.date],
        timeout_error_cls,
    ) -> None:
        rendered_html: Optional[str] = None
        final_url = candidate.page_url
        http_status: Optional[int] = None
        last_exc: Optional[Exception] = None
        unavailable_error: DetailUnavailableError | None = None

        for attempt in range(1, self._detail_retries + 2):
            page = await context.new_page()
            try:
                rendered_html, final_url, http_status = await self._fetch_rendered_html(
                    page=page,
                    candidate=candidate,
                )
                summary.detail_fetched += 1
                break
            except timeout_error_cls as exc:
                last_exc = exc
                if attempt <= self._detail_retries:
                    await asyncio.sleep(1.5 * attempt)
                else:
                    summary.typed_errors.append(
                        execute_failed_error(
                            source_id="cquae",
                            task_id=runtime_task_id("cquae", self.output_type),
                            raw_reason=f"project_id={candidate.project_id} page-timeout: {exc}",
                        )
                    )
            except DetailUnavailableError as exc:
                last_exc = exc
                unavailable_error = exc
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt <= self._detail_retries:
                    await asyncio.sleep(1.5 * attempt)
                else:
                    summary.typed_errors.append(
                        execute_failed_error(
                            source_id="cquae",
                            task_id=runtime_task_id("cquae", self.output_type),
                            raw_reason=f"project_id={candidate.project_id} page-fetch-failed: {exc}",
                        )
                    )
            finally:
                await page.close()

        if rendered_html is None:
            if unavailable_error is not None:
                summary.skipped_by_detail_unavailable += 1
                self.logger.info(
                    "Detail resource unavailable: source=cquae project_id=%s reason=%s final_url=%s title=%r html_len=%s",
                    candidate.project_id,
                    unavailable_error.reason,
                    unavailable_error.evidence.final_url,
                    unavailable_error.evidence.title,
                    unavailable_error.evidence.html_len,
                )
                return
            summary.detail_failed += 1
            if last_exc is not None:
                self.logger.error("Detail fetch failed: project_id=%s error=%s", candidate.project_id, last_exc)
            return

        disclosure_start = self._extract_disclosure_start_date(rendered_html)
        list_start = parse_loose_date(candidate.row.get("disclosure_start"))
        final_date = disclosure_start if disclosure_start is not None else list_start
        if start or end:
            if final_date is None:
                summary.date_missing_skipped += 1
                summary.skipped_by_detail_date += 1
                return
            if not in_date_range(final_date, start, end):
                summary.skipped_by_detail_date += 1
                return

        project_code = self._extract_project_code(
            html_text=rendered_html,
            page_url=final_url,
        )
        final_html_path, _ = resolve_submission_snapshot_target(
            archive_root=self.html_root,
            project_code=project_code or candidate.project_id,
            project_name=candidate.project_name,
            listing_date=(list_start or disclosure_start).isoformat() if (list_start or disclosure_start) else "",
            current_path=candidate.html_path,
        )
        if self.resume and _is_resume_complete(
            final_html_path,
            save_json=self.save_json,
            task_id=runtime_task_id("cquae", self.output_type),
            source_id="cquae",
            business_id=business_id_key(self.output_type),
        ):
            summary.skipped_by_resume += 1
            return
        task_id = runtime_task_id("cquae", self.output_type)
        if not reserve_download_target(
            summary,
            html_root=self.html_root,
            html_path=final_html_path,
            source_id="cquae",
            task_id=task_id,
        ):
            summary.detail_failed += 1
            return

        try:
            if os.path.normcase(os.path.abspath(final_html_path)) != os.path.normcase(
                os.path.abspath(candidate.html_path)
            ):
                remove_snapshot(candidate.html_path)
            self._save_complete_page(
                rendered_html=rendered_html,
                page_url=final_url,
                html_path=final_html_path,
            )
            if self.save_json:
                detail_payload = {
                    "task_id": task_id,
                    "source_id": "cquae",
                    "record_family": "listing",
                    "business_id": business_id_key(self.output_type),
                    "project_id": candidate.project_id,
                    "project_code": project_code,
                    "project_name": candidate.project_name,
                    "page_url": candidate.page_url,
                    "final_url": final_url,
                    **successful_http_evidence(
                        source_url=candidate.page_url,
                        http_status=http_status,
                    ),
                    "list_url": candidate.list_url,
                    "list_row": candidate.row,
                    "disclosure_start_date": disclosure_start.isoformat() if disclosure_start else None,
                }
                self._write_json(
                    json_path=os.path.splitext(final_html_path)[0] + ".json",
                    payload={**detail_payload, "save_status": "pending"},
                )
            else:
                self._write_resume_status(
                    final_html_path,
                    "pending",
                    source_url=candidate.page_url,
                    http_status=http_status,
                )
            if self.save_json:
                self._write_json(
                    json_path=os.path.splitext(final_html_path)[0] + ".json",
                    payload={
                        **detail_payload,
                        "save_status": "complete",
                        **archive_integrity_fields(final_html_path),
                    },
                )
            else:
                self._write_resume_status(
                    final_html_path,
                    "complete",
                    source_url=candidate.page_url,
                    http_status=http_status,
                )
            self._notify_item_saved(
                final_html_path=final_html_path,
                candidate=candidate,
                disclosure_start=disclosure_start,
                project_code=project_code,
            )
            self._resume_index[candidate.project_id] = {
                "project_code": project_code,
                "html_relpath": os.path.relpath(final_html_path, self.html_root),
            }
        except Exception as exc:  # noqa: BLE001
            mark_artifact_save_failed(
                html_path=final_html_path,
                save_json=self.save_json,
                write_json=lambda json_path, payload: self._write_json(
                    json_path=json_path,
                    payload=payload,
                ),
                failure_identity={
                    "task_id": runtime_task_id("cquae", self.output_type),
                    "source_id": "cquae",
                    "record_family": "listing",
                    "business_id": business_id_key(self.output_type),
                },
                write_resume_status=lambda html_path, save_status: self._write_resume_status(
                    html_path,
                    save_status,
                    source_url=candidate.page_url,
                    http_status=http_status,
                ),
                logger=self.logger,
            )
            summary.detail_failed += 1
            summary.typed_errors.append(
                save_failed_error(
                    source_id="cquae",
                    task_id=runtime_task_id("cquae", self.output_type),
                    raw_reason=str(exc),
                )
            )
            return

        summary.saved += 1
        record_downloaded_target(summary, html_root=self.html_root, html_path=final_html_path)

    async def _fetch_rendered_html(
        self,
        *,
        page,
        candidate: _DownloadCandidate,
    ) -> tuple[str, str, int]:
        raw_row = getattr(candidate, "row", {})
        row = raw_row if isinstance(raw_row, dict) else {}
        candidate_list_url = str(getattr(candidate, "list_url", "") or "").strip()
        candidate_page_url = str(getattr(candidate, "page_url", "") or "").strip()
        navigation_list_url = str(
            row.get("navigation_list_url") or candidate_list_url
        ).strip()
        await page.goto(
            navigation_list_url,
            wait_until="domcontentloaded",
            timeout=self._render_timeout_ms,
        )
        await page.wait_for_timeout(1200)
        response = await page.goto(
            candidate_page_url,
            wait_until="domcontentloaded",
            referer=candidate_list_url,
            timeout=self._render_timeout_ms,
        )
        http_status = successful_http_evidence(
            source_url=candidate_page_url,
            http_status=getattr(response, "status", None),
        )["http_status"]

        last_html = ""
        last_title = ""
        for _ in range(12):
            await page.wait_for_timeout(3000)
            try:
                html = await page.content()
            except Exception:
                continue
            title = await page.title()
            last_html = html
            last_title = title
            if self._is_real_detail_page(
                html=html,
                title=title,
                expected_name=candidate.project_name,
                current_url=page.url,
            ):
                return html, page.url, http_status

        if self._is_home_redirect_detail_unavailable(
            html=last_html,
            title=last_title,
            current_url=page.url,
        ):
            raise DetailUnavailableError(
                reason="home_redirect",
                final_url=page.url,
                title=last_title,
                html_len=len(last_html),
                expected_identifier=candidate.project_id,
            )

        raise RuntimeError(
            "detail-page-not-ready: "
            f"project_id={candidate.project_id} final_url={page.url} title={last_title!r} html_len={len(last_html)}"
        )

    @staticmethod
    def _is_home_redirect_detail_unavailable(*, html: str, title: str, current_url: str) -> bool:
        parsed = urllib.parse.urlsplit(str(current_url or ""))
        base = urllib.parse.urlsplit(BASE_URL)
        is_home_url = (
            parsed.scheme.lower() == base.scheme.lower()
            and parsed.netloc.lower() == base.netloc.lower()
            and (parsed.path or "/").rstrip("/") == ""
        )
        if not is_home_url:
            return False
        blob = f"{title} {html}"
        return "首页" in title and "重庆产权交易网" in blob

    @staticmethod
    def _is_real_detail_page(*, html: str, title: str, expected_name: str, current_url: str) -> bool:
        if "__jsl_clearance_s" in html:
            return False
        marker = "\u91cd\u5e86\u4ea7\u6743\u4ea4\u6613\u7f51"
        if marker not in title and marker not in html:
            return False
        if len(html) <= 8000:
            return False

        blob = _normalize_text(title + " " + html)
        detail_markers = (
            "\u9879\u76ee\u540d\u79f0",
            "\u6302\u724c\u4ef7",
            "\u8f6c\u8ba9\u5e95\u4ef7",
            "\u6302\u724c\u5f00\u59cb\u65e5\u671f",
            "\u4fe1\u606f\u62ab\u9732",
        )
        marker_hits = sum(1 for item in detail_markers if item in html)

        if "/Project/Object/Obj_Show" in current_url:
            return marker_hits >= 2 and len(html) > 30000

        normalized_name = _normalize_text(expected_name)
        if _looks_usable_expected_name(expected_name) and normalized_name not in blob:
            return False
        return marker_hits >= 2

    def _extract_project_code(
        self,
        *,
        html_text: str,
        page_url: str,
    ) -> str:
        soup = BeautifulSoup(html_text, "html.parser")

        for label in soup.find_all(["th", "td", "span", "div"]):
            label_text = re.sub(r"\s+", "", label.get_text(" ", strip=True))
            if label_text != "\u9879\u76ee\u7f16\u53f7":
                continue
            for sibling in label.find_next_siblings(["td", "th", "span", "div"], limit=3):
                candidate = str(sibling.get_text(" ", strip=True)).strip()
                if not candidate:
                    continue
                code = self._match_project_code(candidate)
                if code:
                    return code

        code = self._match_project_code(soup.get_text(" ", strip=True))
        if code:
            return code

        for source in (html_text, page_url):
            match = re.search(r"/Project/(?:Show|Object/Obj_Show\d+)\?id=(\d{5,})", source)
            if match:
                return f"CQID{match.group(1)}"

        return ""

    @staticmethod
    def _match_project_code(text: str) -> str:
        raw = str(text or "").strip().upper()
        match = PROJECT_CODE_RE.search(raw)
        if match:
            return match.group(1).upper()
        fallback_match = FALLBACK_PROJECT_CODE_RE.search(raw)
        if fallback_match:
            return fallback_match.group(1)
        return ""

    def _save_complete_page(self, *, rendered_html: str, page_url: str, html_path: str) -> None:
        self._snapshot_saver.save_complete_page(
            rendered_html=rendered_html,
            page_url=page_url,
            html_path=html_path,
        )
        self._write_invalid_shell_evidence_if_needed(
            html_path=html_path,
            page_url=page_url,
            rendered_html=rendered_html,
        )

    @staticmethod
    def _sha256_text(value: str) -> str:
        return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

    def _write_invalid_shell_evidence_if_needed(
        self,
        *,
        html_path: str,
        page_url: str,
        rendered_html: str,
    ) -> None:
        if "__jsl_clearance_s" not in str(rendered_html or ""):
            return
        try:
            with open(html_path, encoding="utf-8") as handle:
                saved_html = handle.read()
        except OSError:
            saved_html = str(rendered_html or "")

        project_code = self._extract_project_code(html_text=saved_html, page_url=page_url)
        identity_hints: Dict[str, str] = {}
        if project_code:
            identity_hints["project_code_hash"] = self._sha256_text(project_code)

        evidence = {
            "schema_version": 1,
            "page_kind": "invalid_shell",
            "source_url_hash": self._sha256_text(page_url),
            "final_url_hash": self._sha256_text(page_url),
            "content_sha256": self._sha256_text(saved_html),
            "identity_hints": identity_hints,
        }
        with open(f"{html_path}.peap-evidence.json", "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, ensure_ascii=False, indent=2, sort_keys=True)

    def _notify_item_saved(
        self,
        *,
        final_html_path: str,
        candidate: _DownloadCandidate,
        disclosure_start: Optional[dt.date],
        project_code: str,
    ) -> None:
        callback = self.item_saved_callback
        if callback is None:
            return
        callback(
            {
                "source_file": final_html_path,
                "page_url": candidate.page_url,
                "project_code": project_code,
                "project_name": candidate.project_name,
                "listing_date": disclosure_start.isoformat() if disclosure_start else "",
                "source_id": "cquae",
                "business_id": business_id_key(self.output_type),
                "row": candidate.row,
            }
        )

    @staticmethod
    def _load_resume_index(path: Optional[str]) -> Dict[str, Dict[str, str]]:
        if not path or not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cquae resume index invalid: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"cquae resume index invalid: {path}: root must be an object")

        index: Dict[str, Dict[str, str]] = {}
        for project_id, raw in payload.items():
            if not isinstance(project_id, str) or not isinstance(raw, dict):
                raise ValueError(
                    f"cquae resume index invalid: {path}: entry {project_id!r} must be an object"
                )
            project_code = str(raw.get("project_code") or "").strip()
            html_relpath = str(raw.get("html_relpath") or raw.get("html_name") or "").strip()
            if not project_code and not html_relpath:
                raise ValueError(
                    f"cquae resume index invalid: {path}: entry {project_id!r} has no project_code or html_relpath"
                )
            index[project_id] = {
                "project_code": project_code,
                "html_relpath": html_relpath,
            }
        return index

    def _save_resume_index(self) -> None:
        if not self._resume_index_path:
            return
        payload_text = json.dumps(
            self._resume_index,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        if os.path.isfile(self._resume_index_path):
            try:
                with open(self._resume_index_path, "r", encoding="utf-8") as handle:
                    if handle.read() == payload_text:
                        return
            except OSError:
                pass
        temp_path = f"{self._resume_index_path}.part"
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write(payload_text)
        os.replace(temp_path, self._resume_index_path)

    def _resolve_resume_html_path(
        self,
        *,
        output_dir: str,
        project_id: str,
        project_code: str,
        project_name: str,
        listing_date: str,
    ) -> Optional[str]:
        if project_code:
            return resolve_submission_snapshot_target(
                archive_root=output_dir,
                project_code=project_code,
                project_name=project_name,
                listing_date=listing_date,
            )[0]

        cached = self._resume_index.get(project_id)
        if cached is None:
            cached = {}
        elif not isinstance(cached, dict):
            raise TypeError("resume index entry must be a mapping")
        html_relpath = str(cached.get("html_relpath") or cached.get("html_name") or "").strip()
        if html_relpath:
            return os.path.join(output_dir, html_relpath)

        cached_code = str(cached.get("project_code") or "").strip()
        if cached_code:
            return resolve_submission_snapshot_target(
                archive_root=output_dir,
                project_code=cached_code,
                project_name=project_name,
                listing_date=listing_date,
            )[0]
        return None

    def _write_json(self, *, json_path: str, payload: Dict[str, Any]) -> None:
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _write_resume_status(
        self,
        html_path: str,
        save_status: str,
        *,
        source_url: str,
        http_status: int,
    ) -> None:
        normalized_status = str(save_status or "").strip() or "pending"
        payload = {
            "schema_version": 1,
            "task_id": runtime_task_id("cquae", self.output_type),
            "source_id": "cquae",
            "record_family": "listing",
            "business_id": business_id_key(self.output_type),
            "save_status": normalized_status,
            **successful_http_evidence(
                source_url=source_url,
                http_status=http_status,
            ),
        }
        if normalized_status == "complete":
            payload.update(archive_integrity_fields(html_path))
        self._write_json(
            json_path=_resume_status_path(html_path),
            payload=payload,
        )

    def _extract_disclosure_start_date(self, html_text: str) -> Optional[dt.date]:
        text = BeautifulSoup(html_text, "html.parser").get_text(" ", strip=True)
        for pattern in DISCLOSURE_START_PATTERNS:
            match = re.search(pattern, text)
            if match:
                parsed = parse_loose_date(match.group(1))
                if parsed is not None:
                    return parsed
        return None


class ChongqingEquityTransferDownloader(ChongqingProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_EQUITY_TRANSFER,
            list_sources=_cquae_list_sources("equity_transfer"),
            **kwargs,
        )


class ChongqingCapitalIncreaseDownloader(ChongqingProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_CAPITAL_INCREASE,
            list_sources=_cquae_list_sources("capital_increase"),
            **kwargs,
        )


class ChongqingPhysicalAssetDownloader(ChongqingProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PHYSICAL_ASSET,
            list_sources=_cquae_list_sources("physical_asset"),
            **kwargs,
        )


class ChongqingPreDisclosureDownloader(ChongqingProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PRE_DISCLOSURE,
            list_sources=_cquae_list_sources("pre_disclosure"),
            **kwargs,
        )
