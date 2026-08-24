"""SSE deal notice downloader."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import shutil
import time
import urllib.error
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
    real_date_outside_requested_range,
    record_downloaded_target,
    reserve_download_target,
)
from .deal_contracts import apply_deal_manifest_fields, preferred_deal_date_field
from .discovery_evidence import DiscoveryEvidenceError, DiscoveryTaskEvidence

BASE_URL = "https://www.suaee.com"

REQUEST_HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "projectType": "suaeeHome",
    "sourcecode": "SUAEE",
    "User-Agent": "Mozilla/5.0",
}

IMPUTED_REMARK_SUFFIX = "成交日期缺失，按采集日填列"
TAG_ASSET_ATTRS = (
    ("link", "href"),
    ("img", "src"),
    ("source", "src"),
    ("video", "poster"),
)
_SSE_DEAL_NOTICE_SHELL_MARKER = bytes(
    (
        60,
        104,
        49,
        62,
        83,
        83,
        69,
        32,
        68,
        101,
        97,
        108,
        32,
        78,
        111,
        116,
        105,
        99,
        101,
        60,
        47,
        104,
        49,
        62,
    )
).decode("ascii")


def _task_id(business_id: str) -> str:
    return f"sse:deal:{business_id}"


def _extract_notice_id(row: Dict[str, Any]) -> str:
    for key in ("GGID", "noticeId", "NOTICEID", "NOTICE_ID"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_xmid(row: Dict[str, Any]) -> str:
    for key in ("XMID", "xmid", "ID", "id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_project_code(row: Dict[str, Any]) -> str:
    for key in ("XMBH", "xmbh", "projectCode", "code"):
        value = str(row.get(key) or "").strip().upper()
        if value:
            return value
    return ""


def _extract_project_name(row: Dict[str, Any]) -> str:
    for key in ("XMMC", "xmmc", "projectName", "title", "name"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("records", "list", "rows", "items"):
            raw = data.get(key)
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
    return []


class _SseDealListStructureError(ValueError):
    pass


def _nonnegative_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise _SseDealListStructureError(f"{field} must be a nonnegative integer")
    if isinstance(value, float) and not value.is_integer():
        raise _SseDealListStructureError(f"{field} must be a nonnegative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _SseDealListStructureError(
            f"{field} must be a nonnegative integer"
        ) from exc
    if parsed < 0:
        raise _SseDealListStructureError(f"{field} must be a nonnegative integer")
    return parsed


def _extract_declared_pagination(
    payload: Dict[str, Any],
    *,
    page_size: int,
) -> tuple[int | None, int | None]:
    """Return authoritative ``(total_records, total_pages)`` when declared."""
    containers: list[Mapping[str, Any]] = [payload]
    data = payload.get("data")
    if isinstance(data, Mapping):
        containers.append(data)
    extra = payload.get("extra")
    if isinstance(extra, Mapping):
        containers.append(extra)

    total_values: set[int] = set()
    page_values: set[int] = set()
    for container in containers:
        for key in ("total", "totalCount", "recordsTotal", "totalElements"):
            if container.get(key) is not None:
                total_values.add(
                    _nonnegative_integer(container[key], field=f"declared {key}")
                )
        for key in ("totalPages", "pages", "pageCount"):
            if container.get(key) is not None:
                page_values.add(
                    _nonnegative_integer(container[key], field=f"declared {key}")
                )

    # SSE uses a scalar ``extra`` as a record count on its list APIs.
    if extra is not None and not isinstance(extra, Mapping):
        total_values.add(
            _nonnegative_integer(extra, field="declared total records")
        )

    if len(total_values) > 1:
        raise _SseDealListStructureError(
            f"conflicting declared total records: {sorted(total_values)}"
        )
    if len(page_values) > 1:
        raise _SseDealListStructureError(
            f"conflicting declared total pages: {sorted(page_values)}"
        )

    total_records = next(iter(total_values), None)
    declared_pages = next(iter(page_values), None)
    calculated_pages = (
        None
        if total_records is None
        else math.ceil(total_records / max(1, int(page_size)))
    )
    if declared_pages is not None and calculated_pages is not None:
        if declared_pages != calculated_pages:
            raise _SseDealListStructureError(
                "declared total pages do not close with total records and page size: "
                f"pages={declared_pages} total={total_records} page_size={page_size}"
            )
    elif declared_pages is None:
        declared_pages = calculated_pages
    return total_records, declared_pages


def _extract_total_pages(payload: Dict[str, Any], *, page_size: int, default_page: int) -> int:
    _total_records, declared_pages = _extract_declared_pagination(
        payload,
        page_size=page_size,
    )
    return max(1, declared_pages if declared_pages is not None else default_page)


def _deal_list_row_identity(row: Dict[str, Any]) -> str:
    identity = (
        _extract_notice_id(row)
        or _extract_xmid(row)
        or _extract_project_code(row)
    )
    return identity or json.dumps(
        row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _is_skip_asset_url(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        not lowered
        or lowered.startswith("#")
        or lowered.startswith("data:")
        or lowered.startswith("javascript:")
        or lowered.startswith("mailto:")
        or lowered.startswith("tel:")
        or lowered.startswith("blob:")
    )


def _is_resume_complete(html_path: str, *, business_id: str) -> bool:
    return complete_resume_sidecar_exists(
        html_path,
        require_integrity=True,
        expected_fields={
            "source_id": "sse",
            "record_family": "deal",
            "business_id": business_id,
            "task_id": _task_id(business_id),
        },
    )


def _guess_ext_from_content_type(content_type: str) -> str:
    text = (content_type or "").lower()
    if "javascript" in text:
        return ".js"
    if "css" in text:
        return ".css"
    if "png" in text:
        return ".png"
    if "jpeg" in text or "jpg" in text:
        return ".jpg"
    if "svg" in text:
        return ".svg"
    if "gif" in text:
        return ".gif"
    if "webp" in text:
        return ".webp"
    if "woff2" in text:
        return ".woff2"
    if "woff" in text:
        return ".woff"
    if "ttf" in text:
        return ".ttf"
    if "eot" in text:
        return ".eot"
    return ""


@dataclass
class _DealCandidate:
    notice_id: str
    xmid: str
    project_code: str
    project_name: str
    source_url: str
    api_url: str
    html_path: str
    row: Dict[str, Any]
    metadata: Dict[str, Any]


class SseDealDownloader:
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
        business_id: str,
        fclass: str,
        page_size: int = 20,
        max_pages: Optional[int] = None,
        concurrency: int = 1,
        resume: bool = False,
        timeout: int = 20,
        save_json: bool = False,
        ssl_verify: bool = True,
        ssl_ca_bundle: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        item_saved_callback=None,
        run_id: Optional[str] = None,
    ):
        self.html_root = html_root
        self.business_id = business_id
        self.fclass = fclass
        self.page_size = max(1, int(page_size))
        self.max_pages = None if max_pages is None else max(1, int(max_pages))
        self.concurrency = max(1, int(concurrency))
        self.resume = bool(resume)
        self.timeout = max(5, int(timeout))
        self.save_json = bool(save_json)
        self.ssl_verify = bool(ssl_verify)
        self.ssl_ca_bundle = str(ssl_ca_bundle or "").strip() or None
        self.logger = logger or logging.getLogger("parser_v2")
        self.item_saved_callback = item_saved_callback
        self.run_id = str(run_id or "").strip() or f"run-{int(time.time() * 1000)}"
        self._deal_requirement = apply_deal_manifest_fields(
            self,
            source_id="sse",
            business_id=self.business_id,
        )
        self._render_timeout_ms = max(120, self.timeout) * 1000
        self._detail_retries = 2
        self.business_label = get_business_descriptor(
            self.business_id,
            family_id="deal",
        ).project_type_label

    def _collection_date(self) -> dt.date:
        return dt.date.today()

    def _preferred_date_field(self) -> str:
        return preferred_deal_date_field(self._deal_requirement)

    def _list_api_url(self) -> str:
        return urllib.parse.urljoin(BASE_URL, self.manifest_list_endpoint)

    def _detail_api_url(self) -> str:
        return urllib.parse.urljoin(BASE_URL, self.manifest_detail_route)

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
        task_id = _task_id(self.business_id)
        query_id = "deal-notice-list"
        task_evidence = DiscoveryTaskEvidence(
            root=output_dir,
            source_id="sse",
            task_id=task_id,
            run_id=self.run_id,
            expected_query_ids=(query_id,),
        )
        staged_summary = DownloadSummary()
        staged_candidates: List[_DealCandidate] = []
        seen_notice_ids: Set[str] = set()
        query_succeeded = False
        with task_evidence:
            query_evidence = task_evidence.query(
                query_id,
                authoritative_total=True,
                page_size=self.page_size,
            )
            with query_evidence:
                page_index = 1
                declared_pages: Optional[int] = None
                declared_total: Optional[int] = None
                termination_reason = "declared_pages_exhausted"
                termination_facts: dict[str, object] = {}
                while True:
                    # Without an authoritative total, an explicit operator cap is
                    # a failure rather than a silent partial result.
                    if (
                        declared_pages is None
                        and self.max_pages is not None
                        and page_index > self.max_pages
                    ):
                        reason = (
                            f"list-page-{page_index}-max-pages-truncated: "
                            f"authoritative total pages unavailable max_pages={self.max_pages}"
                        )
                        staged_summary.typed_errors.append(
                            list_failed_error(
                                source_id="sse",
                                task_id=task_id,
                                raw_reason=reason,
                            )
                        )
                        query_evidence.fail(
                            termination_reason="explicit_max_pages",
                            details={"max_pages": self.max_pages, "next_page": page_index},
                        )
                        break

                    try:
                        raw_response = self._query_list_page(page_index=page_index)
                        response = self._coerce_list_response(raw_response)
                    except Exception as exc:  # noqa: BLE001
                        staged_summary.pages_requested += 1
                        staged_summary.typed_errors.append(
                            list_failed_error(
                                source_id="sse",
                                task_id=task_id,
                                raw_reason=f"list-page-{page_index}-request-failed: {exc}",
                            )
                        )
                        query_evidence.fail(
                            termination_reason="request_failed",
                            details={"page_index": page_index, "error": str(exc)},
                        )
                        break

                    staged_summary.pages_requested += 1
                    try:
                        query_evidence.capture_page(
                            page_index=page_index,
                            response=response,
                            body_format="json",
                            request_metadata={
                                "method": "POST",
                                "page_index": page_index,
                                "page_size": self.page_size,
                                "fclass": self.fclass,
                            },
                        )
                        payload = self._decode_list_response(response)
                        code = _nonnegative_integer(payload.get("code", 200), field="code")
                        if code not in {0, 200}:
                            raise RuntimeError(
                                f"SSE deal list API code={code}: "
                                f"{str(payload.get('message') or payload.get('msg') or '').strip()}"
                            )
                        rows = _extract_rows(payload)
                        total_records, page_count = _extract_declared_pagination(
                            payload,
                            page_size=self.page_size,
                        )
                        if declared_pages is not None and page_count != declared_pages:
                            raise _SseDealListStructureError(
                                "declared total pages changed during traversal: "
                                f"{declared_pages} -> {page_count}"
                            )
                        if declared_total is not None and total_records != declared_total:
                            raise _SseDealListStructureError(
                                "declared total records changed during traversal: "
                                f"{declared_total} -> {total_records}"
                            )
                        if declared_pages is None:
                            declared_pages = page_count
                            declared_total = total_records
                            if (
                                declared_pages is not None
                                and self.max_pages is not None
                                and declared_pages > self.max_pages
                            ):
                                termination_facts = {
                                    "requested_max_pages": self.max_pages,
                                    "effective_max_pages": declared_pages,
                                    "max_pages_overridden": True,
                                }
                                staged_summary.list_page_observations.append(
                                    {
                                        "status": "max_pages_overridden",
                                        "query_id": query_id,
                                        "requested_max_pages": self.max_pages,
                                        "declared_total_pages": declared_pages,
                                        "reason": "authoritative_declared_pages_require_complete_discovery",
                                    }
                                )
                                self.logger.warning(
                                    "SSE deal discovery max_pages=%s is below authoritative "
                                    "declared_pages=%s; continuing to complete discovery",
                                    self.max_pages,
                                    declared_pages,
                                )
                        query_evidence.complete_page(
                            page_index=page_index,
                            extracted_row_count=len(rows),
                            row_identity_values=(_deal_list_row_identity(row) for row in rows),
                            declared_total_items=total_records,
                            declared_total_pages=(
                                declared_pages if declared_pages and declared_pages > 0 else None
                            ),
                        )
                    except DiscoveryEvidenceError as exc:
                        query_evidence.fail(
                            termination_reason="evidence_failed",
                            details={"page_index": page_index, "error": str(exc)},
                        )
                        staged_summary.typed_errors.append(
                            list_failed_error(
                                source_id="sse",
                                task_id=task_id,
                                raw_reason=f"list-page-{page_index}-evidence-failed: {exc}",
                            )
                        )
                        break
                    except (_SseDealListStructureError, RuntimeError, ValueError) as exc:
                        query_evidence.fail_page(
                            page_index=page_index,
                            reason="response_invalid",
                            details={"error_type": type(exc).__name__, "error": str(exc)},
                        )
                        query_evidence.fail(
                            termination_reason="response_invalid",
                            details={"page_index": page_index, "error": str(exc)},
                        )
                        staged_summary.typed_errors.append(
                            list_failed_error(
                                source_id="sse",
                                task_id=task_id,
                                raw_reason=f"list-page-{page_index}-invalid-data: {exc}",
                            )
                        )
                        break

                    self._rows_to_candidates(
                        rows=rows,
                        output_dir=output_dir,
                        start=start,
                        end=end,
                        summary=staged_summary,
                        seen_notice_ids=seen_notice_ids,
                        candidates=staged_candidates,
                    )
                    effective_pages = max(1, declared_pages) if declared_pages is not None else None
                    if effective_pages is not None and page_index >= effective_pages:
                        termination_reason = (
                            "official_empty"
                            if declared_total == 0
                            else "declared_pages_exhausted"
                        )
                        query_evidence.complete(
                            termination_reason=termination_reason,
                            termination_facts=termination_facts,
                        )
                        query_succeeded = True
                        break
                    if effective_pages is None:
                        if not rows:
                            termination_reason = "official_empty"
                            query_evidence.complete(
                                termination_reason=termination_reason,
                                termination_facts=termination_facts,
                            )
                            query_succeeded = True
                            break
                        if len(rows) < self.page_size:
                            termination_reason = "short_page"
                            query_evidence.complete(
                                termination_reason=termination_reason,
                                termination_facts=termination_facts,
                            )
                            query_succeeded = True
                            break
                        query_evidence.fail(
                            termination_reason="undeclared_pagination",
                            details={"page_index": page_index, "page_size": self.page_size},
                        )
                        staged_summary.typed_errors.append(
                            list_failed_error(
                                source_id="sse",
                                task_id=task_id,
                                raw_reason=(
                                    f"list-page-{page_index}-full-page-without-authoritative-total"
                                ),
                            )
                        )
                        break
                    page_index += 1

            if query_succeeded:
                try:
                    task_evidence.complete(candidate_entries=staged_summary.candidate_entries)
                except DiscoveryEvidenceError as exc:
                    query_succeeded = False
                    staged_summary.typed_errors.append(
                        list_failed_error(
                            source_id="sse",
                            task_id=task_id,
                            raw_reason=f"deal-discovery-task-incomplete: {exc}",
                        )
                    )
            else:
                task_evidence.fail(termination_reason="query_failed")

        try:
            summary.discovery_task_manifest = task_evidence.manifest_reference()
        except DiscoveryEvidenceError:
            summary.discovery_task_manifest = None

        # Preserve request/error diagnostics on failure, but only commit rows
        # after the complete query evidence has been finalized.
        summary.pages_requested += staged_summary.pages_requested
        summary.typed_errors.extend(staged_summary.typed_errors)
        if not query_succeeded:
            return

        for field_name in (
            "listed_items",
            "detail_fetched",
            "saved",
            "skipped_by_list_date",
            "skipped_by_detail_date",
            "date_missing_skipped",
            "skipped_by_resume",
            "skipped_by_duplicate",
            "skipped_by_business_filter",
            "skipped_by_missing_xmid",
            "skipped_by_detail_unavailable",
            "detail_candidates",
            "detail_failed",
            "list_unaccounted",
            "detail_unaccounted",
        ):
            setattr(summary, field_name, getattr(summary, field_name) + getattr(staged_summary, field_name))
        summary.candidate_dates.extend(staged_summary.candidate_dates)
        summary.candidate_entries.extend(staged_summary.candidate_entries)
        summary.list_page_observations.extend(staged_summary.list_page_observations)
        candidates.extend(staged_candidates)

    @staticmethod
    def _coerce_list_response(response: object) -> HttpFetchedText:
        if isinstance(response, HttpFetchedText):
            return response
        if isinstance(response, Mapping):
            raw_bytes = json.dumps(
                dict(response),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            return HttpFetchedText(
                raw_bytes.decode("utf-8"),
                source_url=f"{BASE_URL}/si/notice/getDealNoticeList",
                final_url=f"{BASE_URL}/si/notice/getDealNoticeList",
                http_status=200,
                raw_bytes=raw_bytes,
            )
        raise TypeError(
            "SSE deal list transport must return HttpFetchedText or a mapping fixture"
        )

    @staticmethod
    def _decode_list_response(response: HttpFetchedText) -> Dict[str, Any]:
        raw_bytes = response.raw_bytes
        if not isinstance(raw_bytes, bytes):
            raise _SseDealListStructureError(
                "SSE deal list response requires original response bytes"
            )
        try:
            payload = json.loads(raw_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _SseDealListStructureError(f"invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise _SseDealListStructureError("SSE deal list response root must be an object")
        return payload

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
            xmid = _extract_xmid(normalized_row)
            notice_id = _extract_notice_id(normalized_row) or xmid
            if not notice_id:
                summary.skipped_by_missing_xmid += 1
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="sse",
                        task_id=_task_id(self.business_id),
                        raw_reason="missing-notice-id",
                    )
                )
                continue
            if notice_id in seen_notice_ids:
                summary.skipped_by_duplicate += 1
                continue
            seen_notice_ids.add(notice_id)

            project_code = _extract_project_code(normalized_row)
            if not project_code:
                summary.skipped_by_missing_xmid += 1
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="sse",
                        task_id=_task_id(self.business_id),
                        raw_reason="list-missing-project-code",
                    )
                )
                continue
            project_name = _extract_project_name(normalized_row)
            resolved_xmid = xmid or notice_id
            api_url = self._resolve_detail_url(xmid=resolved_xmid, notice_id=notice_id, row=normalized_row)
            source_url = self._resolve_page_url(xmid=resolved_xmid, row=normalized_row)
            resolved = self._resolve_deal_date(
                row=normalized_row,
                preferred_field=self._preferred_date_field(),
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
                project_code=project_code,
                project_name=project_name,
                notice_id=notice_id,
                xmid=resolved_xmid,
                api_url=api_url,
            )
            candidate = _DealCandidate(
                notice_id=notice_id,
                xmid=resolved_xmid,
                project_code=project_code,
                project_name=project_name,
                source_url=source_url,
                api_url=api_url,
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
                        source_id="sse",
                        task_id=_task_id(self.business_id),
                        raw_reason=f"prefetched-entry-{index}-invalid-format",
                    )
                )
                continue
            summary.listed_items += 1
            entry = dict(raw)
            notice_id = str(entry.get("notice_id") or entry.get("ggid") or "").strip()
            xmid = str(entry.get("xmid") or entry.get("XMID") or "").strip()
            row = entry.get("row") if isinstance(entry.get("row"), dict) else {}
            if not notice_id:
                notice_id = _extract_notice_id(row)
            if not xmid:
                xmid = _extract_xmid(row)
            if not notice_id:
                notice_id = xmid
            if not xmid:
                xmid = notice_id
            if not notice_id:
                summary.skipped_by_missing_xmid += 1
                continue
            if notice_id in seen_notice_ids:
                summary.skipped_by_duplicate += 1
                continue
            seen_notice_ids.add(notice_id)

            project_code = str(entry.get("project_code") or "").strip().upper() or _extract_project_code(row)
            if not project_code:
                summary.skipped_by_missing_xmid += 1
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="sse",
                        task_id=_task_id(self.business_id),
                        raw_reason=f"prefetched-entry-{index}-missing-project-code",
                    )
                )
                continue
            project_name = str(entry.get("project_name") or "").strip() or _extract_project_name(row)
            raw_source_url = str(entry.get("source_url") or "").strip()
            api_url = str(entry.get("api_url") or entry.get("detail_api_url") or "").strip()
            if raw_source_url and self.manifest_detail_route in raw_source_url:
                api_url = api_url or raw_source_url
                raw_source_url = ""
            api_url = api_url or self._resolve_detail_url(xmid=xmid, notice_id=notice_id, row=row)
            source_url = str(entry.get("page_url") or raw_source_url or "").strip() or self._resolve_page_url(
                xmid=xmid,
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
                    resolved = self._resolve_deal_date(
                        row=row,
                        preferred_field=self._preferred_date_field(),
                    )
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
                project_code=project_code,
                project_name=project_name,
                notice_id=notice_id,
                xmid=xmid,
                api_url=api_url,
            )
            candidate = _DealCandidate(
                notice_id=notice_id,
                xmid=xmid,
                project_code=project_code,
                project_name=project_name,
                source_url=source_url,
                api_url=api_url,
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
        deal_date: Optional[dt.date],
        collection_date: dt.date,
        deal_date_basis: str,
        deal_date_is_imputed: bool,
        project_code: str | None = None,
        project_name: str | None = None,
        notice_id: str | None = None,
        xmid: str | None = None,
        api_url: str | None = None,
    ) -> Dict[str, Any]:
        collection_date_text = collection_date.isoformat()
        project_code_value = project_code or _extract_project_code(row)
        project_name_value = project_name or _extract_project_name(row)
        notice_id_value = notice_id or _extract_notice_id(row)
        xmid_value = xmid or _extract_xmid(row) or notice_id_value
        remark_suffix = IMPUTED_REMARK_SUFFIX if deal_date_is_imputed else ""
        metadata: Dict[str, Any] = {
            "task_id": _task_id(self.business_id),
            "record_family": "deal",
            "business_id": self.business_id,
            "business_label": self.business_label,
            "source_id": "sse",
            "source_url": source_url,
            "api_url": str(api_url or "").strip(),
            "detail_api_url": str(api_url or "").strip(),
            "collection_date": collection_date_text,
            "deal_date": deal_date.isoformat() if deal_date is not None else "",
            "deal_date_basis": deal_date_basis,
            "deal_date_is_imputed": bool(deal_date_is_imputed),
            "remark_suffix": remark_suffix,
            "deal_date_remark_suffix": remark_suffix,
            "project_code": project_code_value,
            "project_name": project_name_value,
            "notice_id": notice_id_value,
            "xmid": xmid_value,
            "row": row,
        }
        if row.get("CJRQ") is not None:
            metadata["CJRQ"] = str(row.get("CJRQ"))
        return metadata

    def _resolve_deal_date(self, *, row: Dict[str, Any], preferred_field: str) -> Dict[str, Any]:
        collection_date = self._collection_date()
        preferred_raw = row.get(preferred_field)
        preferred_date = parse_loose_date(preferred_raw)
        if preferred_date is not None:
            return {
                "deal_date": preferred_date,
                "collection_date": collection_date,
                "effective_date": preferred_date,
                "deal_date_basis": preferred_field,
                "deal_date_is_imputed": False,
            }
        for key in ("deal_date", "dealDate", "cjrq"):
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

    def _resolve_detail_url(self, *, xmid: str, notice_id: str, row: Dict[str, Any]) -> str:
        for key in ("DETAIL_URL", "detailUrl", "url", "href"):
            value = str(row.get(key) or "").strip()
            if not value:
                continue
            return urllib.parse.urljoin(BASE_URL, value)
        query = urllib.parse.urlencode({"XMID": xmid or notice_id})
        return f"{self._detail_api_url()}?{query}"

    def _resolve_page_url(self, *, xmid: str, row: Dict[str, Any]) -> str:
        fclass = str(row.get("FCLASS") or row.get("fclass") or self.fclass or "").strip()
        if not fclass:
            fclass = self.fclass
        query = urllib.parse.urlencode(
            {
                "ID": str(xmid or "").strip(),
                "FCLASS": f"cjgg{fclass}",
                "skipDateCheck": "1",
            }
        )
        return f"{BASE_URL}/jyxx.html#/xxggDetail?{query}"

    def _query_list_page(self, *, page_index: int) -> HttpFetchedText:
        payload = {
            "pageNo": int(page_index),
            "pageSize": self.page_size,
            "FCLASS": self.fclass,
            "XMLX": "",
            "XMBH": "",
            "XMMC": "",
        }
        request = urllib.request.Request(
            url=self._list_api_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers=REQUEST_HEADERS,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw_bytes = response.read()
            headers = getattr(response, "headers", None)
            charset_getter = getattr(headers, "get_content_charset", None)
            charset = charset_getter() if callable(charset_getter) else None
            text = raw_bytes.decode(charset or "utf-8", errors="replace")
            final_url_getter = getattr(response, "geturl", None)
            final_url = (
                str(final_url_getter())
                if callable(final_url_getter)
                else self._list_api_url()
            )
            status = int(
                getattr(response, "status", None)
                or getattr(response, "getcode", lambda: 200)()
            )
            return HttpFetchedText(
                text,
                source_url=self._list_api_url(),
                final_url=final_url,
                http_status=status,
                raw_bytes=raw_bytes,
            )

    def _query_detail_payload(self, *, xmid: str) -> Dict[str, Any]:
        payload = {
            "XMID": str(xmid or "").strip(),
            "FCLASS": self.fclass,
            "skipDateCheck": True,
        }
        request = urllib.request.Request(
            url=self._detail_api_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers=REQUEST_HEADERS,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

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
            detail_payload = self._query_detail_payload(xmid=candidate.xmid)
            summary.detail_fetched += 1
        except Exception as exc:  # noqa: BLE001
            summary.detail_failed += 1
            summary.typed_errors.append(
                execute_failed_error(
                    source_id="sse",
                    task_id=_task_id(self.business_id),
                    raw_reason=f"detail-fetch-failed notice_id={candidate.notice_id}: {exc}",
                )
            )
            return

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
                page_url=candidate.source_url,
                expected_project_code=candidate.project_code,
                expected_project_name=candidate.project_name,
            )
        except Exception as exc:  # noqa: BLE001
            summary.detail_failed += 1
            summary.typed_errors.append(
                execute_failed_error(
                    source_id="sse",
                    task_id=_task_id(self.business_id),
                    raw_reason=f"rendered-page-fetch-failed notice_id={candidate.notice_id}: {exc}",
                )
            )
            return

        if _SSE_DEAL_NOTICE_SHELL_MARKER in str(rendered_html or ""):
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
                        source_id="sse",
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
                        source_id="sse",
                        task_id=_task_id(self.business_id),
                        raw_reason=f"invalid-detail-shell notice_id={candidate.notice_id}",
                    )
                )
            summary.detail_failed += 1
            return

        sidecar_written = False
        if not reserve_download_target(
            summary,
            html_root=self.html_root,
            html_path=candidate.html_path,
            source_id="sse",
            task_id=_task_id(self.business_id),
        ):
            summary.detail_failed += 1
            return
        try:
            self._save_complete_page(
                rendered_html=rendered_html,
                page_url=rendered_html.final_url,
                html_path=candidate.html_path,
            )
            self._write_invalid_shell_evidence_if_needed(
                rendered_html=rendered_html,
                source_url=rendered_html.source_url,
                final_url=rendered_html.final_url,
                html_path=candidate.html_path,
                metadata=candidate.metadata,
            )
            json_path = os.path.splitext(candidate.html_path)[0] + ".json"
            self._write_sidecar_json(
                json_path=json_path,
                metadata=candidate.metadata,
                detail_payload=detail_payload,
                source_url=rendered_html.source_url,
                final_url=rendered_html.final_url,
                http_status=rendered_html.http_status,
                save_status="pending",
            )
            sidecar_written = True
            self._write_sidecar_json(
                json_path=json_path,
                metadata=candidate.metadata,
                detail_payload=detail_payload,
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
                                "source_id": "sse",
                                "record_family": "deal",
                                "business_id": self.business_id,
                            },
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
                    source_id="sse",
                    task_id=_task_id(self.business_id),
                    raw_reason=f"save-failed notice_id={candidate.notice_id}: {exc}{cleanup_reason}",
                )
            )
            return

        summary.saved += 1
        record_downloaded_target(summary, html_root=self.html_root, html_path=candidate.html_path)

    @staticmethod
    def _extract_detail_deal_date(payload: Dict[str, Any]) -> Optional[dt.date]:
        if not isinstance(payload, dict):
            return None
        for key in ("CJRQ", "dealDate", "deal_date", "contractSignTime"):
            parsed = parse_loose_date(payload.get(key))
            if parsed is not None:
                return parsed
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("CJRQ", "dealDate", "deal_date", "contractSignTime"):
                parsed = parse_loose_date(data.get(key))
                if parsed is not None:
                    return parsed
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                for key in ("CJRQ", "dealDate", "deal_date", "contractSignTime"):
                    parsed = parse_loose_date(item.get(key))
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
            context = await browser.new_context()
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
    ) -> HttpFetchedText:
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
            "sse-deal-page-not-ready: "
            f"expected_project_code={expected_project_code} page_url={page_url} html_len={len(last_html)}"
        )

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
        if expected_code and expected_code not in normalized_text:
            return False
        expected_name = cls._normalize_html_text(expected_project_name)
        if expected_name and expected_name not in normalized_text:
            return False
        return "成交公告" in normalized_text and "成交日期" in normalized_text

    def _save_complete_page(self, *, rendered_html: str, page_url: str, html_path: str) -> None:
        final_assets_dir = f"{os.path.splitext(html_path)[0]}_files"
        temp_html_path = f"{html_path}.part"

        if os.path.isfile(temp_html_path):
            os.remove(temp_html_path)

        try:
            os.makedirs(os.path.dirname(html_path), exist_ok=True)

            with open(temp_html_path, "w", encoding="utf-8") as handle:
                handle.write(rendered_html)

            if os.path.isdir(final_assets_dir):
                shutil.rmtree(final_assets_dir)
            if os.path.isfile(html_path):
                os.remove(html_path)
            os.replace(temp_html_path, html_path)
        except Exception:
            if os.path.isfile(temp_html_path):
                with contextlib.suppress(OSError):
                    os.remove(temp_html_path)
            raise

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
        if _SSE_DEAL_NOTICE_SHELL_MARKER not in str(rendered_html or ""):
            return
        evidence_path = f"{html_path}.peap-evidence.json"
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
        with open(evidence_path, "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, ensure_ascii=False, indent=2, sort_keys=True)

    def _download_asset(
        self,
        *,
        raw_url: str,
        base_url: str,
        assets_dir: str,
        downloaded_by_url: Dict[str, str],
        source_url_by_local: Dict[str, str],
    ) -> Optional[str]:
        value = str(raw_url or "").strip().strip("'\"")
        if _is_skip_asset_url(value):
            return None
        absolute_url = urllib.parse.urljoin(base_url, value)
        parsed = urllib.parse.urlparse(absolute_url)
        if parsed.scheme not in {"http", "https"}:
            return None
        if absolute_url in downloaded_by_url:
            return downloaded_by_url[absolute_url]

        request = urllib.request.Request(absolute_url, headers={"User-Agent": REQUEST_HEADERS["User-Agent"]})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content = response.read()
                content_type = response.headers.get("Content-Type", "")
        except Exception:
            return None

        basename = re.sub(r"[\\/:*?\"<>|]+", "_", os.path.basename(parsed.path))
        if not basename:
            digest = hashlib.md5(absolute_url.encode("utf-8")).hexdigest()[:12]
            basename = f"asset_{digest}"
        if not os.path.splitext(basename)[1]:
            guessed = _guess_ext_from_content_type(content_type)
            if guessed:
                basename = f"{basename}{guessed}"

        final_name = basename
        counter = 1
        while os.path.exists(os.path.join(assets_dir, final_name)):
            name_root, name_ext = os.path.splitext(basename)
            final_name = f"{name_root}__{counter}{name_ext}"
            counter += 1
        with open(os.path.join(assets_dir, final_name), "wb") as handle:
            handle.write(content)
        downloaded_by_url[absolute_url] = final_name
        source_url_by_local[final_name] = absolute_url
        return final_name

    def _rewrite_css_assets(
        self,
        *,
        css_path: str,
        css_source_url: str,
        assets_dir: str,
        downloaded_by_url: Dict[str, str],
        source_url_by_local: Dict[str, str],
    ) -> None:
        try:
            raw = open(css_path, "rb").read()
        except OSError:
            return
        for encoding in ("utf-8", "gb18030", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            return

        def replace(match: re.Match[str]) -> str:
            inside = match.group(1).strip().strip("'\"")
            if _is_skip_asset_url(inside):
                return match.group(0)
            local_name = self._download_asset(
                raw_url=inside,
                base_url=css_source_url,
                assets_dir=assets_dir,
                downloaded_by_url=downloaded_by_url,
                source_url_by_local=source_url_by_local,
            )
            return f"url('{local_name}')" if local_name else match.group(0)

        updated = re.sub(r"url\(([^)]+)\)", replace, text)
        if updated != text:
            with open(css_path, "w", encoding=encoding, errors="ignore") as handle:
                handle.write(updated)

    @staticmethod
    def _write_sidecar_json(
        *,
        json_path: str,
        metadata: Dict[str, Any],
        detail_payload: Dict[str, Any],
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
                    "source_url": fetched.source_url,
                    "final_url": fetched.final_url,
                    "http_status": fetched.http_status,
                    "metadata": metadata,
                    "detail_payload": detail_payload,
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
                "source_id": "sse",
                "business_id": self.business_id,
                "row": candidate.row,
            }
        )


class ShanghaiDealEquityTransferDownloader(SseDealDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            business_id="deal_equity_transfer",
            fclass="GQ",
            **kwargs,
        )


class ShanghaiDealPhysicalAssetDownloader(SseDealDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            business_id="deal_physical_asset",
            fclass="SW",
            **kwargs,
        )


class ShanghaiDealCapitalIncreaseDownloader(SseDealDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            business_id="deal_capital_increase",
            fclass="1C",
            **kwargs,
        )
