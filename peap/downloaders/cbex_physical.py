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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from bs4 import BeautifulSoup

from ..constants import (
    STATUS_LISTED,
    TYPE_CAPITAL_INCREASE,
    TYPE_EQUITY_TRANSFER,
    TYPE_PHYSICAL_ASSET,
    TYPE_PRE_DISCLOSURE,
)
from ..submission_layout import resolve_submission_snapshot_target

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
class DownloadSummary:
    pages_requested: int = 0
    listed_items: int = 0
    detail_candidates: int = 0
    detail_fetched: int = 0
    saved: int = 0
    skipped_by_list_date: int = 0
    skipped_by_detail_date: int = 0
    skipped_by_resume: int = 0
    detail_failed: int = 0
    list_unaccounted: int = 0
    detail_unaccounted: int = 0
    candidate_dates: List[str] = field(default_factory=list)
    candidate_entries: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


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


class _ChallengeError(RuntimeError):
    pass


def _parse_date(value: Any) -> Optional[dt.date]:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    if not raw:
        return None
    raw = re.sub(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?", r"\1-\2-\3", raw)
    raw = raw.replace("/", "-").replace(".", "-")
    if "T" in raw:
        raw = raw.split("T", 1)[0]
    if " " in raw:
        raw = raw.split(" ", 1)[0]
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", raw)
    if not m:
        return None
    try:
        y, mo, d = (int(x) for x in m.groups())
        return dt.date(y, mo, d)
    except ValueError:
        return None


def _in_range(value: Optional[dt.date], start: Optional[dt.date], end: Optional[dt.date]) -> bool:
    if value is None:
        return False
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True


def _safe_filename(name: str) -> str:
    t = re.sub(r"[\\/:*?\"<>|]+", "_", str(name or "").strip())
    return t or "unknown"


def _is_challenge_html(text: str) -> bool:
    low = text.lower()
    return any(h in low for h in CHALLENGE_HINTS)


def _skip_asset_url(v: str) -> bool:
    t = v.strip().lower()
    return (not t) or t.startswith(("#", "data:", "javascript:", "mailto:", "tel:", "blob:"))


class CbexPhysicalAssetDownloader:
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
            list_sources = [
                _ListSource(
                    label=ASSET_TYPES[asset_type],
                    business_type="SW",
                    referer=ASSET_REFERERS[asset_type],
                    asset_type=asset_type,
                )
                for asset_type in ASSET_TYPES
            ]
        self.list_sources = list(list_sources)
        self.include_pre_disclosure = include_pre_disclosure
        self.logger = logger or logging.getLogger("parser_v2")
        self.item_saved_callback = item_saved_callback
        self._render_timeout_ms = max(90, self.timeout) * 1000

    def run(
        self,
        *,
        start_date: Optional[str],
        end_date: Optional[str],
        list_only: bool = False,
        prefetched_candidates: Optional[List[Dict[str, Any]]] = None,
    ) -> DownloadSummary:
        start = _parse_date(start_date) if start_date else None
        end = _parse_date(end_date) if end_date else None
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
            browser = await pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = await browser.new_context(user_agent=REQUEST_HEADERS["User-Agent"], locale="zh-CN", timezone_id="Asia/Shanghai", ignore_https_errors=True)
            await context.add_init_script(STEALTH_JS)
            page = await context.new_page()
            try:
                await self._warmup(page)
                if prefetched_candidates is None:
                    for source in self.list_sources:
                        await self._collect_by_source(
                            context=context,
                            page=page,
                            source=source,
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
                summary.errors.append(f"cbex-list-failed: {exc}")
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

                async def worker(c: _Candidate):
                    nonlocal done
                    async with sem:
                        await self._process_candidate(context=context, candidate=c, summary=summary, start=start, end=end, timeout_cls=PWTimeout)
                    async with lock:
                        done += 1
                        elapsed = max(time.monotonic() - t0, 0.001)
                        self.logger.info("Detail progress: %s/%s saved=%s errors=%s speed=%.2f/min", done, len(cands), summary.saved, len(summary.errors), done / elapsed * 60)

                await asyncio.gather(*[asyncio.create_task(worker(c)) for c in cands])
            else:
                self.logger.info("No candidate details to download.")

            list_accounted = summary.skipped_by_list_date + summary.skipped_by_resume + summary.detail_candidates
            detail_accounted = summary.saved + summary.skipped_by_detail_date + summary.detail_failed
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
    ) -> None:
        p1 = await self._api_with_retry(context=context, page=page, source=source, page_index=1)
        summary.pages_requested += 1
        total = self._total_pages(p1)
        if self.max_pages is not None:
            total = min(total if total > 0 else self.max_pages, self.max_pages)

        self._rows_to_candidates(
            rows=self._rows(p1),
            source=source,
            outdir=outdir,
            summary=summary,
            seen=seen,
            cands=cands,
            start=start,
            end=end,
        )
        self.logger.info(
            "List progress[%s]: page 1%s listed=%s candidates=%s",
            source.label,
            f"/{total}" if total else "",
            summary.listed_items,
            len(cands),
        )

        if total <= 1:
            return
        for i in range(2, total + 1):
            data = await self._api_with_retry(context=context, page=page, source=source, page_index=i)
            summary.pages_requested += 1
            self._rows_to_candidates(
                rows=self._rows(data),
                source=source,
                outdir=outdir,
                summary=summary,
                seen=seen,
                cands=cands,
                start=start,
                end=end,
            )
            self.logger.info(
                "List progress[%s]: page %s/%s listed=%s candidates=%s",
                source.label,
                i,
                total,
                summary.listed_items,
                len(cands),
            )
            await asyncio.sleep(0.25 + random.random() * 0.45)

    async def _api_with_retry(self, *, context, page, source: _ListSource, page_index: int) -> Dict[str, Any]:
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

    async def _api_one(self, *, context, source: _ListSource, page_index: int) -> Dict[str, Any]:
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
        resp = await context.request.get(
            API_URL,
            params=params,
            headers={**REQUEST_HEADERS, "Referer": source.referer},
            timeout=min(self.timeout, 20) * 1000,
        )
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"api-http-{resp.status}")
        if _is_challenge_html(text):
            raise RuntimeError("api-challenge")
        m = re.search(r"^[^(]+\((.*)\)\s*;?\s*$", text, re.S)
        if not m:
            raise RuntimeError("api-jsonp-parse-failed")
        return json.loads(m.group(1))

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
                summary.errors.append(f"prefetched-entry-{index}-invalid-format")
                continue
            summary.listed_items += 1
            entry = dict(raw)

            row_raw = entry.get("row")
            row = row_raw if isinstance(row_raw, dict) else {}
            if row and not self._should_keep_row(row):
                continue

            uid = str(entry.get("uid") or "").strip()
            code = str(entry.get("code") or "").strip().upper()
            url = str(entry.get("url") or "").strip()
            if not uid:
                uid = code or url or hashlib.md5(
                    json.dumps(entry, ensure_ascii=False).encode("utf-8")
                ).hexdigest()[:16]

            if uid in seen:
                continue
            seen.add(uid)

            d = _parse_date(entry.get("list_disclosure_start") or row.get("disclosuretime"))
            if d and "list_disclosure_start" not in row:
                row = {**row, "list_disclosure_start": d.isoformat()}
            if start or end:
                if d and not _in_range(d, start, end):
                    summary.skipped_by_list_date += 1
                    continue

            if not url:
                if row:
                    href = str(row.get("url") or "").strip()
                    url = urllib.parse.urljoin(BASE_URL, href) if href else ""
                if not url:
                    summary.errors.append(f"prefetched-entry-{index}-missing-detail-url: id={uid}")
                    continue

            project_name = str(entry.get("project_name") or row.get("title") or row.get("name") or "").strip()
            filename_seed = code or uid
            path, _ = resolve_submission_snapshot_target(
                archive_root=outdir,
                project_code=filename_seed,
                project_name=project_name,
                listing_date=d.isoformat() if d else "",
            )
            files_dir = f"{os.path.splitext(path)[0]}_files"
            if self.resume and os.path.isfile(path) and os.path.isdir(files_dir):
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
                    "list_disclosure_start": d.isoformat() if d else None,
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
                continue

            code = str(r.get("code") or "").strip().upper()
            href = str(r.get("url") or "").strip()
            url = urllib.parse.urljoin(BASE_URL, href) if href else ""
            uid = code or url or hashlib.md5(json.dumps(r, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
            if uid in seen:
                continue
            seen.add(uid)

            d = _parse_date(r.get("disclosuretime"))
            if start or end:
                if d and not _in_range(d, start, end):
                    summary.skipped_by_list_date += 1
                    continue

            if not url:
                summary.errors.append(f"missing-detail-url: id={uid}")
                continue

            project_name = str(r.get("title") or r.get("name") or "").strip()
            path, _ = resolve_submission_snapshot_target(
                archive_root=outdir,
                project_code=code or uid,
                project_name=project_name,
                listing_date=d.isoformat() if d else "",
            )
            files_dir = f"{os.path.splitext(path)[0]}_files"
            if self.resume and os.path.isfile(path) and os.path.isdir(files_dir):
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
                    "list_disclosure_start": d.isoformat() if d else None,
                }
            )
            if d is not None:
                summary.candidate_dates.append(d.isoformat())

    async def _process_candidate(self, *, context, candidate: _Candidate, summary: DownloadSummary, start: Optional[dt.date], end: Optional[dt.date], timeout_cls) -> None:
        html: Optional[str] = None
        last: Optional[Exception] = None

        for k in range(1, 4):
            page = await context.new_page()
            try:
                html = await self._fetch_html(page=page, url=candidate.url, code=candidate.code)
                summary.detail_fetched += 1
                break
            except (_ChallengeError, timeout_cls) as exc:
                last = exc
                if k <= 2:
                    await asyncio.sleep(1.2 * k + random.random())
                else:
                    summary.errors.append(f"id={candidate.uid} timeout-or-challenge: {exc}")
            except Exception as exc:  # noqa: BLE001
                last = exc
                if k <= 2:
                    await asyncio.sleep(1.2 * k + random.random())
                else:
                    summary.errors.append(f"id={candidate.uid} fetch-failed: {exc}")
            finally:
                await page.close()

        if html is None:
            if last:
                self.logger.error("Detail fetch failed: id=%s err=%s", candidate.uid, last)
            summary.detail_failed += 1
            return

        ds = self._extract_disclosure_start_date(html)
        list_ds = _parse_date(
            candidate.row.get("list_disclosure_start")
            or candidate.row.get("disclosuretime")
        )
        if start or end:
            check_date = list_ds if list_ds is not None else ds
            if check_date is not None and not _in_range(check_date, start, end):
                summary.skipped_by_detail_date += 1
                return

        try:
            await self._save_complete_page(html=html, page_url=candidate.url, html_path=candidate.html_path, request_context=context.request)
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"id={candidate.uid} save-failed: {exc}")
            summary.detail_failed += 1
            return

        if self.save_json:
            p = os.path.splitext(candidate.html_path)[0] + ".json"
            await asyncio.get_running_loop().run_in_executor(None, lambda: open(p, "w", encoding="utf-8").write(json.dumps({"id": candidate.uid, "code": candidate.code, "url": candidate.url, "row": candidate.row, "disclosure_start_date": ds.isoformat() if ds else None}, ensure_ascii=False, indent=2)))
        self._notify_item_saved(candidate=candidate, disclosure_start=ds or list_ds)
        summary.saved += 1

    async def _fetch_html(self, *, page, url: str, code: str) -> str:
        await page.goto(url, wait_until="domcontentloaded", timeout=self._render_timeout_ms)
        try:
            await page.wait_for_function(
                """
                (expectedCode) => {
                    const t = (document.body && document.body.innerText ? document.body.innerText : '').toUpperCase();
                    return t.includes('项目编号') || (expectedCode && t.includes(String(expectedCode).toUpperCase()));
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
        return html

    def _extract_disclosure_start_date(self, html: str) -> Optional[dt.date]:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        for p in DISCLOSURE_START_PATTERNS:
            m = re.search(p, text)
            if m:
                d = _parse_date(m.group(1))
                if d:
                    return d
        return None

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
        except Exception:
            if os.path.isdir(temp_assets_dir):
                shutil.rmtree(temp_assets_dir, ignore_errors=True)
            if os.path.isfile(temp_html_path):
                try:
                    os.remove(temp_html_path)
                except OSError:
                    pass
            raise

    def _notify_item_saved(self, *, candidate: _Candidate, disclosure_start: Optional[dt.date]) -> None:
        callback = self.item_saved_callback
        if callback is None:
            return
        try:
            callback(
                {
                    "source_file": candidate.html_path,
                    "page_url": candidate.url,
                    "project_code": candidate.code,
                    "project_name": str(candidate.row.get("title") or candidate.row.get("name") or ""),
                    "listing_date": disclosure_start.isoformat() if disclosure_start else "",
                    "exchange": "beijing",
                    "project_type": self.output_type,
                    "row": candidate.row,
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("item_saved_callback failed: %s", exc)

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
            list_sources=[
                _ListSource(
                    label="股权转让",
                    business_type="JC",
                    referer=REFERER_CQZR,
                )
            ],
            include_pre_disclosure=False,
            **kwargs,
        )


class CbexCapitalIncreaseDownloader(CbexPhysicalAssetDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_CAPITAL_INCREASE,
            list_sources=[
                _ListSource(
                    label="增资扩股",
                    business_type="GZ",
                    referer=REFERER_QYZZ,
                )
            ],
            include_pre_disclosure=False,
            **kwargs,
        )


class CbexPreDisclosureDownloader(CbexPhysicalAssetDownloader):
    def __init__(self, **kwargs):
        super().__init__(
            output_type=TYPE_PRE_DISCLOSURE,
            list_sources=[
                _ListSource(
                    label="股权转让(预披露)",
                    business_type="JC",
                    referer=REFERER_CQZR,
                ),
                _ListSource(
                    label="增资扩股(预披露)",
                    business_type="GZ",
                    referer=REFERER_QYZZ,
                ),
            ],
            include_pre_disclosure=True,
            **kwargs,
        )
