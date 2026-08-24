"""CBEX physical asset downloader (fwtd/jtysgj/sb)."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import random
import re
import shutil
import time
import urllib.parse
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
    parse_loose_date,
    record_downloaded_target,
    reserve_download_target,
    runtime_task_id,
    successful_http_evidence,
)
from .discovery_evidence import DiscoveryEvidenceError, DiscoveryTaskEvidence

BASE_URL = "https://www.cbex.com.cn"
WARMUP_URL = "https://www.cbex.com.cn/xm/zczr/"
API_URL = "https://www.cbex.com.cn/onss-api/jsonp/project/search"

ASSET_TYPES: Dict[str, str] = {
    "house": "房屋土地",
    "transport": "交通运输工具",
    "equipment": "设备",
}
ASSET_REFERERS: Dict[str, str] = {
    "house": "https://www.cbex.com.cn/xm/zczr/fwtd/",
    "transport": "https://www.cbex.com.cn/xm/zczr/jtysgj/",
    "equipment": "https://www.cbex.com.cn/xm/zczr/sb/",
}

REFERER_CQZR = "https://www.cbex.com.cn/xm/cqzr/"
REFERER_QYZZ = "https://www.cbex.com.cn/xm/qyzz/"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en-US','en'] });
window.chrome = window.chrome || { runtime: {} };
"""

CHALLENGE_HINTS = ("__jsl_clearance_s", "location.href=location.pathname+location.search")

TAG_ASSET_ATTRS = (
    ("link", "href"),
    ("img", "src"),
    ("img", "data-original"),
    ("img", "data-src"),
    ("source", "src"),
    ("video", "poster"),
)

DISCLOSURE_START_PATTERNS = (
    r"(?:信息披露起始日期|挂牌起始日期|挂牌开始日期|披露起始日期|披露开始日期|披露起止日期|挂牌起止日期)\s*[:：]?\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)",
    r"(?:披露日期|挂牌日期)\s*[:：]?\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)",
    r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)\s*(?:至|到|-|—|~|～)\s*20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?",
)


@dataclass
class _Candidate:
    uid: str
    code: str
    url: str
    html_path: str
    row: Dict[str, Any]


@dataclass(frozen=True)
class _ListSource:
    label: str
    business_type: str
    referer: str
    asset_type: Optional[str] = None


def _cbex_list_endpoint() -> str:
    return get_source_business_requirement("cbex", "listing", "physical_asset").list_endpoint


def _cbex_list_endpoint_for_business(business_id: str) -> str:
    return get_source_business_requirement(
        "cbex",
        "listing",
        business_id,
    ).list_endpoint


def _cbex_list_sources(business_id: str) -> List[_ListSource]:
    referer_by_business_type = {"JC": REFERER_CQZR, "GZ": REFERER_QYZZ}
    return [
        _ListSource(
            label=str(spec["label"]),
            business_type=str(spec["businessType"]),
            referer=ASSET_REFERERS[str(spec["assetType"])]
            if "assetType" in spec
            else referer_by_business_type[str(spec["businessType"])],
            asset_type=str(spec["assetType"]) if "assetType" in spec else None,
        )
        for spec in get_source_business_requirement("cbex", "listing", business_id).list_query_specs
    ]


def _cbex_include_pre_disclosure(business_id: str) -> Optional[bool]:
    specs = get_source_business_requirement("cbex", "listing", business_id).list_query_specs
    if not specs or "include_pre_disclosure" not in specs[0]:
        return None
    return bool(specs[0]["include_pre_disclosure"])


def _cbex_url_query(url: str, *, expected_endpoint: str, field: str) -> Dict[str, str]:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    expected = urllib.parse.urlsplit(expected_endpoint)
    if (
        parsed.scheme.lower() != expected.scheme.lower()
        or parsed.netloc.lower() != expected.netloc.lower()
        or parsed.path != expected.path
    ):
        raise DiscoveryEvidenceError(
            f"CBEX list URL {field} does not match authoritative API endpoint: "
            f"expected={expected_endpoint!r} actual={url!r}"
        )
    parsed_query = urllib.parse.parse_qs(
        parsed.query,
        keep_blank_values=True,
    )
    duplicate_keys = sorted(
        key for key, values in parsed_query.items() if len(values) != 1
    )
    if duplicate_keys:
        raise DiscoveryEvidenceError(
            f"CBEX list URL {field} has duplicate query parameters: {duplicate_keys}"
        )
    return {key: values[0] for key, values in parsed_query.items()}


class _ChallengeError(RuntimeError):
    pass


class _CbexListStructureError(ValueError):
    pass


def _is_challenge_html(text: str) -> bool:
    low = text.lower()
    return any(h in low for h in CHALLENGE_HINTS)


def _skip_asset_url(v: str) -> bool:
    t = v.strip().lower()
    return (not t) or t.startswith(("#", "data:", "javascript:", "mailto:", "tel:", "blob:"))


def _read_json_object(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _has_blocking_evidence_sidecar(html_path: str) -> bool:
    evidence_path = f"{html_path}.peap-evidence.json"
    if not os.path.exists(evidence_path):
        return False
    evidence = _read_json_object(evidence_path)
    if evidence is None:
        return True
    return evidence.get("page_kind") == "invalid_shell"


def _resume_status_path(html_path: str) -> str:
    return f"{html_path}.peap-save-status.json"


class CbexPhysicalAssetDownloader:
    manifest_list_endpoint = _cbex_list_endpoint()
    manifest_detail_route = "/xm/zczr/"
    manifest_date_field_candidates = ("disclosure_start",)

    def __init__(
        self,
        *,
        html_root: str,
        page_size: int = 15,
        max_pages: Optional[int] = None,
        concurrency: int = 2,
        resume: bool = False,
        timeout: int = 30,
        save_json: bool = False,
        output_type: str = TYPE_PHYSICAL_ASSET,
        list_sources: Optional[List[_ListSource]] = None,
        include_pre_disclosure: Optional[bool] = None,
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
        self.output_type = str(output_type or TYPE_PHYSICAL_ASSET)
        if list_sources is None:
            list_sources = _cbex_list_sources(business_id_key(self.output_type))
        self.list_sources = list(list_sources)
        self.include_pre_disclosure = include_pre_disclosure
        self.logger = logger or logging.getLogger("parser_v2")
        self.item_saved_callback = item_saved_callback
        self.run_id = str(run_id or "").strip() or f"run-{time.time_ns()}"
        self._render_timeout_ms = max(90, self.timeout) * 1000

    def run(
        self,
        *,
        start_date: Optional[str],
        end_date: Optional[str],
        list_only: bool = False,
        prefetched_candidates: Optional[List[Dict[str, Any]]] = None,
    ) -> DownloadSummary:
        start = parse_loose_date(start_date) if start_date else None
        end = parse_loose_date(end_date) if end_date else None
        if start_date and start is None:
            raise ValueError(f"invalid start-date: {start_date!r}")
        if end_date and end is None:
            raise ValueError(f"invalid end-date: {end_date!r}")
        if start and end and start > end:
            raise ValueError("start-date is after end-date")

        outdir = os.path.abspath(self.html_root)
        os.makedirs(outdir, exist_ok=True)
        s = DownloadSummary()
        asyncio.run(
            self._run_async(
                summary=s,
                outdir=outdir,
                start=start,
                end=end,
                list_only=bool(list_only),
                prefetched_candidates=prefetched_candidates,
            )
        )
        return s

    async def _run_async(
        self,
        *,
        summary: DownloadSummary,
        outdir: str,
        start: Optional[dt.date],
        end: Optional[dt.date],
        list_only: bool,
        prefetched_candidates: Optional[List[Dict[str, Any]]],
    ) -> None:
        from playwright.async_api import TimeoutError as PWTimeout
        from playwright.async_api import async_playwright

        seen: Set[str] = set()
        cands: List[_Candidate] = []

        async with async_playwright() as pw:
            browser = await launch_chromium_browser(
                pw,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(user_agent=REQUEST_HEADERS["User-Agent"], locale="zh-CN", timezone_id="Asia/Shanghai", ignore_https_errors=True)
            await context.add_init_script(STEALTH_JS)
            page = await context.new_page()
            try:
                await self._warmup(page)
                if prefetched_candidates is None:
                    await self._collect_list_sources_with_evidence(
                        context=context,
                        page=page,
                        outdir=outdir,
                        summary=summary,
                        seen=seen,
                        cands=cands,
                        start=start,
                        end=end,
                    )
                else:
                    self.logger.info(
                        "Use prefetched CBEX candidates: type=%s entries=%s",
                        self.output_type,
                        len(prefetched_candidates),
                    )
                    self._prefetched_to_candidates(
                        prefetched_candidates=prefetched_candidates,
                        outdir=outdir,
                        summary=summary,
                        seen=seen,
                        cands=cands,
                        start=start,
                        end=end,
                    )
            except Exception as exc:  # noqa: BLE001
                summary.typed_errors.append(
                    execute_failed_error(
                        source_id="cbex",
                        task_id=runtime_task_id("cbex", self.output_type),
                        raw_reason=f"cbex-list-failed: {exc}",
                    )
                )
            finally:
                await page.close()

            summary.detail_candidates = len(cands)
            if list_only:
                self.logger.info("List-only mode: skip detail download for type=%s candidates=%s", self.output_type, summary.detail_candidates)
            elif cands:
                sem = asyncio.Semaphore(self.concurrency)
                done = 0
                t0 = time.monotonic()
                lock = asyncio.Lock()
                progress_log = ProgressLogThrottle(total=len(cands))

                async def worker(c: _Candidate):
                    nonlocal done
                    async with sem:
                        await self._process_candidate(context=context, candidate=c, summary=summary, start=start, end=end, timeout_cls=PWTimeout)
                    async with lock:
                        done += 1
                        if progress_log.should_log(done):
                            elapsed = max(time.monotonic() - t0, 0.001)
                            self.logger.info(
                                "Detail progress: %s/%s saved=%s unavailable_skipped=%s errors=%s speed=%.2f/min",
                                done,
                                len(cands),
                                summary.saved,
                                summary.skipped_by_detail_unavailable,
                                len(summary.typed_errors),
                                done / elapsed * 60,
                            )

                await asyncio.gather(*[asyncio.create_task(worker(c)) for c in cands])
            else:
                self.logger.info("No candidate details to download.")

            list_accounted = (
                summary.skipped_by_list_date
                + summary.skipped_by_resume
                + summary.skipped_by_duplicate
                + summary.skipped_by_business_filter
                + summary.detail_candidates
            )
            detail_accounted = detail_accounted_count(summary)
            summary.list_unaccounted = summary.listed_items - list_accounted
            summary.detail_unaccounted = 0 if list_only else (summary.detail_candidates - detail_accounted)

            await context.close()
            await browser.close()

    async def _warmup(self, page) -> None:
        await page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=self._render_timeout_ms)
        await page.wait_for_timeout(6000)
        if _is_challenge_html(await page.content()):
            await page.reload(wait_until="domcontentloaded", timeout=self._render_timeout_ms)
            await page.wait_for_timeout(5000)

    async def _collect_list_sources_with_evidence(
        self,
        *,
        context,
        page,
        outdir: str,
        summary: DownloadSummary,
        seen: Set[str],
        cands: List[_Candidate],
        start: Optional[dt.date],
        end: Optional[dt.date],
    ) -> bool:
        task_id = runtime_task_id("cbex", self.output_type)
        business_id = business_id_key(self.output_type)
        authoritative_sources = tuple(_cbex_list_sources(business_id))
        configured_sources = tuple(self.list_sources)
        authoritative_pre_disclosure = _cbex_include_pre_disclosure(business_id)
        expected_query_ids = tuple(
            self._cbex_query_id(source) for source in authoritative_sources
        )
        task_evidence = DiscoveryTaskEvidence(
            root=outdir,
            source_id="cbex",
            task_id=task_id,
            run_id=self.run_id,
            expected_query_ids=expected_query_ids,
        )
        if (
            configured_sources != authoritative_sources
            or self.include_pre_disclosure != authoritative_pre_disclosure
        ):
            summary.typed_errors.append(
                list_failed_error(
                    source_id="cbex",
                    task_id=task_id,
                    raw_reason=(
                        "cbex-list-contract-mismatch: configured sources and "
                        "pre-disclosure scope must exactly match "
                        "source_business_contract; "
                        f"configured_sources={configured_sources!r} "
                        f"authoritative_sources={authoritative_sources!r} "
                        f"configured_pre_disclosure={self.include_pre_disclosure!r} "
                        "authoritative_pre_disclosure="
                        f"{authoritative_pre_disclosure!r}"
                    ),
                )
            )
            task_evidence.fail(
                termination_reason="authoritative_config_mismatch",
                details={
                    "configured_source_count": len(configured_sources),
                    "authoritative_source_count": len(authoritative_sources),
                    "configured_pre_disclosure": self.include_pre_disclosure,
                    "authoritative_pre_disclosure": authoritative_pre_disclosure,
                },
                missing_query_ids=expected_query_ids,
            )
            summary.discovery_task_manifest = task_evidence.manifest_reference()
            return False

        staged_seen = set(seen)
        staged_candidates: List[_Candidate] = []
        list_state = {
            "listed_items": summary.listed_items,
            "skipped_by_list_date": summary.skipped_by_list_date,
            "skipped_by_resume": summary.skipped_by_resume,
            "skipped_by_duplicate": summary.skipped_by_duplicate,
            "skipped_by_business_filter": summary.skipped_by_business_filter,
            "candidate_entries": len(summary.candidate_entries),
            "candidate_dates": len(summary.candidate_dates),
            "typed_errors": len(summary.typed_errors),
        }

        def rollback_staged_discovery() -> None:
            summary.listed_items = int(list_state["listed_items"])
            summary.skipped_by_list_date = int(list_state["skipped_by_list_date"])
            summary.skipped_by_resume = int(list_state["skipped_by_resume"])
            summary.skipped_by_duplicate = int(list_state["skipped_by_duplicate"])
            summary.skipped_by_business_filter = int(
                list_state["skipped_by_business_filter"]
            )
            del summary.candidate_entries[int(list_state["candidate_entries"]):]
            del summary.candidate_dates[int(list_state["candidate_dates"]):]

        with task_evidence:
            try:
                for source_index, source in enumerate(self.list_sources):
                    query_complete = await self._collect_by_source(
                        context=context,
                        page=page,
                        source=source,
                        outdir=outdir,
                        summary=summary,
                        seen=staged_seen,
                        cands=staged_candidates,
                        start=start,
                        end=end,
                        task_evidence=task_evidence,
                    )
                    if not query_complete:
                        rollback_staged_discovery()
                        task_evidence.fail(
                            termination_reason="query_failed",
                            missing_query_ids=expected_query_ids[source_index + 1 :],
                            invalid_query_ids=(self._cbex_query_id(source),),
                        )
                        summary.discovery_task_manifest = (
                            task_evidence.manifest_reference()
                        )
                        return False
                candidate_error_count = len(summary.typed_errors) - int(
                    list_state["typed_errors"]
                )
                if candidate_error_count:
                    rollback_staged_discovery()
                    task_evidence.fail(
                        termination_reason="candidate_conversion_failed",
                        details={"candidate_error_count": candidate_error_count},
                    )
                    summary.discovery_task_manifest = (
                        task_evidence.manifest_reference()
                    )
                    return False
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
                            source_id="cbex",
                            task_id=task_id,
                            raw_reason=f"cbex-discovery-task-incomplete: {exc}",
                        )
                    )
                    summary.discovery_task_manifest = (
                        task_evidence.manifest_reference()
                    )
                    return False
            except Exception as exc:  # noqa: BLE001
                rollback_staged_discovery()
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="cbex",
                        task_id=task_id,
                        raw_reason=f"cbex-discovery-task-failed: {exc}",
                    )
                )
                task_evidence.fail(
                    termination_reason="exception",
                    details={"error": str(exc)},
                )
                summary.discovery_task_manifest = task_evidence.manifest_reference()
                return False

        seen.update(staged_seen)
        cands.extend(staged_candidates)
        return True

    async def _collect_by_source(
        self,
        *,
        context,
        page,
        source: _ListSource,
        outdir: str,
        summary: DownloadSummary,
        seen: Set[str],
        cands: List[_Candidate],
        start: Optional[dt.date],
        end: Optional[dt.date],
        task_evidence: Optional[DiscoveryTaskEvidence] = None,
    ) -> bool:
        if task_evidence is not None:
            return await self._collect_cbex_query(
                context=context,
                page=page,
                source=source,
                outdir=outdir,
                summary=summary,
                seen=seen,
                cands=cands,
                start=start,
                end=end,
                task_evidence=task_evidence,
            )

        task_id = runtime_task_id("cbex", self.output_type)
        candidate_entry_start = len(summary.candidate_entries)
        own_task = DiscoveryTaskEvidence(
            root=outdir,
            source_id="cbex",
            task_id=task_id,
            run_id=self.run_id,
            expected_query_ids=(self._cbex_query_id(source),),
        )
        with own_task:
            query_complete = await self._collect_cbex_query(
                context=context,
                page=page,
                source=source,
                outdir=outdir,
                summary=summary,
                seen=seen,
                cands=cands,
                start=start,
                end=end,
                task_evidence=own_task,
            )
            if not query_complete:
                own_task.fail(termination_reason="query_failed")
                summary.discovery_task_manifest = own_task.manifest_reference()
                return False
            try:
                own_task.complete(
                    candidate_entries=summary.candidate_entries[candidate_entry_start:]
                )
                summary.discovery_task_manifest = own_task.manifest_reference()
            except DiscoveryEvidenceError as exc:
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="cbex",
                        task_id=task_id,
                        raw_reason=f"cbex-discovery-task-incomplete: {exc}",
                    )
                )
                summary.discovery_task_manifest = own_task.manifest_reference()
                return False
        return True

    async def _collect_cbex_query(
        self,
        *,
        context,
        page,
        source: _ListSource,
        outdir: str,
        summary: DownloadSummary,
        seen: Set[str],
        cands: List[_Candidate],
        start: Optional[dt.date],
        end: Optional[dt.date],
        task_evidence: DiscoveryTaskEvidence,
    ) -> bool:
        task_id = runtime_task_id("cbex", self.output_type)
        query_evidence = task_evidence.query(
            self._cbex_query_id(source),
            authoritative_total=True,
            page_size=self.page_size,
        )
        collected_rows: List[Dict[str, Any]] = []
        with query_evidence:
            page_index = 1
            traversal_pages: Optional[int] = None
            first_declared_pages: Optional[int] = None
            termination_reason = "declared_pages_exhausted"
            while traversal_pages is None or page_index <= traversal_pages:
                try:
                    raw_response = await self._api_with_retry(
                        context=context,
                        page=page,
                        source=source,
                        page_index=page_index,
                    )
                except Exception as exc:  # noqa: BLE001
                    query_evidence.fail(
                        termination_reason="request_failed",
                        details={"page_index": page_index, "error": str(exc)},
                    )
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id="cbex",
                            task_id=task_id,
                            raw_reason=(
                                f"list-{source.label}-page-{page_index}-request-failed: {exc}"
                            ),
                        )
                    )
                    return False

                summary.pages_requested += 1
                try:
                    response = self._coerce_cbex_list_response(
                        raw_response,
                        source=source,
                        page_index=page_index,
                    )
                    query_evidence.capture_page(
                        page_index=page_index,
                        response=response,
                        body_format="jsonp",
                        request_metadata={
                            "method": "GET",
                            "source_label": source.label,
                            "business_type": source.business_type,
                            "asset_type": source.asset_type,
                            "page_index": page_index,
                            "page_size": self.page_size,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    query_evidence.fail(
                        termination_reason="transport_evidence_invalid",
                        details={"page_index": page_index, "error": str(exc)},
                    )
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id="cbex",
                            task_id=task_id,
                            raw_reason=f"list-{source.label}-transport-evidence-failed: {exc}",
                        )
                    )
                    return False

                try:
                    rows, declared_pages, declared_total = (
                        self._decode_cbex_list_response(response)
                    )
                    if (
                        first_declared_pages is not None
                        and declared_pages != first_declared_pages
                    ):
                        raise _CbexListStructureError(
                            "declared totalPage changed during traversal: "
                            f"{first_declared_pages} -> {declared_pages}"
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
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id="cbex",
                            task_id=task_id,
                            raw_reason=f"list-{source.label}-page-{page_index}-invalid: {exc}",
                        )
                    )
                    return False

                evidence_pages = declared_pages if declared_pages > 0 else None
                try:
                    query_evidence.complete_page(
                        page_index=page_index,
                        extracted_row_count=len(rows),
                        row_identity_values=self._cbex_page_identities(rows),
                        declared_total_items=declared_total,
                        declared_total_pages=evidence_pages,
                    )
                except DiscoveryEvidenceError as exc:
                    query_evidence.fail(
                        termination_reason="evidence_failed",
                        details={"page_index": page_index, "error": str(exc)},
                    )
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id="cbex",
                            task_id=task_id,
                            raw_reason=f"list-{source.label}-evidence-failed: {exc}",
                        )
                    )
                    return False

                if traversal_pages is None:
                    first_declared_pages = declared_pages
                    traversal_pages = max(1, declared_pages)
                    if declared_pages == 0:
                        termination_reason = "official_empty"
                    if self.max_pages is not None and traversal_pages > self.max_pages:
                        query_evidence.fail(
                            termination_reason="explicit_max_pages",
                            details={
                                "declared_total_pages": declared_pages,
                                "max_pages": self.max_pages,
                            },
                        )
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id="cbex",
                                task_id=task_id,
                                raw_reason=(
                                    "explicit max_pages truncates CBEX discovery: "
                                    f"declared_pages={declared_pages} "
                                    f"max_pages={self.max_pages}"
                                ),
                            )
                        )
                        return False

                collected_rows.extend(rows)
                self.logger.info(
                    "List evidence progress[%s]: page %s/%s rows=%s",
                    source.label,
                    page_index,
                    traversal_pages,
                    len(collected_rows),
                )
                page_index += 1
                if page_index <= traversal_pages:
                    await asyncio.sleep(0.25 + random.random() * 0.45)

            try:
                query_evidence.complete(termination_reason=termination_reason)
            except DiscoveryEvidenceError as exc:
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="cbex",
                        task_id=task_id,
                        raw_reason=f"list-{source.label}-coverage-failed: {exc}",
                    )
                )
                return False

        self._rows_to_candidates(
            rows=collected_rows,
            source=source,
            outdir=outdir,
            summary=summary,
            seen=seen,
            cands=cands,
            start=start,
            end=end,
        )
        return True

    @staticmethod
    def _cbex_query_id(source: _ListSource) -> str:
        return f"{source.label}-{source.business_type}-{source.asset_type or 'all'}"

    async def _api_with_retry(
        self,
        *,
        context,
        page,
        source: _ListSource,
        page_index: int,
    ) -> HttpFetchedText:
        last: Optional[Exception] = None
        total_attempts = 4
        max_retries = total_attempts - 1
        for attempt in range(1, total_attempts + 1):
            try:
                return await self._api_one(context=context, source=source, page_index=page_index)
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt >= total_attempts:
                    break
                self.logger.warning(
                    "List API retry %s/%s (%s p=%s): %s",
                    attempt,
                    max_retries,
                    source.label,
                    page_index,
                    exc,
                )
                await page.wait_for_timeout(int(1000 + random.random() * 1800))
                try:
                    await self._warmup(page)
                except Exception:
                    pass
        raise RuntimeError(f"list-api-failed {source.label} p={page_index}: {last}")

    async def _api_one(
        self,
        *,
        context,
        source: _ListSource,
        page_index: int,
    ) -> HttpFetchedText:
        ts = int(time.time() * 1000)
        callback = f"jQuery{random.randint(10**10, 10**12)}_{ts}"
        params = {
            "callback": callback,
            "fromPage": str(page_index),
            "pageSize": str(self.page_size),
            "businessType": source.business_type,
            "sortProperty": "disclosuretime",
            "sortDirection": "1",
            "mark": "xm",
            "csrftoken": "-799914037",
            "_": str(ts),
        }
        if source.asset_type:
            params["assetType"] = source.asset_type
        source_url = f"{API_URL}?{urllib.parse.urlencode(params)}"
        resp = await context.request.get(
            source_url,
            headers={**REQUEST_HEADERS, "Referer": source.referer},
            timeout=min(self.timeout, 20) * 1000,
        )
        raw_bytes = await resp.body()
        status = int(resp.status)
        if status >= 400:
            raise RuntimeError(f"api-http-{status}")
        return HttpFetchedText(
            raw_bytes.decode("utf-8", errors="replace"),
            source_url=source_url,
            final_url=str(getattr(resp, "url", None) or source_url),
            http_status=status,
            raw_bytes=raw_bytes,
        )

    def _coerce_cbex_list_response(
        self,
        response: object,
        *,
        source: _ListSource,
        page_index: int,
    ) -> HttpFetchedText:
        if not isinstance(response, HttpFetchedText):
            raise DiscoveryEvidenceError(
                "CBEX list transport must return HttpFetchedText"
            )
        expected_endpoint = _cbex_list_endpoint_for_business(
            business_id_key(self.output_type)
        )
        expected_scope = {
            "fromPage": str(page_index),
            "pageSize": str(self.page_size),
            "businessType": source.business_type,
        }
        source_query = _cbex_url_query(
            response.source_url,
            expected_endpoint=expected_endpoint,
            field="source_url",
        )
        final_query = _cbex_url_query(
            response.final_url,
            expected_endpoint=expected_endpoint,
            field="final_url",
        )
        for field, query in (("source_url", source_query), ("final_url", final_query)):
            for key, expected_value in expected_scope.items():
                if query.get(key) != expected_value:
                    raise DiscoveryEvidenceError(
                        f"CBEX list URL {field} does not preserve {key}: "
                        f"expected={expected_value!r} actual={query.get(key)!r}"
                    )
            if source.asset_type is None:
                if "assetType" in query:
                    raise DiscoveryEvidenceError(
                        f"CBEX list URL {field} unexpectedly narrows assetType: "
                        f"actual={query.get('assetType')!r}"
                    )
            elif query.get("assetType") != source.asset_type:
                raise DiscoveryEvidenceError(
                    f"CBEX list URL {field} does not preserve assetType: "
                    f"expected={source.asset_type!r} "
                    f"actual={query.get('assetType')!r}"
                )
        callback = source_query.get("callback")
        if not callback:
            raise DiscoveryEvidenceError(
                "CBEX list URL source_url requires one nonempty callback parameter"
            )
        if final_query.get("callback") != callback:
            raise DiscoveryEvidenceError(
                "CBEX list URL final_url does not preserve callback"
            )
        return response

    def _decode_cbex_list_response(
        self,
        response: HttpFetchedText,
    ) -> tuple[List[Dict[str, Any]], int, int]:
        raw_bytes = response.raw_bytes
        if not isinstance(raw_bytes, bytes):
            raise _CbexListStructureError(
                "CBEX list response requires original response bytes"
            )
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _CbexListStructureError(
                f"invalid UTF-8 JSONP response: {exc}"
            ) from exc
        if _is_challenge_html(text):
            raise _CbexListStructureError("challenge response")
        # CBEX currently prepends the standard anti-JSON-hijacking marker
        # ``/**/`` before the JSONP callback.  It is transport framing, not
        # part of the callback name, so accept that exact marker while
        # retaining strict callback/payload validation below.
        match = re.search(
            r"^\s*(?:/\*\*/\s*)?([A-Za-z_$][0-9A-Za-z_$]*)\s*\((.*)\)\s*;?\s*$",
            text,
            re.S,
        )
        if match is None:
            raise _CbexListStructureError("invalid JSONP wrapper")
        callback_values = urllib.parse.parse_qs(
            urllib.parse.urlsplit(response.source_url).query,
            keep_blank_values=True,
        ).get("callback", [])
        if len(callback_values) != 1 or not callback_values[0]:
            raise _CbexListStructureError(
                "source_url must contain exactly one nonempty JSONP callback"
            )
        if match.group(1) != callback_values[0]:
            raise _CbexListStructureError(
                "JSONP callback does not match source_url callback: "
                f"wrapper={match.group(1)!r} source_url={callback_values[0]!r}"
            )
        try:
            payload = json.loads(match.group(2))
        except json.JSONDecodeError as exc:
            raise _CbexListStructureError(f"invalid JSONP payload: {exc}") from exc
        if not isinstance(payload, dict):
            raise _CbexListStructureError("response root must be an object")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise _CbexListStructureError("response data must be an object")
        if "data" not in data:
            raise _CbexListStructureError("response data has no rows field")
        rows_raw = data.get("data")
        if rows_raw is None:
            rows_raw = []
        if not isinstance(rows_raw, list):
            raise _CbexListStructureError("response rows must be a list")
        if any(not isinstance(row, dict) for row in rows_raw):
            raise _CbexListStructureError("response rows must contain only objects")
        rows: List[Dict[str, Any]] = list(rows_raw)

        if "totalPage" not in data:
            raise _CbexListStructureError("declared totalPage is missing")
        declared_pages = self._cbex_nonnegative_int(
            data.get("totalPage"),
            field="declared totalPage",
        )
        if declared_pages == 0 and rows:
            raise _CbexListStructureError(
                "declared totalPage=0 with nonempty response rows"
            )

        raw_totals = [
            data[key]
            for key in (
                "total",
                "totalRecordNum",
                "totalCount",
                "recordsTotal",
                "totalElements",
            )
            if data.get(key) is not None
        ]
        if not raw_totals:
            raise _CbexListStructureError("declared total records are missing")
        total_values = {
            self._cbex_nonnegative_int(value, field="declared total records")
            for value in raw_totals
        }
        if len(total_values) != 1:
            raise _CbexListStructureError(
                f"conflicting declared total records: {sorted(total_values)}"
            )
        declared_total = next(iter(total_values))
        expected_pages = (
            declared_total + self.page_size - 1
        ) // self.page_size
        if declared_pages != expected_pages:
            raise _CbexListStructureError(
                "declared totalPage does not close with total records and page size: "
                f"totalPage={declared_pages} total={declared_total} "
                f"page_size={self.page_size}"
            )
        return rows, declared_pages, declared_total

    @staticmethod
    def _cbex_nonnegative_int(value: object, *, field: str) -> int:
        if isinstance(value, bool):
            raise _CbexListStructureError(f"{field} must be a nonnegative integer")
        if isinstance(value, float) and not value.is_integer():
            raise _CbexListStructureError(f"{field} must be a nonnegative integer")
        try:
            number = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError) as exc:
            raise _CbexListStructureError(
                f"{field} must be a nonnegative integer"
            ) from exc
        if number < 0:
            raise _CbexListStructureError(f"{field} must be a nonnegative integer")
        return number

    @staticmethod
    def _cbex_page_identities(rows: List[Dict[str, Any]]) -> List[str]:
        identities: List[str] = []
        for row in rows:
            identity = ""
            for key in ("id", "projectId", "projectID", "code", "url"):
                value = str(row.get(key) or "").strip()
                if value:
                    identity = f"{key}:{value}"
                    break
            identities.append(
                identity
                or json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        return identities

    @staticmethod
    def _rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        d = data.get("data") if isinstance(data, dict) else None
        rows = d.get("data") if isinstance(d, dict) else None
        return rows if isinstance(rows, list) else []

    @staticmethod
    def _total_pages(data: Dict[str, Any]) -> int:
        d = data.get("data") if isinstance(data, dict) else None
        try:
            return max(0, int(d.get("totalPage"))) if isinstance(d, dict) else 0
        except Exception:
            return 0

    @staticmethod
    def _is_pre_disclosure_row(row: Dict[str, Any]) -> bool:
        code = str(row.get("code") or "").strip().upper()
        url = str(row.get("url") or "").strip().lower()
        return code.endswith("-0") or "/ypl/" in url

    def _should_keep_row(self, row: Dict[str, Any]) -> bool:
        if self.include_pre_disclosure is None:
            return True
        is_pre = self._is_pre_disclosure_row(row)
        return is_pre if self.include_pre_disclosure else not is_pre

    def _prefetched_to_candidates(
        self,
        *,
        prefetched_candidates: List[Dict[str, Any]],
        outdir: str,
        summary: DownloadSummary,
        seen: Set[str],
        cands: List[_Candidate],
        start: Optional[dt.date],
        end: Optional[dt.date],
    ) -> None:
        for index, raw in enumerate(prefetched_candidates, start=1):
            if not isinstance(raw, dict):
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="cbex",
                        task_id=runtime_task_id("cbex", self.output_type),
                        raw_reason=f"prefetched-entry-{index}-invalid-format",
                    )
                )
                continue
            summary.listed_items += 1
            entry = dict(raw)

            row_raw = entry.get("row")
            row = row_raw if isinstance(row_raw, dict) else {}
            if row and not self._should_keep_row(row):
                summary.skipped_by_business_filter += 1
                continue

            uid = str(entry.get("uid") or "").strip()
            code = str(entry.get("code") or "").strip().upper()
            url = str(entry.get("url") or "").strip()
            if not uid:
                uid = code or url or hashlib.md5(
                    json.dumps(entry, ensure_ascii=False).encode("utf-8")
                ).hexdigest()[:16]

            if uid in seen:
                summary.skipped_by_duplicate += 1
                continue
            seen.add(uid)

            d = parse_loose_date(entry.get("disclosure_start") or row.get("disclosuretime"))
            if d and "disclosure_start" not in row:
                row = {**row, "disclosure_start": d.isoformat()}
            if start or end:
                if d is None:
                    summary.skipped_by_list_date += 1
                    continue
                if not in_date_range(d, start, end):
                    summary.skipped_by_list_date += 1
                    continue

            if not url:
                if row:
                    href = str(row.get("url") or "").strip()
                    url = urllib.parse.urljoin(BASE_URL, href) if href else ""
                if not url:
                    summary.typed_errors.append(
                        invalid_candidate_error(
                            source_id="cbex",
                            task_id=runtime_task_id("cbex", self.output_type),
                            raw_reason=f"prefetched-entry-{index}-missing-detail-url: id={uid}",
                        )
                    )
                    continue

            project_name = str(entry.get("project_name") or row.get("title") or row.get("name") or "").strip()
            filename_seed = code or uid
            path, _ = resolve_submission_snapshot_target(
                archive_root=outdir,
                project_code=filename_seed,
                project_name=project_name,
                listing_date=d.isoformat() if d else "",
            )
            if self.resume and self._resume_artifact_is_complete(path):
                summary.skipped_by_resume += 1
                continue

            candidate = _Candidate(uid=uid, code=code, url=url, html_path=path, row=row)
            cands.append(candidate)
            summary.candidate_entries.append(
                {
                    "uid": candidate.uid,
                    "code": candidate.code,
                    "url": candidate.url,
                    "row": row,
                    "disclosure_start": d.isoformat() if d else None,
                }
            )
            if d is not None:
                summary.candidate_dates.append(d.isoformat())

    def _rows_to_candidates(
        self,
        *,
        rows: List[Dict[str, Any]],
        source: _ListSource,
        outdir: str,
        summary: DownloadSummary,
        seen: Set[str],
        cands: List[_Candidate],
        start: Optional[dt.date],
        end: Optional[dt.date],
    ) -> None:
        for r in rows:
            if not isinstance(r, dict):
                continue
            summary.listed_items += 1

            if not self._should_keep_row(r):
                summary.skipped_by_business_filter += 1
                continue

            code = str(r.get("code") or "").strip().upper()
            href = str(r.get("url") or "").strip()
            url = urllib.parse.urljoin(BASE_URL, href) if href else ""
            uid = code or url or hashlib.md5(json.dumps(r, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
            if uid in seen:
                summary.skipped_by_duplicate += 1
                continue
            seen.add(uid)

            d = parse_loose_date(r.get("disclosuretime"))
            if start or end:
                if d is None:
                    summary.skipped_by_list_date += 1
                    continue
                if not in_date_range(d, start, end):
                    summary.skipped_by_list_date += 1
                    continue

            if not url:
                summary.typed_errors.append(
                    invalid_candidate_error(
                        source_id="cbex",
                        task_id=runtime_task_id("cbex", self.output_type),
                        raw_reason=f"missing-detail-url: id={uid}",
                    )
                )
                continue

            project_name = str(r.get("title") or r.get("name") or "").strip()
            path, _ = resolve_submission_snapshot_target(
                archive_root=outdir,
                project_code=code or uid,
                project_name=project_name,
                listing_date=d.isoformat() if d else "",
            )
            if self.resume and self._resume_artifact_is_complete(path):
                summary.skipped_by_resume += 1
                continue

            row_with_source = {**r, "list_source": source.label}
            candidate = _Candidate(uid=uid, code=code, url=url, html_path=path, row=row_with_source)
            cands.append(candidate)
            summary.candidate_entries.append(
                {
                    "uid": candidate.uid,
                    "code": candidate.code,
                    "url": candidate.url,
                    "row": row_with_source,
                    "disclosure_start": d.isoformat() if d else None,
                }
            )
            if d is not None:
                summary.candidate_dates.append(d.isoformat())

    async def _process_candidate(self, *, context, candidate: _Candidate, summary: DownloadSummary, start: Optional[dt.date], end: Optional[dt.date], timeout_cls) -> None:
        html: Optional[str] = None
        http_status: Optional[int] = None
        last: Optional[Exception] = None
        unavailable_error: DetailUnavailableError | None = None

        for k in range(1, 4):
            page = await context.new_page()
            try:
                html, http_status = await self._fetch_html(
                    page=page,
                    url=candidate.url,
                    code=candidate.code,
                )
                summary.detail_fetched += 1
                break
            except (_ChallengeError, timeout_cls) as exc:
                last = exc
                if k <= 2:
                    await asyncio.sleep(1.2 * k + random.random())
                else:
                    summary.typed_errors.append(
                        execute_failed_error(
                            source_id="cbex",
                            task_id=runtime_task_id("cbex", self.output_type),
                            raw_reason=f"id={candidate.uid} timeout-or-challenge: {exc}",
                        )
                    )
            except DetailUnavailableError as exc:
                last = exc
                unavailable_error = exc
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
                if k <= 2:
                    await asyncio.sleep(1.2 * k + random.random())
                else:
                    summary.typed_errors.append(
                        execute_failed_error(
                            source_id="cbex",
                            task_id=runtime_task_id("cbex", self.output_type),
                            raw_reason=f"id={candidate.uid} fetch-failed: {exc}",
                        )
                    )
            finally:
                await page.close()

        if html is None:
            if unavailable_error is not None:
                summary.skipped_by_detail_unavailable += 1
                self.logger.info(
                    "Detail resource unavailable: source=cbex id=%s reason=%s final_url=%s status=%s title=%r",
                    candidate.uid,
                    unavailable_error.reason,
                    unavailable_error.evidence.final_url,
                    unavailable_error.evidence.status,
                    unavailable_error.evidence.title,
                )
                return
            if last:
                self.logger.error("Detail fetch failed: id=%s err=%s", candidate.uid, last)
            summary.detail_failed += 1
            return

        ds = self._extract_disclosure_start_date(html)
        list_ds = parse_loose_date(
            candidate.row.get("disclosure_start")
            or candidate.row.get("disclosuretime")
        )
        final_date = ds if ds is not None else list_ds
        if start or end:
            if final_date is None:
                summary.date_missing_skipped += 1
                summary.skipped_by_detail_date += 1
                return
            if not in_date_range(final_date, start, end):
                summary.skipped_by_detail_date += 1
                return
        task_id = runtime_task_id("cbex", self.output_type)
        if not reserve_download_target(
            summary,
            html_root=self.html_root,
            html_path=candidate.html_path,
            source_id="cbex",
            task_id=task_id,
        ):
            summary.detail_failed += 1
            return

        try:
            await self._save_complete_page(html=html, page_url=candidate.url, html_path=candidate.html_path, request_context=context.request)
            payload = {
                "task_id": task_id,
                "source_id": "cbex",
                "record_family": "listing",
                "business_id": business_id_key(self.output_type),
                "id": candidate.uid,
                "code": candidate.code,
                "url": candidate.url,
                **successful_http_evidence(
                    source_url=candidate.url,
                    http_status=http_status,
                ),
                "row": candidate.row,
                "disclosure_start_date": ds.isoformat() if ds else None,
            }
            if self.save_json:
                p = os.path.splitext(candidate.html_path)[0] + ".json"
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self._write_json(json_path=p, payload={**payload, "save_status": "pending"}),
                )
            else:
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self._write_resume_status(
                        html_path=candidate.html_path,
                        save_status="pending",
                        source_url=candidate.url,
                        http_status=http_status,
                    ),
                )
            if self.save_json:
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self._write_json(
                        json_path=p,
                        payload={
                            **payload,
                            "save_status": "complete",
                            **archive_integrity_fields(candidate.html_path),
                        },
                    ),
                )
            else:
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self._write_resume_status(
                        html_path=candidate.html_path,
                        save_status="complete",
                        source_url=candidate.url,
                        http_status=http_status,
                    ),
                )
            self._notify_item_saved(candidate=candidate, disclosure_start=ds or list_ds)
        except Exception as exc:  # noqa: BLE001
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: mark_artifact_save_failed(
                    html_path=candidate.html_path,
                    save_json=self.save_json,
                    write_json=lambda json_path, payload: self._write_json(
                        json_path=json_path,
                        payload=payload,
                    ),
                    failure_identity={
                        "task_id": runtime_task_id("cbex", self.output_type),
                        "source_id": "cbex",
                        "record_family": "listing",
                        "business_id": business_id_key(self.output_type),
                    },
                    write_resume_status=lambda html_path, save_status: self._write_resume_status(
                        html_path=html_path,
                        save_status=save_status,
                        source_url=candidate.url,
                        http_status=http_status,
                    ),
                    logger=self.logger,
                ),
            )
            summary.typed_errors.append(
                save_failed_error(
                    source_id="cbex",
                    task_id=runtime_task_id("cbex", self.output_type),
                    raw_reason=str(exc),
                )
            )
            summary.detail_failed += 1
            return

        summary.saved += 1
        record_downloaded_target(summary, html_root=self.html_root, html_path=candidate.html_path)

    async def _fetch_html(self, *, page, url: str, code: str) -> tuple[str, int]:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=self._render_timeout_ms)
        if response is not None and int(response.status) == 404:
            raise DetailUnavailableError(
                reason="not_found_status",
                status=404,
                final_url=page.url,
                expected_identifier=code,
            )
        http_status = successful_http_evidence(
            source_url=url,
            http_status=getattr(response, "status", None),
        )["http_status"]
        try:
            await page.wait_for_function(
                """
                (expectedCode) => {
                    const t = (document.body && document.body.innerText ? document.body.innerText : '').toUpperCase();
                    const code = String(expectedCode || '').toUpperCase();
                    if (code) return t.includes(code);
                    return t.includes('项目编号');
                }
                """,
                arg=code or "",
                timeout=min(15000, self._render_timeout_ms),
            )
        except Exception:
            pass
        await page.wait_for_timeout(1200 + int(random.random() * 500))
        html = await page.content()
        if _is_challenge_html(html):
            raise _ChallengeError("challenge page")
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).upper()
        expected_code = str(code or "").strip().upper()
        if self._is_official_not_found_url(page.url):
            raise DetailUnavailableError(
                reason="not_found_url",
                final_url=page.url,
                expected_identifier=expected_code,
                html_len=len(html or ""),
            )
        if expected_code and expected_code not in text:
            raise RuntimeError(f"detail-page-mismatch expected_code={expected_code} final_url={page.url}")
        if not expected_code and "项目编号" not in text:
            raise RuntimeError(f"detail-page-mismatch missing-project-code final_url={page.url}")
        return html, http_status

    @staticmethod
    def _is_official_not_found_url(url: str) -> bool:
        path = urllib.parse.urlsplit(str(url or "")).path.lower()
        return "/404ym/" in path or path.endswith("/404")

    def _extract_disclosure_start_date(self, html: str) -> Optional[dt.date]:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        for p in DISCLOSURE_START_PATTERNS:
            m = re.search(p, text)
            if m:
                d = parse_loose_date(m.group(1))
                if d:
                    return d
        return None

    def _resume_artifact_is_complete(self, html_path: str) -> bool:
        stem = os.path.splitext(html_path)[0]
        json_path = f"{stem}.json"
        marker_path = _resume_status_path(html_path)
        return complete_resume_sidecar_exists(
            html_path,
            sidecar_path=json_path if self.save_json else marker_path,
            require_integrity=True,
            require_assets_dir=True,
            expected_fields={
                "task_id": runtime_task_id("cbex", self.output_type),
                "source_id": "cbex",
                "record_family": "listing",
                "business_id": business_id_key(self.output_type),
            },
        )

    @staticmethod
    def _write_json(*, json_path: str, payload: Dict[str, Any]) -> None:
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
            "task_id": runtime_task_id("cbex", self.output_type),
            "source_id": "cbex",
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

    async def _save_complete_page(self, *, html: str, page_url: str, html_path: str, request_context) -> None:
        base = os.path.splitext(os.path.basename(html_path))[0]
        final_assets_dir = f"{os.path.splitext(html_path)[0]}_files"
        temp_assets_dir = f"{final_assets_dir}.part"
        temp_html_path = f"{html_path}.part"

        if os.path.isdir(temp_assets_dir):
            shutil.rmtree(temp_assets_dir)
        if os.path.isfile(temp_html_path):
            os.remove(temp_html_path)

        try:
            os.makedirs(temp_assets_dir, exist_ok=True)

            soup = BeautifulSoup(html, "html.parser")
            for script in soup.find_all("script"):
                script.decompose()

            downloaded: Dict[str, str] = {}

            for tag, attr in TAG_ASSET_ATTRS:
                for node in soup.find_all(tag):
                    raw = node.get(attr)
                    if not raw:
                        continue
                    local = await self._download_asset(
                        request_context=request_context,
                        raw_url=str(raw),
                        base_url=page_url,
                        assets_dir=temp_assets_dir,
                        downloaded=downloaded,
                    )
                    if not local:
                        continue
                    ref = f"{base}_files/{local}"
                    node[attr] = ref
                    if tag == "img" and attr != "src" and not node.get("src"):
                        node["src"] = ref

            with open(temp_html_path, "w", encoding="utf-8") as f:
                f.write(str(soup))

            if os.path.isdir(final_assets_dir):
                shutil.rmtree(final_assets_dir)
            if os.path.isfile(html_path):
                os.remove(html_path)

            os.replace(temp_assets_dir, final_assets_dir)
            os.replace(temp_html_path, html_path)
            self._write_invalid_shell_evidence_if_needed(
                html_path=html_path,
                page_url=page_url,
                source_html=html,
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
        return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

    def _write_invalid_shell_evidence_if_needed(
        self,
        *,
        html_path: str,
        page_url: str,
        source_html: str,
    ) -> None:
        evidence_path = f"{html_path}.peap-evidence.json"
        if not _is_challenge_html(source_html):
            if os.path.isfile(evidence_path):
                os.remove(evidence_path)
            return

        with open(html_path, encoding="utf-8") as handle:
            saved_html = handle.read()

        identity_hints: Dict[str, str] = {}
        artifact_stem = os.path.splitext(os.path.basename(html_path))[0].strip()
        if artifact_stem:
            identity_hints["artifact_stem_hash"] = self._safe_sha256(artifact_stem)

        locator_hash = self._safe_sha256(page_url)
        evidence = {
            "schema_version": 1,
            "page_kind": "invalid_shell",
            "source_url_hash": locator_hash,
            "final_url_hash": locator_hash,
            "content_sha256": self._safe_sha256(saved_html),
            "identity_hints": identity_hints,
        }
        with open(evidence_path, "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, ensure_ascii=False, indent=2, sort_keys=True)

    def _notify_item_saved(self, *, candidate: _Candidate, disclosure_start: Optional[dt.date]) -> None:
        callback = self.item_saved_callback
        if callback is None:
            return
        callback(
            {
                "source_file": candidate.html_path,
                "page_url": candidate.url,
                "project_code": candidate.code,
                "project_name": str(candidate.row.get("title") or candidate.row.get("name") or ""),
                "listing_date": disclosure_start.isoformat() if disclosure_start else "",
                "source_id": "cbex",
                "business_id": business_id_key(self.output_type),
                "row": candidate.row,
            }
        )

    async def _download_asset(self, *, request_context, raw_url: str, base_url: str, assets_dir: str, downloaded: Dict[str, str]) -> Optional[str]:
        value = raw_url.strip().strip("'\"")
        if _skip_asset_url(value):
            return None
        u = urllib.parse.urljoin(base_url, value)
        p = urllib.parse.urlparse(u)
        if p.scheme not in {"http", "https"}:
            return None
        if p.netloc and "cbex.com" not in p.netloc.lower():
            return None
        if u in downloaded:
            return downloaded[u]

        try:
            r = await request_context.get(u, headers={"User-Agent": REQUEST_HEADERS["User-Agent"]}, timeout=min(self.timeout, 8) * 1000)
        except Exception:
            return None
        if r.status >= 400:
            return None
        try:
            content = await r.body()
        except Exception:
            return None

        name = re.sub(r"[\\/:*?\"<>|]+", "_", os.path.basename(p.path) or "")
        if not name:
            name = "asset_" + hashlib.md5(u.encode("utf-8")).hexdigest()[:12]
        if not os.path.splitext(name)[1]:
            ct = (r.headers.get("content-type") or "").lower()
            ext = ".js" if "javascript" in ct else ".css" if "css" in ct else ".png" if "png" in ct else ".jpg" if ("jpeg" in ct or "jpg" in ct) else ".svg" if "svg" in ct else ""
            if ext:
                name += ext

        final = name
        n = 1
        while os.path.exists(os.path.join(assets_dir, final)):
            root, ext = os.path.splitext(name)
            final = f"{root}__{n}{ext}"
            n += 1

        with open(os.path.join(assets_dir, final), "wb") as f:
            f.write(content)
        downloaded[u] = final
        return final


class CbexEquityTransferDownloader(CbexPhysicalAssetDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_EQUITY_TRANSFER,
            list_sources=_cbex_list_sources("equity_transfer"),
            include_pre_disclosure=_cbex_include_pre_disclosure("equity_transfer"),
            **kwargs,
        )


class CbexCapitalIncreaseDownloader(CbexPhysicalAssetDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_CAPITAL_INCREASE,
            list_sources=_cbex_list_sources("capital_increase"),
            include_pre_disclosure=_cbex_include_pre_disclosure("capital_increase"),
            **kwargs,
        )


class CbexPreDisclosureDownloader(CbexPhysicalAssetDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PRE_DISCLOSURE,
            list_sources=_cbex_list_sources("pre_disclosure"),
            include_pre_disclosure=_cbex_include_pre_disclosure("pre_disclosure"),
            **kwargs,
        )
