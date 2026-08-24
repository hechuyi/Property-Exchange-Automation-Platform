"""CQUAE deal notice downloader."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

from bs4 import BeautifulSoup

from peap_core.business_catalog import get_business_descriptor

from ..download_errors import (
    execute_failed_error,
    invalid_candidate_error,
    list_failed_error,
    save_failed_error,
)
from ..submission_layout import _build_snapshot_target_path, resolve_submission_snapshot_target
from .common import (
    DownloadSummary,
    HttpFetchedText,
    attach_archive_integrity_to_sidecar,
    clear_artifact_evidence_sidecar,
    complete_resume_sidecar_exists,
    deal_date_outside_requested_range,
    detail_accounted_count,
    parse_bound,
    parse_loose_date,
    real_date_outside_requested_range,
    record_downloaded_target,
    reserve_download_target,
)
from .deal_contracts import apply_deal_manifest_fields
from .jsl_browser_fetcher import open_jsl_browser_fetcher

BASE_URL = "https://www.cquae.com"
EXCLUDED_TITLE_TOKENS = ("租赁", "协议成交", "协议转让")
DATE_RE = re.compile(r"(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)")
CODE_RE = re.compile(r"([A-Z0-9]{2,}\d{4,}(?:-[A-Z0-9]+)?)", flags=re.IGNORECASE)
IMPUTED_REMARK_SUFFIX = "成交日期缺失，按采集日填列"
_CQUAE_DEAL_NOTICE_SHELL_MARKER = "CQUAE Deal Notice"

REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
}


def _task_id(business_id: str) -> str:
    return f"cquae:deal:{business_id}"


def _decode_html(raw: bytes, charset_hint: str | None) -> str:
    tried: set[str] = set()
    for encoding in (charset_hint, "utf-8", "gb18030"):
        normalized = str(encoding or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in tried:
            continue
        tried.add(key)
        try:
            return raw.decode(normalized)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8")


def _has_complete_snapshot_sidecar(html_path: str, *, business_id: str) -> bool:
    return complete_resume_sidecar_exists(
        html_path,
        require_integrity=True,
        expected_fields={
            "source_id": "cquae",
            "record_family": "deal",
            "business_id": business_id,
            "task_id": _task_id(business_id),
        },
    )


@dataclass(frozen=True)
class _CquaeDealQuery:
    business_id: str
    list_path: str


@dataclass
class _DealCandidate:
    candidate_id: str
    project_code: str
    project_name: str
    source_url: str
    html_path: str
    row: Dict[str, Any]
    metadata: Dict[str, Any]


class CquaeDealDownloader:
    manifest_list_endpoint = ""
    manifest_detail_route = ""
    manifest_render_page_route = ""
    manifest_detail_api_endpoint = ""
    manifest_transferee_details_endpoint = ""
    manifest_date_field_candidates: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        html_root: str,
        query: _CquaeDealQuery,
        page_size: int = 10,
        max_pages: Optional[int] = None,
        concurrency: int = 1,
        resume: bool = False,
        timeout: int = 20,
        save_json: bool = False,
        logger: Optional[logging.Logger] = None,
        item_saved_callback=None,
    ):
        self.html_root = html_root
        self.query = query
        self.business_id = query.business_id
        self.page_size = max(1, int(page_size))
        self.max_pages = None if max_pages is None else max(1, int(max_pages))
        self.concurrency = max(1, int(concurrency))
        self.resume = bool(resume)
        self.timeout = max(5, int(timeout))
        self.save_json = bool(save_json)
        self.logger = logger or logging.getLogger("parser_v2")
        self.item_saved_callback = item_saved_callback
        self.business_label = get_business_descriptor(
            self.business_id,
            family_id="deal",
        ).project_type_label
        self._browser_fetch_html: Optional[Callable[[str], str]] = None

    def _collection_date(self) -> dt.date:
        return dt.date.today()

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
        candidates: List[_DealCandidate] = []
        fetcher_context = (
            self._open_browser_fetcher()
            if self._should_use_browser_fetcher(
                list_only=list_only,
                prefetched_candidates=prefetched_candidates,
            )
            else contextlib.nullcontext(None)
        )
        previous_browser_fetch = self._browser_fetch_html
        try:
            with fetcher_context as browser_fetch:
                if browser_fetch is not None:
                    self._browser_fetch_html = browser_fetch
                if prefetched_candidates is None:
                    self._collect_list_candidates(
                        output_dir=output_dir,
                        start=start,
                        end=end,
                        summary=summary,
                        candidates=candidates,
                    )
                else:
                    self._build_prefetched_candidates(
                        prefetched_candidates=prefetched_candidates,
                        output_dir=output_dir,
                        start=start,
                        end=end,
                        summary=summary,
                        candidates=candidates,
                    )
                summary.detail_candidates = len(candidates)
                list_resume_skipped = summary.skipped_by_resume
                if not list_only:
                    for candidate_index, candidate in enumerate(candidates):
                        self._download_candidate(
                            candidate=candidate,
                            summary=summary,
                            start=start,
                            end=end,
                            candidate_index=candidate_index,
                        )
        finally:
            self._browser_fetch_html = previous_browser_fetch

        detail_resume_skipped = summary.skipped_by_resume - list_resume_skipped
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
        return summary

    def _method_is_default(self, name: str) -> bool:
        value = getattr(self, name)
        value_func = getattr(value, "__func__", value)
        default_func = getattr(type(self), name)
        return value_func is default_func

    def _should_use_browser_fetcher(
        self,
        *,
        list_only: bool,
        prefetched_candidates: Optional[List[Dict[str, Any]]],
    ) -> bool:
        needs_list_fetch = (
            prefetched_candidates is None
            and self._method_is_default("_collect_list_candidates")
            and self._method_is_default("_fetch_list_html")
        )
        needs_detail_fetch = (
            not list_only
            and self._method_is_default("_download_candidate")
            and self._method_is_default("_fetch_detail_html")
        )
        return needs_list_fetch or needs_detail_fetch

    def _open_browser_fetcher(self):
        return open_jsl_browser_fetcher(
            warmup_url=BASE_URL + "/",
            request_headers=REQUEST_HEADERS,
            timeout=self.timeout,
            logger=self.logger,
        )

    def _collect_list_candidates(
        self,
        *,
        output_dir: str,
        start: Optional[dt.date],
        end: Optional[dt.date],
        summary: DownloadSummary,
        candidates: List[_DealCandidate],
    ) -> None:
        current_url = urllib.parse.urljoin(BASE_URL, self.query.list_path)
        seen_page_urls: Set[str] = set()
        seen_ids: Set[str] = set()
        page_index = 0
        while current_url and current_url not in seen_page_urls:
            if self.max_pages is not None and page_index >= self.max_pages:
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="cquae",
                        task_id=_task_id(self.business_id),
                        raw_reason=(
                            "explicit-max-pages-truncates-discovery: "
                            f"next_page={current_url} max_pages={self.max_pages}"
                        ),
                    )
                )
                break
            seen_page_urls.add(current_url)
            page_index += 1
            try:
                html = self._fetch_list_html(current_url)
                summary.pages_requested += 1
            except Exception as exc:  # noqa: BLE001
                summary.pages_requested += 1
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="cquae",
                        task_id=_task_id(self.business_id),
                        raw_reason=f"list-page-{page_index}-request-failed: {exc}",
                    )
                )
                break

            rows = self._extract_list_rows(html=html, current_url=current_url)
            structure_failure = self._list_page_failure_reason(html=html, rows=rows)
            if structure_failure:
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="cquae",
                        task_id=_task_id(self.business_id),
                        raw_reason=f"list-page-{page_index}-invalid-structure: {structure_failure}",
                    )
                )
                break
            self._rows_to_candidates(
                rows=rows,
                output_dir=output_dir,
                start=start,
                end=end,
                summary=summary,
                seen_ids=seen_ids,
                candidates=candidates,
            )
            current_url = self._extract_next_page_url(html=html, current_url=current_url)

    def _build_prefetched_candidates(
        self,
        *,
        prefetched_candidates: List[Dict[str, Any]],
        output_dir: str,
        start: Optional[dt.date],
        end: Optional[dt.date],
        summary: DownloadSummary,
        candidates: List[_DealCandidate],
    ) -> None:
        seen_ids: Set[str] = set()
        for index, raw in enumerate(prefetched_candidates, start=1):
            if not isinstance(raw, dict):
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="cquae",
                        task_id=_task_id(self.business_id),
                        raw_reason=f"prefetched-entry-{index}-invalid-format",
                    )
                )
                continue
            summary.listed_items += 1
            entry = dict(raw)
            row = entry.get("row") if isinstance(entry.get("row"), dict) else {}
            candidate_id = str(entry.get("candidate_id") or entry.get("project_code") or "").strip()
            if not candidate_id:
                candidate_id = self._derive_candidate_id(source_url=str(entry.get("source_url") or ""), row=row)
            if not candidate_id:
                summary.skipped_by_missing_xmid += 1
                continue
            if candidate_id in seen_ids:
                summary.skipped_by_duplicate += 1
                continue
            seen_ids.add(candidate_id)
            project_code = str(entry.get("project_code") or "").strip().upper() or candidate_id
            project_name = str(entry.get("project_name") or "").strip() or str(row.get("project_name") or "")
            source_url = str(entry.get("source_url") or "").strip()
            if not source_url:
                summary.skipped_by_missing_xmid += 1
                continue
            if not self._is_whitelisted_detail_url(source_url):
                summary.skipped_by_missing_xmid += 1
                continue
            deal_date = parse_loose_date(entry.get("deal_date"))
            deal_date_basis = str(entry.get("deal_date_basis") or "").strip()
            deal_date_is_imputed = bool(entry.get("deal_date_is_imputed"))
            collection_date = parse_loose_date(entry.get("collection_date"))
            if deal_date_is_imputed and deal_date_basis == "collection_date":
                collection_date = collection_date or deal_date or self._collection_date()
                deal_date = None
            if deal_date is None:
                if collection_date is None:
                    resolved = self._resolve_deal_date(row=row, row_text=str(entry.get("row_text") or ""))
                    deal_date = resolved["deal_date"]
                    collection_date = resolved["collection_date"]
                    effective_date = resolved["effective_date"]
                    deal_date_basis = resolved["deal_date_basis"]
                    deal_date_is_imputed = resolved["deal_date_is_imputed"]
                else:
                    effective_date = collection_date
                    deal_date_basis = deal_date_basis or "collection_date"
                    deal_date_is_imputed = True
            else:
                collection_date = collection_date or self._collection_date()
                effective_date = deal_date
            if real_date_outside_requested_range(deal_date, start, end):
                summary.skipped_by_list_date += 1
                continue
            html_path = _build_snapshot_target_path(
                archive_root=output_dir,
                project_code=project_code,
                project_name=project_name,
                listing_date=effective_date.isoformat(),
            )
            if self.resume and _has_complete_snapshot_sidecar(html_path, business_id=self.business_id):
                summary.skipped_by_resume += 1
                continue
            metadata = self._build_metadata(
                row=row,
                source_url=source_url,
                candidate_id=candidate_id,
                project_code=project_code,
                project_name=project_name,
                deal_date=deal_date,
                collection_date=collection_date,
                deal_date_basis=deal_date_basis or "deal_date",
                deal_date_is_imputed=deal_date_is_imputed,
            )
            candidate = _DealCandidate(
                candidate_id=candidate_id,
                project_code=project_code,
                project_name=project_name,
                source_url=source_url,
                html_path=html_path,
                row=row,
                metadata=metadata,
            )
            candidates.append(candidate)
            summary.candidate_entries.append(dict(metadata))
            summary.candidate_dates.append(effective_date.isoformat())

    def _rows_to_candidates(
        self,
        *,
        rows: List[Dict[str, Any]],
        output_dir: str,
        start: Optional[dt.date],
        end: Optional[dt.date],
        summary: DownloadSummary,
        seen_ids: Set[str],
        candidates: List[_DealCandidate],
    ) -> None:
        for row in rows:
            summary.listed_items += 1
            candidate_id = self._derive_candidate_id(source_url=str(row.get("source_url") or ""), row=row)
            if not candidate_id:
                summary.skipped_by_missing_xmid += 1
                continue
            if candidate_id in seen_ids:
                summary.skipped_by_duplicate += 1
                continue
            seen_ids.add(candidate_id)
            source_url = str(row.get("source_url") or "").strip()
            if not self._is_whitelisted_detail_url(source_url):
                summary.skipped_by_missing_xmid += 1
                continue
            project_code = str(row.get("project_code") or "").strip().upper() or candidate_id
            project_name = str(row.get("project_name") or "").strip()
            resolved = self._resolve_deal_date(
                row=row,
                row_text=str(row.get("row_text") or ""),
            )
            effective_date = resolved["effective_date"]
            if real_date_outside_requested_range(resolved["deal_date"], start, end):
                summary.skipped_by_list_date += 1
                continue
            html_path = _build_snapshot_target_path(
                archive_root=output_dir,
                project_code=project_code,
                project_name=project_name,
                listing_date=effective_date.isoformat(),
            )
            if self.resume and _has_complete_snapshot_sidecar(html_path, business_id=self.business_id):
                summary.skipped_by_resume += 1
                continue
            metadata = self._build_metadata(
                row=row,
                source_url=source_url,
                candidate_id=candidate_id,
                project_code=project_code,
                project_name=project_name,
                deal_date=resolved["deal_date"],
                collection_date=resolved["collection_date"],
                deal_date_basis=resolved["deal_date_basis"],
                deal_date_is_imputed=resolved["deal_date_is_imputed"],
            )
            candidate = _DealCandidate(
                candidate_id=candidate_id,
                project_code=project_code,
                project_name=project_name,
                source_url=source_url,
                html_path=html_path,
                row=row,
                metadata=metadata,
            )
            candidates.append(candidate)
            summary.candidate_entries.append(dict(metadata))
            summary.candidate_dates.append(effective_date.isoformat())

    def _build_metadata(
        self,
        *,
        row: Dict[str, Any],
        source_url: str,
        candidate_id: str,
        project_code: str,
        project_name: str,
        deal_date: Optional[dt.date],
        collection_date: dt.date,
        deal_date_basis: str,
        deal_date_is_imputed: bool,
    ) -> Dict[str, Any]:
        remark_suffix = IMPUTED_REMARK_SUFFIX if deal_date_is_imputed else ""
        return {
            "task_id": _task_id(self.business_id),
            "record_family": "deal",
            "business_id": self.business_id,
            "business_label": self.business_label,
            "source_id": "cquae",
            "source_url": source_url,
            "collection_date": collection_date.isoformat(),
            "deal_date": deal_date.isoformat() if deal_date is not None else "",
            "deal_date_basis": deal_date_basis,
            "deal_date_is_imputed": bool(deal_date_is_imputed),
            "remark_suffix": remark_suffix,
            "deal_date_remark_suffix": remark_suffix,
            "candidate_id": candidate_id,
            "project_code": project_code,
            "project_name": project_name,
            "row": row,
        }

    def _resolve_deal_date(self, *, row: Dict[str, Any], row_text: str) -> Dict[str, Any]:
        collection_date = self._collection_date()
        parsed = parse_loose_date(row.get("deal_date"))
        if parsed is None:
            parsed = parse_loose_date(row.get("dealDate"))
        if parsed is None:
            match = DATE_RE.search(row_text)
            parsed = parse_loose_date(match.group(1)) if match else None
        if parsed is not None:
            return {
                "deal_date": parsed,
                "collection_date": collection_date,
                "effective_date": parsed,
                "deal_date_basis": "deal_date",
                "deal_date_is_imputed": False,
            }
        return {
            "deal_date": None,
            "collection_date": collection_date,
            "effective_date": collection_date,
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
        }

    @staticmethod
    def _derive_candidate_id(*, source_url: str, row: Dict[str, Any]) -> str:
        project_code = str(row.get("project_code") or "").strip().upper()
        if project_code:
            return project_code
        if source_url:
            parsed = urllib.parse.urlsplit(source_url)
            tail = parsed.path.rstrip("/").split("/")[-1]
            if tail:
                return tail
        row_text = str(row.get("row_text") or "")
        match = CODE_RE.search(row_text)
        if match:
            return match.group(1).upper()
        return ""

    def _is_whitelisted_detail_url(self, source_url: str) -> bool:
        route_prefix = str(self.manifest_detail_route or "").strip()
        if not route_prefix:
            return False
        parsed = urllib.parse.urlsplit(source_url)
        path = parsed.path.lower()
        if path == (route_prefix + "Show.cshtml").lower():
            values = urllib.parse.parse_qs(parsed.query).get("id", [])
            return bool(values and str(values[0]).strip())
        detail_prefix = (route_prefix + "Detail/").lower()
        return path.startswith(detail_prefix) and path.endswith((".html", ".shtml", ".cshtml"))

    def _extract_list_rows(self, *, html: str, current_url: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        rows: List[Dict[str, Any]] = []
        for link in soup.select("table a[href], li a[href], div a[href]"):
            table_container = link.find_parent("table")
            if table_container is not None and self._is_excluded_table(table_container):
                continue
            title = str(link.get_text(" ", strip=True) or "").strip()
            if not title:
                continue
            if self._has_excluded_text(title):
                continue
            href = str(link.get("href") or "").strip()
            if not href:
                continue
            source_url = urllib.parse.urljoin(current_url, href)
            if not self._is_whitelisted_detail_url(source_url):
                continue
            row_container = link.find_parent("tr") or link.parent
            row_text = str(row_container.get_text(" ", strip=True) or "")
            if self._has_excluded_text(row_text):
                continue
            code_match = CODE_RE.search(row_text) or CODE_RE.search(title)
            project_code = code_match.group(1).upper() if code_match else ""
            rows.append(
                {
                    "project_name": title,
                    "project_code": project_code,
                    "source_url": source_url,
                    "row_text": row_text,
                }
            )
        return rows

    @staticmethod
    def _has_excluded_text(text: str) -> bool:
        return any(token in str(text or "") for token in EXCLUDED_TITLE_TOKENS)

    @classmethod
    def _is_excluded_table(cls, table) -> bool:
        table_level_parts: List[str] = []
        for node in table.select("caption, thead, th"):
            table_level_parts.append(node.get_text(" ", strip=True))
        first_row = table.find("tr")
        if first_row is not None:
            for cell in first_row.find_all(["td", "th"], recursive=False):
                table_level_parts.append(cell.get_text(" ", strip=True))
        table_level_parts.extend(cls._nearby_table_label_parts(table))
        if not table_level_parts:
            return False
        return cls._has_excluded_text(" ".join(table_level_parts))

    @classmethod
    def _nearby_table_label_parts(cls, table) -> List[str]:
        parts: List[str] = []
        previous = table.find_previous_sibling()
        scanned = 0
        while previous is not None and scanned < 5:
            name = str(getattr(previous, "name", "") or "").lower()
            text = previous.get_text(" ", strip=True) if hasattr(previous, "get_text") else ""
            if cls._looks_like_title_node(previous):
                parts.append(text)
                break
            if name and text:
                break
            previous = previous.find_previous_sibling()
            scanned += 1

        for parent in table.parents:
            parent_name = str(getattr(parent, "name", "") or "").lower()
            if parent_name in {"", "[document]", "html", "body"}:
                break
            for attr in ("title", "aria-label"):
                value = str(parent.get(attr) or "").strip() if hasattr(parent, "get") else ""
                if value:
                    parts.append(value)
            for child in parent.find_all(recursive=False):
                if child is table:
                    break
                if cls._looks_like_title_node(child):
                    parts.append(child.get_text(" ", strip=True))
            break
        return parts

    @staticmethod
    def _looks_like_title_node(node) -> bool:
        name = str(getattr(node, "name", "") or "").lower()
        if name in {"h1", "h2", "h3", "h4", "h5", "h6", "caption"}:
            return True
        if not hasattr(node, "get"):
            return False
        marker = " ".join(
            [
                " ".join(str(value) for value in (node.get("class") or [])),
                str(node.get("id") or ""),
                str(node.get("role") or ""),
            ]
        ).lower()
        return any(token in marker for token in ("title", "tit", "caption", "head"))

    def _extract_next_page_url(self, *, html: str, current_url: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for link in soup.select("a[href]"):
            text = str(link.get_text(" ", strip=True) or "")
            if "下一页" not in text:
                continue
            href = str(link.get("href") or "").strip()
            if not href:
                continue
            return urllib.parse.urljoin(current_url, href)
        return ""

    @classmethod
    def _list_page_failure_reason(cls, *, html: str, rows: List[Dict[str, Any]]) -> str:
        soup = BeautifulSoup(html or "", "html.parser")
        title = re.sub(r"\s+", "", soup.title.get_text(" ", strip=True) if soup.title else "").upper()
        text = re.sub(r"\s+", "", soup.get_text(" ", strip=True)).upper()
        if any(
            marker in title or marker in text
            for marker in (
                "页面不存在",
                "404页面",
                "系统维护",
                "维护中",
                "访问验证",
                "安全验证",
                "人机验证",
                "验证码",
                "访问过于频繁",
                "ACCESSDENIED",
                "SERVICEUNAVAILABLE",
            )
        ):
            return "unavailable-or-interstitial-page"
        if rows:
            return ""
        if any(marker in text for marker in ("暂无数据", "暂无信息", "没有相关", "无数据")):
            if soup.select_one("table, ul, ol, [class*='list'], [id*='list']"):
                return ""
        return "missing-recognizable-list-structure"

    def _fetch_list_html(self, url: str) -> str:
        if self._browser_fetch_html is not None:
            return self._browser_fetch_html(url)
        request = urllib.request.Request(url=url, headers=REQUEST_HEADERS, method="GET")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
        return _decode_html(raw, charset)

    def _download_candidate(
        self,
        *,
        candidate: _DealCandidate,
        summary: DownloadSummary,
        start: Optional[dt.date] = None,
        end: Optional[dt.date] = None,
        candidate_index: Optional[int] = None,
    ) -> None:
        try:
            detail_html = self._fetch_detail_html(candidate.source_url)
            if not isinstance(detail_html, HttpFetchedText):
                raise ValueError("detail-fetch-missing-http-provenance")
            if detail_html.source_url != candidate.source_url:
                raise ValueError(
                    "detail-fetch-source-url-mismatch "
                    f"expected={candidate.source_url} actual={detail_html.source_url}"
                )
            summary.detail_fetched += 1
        except Exception as exc:  # noqa: BLE001
            summary.detail_failed += 1
            summary.typed_errors.append(
                execute_failed_error(
                    source_id="cquae",
                    task_id=_task_id(self.business_id),
                    raw_reason=f"detail-fetch-failed candidate_id={candidate.candidate_id}: {exc}",
                )
            )
            return
        if not self._is_real_detail_page(detail_html=detail_html, candidate=candidate):
            summary.detail_failed += 1
            summary.typed_errors.append(
                execute_failed_error(
                    source_id="cquae",
                    task_id=_task_id(self.business_id),
                    raw_reason=(
                        "invalid-detail-page "
                        f"candidate_id={candidate.candidate_id}: {candidate.source_url}"
                    ),
                )
            )
            return
        detail_date = self._extract_detail_date(detail_html)
        if detail_date is not None:
            candidate.metadata["deal_date"] = detail_date.isoformat()
            candidate.metadata["deal_date_basis"] = "deal_date"
            candidate.metadata["deal_date_is_imputed"] = False
            candidate.metadata["remark_suffix"] = ""
            candidate.metadata["deal_date_remark_suffix"] = ""
        effective_date = parse_loose_date(candidate.metadata.get("deal_date")) or parse_loose_date(
            candidate.metadata.get("collection_date")
        )
        if effective_date is not None:
            if candidate_index is not None:
                if 0 <= candidate_index < len(summary.candidate_entries):
                    summary.candidate_entries[candidate_index] = dict(candidate.metadata)
                if 0 <= candidate_index < len(summary.candidate_dates):
                    summary.candidate_dates[candidate_index] = effective_date.isoformat()
            if deal_date_outside_requested_range(
                parse_loose_date(candidate.metadata.get("deal_date")),
                parse_loose_date(candidate.metadata.get("collection_date")),
                start,
                end,
            ):
                summary.skipped_by_detail_date += 1
                return
            candidate.html_path, _ = resolve_submission_snapshot_target(
                archive_root=os.path.abspath(self.html_root),
                project_code=candidate.project_code,
                project_name=candidate.project_name,
                listing_date=effective_date.isoformat(),
            )
        if self.resume and _has_complete_snapshot_sidecar(
            candidate.html_path,
            business_id=self.business_id,
        ):
            summary.skipped_by_resume += 1
            return
        if _CQUAE_DEAL_NOTICE_SHELL_MARKER in str(detail_html or ""):
            try:
                self._write_invalid_shell_evidence_if_needed(
                    detail_html=detail_html,
                    source_url=detail_html.source_url,
                    final_url=detail_html.final_url,
                    html_path=candidate.html_path,
                    metadata=candidate.metadata,
                )
            except Exception as exc:  # noqa: BLE001
                summary.typed_errors.append(
                    save_failed_error(
                        source_id="cquae",
                        task_id=_task_id(self.business_id),
                        raw_reason=(
                            "invalid-shell-evidence-save-failed "
                            f"candidate_id={candidate.candidate_id}: {exc}"
                        ),
                    )
                )
            else:
                summary.typed_errors.append(
                    execute_failed_error(
                        source_id="cquae",
                        task_id=_task_id(self.business_id),
                        raw_reason=f"invalid-detail-shell candidate_id={candidate.candidate_id}",
                    )
                )
            summary.detail_failed += 1
            return
        sidecar_written = False
        if not reserve_download_target(
            summary,
            html_root=self.html_root,
            html_path=candidate.html_path,
            source_id="cquae",
            task_id=_task_id(self.business_id),
        ):
            summary.detail_failed += 1
            return
        try:
            self._save_snapshot_html(
                html_path=candidate.html_path,
                detail_html=detail_html,
            )
            json_path = os.path.splitext(candidate.html_path)[0] + ".json"
            self._write_sidecar_json(
                json_path=json_path,
                metadata=candidate.metadata,
                detail_url=candidate.source_url,
                save_status="pending",
                source_url=detail_html.source_url,
                final_url=detail_html.final_url,
                http_status=detail_html.http_status,
            )
            sidecar_written = True
            self._write_invalid_shell_evidence_if_needed(
                detail_html=detail_html,
                source_url=detail_html.source_url,
                final_url=detail_html.final_url,
                html_path=candidate.html_path,
                metadata=candidate.metadata,
            )
            self._write_sidecar_json(
                json_path=json_path,
                metadata=candidate.metadata,
                detail_url=candidate.source_url,
                save_status="complete",
                source_url=detail_html.source_url,
                final_url=detail_html.final_url,
                http_status=detail_html.http_status,
            )
            if os.path.isfile(json_path):
                attach_archive_integrity_to_sidecar(
                    json_path=json_path,
                    html_path=candidate.html_path,
                )
            self._notify_item_saved(candidate=candidate)
            clear_artifact_evidence_sidecar(candidate.html_path)
        except Exception as exc:  # noqa: BLE001
            json_path = os.path.splitext(candidate.html_path)[0] + ".json"
            cleanup_reason = ""
            if sidecar_written:
                try:
                    os.remove(json_path)
                except FileNotFoundError:
                    pass
                except OSError as cleanup_exc:
                    cleanup_reason = f"; resume-sidecar-cleanup-failed path={json_path}: {cleanup_exc}"
                    try:
                        self._write_sidecar_json(
                            json_path=json_path,
                            metadata={
                                "task_id": _task_id(self.business_id),
                                "source_id": "cquae",
                                "record_family": "deal",
                                "business_id": self.business_id,
                            },
                            detail_url=candidate.source_url,
                            save_status="failed",
                            source_url=detail_html.source_url,
                            final_url=detail_html.final_url,
                            http_status=detail_html.http_status,
                        )
                    except Exception as marker_exc:  # noqa: BLE001
                        cleanup_reason += f"; resume-sidecar-failed-marker-failed: {marker_exc}"
            summary.detail_failed += 1
            summary.typed_errors.append(
                save_failed_error(
                    source_id="cquae",
                    task_id=_task_id(self.business_id),
                    raw_reason=f"save-failed candidate_id={candidate.candidate_id}: {exc}{cleanup_reason}",
                )
            )
            return
        summary.saved += 1
        record_downloaded_target(summary, html_root=self.html_root, html_path=candidate.html_path)

    def _fetch_detail_html(self, source_url: str) -> HttpFetchedText:
        if self._browser_fetch_html is not None:
            return self._browser_fetch_html(source_url)
        request = urllib.request.Request(url=source_url, headers=REQUEST_HEADERS, method="GET")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            http_status = getattr(response, "status", None)
            final_url = response.geturl()
        return HttpFetchedText(
            _decode_html(raw, charset),
            source_url=source_url,
            final_url=final_url,
            http_status=http_status,
        )

    @staticmethod
    def _normalize_detail_text(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "")).upper()

    @classmethod
    def _is_real_detail_page(cls, *, detail_html: str, candidate: _DealCandidate) -> bool:
        soup = BeautifulSoup(detail_html or "", "html.parser")
        title = cls._normalize_detail_text(soup.title.get_text(" ", strip=True) if soup.title else "")
        text = cls._normalize_detail_text(soup.get_text(" ", strip=True))
        if not text:
            return False
        if any(marker in title or marker in text for marker in ("404页面", "页面不存在", "NOTFOUND")):
            return False
        if _CQUAE_DEAL_NOTICE_SHELL_MARKER in str(detail_html or ""):
            return True
        if "成交" not in text:
            return False
        expected_code = cls._normalize_detail_text(candidate.project_code)
        if expected_code and expected_code in text:
            return True
        expected_name = cls._normalize_detail_text(candidate.project_name)
        if expected_name and expected_name in text:
            return True
        return cls._extract_detail_date(detail_html) is not None

    @staticmethod
    def _extract_detail_date(html: str) -> Optional[dt.date]:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        match = DATE_RE.search(text)
        if not match:
            return None
        return parse_loose_date(match.group(1))

    @staticmethod
    def _save_snapshot_html(*, html_path: str, detail_html: str) -> None:
        os.makedirs(os.path.dirname(html_path), exist_ok=True)
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(detail_html)

    @staticmethod
    def _sha256_text(value: str) -> str:
        return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

    @classmethod
    def _write_invalid_shell_evidence_if_needed(
        cls,
        *,
        detail_html: str,
        source_url: str,
        final_url: str,
        html_path: str,
        metadata: Dict[str, Any],
    ) -> None:
        if _CQUAE_DEAL_NOTICE_SHELL_MARKER not in str(detail_html or ""):
            return
        project_code = str(metadata.get("project_code") or "").strip().upper()
        project_name = str(metadata.get("project_name") or "").strip()
        identity_hints: Dict[str, str] = {}
        if project_code:
            identity_hints["project_code_hash"] = cls._sha256_text(project_code)
        if project_name:
            identity_hints["project_name_hash"] = cls._sha256_text(project_name)
        evidence = {
            "schema_version": 1,
            "page_kind": "invalid_shell",
            "source_url_hash": cls._sha256_text(source_url),
            "final_url_hash": cls._sha256_text(final_url),
            "content_sha256": cls._sha256_text(detail_html),
            "identity_hints": identity_hints,
        }
        os.makedirs(os.path.dirname(html_path), exist_ok=True)
        with open(f"{html_path}.peap-evidence.json", "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, ensure_ascii=False, indent=2, sort_keys=True)

    @staticmethod
    def _write_sidecar_json(
        *,
        json_path: str,
        metadata: Dict[str, Any],
        detail_url: str,
        detail_payload: Optional[Dict[str, Any]] = None,
        save_status: str = "complete",
        source_url: str,
        final_url: str,
        http_status: int,
    ) -> None:
        fetched = HttpFetchedText(
            "",
            source_url=source_url,
            final_url=final_url,
            http_status=http_status,
        )
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        temp_json_path = f"{json_path}.tmp"
        with open(temp_json_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "save_status": save_status,
                    "task_id": str(metadata.get("task_id") or ""),
                    "source_id": str(metadata.get("source_id") or ""),
                    "record_family": str(metadata.get("record_family") or ""),
                    "business_id": str(metadata.get("business_id") or ""),
                    "metadata": metadata,
                    "detail_url": detail_url,
                    "source_url": fetched.source_url,
                    "final_url": fetched.final_url,
                    "http_status": fetched.http_status,
                    **({"detail_payload": detail_payload} if detail_payload is not None else {}),
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
        os.replace(temp_json_path, json_path)

    def _notify_item_saved(self, *, candidate: _DealCandidate) -> None:
        callback = self.item_saved_callback
        if callback is None:
            return
        callback(
            {
                "source_file": candidate.html_path,
                "page_url": candidate.source_url,
                "project_code": candidate.project_code,
                "project_name": candidate.project_name,
                "listing_date": str(
                    candidate.metadata.get("deal_date") or candidate.metadata.get("collection_date") or ""
                ),
                "source_id": "cquae",
                "business_id": self.business_id,
                "row": candidate.row,
            }
        )


class ChongqingDealEquityTransferDownloader(CquaeDealDownloader):
    def __init__(self, **kwargs):
        requirement = apply_deal_manifest_fields(
            self,
            source_id="cquae",
            business_id="deal_equity_transfer",
        )
        super().__init__(
            query=_CquaeDealQuery(
                business_id="deal_equity_transfer",
                list_path=requirement.list_endpoint,
            ),
            **kwargs,
        )


class ChongqingDealPhysicalAssetDownloader(CquaeDealDownloader):
    manifest_list_endpoint = "/CquaeNews/cjgs/List.cshtml?type=3"
    manifest_detail_route = "/CquaeNews/cjgs/"
    manifest_render_page_route = "/CquaeNews/cjgs/"
    manifest_date_field_candidates = ("deal_date",)

    def __init__(self, **kwargs):
        super().__init__(
            query=_CquaeDealQuery(
                business_id="deal_physical_asset",
                list_path=self.manifest_list_endpoint,
            ),
            **kwargs,
        )


class ChongqingDealCapitalIncreaseDownloader(CquaeDealDownloader):
    def __init__(self, **kwargs):
        requirement = apply_deal_manifest_fields(
            self,
            source_id="cquae",
            business_id="deal_capital_increase",
        )
        super().__init__(
            query=_CquaeDealQuery(
                business_id="deal_capital_increase",
                list_path=requirement.list_endpoint,
            ),
            **kwargs,
        )
