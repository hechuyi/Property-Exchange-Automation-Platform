"""Tianjin project-center downloader."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
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
from .snapshot_utils import SnapshotSaver, is_snapshot_complete

BASE_URL = "https://trade.tpre.cn"
LIST_API_URL = f"{BASE_URL}/up/biz/project/anmuas/page"

REQUEST_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
}

DISCLOSURE_START_PATTERNS = (
    r"(?:\u4fe1\u606f\u62ab\u9732\u8d77\u59cb\u65e5\u671f|\u6302\u724c\u5f00\u59cb\u65e5\u671f|\u6302\u724c\u8d77\u59cb\u65e5\u671f|\u62ab\u9732\u5f00\u59cb\u65e5\u671f|\u62ab\u9732\u8d77\u6b62\u65e5\u671f|\u6302\u724c\u8d77\u6b62\u65e5\u671f)\s*[:\uff1a]?\s*(20\d{2}[\u5e74./-]\d{1,2}[\u6708./-]\d{1,2}\u65e5?)",
    r"(20\d{2}[\u5e74./-]\d{1,2}[\u6708./-]\d{1,2}\u65e5?)\s*(?:\u81f3|\u5230|-|\u2014|~|\uff5e)\s*20\d{2}[\u5e74./-]\d{1,2}[\u6708./-]\d{1,2}\u65e5?",
)
PROJECT_CODE_RE = re.compile(
    r"((?:[A-Z]{2}|[A-Z]\d)\d{4}TJ\d+(?:-\d+)?)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class _ListQuerySpec:
    label: str
    system_code: str
    biz_type_code: str
    extra_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _DownloadCandidate:
    project_code: str
    project_name: str
    page_url: str
    html_path: str
    row: Dict[str, Any]


def _extract_project_code(text: str) -> str:
    raw = str(text or "").strip().upper()
    match = PROJECT_CODE_RE.search(raw)
    return match.group(1).upper() if match else ""


class TpreProjectDownloader:
    """Download Tianjin detail pages for parser ingestion."""

    manifest_list_endpoint = LIST_API_URL
    manifest_detail_route = "/transaction-view"
    manifest_date_field_candidates = ("disclosure_start",)

    def __init__(
        self,
        *,
        html_root: str,
        page_size: int = 20,
        max_pages: Optional[int] = None,
        concurrency: int = 4,
        resume: bool = False,
        timeout: int = 20,
        save_json: bool = False,
        output_type: str = TYPE_EQUITY_TRANSFER,
        list_queries: Optional[List[_ListQuerySpec]] = None,
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
        self.list_queries = list(list_queries or [])
        self.logger = logger or logging.getLogger("parser_v2")
        self.item_saved_callback = item_saved_callback
        self._render_timeout_ms = max(90, self.timeout) * 1000
        self._detail_retries = 2
        self._snapshot_saver = SnapshotSaver(
            user_agent=REQUEST_HEADERS["User-Agent"],
            timeout=self.timeout,
        )

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

        summary = DownloadSummary()
        candidates: List[_DownloadCandidate] = []
        self.logger.info(
            "Start TPRE download: type=%s start_date=%s end_date=%s page_size=%s max_pages=%s concurrency=%s resume=%s output=%s",
            self.output_type,
            start.isoformat() if start else "-",
            end.isoformat() if end else "-",
            self.page_size,
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
        seen_codes: Set[str] = set()
        for query in self.list_queries:
            page_index = 1
            total_pages = 1
            while page_index <= total_pages:
                if self.max_pages is not None and page_index > self.max_pages:
                    break
                try:
                    payload = self._query_list_page(page_index=page_index, query=query)
                except Exception as exc:  # noqa: BLE001
                    summary.pages_requested += 1
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id="tpre",
                            task_id=f"tpre:{project_type_key(self.output_type)}",
                            raw_reason=f"list-{query.label}-page-{page_index}-request-failed: {exc}",
                        )
                    )
                    break

                summary.pages_requested += 1
                if int(payload.get("code", -1)) != 0:
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id="tpre",
                            task_id=f"tpre:{project_type_key(self.output_type)}",
                            raw_reason=f"list-{query.label}-page-{page_index}-failed: {payload.get('message')}",
                        )
                    )
                    break

                data = payload.get("data") or {}
                rows = data.get("records") or []
                if not isinstance(rows, list):
                    summary.typed_errors.append(
                        invalid_candidate_error(
                            source_id="tpre",
                            task_id=f"tpre:{project_type_key(self.output_type)}",
                            raw_reason=f"list-{query.label}-page-{page_index}-invalid-data",
                        )
                    )
                    break

                total = int(data.get("total") or 0)
                total_pages = max(1, math.ceil(total / self.page_size)) if total else 1
                self._rows_to_candidates(
                    rows=rows,
                    query=query,
                    output_dir=output_dir,
                    summary=summary,
                    candidates=candidates,
                    seen_codes=seen_codes,
                    start=start,
                    end=end,
                )
                page_index += 1

    def _rows_to_candidates(
        self,
        *,
        rows: List[Dict[str, Any]],
        query: _ListQuerySpec,
        output_dir: str,
        summary: DownloadSummary,
        candidates: List[_DownloadCandidate],
        seen_codes: Set[str],
        start: Optional[dt.date],
        end: Optional[dt.date],
    ) -> None:
        for row in rows:
            if not isinstance(row, dict):
                continue
            summary.listed_items += 1

            project_code = _extract_project_code(row.get("projectCode") or "") or _extract_project_code(
                row.get("title") or ""
            )
            if not project_code:
                summary.skipped_by_missing_xmid += 1
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="tpre",
                        task_id=f"tpre:{project_type_key(self.output_type)}",
                        raw_reason=f"list-{query.label}-missing-project-code",
                    )
                )
                continue
            if project_code in seen_codes:
                summary.skipped_by_duplicate += 1
                continue
            seen_codes.add(project_code)

            list_disclosure_start = parse_loose_date(row.get("startTime"))
            if start or end:
                if list_disclosure_start is None:
                    summary.skipped_by_list_date += 1
                    continue
                if not in_date_range(list_disclosure_start, start, end):
                    summary.skipped_by_list_date += 1
                    continue

            page_url = urllib.parse.urljoin(BASE_URL, str(row.get("projectLink") or "").strip())
            if not page_url:
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="tpre",
                        task_id=f"tpre:{project_type_key(self.output_type)}",
                        raw_reason=f"list-{query.label}-missing-page-url: project_code={project_code}",
                    )
                )
                continue

            project_name = str(row.get("title") or row.get("projectName") or "").strip()
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=output_dir,
                project_code=project_code,
                project_name=project_name,
                listing_date=list_disclosure_start.isoformat() if list_disclosure_start else "",
            )
            if self.resume and is_snapshot_complete(html_path):
                summary.skipped_by_resume += 1
                continue

            row_with_source = {
                **row,
                "list_source": query.label,
                "list_disclosure_start": list_disclosure_start.isoformat() if list_disclosure_start else None,
            }
            candidate = _DownloadCandidate(
                project_code=project_code,
                project_name=project_name,
                page_url=page_url,
                html_path=html_path,
                row=row_with_source,
            )
            candidates.append(candidate)
            summary.candidate_entries.append(
                {
                    "project_code": candidate.project_code,
                    "project_name": candidate.project_name,
                    "page_url": candidate.page_url,
                    "row": row_with_source,
                    "list_disclosure_start": list_disclosure_start.isoformat()
                    if list_disclosure_start
                    else None,
                }
            )
            if list_disclosure_start:
                summary.candidate_dates.append(list_disclosure_start.isoformat())

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
        seen_codes: Set[str] = set()
        for index, raw in enumerate(prefetched_candidates, start=1):
            if not isinstance(raw, dict):
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="tpre",
                        task_id=f"tpre:{project_type_key(self.output_type)}",
                        raw_reason=f"prefetched-entry-{index}-invalid-format",
                    )
                )
                continue
            summary.listed_items += 1

            project_code = _extract_project_code(raw.get("project_code") or "") or _extract_project_code(
                raw.get("project_name") or ""
            )
            if not project_code:
                summary.skipped_by_missing_xmid += 1
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="tpre",
                        task_id=f"tpre:{project_type_key(self.output_type)}",
                        raw_reason=f"prefetched-entry-{index}-missing-project-code",
                    )
                )
                continue
            if project_code in seen_codes:
                summary.skipped_by_duplicate += 1
                continue
            seen_codes.add(project_code)

            row_raw = raw.get("row")
            row = row_raw if isinstance(row_raw, dict) else {}
            list_disclosure_start = parse_loose_date(raw.get("list_disclosure_start") or row.get("startTime"))
            if start or end:
                if list_disclosure_start is None:
                    summary.skipped_by_list_date += 1
                    continue
                if not in_date_range(list_disclosure_start, start, end):
                    summary.skipped_by_list_date += 1
                    continue

            page_url = urllib.parse.urljoin(
                BASE_URL,
                str(raw.get("page_url") or row.get("projectLink") or "").strip(),
            )
            if not page_url:
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="tpre",
                        task_id=f"tpre:{project_type_key(self.output_type)}",
                        raw_reason=f"prefetched-entry-{index}-missing-page-url: project_code={project_code}",
                    )
                )
                continue

            project_name = str(raw.get("project_name") or row.get("title") or "").strip()
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=output_dir,
                project_code=project_code,
                project_name=project_name,
                listing_date=list_disclosure_start.isoformat() if list_disclosure_start else "",
            )
            if self.resume and is_snapshot_complete(html_path):
                summary.skipped_by_resume += 1
                continue

            row_with_source = dict(row)
            row_with_source["list_disclosure_start"] = (
                list_disclosure_start.isoformat() if list_disclosure_start else None
            )
            candidate = _DownloadCandidate(
                project_code=project_code,
                project_name=project_name,
                page_url=page_url,
                html_path=html_path,
                row=row_with_source,
            )
            candidates.append(candidate)
            summary.candidate_entries.append(
                {
                    "project_code": candidate.project_code,
                    "project_name": candidate.project_name,
                    "page_url": candidate.page_url,
                    "row": row_with_source,
                    "list_disclosure_start": list_disclosure_start.isoformat()
                    if list_disclosure_start
                    else None,
                }
            )
            if list_disclosure_start:
                summary.candidate_dates.append(list_disclosure_start.isoformat())

    def _query_list_page(self, *, page_index: int, query: _ListQuerySpec) -> Dict[str, Any]:
        params = {
            "current": int(page_index),
            "size": self.page_size,
            "systemCode": query.system_code,
            "bizTypeCode": query.biz_type_code,
            **query.extra_params,
        }
        url = f"{LIST_API_URL}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url=url, headers=REQUEST_HEADERS, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GET {url} failed: {exc}") from exc
        return json.loads(payload)

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
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                ignore_https_errors=True,
                user_agent=REQUEST_HEADERS["User-Agent"],
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
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
        last_exc: Optional[Exception] = None
        for attempt in range(1, self._detail_retries + 2):
            page = await context.new_page()
            try:
                rendered_html = await self._fetch_rendered_html(
                    page=page,
                    page_url=candidate.page_url,
                    expected_project_code=candidate.project_code,
                    expected_project_name=candidate.project_name,
                )
                summary.detail_fetched += 1
                break
            except timeout_error_cls as exc:
                last_exc = exc
                if attempt <= self._detail_retries:
                    await asyncio.sleep(1.2 * attempt)
                else:
                    summary.typed_errors.append(
                        execute_failed_error(
                            source_id="tpre",
                            task_id=f"tpre:{project_type_key(self.output_type)}",
                            raw_reason=f"project_code={candidate.project_code} page-timeout: {exc}",
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt <= self._detail_retries:
                    await asyncio.sleep(1.2 * attempt)
                else:
                    summary.typed_errors.append(
                        execute_failed_error(
                            source_id="tpre",
                            task_id=f"tpre:{project_type_key(self.output_type)}",
                            raw_reason=f"project_code={candidate.project_code} page-fetch-failed: {exc}",
                        )
                    )
            finally:
                await page.close()

        if rendered_html is None:
            summary.detail_failed += 1
            if last_exc is not None:
                self.logger.error("Detail fetch failed: project_code=%s error=%s", candidate.project_code, last_exc)
            return

        disclosure_start = self._extract_disclosure_start_date(rendered_html)
        list_start = parse_loose_date(candidate.row.get("list_disclosure_start") or candidate.row.get("startTime"))
        final_date = disclosure_start if disclosure_start is not None else list_start
        if start or end:
            if final_date is None:
                summary.date_missing_skipped += 1
                summary.skipped_by_detail_date += 1
                return
            if not in_date_range(final_date, start, end):
                summary.skipped_by_detail_date += 1
                return

        try:
            self._save_complete_page(
                rendered_html=rendered_html,
                page_url=candidate.page_url,
                html_path=candidate.html_path,
            )
        except Exception as exc:  # noqa: BLE001
            summary.detail_failed += 1
            summary.typed_errors.append(
                save_failed_error(
                    source_id="tpre",
                    task_id=f"tpre:{project_type_key(self.output_type)}",
                    raw_reason=str(exc),
                )
            )
            return

        if self.save_json:
            self._write_json(
                json_path=os.path.splitext(candidate.html_path)[0] + ".json",
                payload={
                    "project_code": candidate.project_code,
                    "project_name": candidate.project_name,
                    "page_url": candidate.page_url,
                    "list_row": candidate.row,
                    "disclosure_start_date": disclosure_start.isoformat() if disclosure_start else None,
                },
            )
        self._notify_item_saved(candidate=candidate, disclosure_start=disclosure_start)
        summary.saved += 1
        summary.downloaded_this_run.add(os.path.relpath(candidate.html_path, self.html_root))

    async def _fetch_rendered_html(
        self,
        *,
        page,
        page_url: str,
        expected_project_code: str,
        expected_project_name: str,
    ) -> str:
        await page.goto(page_url, wait_until="domcontentloaded", timeout=self._render_timeout_ms)
        await page.wait_for_selector("body", timeout=self._render_timeout_ms)
        await page.wait_for_function(
            """
            ([expectedCode, expectedName]) => {
                const normalize = (value) => String(value || '').replace(/\\s+/g, '').toUpperCase();
                const bodyText = normalize(document.body ? document.body.innerText : '');
                const code = normalize(expectedCode);
                const name = normalize(expectedName);
                if (!code && !name) return bodyText.length > 0;
                return (code && bodyText.includes(code)) || (name && bodyText.includes(name));
            }
            """,
            arg=[expected_project_code, expected_project_name],
            timeout=self._render_timeout_ms,
        )
        await page.wait_for_timeout(1200)
        html = await page.content()
        normalized_text = re.sub(r"\s+", "", BeautifulSoup(html, "html.parser").get_text(" ", strip=True)).upper()
        expected_code = re.sub(r"\s+", "", expected_project_code).upper()
        expected_name = re.sub(r"\s+", "", expected_project_name).upper()
        if expected_code and expected_code not in normalized_text and expected_name and expected_name not in normalized_text:
            raise RuntimeError(f"detail-page-mismatch expected_project_code={expected_project_code}")
        return html

    def _save_complete_page(self, *, rendered_html: str, page_url: str, html_path: str) -> None:
        self._snapshot_saver.save_complete_page(
            rendered_html=rendered_html,
            page_url=page_url,
            html_path=html_path,
        )

    def _write_json(self, *, json_path: str, payload: Dict[str, Any]) -> None:
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _notify_item_saved(self, *, candidate: _DownloadCandidate, disclosure_start: Optional[dt.date]) -> None:
        callback = self.item_saved_callback
        if callback is None:
            return
        try:
            callback(
                {
                    "source_file": candidate.html_path,
                    "page_url": candidate.page_url,
                    "project_code": candidate.project_code,
                    "project_name": candidate.project_name,
                    "listing_date": disclosure_start.isoformat() if disclosure_start else "",
                    "source_id": "tpre",
                    "project_type": self.output_type,
                    "row": candidate.row,
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("item_saved_callback failed: %s", exc)

    def _extract_disclosure_start_date(self, html_text: str) -> Optional[dt.date]:
        text = BeautifulSoup(html_text, "html.parser").get_text(" ", strip=True)
        for pattern in DISCLOSURE_START_PATTERNS:
            match = re.search(pattern, text)
            if match:
                parsed = parse_loose_date(match.group(1))
                if parsed is not None:
                    return parsed
        return None


class TianjinEquityTransferDownloader(TpreProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_EQUITY_TRANSFER,
            list_queries=[_ListQuerySpec("equity-formal", "PROPERTY_RIGHT_TRANSFER", "FORMAL")],
            **kwargs,
        )


class TianjinCapitalIncreaseDownloader(TpreProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_CAPITAL_INCREASE,
            list_queries=[_ListQuerySpec("capital-formal", "ENTERPRISE_CAPITAL_INCREASE", "FORMAL")],
            **kwargs,
        )


class TianjinPhysicalAssetDownloader(TpreProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PHYSICAL_ASSET,
            list_queries=[
                _ListQuerySpec(
                    "physical-formal-5000plus",
                    "ENTERPRISE_ASSETS",
                    "FORMAL",
                    extra_params={"priceBegin": 5000},
                )
            ],
            **kwargs,
        )


class TianjinPreDisclosureDownloader(TpreProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PRE_DISCLOSURE,
            list_queries=[
                _ListQuerySpec("equity-prepare", "PROPERTY_RIGHT_TRANSFER", "PREPARE"),
                _ListQuerySpec("capital-prepare", "ENTERPRISE_CAPITAL_INCREASE", "PREPARE"),
            ],
            **kwargs,
        )
