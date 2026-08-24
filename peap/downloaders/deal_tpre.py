"""TPRE deal notice downloader."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from bs4 import BeautifulSoup

from peap_core.business_catalog import get_business_descriptor

from ..browser_runtime import launch_chromium_browser
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
    read_json_object,
    real_date_outside_requested_range,
    record_downloaded_target,
    reserve_download_target,
)
from .deal_contracts import apply_deal_manifest_fields, preferred_deal_date_field

BASE_URL = "https://trade.tpre.cn"
REQUEST_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
}

IMPUTED_REMARK_SUFFIX = "成交日期缺失，按采集日填列"
_TPRE_DEAL_NOTICE_SHELL_MARKER = bytes(
    (84, 80, 82, 69, 32, 68, 101, 97, 108, 32, 78, 111, 116, 105, 99, 101)
).decode("ascii")
_TPRE_PROJECT_CODE_RE = re.compile(r"^(?:[A-Z]\d{5}|[A-Z]{2}\d{4})TJ\d+(?:-\d+)?$", re.IGNORECASE)


def _task_id(business_id: str) -> str:
    return f"tpre:deal:{business_id}"


def _looks_like_tpre_project_code(value: str) -> bool:
    return bool(_TPRE_PROJECT_CODE_RE.fullmatch(str(value or "").strip()))


@dataclass(frozen=True)
class _TpreDealQuery:
    business_id: str
    list_endpoint: str
    render_page_route: str
    detail_api_endpoint: str
    transferee_details_endpoint: str
    preferred_date_field: str
    result_tab_label: str = ""


@dataclass
class _DealCandidate:
    notice_id: str
    project_code: str
    project_name: str
    source_url: str
    html_path: str
    row: Dict[str, Any]
    metadata: Dict[str, Any]


class _TpreDealListPayloadError(ValueError):
    pass


def _extract_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("records", "rows", "list", "items"):
            raw = data.get(key)
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _extract_total_pages(payload: Dict[str, Any], *, page_size: int, default_page: int) -> int:
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("pages", "totalPages", "pageCount"):
            raw = data.get(key)
            if isinstance(raw, (int, float, str)) and str(raw).strip():
                try:
                    return max(1, int(raw))
                except ValueError:
                    pass
        total = data.get("total")
        if isinstance(total, (int, float, str)) and str(total).strip():
            try:
                return max(1, math.ceil(float(total) / max(1, page_size)))
            except ValueError:
                pass
    return max(1, default_page)


def _nonnegative_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise _TpreDealListPayloadError(f"{field_name}-must-be-nonnegative-integer")
    text = str(value if value is not None else "").strip()
    if not text or not re.fullmatch(r"\d+", text):
        raise _TpreDealListPayloadError(f"{field_name}-must-be-nonnegative-integer")
    return int(text)


def _validate_list_page_payload(
    payload: object,
    *,
    page_size: int,
    page_index: int,
) -> tuple[List[Dict[str, Any]], int]:
    if not isinstance(payload, Mapping):
        raise _TpreDealListPayloadError("payload-must-be-object")
    if "code" not in payload:
        raise _TpreDealListPayloadError("code-field-missing")
    try:
        code = int(payload.get("code"))
    except (TypeError, ValueError):
        raise _TpreDealListPayloadError("code-field-invalid") from None
    if code not in {0, 200}:
        message = str(payload.get("message") or payload.get("msg") or "").strip()
        raise _TpreDealListPayloadError(f"api-code-{code}: {message}".rstrip())

    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise _TpreDealListPayloadError("data-field-must-be-object")
    records_key = next(
        (key for key in ("records", "rows", "list", "items") if key in data),
        "",
    )
    if not records_key:
        raise _TpreDealListPayloadError("records-field-missing")
    raw_records = data.get(records_key)
    if not isinstance(raw_records, list):
        raise _TpreDealListPayloadError(f"{records_key}-field-must-be-array")
    if any(not isinstance(item, Mapping) for item in raw_records):
        raise _TpreDealListPayloadError(f"{records_key}-items-must-be-objects")
    if "total" not in data:
        raise _TpreDealListPayloadError("total-field-missing")
    total = _nonnegative_int(data.get("total"), field_name="total")
    rows = [dict(item) for item in raw_records]
    if total == 0:
        if rows:
            raise _TpreDealListPayloadError("total-zero-with-nonempty-records")
        return rows, 1
    if not rows:
        raise _TpreDealListPayloadError("positive-total-with-empty-records")
    if len(rows) > total:
        raise _TpreDealListPayloadError("records-count-exceeds-total")

    computed_pages = max(1, math.ceil(total / max(1, page_size)))
    declared_pages: int | None = None
    for key in ("pages", "totalPages", "pageCount"):
        if key in data:
            declared_pages = _nonnegative_int(data.get(key), field_name=key)
            break
    total_pages = max(1, declared_pages if declared_pages is not None else computed_pages)
    if total_pages < page_index:
        raise _TpreDealListPayloadError(
            f"declared-pages-before-current-page: pages={total_pages} current={page_index}"
        )
    if declared_pages is not None and declared_pages != computed_pages:
        raise _TpreDealListPayloadError(
            f"declared-pages-total-mismatch: pages={declared_pages} computed={computed_pages}"
        )
    return rows, total_pages


def _extract_notice_id(row: Dict[str, Any]) -> str:
    for key in ("id", "ID", "noticeId", "notice_id", "projectId", "project_id", "projectCode", "project_code"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_project_code(row: Dict[str, Any], fallback: str) -> str:
    for key in ("projectCode", "code", "xmbh"):
        value = str(row.get(key) or "").strip().upper()
        if value:
            return value
    return fallback


def _extract_project_name(row: Dict[str, Any]) -> str:
    for key in ("projectName", "title", "name", "xmmc"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _normalize_official_tpre_url(raw: str) -> str:
    normalized = str(raw or "").strip()
    if not normalized:
        return ""
    return urllib.parse.urljoin(BASE_URL, normalized)


def _is_resume_complete(html_path: str, *, business_id: str) -> bool:
    if not complete_resume_sidecar_exists(
        html_path,
        require_integrity=True,
        expected_fields={
            "source_id": "tpre",
            "record_family": "deal",
            "business_id": business_id,
            "task_id": _task_id(business_id),
        },
    ):
        return False
    payload = read_json_object(os.path.splitext(str(html_path or ""))[0] + ".json")
    if _capital_transferee_details_incomplete(payload):
        return False
    return True


def _capital_transferee_details_incomplete(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return True
    metadata = payload.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    business_id = str(
        metadata_map.get("business_id")
        or payload.get("business_id")
        or ""
    ).strip()
    if business_id != "deal_capital_increase":
        return False
    detail_payload = payload.get("detail_payload")
    if not isinstance(detail_payload, Mapping):
        return True
    warning = str(detail_payload.get("transferee_details_warning") or "").strip()
    if warning:
        return True
    details = detail_payload.get("transferee_details")
    return not isinstance(details, list) or len(details) == 0


class TpreDealDownloader:
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
        query: _TpreDealQuery,
        page_size: int = 20,
        max_pages: Optional[int] = None,
        max_detail_pages: int = 50,
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
        self.max_detail_pages = max(1, int(max_detail_pages))
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
        self._render_timeout_ms = max(30, self.timeout) * 1000

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

    def _collect_list_candidates(
        self,
        *,
        output_dir: str,
        start: Optional[dt.date],
        end: Optional[dt.date],
        summary: DownloadSummary,
        candidates: List[_DealCandidate],
    ) -> None:
        seen_notice_ids: Set[str] = set()
        page_index = 1
        total_pages = 1
        while page_index <= total_pages:
            if self.max_pages is not None and page_index > self.max_pages:
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="tpre",
                        task_id=_task_id(self.business_id),
                        raw_reason=(
                            "explicit-max-pages-truncates-discovery: "
                            f"declared_pages={total_pages} max_pages={self.max_pages}"
                        ),
                    )
                )
                break
            try:
                payload = self._query_list_page(page_index=page_index)
            except Exception as exc:  # noqa: BLE001
                summary.pages_requested += 1
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="tpre",
                        task_id=_task_id(self.business_id),
                        raw_reason=f"list-page-{page_index}-request-failed: {exc}",
                    )
                )
                break

            summary.pages_requested += 1
            try:
                rows, total_pages = _validate_list_page_payload(
                    payload,
                    page_size=self.page_size,
                    page_index=page_index,
                )
            except _TpreDealListPayloadError as exc:
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="tpre",
                        task_id=_task_id(self.business_id),
                        raw_reason=f"list-page-{page_index}-invalid-schema: {exc}",
                    )
                )
                break
            self._rows_to_candidates(
                rows=rows,
                output_dir=output_dir,
                start=start,
                end=end,
                summary=summary,
                seen_notice_ids=seen_notice_ids,
                candidates=candidates,
            )
            page_index += 1

    def _rows_to_candidates(
        self,
        *,
        rows: List[Dict[str, Any]],
        output_dir: str,
        start: Optional[dt.date],
        end: Optional[dt.date],
        summary: DownloadSummary,
        seen_notice_ids: Set[str],
        candidates: List[_DealCandidate],
    ) -> None:
        for row in rows:
            if not isinstance(row, dict):
                continue
            summary.listed_items += 1
            normalized_row = dict(row)
            notice_id = _extract_notice_id(normalized_row)
            if not notice_id:
                summary.skipped_by_missing_xmid += 1
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="tpre",
                        task_id=_task_id(self.business_id),
                        raw_reason="missing-notice-id",
                    )
                )
                continue
            if notice_id in seen_notice_ids:
                summary.skipped_by_duplicate += 1
                continue
            seen_notice_ids.add(notice_id)

            project_code = _extract_project_code(normalized_row, fallback=notice_id)
            project_name = _extract_project_name(normalized_row)
            source_url = self._resolve_detail_url(notice_id=notice_id, row=normalized_row)
            resolved = self._resolve_deal_date(
                row=normalized_row,
                preferred_field=self.query.preferred_date_field,
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
            if self.resume and _is_resume_complete(html_path, business_id=self.business_id):
                summary.skipped_by_resume += 1
                continue
            metadata = self._build_metadata(
                row=normalized_row,
                source_url=source_url,
                deal_date=resolved["deal_date"],
                collection_date=resolved["collection_date"],
                deal_date_basis=resolved["deal_date_basis"],
                deal_date_is_imputed=resolved["deal_date_is_imputed"],
                notice_id=notice_id,
                project_code=project_code,
                project_name=project_name,
            )
            candidate = _DealCandidate(
                notice_id=notice_id,
                project_code=project_code,
                project_name=project_name,
                source_url=source_url,
                html_path=html_path,
                row=normalized_row,
                metadata=metadata,
            )
            candidates.append(candidate)
            summary.candidate_entries.append(dict(metadata))
            summary.candidate_dates.append(effective_date.isoformat())

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
        seen_notice_ids: Set[str] = set()
        for index, raw in enumerate(prefetched_candidates, start=1):
            if not isinstance(raw, dict):
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="tpre",
                        task_id=_task_id(self.business_id),
                        raw_reason=f"prefetched-entry-{index}-invalid-format",
                    )
                )
                continue
            summary.listed_items += 1
            entry = dict(raw)
            row = entry.get("row") if isinstance(entry.get("row"), dict) else {}
            notice_id = str(entry.get("notice_id") or "").strip() or _extract_notice_id(row)
            if not notice_id:
                summary.skipped_by_missing_xmid += 1
                continue
            if notice_id in seen_notice_ids:
                summary.skipped_by_duplicate += 1
                continue
            seen_notice_ids.add(notice_id)

            project_code = str(entry.get("project_code") or "").strip().upper() or _extract_project_code(
                row,
                fallback=notice_id,
            )
            project_name = str(entry.get("project_name") or "").strip() or _extract_project_name(row)
            source_url = str(entry.get("source_url") or "").strip()
            normalized_source_url = _normalize_official_tpre_url(source_url)
            if self._is_renderable_detail_page_url(normalized_source_url):
                source_url = normalized_source_url
            else:
                source_url = self._resolve_detail_url(
                    notice_id=notice_id,
                    row=row,
                )
            deal_date = parse_loose_date(entry.get("deal_date"))
            deal_date_basis = str(entry.get("deal_date_basis") or "").strip()
            deal_date_is_imputed = bool(entry.get("deal_date_is_imputed"))
            collection_date = parse_loose_date(entry.get("collection_date"))
            if deal_date_is_imputed and deal_date_basis == "collection_date":
                collection_date = collection_date or deal_date or self._collection_date()
                deal_date = None
            if deal_date is None:
                if collection_date is None:
                    resolved = self._resolve_deal_date(row=row, preferred_field=self.query.preferred_date_field)
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
            if self.resume and _is_resume_complete(html_path, business_id=self.business_id):
                summary.skipped_by_resume += 1
                continue
            metadata = self._build_metadata(
                row=row,
                source_url=source_url,
                deal_date=deal_date,
                collection_date=collection_date,
                deal_date_basis=deal_date_basis or "deal_date",
                deal_date_is_imputed=deal_date_is_imputed,
                notice_id=notice_id,
                project_code=project_code,
                project_name=project_name,
            )
            candidate = _DealCandidate(
                notice_id=notice_id,
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

    def _resolve_deal_date(self, *, row: Dict[str, Any], preferred_field: str) -> Dict[str, Any]:
        collection_date = self._collection_date()
        preferred_date = parse_loose_date(row.get(preferred_field))
        if preferred_date is not None:
            return {
                "deal_date": preferred_date,
                "collection_date": collection_date,
                "effective_date": preferred_date,
                "deal_date_basis": preferred_field,
                "deal_date_is_imputed": False,
            }
        for key in ("dealDate", "deal_date", "contractSignTime"):
            parsed = parse_loose_date(row.get(key))
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

    def _build_metadata(
        self,
        *,
        row: Dict[str, Any],
        source_url: str,
        deal_date: Optional[dt.date],
        collection_date: dt.date,
        deal_date_basis: str,
        deal_date_is_imputed: bool,
        notice_id: str,
        project_code: str,
        project_name: str,
    ) -> Dict[str, Any]:
        remark_suffix = IMPUTED_REMARK_SUFFIX if deal_date_is_imputed else ""
        metadata: Dict[str, Any] = {
            "task_id": _task_id(self.business_id),
            "record_family": "deal",
            "business_id": self.business_id,
            "business_label": self.business_label,
            "source_id": "tpre",
            "source_url": source_url,
            "collection_date": collection_date.isoformat(),
            "deal_date": deal_date.isoformat() if deal_date is not None else "",
            "deal_date_basis": deal_date_basis,
            "deal_date_is_imputed": bool(deal_date_is_imputed),
            "remark_suffix": remark_suffix,
            "deal_date_remark_suffix": remark_suffix,
            "notice_id": notice_id,
            "project_code": project_code,
            "project_name": project_name,
            "row": row,
        }
        if row.get("contractSignTime") is not None:
            metadata["contractSignTime"] = str(row.get("contractSignTime"))
        return metadata

    def _query_list_page(self, *, page_index: int) -> Dict[str, Any]:
        url = urllib.parse.urljoin(BASE_URL, self.query.list_endpoint)
        parsed = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        query.update(
            {
                "current": str(int(page_index)),
                "size": str(self.page_size),
            }
        )
        final_url = urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urllib.parse.urlencode(query, doseq=True),
                parsed.fragment,
            )
        )
        request = urllib.request.Request(url=final_url, headers=REQUEST_HEADERS, method="GET")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _build_renderable_detail_page_url(self, *, notice_id: str, project_code: str = "") -> str:
        route = str(self.query.render_page_route or "").strip()
        if not route:
            return ""
        query_value = str(notice_id or "").strip() or str(project_code or "").strip()
        if not query_value:
            return ""
        return _normalize_official_tpre_url(
            f"{route}?{urllib.parse.urlencode({'id': query_value})}"
        )

    def _resolve_detail_url(self, *, notice_id: str, row: Dict[str, Any]) -> str:
        renderable_url = self._resolve_renderable_detail_page_url(row=row)
        if renderable_url:
            return renderable_url
        return self._build_renderable_detail_page_url(
            notice_id=notice_id,
            project_code=_extract_project_code(row, fallback=""),
        )

    def _resolve_renderable_detail_page_url(self, *, row: Dict[str, Any]) -> str:
        for key in ("projectLink", "projectlink", "detailUrl", "detail_url", "url", "href"):
            normalized = _normalize_official_tpre_url(row.get(key))
            if self._is_renderable_detail_page_url(normalized):
                return normalized
        return self._build_renderable_detail_page_url(
            notice_id=_extract_notice_id(row),
            project_code=_extract_project_code(row, fallback=""),
        )

    def _resolve_candidate_renderable_page_url(self, *, candidate: _DealCandidate) -> str:
        direct_source_url = _normalize_official_tpre_url(candidate.source_url)
        if self._is_renderable_detail_page_url(direct_source_url):
            return direct_source_url
        return self._resolve_renderable_detail_page_url(row=candidate.row)

    @staticmethod
    def _is_renderable_detail_page_url(page_url: str) -> bool:
        normalized = str(page_url or "").strip()
        if not normalized:
            return False
        parsed = urllib.parse.urlsplit(normalized)
        if parsed.scheme not in {"http", "https"}:
            return False
        if parsed.netloc.lower() != "trade.tpre.cn":
            return False
        path = parsed.path or ""
        return path.startswith("/transaction-view/")

    @staticmethod
    def _is_api_detail_url(page_url: str) -> bool:
        normalized = str(page_url or "").strip()
        if not normalized:
            return False
        parsed = urllib.parse.urlsplit(normalized)
        if parsed.netloc.lower() != "trade.tpre.cn":
            return False
        path = parsed.path or ""
        return path.startswith("/transaction/biz/")

    def _query_detail_payload(self, *, notice_id: str) -> Dict[str, Any]:
        endpoint = str(self.query.detail_api_endpoint or "").strip()
        if not endpoint:
            raise RuntimeError(f"detail-api-unavailable notice_id={notice_id}")
        url = urllib.parse.urljoin(
            BASE_URL,
            f"{endpoint}?{urllib.parse.urlencode({'id': notice_id})}",
        )
        request = urllib.request.Request(url=url, headers=REQUEST_HEADERS, method="GET")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _has_detail_api_endpoint(self) -> bool:
        return bool(str(self.query.detail_api_endpoint or "").strip())

    def _download_candidate(
        self,
        *,
        candidate: _DealCandidate,
        summary: DownloadSummary,
        start: Optional[dt.date] = None,
        end: Optional[dt.date] = None,
        candidate_index: Optional[int] = None,
    ) -> None:
        renderable_page_url = self._resolve_candidate_renderable_page_url(candidate=candidate)
        if not renderable_page_url:
            raw_reason = f"invalid-unrenderable-source-url notice_id={candidate.notice_id}: {candidate.source_url}"
            if self._is_api_detail_url(candidate.source_url):
                raw_reason = (
                    f"invalid-unrenderable-source-url api-detail-endpoint notice_id="
                    f"{candidate.notice_id}: {candidate.source_url}"
                )
            summary.detail_failed += 1
            summary.typed_errors.append(
                invalid_candidate_error(
                    source_id="tpre",
                    task_id=_task_id(self.business_id),
                    raw_reason=raw_reason,
                )
            )
            return

        detail_payload: Dict[str, Any] = {}
        detail_payload_errors: List[str] = []
        detail_payload_was_fetched = False
        detail_fetch_error = ""

        def append_execute_error(raw_reason: str) -> None:
            detail_payload_errors.append(raw_reason)
            summary.typed_errors.append(
                execute_failed_error(
                    source_id="tpre",
                    task_id=_task_id(self.business_id),
                    raw_reason=raw_reason,
                )
            )

        if self._has_detail_api_endpoint():
            try:
                raw_detail_payload = self._query_detail_payload(notice_id=candidate.notice_id)
                if not isinstance(raw_detail_payload, Mapping):
                    summary.detail_failed += 1
                    summary.typed_errors.append(
                        invalid_candidate_error(
                            source_id="tpre",
                            task_id=_task_id(self.business_id),
                            raw_reason=(
                                "detail-payload-non-mapping: "
                                f"field=detail_payload notice_id={candidate.notice_id}"
                            ),
                        )
                    )
                    return
                detail_payload = dict(raw_detail_payload)
                detail_payload_was_fetched = True
            except Exception as exc:  # noqa: BLE001
                detail_fetch_error = (
                    f"detail-payload-fetch-failed notice_id={candidate.notice_id}: {exc}"
                )
                self.logger.warning(
                    "detail payload fetch failed for notice_id=%s: %s",
                    candidate.notice_id,
                    exc,
                )
        if self.business_id == "deal_capital_increase":
            try:
                detail_payload = self._merge_capital_transferee_details(
                    detail_payload=detail_payload,
                    project_code=candidate.project_code,
                    notice_id=candidate.notice_id,
                )
            except Exception as exc:  # noqa: BLE001
                merge_raw_reason = (
                    f"capital-transferee-merge-failed notice_id={candidate.notice_id}: {exc}"
                )
                self.logger.warning(
                    "capital transferee merge failed for notice_id=%s: %s",
                    candidate.notice_id,
                    exc,
                )
                if detail_fetch_error:
                    detail_payload_errors.extend((detail_fetch_error, merge_raw_reason))
                    summary.typed_errors.append(
                        execute_failed_error(
                            source_id="tpre",
                            task_id=_task_id(self.business_id),
                            raw_reason=f"{detail_fetch_error} | {merge_raw_reason}",
                        )
                    )
                else:
                    append_execute_error(merge_raw_reason)
            else:
                if detail_fetch_error:
                    append_execute_error(detail_fetch_error)
                elif detail_payload_was_fetched:
                    summary.detail_fetched += 1
        elif detail_fetch_error:
            append_execute_error(detail_fetch_error)
        elif detail_payload_was_fetched:
            summary.detail_fetched += 1
        detail_deal_date = self._extract_detail_deal_date(detail_payload)
        if detail_deal_date is not None:
            candidate.metadata["deal_date"] = detail_deal_date.isoformat()
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
            if self.resume and _is_resume_complete(
                candidate.html_path,
                business_id=self.business_id,
            ):
                summary.skipped_by_resume += 1
                return
        try:
            rendered_html = self._fetch_rendered_detail_html(
                page_url=renderable_page_url,
                expected_project_code=candidate.project_code,
                expected_project_name=candidate.project_name,
            )
        except Exception as exc:  # noqa: BLE001
            raw_reason = f"rendered-page-fetch-failed notice_id={candidate.notice_id}: {exc}"
            append_execute_error(raw_reason)
            summary.detail_failed += 1
            return
        if _TPRE_DEAL_NOTICE_SHELL_MARKER in str(rendered_html or ""):
            try:
                self._write_invalid_shell_evidence_if_needed(
                    rendered_html=rendered_html,
                    source_url=rendered_html.source_url,
                    final_url=rendered_html.final_url,
                    html_path=candidate.html_path,
                    metadata=candidate.metadata,
                )
            except Exception as exc:  # noqa: BLE001
                summary.typed_errors.append(
                    save_failed_error(
                        source_id="tpre",
                        task_id=_task_id(self.business_id),
                        raw_reason=(
                            "invalid-shell-evidence-save-failed "
                            f"notice_id={candidate.notice_id}: {exc}"
                        ),
                    )
                )
            else:
                summary.typed_errors.append(
                    execute_failed_error(
                        source_id="tpre",
                        task_id=_task_id(self.business_id),
                        raw_reason=f"invalid-detail-shell notice_id={candidate.notice_id}",
                    )
                )
            summary.detail_failed += 1
            return
        sidecar_written = False
        json_path = os.path.splitext(candidate.html_path)[0] + ".json"
        if not reserve_download_target(
            summary,
            html_root=self.html_root,
            html_path=candidate.html_path,
            source_id="tpre",
            task_id=_task_id(self.business_id),
        ):
            summary.detail_failed += 1
            return
        try:
            self._save_snapshot_html(
                html_path=candidate.html_path,
                rendered_html=rendered_html,
            )
            self._write_invalid_shell_evidence_if_needed(
                rendered_html=rendered_html,
                source_url=rendered_html.source_url,
                final_url=rendered_html.final_url,
                html_path=candidate.html_path,
                metadata=candidate.metadata,
            )
            self._write_sidecar_json(
                json_path=json_path,
                metadata=candidate.metadata,
                detail_url=renderable_page_url,
                detail_payload=detail_payload,
                detail_payload_error=" | ".join(detail_payload_errors),
                source_url=rendered_html.source_url,
                final_url=rendered_html.final_url,
                http_status=rendered_html.http_status,
                save_status="pending",
            )
            sidecar_written = True
            if detail_payload_errors:
                self._write_sidecar_json(
                    json_path=json_path,
                    metadata=candidate.metadata,
                    detail_url=renderable_page_url,
                    detail_payload=detail_payload,
                    detail_payload_error=" | ".join(detail_payload_errors),
                    source_url=rendered_html.source_url,
                    final_url=rendered_html.final_url,
                    http_status=rendered_html.http_status,
                    save_status="failed",
                )
            else:
                self._write_sidecar_json(
                    json_path=json_path,
                    metadata=candidate.metadata,
                    detail_url=renderable_page_url,
                    detail_payload=detail_payload,
                    detail_payload_error="",
                    source_url=rendered_html.source_url,
                    final_url=rendered_html.final_url,
                    http_status=rendered_html.http_status,
                    save_status="complete",
                )
                if os.path.isfile(json_path):
                    attach_archive_integrity_to_sidecar(
                        json_path=json_path,
                        html_path=candidate.html_path,
                    )
                self._notify_item_saved(candidate=candidate)
                clear_artifact_evidence_sidecar(candidate.html_path)
        except Exception as exc:  # noqa: BLE001
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
                                "source_id": "tpre",
                                "record_family": "deal",
                                "business_id": self.business_id,
                            },
                            detail_url=renderable_page_url,
                            detail_payload={},
                            source_url=rendered_html.source_url,
                            final_url=rendered_html.final_url,
                            http_status=rendered_html.http_status,
                            save_status="failed",
                        )
                    except Exception as marker_exc:  # noqa: BLE001
                        cleanup_reason += f"; resume-sidecar-failed-marker-failed: {marker_exc}"
            summary.detail_failed += 1
            summary.typed_errors.append(
                save_failed_error(
                    source_id="tpre",
                    task_id=_task_id(self.business_id),
                    raw_reason=f"save-failed notice_id={candidate.notice_id}: {exc}{cleanup_reason}",
                )
            )
            return
        if detail_payload_errors:
            summary.detail_failed += 1
            return
        summary.saved += 1
        record_downloaded_target(summary, html_root=self.html_root, html_path=candidate.html_path)

    def _merge_capital_transferee_details(
        self,
        *,
        detail_payload: Dict[str, Any],
        project_code: str,
        notice_id: str,
    ) -> Dict[str, Any]:
        if not isinstance(detail_payload, Mapping):
            raise TypeError(f"field=detail_payload non-mapping notice_id={notice_id}")
        payload = dict(detail_payload)
        resolved_project_code = self._resolve_capital_detail_project_code(
            detail_payload=payload,
            candidate_project_code=project_code,
            notice_id=notice_id,
        )
        if not resolved_project_code:
            payload["transferee_details"] = []
            payload["transferee_details_warning"] = "missing-project-code"
            return payload
        details = self._collect_capital_transferee_details(project_code=resolved_project_code)
        payload["transferee_details_project_code"] = resolved_project_code
        payload["transferee_details"] = details
        return payload

    @classmethod
    def _resolve_capital_detail_project_code(
        cls,
        *,
        detail_payload: Dict[str, Any],
        candidate_project_code: str,
        notice_id: str,
    ) -> HttpFetchedText:
        detail_project_code = cls._extract_project_code_from_detail_payload(detail_payload)
        if detail_project_code:
            return detail_project_code
        normalized_candidate = str(candidate_project_code or "").strip().upper()
        normalized_notice_id = str(notice_id or "").strip().upper()
        if normalized_candidate and (
            normalized_candidate != normalized_notice_id or _looks_like_tpre_project_code(normalized_candidate)
        ):
            return normalized_candidate
        return ""

    @staticmethod
    def _extract_project_code_from_detail_payload(payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        containers: list[Dict[str, Any]] = [payload]
        data = payload.get("data")
        if isinstance(data, dict):
            containers.insert(0, data)
        for container in containers:
            for key in ("projectCode", "code", "xmbh"):
                value = str(container.get(key) or "").strip().upper()
                if value:
                    return value
        return ""

    def _collect_capital_transferee_details(self, *, project_code: str) -> List[Dict[str, Any]]:
        normalized_project_code = str(project_code or "").strip()
        if not normalized_project_code:
            return []
        current = 1
        size = max(1, int(self.page_size))
        all_records: List[Dict[str, Any]] = []
        seen_page_signatures: Set[str] = set()
        seen_record_signatures: Set[str] = set()
        while current <= self.max_detail_pages:
            payload = self._query_capital_transferee_details_page(
                project_code=normalized_project_code,
                current=current,
                size=size,
            )
            records = self._extract_paginated_records(payload)
            page_signature = self._stable_json_signature(records if records else payload)
            if page_signature in seen_page_signatures:
                break
            seen_page_signatures.add(page_signature)
            for record in records:
                record_signature = self._stable_json_signature(record)
                if record_signature in seen_record_signatures:
                    continue
                seen_record_signatures.add(record_signature)
                all_records.append(record)
            total = self._extract_paginated_total(payload)
            if total is not None:
                total_pages = max(1, math.ceil(total / size))
                if current >= total_pages:
                    break
            elif not records or len(records) < size:
                break
            current += 1
        return all_records

    @staticmethod
    def _stable_json_signature(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    def _query_capital_transferee_details_page(
        self,
        *,
        project_code: str,
        current: int,
        size: int,
    ) -> Dict[str, Any]:
        params = {
            "projectCode": project_code,
            "current": int(current),
            "size": int(size),
        }
        endpoint = str(self.query.transferee_details_endpoint or "").strip()
        if not endpoint:
            raise RuntimeError(f"transferee-details-endpoint-unavailable projectCode={project_code}")
        url = urllib.parse.urljoin(
            BASE_URL,
            f"{endpoint}?{urllib.parse.urlencode(params)}",
        )
        request = urllib.request.Request(url=url, headers=REQUEST_HEADERS, method="GET")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _extract_paginated_records(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        sentinel = object()
        if not isinstance(payload, dict):
            raise TypeError("transferee-pagination-payload-non-mapping")
        TpreDealDownloader._raise_for_paginated_api_error(payload)
        data = payload.get("data")
        raw_records: Any = sentinel
        records_field = ""
        if isinstance(data, list):
            raw_records = data
            records_field = "data"
        elif isinstance(data, dict):
            for key in ("records", "rows", "list", "items"):
                if key not in data:
                    continue
                raw_records = data.get(key)
                records_field = f"data.{key}"
                break
        elif data is not None:
            raise TypeError("transferee-pagination-data-invalid")
        if raw_records is sentinel:
            for key in ("records", "rows", "list", "items"):
                if key not in payload:
                    continue
                raw_records = payload.get(key)
                records_field = key
                break
        if raw_records is sentinel:
            raise ValueError("transferee-pagination-records-missing")
        if not isinstance(raw_records, list):
            raise TypeError(f"transferee-pagination-records-non-list field={records_field}")
        records: List[Dict[str, Any]] = []
        for index, item in enumerate(raw_records):
            if not isinstance(item, dict):
                raise TypeError(
                    f"transferee-pagination-record-non-mapping field={records_field}[{index}]"
                )
            records.append(item)
        return records

    @staticmethod
    def _extract_paginated_total(payload: Dict[str, Any]) -> Optional[int]:
        if not isinstance(payload, dict):
            raise TypeError("transferee-pagination-payload-non-mapping")
        TpreDealDownloader._raise_for_paginated_api_error(payload)
        data = payload.get("data")
        candidates: list[tuple[str, Any]] = []
        if isinstance(data, dict):
            for key in ("total", "count"):
                if key in data:
                    candidates.append((f"data.{key}", data.get(key)))
        for key in ("total", "count"):
            if key in payload:
                candidates.append((key, payload.get(key)))
        for field, raw in candidates:
            if raw is None or raw == "":
                raise ValueError(f"transferee-pagination-total-invalid field={field} value={raw!r}")
            try:
                total = int(raw)
            except (TypeError, ValueError):
                raise ValueError(
                    f"transferee-pagination-total-invalid field={field} value={raw!r}"
                ) from None
            if total < 0:
                raise ValueError(f"transferee-pagination-total-invalid field={field} value={raw!r}")
            return total
        return None

    @staticmethod
    def _raise_for_paginated_api_error(payload: Dict[str, Any]) -> None:
        if "code" in payload and payload.get("code") not in (None, ""):
            raw_code = payload.get("code")
            try:
                code = int(raw_code)
            except (TypeError, ValueError):
                raise ValueError(f"transferee-pagination-api-code-invalid code={raw_code!r}") from None
            if code not in {0, 200}:
                message = str(
                    payload.get("message")
                    or payload.get("msg")
                    or payload.get("error")
                    or ""
                ).strip()
                raise RuntimeError(
                    f"transferee-pagination-api-error code={code} message={message}"
                )
        error = payload.get("error")
        if error:
            raise RuntimeError(f"transferee-pagination-api-error error={error}")
        if payload.get("success") is False:
            message = str(payload.get("message") or payload.get("msg") or "").strip()
            raise RuntimeError(f"transferee-pagination-api-error success=false message={message}")

    @staticmethod
    def _extract_detail_deal_date(payload: Dict[str, Any]) -> Optional[dt.date]:
        if not isinstance(payload, dict):
            return None
        for key in ("contractSignTime", "dealDate", "deal_date", "signDate"):
            parsed = parse_loose_date(payload.get(key))
            if parsed is not None:
                return parsed
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("contractSignTime", "dealDate", "deal_date", "signDate"):
                parsed = parse_loose_date(data.get(key))
                if parsed is not None:
                    return parsed
        return None

    def _fetch_rendered_detail_html(
        self,
        *,
        page_url: str,
        expected_project_code: str,
        expected_project_name: str,
    ) -> HttpFetchedText:
        return asyncio.run(
            self._fetch_rendered_detail_html_async(
                page_url=page_url,
                expected_project_code=expected_project_code,
                expected_project_name=expected_project_name,
            )
        )

    async def _fetch_rendered_detail_html_async(
        self,
        *,
        page_url: str,
        expected_project_code: str,
        expected_project_name: str,
    ) -> HttpFetchedText:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await launch_chromium_browser(pw, headless=True)
            context = await browser.new_context(
                ignore_https_errors=True,
                user_agent=REQUEST_HEADERS["User-Agent"],
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = await context.new_page()
            try:
                return await self._fetch_rendered_html_from_page(
                    page=page,
                    page_url=page_url,
                    expected_project_code=expected_project_code,
                    expected_project_name=expected_project_name,
                )
            finally:
                await page.close()
                await context.close()
                await browser.close()

    async def _fetch_rendered_html_from_page(
        self,
        *,
        page,
        page_url: str,
        expected_project_code: str,
        expected_project_name: str,
    ) -> tuple[str, int]:
        response = await page.goto(
            page_url,
            wait_until="domcontentloaded",
            timeout=self._render_timeout_ms,
        )
        transport_evidence = HttpFetchedText(
            "",
            source_url=page_url,
            final_url=getattr(response, "url", None),
            http_status=getattr(response, "status", None),
        )
        await page.wait_for_selector("body", timeout=self._render_timeout_ms)
        await self._select_result_tab_if_needed(page=page)
        last_html = ""
        for _ in range(12):
            await page.wait_for_timeout(1500)
            html = await page.content()
            last_html = html
            if self._is_real_deal_detail_page(
                html_text=html,
                expected_project_code=expected_project_code,
                expected_project_name=expected_project_name,
            ):
                return HttpFetchedText(
                    html,
                    source_url=transport_evidence.source_url,
                    final_url=transport_evidence.final_url,
                    http_status=transport_evidence.http_status,
                )
        raise RuntimeError(
            "tpre-deal-page-not-ready: "
            f"expected_project_code={expected_project_code} page_url={page_url} html_len={len(last_html)}"
        )

    async def _select_result_tab_if_needed(self, *, page) -> None:
        label = str(self.query.result_tab_label or "").strip()
        if not label:
            return
        candidates = page.locator(f"text={label}")
        for _ in range(20):
            for index in range(await candidates.count()):
                tab = candidates.nth(index)
                try:
                    box = await tab.bounding_box()
                except Exception:
                    box = None
                if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
                    continue
                await tab.click(timeout=min(self._render_timeout_ms, 10000))
                await page.wait_for_timeout(800)
                return
            await page.wait_for_timeout(500)
        await page.wait_for_timeout(800)

    @staticmethod
    def _normalize_html_text(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "")).upper()

    @classmethod
    def _is_real_deal_detail_page(
        cls,
        *,
        html_text: str,
        expected_project_code: str,
        expected_project_name: str,
    ) -> bool:
        soup = BeautifulSoup(html_text or "", "html.parser")
        normalized_text = cls._normalize_html_text(soup.get_text(" ", strip=True))
        if not normalized_text:
            return False
        expected_code = cls._normalize_html_text(expected_project_code)
        expected_name = cls._normalize_html_text(expected_project_name)
        target_matches = bool(expected_code and expected_code in normalized_text) or bool(
            expected_name and expected_name in normalized_text
        )
        if not target_matches:
            return False
        if "成交公告" in normalized_text:
            return True
        common_result_markers = ("结果公告", "项目编号", "项目名称")
        deal_result_markers = ("交易价格", "合同签订日期")
        capital_result_markers = ("投资金额", "实缴出资金额", "持股比例")
        return (
            bool(expected_code and expected_code in normalized_text)
            and all(marker in normalized_text for marker in common_result_markers)
            and (
                all(marker in normalized_text for marker in deal_result_markers)
                or all(marker in normalized_text for marker in capital_result_markers)
            )
        )

    @staticmethod
    def _save_snapshot_html(*, html_path: str, rendered_html: str) -> None:
        os.makedirs(os.path.dirname(html_path), exist_ok=True)
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(rendered_html)

    @staticmethod
    def _sha256_text(value: str) -> str:
        return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

    @classmethod
    def _write_invalid_shell_evidence_if_needed(
        cls,
        *,
        rendered_html: str,
        source_url: str,
        final_url: str,
        html_path: str,
        metadata: Dict[str, Any],
    ) -> None:
        if _TPRE_DEAL_NOTICE_SHELL_MARKER not in str(rendered_html or ""):
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
            "content_sha256": cls._sha256_text(rendered_html),
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
        detail_payload: Dict[str, Any],
        detail_payload_error: str = "",
        source_url: str,
        final_url: str,
        http_status: int,
        save_status: str = "complete",
    ) -> None:
        fetched = HttpFetchedText(
            "",
            source_url=source_url,
            final_url=final_url,
            http_status=http_status,
        )
        sidecar = {
            "save_status": save_status,
            "task_id": str(metadata.get("task_id") or ""),
            "source_id": str(metadata.get("source_id") or ""),
            "record_family": str(metadata.get("record_family") or ""),
            "business_id": str(metadata.get("business_id") or ""),
            "source_url": fetched.source_url,
            "final_url": fetched.final_url,
            "http_status": fetched.http_status,
            "metadata": metadata,
            "detail_url": detail_url,
            "detail_payload": detail_payload,
        }
        if detail_payload_error:
            sidecar["detail_payload_error"] = detail_payload_error
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        temp_json_path = f"{json_path}.tmp"
        with open(temp_json_path, "w", encoding="utf-8") as handle:
            json.dump(sidecar, handle, ensure_ascii=False, indent=2)
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
                    candidate.metadata.get("deal_date")
                    or candidate.metadata.get("collection_date")
                    or ""
                ),
                "source_id": "tpre",
                "business_id": self.business_id,
                "row": candidate.row,
            }
        )


class TianjinDealEquityTransferDownloader(TpreDealDownloader):
    def __init__(self, **kwargs):
        requirement = apply_deal_manifest_fields(
            self,
            source_id="tpre",
            business_id="deal_equity_transfer",
        )
        super().__init__(
            query=_TpreDealQuery(
                business_id="deal_equity_transfer",
                list_endpoint=requirement.list_endpoint,
                render_page_route=requirement.render_page_route,
                detail_api_endpoint=requirement.detail_api_endpoint,
                transferee_details_endpoint=requirement.transferee_details_endpoint,
                preferred_date_field=preferred_deal_date_field(requirement),
                result_tab_label="产权转让",
            ),
            **kwargs,
        )


class TianjinDealPhysicalAssetDownloader(TpreDealDownloader):
    manifest_list_endpoint = "/transaction/biz/transaction-management/anmuas/result-notice/page?bizType=ENTERPRISE_ASSETS"
    manifest_detail_route = "/transaction-view/data/common/transaction-announcement"
    manifest_render_page_route = "/transaction-view/data/common/transaction-announcement"
    manifest_detail_api_endpoint = ""
    manifest_transferee_details_endpoint = ""
    manifest_date_field_candidates = ("contractSignTime", "deal_date")

    def __init__(self, **kwargs):
        super().__init__(
            query=_TpreDealQuery(
                business_id="deal_physical_asset",
                list_endpoint=self.manifest_list_endpoint,
                render_page_route=self.manifest_render_page_route,
                detail_api_endpoint=self.manifest_detail_api_endpoint,
                transferee_details_endpoint=self.manifest_transferee_details_endpoint,
                preferred_date_field="contractSignTime",
                result_tab_label="企业资产",
            ),
            **kwargs,
        )


class TianjinDealCapitalIncreaseDownloader(TpreDealDownloader):
    def __init__(self, **kwargs):
        requirement = apply_deal_manifest_fields(
            self,
            source_id="tpre",
            business_id="deal_capital_increase",
        )
        super().__init__(
            query=_TpreDealQuery(
                business_id="deal_capital_increase",
                list_endpoint=requirement.list_endpoint,
                render_page_route=requirement.render_page_route,
                detail_api_endpoint=requirement.detail_api_endpoint,
                transferee_details_endpoint=requirement.transferee_details_endpoint,
                preferred_date_field=preferred_deal_date_field(requirement),
                result_tab_label="企业增资",
            ),
            **kwargs,
        )
