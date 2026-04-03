"""Chongqing exchange downloader."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from bs4 import BeautifulSoup

from ..constants import (
    TYPE_CAPITAL_INCREASE,
    TYPE_EQUITY_TRANSFER,
    TYPE_PHYSICAL_ASSET,
    TYPE_PRE_DISCLOSURE,
)
from ..download_errors import execute_failed_error, invalid_candidate_error, list_failed_error, save_failed_error
from ..submission_layout import resolve_submission_snapshot_target
from .common import DownloadSummary, in_date_range, parse_bound, parse_loose_date, project_type_key
from .snapshot_utils import SnapshotSaver, is_snapshot_complete, remove_snapshot

BASE_URL = "https://www.cquae.com"

REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
}

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


@dataclass
class _DownloadCandidate:
    project_id: str
    project_name: str
    page_url: str
    html_path: str
    list_url: str
    row: Dict[str, Any]
    project_code: str = ""


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


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().upper()


def _looks_usable_expected_name(value: str) -> bool:
    normalized = _normalize_text(value)
    if len(normalized) < 4:
        return False
    if "\ufffd" in normalized:
        return False
    return normalized.count("?") * 2 < len(normalized)


def _decode_html(raw: bytes, charset_hint: Optional[str]) -> str:
    tried: List[str] = []
    for encoding in (charset_hint, "utf-8", "gb18030"):
        if not encoding:
            continue
        norm = str(encoding).strip().lower()
        if not norm or norm in tried:
            continue
        tried.append(norm)
        try:
            return raw.decode(norm)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


class ChongqingProjectDownloader:
    """Download Chongqing detail pages after parsing list pages."""

    manifest_list_endpoint = f"{BASE_URL}/project"
    manifest_detail_route = "/Project/Show"
    manifest_date_field_candidates = ("list_disclosure_start",)

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
    ):
        self.html_root = html_root
        self.page_size = max(1, int(page_size))
        self.max_pages = max_pages if max_pages is None else max(1, int(max_pages))
        self.concurrency = max(1, int(concurrency))
        self.resume = bool(resume)
        self.timeout = max(10, int(timeout))
        self.save_json = bool(save_json)
        self.output_type = str(output_type or TYPE_EQUITY_TRANSFER)
        self.list_sources = list(list_sources or [])
        self.logger = logger or logging.getLogger("parser_v2")
        self.item_saved_callback = item_saved_callback
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

        list_accounted = (
            summary.skipped_by_list_date
            + summary.skipped_by_resume
            + summary.skipped_by_duplicate
            + summary.skipped_by_missing_xmid
            + summary.detail_candidates
        )
        detail_accounted = summary.saved + summary.skipped_by_detail_date + summary.detail_failed
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
        seen_ids: Set[str] = set()
        for source in self.list_sources:
            current_url = source.list_url
            seen_pages: Set[str] = set()
            page_index = 0
            while current_url and current_url not in seen_pages:
                if self.max_pages is not None and page_index >= self.max_pages:
                    break
                seen_pages.add(current_url)
                page_index += 1

                try:
                    html = self._fetch_list_html(current_url)
                except Exception as exc:  # noqa: BLE001
                    summary.pages_requested += 1
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id="cquae",
                            task_id=f"cquae:{project_type_key(self.output_type)}",
                            raw_reason=f"list-{source.label}-page-{page_index}-request-failed: {exc}",
                        )
                    )
                    break

                summary.pages_requested += 1
                soup = BeautifulSoup(html, "html.parser")
                item_nodes = soup.select("div.n2_List.itcon")
                if not item_nodes:
                    self.logger.warning("No list items found for source=%s url=%s", source.label, current_url)
                    break

                self._item_nodes_to_candidates(
                    item_nodes=item_nodes,
                    source=source,
                    current_url=current_url,
                    output_dir=output_dir,
                    summary=summary,
                    candidates=candidates,
                    seen_ids=seen_ids,
                    start=start,
                    end=end,
                )
                current_url = self._extract_next_page_url(soup=soup, current_url=current_url)

    def _item_nodes_to_candidates(
        self,
        *,
        item_nodes: List[Any],
        source: _ListSource,
        current_url: str,
        output_dir: str,
        summary: DownloadSummary,
        candidates: List[_DownloadCandidate],
        seen_ids: Set[str],
        start: Optional[dt.date],
        end: Optional[dt.date],
    ) -> None:
        for item in item_nodes:
            row = self._parse_list_item(item, current_url=current_url)
            if row is None:
                continue
            summary.listed_items += 1

            project_id = str(row.get("project_id") or "").strip()
            if not project_id:
                summary.skipped_by_missing_xmid += 1
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="cquae",
                        task_id=f"cquae:{project_type_key(self.output_type)}",
                        raw_reason=f"list-{source.label}-missing-project-id",
                    )
                )
                continue
            if project_id in seen_ids:
                summary.skipped_by_duplicate += 1
                continue
            seen_ids.add(project_id)

            list_disclosure_start = parse_loose_date(row.get("list_disclosure_start"))
            if (start or end) and list_disclosure_start and not in_date_range(list_disclosure_start, start, end):
                summary.skipped_by_list_date += 1
                continue

            project_code = str(row.get("project_code") or "").strip().upper()
            project_name = str(row.get("project_name") or "").strip()
            html_seed = project_code or project_id
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=output_dir,
                project_code=html_seed,
                project_name=project_name,
                listing_date=str(row.get("list_disclosure_start") or ""),
            )
            resume_html_path = self._resolve_resume_html_path(
                output_dir=output_dir,
                project_id=project_id,
                project_code=project_code,
                project_name=project_name,
                listing_date=str(row.get("list_disclosure_start") or ""),
            )
            if self.resume and resume_html_path and is_snapshot_complete(resume_html_path):
                summary.skipped_by_resume += 1
                continue

            row_with_source = {**row, "list_source": source.label, "list_url": current_url}
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
                    "list_disclosure_start": row.get("list_disclosure_start"),
                }
            )
            if row.get("list_disclosure_start"):
                summary.candidate_dates.append(str(row["list_disclosure_start"]))

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
                        task_id=f"cquae:{project_type_key(self.output_type)}",
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
                        task_id=f"cquae:{project_type_key(self.output_type)}",
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
                raw.get("list_disclosure_start") or row.get("list_disclosure_start")
            )
            if (start or end) and list_disclosure_start and not in_date_range(list_disclosure_start, start, end):
                summary.skipped_by_list_date += 1
                continue

            page_url = str(raw.get("page_url") or row.get("page_url") or "").strip()
            if not page_url:
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="cquae",
                        task_id=f"cquae:{project_type_key(self.output_type)}",
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
            if self.resume and resume_html_path and is_snapshot_complete(resume_html_path):
                summary.skipped_by_resume += 1
                continue

            row_with_source = dict(row)
            row_with_source["list_disclosure_start"] = (
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
                    "list_disclosure_start": list_disclosure_start.isoformat()
                    if list_disclosure_start
                    else None,
                }
            )
            if list_disclosure_start:
                summary.candidate_dates.append(list_disclosure_start.isoformat())

    def _fetch_list_html(self, url: str) -> str:
        normalized_url = _normalize_list_url(url)
        last_exc: Exception | None = None
        for attempt in range(1, 4):
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
                    charset = response.headers.get_content_charset()
                return _decode_html(raw, charset)
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code in RETRYABLE_HTTP_STATUS_CODES and attempt < 3:
                    time.sleep(0.8 * attempt)
                    continue
                break
            except urllib.error.URLError as exc:
                last_exc = exc
                if attempt < 3:
                    time.sleep(0.8 * attempt)
                    continue
                break
        raise RuntimeError(f"GET {normalized_url} failed: {last_exc}") from last_exc

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
            "list_disclosure_start": start_match.group(1) if start_match else None,
            "list_disclosure_end": end_match.group(1) if end_match else None,
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

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
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
                        elapsed = max(0.001, time.monotonic() - started_at)
                        self.logger.info(
                            "Detail progress: %s/%s saved=%s detail_date_skipped=%s errors=%s speed=%.2f/min",
                            completed,
                            total,
                            summary.saved,
                            summary.skipped_by_detail_date,
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
        last_exc: Optional[Exception] = None

        for attempt in range(1, self._detail_retries + 2):
            page = await context.new_page()
            try:
                rendered_html, final_url = await self._fetch_rendered_html(page=page, candidate=candidate)
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
                            task_id=f"cquae:{project_type_key(self.output_type)}",
                            raw_reason=f"project_id={candidate.project_id} page-timeout: {exc}",
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt <= self._detail_retries:
                    await asyncio.sleep(1.5 * attempt)
                else:
                    summary.typed_errors.append(
                        execute_failed_error(
                            source_id="cquae",
                            task_id=f"cquae:{project_type_key(self.output_type)}",
                            raw_reason=f"project_id={candidate.project_id} page-fetch-failed: {exc}",
                        )
                    )
            finally:
                await page.close()

        if rendered_html is None:
            summary.detail_failed += 1
            if last_exc is not None:
                self.logger.error("Detail fetch failed: project_id=%s error=%s", candidate.project_id, last_exc)
            return

        disclosure_start = self._extract_disclosure_start_date(rendered_html)
        list_start = parse_loose_date(candidate.row.get("list_disclosure_start"))
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
        except Exception as exc:  # noqa: BLE001
            summary.detail_failed += 1
            summary.typed_errors.append(
                save_failed_error(
                    source_id="cquae",
                    task_id=f"cquae:{project_type_key(self.output_type)}",
                    raw_reason=str(exc),
                )
            )
            return

        if self.save_json:
            self._write_json(
                json_path=os.path.splitext(final_html_path)[0] + ".json",
                payload={
                    "project_id": candidate.project_id,
                    "project_code": project_code,
                    "project_name": candidate.project_name,
                    "page_url": candidate.page_url,
                    "final_url": final_url,
                    "list_url": candidate.list_url,
                    "list_row": candidate.row,
                    "disclosure_start_date": disclosure_start.isoformat() if disclosure_start else None,
                },
            )
        self._resume_index[candidate.project_id] = {
            "project_code": project_code,
            "html_relpath": os.path.relpath(final_html_path, self.html_root),
        }
        self._notify_item_saved(
            final_html_path=final_html_path,
            candidate=candidate,
            disclosure_start=disclosure_start,
            project_code=project_code,
        )
        summary.saved += 1

    async def _fetch_rendered_html(self, *, page, candidate: _DownloadCandidate) -> tuple[str, str]:
        await page.goto(candidate.list_url, wait_until="domcontentloaded", timeout=self._render_timeout_ms)
        await page.wait_for_timeout(1200)
        await page.goto(
            candidate.page_url,
            wait_until="domcontentloaded",
            referer=candidate.list_url,
            timeout=self._render_timeout_ms,
        )

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
                return html, page.url

        raise RuntimeError(
            "detail-page-not-ready: "
            f"project_id={candidate.project_id} final_url={page.url} title={last_title!r} html_len={len(last_html)}"
        )

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
        try:
            callback(
                {
                    "source_file": final_html_path,
                    "page_url": candidate.page_url,
                    "project_code": project_code,
                    "project_name": candidate.project_name,
                    "listing_date": disclosure_start.isoformat() if disclosure_start else "",
                    "source_id": "cquae",
                    "project_type": self.output_type,
                    "row": candidate.row,
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("item_saved_callback failed: %s", exc)

    @staticmethod
    def _load_resume_index(path: Optional[str]) -> Dict[str, Dict[str, str]]:
        if not path or not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}

        index: Dict[str, Dict[str, str]] = {}
        for project_id, raw in payload.items():
            if not isinstance(project_id, str) or not isinstance(raw, dict):
                continue
            project_code = str(raw.get("project_code") or "").strip()
            html_relpath = str(raw.get("html_relpath") or raw.get("html_name") or "").strip()
            if not project_code and not html_relpath:
                continue
            index[project_id] = {
                "project_code": project_code,
                "html_relpath": html_relpath,
            }
        return index

    def _save_resume_index(self) -> None:
        if not self._resume_index_path:
            return
        temp_path = f"{self._resume_index_path}.part"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(self._resume_index, handle, ensure_ascii=False, indent=2)
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

        cached = self._resume_index.get(project_id) or {}
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
            list_sources=[
                _ListSource(
                    label="equity-formal",
                    list_url=_build_list_url({"q": "s", "projectID": 1, "nt": 1, "priceID": 32}),
                )
            ],
            **kwargs,
        )


class ChongqingCapitalIncreaseDownloader(ChongqingProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_CAPITAL_INCREASE,
            list_sources=[
                _ListSource(
                    label="capital-formal",
                    list_url=_build_list_url({"q": "s", "projectID": 2, "ly": 34, "nt": 1, "priceID": 33}),
                )
            ],
            **kwargs,
        )


class ChongqingPhysicalAssetDownloader(ChongqingProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PHYSICAL_ASSET,
            list_sources=[
                _ListSource(
                    label="physical-5000w-to-1y",
                    list_url=_build_list_url({"q": "s", "projectID": 3, "price": "5000\u4e07-1\u4ebf"}),
                ),
                _ListSource(
                    label="physical-over-1y",
                    list_url=_build_list_url({"q": "s", "projectID": 3, "price": "1\u4ebf\u4ee5\u4e0a"}),
                ),
            ],
            **kwargs,
        )


class ChongqingPreDisclosureDownloader(ChongqingProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PRE_DISCLOSURE,
            list_sources=[
                _ListSource(
                    label="equity-pre",
                    list_url=_build_list_url({"q": "s", "projectID": 1, "nt": 3, "priceID": 35}),
                ),
                _ListSource(
                    label="capital-pre",
                    list_url=_build_list_url({"q": "s", "projectID": 2, "ly": 34, "nt": 3, "priceID": 34}),
                ),
            ],
            **kwargs,
        )
