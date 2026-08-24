#!/usr/bin/env python3
"""Collect the official public-resource equity-transfer deal archive.

The collector is deliberately independent of PEAP's SQLite database.  It
queries the official listing API once per calendar month, closes pagination
against the server-declared totals, archives every listing/detail response as
raw bytes, and materializes parser-compatible MHTML only for records whose
two-factor exchange attribution is accepted.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import hashlib
import html
import http.client
import json
import math
import os
import re
import shutil
import socket
import ssl
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from bs4 import BeautifulSoup

from peap.public_resource_attribution import EXCHANGE_RULES, normalize_public_resource_exchange
from peap.public_resource_deals import build_workbook, parse_mhtml_file
from peap_core.runtime_paths import resolve_runtime_workspace_paths

BASE_URL = "https://www.ggzy.gov.cn"
SEARCH_URL = f"{BASE_URL}/information/pubTradingInfo/getTradList"
LIST_URL = f"{BASE_URL}/deal/dealList.html"
MANUAL_ARCHIVE_DIRECTORY = "公共资源网四大交易所股权转让成交信息统计"
EXCHANGES = frozenset({"北交互联", "上海联合产权交易所", "天津产权交易中心", "重庆联合产权交易所"})
TARGET_LISTING_PLATFORM_NAMES = frozenset(
    platform_name
    for rule in EXCHANGE_RULES
    if rule.exchange_name in EXCHANGES
    for platform_name in rule.platform_names
)
SEARCH_FIELDS = ("SOURCE_TYPE", "DEAL_TIME", "TIMEBEGIN", "TIMEEND", "DEAL_CLASSIFY", "DEAL_STAGE", "FINDTXT", "PAGENUMBER")
TABLE_FIELDS = ("项目编号", "项目名称", "交易方式", "受让方名称", "转让标的评估值或账面净值", "成交金额", "成交日期")
MHTML_PARSED_FIELD_NAMES = {
    "转让标的评估值或账面净值": "转让标的评估值",
}
DEFAULT_RETRIES = 3
DEFAULT_SEARCH_DELAY_SECONDS = 20.0
DEFAULT_DETAIL_DELAY_SECONDS = 0.5
RATE_LIMIT_RETRY_DELAYS_SECONDS = (20.0, 60.0, 120.0)
NON_RETRYABLE_BUSINESS_CODES = frozenset({801, 804, 829})


class CollectionError(RuntimeError):
    """A completeness or evidence invariant failed."""


class ServiceAbortError(CollectionError):
    """A service-wide refusal makes additional record requests unsafe."""


class FailClosedHTTPError(ServiceAbortError):
    """The origin explicitly refused the request and collection must stop."""


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _month_ranges(year: int, through: dt.date) -> list[tuple[str, str, str]]:
    result = []
    for month in range(1, 13):
        begin = dt.date(year, month, 1)
        if begin > through:
            break
        end = dt.date(year, month, calendar.monthrange(year, month)[1])
        end = min(end, through)
        result.append((f"{year:04d}-{month:02d}", begin.isoformat(), end.isoformat()))
    return result


def _ssl_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # The IPv6 origin negotiates TLS 1.3 quickly but can stall when the client
    # caps the handshake at TLS 1.2.  Keep TLS 1.2 as the security floor while
    # allowing the standard stack to negotiate the current protocol.
    context.set_ciphers("ECDHE-RSA-AES128-GCM-SHA256:AES128-SHA")
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_default_certs()
    return context


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection whose DNS selection is explicitly AF_INET-only."""

    def connect(self) -> None:
        candidates = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)
        if not candidates:
            raise OSError(f"no IPv4 address for {self.host}")
        last_error: OSError | None = None
        for family, socktype, protocol, _canonname, sockaddr in candidates:
            sock = socket.socket(family, socktype, protocol)
            try:
                sock.settimeout(self.timeout)
                sock.connect(sockaddr)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as error:
                sock.close()
                last_error = error
        raise OSError(f"all IPv4 connections failed for {self.host}: {last_error}")


class _IPv4HTTPSHandler(HTTPSHandler):
    def https_open(self, request):  # type: ignore[no-untyped-def]
        return self.do_open(_IPv4HTTPSConnection, request, context=self._context, check_hostname=self._check_hostname)


def _ipv4_opener():
    """Ignore ambient proxy variables and use the server-compatible TLS stack."""
    # The origin currently returns a misleading business-busy response for the
    # hand-rolled ``_IPv4HTTPSConnection`` fingerprint.  The standard handler
    # still resolves the available IPv4 address in this environment and keeps
    # normal SNI/HTTP negotiation intact.
    return build_opener(ProxyHandler({}), HTTPSHandler(context=_ssl_context()))


def _headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Encoding": "identity",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        "Access-Control-Allow-Origin": "http://ggzy.gov.cn",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": LIST_URL,
        "X-Pass-Token": "",
    }


def _parse_curl_response_headers(raw: bytes) -> dict[str, str]:
    """Parse the final HTTP header block emitted by ``curl --dump-header``."""
    normalized = raw.replace(b"\r\n", b"\n")
    blocks = [block for block in normalized.split(b"\n\n") if block.strip()]
    for block in reversed(blocks):
        lines = block.splitlines()
        if not lines or not lines[0].startswith(b"HTTP/"):
            continue
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if b":" not in line:
                continue
            key, value = line.split(b":", 1)
            name = key.decode("latin-1").strip()
            text = value.decode("latin-1").strip()
            if name:
                headers[name] = text
        return headers
    raise CollectionError("curl response headers are invalid")


def _request_via_curl_transport(
    url: str,
    *,
    body: bytes | None,
    timeout: float,
) -> tuple[int, bytes, dict[str, str], str]:
    """Use the macOS system HTTP stack when urllib/Chromium is throttled.

    The official endpoint currently distinguishes transport fingerprints.  A
    stock macOS ``curl`` request succeeds in cases where its own Chromium page
    receives business code 800.  macOS ships this binary, so this fallback does
    not add an installation requirement to the offline App.
    """
    executable = shutil.which("curl")
    if not executable:
        raise CollectionError("curl transport is unavailable")
    effective_timeout = max(0.1, float(timeout))
    with TemporaryDirectory(prefix="peap-public-resource-curl-") as directory:
        root = Path(directory)
        header_path = root / "headers.txt"
        response_path = root / "response.bin"
        command = [
            executable,
            "--ipv4",
            "--http1.1",
            "--silent",
            "--show-error",
            "--location",
            "--max-redirs",
            "3",
            "--noproxy",
            "*",
            "--connect-timeout",
            f"{min(effective_timeout, 10.0):.3f}",
            "--max-time",
            f"{effective_timeout:.3f}",
            "--dump-header",
            str(header_path),
            "--output",
            str(response_path),
            "--write-out",
            "%{http_code}\n%{url_effective}\n",
        ]
        for key, value in _headers().items():
            # A trailing colon is the exact successful system-curl request for
            # the official endpoint; curl omits rather than invents a value.
            command.extend(("--header", f"{key}: {value}" if value else f"{key}:"))
        if body is None:
            command.extend(("--request", "GET"))
            input_body = None
        else:
            command.extend(("--request", "POST", "--data-binary", "@-"))
            input_body = body
        command.append(url)
        try:
            completed = subprocess.run(
                command,
                input=input_body,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=effective_timeout + 5.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CollectionError(
                f"curl transport failed: {url}: {type(error).__name__}: {error}"
            ) from error
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise CollectionError(
                f"curl transport failed: {url}: exit={completed.returncode}: {detail}"
            )
        metadata = completed.stdout.decode("utf-8", errors="replace").splitlines()
        if len(metadata) < 2:
            raise CollectionError(f"curl transport metadata is invalid: {url}")
        try:
            status = int(metadata[-2])
        except ValueError as error:
            raise CollectionError(f"curl transport status is invalid: {url}") from error
        final_url = metadata[-1].strip() or url
        try:
            raw = response_path.read_bytes()
            headers = _parse_curl_response_headers(header_path.read_bytes())
        except OSError as error:
            raise CollectionError(f"curl transport output is missing: {url}") from error
        if status in (403, 429):
            raise FailClosedHTTPError(f"HTTP {status} fail-closed: {url}")
        if status < 200 or status >= 300:
            raise CollectionError(f"curl transport HTTP {status}: {url}")
        return status, raw, headers, final_url


def _browser_fetch_headers() -> dict[str, str]:
    """Headers used by the site's own page-level POST request."""
    return {
        "Content-Type": "application/x-www-form-urlencoded",
        "Access-Control-Allow-Origin": "http://ggzy.gov.cn",
        "X-Pass-Token": "",
    }


def _request_via_browser_transport_unchecked(
    url: str,
    *,
    body: bytes | None,
    timeout: float,
) -> tuple[int, bytes, dict[str, str], str]:
    """Replay an official request inside a real Chromium page.

    APIRequestContext has its own Node.js user agent and TLS fingerprint.  The
    official site applies transport-sensitive throttling, so the fallback must
    use page-level ``fetch`` and the shared bundled/system-Chrome launch chain.
    """
    from playwright.sync_api import sync_playwright

    from peap.browser_runtime import launch_chromium_browser_sync

    with sync_playwright() as playwright:
        browser = launch_chromium_browser_sync(playwright, headless=True)
        try:
            context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
            page = context.new_page()
            timeout_ms = max(1, int(timeout * 1000))
            page.goto(LIST_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            if body is not None:
                try:
                    encoded_body = body.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise CollectionError("browser POST body is not UTF-8 form data") from error
                with page.expect_response(
                    lambda candidate: candidate.url == url and candidate.request.method == "POST",
                    timeout=timeout_ms,
                ) as response_info:
                    page.evaluate(
                        """
                        async ({ url, body, headers, timeoutMs }) => {
                          const controller = new AbortController();
                          const timer = setTimeout(() => controller.abort(), timeoutMs);
                          try {
                            const response = await fetch(url, {
                              method: "POST",
                              headers,
                              body,
                              signal: controller.signal,
                            });
                            await response.arrayBuffer();
                          } finally {
                            clearTimeout(timer);
                          }
                        }
                        """,
                        {
                            "url": url,
                            "body": encoded_body,
                            "headers": _browser_fetch_headers(),
                            "timeoutMs": timeout_ms,
                        },
                    )
                response = response_info.value
            else:
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                if response is None:
                    raise CollectionError(f"browser navigation produced no response: {url}")
            status = int(response.status)
            if status >= 400:
                raise HTTPError(
                    url,
                    status,
                    f"HTTP Error {status}",
                    hdrs=None,
                    fp=None,
                )
            raw = response.body()
            headers = {
                str(key): str(value)
                for key, value in response.all_headers().items()
            }
            return status, raw, headers, response.url
        finally:
            browser.close()


def _request_via_browser_transport(
    url: str,
    *,
    body: bytes | None,
    timeout: float,
) -> tuple[int, bytes, dict[str, str], str]:
    """Run the browser transport behind one stable exception boundary."""
    try:
        return _request_via_browser_transport_unchecked(
            url,
            body=body,
            timeout=timeout,
        )
    except FailClosedHTTPError:
        raise
    except HTTPError as error:
        if error.code in (403, 429):
            raise FailClosedHTTPError(
                f"HTTP {error.code} fail-closed: {url}"
            ) from error
        raise CollectionError(
            f"browser transport HTTP {error.code}: {url}"
        ) from error
    except CollectionError:
        raise
    except Exception as error:  # Playwright, TLS, and browser launch errors.
        raise CollectionError(
            f"browser transport failed: {url}: "
            f"{type(error).__name__}: {error}"
        ) from error


def _business_retry_delay(business_code: object, attempt: int) -> float:
    if business_code == 800:
        index = min(max(0, attempt - 1), len(RATE_LIMIT_RETRY_DELAYS_SECONDS) - 1)
        return RATE_LIMIT_RETRY_DELAYS_SECONDS[index]
    return min(30.0, 2.0 * (2 ** max(0, attempt - 1)))


def _transport_for_attempt(attempt: int) -> str:
    if attempt <= 1:
        return "standard"
    if attempt == 2:
        return "curl"
    return "browser"


def _retry_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    *,
    phase: str,
    attempt: int,
    retries: int,
    delay: float,
    business_code: object,
    next_transport: str,
    page: int = 0,
    role: str = "",
) -> None:
    if callback is None:
        return
    callback(
        {
            "phase": phase,
            "attempt": attempt,
            "attempt_total": retries + 1,
            "retry_in_seconds": max(0, int(math.ceil(delay))),
            "business_code": int(business_code) if isinstance(business_code, int) else 0,
            "page": page,
            # ``transport`` is retained for the existing event contract.  It
            # has always represented the upcoming retry; ``next_transport``
            # removes that ambiguity for direct callback consumers.
            "transport": next_transport,
            "next_transport": next_transport,
            "role": role,
        }
    )


def _request(
    url: str,
    *,
    body: bytes | None,
    timeout: float,
    retries: int,
    allow_browser_fallback: bool = True,
) -> tuple[int, bytes, dict[str, str], str]:
    last: Exception | None = None
    last_was_transport_error = False
    for attempt in range(max(1, retries + 1)):
        request = Request(url, data=body, headers=_headers(), method="POST" if body is not None else "GET")
        try:
            opener = _ipv4_opener()
            with opener.open(request, timeout=timeout) as response:
                return int(response.status), response.read(), {str(k): str(v) for k, v in response.headers.items()}, str(response.url)
        except HTTPError as error:
            if error.code in (403, 429):
                raise FailClosedHTTPError(
                    f"HTTP {error.code} fail-closed: {url}"
                ) from error
            last = error
            last_was_transport_error = False
            if error.code < 500:
                raise
        except (URLError, TimeoutError, OSError) as error:
            last = error
            last_was_transport_error = True
        if attempt < retries:
            time.sleep(min(30.0, 0.5 * (2**attempt)))
    if last_was_transport_error and allow_browser_fallback:
        try:
            return _request_via_browser_transport(url, body=body, timeout=timeout)
        except FailClosedHTTPError:
            raise
        except HTTPError as browser_error:
            if browser_error.code in (403, 429):
                raise FailClosedHTTPError(
                    f"HTTP {browser_error.code} fail-closed: {url}"
                ) from browser_error
            last = browser_error
        except CollectionError as browser_error:
            last = browser_error
    raise CollectionError(f"request failed after retries: {url}: {last}") from last


def _params(begin: str, end: str, page: int) -> dict[str, str]:
    return {"SOURCE_TYPE": "1", "DEAL_TIME": "06", "TIMEBEGIN": begin, "TIMEEND": end, "DEAL_CLASSIFY": "05", "DEAL_STAGE": "0502", "FINDTXT": "股权", "PAGENUMBER": str(page)}


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise CollectionError(f"refusing to write evidence through symlink: {path}")
    if path.exists():
        if path.read_bytes() != data:
            raise CollectionError(f"existing evidence differs; refusing overwrite: {path}")
        return
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            if path.read_bytes() != data:
                raise CollectionError(f"existing evidence differs; refusing overwrite: {path}")
            return
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _write_json(path: Path, value: object) -> None:
    _write_bytes(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def _sidecar(path: Path, *, url: str, final_url: str, status: int, headers: dict[str, str], body: bytes, role: str, params: dict[str, str] | None = None) -> None:
    _write_json(Path(str(path) + ".sidecar.json"), {"url": url, "final_url": final_url, "status": status, "headers": dict(sorted(headers.items(), key=lambda item: item[0].lower())), "bytes": len(body), "sha256": _sha(body), "role": role, "params": params or {}})


def _write_transient_attempt(
    path: Path,
    body: bytes,
    *,
    url: str,
    final_url: str,
    status: int,
    headers: dict[str, str],
    role: str,
    params: dict[str, str] | None,
) -> Path:
    """Persist a transient response without overwriting prior evidence.

    A resumed run intentionally reuses the logical attempt number.  Response
    headers (notably ``Date`` and connection metadata) may differ even when
    the body is byte-identical, so writing the same sidecar path would either
    destroy provenance or fail the atomic evidence guard.  Keep the original
    path for the first observation and allocate a deterministic repeat suffix
    for later observations.
    """
    candidate = path
    repeat = 0
    while candidate.exists() or Path(str(candidate) + ".sidecar.json").exists():
        repeat += 1
        candidate = path.with_name(f"{path.stem}-repeat-{repeat}{path.suffix}")
    _write_bytes(candidate, body)
    _sidecar(
        candidate,
        url=url,
        final_url=final_url,
        status=status,
        headers=headers,
        body=body,
        role=role,
        params=params,
    )
    return candidate


def _quarantine_canonical(path: Path, *, reason: str) -> Path | None:
    """Preserve unusable canonical evidence, then free its canonical path."""
    sidecar_path = Path(str(path) + ".sidecar.json")
    if not any(
        existing.exists() or existing.is_symlink()
        for existing in (path, sidecar_path)
    ):
        return None
    for existing in (path, sidecar_path):
        if existing.is_symlink():
            raise CollectionError(
                f"refusing to quarantine evidence symlink: {existing}"
            )
        if existing.exists() and not existing.is_file():
            raise CollectionError(
                f"canonical evidence is not a regular file: {existing}"
            )

    quarantine_root = path.parent / "attempts" / "quarantine"
    candidate = quarantine_root / path.name
    repeat = 0
    while any(
        existing.exists()
        for existing in (
            candidate,
            Path(str(candidate) + ".sidecar.json"),
            Path(str(candidate) + ".quarantine.json"),
        )
    ):
        repeat += 1
        candidate = quarantine_root / f"{path.stem}-repeat-{repeat}{path.suffix}"

    payload_present = path.is_file()
    sidecar_present = sidecar_path.is_file()
    if payload_present:
        _write_bytes(candidate, path.read_bytes())
    if sidecar_present:
        _write_bytes(
            Path(str(candidate) + ".sidecar.json"),
            sidecar_path.read_bytes(),
        )
    _write_json(
        Path(str(candidate) + ".quarantine.json"),
        {
            "original_path": str(path),
            "reason": reason,
            "payload_present": payload_present,
            "sidecar_present": sidecar_present,
        },
    )

    # Publish the quarantine copy completely before releasing the canonical
    # name, so a stopped process never destroys the only copy of the response.
    if sidecar_present:
        sidecar_path.unlink()
    if payload_present:
        path.unlink()
    return candidate


def _canonical_evidence_present(path: Path) -> bool:
    sidecar_path = Path(str(path) + ".sidecar.json")
    return any(
        existing.exists() or existing.is_symlink()
        for existing in (path, sidecar_path)
    )


def _resume_evidence(path: Path, *, url: str, role: str, params: dict[str, str] | None) -> tuple[int, bytes, dict[str, str], str] | None:
    """Return only evidence whose payload and identity sidecar still agree."""
    sidecar_path = Path(str(path) + ".sidecar.json")
    if not path.exists() and not sidecar_path.exists():
        return None
    # A process can stop after the payload's atomic publication but before its
    # sidecar.  Re-fetching is safe here: _write_bytes accepts the recovery
    # only when the official response is byte-identical to the orphan payload.
    if path.is_file() and not sidecar_path.exists():
        return None
    if not path.is_file() or not sidecar_path.is_file():
        raise CollectionError(f"incomplete resume evidence: {path}")
    try:
        body = path.read_bytes()
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        status = int(sidecar["status"])
        headers = sidecar["headers"]
        final_url = str(sidecar["final_url"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise CollectionError(f"invalid resume sidecar: {sidecar_path}") from error
    legacy_roles = {
        "outer": {"detail_outer"},
        "inner": {"detail_inner"},
    }
    if (
        not isinstance(headers, dict)
        or sidecar.get("url") != url
        or sidecar.get("role") not in {role, *legacy_roles.get(role, set())}
        or sidecar.get("params", {}) != (params or {})
        or sidecar.get("sha256") != _sha(body)
        or sidecar.get("bytes") != len(body)
        or status < 200
        or status > 299
    ):
        raise CollectionError(f"resume evidence identity differs: {path}")
    return status, body, {str(key): str(value) for key, value in headers.items()}, final_url


def _fetch_evidence(path: Path, *, url: str, body: bytes | None, timeout: float, retries: int, role: str, params: dict[str, str] | None, resume: bool) -> tuple[int, bytes, dict[str, str], str]:
    existing = _resume_evidence(path, url=url, role=role, params=params) if resume else None
    if existing is not None:
        return existing
    status, raw, headers, final_url = _request(url, body=body, timeout=timeout, retries=retries)
    _write_bytes(path, raw)
    _sidecar(path, url=url, final_url=final_url, status=status, headers=headers, body=raw, role=role, params=params)
    return status, raw, headers, final_url


def _fetch_search_evidence(
    path: Path,
    *,
    url: str,
    body: bytes,
    params: dict[str, str],
    timeout: float,
    retries: int,
    resume: bool,
    page: int,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    before_network_request: Callable[[], None] | None = None,
) -> tuple[int, bytes, dict[str, str], str]:
    """Fetch a listing page without promoting code=800 responses.

    The public endpoint sometimes returns HTTP 200 with a JSON business-error
    envelope.  Such a response is useful evidence of the transient incident,
    but it must never occupy the canonical page path used by ``--resume``.
    """
    existing = None
    if resume:
        try:
            existing = _resume_evidence(
                path,
                url=url,
                role="search",
                params=params,
            )
        except CollectionError:
            _quarantine_canonical(path, reason="resume_identity_invalid")
        else:
            if existing is None and _canonical_evidence_present(path):
                _quarantine_canonical(path, reason="resume_evidence_incomplete")
    if existing is not None:
        try:
            payload = json.loads(existing[1].decode("utf-8"))
            if not isinstance(payload, dict):
                raise CollectionError(f"search JSON is not an object page {page}")
            _rows(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, CollectionError):
            _quarantine_canonical(path, reason="search_payload_invalid")
        else:
            return existing

    if before_network_request is not None:
        before_network_request()

    def retry_invalid_response(
        *,
        attempt: int,
        raw: bytes,
        status: int,
        headers: dict[str, str],
        final_url: str,
        transport: str,
    ) -> None:
        attempt_path = (
            path.parent
            / "attempts"
            / f"page-{page:05d}-invalid-attempt-{attempt}.body"
        )
        _write_transient_attempt(
            attempt_path,
            raw,
            url=url,
            final_url=final_url,
            status=status,
            headers=headers,
            role="search_transient_invalid_response",
            params={
                **params,
                "attempt": str(attempt),
                "transport": transport,
            },
        )
        if attempt > retries:
            raise CollectionError(
                f"search page {page} invalid response exhausted retries"
            )
        delay = min(30.0, 0.5 * (2 ** (attempt - 1)))
        _retry_progress(
            progress_callback,
            phase="search_invalid_response_backoff",
            attempt=attempt,
            retries=retries,
            delay=delay,
            business_code=0,
            next_transport=_transport_for_attempt(attempt + 1),
            page=page,
        )
        time.sleep(delay)

    for attempt in range(1, retries + 2):
        try:
            transport = _transport_for_attempt(attempt)
            if transport == "standard":
                status, raw, headers, final_url = _request(
                    url,
                    body=body,
                    timeout=timeout,
                    retries=0,
                    allow_browser_fallback=False,
                )
            elif transport == "curl":
                status, raw, headers, final_url = _request_via_curl_transport(
                    url,
                    body=body,
                    timeout=timeout,
                )
            else:
                status, raw, headers, final_url = _request_via_browser_transport(
                    url,
                    body=body,
                    timeout=timeout,
                )
        except FailClosedHTTPError:
            raise
        except CollectionError:
            if attempt > retries:
                raise
            _retry_progress(
                progress_callback,
                phase="search_transport_backoff",
                attempt=attempt,
                retries=retries,
                delay=min(30.0, 0.5 * (2 ** (attempt - 1))),
                business_code=0,
                next_transport=_transport_for_attempt(attempt + 1),
                page=page,
            )
            time.sleep(min(30.0, 0.5 * (2 ** (attempt - 1))))
            continue
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            retry_invalid_response(
                attempt=attempt,
                raw=raw,
                status=status,
                headers=headers,
                final_url=final_url,
                transport=transport,
            )
            continue
        if not isinstance(payload, dict):
            retry_invalid_response(
                attempt=attempt,
                raw=raw,
                status=status,
                headers=headers,
                final_url=final_url,
                transport=transport,
            )
            continue
        business_code = payload.get("code")
        if not isinstance(business_code, int) or isinstance(business_code, bool):
            retry_invalid_response(
                attempt=attempt,
                raw=raw,
                status=status,
                headers=headers,
                final_url=final_url,
                transport=transport,
            )
            continue
        if business_code != 200:
            attempt_path = path.parent / "attempts" / f"page-{page:05d}-attempt-{attempt}.json"
            _write_transient_attempt(
                attempt_path,
                raw,
                url=url,
                final_url=final_url,
                status=status,
                headers=headers,
                role="search_transient_business_error",
                params={
                    **params,
                    "attempt": str(attempt),
                    "business_code": str(business_code),
                    "transport": transport,
                },
            )
            if business_code in NON_RETRYABLE_BUSINESS_CODES:
                reason = "captcha required" if business_code == 829 else "request rejected"
                error_type = ServiceAbortError if business_code == 829 else CollectionError
                raise error_type(f"search page {page} business error {business_code}: {reason}")
            if attempt > retries:
                error_type = ServiceAbortError if business_code == 800 else CollectionError
                raise error_type(
                    f"search page {page} business error {business_code} exhausted retries"
                )
            delay = _business_retry_delay(business_code, attempt)
            _retry_progress(
                progress_callback,
                phase="search_rate_limit_backoff",
                attempt=attempt,
                retries=retries,
                delay=delay,
                business_code=business_code,
                next_transport=_transport_for_attempt(attempt + 1),
                page=page,
            )
            time.sleep(delay)
            continue
        try:
            _rows(payload)
        except CollectionError:
            retry_invalid_response(
                attempt=attempt,
                raw=raw,
                status=status,
                headers=headers,
                final_url=final_url,
                transport=transport,
            )
            continue
        _write_bytes(path, raw)
        _sidecar(path, url=url, final_url=final_url, status=status, headers=headers, body=raw, role="search", params=params)
        return status, raw, headers, final_url
    raise AssertionError("search retry loop did not return")


def _business_error_code(body: bytes) -> int | None:
    """Return an official JSON business-error code, never treating HTML as JSON."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    if isinstance(code, int) and code != 200:
        return code
    if isinstance(code, int):
        raise CollectionError("detail endpoint returned JSON instead of HTML")
    return None


def _valid_outer_html(body: bytes, *, outer_url: str = "") -> None:
    if _business_error_code(body) is not None:
        raise CollectionError("official business-error JSON is not outer HTML")
    soup = BeautifulSoup(body, "html.parser")
    if soup.find() is None:
        raise CollectionError("outer detail is not HTML")
    if outer_url:
        expected = urlsplit(_to_b_url(outer_url))
        expected_identity = (
            expected.scheme.lower(),
            expected.netloc.lower(),
            expected.path,
        )
        candidates: list[str] = []
        for match in re.finditer(
            rb"firstLastUrl\s*=\s*['\"]([^'\"]+)['\"]",
            body,
        ):
            candidates.append(match.group(1).decode("utf-8"))
        candidates.extend(
            _clean(iframe.get("src"))
            for iframe in soup.find_all("iframe")
            if _clean(iframe.get("src"))
        )
        candidates.extend(
            match.group(0).decode("utf-8")
            for match in re.finditer(
                rb"/information/deal/html/[ab]/[^'\" ]+",
                body,
            )
        )
        identities = {
            (
                candidate_url.scheme.lower(),
                candidate_url.netloc.lower(),
                candidate_url.path,
            )
            for candidate in candidates
            for candidate_url in (urlsplit(urljoin(outer_url, candidate)),)
        }
        if expected_identity not in identities:
            raise CollectionError(
                "outer detail inner URL is not bound to the listing record"
            )


def _fetch_detail_evidence(
    path: Path,
    *,
    stem: str,
    role: str,
    url: str,
    timeout: float,
    retries: int,
    resume: bool,
    validator: Any,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    before_network_request: Callable[[], None] | None = None,
) -> tuple[int, bytes, dict[str, str], str, dict[str, Any]]:
    """Fetch a detail page without promoting transient business errors."""
    existing = None
    if resume:
        try:
            existing = _resume_evidence(path, url=url, role=role, params=None)
        except CollectionError:
            _quarantine_canonical(path, reason="resume_identity_invalid")
        else:
            if existing is None and _canonical_evidence_present(path):
                _quarantine_canonical(path, reason="resume_evidence_incomplete")
    if existing is not None:
        try:
            validator(existing[1])
        except ServiceAbortError:
            raise
        except Exception:
            _quarantine_canonical(path, reason="detail_payload_invalid")
        else:
            return (*existing, {"retry_count": 0, "transient_attempts": [], "transport_errors": []})

    attempts: list[dict[str, Any]] = []
    transport_errors: list[str] = []
    if before_network_request is not None:
        before_network_request()

    def retry_invalid_response(
        *,
        attempt: int,
        body: bytes,
        status: int,
        headers: dict[str, str],
        final_url: str,
        transport: str,
    ) -> None:
        attempt_path = (
            path.parent
            / "attempts"
            / f"{stem}-{role}-invalid-attempt-{attempt}.body"
        )
        actual_attempt_path = _write_transient_attempt(
            attempt_path,
            body,
            url=url,
            final_url=final_url,
            status=status,
            headers=headers,
            role=f"{role}_transient_invalid_response",
            params={"attempt": str(attempt), "transport": transport},
        )
        attempts.append(
            {
                "path": str(actual_attempt_path),
                "sha256": _sha(body),
                "status": status,
                "business_code": 0,
                "transport": transport,
                "validation_error": True,
            }
        )
        if attempt > retries:
            raise ServiceAbortError(
                f"detail {role} invalid response exhausted retries"
            )
        delay = min(30.0, 0.5 * (2 ** (attempt - 1)))
        _retry_progress(
            progress_callback,
            phase="detail_invalid_response_backoff",
            attempt=attempt,
            retries=retries,
            delay=delay,
            business_code=0,
            next_transport=_transport_for_attempt(attempt + 1),
            role=role,
        )
        time.sleep(delay)

    for attempt in range(1, retries + 2):
        # Keep transport retry bounded independently from business-error retries.
        try:
            transport = _transport_for_attempt(attempt)
            if transport == "standard":
                status, body, headers, final_url = _request(
                    url,
                    body=None,
                    timeout=timeout,
                    retries=0,
                    allow_browser_fallback=False,
                )
            elif transport == "curl":
                status, body, headers, final_url = _request_via_curl_transport(
                    url,
                    body=None,
                    timeout=timeout,
                )
            else:
                status, body, headers, final_url = _request_via_browser_transport(
                    url,
                    body=None,
                    timeout=timeout,
                )
        except FailClosedHTTPError:
            raise
        except CollectionError as error:
            transport_errors.append(str(error))
            if attempt > retries:
                raise ServiceAbortError(
                    f"detail {role} transports exhausted retries: {error}"
                ) from error
            delay = min(30.0, 0.5 * (2 ** (attempt - 1)))
            _retry_progress(
                progress_callback,
                phase="detail_transport_backoff",
                attempt=attempt,
                retries=retries,
                delay=delay,
                business_code=0,
                next_transport=_transport_for_attempt(attempt + 1),
                role=role,
            )
            time.sleep(delay)
            continue
        try:
            business_code = _business_error_code(body)
        except CollectionError:
            retry_invalid_response(
                attempt=attempt,
                body=body,
                status=status,
                headers=headers,
                final_url=final_url,
                transport=transport,
            )
            continue
        if business_code is not None:
            attempt_path = path.parent / "attempts" / f"{stem}-{role}-attempt-{attempt}.json"
            actual_attempt_path = _write_transient_attempt(
                attempt_path,
                body,
                url=url,
                final_url=final_url,
                status=status,
                headers=headers,
                role=f"{role}_transient_business_error",
                params={
                    "attempt": str(attempt),
                    "business_code": str(business_code),
                    "transport": transport,
                },
            )
            attempts.append(
                {
                    "path": str(actual_attempt_path),
                    "sha256": _sha(body),
                    "status": status,
                    "business_code": business_code,
                    "transport": transport,
                }
            )
            if business_code in NON_RETRYABLE_BUSINESS_CODES:
                reason = "captcha required" if business_code == 829 else "request rejected"
                error_type = ServiceAbortError if business_code == 829 else CollectionError
                raise error_type(
                    f"detail {role} business error {business_code}: {reason}"
                )
            if attempt > retries:
                error_type = ServiceAbortError if business_code == 800 else CollectionError
                raise error_type(
                    f"detail {role} business error {business_code} exhausted retries"
                )
            delay = _business_retry_delay(business_code, attempt)
            _retry_progress(
                progress_callback,
                phase="detail_rate_limit_backoff",
                attempt=attempt,
                retries=retries,
                delay=delay,
                business_code=business_code,
                next_transport=_transport_for_attempt(attempt + 1),
                role=role,
            )
            time.sleep(delay)
            continue
        try:
            validator(body)
        except ServiceAbortError:
            raise
        except Exception:
            retry_invalid_response(
                attempt=attempt,
                body=body,
                status=status,
                headers=headers,
                final_url=final_url,
                transport=transport,
            )
            continue
        _write_bytes(path, body)
        _sidecar(path, url=url, final_url=final_url, status=status, headers=headers, body=body, role=role)
        return status, body, headers, final_url, {
            "retry_count": len(attempts),
            "transient_attempts": attempts,
            "transport_errors": transport_errors,
        }
    raise AssertionError("detail retry loop did not return")


def _record_id(row: dict[str, Any]) -> str:
    for key in ("id", "recordId", "recordID", "informationId", "infoId"):
        value = _clean(row.get(key))
        if value:
            return value
    raise CollectionError("listing record has no stable id")


def _record_url(row: dict[str, Any]) -> str:
    for key in ("url", "href", "detailUrl", "detailURL"):
        value = _clean(row.get(key))
        if value:
            return urljoin(BASE_URL, value)
    raise CollectionError("listing record has no detail URL")


def _is_target_listing_candidate(row: dict[str, Any]) -> bool:
    """Use official listing metadata to avoid downloading unrelated provinces.

    Beijing, Shanghai, and Tianjin publish the same platform labels used by
    the detail-page attribution rules.  Chongqing listings historically omit
    that label, so its official province code is the conservative fallback;
    detail attribution still requires the platform-label/original-host pair.
    """
    platform_name = _clean(row.get("transactionSourcesPlatformText"))
    return platform_name in TARGET_LISTING_PLATFORM_NAMES or _clean(row.get("province")) == "500000"


def _rows(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    if payload.get("code") != 200 or payload.get("message") != "success":
        raise CollectionError(f"listing API envelope invalid: {payload.get('code')} {payload.get('message')}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CollectionError("listing API data is not an object")
    records = data.get("records")
    if not isinstance(records, list) and isinstance(data.get("list"), list):
        records = data["list"]
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise CollectionError("listing API records are invalid")
    try:
        total = int(data.get("total"))
        pages = int(data.get("pages", data.get("pageCount")))
    except (TypeError, ValueError) as error:
        raise CollectionError("listing API total/pages are invalid") from error
    # The official endpoint represents an empty result as total=0/pages=0.
    # Non-empty results must still advertise at least one page.
    if total < 0 or pages < 0 or (total > 0 and pages < 1) or (total == 0 and pages > 1):
        raise CollectionError("listing API total/pages out of range")
    return records, total, pages


def _table(html: bytes) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="detail_Table")
    if table is None:
        raise CollectionError("detail table missing")
    result: dict[str, str] = {}
    for tr in table.find_all("tr"):
        cells = [_clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if len(cells) >= 2 and cells[0]:
            result[cells[0]] = cells[1]
    # Transaction mode and buyer can legally be blank in official results.
    required = tuple(name for name in TABLE_FIELDS if name not in {"交易方式", "受让方名称"})
    missing = [name for name in required if name not in result]
    if missing:
        raise CollectionError(f"detail table fields missing: {missing}")
    return result


def _detail_url(outer_url: str, html: bytes) -> str:
    soup = BeautifulSoup(html, "html.parser")
    iframe = soup.find("iframe")
    if iframe and _clean(iframe.get("src")):
        return urljoin(outer_url, _clean(iframe.get("src")))
    match = re.search(rb"firstLastUrl\s*=\s*['\"]([^'\"]+)['\"]", html)
    if match:
        return urljoin(outer_url, match.group(1).decode("utf-8"))
    match = re.search(rb"/information/deal/html/[ab]/[^'\" ]+", html)
    if match:
        return urljoin(outer_url, match.group(0).decode("utf-8"))
    raise CollectionError("detail page has no inner URL")


def _to_b_url(url: str) -> str:
    if "/html/a/" not in url:
        raise CollectionError(f"detail URL is not /html/a/: {url}")
    return url.replace("/html/a/", "/html/b/", 1)


def _source_label(html: bytes) -> str:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("#platformName")
    return _clean(node.get_text(" ", strip=True) if node else "")


def _original_link(html: bytes, base_url: str = BASE_URL) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = _clean(anchor.get("href"))
        if href:
            return urljoin(base_url, href)
    return ""


def _mhtml(record_id: str, source_label: str, inner: bytes, inner_url: str) -> bytes:
    """Render the legacy PEAP two-part snapshot deterministically."""
    boundary = f"----PEAPPublicResource-{record_id}"
    cid = f"detail-{record_id}"
    outer = (
        "<!doctype html><html><body><span id=\"platformName\">"
        + html.escape(source_label)
        + f"</span><div id=\"div_0502\"><iframe src=\"cid:{cid}\"></iframe></div></body></html>"
    ).encode("utf-8")
    pieces = [
        b"MIME-Version: 1.0\r\n",
        f'Content-Type: multipart/related; boundary="{boundary}"\r\n\r\n'.encode("ascii"),
        f"--{boundary}\r\n".encode("ascii"),
        b"Content-Type: text/html; charset=utf-8\r\n",
        f"Content-Location: {inner_url}\r\n\r\n".encode("utf-8"),
        outer,
        b"\r\n",
        f"--{boundary}\r\n".encode("ascii"),
        b"Content-Type: text/html; charset=utf-8\r\n",
        f"Content-Location: {inner_url}\r\n".encode("utf-8"),
        f"Content-ID: <{cid}>\r\n\r\n".encode("ascii"),
        inner,
        b"\r\n",
        f"--{boundary}--\r\n".encode("ascii"),
    ]
    return b"".join(pieces)


def _write_mhtml(path: Path, data: bytes, expected: dict[str, str]) -> None:
    if not path.exists():
        _write_bytes(path, data)
        return
    try:
        parsed = parse_mhtml_file(str(path))
    except Exception as error:
        raise CollectionError(f"existing MHTML cannot be reused: {path}") from error
    compared_fields = ("交易所", *TABLE_FIELDS)

    def same_fact(name: str) -> bool:
        parsed_name = MHTML_PARSED_FIELD_NAMES.get(name, name)
        actual = _clean(parsed.get(parsed_name))
        wanted = _clean(expected.get(name))
        if name == "成交日期":
            actual = actual.replace("-", "/")
            wanted = wanted.replace("-", "/")
        return actual == wanted

    if any(not same_fact(name) for name in compared_fields):
        raise CollectionError(f"existing MHTML facts differ; refusing overwrite: {path}")


def _month_dir(manual_root: Path, month: str) -> Path:
    year, number = month.split("-")
    return manual_root / f"{year}年{int(number)}月"


def _stable_month_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Remove run-local retry diagnostics from a month-manifest comparison."""
    stable = dict(manifest)
    stable_details: list[dict[str, Any]] = []
    details = manifest.get("details")
    if not isinstance(details, list):
        raise CollectionError("month manifest details are invalid")
    retry_fields = {
        "outer_retry_count",
        "outer_transient_attempts",
        "inner_retry_count",
        "inner_transient_attempts",
    }
    for raw_detail in details:
        if not isinstance(raw_detail, dict):
            raise CollectionError("month manifest detail entry is invalid")
        stable_details.append(
            {
                key: value
                for key, value in raw_detail.items()
                if key not in retry_fields
            }
        )
    stable["details"] = stable_details
    return stable


def _reuse_equivalent_month_manifest(
    path: Path,
    manifest: dict[str, Any],
    *,
    resume: bool,
) -> dict[str, Any] | None:
    if not resume or not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise CollectionError(f"month manifest is not a regular file: {path}")
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CollectionError(f"existing month manifest is invalid: {path}") from error
    if not isinstance(existing, dict):
        raise CollectionError(f"existing month manifest is not an object: {path}")
    if _stable_month_manifest(existing) == _stable_month_manifest(manifest):
        return existing
    return None


def collect_month(
    month: str,
    begin: str,
    end: str,
    *,
    evidence_root: Path,
    manual_root: Path,
    timeout: float,
    retries: int,
    detail_delay: float,
    resume: bool,
    search_delay: float = DEFAULT_SEARCH_DELAY_SECONDS,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    month_root = evidence_root / month
    search_root = month_root / "search"
    detail_root = month_root / "details"
    records: list[dict[str, Any]] = []
    declared_total = declared_pages = None
    search_network_fetches = 0

    def before_search_network_request() -> None:
        nonlocal search_network_fetches
        if search_network_fetches and search_delay:
            time.sleep(search_delay)
        search_network_fetches += 1

    page = 1
    while True:
        params = _params(begin, end, page)
        body = urlencode(params).encode("utf-8")
        url = SEARCH_URL
        path = search_root / f"page-{page:05d}.json"
        status, raw, headers, final_url = _fetch_search_evidence(
            path,
            url=url,
            body=body,
            params=params,
            timeout=timeout,
            retries=retries,
            resume=resume,
            page=page,
            progress_callback=progress_callback,
            before_network_request=before_search_network_request,
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CollectionError(f"search JSON invalid page {page}") from error
        rows, total, pages = _rows(payload)
        if declared_total is None:
            declared_total, declared_pages = total, pages
        elif (total, pages) != (declared_total, declared_pages):
            for canonical in sorted(search_root.glob("page-*.json")):
                _quarantine_canonical(
                    canonical,
                    reason="search_total_or_pages_changed",
                )
            raise CollectionError(f"search total/pages changed on page {page}")
        records.extend(rows)
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "search",
                    "month": month,
                    "page": page,
                    "pages": pages,
                    "listed": len(records),
                    "official_total": total,
                }
            )
        if page >= pages:
            break
        if page + 1 != pages and page >= declared_pages:
            raise CollectionError("pagination discontinuity")
        page += 1
    if declared_total != len(records):
        raise CollectionError(f"listing count {len(records)} differs from declared total {declared_total}")
    ids = [_record_id(row) for row in records]
    if len(set(ids)) != len(ids):
        raise CollectionError("listing record ids are not unique")

    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    parser_failures: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    exchange_counts: dict[str, int] = {}
    detail_candidates = 0
    listing_scope_excluded = 0
    detail_network_records = 0
    mdir = _month_dir(manual_root, month)
    mdir.mkdir(parents=True, exist_ok=True)
    for ordinal, row in enumerate(records, 1):
        rid = _record_id(row)
        outer_url = _record_url(row)
        stem = f"{ordinal:05d}-{_safe(rid)}"
        if not _is_target_listing_candidate(row):
            listing_scope_excluded += 1
            excluded.append(
                {
                    "record_id": rid,
                    "reason": "listing_scope_not_target",
                    "platform_name": _clean(row.get("transactionSourcesPlatformText")),
                    "province": _clean(row.get("province")),
                }
            )
            details.append(
                {
                    "record_id": rid,
                    "outer_path": "",
                    "outer_sha256": "",
                    "outer_status": None,
                    "outer_retry_count": 0,
                    "outer_transient_attempts": [],
                    "inner_path": "",
                    "inner_sha256": "",
                    "inner_status": None,
                    "inner_retry_count": 0,
                    "inner_transient_attempts": [],
                    "outcome": "excluded_listing_scope",
                }
            )
            continue
        detail_candidates += 1
        outer_path = detail_root / f"{stem}-outer.html"
        outer_status = 0
        outer = b""
        outer_retry: dict[str, Any] = {"retry_count": 0, "transient_attempts": []}
        inner_path: Path | None = None
        inner = b""
        inner_status: int | None = None
        inner_retry: dict[str, Any] = {"retry_count": 0, "transient_attempts": []}
        record_network_started = False

        def before_detail_network_request() -> None:
            nonlocal detail_network_records, record_network_started
            if record_network_started:
                return
            if detail_network_records and detail_delay:
                time.sleep(detail_delay)
            detail_network_records += 1
            record_network_started = True

        try:
            outer_status, outer, outer_headers, outer_final, outer_retry = _fetch_detail_evidence(
                outer_path,
                stem=stem,
                role="outer",
                url=outer_url,
                timeout=timeout,
                retries=retries,
                resume=resume,
                validator=lambda body, bound_url=outer_url: _valid_outer_html(
                    body,
                    outer_url=bound_url,
                ),
                progress_callback=progress_callback,
                before_network_request=before_detail_network_request,
            )
            # The search result's /html/a/ URL is the record-bound authority;
            # the corresponding /html/b/ page is the actual detail table.
            inner_url = _to_b_url(outer_url)
            inner_path = detail_root / f"{stem}-inner.html"
            inner_status, inner, inner_headers, inner_final, inner_retry = _fetch_detail_evidence(
                inner_path,
                stem=stem,
                role="inner",
                url=inner_url,
                timeout=timeout,
                retries=retries,
                resume=resume,
                validator=_table,
                progress_callback=progress_callback,
                before_network_request=before_detail_network_request,
            )
            if outer_status != 200 or inner_status != 200:
                raise CollectionError(f"detail HTTP status outer={outer_status} inner={inner_status}")
            fields = _table(inner)
            # The platform attribution belongs to the outer disclosure page.
            # Keep a narrowly-scoped fallback for older snapshots that omitted
            # the outer marker, but never let an inner marker override it.
            source_label = _source_label(outer) or _source_label(inner)
            try:
                exchange = normalize_public_resource_exchange(source_label, _original_link(inner, inner_url))
            except Exception as error:
                excluded.append({"record_id": rid, "reason": "attribution_conflict", "error": str(error)})
                details.append({"record_id": rid, "outer_path": str(outer_path), "outer_sha256": _sha(outer), "outer_status": outer_status, "outer_retry_count": outer_retry["retry_count"], "outer_transient_attempts": outer_retry["transient_attempts"], "inner_path": str(inner_path), "inner_sha256": _sha(inner), "inner_status": inner_status, "inner_retry_count": inner_retry["retry_count"], "inner_transient_attempts": inner_retry["transient_attempts"], "outcome": "excluded_attribution_conflict"})
                continue
            if exchange not in EXCHANGES:
                excluded.append({"record_id": rid, "reason": "unrecognized_exchange", "exchange": exchange})
                details.append({"record_id": rid, "outer_path": str(outer_path), "outer_sha256": _sha(outer), "outer_status": outer_status, "outer_retry_count": outer_retry["retry_count"], "outer_transient_attempts": outer_retry["transient_attempts"], "inner_path": str(inner_path), "inner_sha256": _sha(inner), "inner_status": inner_status, "inner_retry_count": inner_retry["retry_count"], "inner_transient_attempts": inner_retry["transient_attempts"], "outcome": "excluded"})
                continue
            expected = {
                "交易所": exchange,
                **{name: fields[name] for name in TABLE_FIELDS},
            }
            mhtml = _mhtml(rid, source_label, inner, inner_url)
            mpath = mdir / f"{_safe(rid)}.mhtml"
            _write_mhtml(mpath, mhtml, expected)
            parsed = parse_mhtml_file(str(mpath))
            if parsed.get("项目编号") != fields["项目编号"]:
                raise CollectionError("MHTML parser identity differs from detail")
            selected.append({"record_id": rid, "exchange": exchange, "project_code": fields["项目编号"], "project_name": fields["项目名称"], "mhtml": str(mpath), "mhtml_sha256": _sha(mpath.read_bytes()), "detail_sha256": _sha(inner)})
            details.append({"record_id": rid, "outer_path": str(outer_path), "outer_sha256": _sha(outer), "outer_status": outer_status, "outer_retry_count": outer_retry["retry_count"], "outer_transient_attempts": outer_retry["transient_attempts"], "inner_path": str(inner_path), "inner_sha256": _sha(inner), "inner_status": inner_status, "inner_retry_count": inner_retry["retry_count"], "inner_transient_attempts": inner_retry["transient_attempts"], "outcome": "selected", "mhtml": str(mpath)})
            exchange_counts[exchange] = exchange_counts.get(exchange, 0) + 1
        except ServiceAbortError:
            raise
        except Exception as error:
            parser_failures.append({"record_id": rid, "error": str(error)})
            details.append(
                {
                    "record_id": rid,
                    "outer_path": str(outer_path),
                    "outer_sha256": _sha(outer),
                    "outer_status": outer_status,
                    "outer_retry_count": outer_retry["retry_count"],
                    "outer_transient_attempts": outer_retry["transient_attempts"],
                    "inner_path": str(inner_path or ""),
                    "inner_sha256": _sha(inner) if inner else "",
                    "inner_status": inner_status,
                    "inner_retry_count": inner_retry["retry_count"],
                    "inner_transient_attempts": inner_retry["transient_attempts"],
                    "outcome": "detail_or_parser_failure",
                    "error": str(error),
                }
            )
        finally:
            if progress_callback is not None:
                progress_callback(
                    {
                        "phase": "detail",
                        "month": month,
                        "current": ordinal,
                        "total": len(records),
                        "selected": len(selected),
                        "excluded": len(excluded),
                        "failed": len(parser_failures),
                    }
                )

    codes = {}
    for item in selected:
        code = item["project_code"]
        if code in codes and codes[code]["detail_sha256"] != item["detail_sha256"]:
            raise CollectionError(f"project code conflict: {code}")
        codes[code] = item
    if len(details) != len(records):
        raise CollectionError("detail manifest does not cover every official listing record")
    if len(selected) + len(excluded) + len(parser_failures) != len(records):
        raise CollectionError("record outcomes do not cover every official listing record")
    manifest = {"schema_version": 1, "month": month, "time_begin": begin, "time_end": end, "official_total": declared_total, "official_pages": declared_pages, "detail_candidates": detail_candidates, "listing_scope_excluded": listing_scope_excluded, "detail_ok": len(records) - len(parser_failures), "selected": len(selected), "excluded": len(excluded), "parser_failures": parser_failures, "excluded_records": excluded, "exchange_counts": dict(sorted(exchange_counts.items())), "details": details, "records": selected}
    manifest_path = month_root / "manifest.json"
    existing_manifest = _reuse_equivalent_month_manifest(
        manifest_path,
        manifest,
        resume=resume,
    )
    if existing_manifest is not None:
        manifest = existing_manifest
    else:
        _write_json(manifest_path, manifest)
    if parser_failures:
        raise CollectionError(f"month {month} has {len(parser_failures)} detail/parser failures")
    return manifest


def _safe(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", _clean(value))
    return text.strip("._")[:100] or "unnamed"


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def _build_workbook_from_records(
    records: list[dict[str, Any]],
    output_file: Path,
) -> Any:
    """Build an Excel file from exactly the records in a manifest.

    ``build_workbook`` intentionally accepts a directory for the legacy CLI.
    Staging only the manifest-selected files in a temporary directory keeps
    that API intact while preventing prior snapshots in a month directory from
    leaking into a new export.
    """
    with TemporaryDirectory(prefix="peap-public-resource-selected-") as directory:
        stage = Path(directory)
        for ordinal, item in enumerate(records, 1):
            source = Path(str(item["mhtml"]))
            if not source.is_file():
                raise CollectionError(f"selected MHTML is missing: {source}")
            body = source.read_bytes()
            expected_hash = _clean(item.get("mhtml_sha256"))
            if expected_hash and _sha(body) != expected_hash:
                raise CollectionError(f"selected MHTML hash differs: {source}")
            target = stage / f"{ordinal:05d}-{_safe(item['record_id'])}.mhtml"
            _write_bytes(target, body)
        return build_workbook(str(stage), str(output_file))


def _month_ranges_between(start: dt.date, end: dt.date) -> list[tuple[str, str, str]]:
    if start > end:
        raise ValueError("start must be on or before end")
    ranges: list[tuple[str, str, str]] = []
    cursor = start
    while cursor <= end:
        month_end = dt.date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1])
        period_end = min(month_end, end)
        ranges.append(
            (
                f"{cursor.year:04d}-{cursor.month:02d}",
                cursor.isoformat(),
                period_end.isoformat(),
            )
        )
        cursor = period_end + dt.timedelta(days=1)
    return ranges


def collect_date_range(
    start: dt.date,
    end: dt.date,
    *,
    evidence_root: Path,
    manual_root: Path,
    export_root: Path,
    resume: bool = True,
    timeout: float = 30.0,
    retries: int = DEFAULT_RETRIES,
    detail_delay: float = DEFAULT_DETAIL_DELAY_SECONDS,
    search_delay: float = DEFAULT_SEARCH_DELAY_SECONDS,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Collect and parse one inclusive range, including ranges crossing years."""
    evidence_root = Path(evidence_root)
    manual_root = Path(manual_root)
    export_root = Path(export_root)
    if start > end:
        raise ValueError("start must be on or before end")
    if retries < 0 or detail_delay < 0 or search_delay < 0:
        raise ValueError("retries and delays must be non-negative")

    range_id = f"{start.isoformat()}--{end.isoformat()}"
    scoped_evidence_root = evidence_root / "ranges" / range_id
    scoped_export_root = export_root / "ranges" / range_id
    scoped_evidence_root.mkdir(parents=True, exist_ok=True)
    manual_root.mkdir(parents=True, exist_ok=True)
    scoped_export_root.mkdir(parents=True, exist_ok=True)

    summaries: list[dict[str, Any]] = []
    periods = _month_ranges_between(start, end)
    for period_index, (month, begin, through) in enumerate(periods, 1):
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "period",
                    "month": month,
                    "period_index": period_index,
                    "period_total": len(periods),
                    "time_begin": begin,
                    "time_end": through,
                }
            )
        summary = collect_month(
            month,
            begin,
            through,
            evidence_root=scoped_evidence_root,
            manual_root=manual_root,
            timeout=timeout,
            retries=retries,
            detail_delay=detail_delay,
            resume=resume,
            search_delay=search_delay,
            progress_callback=progress_callback,
        )
        summaries.append(summary)
        workbook_summary = _build_workbook_from_records(
            summary["records"],
            scoped_export_root / f"{month}.xlsx",
        )
        if workbook_summary.failed or workbook_summary.success_count != summary["selected"]:
            raise CollectionError(
                f"range workbook mismatch for {month}: selected={summary['selected']} "
                f"parsed={workbook_summary.success_count} failed={len(workbook_summary.failed)}"
            )

    unique_records: dict[str, dict[str, Any]] = {}
    duplicate_project_codes: list[str] = []
    for summary in summaries:
        for item in summary["records"]:
            code = _clean(item["project_code"])
            prior = unique_records.get(code)
            if prior is not None:
                if prior["detail_sha256"] != item["detail_sha256"]:
                    raise CollectionError(f"range project-code duplicate conflict: {code}")
                duplicate_project_codes.append(code)
                continue
            unique_records[code] = item

    range_workbook = scoped_export_root / f"public-resource-{range_id}.xlsx"
    workbook_summary = _build_workbook_from_records(list(unique_records.values()), range_workbook)
    if workbook_summary.failed or workbook_summary.success_count != len(unique_records):
        raise CollectionError(
            f"range workbook mismatch: unique_selected={len(unique_records)} "
            f"parsed={workbook_summary.success_count} failed={len(workbook_summary.failed)}"
        )
    result = {
        "schema_version": 1,
        "range_id": range_id,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "periods": summaries,
        "records": list(unique_records.values()),
        "unique_selected": len(unique_records),
        "duplicate_project_codes": sorted(set(duplicate_project_codes)),
        "workbook": str(range_workbook),
        "evidence_root": str(scoped_evidence_root),
        "manual_root": str(manual_root),
    }
    _write_json(scoped_export_root / "manifest.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--through", type=_parse_date, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument(
        "--manual-root",
        type=Path,
        default=(
            Path(resolve_runtime_workspace_paths().app_home)
            / "manual"
            / MANUAL_ARCHIVE_DIRECTORY
        ),
    )
    parser.add_argument("--export-root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--detail-delay", type=float, default=DEFAULT_DETAIL_DELAY_SECONDS)
    parser.add_argument("--search-delay", type=float, default=DEFAULT_SEARCH_DELAY_SECONDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if (
        args.through.year != args.year
        or args.retries < 0
        or args.detail_delay < 0
        or args.search_delay < 0
    ):
        raise SystemExit("--through must be within --year and retries/delays must be valid")
    args.evidence_root.mkdir(parents=True, exist_ok=True)
    args.manual_root.mkdir(parents=True, exist_ok=True)
    summaries = []
    for month, begin, end in _month_ranges(args.year, args.through):
        summary = collect_month(month, begin, end, evidence_root=args.evidence_root, manual_root=args.manual_root, timeout=args.timeout, retries=args.retries, detail_delay=args.detail_delay, resume=args.resume, search_delay=args.search_delay)
        summaries.append(summary)
        output_file = args.export_root / f"{month}.xlsx"
        workbook_summary = _build_workbook_from_records(summary["records"], output_file)
        if workbook_summary.failed or workbook_summary.success_count != summary["selected"]:
            raise CollectionError(
                f"monthly workbook mismatch for {month}: "
                f"selected={summary['selected']} parsed={workbook_summary.success_count} "
                f"failed={len(workbook_summary.failed)}"
            )
    annual_records: dict[str, dict[str, Any]] = {}
    annual_duplicates: list[str] = []
    for summary in summaries:
        for item in summary["records"]:
            code = _clean(item["project_code"])
            prior = annual_records.get(code)
            if prior is not None:
                if prior["detail_sha256"] != item["detail_sha256"]:
                    raise CollectionError(f"annual project-code duplicate conflict: {code}")
                annual_duplicates.append(code)
                continue
            annual_records[code] = item
    annual_workbook = args.export_root / f"{args.year}-annual.xlsx"
    annual_summary = _build_workbook_from_records(list(annual_records.values()), annual_workbook)
    if annual_summary.failed or annual_summary.success_count != len(annual_records):
        raise CollectionError(
            f"annual workbook mismatch: unique_selected={len(annual_records)} "
            f"parsed={annual_summary.success_count} failed={len(annual_summary.failed)}"
        )
    annual = {"schema_version": 1, "year": args.year, "through": args.through.isoformat(), "months": summaries, "annual_unique_selected": len(annual_records), "annual_duplicate_project_codes": sorted(annual_duplicates), "annual_workbook": str(annual_workbook)}
    _write_json(args.export_root / "annual-manifest.json", annual)
    print(json.dumps(annual, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
