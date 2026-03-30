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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from bs4 import BeautifulSoup

from ..constants import (
    STATUS_LISTED,
    TYPE_CAPITAL_INCREASE,
    TYPE_EQUITY_TRANSFER,
    TYPE_PHYSICAL_ASSET,
    TYPE_PRE_DISCLOSURE,
)
from ..submission_layout import resolve_submission_snapshot_target

LIST_API_URL = "https://www.suaee.com/si/prjs/realright/list"
DETAIL_API_URL = "https://www.suaee.com/si/prjs/realright/detail_zspl"
DETAIL_PAGE_URL = "https://www.suaee.com/xmzx.html#/zczrDetail?XMID={xmid}"
OFFLINE_ARTIFACT_SCRIPT_ID = "peap-suaee-offline-artifact"

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
class DownloadSummary:
    pages_requested: int = 0
    listed_items: int = 0
    detail_fetched: int = 0
    saved: int = 0
    skipped_by_list_date: int = 0
    skipped_by_detail_date: int = 0
    skipped_by_resume: int = 0
    skipped_by_duplicate: int = 0
    skipped_by_missing_xmid: int = 0
    detail_candidates: int = 0
    detail_failed: int = 0
    list_unaccounted: int = 0
    detail_unaccounted: int = 0
    candidate_dates: List[str] = field(default_factory=list)
    candidate_entries: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class _DownloadCandidate:
    xmid: str
    project_code: str
    page_url: str
    html_path: str
    row: Dict[str, Any]


def _response_code_ok(payload: Dict[str, Any]) -> bool:
    try:
        return int(payload.get("code", -1)) in {0, 200}
    except Exception:
        return False


def _row_value(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _extract_list_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list):
            return [dict(item) for item in rows if isinstance(item, dict)]
    return []


def _extract_list_page_count(payload: Dict[str, Any], *, page_size: int) -> int:
    data = payload.get("data")
    if isinstance(data, dict):
        try:
            page_count = int(data.get("pageCount") or 0)
        except Exception:
            page_count = 0
        if page_count > 0:
            return page_count
    try:
        total = int(payload.get("extra") or 0)
    except Exception:
        total = 0
    if total > 0:
        return max(1, (total + max(1, int(page_size)) - 1) // max(1, int(page_size)))
    return 1 if _extract_list_rows(payload) else 0


def _coerce_xmid_payload(xmid: str) -> int | str:
    return int(xmid) if str(xmid).isdigit() else str(xmid)


def _parse_date(value: Any) -> Optional[dt.date]:
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        try:
            return dt.datetime.utcfromtimestamp(ts).date()
        except (OverflowError, OSError, ValueError):
            return None

    raw = str(value).strip()
    if not raw:
        return None

    if raw.isdigit():
        return _parse_date(int(raw))

    raw = re.sub(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?", r"\1-\2-\3", raw)
    raw = raw.replace("/", "-").replace(".", "-")
    if "T" in raw:
        raw = raw.split("T", 1)[0]
    if " " in raw:
        raw = raw.split(" ", 1)[0]

    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", raw)
    if not match:
        return None

    try:
        year, month, day = (int(part) for part in match.groups())
        return dt.date(year, month, day)
    except ValueError:
        return None


def _parse_bound(raw: Optional[str], name: str) -> Optional[dt.date]:
    if raw in (None, ""):
        return None
    parsed = _parse_date(raw)
    if parsed is None:
        raise ValueError(f"invalid {name}: {raw!r} (expected YYYY-MM-DD)")
    return parsed


def _in_range(value: Optional[dt.date], start: Optional[dt.date], end: Optional[dt.date]) -> bool:
    if value is None:
        return False
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", str(name or "").strip())
    return cleaned or "unknown"


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
        ssl_fallback_insecure: bool = True,
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
            list_query_specs = [("realright", "")]
        self.list_query_specs: List[Tuple[str, str]] = [
            (str(project_type), str(gplx))
            for project_type, gplx in list_query_specs
        ]
        self._default_detail_route = str(default_detail_route or "zczrDetail").strip("/") or "zczrDetail"
        self.logger = logger or logging.getLogger("parser_v2")
        self.item_saved_callback = item_saved_callback
        self._render_timeout_ms = max(120, self.timeout) * 1000
        self._detail_retries = 2
        self.ssl_verify = bool(ssl_verify)
        raw_ca_bundle = str(ssl_ca_bundle or "").strip()
        self.ssl_ca_bundle = raw_ca_bundle or None
        self.ssl_fallback_insecure = bool(ssl_fallback_insecure)
        self._ssl_fallback_warned = False
        self._ssl_context_insecure = ssl._create_unverified_context()
        self._ssl_context_verified = self._build_verified_ssl_context() if self.ssl_verify else None
        self._ssl_context = self._ssl_context_verified if self.ssl_verify else self._ssl_context_insecure
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
        start = _parse_bound(start_date, "start-date")
        end = _parse_bound(end_date, "end-date")
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
            len(summary.errors),
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
                summary.errors.append(
                    f"list-{list_project_type}-{gplx}-page-1-request-failed: {exc}"
                )
                continue
            if not _response_code_ok(first_page):
                summary.errors.append(
                    f"list-{list_project_type}-{gplx}-api-failed: {first_page.get('message')}"
                )
                continue

            page_count = _extract_list_page_count(first_page, page_size=self.page_size)
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
                        summary.errors.append(
                            f"list-{list_project_type}-{gplx}-page-{page_index}-request-failed: {exc}"
                        )
                        continue
                summary.pages_requested += 1
                if not _response_code_ok(payload):
                    summary.errors.append(
                        f"list-{list_project_type}-{gplx}-page-{page_index}-failed: {payload.get('message')}"
                    )
                    continue

                rows = _extract_list_rows(payload)
                if not rows:
                    if payload.get("data") not in ({}, [], None):
                        self.logger.info(
                            "No list rows on page projectType=%s gplx=%s page=%s.",
                            list_project_type,
                            gplx,
                            page_index,
                        )
                    elif page_index == 1:
                        summary.errors.append(
                            f"list-{list_project_type}-{gplx}-page-{page_index}-invalid-data"
                        )
                    continue
                if not isinstance(rows, list):
                    summary.errors.append(
                        f"list-{list_project_type}-{gplx}-page-{page_index}-invalid-data"
                    )
                    continue

                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    summary.listed_items += 1

                    xmid = str(_row_value(row, "XMID", "xmid") or "").strip()
                    if not xmid:
                        summary.skipped_by_missing_xmid += 1
                        summary.errors.append(
                            f"list-{list_project_type}-{gplx}-page-{page_index}-missing-xmid"
                        )
                        continue
                    if xmid in seen_xmid:
                        summary.skipped_by_duplicate += 1
                        continue
                    seen_xmid.add(xmid)

                    list_disclosure_start = _parse_date(
                        _row_value(row, "PLKSRQ", "GPKSRQ", "plksrq", "gpksrq")
                    )
                    if start or end:
                        if list_disclosure_start and not _in_range(list_disclosure_start, start, end):
                            summary.skipped_by_list_date += 1
                            continue

                    project_code = str(_row_value(row, "XMBH", "xmbh") or xmid).strip()
                    project_name = str(_row_value(row, "XMMC", "xmmc") or "").strip()
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
                            "list_disclosure_start": list_disclosure_start.isoformat()
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
                summary.errors.append(f"prefetched-entry-{index}-invalid-format")
                continue
            summary.listed_items += 1
            entry = dict(raw)

            xmid = str(entry.get("xmid") or entry.get("XMID") or "").strip()
            if not xmid:
                summary.skipped_by_missing_xmid += 1
                summary.errors.append(f"prefetched-entry-{index}-missing-xmid")
                continue
            if xmid in seen_xmid:
                summary.skipped_by_duplicate += 1
                continue
            seen_xmid.add(xmid)

            row_raw = entry.get("row")
            row = row_raw if isinstance(row_raw, dict) else {}
            list_disclosure_start = _parse_date(
                entry.get("list_disclosure_start")
                or _row_value(row, "PLKSRQ", "GPKSRQ", "plksrq", "gpksrq")
            )
            if list_disclosure_start and "list_disclosure_start" not in row:
                row = {**row, "list_disclosure_start": list_disclosure_start.isoformat()}
            if start or end:
                if list_disclosure_start and not _in_range(list_disclosure_start, start, end):
                    summary.skipped_by_list_date += 1
                    continue

            project_code = str(entry.get("project_code") or _row_value(row, "XMBH", "xmbh") or xmid).strip().upper()
            page_url = str(entry.get("page_url") or self._resolve_page_url(row=row, xmid=xmid)).strip()
            if not page_url:
                summary.errors.append(f"prefetched-entry-{index}-missing-page-url: xmid={xmid}")
                continue

            project_name = str(entry.get("project_name") or _row_value(row, "XMMC", "xmmc") or "").strip()
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
                    "list_disclosure_start": list_disclosure_start.isoformat()
                    if list_disclosure_start
                    else None,
                }
            )
            if list_disclosure_start:
                summary.candidate_dates.append(list_disclosure_start.isoformat())

    def _query_list_page(self, *, page_index: int, list_project_type: str, gplx: str) -> Dict[str, Any]:
        payload = {"pageNo": int(page_index), "pageSize": self.page_size}
        return self._post_json(LIST_API_URL, payload)

    def _resolve_page_url(self, *, row: Dict[str, Any], xmid: str) -> str:
        xmurl = str(_row_value(row, "XMURL", "xmurl") or "").strip()
        if xmurl:
            if xmurl.startswith(("http://", "https://")):
                return xmurl
            return urllib.parse.urljoin("https://www.suaee.com/", xmurl)
        return DETAIL_PAGE_URL.format(xmid=urllib.parse.quote(xmid))

    def _guess_detail_route(self, *, row: Dict[str, Any]) -> str:
        list_project_type = str(_row_value(row, "projectType", "PROJECTTYPE") or "").upper()
        gplx = str(row.get("gplx") or "")
        if list_project_type == "ZICHANZHUANRANG":
            return "zczrDetail"
        if list_project_type == "CHANQUAN":
            return "jymhchanquanyu" if gplx == "1" else "jymhchanquan"
        if list_project_type == "ZENGZI":
            return "jymhzengziyu" if gplx == "1" else "jymhzengzi"
        return self._default_detail_route

    def _query_detail_payload(self, *, xmid: str) -> Dict[str, Any]:
        return self._post_json(DETAIL_API_URL, {"XMID": _coerce_xmid_payload(xmid)})

    def _build_offline_artifact(
        self,
        *,
        candidate: _DownloadCandidate,
        detail_response: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        artifact: Dict[str, Any] = {
            "schema_version": 1,
            "page_url": candidate.page_url,
            "list_row": dict(candidate.row),
        }
        if detail_response:
            artifact["detail_response"] = dict(detail_response)
        return artifact

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
            if (
                self.ssl_verify
                and self.ssl_fallback_insecure
                and _is_cert_verify_error(exc)
            ):
                if not self._ssl_fallback_warned:
                    self._ssl_fallback_warned = True
                    self.logger.warning(
                        "SSE SSL certificate verification failed, falling back to insecure TLS "
                        "(verify=False). Set --no-sse-ssl-fallback-insecure to disable this fallback."
                    )
                return urllib.request.urlopen(
                    request,
                    timeout=self.timeout,
                    context=self._ssl_context_insecure,
                )
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
        detail_response: Dict[str, Any] | None = None
        last_exc: Optional[Exception] = None

        for attempt in range(1, self._detail_retries + 2):
            page = await context.new_page()
            try:
                rendered_html = await self._fetch_rendered_html(
                    page=page,
                    page_url=candidate.page_url,
                    expected_project_code=candidate.project_code,
                    expected_xmid=candidate.xmid,
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
                    summary.errors.append(f"xmid={candidate.xmid} page-timeout: {exc}")
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
                    summary.errors.append(f"xmid={candidate.xmid} page-fetch-failed: {exc}")
            finally:
                await page.close()

        if rendered_html is None:
            summary.detail_failed += 1
            if last_exc is not None:
                self.logger.error("Detail fetch failed: xmid=%s error=%s", candidate.xmid, last_exc)
            return

        try:
            detail_response = await self._run_blocking(
                self._query_detail_payload,
                xmid=candidate.xmid,
            )
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"xmid={candidate.xmid} detail-api-failed: {exc}")
            detail_response = None

        disclosure_start = self._extract_disclosure_start_date(rendered_html)
        list_start = _parse_date(
            candidate.row.get("list_disclosure_start")
            or _row_value(candidate.row, "PLKSRQ", "GPKSRQ", "plksrq", "gpksrq")
        )
        if start or end:
            check_date = list_start if list_start is not None else disclosure_start
            if check_date is not None and not _in_range(check_date, start, end):
                summary.skipped_by_detail_date += 1
                return

        try:
            await self._run_blocking(
                self._save_complete_page,
                rendered_html=rendered_html,
                page_url=candidate.page_url,
                html_path=candidate.html_path,
                offline_artifact=self._build_offline_artifact(
                    candidate=candidate,
                    detail_response=detail_response,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            summary.detail_failed += 1
            summary.errors.append(f"xmid={candidate.xmid} save-failed: {exc}")
            return

        if self.save_json:
            sidecar = self._build_offline_artifact(
                candidate=candidate,
                detail_response=detail_response,
            )
            sidecar["disclosure_start_date"] = disclosure_start.isoformat() if disclosure_start else None
            json_path = os.path.splitext(candidate.html_path)[0] + ".json"
            await self._run_blocking(self._write_json, json_path=json_path, payload=sidecar)
        self._notify_item_saved(candidate=candidate, disclosure_start=disclosure_start or list_start)
        summary.saved += 1

    async def _fetch_rendered_html(
        self,
        *,
        page,
        page_url: str,
        expected_project_code: Optional[str] = None,
        expected_xmid: Optional[str] = None,
    ) -> str:
        detail_path = urllib.parse.urlparse(DETAIL_API_URL).path
        await page.add_init_script(
            f"""
            (() => {{
              const detailPath = {json.dumps(detail_path)};
              const assignDetail = (url, payload) => {{
                if (!url || String(url).indexOf(detailPath) === -1) {{
                  return;
                }}
                try {{
                  window.__PEAP_SUAEE_DETAIL__ = JSON.parse(String(payload || 'null'));
                }} catch (error) {{
                  window.__PEAP_SUAEE_DETAIL__ = payload || null;
                }}
              }};

              if (typeof window.fetch === 'function') {{
                const originalFetch = window.fetch.bind(window);
                window.fetch = async (...args) => {{
                  const response = await originalFetch(...args);
                  try {{
                    const targetUrl = String((args[0] && args[0].url) || args[0] || response.url || '');
                    if (targetUrl.indexOf(detailPath) !== -1) {{
                      response.clone().text().then((text) => assignDetail(targetUrl, text)).catch(() => null);
                    }}
                  }} catch (error) {{
                    void error;
                  }}
                  return response;
                }};
              }}

              if (window.XMLHttpRequest) {{
                const originalOpen = XMLHttpRequest.prototype.open;
                const originalSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(method, url, ...rest) {{
                  this.__peapUrl = url;
                  return originalOpen.call(this, method, url, ...rest);
                }};
                XMLHttpRequest.prototype.send = function(body) {{
                  this.addEventListener('load', function() {{
                    assignDetail(this.__peapUrl || this.responseURL || '', this.responseText || '');
                  }});
                  return originalSend.call(this, body);
                }};
              }}
            }})();
            """
        )
        await page.goto(page_url, wait_until="domcontentloaded", timeout=self._render_timeout_ms)
        await page.wait_for_function(
            """
            (expected) => {
                const detail = window.__PEAP_SUAEE_DETAIL__ || null;
                const detailData = detail && typeof detail === 'object' && detail.data ? detail.data : detail;
                const bodyText = String((document.body && document.body.innerText) || '');
                const expectedCode = String((expected && expected.projectCode) || '').toUpperCase();
                if (expectedCode && bodyText.toUpperCase().includes(expectedCode)) {
                    return true;
                }
                const expectedXmid = String((expected && expected.xmid) || '');
                const detailXmid = String(
                    (detailData && (detailData.XMID || detailData.xmid))
                    || (detail && (detail.XMID || detail.xmid))
                    || ''
                );
                if (expectedXmid && detailXmid && detailXmid === expectedXmid) {
                    return true;
                }
                return !!detail;
            }
            """,
            arg={
                "projectCode": str(expected_project_code or ""),
                "xmid": str(expected_xmid or ""),
            },
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
            len(summary.errors),
            speed,
            eta_text,
        )

    def _extract_disclosure_start_date(self, html_text: str) -> Optional[dt.date]:
        soup = BeautifulSoup(html_text, "html.parser")
        text = soup.get_text(" ", strip=True)
        for pattern in DISCLOSURE_START_PATTERNS:
            match = re.search(pattern, text)
            if match:
                parsed = _parse_date(match.group(1))
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

    def _save_complete_page(
        self,
        *,
        rendered_html: str,
        page_url: str,
        html_path: str,
        offline_artifact: Dict[str, Any] | None = None,
    ) -> None:
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

            if offline_artifact:
                script = soup.new_tag("script", id=OFFLINE_ARTIFACT_SCRIPT_ID, type="application/json")
                script.string = json.dumps(offline_artifact, ensure_ascii=False)
                if soup.body is not None:
                    soup.body.append(script)
                else:
                    soup.append(script)

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
                    "exchange": "shanghai",
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
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_EQUITY_TRANSFER,
            list_query_specs=[("CHANQUAN", "2")],
            default_detail_route="jymhchanquan",
            **kwargs,
        )


class ShanghaiCapitalIncreaseDownloader(ShanghaiPhysicalAssetDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_CAPITAL_INCREASE,
            list_query_specs=[("ZENGZI", "2")],
            default_detail_route="jymhzengzi",
            **kwargs,
        )


class ShanghaiPreDisclosureDownloader(ShanghaiPhysicalAssetDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PRE_DISCLOSURE,
            list_query_specs=[("CHANQUAN", "1"), ("ZENGZI", "1")],
            default_detail_route="jymhchanquanyu",
            **kwargs,
        )
