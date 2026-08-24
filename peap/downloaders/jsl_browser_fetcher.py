"""Browser-backed HTML fetcher for JSL-protected exchange pages."""

from __future__ import annotations

import contextlib
import logging
from typing import Dict, Iterator, Optional, Protocol

from ..browser_runtime import launch_chromium_browser_sync
from .common import HttpFetchedText

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
window.chrome = window.chrome || { runtime: {} };
"""

CHALLENGE_HINTS = (
    "__jsl_clearance",
    "location.href=location.pathname+location.search",
)


class BrowserFetchCallable(Protocol):
    def __call__(self, url: str, rendered: bool = False) -> HttpFetchedText: ...

def _is_challenge_html(text: str) -> bool:
    low = str(text or "").lower()
    return any(hint in low for hint in CHALLENGE_HINTS)


@contextlib.contextmanager
def open_jsl_browser_fetcher(
    *,
    warmup_url: str,
    request_headers: Dict[str, str],
    timeout: int,
    logger: Optional[logging.Logger] = None,
) -> Iterator[BrowserFetchCallable]:
    from playwright.sync_api import sync_playwright

    render_timeout_ms = max(90, int(timeout or 30)) * 1000
    user_agent = request_headers.get("User-Agent", "")
    browser = None
    context = None
    page = None
    last_referer = warmup_url

    def warmup() -> None:
        try:
            page.goto(warmup_url, wait_until="domcontentloaded", timeout=render_timeout_ms)
            page.wait_for_timeout(6000)
            html = page.content()
            if _is_challenge_html(html):
                page.reload(wait_until="domcontentloaded", timeout=render_timeout_ms)
                page.wait_for_timeout(5000)
        except Exception as exc:  # noqa: BLE001
            if logger is not None:
                logger.debug("JSL warmup failed: url=%s error=%s", warmup_url, exc)

    def request_fetch(url: str, referer: str) -> tuple[int, str, str]:
        headers = {
            "Accept": request_headers.get("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
            "Accept-Language": request_headers.get("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8"),
            "Referer": referer,
        }
        response = context.request.get(url, headers=headers, timeout=render_timeout_ms)
        return int(response.status), response.text(), str(response.url or url)

    def page_fetch(url: str, referer: str) -> tuple[int, str, str]:
        response = page.goto(
            url,
            wait_until="domcontentloaded",
            referer=referer,
            timeout=render_timeout_ms,
        )
        page.wait_for_timeout(3000)
        status = int(response.status) if response is not None else 0
        return status, page.content(), page.url

    def fetch(url: str, rendered: bool = False) -> HttpFetchedText:
        nonlocal last_referer
        last_reason = ""
        for attempt in range(1, 4):
            if attempt > 1:
                warmup()

            if not rendered:
                try:
                    status, html, final_url = request_fetch(url, last_referer)
                    if 200 <= status <= 299 and not _is_challenge_html(html):
                        last_referer = final_url or url
                        return HttpFetchedText(
                            html,
                            source_url=url,
                            final_url=final_url or url,
                            http_status=status,
                        )
                    last_reason = f"request-http-{status} challenge={_is_challenge_html(html)} html_len={len(html)}"
                except Exception as exc:  # noqa: BLE001
                    last_reason = f"request-failed: {exc}"

            try:
                status, html, final_url = page_fetch(url, last_referer)
                if 200 <= status <= 299 and not _is_challenge_html(html):
                    last_referer = final_url or url
                    return HttpFetchedText(
                        html,
                        source_url=url,
                        final_url=final_url or url,
                        http_status=status,
                    )
                last_reason = f"page-http-{status} challenge={_is_challenge_html(html)} html_len={len(html)}"
            except Exception as exc:  # noqa: BLE001
                last_reason = f"page-failed: {exc}"

        raise RuntimeError(f"browser-fetch-failed url={url}: {last_reason}")

    with sync_playwright() as playwright:
        browser = launch_chromium_browser_sync(
            playwright,
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=user_agent,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            ignore_https_errors=True,
        )
        context.add_init_script(STEALTH_JS)
        page = context.new_page()
        try:
            warmup()
            yield fetch
        finally:
            if page is not None:
                page.close()
            if context is not None:
                context.close()
            if browser is not None:
                browser.close()
