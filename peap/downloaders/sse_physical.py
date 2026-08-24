"""Downloader for Shanghai Exchange physical asset projects.

This downloader keeps real rendered detail pages and companion *_files folders,
instead of reconstructing HTML from API responses.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

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

LIST_API_URL = "https://www.suaee.com/si/prjs/realright/list"
DETAIL_PAGE_URL = "https://www.suaee.com/xmzx.html#/zczrDetail"
SSE_LIST_API_ORIGIN = "https://www.suaee.com/si"

REQUEST_HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "projectType": "suaeeHome",
    "sourcecode": "SUAEE",
    "User-Agent": "Mozilla/5.0",
}

DISCLOSURE_START_PATTERNS = (
    r"(?:信息披露起始日期|挂牌开始日期|挂牌起始日期|披露开始日期|披露起止日期|挂牌起止日期)\s*[:：]?\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)",
    r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)\s*(?:至|到|-|—|~|～)\s*20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?",
)

TAG_ASSET_ATTRS = (
    ("link", "href"),
    ("img", "src"),
    ("source", "src"),
    ("video", "poster"),
)

DETAIL_SHELL_PLACEHOLDERS = frozenset({"", "-", "--", "—", "暂无", "万元", "元"})
DETAIL_SHELL_LABELS = frozenset({"项目负责人", "项目联系人", "联系人", "交易机构", "业务负责人", "受托机构"})


class _SseListStructureError(ValueError):
    pass


@dataclass
class _DownloadCandidate:
    xmid: str
    project_code: str
    page_url: str
    html_path: str
    row: Dict[str, Any]


def _sse_list_endpoint(business_id: str) -> str:
    return get_source_business_requirement("sse", "listing", business_id).list_endpoint


def _sse_list_query_specs(business_id: str) -> List[Tuple[str, str]]:
    return [
        (str(spec["project_type"]), str(spec["gplx"]))
        for spec in get_source_business_requirement("sse", "listing", business_id).list_query_specs
    ]


def _sse_authoritative_query_spec(
    business_id: str,
    *,
    project_type: str,
    gplx: Optional[str] = None,
) -> Dict[str, object]:
    requirement = get_source_business_requirement("sse", "listing", business_id)
    matches = [
        dict(spec)
        for spec in requirement.list_query_specs
        if str(spec.get("project_type") or "") == str(project_type)
        and (gplx is None or str(spec.get("gplx") or "") == str(gplx))
    ]
    if len(matches) != 1:
        raise ValueError(
            "SSE authoritative query spec is not unique: "
            f"business_id={business_id} project_type={project_type} gplx={gplx}"
        )
    return matches[0]


def _sse_list_api_url(endpoint: object) -> str:
    normalized_endpoint = str(endpoint or "").strip()
    if not normalized_endpoint.startswith("/"):
        raise ValueError(f"invalid SSE list endpoint: {normalized_endpoint!r}")
    return f"{SSE_LIST_API_ORIGIN}{normalized_endpoint}"


def _url_matches_endpoint(url: str, expected_url: str) -> bool:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    expected = urllib.parse.urlsplit(expected_url)
    return (
        parsed.scheme.lower() == expected.scheme.lower()
        and parsed.netloc.lower() == expected.netloc.lower()
        and parsed.path == expected.path
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


def _is_cert_verify_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    if isinstance(exc, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(exc):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    if isinstance(reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(reason):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(exc)


def _read_sidecar_json_object(json_path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(json_path):
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _resume_status_path(html_path: str) -> str:
    return f"{html_path}.peap-save-status.json"


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


class ShanghaiPhysicalAssetDownloader:
    """Download Shanghai physical asset projects and save full rendered pages."""

    manifest_list_endpoint = _sse_list_endpoint("physical_asset")
    manifest_detail_route = "jymhzichan"
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
        output_type: str = TYPE_PHYSICAL_ASSET,
        list_query_specs: Optional[List[Tuple[str, str]]] = None,
        default_detail_route: str = "jymhzichan",
        ssl_verify: bool = True,
        ssl_ca_bundle: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        item_saved_callback=None,
        run_id: Optional[str] = None,
    ):
        self.html_root = html_root
        self.page_size = max(1, int(page_size))
        self.max_pages = max_pages if max_pages is None else max(1, int(max_pages))
        self.concurrency = max(1, int(concurrency))
        self.resume = bool(resume)
        self.timeout = max(5, int(timeout))
        self.save_json = bool(save_json)
        self.output_type = str(output_type or TYPE_PHYSICAL_ASSET)
        if list_query_specs is None:
            list_query_specs = _sse_list_query_specs(
                business_id_key(self.output_type)
            )
        self.list_query_specs: List[Tuple[str, str]] = [
            (str(project_type), str(gplx))
            for project_type, gplx in list_query_specs
        ]
        self._default_detail_route = str(default_detail_route or "jymhzichan").strip("/") or "jymhzichan"
        self.logger = logger or logging.getLogger("parser_v2")
        self.item_saved_callback = item_saved_callback
        self.run_id = str(run_id or "").strip() or f"run-{time.time_ns()}"
        self._render_timeout_ms = max(120, self.timeout) * 1000
        self._detail_retries = 2
        self.ssl_verify = bool(ssl_verify)
        raw_ca_bundle = str(ssl_ca_bundle or "").strip()
        self.ssl_ca_bundle = raw_ca_bundle or None
        self._ssl_context_verified = self._build_verified_ssl_context() if self.ssl_verify else None
        self._ssl_context = self._ssl_context_verified
        if not self.ssl_verify:
            self.logger.warning(
                "SSE SSL verification is disabled. Traffic to suaee.com will not verify certificates."
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
            "Start SSE download: type=%s start_date=%s end_date=%s page_size=%s max_pages=%s concurrency=%s resume=%s output=%s",
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
            self.logger.info(
                "Use prefetched SSE candidates: type=%s entries=%s",
                self.output_type,
                len(prefetched_candidates),
            )
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
            self.logger.info(
                "List-only mode: skip detail download for type=%s candidates=%s",
                self.output_type,
                summary.detail_candidates,
            )
        elif candidates:
            asyncio.run(
                self._download_candidates_concurrently(
                    candidates=candidates,
                    summary=summary,
                    start=start,
                    end=end,
                )
            )
        else:
            self.logger.info("No candidate details to download.")

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

        self.logger.info(
            "Done: pages=%s listed=%s fetched=%s saved=%s list_date_skipped=%s detail_date_skipped=%s resume_skipped=%s duplicate_skipped=%s missing_xmid_skipped=%s detail_unavailable_skipped=%s detail_candidates=%s detail_failed=%s list_unaccounted=%s detail_unaccounted=%s errors=%s",
            summary.pages_requested,
            summary.listed_items,
            summary.detail_fetched,
            summary.saved,
            summary.skipped_by_list_date,
            summary.skipped_by_detail_date,
            summary.skipped_by_resume,
            summary.skipped_by_duplicate,
            summary.skipped_by_missing_xmid,
            summary.skipped_by_detail_unavailable,
            summary.detail_candidates,
            summary.detail_failed,
            summary.list_unaccounted,
            summary.detail_unaccounted,
            len(summary.typed_errors),
        )
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
        task_id = runtime_task_id("sse", self.output_type)
        business_id = business_id_key(self.output_type)
        authoritative_specs = tuple(_sse_list_query_specs(business_id))
        configured_specs = tuple(self.list_query_specs)
        expected_query_ids = tuple(
            self._sse_query_id(list_project_type, gplx)
            for list_project_type, gplx in authoritative_specs
        )
        task_evidence = DiscoveryTaskEvidence(
            root=output_dir,
            source_id="sse",
            task_id=task_id,
            run_id=self.run_id,
            expected_query_ids=expected_query_ids,
        )
        if configured_specs != authoritative_specs:
            summary.typed_errors.append(
                list_failed_error(
                    source_id="sse",
                    task_id=task_id,
                    raw_reason=(
                        "sse-list-contract-mismatch: configured query specs must "
                        "exactly match source_business_contract; "
                        f"configured={configured_specs!r} "
                        f"authoritative={authoritative_specs!r}"
                    ),
                )
            )
            task_evidence.fail(
                termination_reason="authoritative_config_mismatch",
                details={
                    "configured_query_count": len(configured_specs),
                    "authoritative_query_count": len(authoritative_specs),
                },
                missing_query_ids=expected_query_ids,
            )
            summary.discovery_task_manifest = task_evidence.manifest_reference()
            return
        staged_candidates: List[_DownloadCandidate] = []
        list_state = {
            "listed_items": summary.listed_items,
            "skipped_by_list_date": summary.skipped_by_list_date,
            "skipped_by_resume": summary.skipped_by_resume,
            "skipped_by_duplicate": summary.skipped_by_duplicate,
            "skipped_by_missing_xmid": summary.skipped_by_missing_xmid,
            "candidate_entries": len(summary.candidate_entries),
            "candidate_dates": len(summary.candidate_dates),
            "typed_errors": len(summary.typed_errors),
        }

        def rollback_staged_discovery() -> None:
            summary.listed_items = int(list_state["listed_items"])
            summary.skipped_by_list_date = int(list_state["skipped_by_list_date"])
            summary.skipped_by_resume = int(list_state["skipped_by_resume"])
            summary.skipped_by_duplicate = int(list_state["skipped_by_duplicate"])
            summary.skipped_by_missing_xmid = int(list_state["skipped_by_missing_xmid"])
            del summary.candidate_entries[int(list_state["candidate_entries"]):]
            del summary.candidate_dates[int(list_state["candidate_dates"]):]

        with task_evidence:
            try:
                task_complete = self._collect_sse_task_queries(
                    output_dir=output_dir,
                    summary=summary,
                    candidates=staged_candidates,
                    start=start,
                    end=end,
                    task_evidence=task_evidence,
                )
            except Exception as exc:  # noqa: BLE001
                rollback_staged_discovery()
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="sse",
                        task_id=task_id,
                        raw_reason=f"sse-discovery-task-failed: {exc}",
                    )
                )
                task_evidence.fail(
                    termination_reason="exception",
                    details={"error": str(exc)},
                )
                summary.discovery_task_manifest = task_evidence.manifest_reference()
                return
            if not task_complete:
                rollback_staged_discovery()
                task_evidence.fail(termination_reason="query_failed")
                summary.discovery_task_manifest = task_evidence.manifest_reference()
                return
            candidate_error_count = len(summary.typed_errors) - int(
                list_state["typed_errors"]
            )
            if candidate_error_count:
                rollback_staged_discovery()
                task_evidence.fail(
                    termination_reason="candidate_conversion_failed",
                    details={"candidate_error_count": candidate_error_count},
                )
                summary.discovery_task_manifest = task_evidence.manifest_reference()
                return
            try:
                task_evidence.complete(
                    candidate_entries=summary.candidate_entries[
                        int(list_state["candidate_entries"]):
                    ]
                )
                summary.discovery_task_manifest = task_evidence.manifest_reference()
            except DiscoveryEvidenceError as exc:
                rollback_staged_discovery()
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="sse",
                        task_id=task_id,
                        raw_reason=f"sse-discovery-task-incomplete: {exc}",
                    )
                )
                summary.discovery_task_manifest = task_evidence.manifest_reference()
                return
        candidates.extend(staged_candidates)

    @staticmethod
    def _sse_query_id(list_project_type: str, gplx: str) -> str:
        return f"{list_project_type}-gplx-{gplx}"

    def _collect_sse_task_queries(
        self,
        *,
        output_dir: str,
        summary: DownloadSummary,
        candidates: List[_DownloadCandidate],
        start: Optional[dt.date],
        end: Optional[dt.date],
        task_evidence: DiscoveryTaskEvidence,
    ) -> bool:
        seen_xmid: Set[str] = set()
        task_id = runtime_task_id("sse", self.output_type)
        for list_project_type, gplx in self.list_query_specs:
            query_rows: List[Dict[str, Any]] = []
            query_evidence = task_evidence.query(
                self._sse_query_id(list_project_type, gplx),
                authoritative_total=True,
                page_size=self.page_size,
            )
            try:
                with query_evidence:
                    page_index = 1
                    page_count: Optional[int] = None
                    termination_reason = "declared_pages_exhausted"
                    termination_facts: dict[str, object] = {}
                    while page_count is None or page_index <= page_count:
                        request_metadata = {
                            "method": "POST",
                            "project_type": list_project_type,
                            "gplx": gplx,
                            "page_index": page_index,
                            "page_size": self.page_size,
                        }
                        try:
                            raw_response = self._query_list_page(
                                page_index=page_index,
                                list_project_type=list_project_type,
                                gplx=gplx,
                            )
                        except Exception as exc:  # noqa: BLE001
                            query_evidence.fail(
                                termination_reason="request_failed",
                                details={"page_index": page_index, "error": str(exc)},
                            )
                            raise

                        summary.pages_requested += 1
                        response = self._coerce_sse_list_response(
                            raw_response,
                            list_project_type=list_project_type,
                        )
                        query_evidence.capture_page(
                            page_index=page_index,
                            response=response,
                            body_format="json",
                            request_metadata=request_metadata,
                        )
                        try:
                            _payload, rows, total_records, declared_pages = (
                                self._decode_sse_list_response(response)
                            )
                        except Exception as exc:
                            query_evidence.fail_page(
                                page_index=page_index,
                                reason="response_invalid",
                                details={
                                    "error_type": type(exc).__name__,
                                    "error": str(exc),
                                },
                            )
                            query_evidence.fail(
                                termination_reason="response_invalid",
                                details={"page_index": page_index, "error": str(exc)},
                            )
                            raise

                        try:
                            query_evidence.complete_page(
                                page_index=page_index,
                                extracted_row_count=len(rows),
                                row_identity_values=self._sse_page_identities(rows),
                                declared_total_items=total_records,
                                declared_total_pages=(
                                    declared_pages if declared_pages > 0 else None
                                ),
                            )
                        except DiscoveryEvidenceError as exc:
                            query_evidence.fail(
                                termination_reason="evidence_failed",
                                details={"page_index": page_index, "error": str(exc)},
                            )
                            raise

                        if page_count is None:
                            page_count = max(1, declared_pages)
                            if declared_pages == 0:
                                termination_reason = "official_empty"
                            if self.max_pages is not None and page_count > self.max_pages:
                                # SSE returns an authoritative page count.  A smaller
                                # operator limit would make the result provably
                                # incomplete, so retain the request as provenance but
                                # traverse all declared pages.
                                termination_facts = {
                                    "requested_max_pages": self.max_pages,
                                    "effective_max_pages": page_count,
                                    "max_pages_overridden": True,
                                }
                                summary.list_page_observations.append(
                                    {
                                        "status": "max_pages_overridden",
                                        "query_id": self._sse_query_id(list_project_type, gplx),
                                        "requested_max_pages": self.max_pages,
                                        "declared_total_pages": page_count,
                                        "reason": "authoritative_declared_pages_require_complete_discovery",
                                    }
                                )
                                self.logger.warning(
                                    "SSE discovery max_pages=%s is below authoritative declared_pages=%s; "
                                    "continuing to complete discovery (query=%s)",
                                    self.max_pages,
                                    page_count,
                                    self._sse_query_id(list_project_type, gplx),
                                )
                        query_rows.extend(rows)
                        page_index += 1

                    try:
                        query_evidence.complete(
                            termination_reason=termination_reason,
                            termination_facts=termination_facts,
                        )
                    except DiscoveryEvidenceError:
                        raise
            except _SseListStructureError as exc:
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="sse",
                        task_id=task_id,
                        raw_reason=(
                            f"list-{list_project_type}-{gplx}-page-{page_index}-invalid-data: {exc}"
                        ),
                    )
                )
                return False
            except Exception as exc:  # noqa: BLE001
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="sse",
                        task_id=task_id,
                        raw_reason=f"list-{list_project_type}-{gplx}-discovery-failed: {exc}",
                    )
                )
                return False

            page_index = max(1, page_index - 1)
            page_count = page_count or page_index
            for row in query_rows:
                if not isinstance(row, dict):
                    continue
                row = self._normalize_list_row(row)
                summary.listed_items += 1

                xmid = str(row.get("xmid") or row.get("XMID") or "").strip()
                if not xmid:
                    summary.skipped_by_missing_xmid += 1
                    summary.typed_errors.append(
                        invalid_candidate_error(
                            source_id="sse",
                            task_id=task_id,
                            raw_reason=f"list-{list_project_type}-{gplx}-page-{page_index}-missing-xmid",
                        )
                    )
                    continue
                if xmid in seen_xmid:
                    summary.skipped_by_duplicate += 1
                    continue
                seen_xmid.add(xmid)

                list_disclosure_start = parse_loose_date(
                    row.get("plksrq")
                    or row.get("PLKSRQ")
                    or row.get("gpksrq")
                    or row.get("GPKSRQ")
                )
                if start or end:
                    if list_disclosure_start is None:
                        summary.skipped_by_list_date += 1
                        continue
                    if not in_date_range(list_disclosure_start, start, end):
                        summary.skipped_by_list_date += 1
                        continue

                project_code = str(row.get("xmbh") or row.get("XMBH") or xmid).strip()
                project_name = str(row.get("xmmc") or row.get("XMMC") or "").strip()
                html_path, _ = resolve_submission_snapshot_target(
                    archive_root=output_dir,
                    project_code=project_code.upper(),
                    project_name=project_name,
                    listing_date=list_disclosure_start.isoformat()
                    if list_disclosure_start
                    else "",
                )
                business_id = business_id_key(self.output_type)
                if self.resume and _is_resume_complete(
                    html_path,
                    save_json=self.save_json,
                    task_id=task_id,
                    source_id="sse",
                    business_id=business_id,
                ):
                    summary.skipped_by_resume += 1
                    self.logger.info(
                        "Resume skip: xmid=%s existing=%s",
                        xmid,
                        os.path.basename(html_path),
                    )
                    continue

                page_url = self._resolve_page_url(row=row, xmid=xmid)
                candidate = _DownloadCandidate(
                    xmid=xmid,
                    project_code=project_code.upper(),
                    page_url=page_url,
                    html_path=html_path,
                    row=row,
                )
                candidates.append(candidate)
                summary.candidate_entries.append(
                    {
                        "xmid": candidate.xmid,
                        "project_code": candidate.project_code,
                        "page_url": candidate.page_url,
                        "row": row,
                        "disclosure_start": list_disclosure_start.isoformat()
                        if list_disclosure_start
                        else None,
                    }
                )
                if list_disclosure_start:
                    summary.candidate_dates.append(list_disclosure_start.isoformat())

            self.logger.info(
                "List progress[%s|gplx=%s]: page %s/%s total_listed=%s candidates=%s list_date_skipped=%s resume_skipped=%s duplicate_skipped=%s missing_xmid_skipped=%s",
                list_project_type,
                gplx,
                page_index,
                page_count,
                summary.listed_items,
                len(candidates),
                summary.skipped_by_list_date,
                summary.skipped_by_resume,
                summary.skipped_by_duplicate,
                summary.skipped_by_missing_xmid,
                )
        return True

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
        seen_xmid: Set[str] = set()
        for index, raw in enumerate(prefetched_candidates, start=1):
            if not isinstance(raw, dict):
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="sse",
                        task_id=runtime_task_id("sse", self.output_type),
                        raw_reason=f"prefetched-entry-{index}-invalid-format",
                    )
                )
                continue
            summary.listed_items += 1
            entry = dict(raw)

            xmid = str(entry.get("xmid") or "").strip()
            if not xmid:
                summary.skipped_by_missing_xmid += 1
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="sse",
                        task_id=runtime_task_id("sse", self.output_type),
                        raw_reason=f"prefetched-entry-{index}-missing-xmid",
                    )
                )
                continue
            if xmid in seen_xmid:
                summary.skipped_by_duplicate += 1
                continue
            seen_xmid.add(xmid)

            row_raw = entry.get("row")
            row = row_raw if isinstance(row_raw, dict) else {}
            list_disclosure_start = parse_loose_date(
                entry.get("disclosure_start") or row.get("plksrq") or row.get("gpksrq")
            )
            if list_disclosure_start and "disclosure_start" not in row:
                row = {**row, "disclosure_start": list_disclosure_start.isoformat()}
            if start or end:
                if list_disclosure_start is None:
                    summary.skipped_by_list_date += 1
                    continue
                if not in_date_range(list_disclosure_start, start, end):
                    summary.skipped_by_list_date += 1
                    continue

            project_code = str(entry.get("project_code") or row.get("xmbh") or xmid).strip().upper()
            page_url = str(entry.get("page_url") or self._resolve_page_url(row=row, xmid=xmid)).strip()
            if not page_url:
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="sse",
                        task_id=runtime_task_id("sse", self.output_type),
                        raw_reason=f"prefetched-entry-{index}-missing-page-url: xmid={xmid}",
                    )
                )
                continue

            project_name = str(entry.get("project_name") or row.get("xmmc") or "").strip()
            html_path, _ = resolve_submission_snapshot_target(
                archive_root=output_dir,
                project_code=project_code or xmid,
                project_name=project_name,
                listing_date=list_disclosure_start.isoformat() if list_disclosure_start else "",
            )
            business_id = business_id_key(self.output_type)
            if self.resume and _is_resume_complete(
                html_path,
                save_json=self.save_json,
                task_id=runtime_task_id("sse", self.output_type),
                source_id="sse",
                business_id=business_id,
            ):
                summary.skipped_by_resume += 1
                continue

            candidate = _DownloadCandidate(
                xmid=xmid,
                project_code=project_code,
                page_url=page_url,
                html_path=html_path,
                row=row,
            )
            candidates.append(candidate)
            summary.candidate_entries.append(
                {
                    "xmid": candidate.xmid,
                    "project_code": candidate.project_code,
                    "page_url": candidate.page_url,
                    "row": row,
                    "disclosure_start": list_disclosure_start.isoformat()
                    if list_disclosure_start
                    else None,
                }
            )
            if list_disclosure_start:
                summary.candidate_dates.append(list_disclosure_start.isoformat())

    def _query_list_page(
        self,
        *,
        page_index: int,
        list_project_type: str,
        gplx: str,
    ) -> HttpFetchedText:
        business_id = business_id_key(self.output_type)
        try:
            query_spec = _sse_authoritative_query_spec(
                business_id,
                project_type=list_project_type,
                gplx=gplx,
            )
        except ValueError as exc:
            raise ValueError(
                f"sse-list-contract-mismatch list_project_type={list_project_type} "
                f"gplx={gplx} business_id={business_id}"
            ) from exc

        payload: Dict[str, Any] = {
            "pageNo": int(page_index),
            "pageSize": self.page_size,
            "SZDQ": "",
            "SORT": "",
            "SZCS": "",
            "SZQX": "",
            "ZCLB": "",
            "ZRDJXX": "",
            "ZRDJSX": "",
            "KEY": "",
            "SFGZ": "",
        }
        if "XMLX" in query_spec:
            payload["XMLX"] = str(query_spec["XMLX"])
        return self._post_json(_sse_list_api_url(query_spec["endpoint"]), payload)

    def _coerce_sse_list_response(
        self,
        response: object,
        *,
        list_project_type: str,
    ) -> HttpFetchedText:
        if not isinstance(response, HttpFetchedText):
            raise DiscoveryEvidenceError(
                "SSE list transport must return HttpFetchedText"
            )
        try:
            query_spec = _sse_authoritative_query_spec(
                business_id_key(self.output_type),
                project_type=list_project_type,
            )
            expected_url = _sse_list_api_url(query_spec["endpoint"])
        except ValueError as exc:
            raise DiscoveryEvidenceError(
                f"unknown authoritative SSE list project type: {list_project_type}"
            ) from exc
        for field, actual_url in (
            ("source_url", response.source_url),
            ("final_url", response.final_url),
        ):
            if not _url_matches_endpoint(actual_url, expected_url):
                raise DiscoveryEvidenceError(
                    f"SSE {field} does not match authoritative endpoint: "
                    f"project_type={list_project_type} expected={expected_url!r} "
                    f"actual={actual_url!r}"
                )
        return response

    def _decode_sse_list_response(
        self,
        response: HttpFetchedText,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]], int, int]:
        raw_bytes = response.raw_bytes
        if not isinstance(raw_bytes, bytes):
            raise _SseListStructureError(
                "SSE list response requires original response bytes"
            )
        try:
            payload = json.loads(raw_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _SseListStructureError(f"invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise _SseListStructureError("response root must be an object")

        code = self._sse_nonnegative_int(payload.get("code"), field="code")
        if code not in {0, 200}:
            message = str(payload.get("message") or payload.get("msg") or "").strip()
            raise RuntimeError(f"SSE list API code={code}: {message}")

        data = payload.get("data")
        if isinstance(data, list):
            rows_raw = data
        elif data is None:
            rows_raw = []
        elif isinstance(data, dict):
            if "data" not in data:
                raise _SseListStructureError("response data object has no data rows field")
            rows_raw = data.get("data")
            if rows_raw is None:
                rows_raw = []
        else:
            raise _SseListStructureError("response data must be a list or object")
        if not isinstance(rows_raw, list):
            raise _SseListStructureError("response rows must be a list")
        if any(not isinstance(row, dict) for row in rows_raw):
            raise _SseListStructureError("response rows must contain only objects")
        rows: List[Dict[str, Any]] = list(rows_raw)

        extra = payload.get("extra")
        count_containers = [payload]
        if isinstance(extra, dict):
            count_containers.append(extra)
        if isinstance(data, dict):
            count_containers.append(data)

        raw_totals: List[object] = []
        if extra is not None and not isinstance(extra, dict):
            raw_totals.append(extra)
        for container in count_containers:
            for key in ("total", "totalCount", "recordsTotal", "totalElements"):
                if container.get(key) is not None:
                    raw_totals.append(container[key])
        if not raw_totals:
            raise _SseListStructureError("declared total records are missing")
        total_values = {
            self._sse_nonnegative_int(value, field="declared total records")
            for value in raw_totals
        }
        if len(total_values) != 1:
            raise _SseListStructureError(
                f"conflicting declared total records: {sorted(total_values)}"
            )
        total_records = next(iter(total_values))

        raw_page_counts = [
            container["pageCount"]
            for container in count_containers
            if container.get("pageCount") is not None
        ]
        page_count_values = {
            self._sse_nonnegative_int(value, field="declared pageCount")
            for value in raw_page_counts
        }
        if len(page_count_values) > 1:
            raise _SseListStructureError(
                f"conflicting declared pageCount values: {sorted(page_count_values)}"
            )
        expected_pages = (total_records + self.page_size - 1) // self.page_size
        if page_count_values:
            declared_pages = next(iter(page_count_values))
            if declared_pages != expected_pages:
                raise _SseListStructureError(
                    "declared pageCount does not close with total records and page size: "
                    f"pageCount={declared_pages} total={total_records} "
                    f"page_size={self.page_size}"
                )
        else:
            declared_pages = expected_pages
        if total_records == 0 and rows:
            raise _SseListStructureError(
                "declared total records=0 with nonempty response rows"
            )
        return payload, rows, total_records, declared_pages

    @staticmethod
    def _sse_nonnegative_int(value: object, *, field: str) -> int:
        if isinstance(value, bool):
            raise _SseListStructureError(f"{field} must be a nonnegative integer")
        if isinstance(value, float) and not value.is_integer():
            raise _SseListStructureError(f"{field} must be a nonnegative integer")
        try:
            number = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError) as exc:
            raise _SseListStructureError(
                f"{field} must be a nonnegative integer"
            ) from exc
        if number < 0:
            raise _SseListStructureError(f"{field} must be a nonnegative integer")
        return number

    @staticmethod
    def _sse_page_identities(rows: List[Dict[str, Any]]) -> List[str]:
        identities: List[str] = []
        for row in rows:
            identifier = str(
                row.get("xmid")
                or row.get("XMID")
                or row.get("ID")
                or row.get("id")
                or ""
            ).strip()
            identities.append(
                identifier
                or json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        return identities

    def _normalize_list_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            **row,
            "xmid": str(row.get("xmid") or row.get("XMID") or row.get("ID") or "").strip(),
            "xmbh": str(row.get("xmbh") or row.get("XMBH") or "").strip(),
            "xmmc": str(row.get("xmmc") or row.get("XMMC") or "").strip(),
            "plksrq": row.get("plksrq") or row.get("PLKSRQ"),
            "pljsrq": row.get("pljsrq") or row.get("PLJSRQ"),
            "xmlx": str(row.get("xmlx") or row.get("XMLX") or "").strip(),
            "fclass": str(row.get("fclass") or row.get("FCLASS") or "").strip(),
        }

    def _resolve_page_url(self, *, row: Dict[str, Any], xmid: str) -> str:
        fclass = row.get("fclass") or row.get("FCLASS") or ""
        xmlx = str(row.get("xmlx") or row.get("XMLX") or "").strip()
        xmurl = str(row.get("xmurl") or "").strip()
        if xmurl:
            if xmurl.startswith(("http://", "https://")):
                return xmurl
            return urllib.parse.urljoin("https://www.suaee.com/", xmurl)

        base = "https://www.suaee.com/xmzx.html#"
        xmid_quoted = urllib.parse.quote(xmid)
        route = self._resolve_detail_route_name(fclass=str(fclass).strip(), xmlx=xmlx)
        plzt = self._resolve_detail_plzt(xmlx=xmlx)
        if route == "zczrDetail":
            return f"{base}/zczrDetail?XMID={xmid_quoted}"
        if route == "qyzzDetail":
            return f"{base}/qyzzDetail?XMID={xmid_quoted}&PLZT={plzt}"
        return f"{base}/Detail?XMID={xmid_quoted}&PLZT={plzt}"

    def _resolve_detail_route_name(self, *, fclass: str, xmlx: str) -> str:
        if fclass == "SW":
            return "zczrDetail"
        if fclass == "1C":
            return "qyzzDetail"
        if fclass == "GQ":
            return "Detail"

        route = self._default_detail_route
        if route == "jymhzichan":
            return "zczrDetail"
        if route in ("jymhchanquan", "jymhchanquanyu"):
            return "Detail"
        if route in ("jymhzengzi", "jymhzengziyu"):
            return "qyzzDetail"
        return "Detail"

    def _resolve_detail_plzt(self, *, xmlx: str) -> str:
        if xmlx:
            return xmlx
        if self._default_detail_route in ("jymhchanquanyu", "jymhzengziyu"):
            return "1"
        return "2"

    def _guess_detail_route(self, *, row: Dict[str, Any]) -> str:
        list_project_type = str(row.get("projectType") or "").upper()
        gplx = str(row.get("gplx") or "")
        if list_project_type == "ZICHANZHUANRANG":
            return "jymhzichan"
        if list_project_type == "CHANQUAN":
            return "jymhchanquanyu" if gplx == "1" else "jymhchanquan"
        if list_project_type == "ZENGZI":
            return "jymhzengziyu" if gplx == "1" else "jymhzengzi"
        return self._default_detail_route

    def _build_verified_ssl_context(self) -> ssl.SSLContext:
        if self.ssl_ca_bundle:
            if not os.path.isfile(self.ssl_ca_bundle):
                raise ValueError(f"invalid ssl_ca_bundle (file not found): {self.ssl_ca_bundle}")
            self.logger.info("SSE SSL verification with custom CA bundle: %s", self.ssl_ca_bundle)
            return ssl.create_default_context(cafile=self.ssl_ca_bundle)

        try:
            import certifi  # type: ignore

            certifi_bundle = certifi.where()
            self.logger.info("SSE SSL verification with certifi CA bundle: %s", certifi_bundle)
            return ssl.create_default_context(cafile=certifi_bundle)
        except Exception:
            self.logger.info("SSE SSL verification with system CA store.")
            return ssl.create_default_context()

    def _urlopen(self, request: urllib.request.Request):
        try:
            return urllib.request.urlopen(
                request,
                timeout=self.timeout,
                context=self._ssl_context,
            )
        except Exception:  # noqa: BLE001
            raise

    def _post_json(self, url: str, payload: Dict[str, Any]) -> HttpFetchedText:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url=url, data=body, headers=REQUEST_HEADERS, method="POST")
        try:
            with self._urlopen(request) as response:
                raw_bytes = response.read()
                headers = getattr(response, "headers", None)
                charset_getter = getattr(headers, "get_content_charset", None)
                charset = charset_getter() if callable(charset_getter) else None
                text = raw_bytes.decode(charset or "utf-8", errors="replace")
                final_url = str(response.geturl())
                http_status = int(
                    getattr(response, "status", None) or response.getcode()
                )
        except urllib.error.URLError as exc:
            raise RuntimeError(f"POST {url} failed: {exc}") from exc
        return HttpFetchedText(
            text,
            source_url=url,
            final_url=final_url,
            http_status=http_status,
            raw_bytes=raw_bytes,
        )

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

        total = len(candidates)
        completed = 0
        started_at = time.monotonic()
        progress_lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(self.concurrency)
        progress_log = ProgressLogThrottle(total=total)

        self.logger.info("Detail download start: total=%s concurrency=%s", total, self.concurrency)

        async with async_playwright() as pw:
            browser = await launch_chromium_browser(pw, headless=True)
            context = await browser.new_context()
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
                            self._log_detail_progress(
                                completed=completed,
                                total=total,
                                summary=summary,
                                started_at=started_at,
                            )

                tasks = [asyncio.create_task(worker(candidate)) for candidate in candidates]
                await asyncio.gather(*tasks)
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
                )
                actual_code = self._extract_project_code(rendered_html)
                if actual_code and actual_code != candidate.project_code:
                    raise RuntimeError(
                        f"project-code-mismatch expected={candidate.project_code} actual={actual_code}"
                    )
                summary.detail_fetched += 1
                break
            except timeout_error_cls as exc:
                last_exc = exc
                if attempt <= self._detail_retries:
                    self.logger.warning(
                        "Detail page timeout, retry %s/%s: xmid=%s",
                        attempt,
                        self._detail_retries,
                        candidate.xmid,
                    )
                    await asyncio.sleep(1.2 * attempt)
                else:
                    summary.typed_errors.append(
                        execute_failed_error(
                            source_id="sse",
                            task_id=runtime_task_id("sse", self.output_type),
                            raw_reason=f"xmid={candidate.xmid} page-timeout: {exc}",
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt <= self._detail_retries:
                    self.logger.warning(
                        "Detail fetch failed, retry %s/%s: xmid=%s error=%s",
                        attempt,
                        self._detail_retries,
                        candidate.xmid,
                        exc,
                    )
                    await asyncio.sleep(1.2 * attempt)
                else:
                    summary.typed_errors.append(
                        execute_failed_error(
                            source_id="sse",
                            task_id=runtime_task_id("sse", self.output_type),
                            raw_reason=f"xmid={candidate.xmid} page-fetch-failed: {exc}",
                        )
                    )
            finally:
                await page.close()

        if rendered_html is None:
            summary.detail_failed += 1
            if last_exc is not None:
                self.logger.error("Detail fetch failed: xmid=%s error=%s", candidate.xmid, last_exc)
            return

        disclosure_start = self._extract_disclosure_start_date(rendered_html)
        list_start = parse_loose_date(
            candidate.row.get("disclosure_start")
            or candidate.row.get("plksrq")
            or candidate.row.get("gpksrq")
        )
        final_date = disclosure_start if disclosure_start is not None else list_start
        if start or end:
            if final_date is None:
                summary.date_missing_skipped += 1
                summary.skipped_by_detail_date += 1
                return
            if not in_date_range(final_date, start, end):
                summary.skipped_by_detail_date += 1
                return
        task_id = runtime_task_id("sse", self.output_type)
        if not reserve_download_target(
            summary,
            html_root=self.html_root,
            html_path=candidate.html_path,
            source_id="sse",
            task_id=task_id,
        ):
            summary.detail_failed += 1
            return

        try:
            await self._run_blocking(
                self._save_complete_page,
                rendered_html=rendered_html,
                page_url=candidate.page_url,
                html_path=candidate.html_path,
            )
            sidecar = {
                "task_id": task_id,
                "source_id": "sse",
                "record_family": "listing",
                "business_id": business_id_key(self.output_type),
                "xmid": candidate.xmid,
                "xmbh": candidate.row.get("xmbh"),
                "xmmc": candidate.row.get("xmmc"),
                "page_url": candidate.page_url,
                **successful_http_evidence(
                    source_url=candidate.page_url,
                    http_status=http_status,
                ),
                "list_row": candidate.row,
                "disclosure_start_date": disclosure_start.isoformat() if disclosure_start else None,
            }
            if self.save_json:
                json_path = os.path.splitext(candidate.html_path)[0] + ".json"
                await self._run_blocking(
                    self._write_json,
                    json_path=json_path,
                    payload={**sidecar, "save_status": "pending"},
                )
            else:
                await self._run_blocking(
                    self._write_resume_status,
                    html_path=candidate.html_path,
                    save_status="pending",
                    source_url=candidate.page_url,
                    http_status=http_status,
                )
            if self.save_json:
                await self._run_blocking(
                    self._write_json,
                    json_path=json_path,
                    payload={
                        **sidecar,
                        "save_status": "complete",
                        **archive_integrity_fields(candidate.html_path),
                    },
                )
            else:
                await self._run_blocking(
                    self._write_resume_status,
                    html_path=candidate.html_path,
                    save_status="complete",
                    source_url=candidate.page_url,
                    http_status=http_status,
                )
            self._notify_item_saved(candidate=candidate, disclosure_start=disclosure_start or list_start)
        except Exception as exc:  # noqa: BLE001
            await self._run_blocking(
                mark_artifact_save_failed,
                html_path=candidate.html_path,
                save_json=self.save_json,
                write_json=lambda json_path, payload: self._write_json(
                    json_path=json_path,
                    payload=payload,
                ),
                failure_identity={
                    "task_id": runtime_task_id("sse", self.output_type),
                    "source_id": "sse",
                    "record_family": "listing",
                    "business_id": business_id_key(self.output_type),
                },
                write_resume_status=lambda html_path, save_status: self._write_resume_status(
                    html_path=html_path,
                    save_status=save_status,
                    source_url=candidate.page_url,
                    http_status=http_status,
                ),
                logger=self.logger,
            )
            summary.detail_failed += 1
            summary.typed_errors.append(
                save_failed_error(
                    source_id="sse",
                    task_id=runtime_task_id("sse", self.output_type),
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
        expected_project_code: Optional[str] = None,
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
        await page.wait_for_selector(
            "body",
            timeout=self._render_timeout_ms,
        )
        last_html = ""
        for _ in range(12):
            await page.wait_for_timeout(1500)
            html = await page.content()
            last_html = html
            if self._is_real_detail_page(
                html_text=html,
                expected_project_code=expected_project_code,
            ):
                return html, http_status
        raise RuntimeError(
            "detail-page-not-ready: "
            f"expected_project_code={expected_project_code or ''} page_url={page_url} html_len={len(last_html)}"
        )

    @staticmethod
    def _normalize_html_text(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "")).upper()

    @classmethod
    def _extract_detail_title(cls, soup: BeautifulSoup) -> str:
        for selector in (".project-detail-top .title", "div.project_xmmc"):
            node = soup.select_one(selector)
            if node is None:
                continue
            text = str(node.get_text(" ", strip=True) or "").strip()
            if text:
                return text
        return ""

    @classmethod
    def _has_meaningful_detail_content(cls, soup: BeautifulSoup) -> bool:
        selectors = (
            ".project-price-num .fs30",
            ".project-price-num span",
            ".xmjs-infor-box .numb",
            ".xmjs-infor-box .label-infor .cont",
            "table .xmtd2",
            ".project_content span",
            ".detail-info td .text",
        )
        for selector in selectors:
            for node in soup.select(selector):
                text = str(node.get_text(" ", strip=True) or "").strip()
                normalized = cls._normalize_html_text(text)
                if not normalized:
                    continue
                if normalized in {cls._normalize_html_text(item) for item in DETAIL_SHELL_PLACEHOLDERS}:
                    continue
                if normalized in {cls._normalize_html_text(item) for item in DETAIL_SHELL_LABELS}:
                    continue
                return True
        return False

    @classmethod
    def _is_real_detail_page(
        cls,
        *,
        html_text: str,
        expected_project_code: Optional[str] = None,
    ) -> bool:
        soup = BeautifulSoup(html_text, "html.parser")
        normalized_text = cls._normalize_html_text(soup.get_text(" ", strip=True))
        if not normalized_text:
            return False

        expected_code = cls._normalize_html_text(expected_project_code or "")
        if expected_code and expected_code not in normalized_text:
            return False

        title = cls._normalize_html_text(cls._extract_detail_title(soup))
        if not title:
            return False

        if "NETWORKERROR" in normalized_text and not cls._has_meaningful_detail_content(soup):
            return False

        return cls._has_meaningful_detail_content(soup)

    async def _run_blocking(self, func, /, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(**kwargs))

    def _write_json(self, *, json_path: str, payload: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        temp_json_path = f"{json_path}.tmp"
        with open(temp_json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp_json_path, json_path)

    def _write_resume_status(
        self,
        *,
        html_path: str,
        save_status: str,
        source_url: str,
        http_status: int,
    ) -> None:
        normalized_status = str(save_status or "").strip() or "pending"
        payload = {
            "schema_version": 1,
            "task_id": runtime_task_id("sse", self.output_type),
            "source_id": "sse",
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

    def _log_detail_progress(
        self,
        *,
        completed: int,
        total: int,
        summary: DownloadSummary,
        started_at: float,
    ) -> None:
        elapsed = max(0.001, time.monotonic() - started_at)
        speed = completed / elapsed * 60.0
        remaining = max(0, total - completed)
        eta_seconds = int((remaining / max(completed / elapsed, 1e-6)))
        eta_text = str(dt.timedelta(seconds=eta_seconds))
        self.logger.info(
            "Detail progress: %s/%s saved=%s detail_date_skipped=%s unavailable_skipped=%s errors=%s speed=%.2f/min eta=%s",
            completed,
            total,
            summary.saved,
            summary.skipped_by_detail_date,
            summary.skipped_by_detail_unavailable,
            len(summary.typed_errors),
            speed,
            eta_text,
        )

    def _extract_disclosure_start_date(self, html_text: str) -> Optional[dt.date]:
        soup = BeautifulSoup(html_text, "html.parser")
        text = soup.get_text(" ", strip=True)
        for pattern in DISCLOSURE_START_PATTERNS:
            match = re.search(pattern, text)
            if match:
                parsed = parse_loose_date(match.group(1))
                if parsed is not None:
                    return parsed
        return None

    @staticmethod
    def _extract_project_code(html_text: str) -> str:
        soup = BeautifulSoup(html_text, "html.parser")
        block = soup.find("div", class_="project_code")
        text = block.get_text(" ", strip=True) if block else soup.get_text(" ", strip=True)
        match = re.search(
            r"(G3|Q3|P3|G6|Q6|P6|GR|QR|PR)\d{4}SH\d+(?:-\d+)?",
            text,
            flags=re.IGNORECASE,
        )
        return match.group(0).upper() if match else ""

    def _save_complete_page(self, *, rendered_html: str, page_url: str, html_path: str) -> None:
        base_name = os.path.splitext(os.path.basename(html_path))[0]
        final_assets_dir = f"{os.path.splitext(html_path)[0]}_files"
        temp_assets_dir = f"{final_assets_dir}.part"
        temp_html_path = f"{html_path}.part"

        if os.path.isdir(temp_assets_dir):
            shutil.rmtree(temp_assets_dir)
        if os.path.isfile(temp_html_path):
            os.remove(temp_html_path)

        try:
            os.makedirs(temp_assets_dir, exist_ok=True)

            soup = BeautifulSoup(rendered_html, "html.parser")
            # Freeze current rendered state for offline open: remove runtime scripts.
            for script in soup.find_all("script"):
                script.decompose()
            # Drop prefetch/preload hints that do not affect parsed content.
            for link in soup.find_all("link"):
                rel = [str(x).lower() for x in (link.get("rel") or [])]
                if any(x in {"prefetch", "preload", "modulepreload"} for x in rel):
                    link.decompose()

            downloaded_by_url: Dict[str, str] = {}
            source_url_by_local: Dict[str, str] = {}

            for tag_name, attr_name in TAG_ASSET_ATTRS:
                for node in soup.find_all(tag_name):
                    raw_value = node.get(attr_name)
                    if not raw_value:
                        continue
                    local_name = self._download_asset(
                        raw_url=str(raw_value),
                        base_url=page_url,
                        assets_dir=temp_assets_dir,
                        downloaded_by_url=downloaded_by_url,
                        source_url_by_local=source_url_by_local,
                    )
                    if not local_name:
                        continue
                    node[attr_name] = f"{base_name}_files/{local_name}"

            # Rewrite nested css url(...) assets after first-pass css download.
            for local_name, source_url in list(source_url_by_local.items()):
                if not local_name.lower().endswith(".css"):
                    continue
                css_path = os.path.join(temp_assets_dir, local_name)
                self._rewrite_css_assets(
                    css_path=css_path,
                    css_source_url=source_url,
                    assets_dir=temp_assets_dir,
                    downloaded_by_url=downloaded_by_url,
                    source_url_by_local=source_url_by_local,
                )

            with open(temp_html_path, "w", encoding="utf-8") as handle:
                handle.write(str(soup))

            if os.path.isdir(final_assets_dir):
                shutil.rmtree(final_assets_dir)
            if os.path.isfile(html_path):
                os.remove(html_path)

            os.replace(temp_assets_dir, final_assets_dir)
            os.replace(temp_html_path, html_path)
            self._write_invalid_shell_evidence_if_needed(
                html_path=html_path,
                page_url=page_url,
            )
        except Exception:
            if os.path.isdir(temp_assets_dir):
                shutil.rmtree(temp_assets_dir, ignore_errors=True)
            if os.path.isfile(temp_html_path):
                try:
                    os.remove(temp_html_path)
                except OSError:
                    pass
            raise

    @staticmethod
    def _safe_sha256(value: str) -> str:
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _write_invalid_shell_evidence_if_needed(self, *, html_path: str, page_url: str) -> None:
        with open(html_path, encoding="utf-8") as handle:
            saved_html = handle.read()

        if self._is_real_detail_page(html_text=saved_html):
            evidence_path = f"{html_path}.peap-evidence.json"
            if os.path.isfile(evidence_path):
                os.remove(evidence_path)
            return

        identity_hints: Dict[str, str] = {}
        project_code = self._extract_project_code(saved_html)
        if project_code:
            identity_hints["project_code_hash"] = self._safe_sha256(project_code)

        locator_hash = self._safe_sha256(page_url)
        evidence = {
            "schema_version": 1,
            "page_kind": "invalid_shell",
            "source_url_hash": locator_hash,
            "final_url_hash": locator_hash,
            "content_sha256": self._safe_sha256(saved_html),
            "identity_hints": identity_hints,
        }
        with open(f"{html_path}.peap-evidence.json", "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, ensure_ascii=False, indent=2, sort_keys=True)

    def _notify_item_saved(self, *, candidate: _DownloadCandidate, disclosure_start: Optional[dt.date]) -> None:
        callback = self.item_saved_callback
        if callback is None:
            return
        callback(
            {
                "source_file": candidate.html_path,
                "page_url": candidate.page_url,
                "project_code": candidate.project_code,
                "project_name": str(candidate.row.get("xmmc") or ""),
                "listing_date": disclosure_start.isoformat() if disclosure_start else "",
                "source_id": "sse",
                "business_id": business_id_key(self.output_type),
                "row": candidate.row,
            }
        )

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
            with self._urlopen(request) as response:
                content = response.read()
                content_type = response.headers.get("Content-Type", "")
        except Exception:
            return None

        basename = os.path.basename(parsed.path)
        basename = re.sub(r"[\\/:*?\"<>|]+", "_", basename)
        if not basename:
            digest = hashlib.md5(absolute_url.encode("utf-8")).hexdigest()[:12]
            basename = f"asset_{digest}"

        root, ext = os.path.splitext(basename)
        if not ext:
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

        try:
            text = raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            try:
                text = raw.decode("gb18030")
                encoding = "gb18030"
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
                encoding = "latin-1"

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
            if not local_name:
                return match.group(0)
            return f"url('{local_name}')"

        updated = re.sub(r"url\(([^)]+)\)", replace, text)
        if updated != text:
            with open(css_path, "w", encoding=encoding, errors="ignore") as handle:
                handle.write(updated)


class ShanghaiEquityTransferDownloader(ShanghaiPhysicalAssetDownloader):
    manifest_list_endpoint = _sse_list_endpoint("equity_transfer")
    manifest_date_field_candidates = ("disclosure_start", "disclosure_end")

    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_EQUITY_TRANSFER,
            list_query_specs=_sse_list_query_specs("equity_transfer"),
            default_detail_route="jymhchanquan",
            **kwargs,
        )


class ShanghaiCapitalIncreaseDownloader(ShanghaiPhysicalAssetDownloader):
    manifest_list_endpoint = _sse_list_endpoint("capital_increase")
    manifest_date_field_candidates = ("disclosure_start", "disclosure_end")

    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_CAPITAL_INCREASE,
            list_query_specs=_sse_list_query_specs("capital_increase"),
            default_detail_route="jymhzengzi",
            **kwargs,
        )


class ShanghaiPreDisclosureDownloader(ShanghaiPhysicalAssetDownloader):
    manifest_list_endpoint = _sse_list_endpoint("pre_disclosure")
    manifest_date_field_candidates = ("disclosure_start",)

    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PRE_DISCLOSURE,
            list_query_specs=_sse_list_query_specs("pre_disclosure"),
            default_detail_route="jymhchanquanyu",
            **kwargs,
        )
