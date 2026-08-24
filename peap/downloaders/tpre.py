"""Tianjin project-center downloader."""

from __future__ import annotations

import asyncio
import codecs
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
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
from .snapshot_utils import SnapshotSaver

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

TPRE_PHYSICAL_ASSET_PRICE_BEGIN = get_source_business_requirement(
    "tpre",
    "listing",
    "physical_asset",
).required_query_filters["priceBegin"]

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


class _ListResponseSchemaError(ValueError):
    pass


class _ListApiError(_ListResponseSchemaError):
    pass


def _list_integer(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise _ListResponseSchemaError(f"{field} must be an integer")
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise _ListResponseSchemaError(f"{field} must be an integer") from exc
    if number < minimum:
        raise _ListResponseSchemaError(f"{field} must be >= {minimum}")
    return number


def _list_row_identity(row: Mapping[str, Any]) -> str:
    for field_name in ("id", "projectId", "projectCode", "projectNo"):
        value = str(row.get(field_name) or "").strip()
        if value:
            return f"{field_name}:{value}"
    title_code = _extract_project_code(str(row.get("title") or row.get("projectName") or ""))
    if title_code:
        return f"projectCode:{title_code}"
    raise _ListResponseSchemaError("data.records entry lacks a stable project id/code")


def _decode_list_json_bytes(raw_bytes: bytes, charset_hint: object) -> tuple[str, str]:
    normalized_hint = str(charset_hint or "").strip().lower()
    if normalized_hint:
        try:
            codecs.lookup(normalized_hint)
        except LookupError as exc:
            return raw_bytes.decode("utf-8", errors="replace"), str(exc)

    tried: Set[str] = set()
    last_error: UnicodeDecodeError | None = None
    for candidate in (normalized_hint, "utf-8"):
        encoding = str(candidate or "").strip().lower()
        if not encoding or encoding in tried:
            continue
        tried.add(encoding)
        try:
            return raw_bytes.decode(encoding), ""
        except UnicodeDecodeError as exc:
            last_error = exc
    return raw_bytes.decode("utf-8", errors="replace"), str(last_error or "decode failed")


def _query_values(url: str) -> Dict[str, List[str]]:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    values: Dict[str, List[str]] = {}
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        values.setdefault(key, []).append(value)
    return values


def _same_http_endpoint(left: str, right: str) -> bool:
    left_url = urllib.parse.urlsplit(str(left or "").strip())
    right_url = urllib.parse.urlsplit(str(right or "").strip())
    return (
        left_url.scheme.lower() == right_url.scheme.lower()
        and left_url.netloc.lower() == right_url.netloc.lower()
        and (left_url.path or "/") == (right_url.path or "/")
        and not left_url.fragment
    )


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


def _tpre_list_endpoint(business_id: str = "equity_transfer") -> str:
    return get_source_business_requirement("tpre", "listing", business_id).list_endpoint


def _tpre_list_queries(business_id: str) -> List[_ListQuerySpec]:
    return [
        _ListQuerySpec(
            label=str(spec.get("label") or ""),
            system_code=str(spec["systemCode"]),
            biz_type_code=str(spec["bizTypeCode"]),
            extra_params={
                key: value
                for key, value in spec.items()
                if key not in {"label", "systemCode", "bizTypeCode"}
            },
        )
        for spec in get_source_business_requirement("tpre", "listing", business_id).list_query_specs
    ]


def _extract_project_code(text: str) -> str:
    raw = str(text or "").strip().upper()
    match = PROJECT_CODE_RE.search(raw)
    return match.group(1).upper() if match else ""


class TpreProjectDownloader:
    """Download Tianjin detail pages for parser ingestion."""

    manifest_list_endpoint = _tpre_list_endpoint()
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
        if list_queries is None:
            self.list_queries = []
        elif not isinstance(list_queries, list):
            raise TypeError("list_queries must be a list or None")
        else:
            self.list_queries = list(list_queries)
        self.logger = logger or logging.getLogger("parser_v2")
        self.item_saved_callback = item_saved_callback
        self.run_id = str(run_id or "").strip() or f"run-{int(time.time() * 1000)}"
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
        detail_accounted = detail_accounted_count(summary)
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
        business_id = business_id_key(self.output_type)
        authoritative_queries = _tpre_list_queries(business_id)
        task_id = runtime_task_id("tpre", self.output_type)
        expected_query_ids = tuple(
            f"{query_number:03d}-{query.label or 'listing'}"
            for query_number, query in enumerate(authoritative_queries, start=1)
        )
        task_evidence = DiscoveryTaskEvidence(
            root=output_dir,
            source_id="tpre",
            task_id=task_id,
            run_id=self.run_id,
            expected_query_ids=expected_query_ids,
        )
        if self.list_queries != authoritative_queries:
            raw_reason = (
                "authoritative-list-config-mismatch: "
                f"expected={len(authoritative_queries)} configured={len(self.list_queries)}"
            )
            summary.typed_errors.append(
                list_failed_error(
                    source_id="tpre",
                    task_id=task_id,
                    raw_reason=raw_reason,
                )
            )
            task_evidence.fail(
                termination_reason="authoritative_config_mismatch",
                details={
                    "expected_query_count": len(authoritative_queries),
                    "configured_query_count": len(self.list_queries),
                },
                missing_query_ids=expected_query_ids,
            )
            summary.discovery_task_manifest = task_evidence.manifest_reference()
            return

        staged_summary = DownloadSummary()
        staged_candidates: List[_DownloadCandidate] = []
        with task_evidence:
            try:
                pending_queries = self._collect_list_query_pages(
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
                        source_id="tpre",
                        task_id=task_id,
                        raw_reason=f"discovery-task-failed: {exc}",
                    )
                )
                summary.discovery_task_manifest = task_evidence.manifest_reference()
                return
            if pending_queries is None:
                task_evidence.fail(termination_reason="query_failed")
                summary.discovery_task_manifest = task_evidence.manifest_reference()
                return

            seen_codes: Set[str] = set()
            try:
                for query, query_rows in pending_queries:
                    for rows in query_rows:
                        self._rows_to_candidates(
                            rows=rows,
                            query=query,
                            output_dir=output_dir,
                            summary=staged_summary,
                            candidates=staged_candidates,
                            seen_codes=seen_codes,
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
                        source_id="tpre",
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
                        source_id="tpre",
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

    def _collect_list_query_pages(
        self,
        *,
        summary: DownloadSummary,
        task_evidence: DiscoveryTaskEvidence,
    ) -> Optional[List[tuple[_ListQuerySpec, List[List[Dict[str, Any]]]]]]:
        task_id = runtime_task_id("tpre", self.output_type)
        pending_queries: List[tuple[_ListQuerySpec, List[List[Dict[str, Any]]]]] = []
        all_queries_complete = True
        for query_number, query in enumerate(self.list_queries, start=1):
            page_index = 1
            query_rows: List[List[Dict[str, Any]]] = []
            query_complete = False
            evidence_enabled = True
            query_evidence = task_evidence.query(
                f"{query_number:03d}-{query.label or 'listing'}",
                authoritative_total=True,
                page_size=self.page_size,
            )
            with query_evidence:
                while True:
                    if self.max_pages is not None and page_index > self.max_pages:
                        raw_reason = (
                            f"list-{query.label}-page-{page_index}-max-pages-truncated: "
                            f"max_pages={self.max_pages}"
                        )
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id="tpre",
                                task_id=task_id,
                                raw_reason=raw_reason,
                            )
                        )
                        if evidence_enabled:
                            query_evidence.fail(
                                termination_reason="explicit_max_pages",
                                details={"max_pages": self.max_pages, "next_page": page_index},
                            )
                        break

                    try:
                        response = self._query_list_page(page_index=page_index, query=query)
                    except Exception as exc:  # noqa: BLE001
                        summary.pages_requested += 1
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id="tpre",
                                task_id=task_id,
                                raw_reason=(
                                    f"list-{query.label}-page-{page_index}-request-failed: {exc}"
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
                    is_transport_response = isinstance(response, HttpFetchedText)
                    if is_transport_response:
                        try:
                            query_evidence.capture_page(
                                page_index=page_index,
                                response=response,
                                body_format="json",
                                request_metadata=self._list_request_metadata(
                                    page_index=page_index,
                                    query=query,
                                ),
                            )
                        except DiscoveryEvidenceError as exc:
                            summary.typed_errors.append(
                                list_failed_error(
                                    source_id="tpre",
                                    task_id=task_id,
                                    raw_reason=(
                                        f"list-{query.label}-page-{page_index}-capture-failed: {exc}"
                                    ),
                                )
                            )
                            query_evidence.fail(
                                termination_reason="evidence_capture_failed",
                                details={"page_index": page_index, "error": str(exc)},
                            )
                            break
                        try:
                            self._validate_list_transport_urls(
                                response=response,
                                page_index=page_index,
                                query=query,
                            )
                        except _ListResponseSchemaError as exc:
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
                                    source_id="tpre",
                                    task_id=task_id,
                                    raw_reason=(
                                        f"list-{query.label}-page-{page_index}-url-invalid: "
                                        f"{exc}"
                                    ),
                                )
                            )
                            break

                        raw_bytes = response.raw_bytes
                        payload_source, decode_error = _decode_list_json_bytes(
                            raw_bytes,
                            getattr(response, "charset_hint", None),
                        )
                        decode_error = decode_error or str(
                            getattr(response, "decode_error", "") or ""
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
                                    source_id="tpre",
                                    task_id=task_id,
                                    raw_reason=(
                                        f"list-{query.label}-page-{page_index}-decode-failed: "
                                        f"{decode_error}"
                                    ),
                                )
                            )
                            break
                    else:
                        # Compatibility for unit-injected decoded payloads. Real fetches always
                        # return HttpFetchedText and therefore take the evidence path above.
                        if evidence_enabled:
                            query_evidence.fail(
                                termination_reason="transport_evidence_unavailable",
                                details={"page_index": page_index},
                            )
                            evidence_enabled = False
                        payload_source = response

                    try:
                        rows, declared_total, declared_pages = self._parse_list_response(
                            payload_source,
                            page_index=page_index,
                        )
                    except _ListResponseSchemaError as exc:
                        if evidence_enabled:
                            query_evidence.fail_page(
                                page_index=page_index,
                                reason="response_schema_invalid",
                                details={"error": str(exc)},
                            )
                            query_evidence.fail(
                                termination_reason="response_invalid",
                                details={"page_index": page_index, "error": str(exc)},
                            )
                        error_factory = list_failed_error if isinstance(exc, _ListApiError) else invalid_candidate_error
                        summary.typed_errors.append(
                            error_factory(
                                source_id="tpre",
                                task_id=task_id,
                                raw_reason=(
                                    f"list-{query.label}-page-{page_index}-invalid-data: {exc}"
                                ),
                            )
                        )
                        break

                    if evidence_enabled:
                        try:
                            query_evidence.complete_page(
                                page_index=page_index,
                                extracted_row_count=len(rows),
                                row_identity_values=(_list_row_identity(row) for row in rows),
                                declared_total_items=declared_total,
                                declared_total_pages=declared_pages,
                            )
                        except DiscoveryEvidenceError as exc:
                            query_evidence.fail(
                                termination_reason="repeated_page",
                                details={"page_index": page_index, "error": str(exc)},
                            )
                            summary.typed_errors.append(
                                list_failed_error(
                                    source_id="tpre",
                                    task_id=task_id,
                                    raw_reason=(
                                        f"list-{query.label}-page-{page_index}-repeated-page: {exc}"
                                    ),
                                )
                            )
                            break

                    query_rows.append(rows)
                    if page_index >= declared_pages:
                        termination_reason = (
                            "official_empty" if declared_total == 0 else "declared_pages_exhausted"
                        )
                        if evidence_enabled:
                            try:
                                query_evidence.complete(
                                    termination_reason=termination_reason,
                                )
                            except DiscoveryEvidenceError as exc:
                                summary.typed_errors.append(
                                    list_failed_error(
                                        source_id="tpre",
                                        task_id=task_id,
                                        raw_reason=(
                                            f"list-{query.label}-coverage-failed: {exc}"
                                        ),
                                    )
                                )
                                break
                        query_complete = True
                        break
                    if not rows:
                        raw_reason = (
                            f"list-{query.label}-page-{page_index}-empty-before-declared-end"
                        )
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id="tpre",
                                task_id=task_id,
                                raw_reason=raw_reason,
                            )
                        )
                        if evidence_enabled:
                            query_evidence.fail(
                                termination_reason="empty_page_before_declared_end",
                                details={"page_index": page_index},
                            )
                        break
                    page_index += 1

            if not query_complete:
                all_queries_complete = False
                continue
            pending_queries.append((query, query_rows))
        return pending_queries if all_queries_complete else None

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
                        task_id=runtime_task_id("tpre", self.output_type),
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
                        task_id=runtime_task_id("tpre", self.output_type),
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
            business_id = business_id_key(self.output_type)
            if self.resume and _is_resume_complete(
                html_path,
                save_json=self.save_json,
                task_id=runtime_task_id("tpre", self.output_type),
                source_id="tpre",
                business_id=business_id,
            ):
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
                    "disclosure_start": list_disclosure_start.isoformat()
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
                        task_id=runtime_task_id("tpre", self.output_type),
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
                        task_id=runtime_task_id("tpre", self.output_type),
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
            list_disclosure_start = parse_loose_date(raw.get("disclosure_start") or row.get("startTime"))
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
                        task_id=runtime_task_id("tpre", self.output_type),
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
            business_id = business_id_key(self.output_type)
            if self.resume and _is_resume_complete(
                html_path,
                save_json=self.save_json,
                task_id=runtime_task_id("tpre", self.output_type),
                source_id="tpre",
                business_id=business_id,
            ):
                summary.skipped_by_resume += 1
                continue

            row_with_source = dict(row)
            row_with_source["disclosure_start"] = (
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
                    "disclosure_start": list_disclosure_start.isoformat()
                    if list_disclosure_start
                    else None,
                }
            )
            if list_disclosure_start:
                summary.candidate_dates.append(list_disclosure_start.isoformat())

    def _list_request_metadata(
        self,
        *,
        page_index: int,
        query: _ListQuerySpec,
    ) -> Dict[str, Any]:
        return {
            "method": "GET",
            "params": {
                "current": int(page_index),
                "size": self.page_size,
                "systemCode": query.system_code,
                "bizTypeCode": query.biz_type_code,
                **query.extra_params,
            },
        }

    def _validate_list_transport_urls(
        self,
        *,
        response: HttpFetchedText,
        page_index: int,
        query: _ListQuerySpec,
    ) -> None:
        endpoint = _tpre_list_endpoint(business_id_key(self.output_type))
        expected_params = self._list_request_metadata(
            page_index=page_index,
            query=query,
        )["params"]
        expected_values = {key: [str(value)] for key, value in expected_params.items()}
        for field_name, url, allow_extra in (
            ("source_url", response.source_url, False),
            ("final_url", response.final_url, True),
        ):
            if not _same_http_endpoint(url, endpoint):
                raise _ListResponseSchemaError(
                    f"{field_name} does not use authoritative endpoint"
                )
            actual_values = _query_values(url)
            for key, values in expected_values.items():
                if actual_values.get(key) != values:
                    raise _ListResponseSchemaError(
                        f"{field_name} query {key!r} does not match request"
                    )
            if not allow_extra and set(actual_values) != set(expected_values):
                raise _ListResponseSchemaError(
                    f"{field_name} query contains non-authoritative parameters"
                )

    def _parse_list_response(
        self,
        response: object,
        *,
        page_index: int,
    ) -> tuple[List[Dict[str, Any]], int, int]:
        if isinstance(response, Mapping):
            payload: object = dict(response)
        elif isinstance(response, str):
            try:
                payload = json.loads(response)
            except json.JSONDecodeError as exc:
                raise _ListResponseSchemaError(f"invalid-json: {exc}") from exc
        else:
            raise _ListResponseSchemaError(
                f"response root must be an object, got {type(response).__name__}"
            )
        if not isinstance(payload, dict):
            raise _ListResponseSchemaError("response root must be an object")

        code = _list_integer(payload.get("code"), field="code")
        if code != 0:
            raise _ListApiError(
                f"api code={code} message={str(payload.get('message') or '').strip()!r}"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise _ListResponseSchemaError("data must be an object")
        if "records" not in data or not isinstance(data.get("records"), list):
            raise _ListResponseSchemaError("data.records must be a list")
        raw_rows = data["records"]
        if any(not isinstance(row, dict) for row in raw_rows):
            raise _ListResponseSchemaError("every data.records entry must be an object")
        rows: List[Dict[str, Any]] = list(raw_rows)
        for row in rows:
            _list_row_identity(row)

        for field_name in ("current", "size", "pages", "total"):
            if field_name not in data or data[field_name] is None:
                raise _ListResponseSchemaError(f"data.{field_name} is required")
        declared_total = _list_integer(data["total"], field="data.total")
        response_page_size = _list_integer(
            data["size"],
            field="data.size",
            minimum=1,
        )
        if response_page_size != self.page_size:
            raise _ListResponseSchemaError(
                f"data.size={response_page_size} does not match requested size={self.page_size}"
            )
        calculated_pages = max(1, math.ceil(declared_total / response_page_size))
        raw_declared_pages = _list_integer(data["pages"], field="data.pages")
        if declared_total == 0 and raw_declared_pages == 0:
            declared_pages = 1
        else:
            declared_pages = raw_declared_pages
            if declared_pages < 1:
                raise _ListResponseSchemaError("data.pages must be positive")
            if declared_pages != calculated_pages:
                raise _ListResponseSchemaError(
                    "data.pages is inconsistent with data.total/data.size: "
                    f"pages={declared_pages} calculated={calculated_pages}"
                )
        current = _list_integer(data["current"], field="data.current", minimum=1)
        if current != page_index:
            raise _ListResponseSchemaError(
                f"data.current={current} does not match requested page={page_index}"
            )
        if page_index > declared_pages:
            raise _ListResponseSchemaError(
                f"requested page={page_index} exceeds declared pages={declared_pages}"
            )
        if len(rows) > response_page_size:
            raise _ListResponseSchemaError(
                f"records exceed page size: rows={len(rows)} size={response_page_size}"
            )
        return rows, declared_total, declared_pages

    def _query_list_page(self, *, page_index: int, query: _ListQuerySpec) -> HttpFetchedText:
        params = {
            "current": int(page_index),
            "size": self.page_size,
            "systemCode": query.system_code,
            "bizTypeCode": query.biz_type_code,
            **query.extra_params,
        }
        endpoint = _tpre_list_endpoint(business_id_key(self.output_type))
        url = f"{endpoint}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url=url, headers=REQUEST_HEADERS, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw_bytes = response.read()
                headers = getattr(response, "headers", None)
                charset = (
                    headers.get_content_charset()
                    if headers is not None and hasattr(headers, "get_content_charset")
                    else None
                )
                http_status = getattr(response, "status", None)
                if http_status is None and hasattr(response, "getcode"):
                    http_status = response.getcode()
                final_url = response.geturl() if hasattr(response, "geturl") else url
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GET {url} failed: {exc}") from exc
        text, decode_error = _decode_list_json_bytes(raw_bytes, charset)
        fetched = HttpFetchedText(
            text,
            source_url=url,
            final_url=final_url,
            http_status=http_status,
            raw_bytes=raw_bytes,
        )
        if decode_error:
            fetched.decode_error = decode_error
        fetched.charset_hint = charset
        return fetched

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
            browser = await launch_chromium_browser(pw, headless=True)
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
        http_status: Optional[int] = None
        last_exc: Optional[Exception] = None
        for attempt in range(1, self._detail_retries + 2):
            page = await context.new_page()
            try:
                rendered_html, http_status = await self._fetch_rendered_html(
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
                            task_id=runtime_task_id("tpre", self.output_type),
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
                            task_id=runtime_task_id("tpre", self.output_type),
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
        list_start = parse_loose_date(candidate.row.get("disclosure_start") or candidate.row.get("startTime"))
        final_date = disclosure_start if disclosure_start is not None else list_start
        if start or end:
            if final_date is None:
                summary.date_missing_skipped += 1
                summary.skipped_by_detail_date += 1
                return
            if not in_date_range(final_date, start, end):
                summary.skipped_by_detail_date += 1
                return
        task_id = runtime_task_id("tpre", self.output_type)
        if not reserve_download_target(
            summary,
            html_root=self.html_root,
            html_path=candidate.html_path,
            source_id="tpre",
            task_id=task_id,
        ):
            summary.detail_failed += 1
            return

        try:
            self._save_complete_page(
                rendered_html=rendered_html,
                page_url=candidate.page_url,
                html_path=candidate.html_path,
            )
            if self.save_json:
                detail_payload = {
                    "task_id": task_id,
                    "source_id": "tpre",
                    "record_family": "listing",
                    "business_id": business_id_key(self.output_type),
                    "project_code": candidate.project_code,
                    "project_name": candidate.project_name,
                    "page_url": candidate.page_url,
                    **successful_http_evidence(
                        source_url=candidate.page_url,
                        http_status=http_status,
                    ),
                    "list_row": candidate.row,
                    "disclosure_start_date": disclosure_start.isoformat() if disclosure_start else None,
                }
                self._write_json(
                    json_path=os.path.splitext(candidate.html_path)[0] + ".json",
                    payload={**detail_payload, "save_status": "pending"},
                )
            else:
                self._write_resume_status(
                    candidate.html_path,
                    "pending",
                    source_url=candidate.page_url,
                    http_status=http_status,
                )
            if self.save_json:
                self._write_json(
                    json_path=os.path.splitext(candidate.html_path)[0] + ".json",
                    payload={
                        **detail_payload,
                        "save_status": "complete",
                        **archive_integrity_fields(candidate.html_path),
                    },
                )
            else:
                self._write_resume_status(
                    candidate.html_path,
                    "complete",
                    source_url=candidate.page_url,
                    http_status=http_status,
                )
            self._notify_item_saved(candidate=candidate, disclosure_start=disclosure_start)
        except Exception as exc:  # noqa: BLE001
            mark_artifact_save_failed(
                html_path=candidate.html_path,
                save_json=self.save_json,
                write_json=lambda json_path, payload: self._write_json(
                    json_path=json_path,
                    payload=payload,
                ),
                failure_identity={
                    "task_id": runtime_task_id("tpre", self.output_type),
                    "source_id": "tpre",
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
                    source_id="tpre",
                    task_id=runtime_task_id("tpre", self.output_type),
                    raw_reason=str(exc),
                )
            )
            return

        summary.saved += 1
        record_downloaded_target(summary, html_root=self.html_root, html_path=candidate.html_path)

    async def _fetch_rendered_html(
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
        http_status = successful_http_evidence(
            source_url=page_url,
            http_status=getattr(response, "status", None),
        )["http_status"]
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
        return html, http_status

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

        project_code = _extract_project_code(saved_html) or _extract_project_code(rendered_html)
        identity_hints: Dict[str, str] = {}
        if project_code:
            identity_hints["project_code_hash"] = self._sha256_text(project_code)

        locator_hash = self._sha256_text(page_url)
        evidence = {
            "schema_version": 1,
            "page_kind": "invalid_shell",
            "source_url_hash": locator_hash,
            "final_url_hash": locator_hash,
            "content_sha256": self._sha256_text(saved_html),
            "identity_hints": identity_hints,
        }
        with open(f"{html_path}.peap-evidence.json", "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, ensure_ascii=False, indent=2, sort_keys=True)

    def _write_json(self, *, json_path: str, payload: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        temp_json_path = f"{json_path}.tmp"
        with open(temp_json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp_json_path, json_path)

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
            "task_id": runtime_task_id("tpre", self.output_type),
            "source_id": "tpre",
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

    def _notify_item_saved(self, *, candidate: _DownloadCandidate, disclosure_start: Optional[dt.date]) -> None:
        callback = self.item_saved_callback
        if callback is None:
            return
        callback(
            {
                "source_file": candidate.html_path,
                "page_url": candidate.page_url,
                "project_code": candidate.project_code,
                "project_name": candidate.project_name,
                "listing_date": disclosure_start.isoformat() if disclosure_start else "",
                "source_id": "tpre",
                "business_id": business_id_key(self.output_type),
                "row": candidate.row,
            }
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


class TianjinEquityTransferDownloader(TpreProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_EQUITY_TRANSFER,
            list_queries=_tpre_list_queries("equity_transfer"),
            **kwargs,
        )


class TianjinCapitalIncreaseDownloader(TpreProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_CAPITAL_INCREASE,
            list_queries=_tpre_list_queries("capital_increase"),
            **kwargs,
        )


class TianjinPhysicalAssetDownloader(TpreProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PHYSICAL_ASSET,
            list_queries=_tpre_list_queries("physical_asset"),
            **kwargs,
        )


class TianjinPreDisclosureDownloader(TpreProjectDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PRE_DISCLOSURE,
            list_queries=_tpre_list_queries("pre_disclosure"),
            **kwargs,
        )
