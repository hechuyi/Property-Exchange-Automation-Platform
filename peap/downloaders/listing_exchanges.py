"""Downloaders for the newly supported listing exchange scopes.

The detail artifact contract for these sources is intentionally raw: the
primary HTML file is exactly the rendered ``page.content()`` string.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import html
import json
import logging
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from peap_core.source_business_contract import get_source_business_requirement

from ..browser_runtime import launch_chromium_browser
from ..constants import TYPE_CAPITAL_INCREASE, TYPE_EQUITY_TRANSFER
from ..download_errors import invalid_candidate_error, list_failed_error, save_failed_error
from ..submission_layout import resolve_submission_snapshot_target
from .common import (
    DownloadSummary,
    HttpFetchedText,
    archive_integrity_fields,
    complete_resume_sidecar_exists,
    detail_accounted_count,
    in_date_range,
    parse_bound,
    parse_loose_date,
    record_downloaded_target,
    reserve_download_target,
    successful_http_evidence,
)
from .discovery_evidence import DiscoveryEvidenceError, DiscoveryTaskEvidence


@dataclass
class _ListingCandidate:
    item_id: str
    project_code: str
    project_name: str
    page_url: str
    disclosure_start: dt.date | None
    row: dict[str, Any]
    html_path: str


@dataclass(frozen=True)
class _CandidateSchema:
    id_fields: tuple[str, ...]
    date_fields: tuple[str, ...]
    url_fields: tuple[str, ...]
    code_fields: tuple[str, ...]
    name_fields: tuple[str, ...]


class _DeclaredRowsPathSchemaError(ValueError):
    pass


_HTML_LIST_OK_STATUSES = {"items", "empty"}


class _BaseListingExchangeDownloader:
    source_id = ""
    business_id = ""
    output_type = TYPE_EQUITY_TRANSFER
    manifest_list_endpoint = ""
    manifest_detail_route = ""
    manifest_render_page_route = ""
    manifest_date_field_candidates = ("publishDate", "disclosure_start", "listing_date")
    list_api_url = ""
    list_method = "POST_JSON"
    list_headers: dict[str, str] = {}
    rows_path: tuple[str, ...] | None = None
    html_list_response_enabled = False
    html_list_identity_markers: tuple[str, ...] = ()
    html_list_block_markers: tuple[str, ...] = ()
    html_list_empty_markers: tuple[str, ...] = ()
    list_fetch_attempts = 3
    list_browser_request_fallback_enabled = True
    detail_navigation_attempts = 2
    detail_navigation_wait_until = "domcontentloaded"
    detail_body_wait_state = "visible"
    candidate_schema = _CandidateSchema(
        id_fields=("id", "itemId", "projectId", "xmid", "guid"),
        date_fields=(
            "disclosure_start",
            "publishDate",
            "listingDate",
            "listDate",
            "gpksrq",
            "plksrq",
            "startDate",
            "createdTime",
        ),
        url_fields=("page_url", "url", "detailUrl", "href", "link"),
        code_fields=("project_code", "projectCode", "xmbh", "code", "projectNo"),
        name_fields=("project_name", "title", "projectName", "xmmc", "name"),
    )

    def __init__(
        self,
        *,
        html_root: str,
        page_size: int = 20,
        max_pages: Optional[int] = None,
        concurrency: int = 1,
        resume: bool = False,
        timeout: int = 30,
        save_json: bool = False,
        output_type: str | None = None,
        logger: Optional[logging.Logger] = None,
        item_saved_callback=None,
        run_id: str | None = None,
    ) -> None:
        self.html_root = html_root
        self.page_size = max(1, int(page_size))
        self.max_pages = max_pages if max_pages is None else max(1, int(max_pages))
        self.concurrency = max(1, int(concurrency))
        self.resume = bool(resume)
        self.timeout = max(5, int(timeout))
        self.save_json = bool(save_json)
        self.output_type = str(output_type or self.output_type)
        self.logger = logger or logging.getLogger("parser_v2")
        self.item_saved_callback = item_saved_callback
        self.run_id = str(run_id or "").strip() or f"run-{int(time.time() * 1000)}"
        self._render_timeout_ms = self.timeout * 1000
        self._detail_payload_cache: dict[str, dict[str, Any]] = {}

    @property
    def task_id(self) -> str:
        return f"{self.source_id}:listing:{self.business_id}"

    def run(
        self,
        *,
        start_date: Optional[str],
        end_date: Optional[str],
        list_only: bool = False,
        prefetched_candidates: Optional[list[dict[str, Any]]] = None,
    ) -> DownloadSummary:
        start = parse_bound(start_date, "start-date")
        end = parse_bound(end_date, "end-date")
        if start and end and start > end:
            raise ValueError(f"start-date {start_date!r} is after end-date {end_date!r}")

        output_dir = os.path.abspath(self.html_root)
        os.makedirs(output_dir, exist_ok=True)
        evidence_dir = self._evidence_dir(output_dir)
        os.makedirs(evidence_dir, exist_ok=True)

        summary = DownloadSummary()
        candidates: list[_ListingCandidate]
        if prefetched_candidates is None:
            candidates = self._collect_list_candidates(
                output_dir=output_dir,
                evidence_dir=evidence_dir,
                summary=summary,
                start=start,
                end=end,
            )
        else:
            candidates = self._build_prefetched_candidates(
                prefetched_candidates=prefetched_candidates,
                output_dir=output_dir,
                evidence_dir=evidence_dir,
                summary=summary,
                start=start,
                end=end,
            )
        summary.detail_candidates = len(candidates)

        if candidates and not list_only:
            asyncio.run(
                self._download_candidates(
                    candidates=candidates, summary=summary, evidence_dir=evidence_dir
                )
            )

        list_accounted = (
            summary.skipped_by_list_date
            + summary.skipped_by_resume
            + summary.skipped_by_duplicate
            + summary.skipped_by_business_filter
            + summary.skipped_by_missing_xmid
            + summary.detail_candidates
        )
        detail_accounted = detail_accounted_count(summary)
        summary.list_unaccounted = summary.listed_items - list_accounted
        summary.detail_unaccounted = (
            0 if list_only else (summary.detail_candidates - detail_accounted)
        )
        return summary

    def _evidence_dir(self, output_dir: str) -> str:
        return os.path.join(
            output_dir,
            "_evidence",
            self._safe_path_component(self.run_id),
            self._safe_path_component(self.task_id.replace(":", "__")),
        )

    def _collect_list_candidates(
        self,
        *,
        output_dir: str,
        evidence_dir: str,
        summary: DownloadSummary,
        start: dt.date | None,
        end: dt.date | None,
    ) -> list[_ListingCandidate]:
        candidates: list[_ListingCandidate] = []
        seen: set[str] = set()
        seen_page_signatures: set[tuple[Any, ...]] = set()
        page_num = 1
        query_failed = False
        termination_reason = ""
        declared_pages: int | None = None
        task_evidence = DiscoveryTaskEvidence(
            root=output_dir,
            source_id=self.source_id,
            task_id=self.task_id,
            run_id=self.run_id,
            expected_query_ids=("listing",),
        )
        query_evidence = task_evidence.query(
            "listing",
            authoritative_total=True,
            page_size=self.page_size,
        )
        with task_evidence, query_evidence:
            while True:
                if self.max_pages is not None and page_num > self.max_pages:
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id=self.source_id,
                            task_id=self.task_id,
                            raw_reason=(
                                "explicit-max-pages-truncated-discovery: "
                                f"max_pages={self.max_pages} next_page={page_num}"
                            ),
                        )
                    )
                    query_evidence.fail(
                        termination_reason="explicit_max_pages",
                        details={"max_pages": self.max_pages},
                    )
                    query_failed = True
                    break
                payload = self._list_payload(page_num)
                try:
                    raw_text = self._fetch_list_page(page_num=page_num, payload=payload)
                except Exception as exc:  # noqa: BLE001
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id=self.source_id, task_id=self.task_id, raw_reason=str(exc)
                        )
                    )
                    self._append_decision(
                        evidence_dir,
                        {"decision": "failure", "stage": "list", "reason": str(exc)},
                    )
                    query_evidence.fail(
                        termination_reason="request_failed",
                        details={"page_index": page_num, "error": str(exc)},
                    )
                    query_failed = True
                    break
                self._write_text(
                    os.path.join(evidence_dir, f"list_page_{page_num}.json"), str(raw_text)
                )
                summary.pages_requested += 1
                try:
                    query_evidence.capture_page(
                        page_index=page_num,
                        response=raw_text,
                        body_format=(
                            "html"
                            if self._accept_html_list_response(str(raw_text))
                            else "json"
                        ),
                        request_metadata={"method": self.list_method, "payload": payload},
                    )
                except DiscoveryEvidenceError as exc:
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id=self.source_id,
                            task_id=self.task_id,
                            raw_reason=f"list-page-{page_num}-capture-failed: {exc}",
                        )
                    )
                    query_evidence.fail(
                        termination_reason="evidence_capture_failed",
                        details={"page_index": page_num, "error": str(exc)},
                    )
                    query_failed = True
                    break
                try:
                    payload_obj = self._decode_list_response(raw_text)
                except json.JSONDecodeError as exc:
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id=self.source_id,
                            task_id=self.task_id,
                            raw_reason=f"invalid-json: {exc}",
                        )
                    )
                    self._append_decision(
                        evidence_dir,
                        {
                            "decision": "failure",
                            "stage": "list_parse",
                            "reason": f"invalid-json: {exc}",
                        },
                    )
                    query_evidence.fail_page(
                        page_index=page_num,
                        reason="response_invalid",
                        details={"error": str(exc)},
                    )
                    query_evidence.fail(
                        termination_reason="response_invalid",
                        details={"page_index": page_num, "error": str(exc)},
                    )
                    query_failed = True
                    break
                try:
                    rows = self._extract_rows(payload_obj)
                except _DeclaredRowsPathSchemaError as exc:
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id=self.source_id,
                            task_id=self.task_id,
                            raw_reason=str(exc),
                        )
                    )
                    self._append_decision(
                        evidence_dir,
                        {
                            "decision": "failure",
                            "stage": "list_schema",
                            "reason": str(exc),
                        },
                    )
                    query_evidence.fail_page(
                        page_index=page_num,
                        reason="schema_invalid",
                        details={"error": str(exc)},
                    )
                    query_evidence.fail(
                        termination_reason="schema_invalid",
                        details={"page_index": page_num, "error": str(exc)},
                    )
                    query_failed = True
                    break
                observation = self._observe_list_page(
                    page_num=page_num,
                    raw_text=raw_text,
                    payload=payload_obj,
                    rows=rows,
                )
                if observation is not None:
                    summary.list_page_observations.append(observation)
                declared_total, observed_declared_pages = self._declared_list_counts(
                    payload=payload_obj,
                    observation=observation,
                )
                if observed_declared_pages is not None:
                    declared_pages = observed_declared_pages
                try:
                    query_evidence.complete_page(
                        page_index=page_num,
                        extracted_row_count=len(rows),
                        row_identity_values=self._page_signature(rows),
                        declared_total_items=declared_total,
                        declared_total_pages=declared_pages,
                    )
                except DiscoveryEvidenceError as exc:
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id=self.source_id,
                            task_id=self.task_id,
                            raw_reason=f"list-page-{page_num}-evidence-failed: {exc}",
                        )
                    )
                    query_evidence.fail(
                        termination_reason="evidence_failed",
                        details={"page_index": page_num, "error": str(exc)},
                    )
                    query_failed = True
                    break
                if observation is not None:
                    status = str(observation.get("status") or "")
                    if status not in _HTML_LIST_OK_STATUSES:
                        raw_reason = f"list-page-{page_num}-{status}"
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id=self.source_id,
                                task_id=self.task_id,
                                raw_reason=raw_reason,
                            )
                        )
                        self._append_decision(
                            evidence_dir,
                            {
                                "decision": "failure",
                                "stage": "list_observe",
                                "reason": raw_reason,
                            },
                        )
                        query_evidence.fail(
                            termination_reason="list_observation_failed",
                            details={"page_index": page_num, "status": status},
                        )
                        query_failed = True
                        break
                page_signature = self._page_signature(rows)
                if page_signature and page_signature in seen_page_signatures:
                    raw_reason = f"list-page-{page_num}-repeated-page"
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id=self.source_id,
                            task_id=self.task_id,
                            raw_reason=raw_reason,
                        )
                    )
                    query_evidence.fail(
                        termination_reason="repeated_page",
                        details={"page_index": page_num},
                    )
                    query_failed = True
                    break
                seen_page_signatures.add(page_signature)
                for row in rows:
                    candidate = self._candidate_from_row(
                        row=row,
                        output_dir=output_dir,
                        summary=summary,
                        evidence_dir=evidence_dir,
                        start=start,
                        end=end,
                        seen=seen,
                    )
                    if candidate is not None:
                        candidates.append(candidate)

                if declared_pages is not None and page_num >= declared_pages:
                    termination_reason = "declared_pages_exhausted"
                    break
                if len(rows) < self.page_size:
                    termination_reason = "official_empty" if not rows else "short_page"
                    break
                page_num += 1

            if termination_reason and not query_failed:
                try:
                    query_evidence.complete(termination_reason=termination_reason)
                    task_evidence.complete(
                        candidate_entries=summary.candidate_entries,
                    )
                    summary.discovery_task_manifest = task_evidence.manifest_reference()
                except DiscoveryEvidenceError as exc:
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id=self.source_id,
                            task_id=self.task_id,
                            raw_reason=f"discovery-coverage-failed: {exc}",
                        )
                    )
        if os.path.isfile(task_evidence.manifest_path):
            summary.discovery_task_manifest = task_evidence.manifest_reference()
        return candidates

    def _build_prefetched_candidates(
        self,
        *,
        prefetched_candidates: list[dict[str, Any]],
        output_dir: str,
        evidence_dir: str,
        summary: DownloadSummary,
        start: dt.date | None,
        end: dt.date | None,
    ) -> list[_ListingCandidate]:
        candidates: list[_ListingCandidate] = []
        seen: set[str] = set()
        for raw in prefetched_candidates:
            row_raw = raw.get("row")
            if row_raw is None:
                row = {}
            elif isinstance(row_raw, Mapping):
                row = dict(row_raw)
            else:
                summary.listed_items += 1
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id=self.source_id,
                        task_id=self.task_id,
                        raw_reason="prefetched-candidate-row-non-mapping: field=row",
                    )
                )
                self._append_decision(
                    evidence_dir,
                    {"decision": "failure", "reason": "row-non-mapping", "field": "row"},
                )
                continue
            merged = {**row, **raw}
            candidate = self._candidate_from_row(
                row=merged,
                output_dir=output_dir,
                summary=summary,
                evidence_dir=evidence_dir,
                start=start,
                end=end,
                seen=seen,
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _candidate_from_row(
        self,
        *,
        row: dict[str, Any],
        output_dir: str,
        summary: DownloadSummary,
        evidence_dir: str,
        start: dt.date | None,
        end: dt.date | None,
        seen: set[str],
    ) -> _ListingCandidate | None:
        summary.listed_items += 1
        schema = self.candidate_schema
        item_id = self._first_text(row, *schema.id_fields)
        if not item_id:
            summary.skipped_by_missing_xmid += 1
            summary.typed_errors.append(
                invalid_candidate_error(
                    source_id=self.source_id,
                    task_id=self.task_id,
                    raw_reason="missing-candidate-id",
                )
            )
            self._append_decision(
                evidence_dir, {"decision": "failure", "reason": "missing-candidate-id", "row": row}
            )
            return None

        explicit_page_url = self._first_text(row, *schema.url_fields)
        business_filter_reason = self._candidate_business_filter_reason(
            row=row,
            explicit_page_url=explicit_page_url,
        )
        if business_filter_reason:
            summary.skipped_by_business_filter += 1
            self._append_decision(
                evidence_dir,
                {
                    "decision": "excluded",
                    "reason": "business-filter",
                    "business_filter_reason": business_filter_reason,
                    "item_id": item_id,
                    "fclass": self._first_text(row, "FCLASS"),
                    "cqlsgx": self._first_text(row, "CQLSGX"),
                    "explicit_page_url": explicit_page_url,
                },
            )
            return None
        if item_id in seen:
            summary.skipped_by_duplicate += 1
            self._append_decision(
                evidence_dir, {"decision": "excluded", "reason": "duplicate", "item_id": item_id}
            )
            return None
        seen.add(item_id)

        disclosure_start = parse_loose_date(
            self._first_text(
                row,
                *schema.date_fields,
            )
        )
        if (start or end) and not in_date_range(disclosure_start, start, end):
            summary.skipped_by_list_date += 1
            self._append_decision(
                evidence_dir,
                {
                    "decision": "excluded",
                    "reason": "out-of-date-range",
                    "item_id": item_id,
                    "disclosure_start": disclosure_start.isoformat() if disclosure_start else None,
                },
            )
            return None

        page_url = self._normalize_page_url(explicit_page_url, row=row)
        if not page_url:
            summary.typed_errors.append(
                invalid_candidate_error(
                    source_id=self.source_id,
                    task_id=self.task_id,
                    raw_reason=f"missing-page-url: {item_id}",
                )
            )
            self._append_decision(
                evidence_dir,
                {"decision": "failure", "reason": "missing-page-url", "item_id": item_id},
            )
            return None

        project_code = (
            self._first_text(row, *schema.code_fields)
            or item_id
        )
        project_name = self._first_text(row, *schema.name_fields)
        html_path, _ = resolve_submission_snapshot_target(
            archive_root=output_dir,
            project_code=project_code,
            project_name=project_name,
            listing_date=disclosure_start.isoformat() if disclosure_start else "",
        )
        if self.resume and self._is_resume_complete(html_path):
            summary.skipped_by_resume += 1
            self._append_decision(
                evidence_dir,
                {
                    "decision": "excluded",
                    "reason": "resume-existing",
                    "item_id": item_id,
                    "archive_path": html_path,
                },
            )
            return None

        candidate = _ListingCandidate(
            item_id=item_id,
            project_code=project_code,
            project_name=project_name,
            page_url=page_url,
            disclosure_start=disclosure_start,
            row=row,
            html_path=html_path,
        )
        summary.candidate_entries.append(
            {
                "id": item_id,
                "project_code": project_code,
                "project_name": project_name,
                "page_url": page_url,
                "disclosure_start": disclosure_start.isoformat() if disclosure_start else None,
                "row": row,
            }
        )
        if disclosure_start:
            summary.candidate_dates.append(disclosure_start.isoformat())
        self._append_decision(
            evidence_dir,
            {
                "decision": "accepted",
                "item_id": item_id,
                "project_code": project_code,
                "page_url": page_url,
                "archive_path": html_path,
            },
        )
        return candidate

    def _candidate_business_filter_reason(
        self,
        *,
        row: dict[str, Any],
        explicit_page_url: str,
    ) -> str:
        return ""

    def _is_resume_complete(self, html_path: str) -> bool:
        if not os.path.isfile(html_path):
            return False
        expected_fields = {
            "task_id": self.task_id,
            "source_id": self.source_id,
            "record_family": "listing",
            "business_id": self.business_id,
        }
        marker_path = self._resume_status_path(html_path)
        json_path = os.path.splitext(html_path)[0] + ".json"
        if self.save_json:
            return complete_resume_sidecar_exists(
                html_path,
                sidecar_path=json_path,
                require_integrity=True,
                expected_fields=expected_fields,
            )
        return complete_resume_sidecar_exists(
            html_path,
            sidecar_path=marker_path,
            require_integrity=True,
            expected_fields=expected_fields,
        )

    def _is_complete_resume_payload(self, payload: Mapping[str, Any]) -> bool:
        save_status = str(payload.get("save_status") or "").strip().lower()
        return (
            save_status == "complete"
            and str(payload.get("task_id") or "").strip() == self.task_id
            and str(payload.get("source_id") or "").strip() == self.source_id
            and str(payload.get("record_family") or "").strip() == "listing"
            and str(payload.get("business_id") or "").strip() == self.business_id
        )

    @staticmethod
    def _has_invalid_shell_evidence(html_path: str) -> bool:
        evidence_path = f"{html_path}.peap-evidence.json"
        if not os.path.isfile(evidence_path):
            return False
        try:
            with open(evidence_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        return str(payload.get("page_kind") or "").strip().lower() == "invalid_shell"

    @staticmethod
    def _resume_status_path(html_path: str) -> str:
        return f"{html_path}.peap-save-status.json"

    @staticmethod
    def _load_resume_status_payload(marker_path: str) -> dict[str, Any] | None:
        try:
            with open(marker_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

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
            "task_id": self.task_id,
            "source_id": self.source_id,
            "record_family": "listing",
            "business_id": self.business_id,
            "save_status": normalized_status,
            **successful_http_evidence(
                source_url=source_url,
                http_status=http_status,
            ),
        }
        if normalized_status == "complete":
            payload.update(archive_integrity_fields(html_path))
        self._write_json(
            self._resume_status_path(html_path),
            payload,
        )

    async def _download_candidates(
        self,
        *,
        candidates: list[_ListingCandidate],
        summary: DownloadSummary,
        evidence_dir: str,
    ) -> None:
        async with async_playwright() as pw:
            browser = await launch_chromium_browser(pw, headless=True)
            try:
                lock = asyncio.Lock()
                semaphore = asyncio.Semaphore(self.concurrency)

                async def download_one(candidate: _ListingCandidate) -> None:
                    async with semaphore:
                        page = await browser.new_page()
                        try:
                            async with lock:
                                if not reserve_download_target(
                                    summary,
                                    html_root=self.html_root,
                                    html_path=candidate.html_path,
                                    source_id=self.source_id,
                                    task_id=self.task_id,
                                ):
                                    summary.detail_failed += 1
                                    return
                            html_text, http_status = await self._fetch_rendered_html(
                                page=page,
                                page_url=candidate.page_url,
                                expected_project_code=candidate.project_code,
                                expected_project_name=candidate.project_name,
                            )
                            evidence_source_url = self._detail_evidence_source_url(candidate)
                            self._write_text(candidate.html_path, html_text)
                            self._write_invalid_shell_evidence_if_needed(
                                candidate=candidate,
                                html_text=html_text,
                            )
                            detail_payload = {
                                "task_id": self.task_id,
                                "source_id": self.source_id,
                                "record_family": "listing",
                                "business_id": self.business_id,
                                "archive_path": candidate.html_path,
                                "page_url": candidate.page_url,
                                **successful_http_evidence(
                                    source_url=evidence_source_url,
                                    http_status=http_status,
                                ),
                                "project_code": candidate.project_code,
                                "project_name": candidate.project_name,
                                "disclosure_start": candidate.disclosure_start.isoformat()
                                if candidate.disclosure_start
                                else None,
                                "list_row": candidate.row,
                            }
                            detail_extras = self._detail_sidecar_extras(candidate)
                            if detail_extras:
                                detail_payload.update(detail_extras)
                            should_write_sidecar = self.save_json or bool(detail_extras)
                            sidecar_path = os.path.splitext(candidate.html_path)[0] + ".json"
                            if should_write_sidecar:
                                self._write_json(
                                    sidecar_path,
                                    {**detail_payload, "save_status": "pending"},
                                )
                            if not self.save_json:
                                self._write_resume_status(
                                    candidate.html_path,
                                    "pending",
                                    source_url=evidence_source_url,
                                    http_status=http_status,
                                )
                            if should_write_sidecar:
                                complete_payload = {**detail_payload, "save_status": "complete"}
                                complete_payload.update(archive_integrity_fields(candidate.html_path))
                                self._write_json(
                                    sidecar_path,
                                    complete_payload,
                                )
                            if not self.save_json:
                                self._write_resume_status(
                                    candidate.html_path,
                                    "complete",
                                    source_url=evidence_source_url,
                                    http_status=http_status,
                                )
                            self._notify_item_saved(candidate)
                            async with lock:
                                summary.detail_fetched += 1
                                summary.saved += 1
                                record_downloaded_target(
                                    summary,
                                    html_root=self.html_root,
                                    html_path=candidate.html_path,
                                )
                        except Exception as exc:  # noqa: BLE001
                            if os.path.isfile(candidate.html_path):
                                if self.save_json:
                                    json_path = os.path.splitext(candidate.html_path)[0] + ".json"
                                    try:
                                        existing_payload = {}
                                        if os.path.isfile(json_path):
                                            with open(json_path, "r", encoding="utf-8") as handle:
                                                loaded = json.load(handle)
                                            if isinstance(loaded, dict):
                                                existing_payload = dict(loaded)
                                        existing_payload.update(
                                            {
                                                "task_id": self.task_id,
                                                "source_id": self.source_id,
                                                "record_family": "listing",
                                                "business_id": self.business_id,
                                            }
                                        )
                                        existing_payload["save_status"] = "failed"
                                        self._write_json(json_path, existing_payload)
                                    except Exception:  # noqa: BLE001
                                        self._write_resume_status(
                                            candidate.html_path,
                                            "failed",
                                            source_url=self._detail_evidence_source_url(candidate),
                                            http_status=http_status,
                                        )
                                else:
                                    self._write_resume_status(
                                        candidate.html_path,
                                        "failed",
                                        source_url=self._detail_evidence_source_url(candidate),
                                        http_status=http_status,
                                    )
                            async with lock:
                                summary.detail_failed += 1
                                summary.typed_errors.append(
                                    save_failed_error(
                                        source_id=self.source_id,
                                        task_id=self.task_id,
                                        raw_reason=str(exc),
                                    )
                                )
                            self._append_decision(
                                evidence_dir,
                                {
                                    "decision": "failure",
                                    "stage": "detail",
                                    "item_id": candidate.item_id,
                                    "reason": str(exc),
                                },
                            )
                        finally:
                            close = getattr(page, "close", None)
                            if callable(close):
                                try:
                                    await close()
                                except Exception as exc:  # noqa: BLE001
                                    self.logger.warning("failed to close detail page: %s", exc)

                tasks = [asyncio.create_task(download_one(candidate)) for candidate in candidates]
                if tasks:
                    try:
                        await asyncio.gather(*tasks)
                    except Exception:
                        for task in tasks:
                            task.cancel()
                        raise
            finally:
                await browser.close()

    def _detail_sidecar_extras(self, candidate: _ListingCandidate) -> dict[str, Any]:
        return {}

    def _detail_evidence_source_url(self, candidate: _ListingCandidate) -> str:
        return candidate.page_url

    async def _fetch_rendered_html(
        self,
        *,
        page,
        page_url: str,
        expected_project_code: str = "",
        expected_project_name: str = "",
    ) -> tuple[str, int]:
        http_status = await self._goto_rendered_page_with_retry(page=page, page_url=page_url)
        await page.wait_for_selector(
            "body",
            state=self.detail_body_wait_state,
            timeout=self._render_timeout_ms,
        )
        last_html = ""
        has_expected_identity = bool(
            self._normalize_identity_text(expected_project_code)
            or self._normalize_identity_text(expected_project_name)
        )
        for _ in range(20):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(300)
            html = await page.content()
            if self._rendered_html_matches_candidate(
                html,
                expected_project_code=expected_project_code,
                expected_project_name=expected_project_name,
            ):
                return html, http_status
            if html == last_html and not has_expected_identity:
                self._ensure_rendered_html_matches_candidate(
                    html,
                    expected_project_code=expected_project_code,
                    expected_project_name=expected_project_name,
                    page_url=page_url,
                )
                return html, http_status
            last_html = html
        self._ensure_rendered_html_matches_candidate(
            last_html,
            expected_project_code=expected_project_code,
            expected_project_name=expected_project_name,
            page_url=page_url,
        )
        raise RuntimeError("rendered-html-unstable")

    async def _goto_rendered_page_with_retry(self, *, page, page_url: str) -> int:
        attempts = max(1, int(self.detail_navigation_attempts))
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await page.goto(
                    page_url,
                    wait_until=self.detail_navigation_wait_until,
                    timeout=self._render_timeout_ms,
                )
                return successful_http_evidence(
                    source_url=page_url,
                    http_status=getattr(response, "status", None),
                )["http_status"]
            except Exception as exc:  # noqa: BLE001
                if not self._is_retryable_render_navigation_error(exc) or attempt >= attempts:
                    raise
                last_exc = exc
                wait_for_timeout = getattr(page, "wait_for_timeout", None)
                if callable(wait_for_timeout):
                    await wait_for_timeout(500 * attempt)
        if last_exc is not None:
            raise last_exc

    @staticmethod
    def _is_retryable_render_navigation_error(exc: Exception) -> bool:
        text = str(exc).upper()
        retryable_markers = (
            "ERR_TIMED_OUT",
            "TIMEOUT",
            "TIMED OUT",
            "ERR_CONNECTION_RESET",
            "ERR_CONNECTION_CLOSED",
            "ERR_NETWORK_CHANGED",
        )
        return any(marker in text for marker in retryable_markers)

    @staticmethod
    def _normalize_identity_text(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "")).upper()

    @classmethod
    def _rendered_html_matches_candidate(
        cls,
        html_text: str,
        *,
        expected_project_code: str,
        expected_project_name: str,
    ) -> bool:
        text = cls._normalize_identity_text(BeautifulSoup(html_text or "", "html.parser").get_text(" ", strip=True))
        expected_code = cls._normalize_identity_text(expected_project_code)
        expected_name = cls._normalize_identity_text(expected_project_name)
        if expected_code and expected_code in text:
            return True
        if expected_name and expected_name in text:
            return True
        return False

    @classmethod
    def _ensure_rendered_html_matches_candidate(
        cls,
        html_text: str,
        *,
        expected_project_code: str,
        expected_project_name: str,
        page_url: str,
    ) -> None:
        if cls._rendered_html_matches_candidate(
            html_text,
            expected_project_code=expected_project_code,
            expected_project_name=expected_project_name,
        ):
            return
        expected_code = cls._normalize_identity_text(expected_project_code)
        expected_name = cls._normalize_identity_text(expected_project_name)
        if expected_code or expected_name:
            raise RuntimeError(
                "detail-page-mismatch "
                f"expected_project_code={expected_project_code} page_url={page_url}"
            )

    def _fetch_list_page(self, *, page_num: int, payload: dict[str, Any]) -> HttpFetchedText:
        def fetch() -> HttpFetchedText:
            if self.list_method == "GET":
                return self._get_text(self._list_get_url(payload))
            return self._post_json(self.list_api_url, payload)

        try:
            return self._fetch_list_page_with_retry(fetch)
        except Exception as exc:
            if not self._should_use_browser_request_fallback(exc):
                raise
            self.logger.warning(
                "List fetch exhausted urllib retries; trying browser request fallback: source=%s page=%s error=%s",
                self.source_id,
                page_num,
                exc,
            )
            return self._fetch_list_page_via_browser_request(
                page_num=page_num,
                payload=payload,
                last_error=exc,
            )

    def _list_get_url(self, payload: dict[str, Any]) -> str:
        query_items: list[tuple[str, Any]] = []
        for key, value in payload.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                query_items.extend((key, item) for item in value if item is not None)
            else:
                query_items.append((key, value))
        if not query_items:
            return self.list_api_url
        parts = urllib.parse.urlsplit(self.list_api_url)
        existing_query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        query = urllib.parse.urlencode(existing_query + query_items, doseq=True)
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, query, parts.fragment)
        )

    def _fetch_list_page_with_retry(self, fetch) -> HttpFetchedText:
        attempts = max(1, int(self.list_fetch_attempts))
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return fetch()
            except (TimeoutError, urllib.error.URLError, OSError, ssl.SSLError) as exc:
                last_exc = exc
                if attempt >= attempts:
                    break
                time.sleep(min(2.0, 0.25 * attempt))
        assert last_exc is not None
        raise last_exc

    def _should_use_browser_request_fallback(self, exc: Exception) -> bool:
        if not bool(self.list_browser_request_fallback_enabled):
            return False
        text = str(exc).upper()
        reason = getattr(exc, "reason", None)
        if reason is not None:
            text = f"{text} {reason}".upper()
        retryable_markers = (
            "HANDSHAKE",
            "SSL",
            "TIMEOUT",
            "TIMED OUT",
            "CONNECTION RESET",
            "CONNECTIONRESET",
            "CONNECTION ABORTED",
            "CONNECTION CLOSED",
            "EOF OCCURRED",
            "NETWORK",
        )
        return any(marker in text for marker in retryable_markers)

    def _fetch_list_page_via_browser_request(
        self,
        *,
        page_num: int,
        payload: dict[str, Any],
        last_error: Exception,
    ) -> HttpFetchedText:
        return asyncio.run(
            self._fetch_list_page_via_browser_request_async(
                page_num=page_num,
                payload=payload,
                last_error=last_error,
            )
        )

    async def _fetch_list_page_via_browser_request_async(
        self,
        *,
        page_num: int,
        payload: dict[str, Any],
        last_error: Exception,
    ) -> HttpFetchedText:
        headers = {"User-Agent": "Mozilla/5.0", **dict(self.list_headers)}
        timeout_ms = max(1, int(self.timeout)) * 1000
        async with async_playwright() as pw:
            request_context = await pw.request.new_context(
                extra_http_headers=headers,
                ignore_https_errors=True,
            )
            try:
                if self.list_method == "GET":
                    url = self._list_get_url(payload)
                    response = await request_context.get(url, timeout=timeout_ms)
                else:
                    url = self.list_api_url
                    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    response = await request_context.post(
                        url,
                        data=data,
                        headers={"Content-Type": "application/json;charset=UTF-8"},
                        timeout=timeout_ms,
                    )
                raw_bytes = await response.body()
                text = await response.text()
                status = int(response.status)
                if status >= 400:
                    raise RuntimeError(
                        "browser-request-list-failed "
                        f"status={status} source={self.source_id} page={page_num} "
                        f"url={url} previous_error={last_error}"
                    )
                return HttpFetchedText(
                    text,
                    source_url=url,
                    final_url=str(response.url),
                    http_status=status,
                    raw_bytes=raw_bytes,
                )
            finally:
                await request_context.dispose()

    def _decode_list_response(self, raw_text: str) -> Any:
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            if self._accept_html_list_response(raw_text):
                return raw_text
            raise

    def _accept_html_list_response(self, raw_text: str) -> bool:
        if not self.html_list_response_enabled:
            return False
        return bool(re.search(r"<(?:!doctype\s+html|html|body|script|div|table)\b", raw_text, re.I))

    def _observe_list_page(
        self,
        *,
        page_num: int,
        raw_text: str,
        payload: Any,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not isinstance(payload, str):
            return None
        declared_total = self._parse_html_list_declared_total(raw_text)
        parsed_items = len(rows)
        blocked = self._html_list_blocked(raw_text)
        identity_valid = self._html_list_identity_valid(raw_text)
        if blocked:
            status = "blocked"
        elif not identity_valid:
            status = "identity-mismatch"
        elif parsed_items > 0:
            status = "items"
        elif declared_total == 0 or self._html_list_empty(raw_text):
            status = "empty"
        elif declared_total is not None and declared_total > 0:
            status = "positive-total-without-items"
        else:
            status = "no-list-items"
        return {
            "page": page_num,
            "status": status,
            "declared_total": declared_total,
            "parsed_items": parsed_items,
            "identity_valid": identity_valid,
            "blocked": blocked,
        }

    def _html_list_identity_valid(self, raw_text: str) -> bool:
        markers = self.html_list_identity_markers
        return not markers or any(marker in raw_text for marker in markers)

    def _html_list_blocked(self, raw_text: str) -> bool:
        return any(marker in raw_text for marker in self.html_list_block_markers)

    def _html_list_empty(self, raw_text: str) -> bool:
        return any(marker in raw_text for marker in self.html_list_empty_markers)

    def _parse_html_list_declared_total(self, raw_text: str) -> int | None:
        for pattern in (
            r"\bdataTotal\s*:\s*(\d+)\s*(?:\|\|\s*0)?",
            r"\btotal\s*:\s*(\d+)\b",
            r"共\s*(\d+)\s*条",
        ):
            match = re.search(pattern, raw_text)
            if match:
                return int(match.group(1))
        return None

    def _declared_list_counts(
        self,
        *,
        payload: Any,
        observation: dict[str, Any] | None,
    ) -> tuple[int | None, int | None]:
        declared_total: int | None = None
        declared_pages: int | None = None
        if observation is not None:
            raw_total = observation.get("declared_total")
            if isinstance(raw_total, int) and not isinstance(raw_total, bool) and raw_total >= 0:
                declared_total = raw_total
        if isinstance(payload, dict):
            candidates = [payload]
            for key in ("data", "extra", "page"):
                nested = payload.get(key)
                if isinstance(nested, dict):
                    candidates.append(nested)
            for candidate in candidates:
                if declared_total is None:
                    for key in ("totalElements", "total", "totalCount", "recordsTotal"):
                        value = candidate.get(key)
                        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                            declared_total = value
                            break
                if declared_pages is None:
                    for key in ("totalPages", "pages", "pageCount"):
                        value = candidate.get(key)
                        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                            declared_pages = value
                            break
        if declared_pages is None and declared_total is not None:
            declared_pages = max(1, (declared_total + self.page_size - 1) // self.page_size)
        return declared_total, declared_pages

    def _post_json(self, url: str, payload: dict[str, Any]) -> HttpFetchedText:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "Mozilla/5.0",
            **dict(self.list_headers),
        }
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(
            request,
            timeout=self.timeout,
            context=self._ssl_context_for_url(url),
        ) as response:
            raw_bytes = response.read()
            return HttpFetchedText(
                raw_bytes.decode("utf-8"),
                source_url=url,
                final_url=str(response.geturl()),
                http_status=int(getattr(response, "status", response.getcode())),
                raw_bytes=raw_bytes,
            )

    def _get_text(self, url: str) -> HttpFetchedText:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", **dict(self.list_headers)},
            method="GET",
        )
        with urllib.request.urlopen(
            request,
            timeout=self.timeout,
            context=self._ssl_context_for_url(url),
        ) as response:
            raw_bytes = response.read()
            charset = response.headers.get_content_charset() if response.headers else None
            return HttpFetchedText(
                raw_bytes.decode(charset or "utf-8", errors="replace"),
                source_url=url,
                final_url=str(response.geturl()),
                http_status=int(getattr(response, "status", response.getcode())),
                raw_bytes=raw_bytes,
            )

    def _ssl_context_for_url(self, url: str):
        if not url.startswith("https://"):
            return None
        context = ssl.create_default_context()
        if urllib.parse.urlparse(url).hostname == "new.gduaee.com":
            context.set_ciphers("DEFAULT:@SECLEVEL=1")
        return context

    def _list_payload(self, page_num: int) -> dict[str, Any]:
        raise NotImplementedError

    def _contract_required_query_filters(self) -> dict[str, Any]:
        requirement = get_source_business_requirement(
            self.source_id,
            "listing",
            self.business_id,
        )
        return {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in requirement.required_query_filters.items()
        }

    def _extract_rows(self, payload: Any) -> list[dict[str, Any]]:
        if self.rows_path is not None:
            value = payload
            for key in self.rows_path:
                if not isinstance(value, dict):
                    raise _DeclaredRowsPathSchemaError(
                        "declared-rows-path-schema-error: "
                        f"path={'.'.join(self.rows_path)} parent-not-object key={key}"
                    )
                if key not in value:
                    raise _DeclaredRowsPathSchemaError(
                        "declared-rows-path-schema-error: "
                        f"path={'.'.join(self.rows_path)} missing-key={key}"
                    )
                value = value[key]
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            raise _DeclaredRowsPathSchemaError(
                "declared-rows-path-schema-error: "
                f"path={'.'.join(self.rows_path)} expected-list got={type(value).__name__}"
            )
        return self._extract_rows_without_path(payload)

    def _extract_rows_without_path(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("records", "list", "rows", "dataList", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        data = payload.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            return self._extract_rows_without_path(data)
        return []

    @staticmethod
    def _first_text(row: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    def _normalize_page_url(self, page_url: str, *, row: dict[str, Any] | None = None) -> str:
        if not page_url:
            return ""
        return urllib.parse.urljoin(self.list_api_url, page_url)

    def _page_signature(self, rows: list[dict[str, Any]]) -> tuple[Any, ...]:
        signature = []
        schema = self.candidate_schema
        for row in rows:
            row_id = self._first_text(row, *schema.id_fields)
            if row_id:
                signature.append(("id", row_id))
            else:
                signature.append(("row", json.dumps(row, ensure_ascii=False, sort_keys=True)))
        return tuple(signature)

    @staticmethod
    def _safe_path_component(value: str) -> str:
        parts = []
        for part in re.split(r"[\\/]+", value):
            if part in ("", ".", ".."):
                continue
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", part).strip("._")
            if safe:
                parts.append(safe)
        return "__".join(parts) or "run"

    def _append_decision(self, evidence_dir: str, payload: dict[str, Any]) -> None:
        os.makedirs(evidence_dir, exist_ok=True)
        enriched = {
            "task_id": self.task_id,
            "source_id": self.source_id,
            "business_id": self.business_id,
            **payload,
        }
        with open(os.path.join(evidence_dir, "candidates.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(enriched, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _write_text(path: str, text: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    @staticmethod
    def _write_json(path: str, payload: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)

    def _write_invalid_shell_evidence_if_needed(
        self,
        *,
        candidate: _ListingCandidate,
        html_text: str,
    ) -> None:
        if "__jsl_clearance_s" not in str(html_text or ""):
            return
        evidence = {
            "schema_version": 1,
            "page_kind": "invalid_shell",
            "source_url_hash": self._sha256_text(candidate.page_url),
            "final_url_hash": self._sha256_text(candidate.page_url),
            "content_sha256": self._sha256_text(html_text),
            "identity_hints": {
                "project_code_hash": self._sha256_text(candidate.project_code),
                "project_name_hash": self._sha256_text(candidate.project_name),
            },
        }
        self._write_json(f"{candidate.html_path}.peap-evidence.json", evidence)

    @staticmethod
    def _sha256_text(value: str) -> str:
        return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

    def _notify_item_saved(self, candidate: _ListingCandidate) -> None:
        callback = self.item_saved_callback
        if callback is None:
            return
        callback(
            {
                "task_id": self.task_id,
                "source_id": self.source_id,
                "business_id": self.business_id,
                "archive_path": candidate.html_path,
                "source_file": candidate.html_path,
                "page_url": candidate.page_url,
                "project_code": candidate.project_code,
                "project_name": candidate.project_name,
                "listing_date": candidate.disclosure_start.isoformat()
                if candidate.disclosure_start
                else "",
            }
        )


class ShandongEquityTransferDownloader(_BaseListingExchangeDownloader):
    source_id = "shandong"
    business_id = "equity_transfer"
    output_type = TYPE_EQUITY_TRANSFER
    manifest_list_endpoint = "/projlist/xmpd/yqgq"
    manifest_detail_route = "/proj/tc/"
    list_api_url = "http://www.sdcqjy.com/projlist/xmpd/yqgq"
    list_method = "GET"
    html_list_response_enabled = True
    html_list_identity_markers = ("SPREC山东产权交易中心", "山东产权交易中心", "山东产权交易")
    html_list_block_markers = ("__jsl_clearance_s", "JavaScript required", "访问验证")
    html_list_empty_markers = ('class="no-data"', "class='no-data'", "暂无数据")
    candidate_schema = _CandidateSchema(
        id_fields=("id", "itemId", "projectId", "xmid", "guid"),
        date_fields=("disclosure_start", "startDate", "endDate", "publishDate", "listingDate"),
        url_fields=("page_url", "url", "detailUrl", "href", "link"),
        code_fields=("project_code", "code", "projectCode", "xmbh", "projectNo"),
        name_fields=("project_name", "name", "title", "projectName", "xmmc"),
    )

    def _list_payload(self, page_num: int) -> dict[str, Any]:
        return {
            "path": "yqgq",
            **self._contract_required_query_filters(),
            "pageNum": page_num,
            "pageSize": self.page_size,
        }

    def _extract_rows(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, str):
            return super()._extract_rows(payload)
        rows: list[dict[str, Any]] = []
        for match in re.finditer(r"linkToDetail\((\{.*?\})\)", payload, flags=re.S):
            raw_json = html.unescape(match.group(1))
            try:
                row = json.loads(raw_json)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(
                    {
                        **row,
                        "project_code": self._first_text(row, "code"),
                        "project_name": self._first_text(row, "name"),
                        "page_url": f"http://www.sdcqjy.com/proj/tc/{self._first_text(row, 'id')}",
                    }
                )
        return rows


class ShandongCapitalIncreaseDownloader(ShandongEquityTransferDownloader):
    business_id = "capital_increase"
    output_type = TYPE_CAPITAL_INCREASE
    manifest_list_endpoint = "/projlist/xmpd/zrzz"
    list_api_url = "http://www.sdcqjy.com/projlist/xmpd/zrzz"

    def _list_payload(self, page_num: int) -> dict[str, Any]:
        payload = super()._list_payload(page_num)
        payload["path"] = "zrzz"
        return payload


class GuangdongEquityTransferDownloader(_BaseListingExchangeDownloader):
    source_id = "guangdong"
    business_id = "equity_transfer"
    output_type = TYPE_EQUITY_TRANSFER
    # The Guangdong detail route is a hash-based SPA.  Its document commits
    # promptly, but third-party resources can keep DOMContentLoaded pending
    # indefinitely.  Waiting for commit/attached body lets the identity loop
    # decide when the rendered project is actually ready while preserving the
    # bounded navigation timeout.
    detail_navigation_wait_until = "commit"
    detail_body_wait_state = "attached"
    manifest_list_endpoint = "/si/prjs/equity/list"
    manifest_detail_route = "/xmzx.html#/equityDetail"
    manifest_detail_api_endpoint = "/si/prjs/equity/detail"
    list_api_url = "https://new.gduaee.com/si/prjs/equity/list"
    detail_api_url = "https://new.gduaee.com/si/prjs/equity/detail"
    list_headers = {"Referer": "https://new.gduaee.com/"}
    rows_path = ("data",)
    expected_fclass = "GQ"
    expected_cqlsgx = "GQ100101"
    accepted_detail_route_markers = (
        "/equitydetail",
        "/si/prjs/equity/detail",
    )
    candidate_schema = _CandidateSchema(
        id_fields=("XMID", "id", "itemId", "projectId", "xmid", "guid"),
        date_fields=("disclosure_start", "KSRQ", "publishDate", "listingDate", "startDate"),
        url_fields=("page_url", "url", "detailUrl", "href", "link"),
        code_fields=("XMBH", "project_code", "projectCode", "xmbh", "code", "projectNo"),
        name_fields=("XMMC", "project_name", "title", "projectName", "xmmc", "name"),
    )

    @staticmethod
    def _detail_xmid(page_url: str) -> str:
        parsed = urllib.parse.urlsplit(str(page_url or "").strip())
        query_text = parsed.query
        if "?" in parsed.fragment:
            query_text = f"{query_text}&{parsed.fragment.split('?', 1)[1]}".strip("&")
        values = urllib.parse.parse_qs(query_text).get("XMID", ())
        return str(values[0] if values else "").strip()

    @staticmethod
    def _payload_value(mapping: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            value = mapping.get(key)
            if value not in (None, "", [], {}):
                return value
        return ""

    @classmethod
    def _validate_detail_payload(
        cls,
        payload: object,
        *,
        xmid: str,
        expected_project_code: str,
        expected_project_name: str,
    ) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise RuntimeError("guangdong-detail-api-payload-not-object")
        try:
            code = int(payload.get("code"))
        except (TypeError, ValueError):
            raise RuntimeError("guangdong-detail-api-code-invalid") from None
        if code != 200:
            raise RuntimeError(f"guangdong-detail-api-code-{code}")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise RuntimeError("guangdong-detail-api-data-not-object")
        project = data.get("PLXmMap")
        if not isinstance(project, Mapping):
            raise RuntimeError("guangdong-detail-api-project-not-object")

        observed_xmid = str(cls._payload_value(project, "XMID") or "").strip()
        if xmid and observed_xmid != xmid:
            raise RuntimeError(
                "guangdong-detail-api-xmid-mismatch "
                f"expected={xmid} observed={observed_xmid or '<missing>'}"
            )
        observed_code = str(cls._payload_value(project, "XMBH") or "").strip()
        observed_name = str(cls._payload_value(project, "XMMC") or "").strip()
        if expected_project_code and cls._normalize_identity_text(observed_code) != cls._normalize_identity_text(
            expected_project_code
        ):
            raise RuntimeError(
                "guangdong-detail-api-project-code-mismatch "
                f"expected={expected_project_code} observed={observed_code or '<missing>'}"
            )
        if expected_project_name and cls._normalize_identity_text(observed_name) != cls._normalize_identity_text(
            expected_project_name
        ):
            raise RuntimeError(
                "guangdong-detail-api-project-name-mismatch "
                f"expected={expected_project_name} observed={observed_name or '<missing>'}"
            )
        expected_markers = {
            "equity_transfer": ("GQ", "GQ100101"),
            "capital_increase": ("1C", "1C100301"),
        }
        expected_fclass, expected_relation = expected_markers[cls.business_id]
        observed_fclass = str(cls._payload_value(project, "FCLASS") or "").strip()
        observed_relation = str(cls._payload_value(project, "CQLSGX") or "").strip()
        if observed_fclass != expected_fclass or (
            observed_relation and observed_relation != expected_relation
        ):
            raise RuntimeError(
                "guangdong-detail-api-business-mismatch "
                f"expected={expected_fclass}/{expected_relation} "
                f"observed={observed_fclass or '<missing>'}/{observed_relation or '<missing>'}"
            )
        return data

    @classmethod
    def _render_detail_payload_html(
        cls,
        *,
        payload: Mapping[str, Any],
        data: Mapping[str, Any],
        page_url: str,
        detail_api_url: str,
    ) -> str:
        project = data["PLXmMap"]
        assert isinstance(project, Mapping)
        sellers = [item for item in data.get("ZrfList", ()) if isinstance(item, Mapping)]
        annual_rows = [item for item in data.get("PLNsList", ()) if isinstance(item, Mapping)]
        latest_annual = max(
            annual_rows,
            key=lambda item: int(item.get("ND") or 0),
            default={},
        )

        seller_parts: list[str] = []
        for seller in sellers:
            name = str(cls._payload_value(seller, "ZRFMC") or "").strip()
            ratio = cls._payload_value(seller, "CCGQBL", "NZRCGQBL")
            if name:
                seller_parts.append(f"{name}({ratio}%)" if ratio not in (None, "") else name)
        seller = "，".join(seller_parts) or str(
            cls._payload_value(project, "MC", "BDQYMC") or ""
        ).strip()
        group = str(cls._payload_value(project, "SSJT") or "").strip()
        if not group:
            group = next(
                (
                    str(cls._payload_value(item, "SSJT") or "").strip()
                    for item in sellers
                    if str(cls._payload_value(item, "SSJT") or "").strip()
                ),
                "",
            )
        region_parts: list[str] = []
        for key in ("PROVINCEMC", "SZDQSMC", "CITYMC", "SZDQSQMC", "QXMC", "SZDQQXMC"):
            value = str(cls._payload_value(project, key) or "").strip()
            if value and value not in region_parts:
                region_parts.append(value)

        price = cls._payload_value(project, "ZRDJ", "NMUZJZE", "price")
        share_ratio = cls._payload_value(
            project,
            "NZRBL",
            "ZZZHGQBLSM",
            "CGBL",
        )
        rows = (
            ("项目编号", cls._payload_value(project, "XMBH")),
            ("项目名称", cls._payload_value(project, "XMMC", "MC", "BDQYMC")),
            ("挂牌开始日期", cls._payload_value(project, "PLKSRQ", "KSRQ", "SQSJ")),
            ("挂牌截止日期", cls._payload_value(project, "PLJSRQ", "JSRQ")),
            ("转让底价", price),
            ("所在地区", " ".join(region_parts)),
            ("所属行业", cls._payload_value(project, "SSHYMC")),
            ("转让方名称", seller),
            ("国家出资企业或主管部门名称", group),
            ("交易机构联系人", cls._payload_value(project, "XMFZRXM", "JYJG_LXR")),
            ("受托机构", "广东联合产权交易中心"),
            ("拟募集资金对应持股比例（%）", share_ratio),
            ("年度审计报告", cls._payload_value(latest_annual, "ND")),
            ("净利润", cls._payload_value(latest_annual, "JLR")),
            ("总资产", cls._payload_value(latest_annual, "ZCZJ")),
        )
        table_rows = "".join(
            f"<tr><th>{html.escape(str(label))}</th><td>{html.escape(str(value or ''))}</td></tr>"
            for label, value in rows
        )
        raw_payload = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )
        project_name = html.escape(str(cls._payload_value(project, "XMMC", "MC", "BDQYMC")))
        return (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<title>{project_name} - 广东联合产权交易中心</title>"
            "<meta name=\"peap-source-transport\" content=\"first-party-detail-api\">"
            f"<meta name=\"peap-page-url\" content=\"{html.escape(page_url, quote=True)}\">"
            f"<meta name=\"peap-detail-api-url\" content=\"{html.escape(detail_api_url, quote=True)}\">"
            "</head><body><main><h1>广东联合产权交易中心</h1>"
            f"<p>{html.escape(cls.manifest_detail_route)}</p><table>{table_rows}</table>"
            f"<script id=\"peap-listing-detail\" type=\"application/json\">{raw_payload}</script>"
            "</main></body></html>"
        )

    async def _fetch_rendered_html(
        self,
        *,
        page,
        page_url: str,
        expected_project_code: str = "",
        expected_project_name: str = "",
    ) -> tuple[str, int]:
        try:
            return await super()._fetch_rendered_html(
                page=page,
                page_url=page_url,
                expected_project_code=expected_project_code,
                expected_project_name=expected_project_name,
            )
        except Exception as render_error:  # noqa: BLE001
            xmid = self._detail_xmid(page_url)
            if not xmid:
                raise RuntimeError(f"guangdong-detail-xmid-missing: {page_url}") from render_error
            try:
                response = await asyncio.to_thread(
                    self._post_json,
                    self.detail_api_url,
                    {"XMID": int(xmid) if xmid.isdigit() else xmid, "SQJL": 1},
                )
                payload = json.loads(response)
                data = self._validate_detail_payload(
                    payload,
                    xmid=xmid,
                    expected_project_code=expected_project_code,
                    expected_project_name=expected_project_name,
                )
            except Exception as api_error:  # noqa: BLE001
                raise RuntimeError(
                    f"guangdong-detail-render-and-api-failed: render={render_error}; api={api_error}"
                ) from api_error
            raw_bytes = getattr(response, "raw_bytes", None) or str(response).encode("utf-8")
            self._detail_payload_cache[xmid] = {
                "detail_api_url": self.detail_api_url,
                "detail_api_final_url": str(
                    getattr(response, "final_url", None) or self.detail_api_url
                ),
                "detail_payload": payload,
                "detail_payload_content_sha256": f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}",
                "detail_payload_content_bytes": len(raw_bytes),
                "detail_transport": "first_party_detail_api",
            }
            return (
                self._render_detail_payload_html(
                    payload=payload,
                    data=data,
                    page_url=page_url,
                    detail_api_url=self.detail_api_url,
                ),
                int(response.http_status or 200),
            )

    def _cached_detail_payload(self, candidate: _ListingCandidate) -> Mapping[str, Any]:
        xmid = self._detail_xmid(candidate.page_url)
        if xmid and xmid in self._detail_payload_cache:
            return self._detail_payload_cache[xmid]
        return self._detail_payload_cache.get(candidate.item_id) or {}

    def _detail_sidecar_extras(self, candidate: _ListingCandidate) -> dict[str, Any]:
        return dict(self._cached_detail_payload(candidate))

    def _detail_evidence_source_url(self, candidate: _ListingCandidate) -> str:
        cached = self._cached_detail_payload(candidate)
        return str(cached.get("detail_api_url") or candidate.page_url)

    def _list_payload(self, page_num: int) -> dict[str, Any]:
        return {
            "pageNo": page_num,
            "pageSize": self.page_size,
            **self._contract_required_query_filters(),
            "QueryFlag": 4,
            "SQJL": 1,
        }

    def _candidate_business_filter_reason(
        self,
        *,
        row: dict[str, Any],
        explicit_page_url: str,
    ) -> str:
        fclass = self._first_text(row, "FCLASS")
        cqlsgx = self._first_text(row, "CQLSGX")
        if fclass != self.expected_fclass or cqlsgx != self.expected_cqlsgx:
            return (
                "guangdong-business-markers-mismatch: "
                f"expected_fclass={self.expected_fclass} actual_fclass={fclass or '<missing>'} "
                f"expected_cqlsgx={self.expected_cqlsgx} actual_cqlsgx={cqlsgx or '<missing>'}"
            )

        if explicit_page_url:
            normalized_route = urllib.parse.unquote(explicit_page_url).lower()
            if not any(
                marker in normalized_route for marker in self.accepted_detail_route_markers
            ):
                return (
                    "guangdong-detail-route-mismatch: "
                    f"expected_one_of={','.join(self.accepted_detail_route_markers)}"
                )
        return ""

    def _normalize_page_url(self, page_url: str, *, row: dict[str, Any] | None = None) -> str:
        if page_url:
            return super()._normalize_page_url(page_url, row=row)
        xmid = self._first_text({} if row is None else row, "XMID")
        if not xmid:
            return ""
        return f"https://new.gduaee.com/xmzx.html#/equityDetail?XMID={urllib.parse.quote(xmid)}"


class GuangdongCapitalIncreaseDownloader(GuangdongEquityTransferDownloader):
    business_id = "capital_increase"
    output_type = TYPE_CAPITAL_INCREASE
    manifest_list_endpoint = "/si/prjs/capitalincrease/list"
    manifest_detail_route = "/xmzx.html#/capital_increaseDetail"
    manifest_detail_api_endpoint = "/si/prjs/capitalincrease/detail"
    list_api_url = "https://new.gduaee.com/si/prjs/capitalincrease/list"
    detail_api_url = "https://new.gduaee.com/si/prjs/capitalincrease/detail"
    expected_fclass = "1C"
    expected_cqlsgx = "1C100301"
    accepted_detail_route_markers = (
        "/capital_increasedetail",
        "/si/prjs/capitalincrease/detail",
    )

    def _list_payload(self, page_num: int) -> dict[str, Any]:
        return {
            "pageNo": page_num,
            "pageSize": self.page_size,
            **self._contract_required_query_filters(),
            "QueryFlag": 4,
            "SQJL": 1,
        }

    def _normalize_page_url(self, page_url: str, *, row: dict[str, Any] | None = None) -> str:
        if page_url:
            return super()._normalize_page_url(page_url, row=row)
        xmid = self._first_text({} if row is None else row, "XMID")
        if not xmid:
            return ""
        return (
            "https://new.gduaee.com/xmzx.html#/capital_increaseDetail"
            f"?XMID={urllib.parse.quote(xmid)}"
        )


class _ShenzhenListingDownloader(_BaseListingExchangeDownloader):
    source_id = "shenzhen"
    manifest_list_endpoint = "/api/v1/sotcbb/local/project/list"
    manifest_detail_route = "/project/detail"
    list_api_url = "https://www.sotcbb.com/api/v1/sotcbb/local/project/list"
    package_view_api_url = "https://www.sotcbb.com/cqjy-api/package/view"
    list_headers = {"Referer": "https://www.sotcbb.com/"}
    # The public detail shell streams third-party assets after the document has
    # committed and keeps body hidden while its detail API fills the page.
    detail_navigation_wait_until = "commit"
    detail_body_wait_state = "attached"
    rows_path = ("data", "content")
    candidate_schema = _CandidateSchema(
        id_fields=("contentId", "id", "itemId", "projectId", "xmid", "guid"),
        date_fields=("disclosure_start", "registerFrom", "releaseTime", "publishDate", "listingDate", "startDate"),
        url_fields=("page_url", "linkTo", "url", "detailUrl", "href", "link"),
        code_fields=("projectNo", "project_code", "projectCode", "xmbh", "code", "contentId"),
        name_fields=("title", "project_name", "projectName", "xmmc", "name"),
    )
    @property
    def channel_ids(self) -> tuple[str, ...]:
        return tuple(self._contract_required_query_filters().get("channelIds") or ())

    @property
    def target_column_ids(self) -> tuple[str, ...]:
        return tuple(self._contract_required_query_filters().get("targetColumnIds") or ())

    def _list_payload(self, page_num: int) -> dict[str, Any]:
        return {
            "pageNum": page_num,
            "pageSize": self.page_size,
            **self._contract_required_query_filters(),
            "dataType": 1,
        }

    def _detail_sidecar_extras(self, candidate: _ListingCandidate) -> dict[str, Any]:
        object_id = self._package_view_object_id(candidate)
        if not object_id:
            return {}
        api_url = f"{self.package_view_api_url}?id={urllib.parse.quote(object_id, safe='')}"
        try:
            raw_text = self._post_json(api_url, {})
            payload = json.loads(raw_text)
        except Exception as exc:  # noqa: BLE001
            return {
                "detail_api_url": api_url,
                "detail_payload_error": f"package-view-fetch-failed: {exc}",
            }
        if not isinstance(payload, dict):
            return {
                "detail_api_url": api_url,
                "detail_payload_error": f"package-view-invalid-schema: {type(payload).__name__}",
            }
        return {"detail_api_url": api_url, "detail_payload": payload}

    def _package_view_object_id(self, candidate: _ListingCandidate) -> str:
        page_url = str(candidate.page_url or "").strip()
        parsed = urllib.parse.urlparse(page_url)
        if "bdDetail" in parsed.path:
            query = urllib.parse.parse_qs(parsed.query)
            for value in query.get("contentId", ()):
                text = str(value or "").strip()
                if text:
                    return text
        row = candidate.row if isinstance(candidate.row, dict) else {}
        if str(row.get("isObject") or "").strip() == "1":
            return self._first_text(row, "objectId")
        return ""

    def _normalize_page_url(self, page_url: str, *, row: dict[str, Any] | None = None) -> str:
        if page_url:
            return super()._normalize_page_url(page_url, row=row)
        raw_row = {} if row is None else row
        content_id = self._first_text(raw_row, "contentId")
        if not content_id:
            return ""
        if str(raw_row.get("isObject") or "").strip() == "1":
            object_id = self._first_text(raw_row, "objectId")
            channel_id = self._first_text(raw_row, "channelId") or self._first_text(raw_row, "targetColumnId")
            if object_id and channel_id:
                return (
                    "https://www.sotcbb.com/bdDetail.htm?"
                    f"contentId={urllib.parse.quote(object_id)}"
                    f"&channelId={urllib.parse.quote(channel_id)}"
                    f"&id={urllib.parse.quote(content_id)}"
                )
        return f"https://www.sotcbb.com/projectDetails.htm?siteId=192&contentId={urllib.parse.quote(content_id)}"


class ShenzhenEquityTransferDownloader(_ShenzhenListingDownloader):
    business_id = "equity_transfer"
    output_type = TYPE_EQUITY_TRANSFER


class ShenzhenCapitalIncreaseDownloader(_ShenzhenListingDownloader):
    business_id = "capital_increase"
    output_type = TYPE_CAPITAL_INCREASE


__all__ = [
    "GuangdongCapitalIncreaseDownloader",
    "GuangdongEquityTransferDownloader",
    "ShandongCapitalIncreaseDownloader",
    "ShandongEquityTransferDownloader",
    "ShenzhenCapitalIncreaseDownloader",
    "ShenzhenEquityTransferDownloader",
]
