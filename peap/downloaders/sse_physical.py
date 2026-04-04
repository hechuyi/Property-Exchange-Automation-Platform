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

from ..constants import (
    TYPE_CAPITAL_INCREASE,
    TYPE_EQUITY_TRANSFER,
    TYPE_PHYSICAL_ASSET,
    TYPE_PRE_DISCLOSURE,
)
from ..download_errors import execute_failed_error, invalid_candidate_error, list_failed_error, save_failed_error
from ..submission_layout import resolve_submission_snapshot_target
from .common import DownloadSummary, in_date_range, parse_bound, parse_loose_date, project_type_key
from .sse_contracts import get_sse_task_contract, SseListRequest

LIST_API_URL = "https://www.suaee.com/si/prjs/realright/list"
DETAIL_PAGE_URL = "https://www.suaee.com/xmzx.html#/zczrDetail"

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


@dataclass
class _DownloadCandidate:
    xmid: str
    project_code: str
    page_url: str
    html_path: str
    row: Dict[str, Any]


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


class ShanghaiPhysicalAssetDownloader:
    """Download Shanghai physical asset projects and save full rendered pages."""

    manifest_list_endpoint = "/prjs/realright/list"
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
    ):
        self.html_root = html_root
        self.page_size = max(1, int(page_size))
        self.max_pages = max_pages if max_pages is None else max(1, int(max_pages))
        self.concurrency = max(1, int(concurrency))
        self.resume = bool(resume)
        self.timeout = max(5, int(timeout))
        self.save_json = bool(save_json)
        self.output_type = str(output_type or TYPE_PHYSICAL_ASSET)
        if not list_query_specs:
            list_query_specs = [("ZICHANZHUANRANG", "2")]
        self.list_query_specs: List[Tuple[str, str]] = [
            (str(project_type), str(gplx))
            for project_type, gplx in list_query_specs
        ]
        self._default_detail_route = str(default_detail_route or "jymhzichan").strip("/") or "jymhzichan"
        self.logger = logger or logging.getLogger("parser_v2")
        self.item_saved_callback = item_saved_callback
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
        detail_accounted = summary.saved + summary.skipped_by_detail_date + summary.detail_failed
        summary.list_unaccounted = summary.listed_items - list_accounted
        summary.detail_unaccounted = 0 if list_only else (summary.detail_candidates - detail_accounted)

        self.logger.info(
            "Done: pages=%s listed=%s fetched=%s saved=%s list_date_skipped=%s detail_date_skipped=%s resume_skipped=%s duplicate_skipped=%s missing_xmid_skipped=%s detail_candidates=%s detail_failed=%s list_unaccounted=%s detail_unaccounted=%s errors=%s",
            summary.pages_requested,
            summary.listed_items,
            summary.detail_fetched,
            summary.saved,
            summary.skipped_by_list_date,
            summary.skipped_by_detail_date,
            summary.skipped_by_resume,
            summary.skipped_by_duplicate,
            summary.skipped_by_missing_xmid,
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
        seen_xmid: Set[str] = set()
        for list_project_type, gplx in self.list_query_specs:
            try:
                first_page = self._query_list_page(
                    page_index=1,
                    list_project_type=list_project_type,
                    gplx=gplx,
                )
            except Exception as exc:  # noqa: BLE001
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="sse",
                        task_id=f"sse:{project_type_key(self.output_type)}",
                        raw_reason=f"list-{list_project_type}-{gplx}-page-1-request-failed: {exc}",
                    )
                )
                continue
            if int(first_page.get("code", -1)) not in {0, 200}:
                summary.typed_errors.append(
                    list_failed_error(
                        source_id="sse",
                        task_id=f"sse:{project_type_key(self.output_type)}",
                        raw_reason=f"list-{list_project_type}-{gplx}-api-failed: {first_page.get('message')}",
                    )
                )
                continue

            raw_total = first_page.get("extra")
            if isinstance(raw_total, (int, float, str, bytes, bytearray)):
                total_records = int(raw_total)
            else:
                total_records = int(first_page.get("data", {}).get("pageCount") or 0)
            page_count = max(1, (total_records + self.page_size - 1) // self.page_size) if total_records else 1
            if page_count <= 0:
                self.logger.info(
                    "No list data for projectType=%s gplx=%s.",
                    list_project_type,
                    gplx,
                )
                continue
            if self.max_pages is not None:
                page_count = min(page_count, self.max_pages)

            for page_index in range(1, page_count + 1):
                if page_index == 1:
                    payload = first_page
                else:
                    try:
                        payload = self._query_list_page(
                            page_index=page_index,
                            list_project_type=list_project_type,
                            gplx=gplx,
                        )
                    except Exception as exc:  # noqa: BLE001
                        summary.pages_requested += 1
                        summary.typed_errors.append(
                            list_failed_error(
                                source_id="sse",
                                task_id=f"sse:{project_type_key(self.output_type)}",
                                raw_reason=f"list-{list_project_type}-{gplx}-page-{page_index}-request-failed: {exc}",
                            )
                        )
                        continue
                summary.pages_requested += 1
                if int(payload.get("code", -1)) not in {0, 200}:
                    summary.typed_errors.append(
                        list_failed_error(
                            source_id="sse",
                            task_id=f"sse:{project_type_key(self.output_type)}",
                            raw_reason=f"list-{list_project_type}-{gplx}-page-{page_index}-failed: {payload.get('message')}",
                        )
                    )
                    continue

                rows_raw = payload.get("data")
                if isinstance(rows_raw, list):
                    rows = rows_raw
                else:
                    rows = payload.get("data", {}).get("data") or []
                if not isinstance(rows, list):
                    summary.typed_errors.append(
                        invalid_candidate_error(
                            source_id="sse",
                            task_id=f"sse:{project_type_key(self.output_type)}",
                            raw_reason=f"list-{list_project_type}-{gplx}-page-{page_index}-invalid-data",
                        )
                    )
                    continue

                for row in rows:
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
                                task_id=f"sse:{project_type_key(self.output_type)}",
                                raw_reason=f"list-{list_project_type}-{gplx}-page-{page_index}-missing-xmid",
                            )
                        )
                        continue
                    if xmid in seen_xmid:
                        summary.skipped_by_duplicate += 1
                        continue
                    seen_xmid.add(xmid)

                    list_disclosure_start = parse_loose_date(
                        row.get("plksrq") or row.get("PLKSRQ") or row.get("gpksrq") or row.get("GPKSRQ")
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
                        listing_date=list_disclosure_start.isoformat() if list_disclosure_start else "",
                    )
                    assets_dir = f"{os.path.splitext(html_path)[0]}_files"
                    if self.resume and os.path.isfile(html_path) and os.path.isdir(assets_dir):
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
                        task_id=f"sse:{project_type_key(self.output_type)}",
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
                        task_id=f"sse:{project_type_key(self.output_type)}",
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
                        task_id=f"sse:{project_type_key(self.output_type)}",
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
            assets_dir = f"{os.path.splitext(html_path)[0]}_files"
            if self.resume and os.path.isfile(html_path) and os.path.isdir(assets_dir):
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

    def _query_list_page(self, *, page_index: int, list_project_type: str, gplx: str) -> Dict[str, Any]:
        contract = get_sse_task_contract(project_type_key(self.output_type))
        # Find the matching list request by endpoint pattern
        list_req: SseListRequest | None = None
        for req in contract.list_requests:
            # The list_project_type maps to a specific endpoint:
            # ZICHANZHUANRANG -> realright, CHANQUAN -> equity, ZENGZI -> capitalincrease
            if list_project_type == "ZICHANZHUANRANG" and "realright" in req.endpoint:
                list_req = req
                break
            elif list_project_type == "CHANQUAN" and "equity" in req.endpoint:
                list_req = req
                break
            elif list_project_type == "ZENGZI" and "capitalincrease" in req.endpoint:
                list_req = req
                break
        if list_req is None:
            self.logger.warning("No SSE contract for list_project_type=%s gplx=%s; returning empty", list_project_type, gplx)
            return {"code": 200, "data": [], "extra": 0}

        payload: Dict[str, Any] = {
            list_req.page_no_field: int(page_index),
            list_req.page_size_field: self.page_size,
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
        if list_req.xmlx is not None:
            payload["XMLX"] = list_req.xmlx
        return self._post_json(f"https://www.suaee.com/si{list_req.endpoint}", payload)

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
        # FCLASS-based routing from live SSE frontend bundle
        # SW -> zczrDetail, 1C -> qyzzDetail, default -> Detail
        fclass = row.get("fclass") or row.get("FCLASS") or ""
        xmurl = str(row.get("xmurl") or "").strip()
        if xmurl:
            if xmurl.startswith(("http://", "https://")):
                return xmurl
            return urllib.parse.urljoin("https://www.suaee.com/", xmurl)

        base = "https://www.suaee.com/xmzx.html#"
        xmid_quoted = urllib.parse.quote(xmid)
        if fclass == "SW":
            return f"{base}/zczrDetail?XMID={xmid_quoted}"
        if fclass == "1C":
            xmlx = row.get("xmlx") or row.get("XMLX") or ""
            return f"{base}/qyzzDetail?XMID={xmid_quoted}&PLZT={xmlx}"
        # Fallback for empty FCLASS: use default_detail_route
        # jymhzichan -> zczrDetail, jymhchanquan -> qyzzDetail, jymhzengzi -> qyzzDetail
        route = self._default_detail_route
        if route == "jymhzichan":
            return f"{base}/zczrDetail?XMID={xmid_quoted}"
        if route in ("jymhchanquan", "jymhzengzi", "jymhchanquanyu", "jymhzengziyu"):
            xmlx = row.get("xmlx") or row.get("XMLX") or ""
            return f"{base}/qyzzDetail?XMID={xmid_quoted}&PLZT={xmlx}"
        return f"{base}/Detail?XMID={xmid_quoted}"

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
        except Exception as exc:  # noqa: BLE001
            raise

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url=url, data=body, headers=REQUEST_HEADERS, method="POST")
        try:
            with self._urlopen(request) as response:
                text = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"POST {url} failed: {exc}") from exc
        return json.loads(text)

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

        self.logger.info("Detail download start: total=%s concurrency=%s", total, self.concurrency)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
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
        last_exc: Optional[Exception] = None

        for attempt in range(1, self._detail_retries + 2):
            page = await context.new_page()
            try:
                rendered_html = await self._fetch_rendered_html(
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
                            task_id=f"sse:{project_type_key(self.output_type)}",
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
                            task_id=f"sse:{project_type_key(self.output_type)}",
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

        try:
            await self._run_blocking(
                self._save_complete_page,
                rendered_html=rendered_html,
                page_url=candidate.page_url,
                html_path=candidate.html_path,
            )
        except Exception as exc:  # noqa: BLE001
            summary.detail_failed += 1
            summary.typed_errors.append(
                save_failed_error(
                    source_id="sse",
                    task_id=f"sse:{project_type_key(self.output_type)}",
                    raw_reason=str(exc),
                )
            )
            return

        if self.save_json:
            sidecar = {
                "xmid": candidate.xmid,
                "xmbh": candidate.row.get("xmbh"),
                "xmmc": candidate.row.get("xmmc"),
                "page_url": candidate.page_url,
                "list_row": candidate.row,
                "disclosure_start_date": disclosure_start.isoformat() if disclosure_start else None,
            }
            json_path = os.path.splitext(candidate.html_path)[0] + ".json"
            await self._run_blocking(self._write_json, json_path=json_path, payload=sidecar)
        self._notify_item_saved(candidate=candidate, disclosure_start=disclosure_start or list_start)
        summary.saved += 1
        summary.downloaded_this_run.add(os.path.relpath(candidate.html_path, self.html_root))

    async def _fetch_rendered_html(
        self,
        *,
        page,
        page_url: str,
        expected_project_code: Optional[str] = None,
    ) -> str:
        await page.goto(page_url, wait_until="domcontentloaded", timeout=self._render_timeout_ms)
        await page.wait_for_selector(
            "body",
            timeout=self._render_timeout_ms,
        )
        if expected_project_code:
            await page.wait_for_function(
                """
                (expectedCode) => {
                    const text = (document.body?.innerText || "").toUpperCase();
                    return text.includes("\\u9879\\u76ee\\u7f16\\u53f7") || text.includes(String(expectedCode || "").toUpperCase());
                }
                """,
                arg=expected_project_code,
                timeout=self._render_timeout_ms,
            )
        await page.wait_for_timeout(1500)
        return await page.content()

    async def _run_blocking(self, func, /, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(**kwargs))

    def _write_json(self, *, json_path: str, payload: Dict[str, Any]) -> None:
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

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
            "Detail progress: %s/%s saved=%s detail_date_skipped=%s errors=%s speed=%.2f/min eta=%s",
            completed,
            total,
            summary.saved,
            summary.skipped_by_detail_date,
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
        except Exception:
            if os.path.isdir(temp_assets_dir):
                shutil.rmtree(temp_assets_dir, ignore_errors=True)
            if os.path.isfile(temp_html_path):
                try:
                    os.remove(temp_html_path)
                except OSError:
                    pass
            raise

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
                    "project_name": str(candidate.row.get("xmmc") or ""),
                    "listing_date": disclosure_start.isoformat() if disclosure_start else "",
                    "source_id": "sse",
                    "project_type": self.output_type,
                    "row": candidate.row,
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("item_saved_callback failed: %s", exc)

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
    manifest_list_endpoint = "/prjs/equity/list"
    manifest_date_field_candidates = ("disclosure_start", "disclosure_end")

    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_EQUITY_TRANSFER,
            list_query_specs=[("CHANQUAN", "2")],
            default_detail_route="jymhchanquan",
            **kwargs,
        )


class ShanghaiCapitalIncreaseDownloader(ShanghaiPhysicalAssetDownloader):
    manifest_list_endpoint = "/prjs/capitalincrease/list"
    manifest_date_field_candidates = ("disclosure_start", "disclosure_end")

    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_CAPITAL_INCREASE,
            list_query_specs=[("ZENGZI", "2")],
            default_detail_route="jymhzengzi",
            **kwargs,
        )


class ShanghaiPreDisclosureDownloader(ShanghaiPhysicalAssetDownloader):
    # pre_disclosure uses two list requests, so we report the first one for manifest purposes
    manifest_list_endpoint = "/prjs/equity/list"
    manifest_date_field_candidates = ("disclosure_start",)

    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PRE_DISCLOSURE,
            list_query_specs=[("CHANQUAN", "1"), ("ZENGZI", "1")],
            default_detail_route="jymhchanquanyu",
            **kwargs,
        )
