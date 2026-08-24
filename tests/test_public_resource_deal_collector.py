from __future__ import annotations

import ssl
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scripts import collect_public_resource_deals as collector


def _required_cli_args(tmp_path: Path) -> list[str]:
    return [
        "--year",
        "2026",
        "--through",
        "2026-01-31",
        "--evidence-root",
        str(tmp_path / "evidence"),
        "--export-root",
        str(tmp_path / "exports"),
    ]


def _seed_canonical(
    path: Path,
    body: bytes,
    *,
    url: str,
    role: str,
    params: dict[str, str] | None = None,
) -> None:
    collector._write_bytes(path, body)
    collector._sidecar(
        path,
        url=url,
        final_url=url,
        status=200,
        headers={"Content-Type": "application/json"},
        body=body,
        role=role,
        params=params,
    )


def test_collector_does_not_expose_unimplemented_workers_flag(tmp_path):
    with pytest.raises(SystemExit):
        collector.build_parser().parse_args(
            [*_required_cli_args(tmp_path), "--workers", "2"]
        )


def test_collector_keeps_effective_timeout_and_portable_manual_root(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("PEAP_WORKSPACE_ROOT", str(workspace))

    args = collector.build_parser().parse_args(
        [*_required_cli_args(tmp_path), "--timeout", "12.5"]
    )

    assert args.timeout == 12.5
    assert args.manual_root == workspace / "manual" / collector.MANUAL_ARCHIVE_DIRECTORY


def test_month_ranges_between_clips_first_and_last_month_and_crosses_year():
    assert collector._month_ranges_between(date(2025, 12, 20), date(2026, 2, 3)) == [
        ("2025-12", "2025-12-20", "2025-12-31"),
        ("2026-01", "2026-01-01", "2026-01-31"),
        ("2026-02", "2026-02-01", "2026-02-03"),
    ]


def test_collect_date_range_uses_exact_dates_and_builds_one_range_workbook(tmp_path):
    calls = []

    def fake_collect(month, begin, end, **kwargs):
        calls.append((month, begin, end, kwargs["resume"]))
        code = f"CODE-{month}"
        source = tmp_path / f"{month}.mhtml"
        source.write_bytes(month.encode())
        return {
            "month": month,
            "selected": 1,
            "records": [
                {
                    "record_id": month,
                    "project_code": code,
                    "mhtml": str(source),
                    "mhtml_sha256": collector._sha(source.read_bytes()),
                    "detail_sha256": collector._sha(source.read_bytes()),
                }
            ],
        }

    with patch.object(collector, "collect_month", side_effect=fake_collect), patch.object(
        collector,
        "_build_workbook_from_records",
        side_effect=lambda records, _output: SimpleNamespace(
            failed=[],
            success_count=len(records),
        ),
    ) as build:
        result = collector.collect_date_range(
            date(2025, 12, 20),
            date(2026, 1, 5),
            evidence_root=tmp_path / "evidence",
            manual_root=tmp_path / "manual",
            export_root=tmp_path / "exports",
        )

    assert calls == [
        ("2025-12", "2025-12-20", "2025-12-31", True),
        ("2026-01", "2026-01-01", "2026-01-05", True),
    ]
    assert result["unique_selected"] == 2
    assert result["start_date"] == "2025-12-20"
    assert result["end_date"] == "2026-01-05"
    assert result["workbook"].endswith("public-resource-2025-12-20--2026-01-05.xlsx")
    assert build.call_count == 3


def test_ssl_context_allows_current_tls_above_security_floor():
    context = collector._ssl_context()

    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.maximum_version != ssl.TLSVersion.TLSv1_2


def test_official_request_headers_match_standard_and_browser_transports():
    standard = collector._headers()

    assert standard["Content-Type"] == "application/x-www-form-urlencoded"
    assert standard["Access-Control-Allow-Origin"] == "http://ggzy.gov.cn"
    assert standard["X-Pass-Token"] == ""
    assert standard["Referer"] == collector.LIST_URL
    assert collector._browser_fetch_headers() == {
        "Content-Type": "application/x-www-form-urlencoded",
        "Access-Control-Allow-Origin": "http://ggzy.gov.cn",
        "X-Pass-Token": "",
    }


class _FakeResponse:
    status = 200
    headers = {"Content-Type": "text/html"}
    url = "https://example.test/final"

    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def test_standard_transport_retries_before_browser_fallback():
    opener = MagicMock()
    opener.open.side_effect = [collector.URLError("temporary"), _FakeResponse(b"standard")]

    with patch.object(collector, "_ipv4_opener", return_value=opener), patch.object(collector, "_request_via_browser_transport") as browser, patch.object(collector.time, "sleep"):
        status, body, _headers, final_url = collector._request(
            "https://example.test/detail",
            body=None,
            timeout=1,
            retries=1,
        )

    assert (status, body, final_url) == (200, b"standard", "https://example.test/final")
    browser.assert_not_called()


def test_browser_transport_is_used_once_after_standard_retries_exhausted():
    opener = MagicMock()
    opener.open.side_effect = collector.URLError("temporary")
    browser_response = (200, b"browser", {"Content-Type": "text/html"}, "https://example.test/final")

    with patch.object(collector, "_ipv4_opener", return_value=opener), patch.object(collector, "_request_via_browser_transport", return_value=browser_response) as browser, patch.object(collector.time, "sleep"):
        result = collector._request(
            "https://example.test/detail",
            body=None,
            timeout=1,
            retries=1,
        )

    assert result == browser_response
    assert opener.open.call_count == 2
    browser.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        ssl.SSLError("TLS handshake failed"),
        RuntimeError("Playwright launch failed"),
    ],
    ids=["timeout", "tls", "playwright"],
)
def test_browser_transport_wraps_non_http_errors_as_collection_error(error):
    with patch.object(
        collector,
        "_request_via_browser_transport_unchecked",
        side_effect=error,
    ), pytest.raises(collector.CollectionError, match="browser transport failed") as raised:
        collector._request_via_browser_transport(
            "https://example.test/detail",
            body=None,
            timeout=1,
        )

    assert raised.value.__cause__ is error


@pytest.mark.parametrize("status", [403, 429])
def test_browser_transport_http_refusal_is_fail_closed(status):
    error = collector.HTTPError(
        "https://example.test/detail",
        status,
        "refused",
        hdrs=None,
        fp=None,
    )

    with patch.object(
        collector,
        "_request_via_browser_transport_unchecked",
        side_effect=error,
    ), pytest.raises(collector.FailClosedHTTPError, match=f"HTTP {status} fail-closed"):
        collector._request_via_browser_transport(
            "https://example.test/detail",
            body=None,
            timeout=1,
        )


def test_browser_transport_http_5xx_is_retryable_collection_error():
    error = collector.HTTPError(
        "https://example.test/detail",
        503,
        "unavailable",
        hdrs=None,
        fp=None,
    )

    with patch.object(
        collector,
        "_request_via_browser_transport_unchecked",
        side_effect=error,
    ), pytest.raises(
        collector.CollectionError,
        match="browser transport HTTP 503",
    ) as raised:
        collector._request_via_browser_transport(
            "https://example.test/detail",
            body=None,
            timeout=1,
        )

    assert not isinstance(raised.value, collector.ServiceAbortError)


def test_curl_transport_returns_body_headers_status_and_final_url():
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["input"] = kwargs["input"]
        header_path = Path(command[command.index("--dump-header") + 1])
        response_path = Path(command[command.index("--output") + 1])
        header_path.write_bytes(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Test: yes\r\n\r\n"
        )
        response_path.write_bytes(b'{"code":200}')
        return SimpleNamespace(
            returncode=0,
            stdout=b"200\nhttps://example.test/final\n",
            stderr=b"",
        )

    with patch.object(collector.shutil, "which", return_value="/usr/bin/curl"), patch.object(
        collector.subprocess,
        "run",
        side_effect=run,
    ):
        result = collector._request_via_curl_transport(
            "https://example.test/search",
            body=b"PAGENUMBER=1",
            timeout=3,
        )

    assert result == (
        200,
        b'{"code":200}',
        {"Content-Type": "application/json", "X-Test": "yes"},
        "https://example.test/final",
    )
    assert observed["input"] == b"PAGENUMBER=1"
    assert "--http1.1" in observed["command"]
    assert observed["command"][-1] == "https://example.test/search"


@pytest.mark.parametrize("status", [403, 429])
def test_standard_transport_http_refusal_does_not_fallback(status):
    opener = MagicMock()
    opener.open.side_effect = collector.HTTPError(
        "https://example.test/detail",
        status,
        "refused",
        hdrs=None,
        fp=None,
    )

    with patch.object(collector, "_ipv4_opener", return_value=opener), patch.object(
        collector,
        "_request_via_browser_transport",
    ) as browser, pytest.raises(
        collector.FailClosedHTTPError,
        match=f"HTTP {status} fail-closed",
    ):
        collector._request(
            "https://example.test/detail",
            body=None,
            timeout=1,
            retries=3,
        )

    browser.assert_not_called()


def test_ipv4_opener_disables_ambient_proxy_configuration():
    with patch.object(collector, "build_opener", return_value=object()) as build:
        collector._ipv4_opener()

    proxy_handler, https_handler = build.call_args.args
    assert proxy_handler.proxies == {}
    assert isinstance(https_handler, collector.HTTPSHandler)


def test_outer_page_first_last_url_can_point_directly_to_b_detail():
    outer_url = "https://www.ggzy.gov.cn/information/deal/html/a/abc123.html"
    outer = b"<script>var firstLastUrl = '/information/deal/html/b/abc123.html';</script>"

    assert collector._detail_url(outer_url, outer) == (
        "https://www.ggzy.gov.cn/information/deal/html/b/abc123.html"
    )
    assert collector._to_b_url(outer_url) == (
        "https://www.ggzy.gov.cn/information/deal/html/b/abc123.html"
    )


def test_detail_business_error_is_archived_as_attempt_then_retried(tmp_path):
    first = (
        200,
        b'{"code":800,"message":"busy"}',
        {"Content-Type": "application/json"},
        "https://example.test/detail",
    )
    second = (
        200,
        b"<html><body>ok</body></html>",
        {"Content-Type": "text/html"},
        "https://example.test/detail",
    )
    target = tmp_path / "details" / "001-rid-outer.html"
    progress = []
    with patch.object(collector, "_request", return_value=first) as standard, patch.object(
        collector,
        "_request_via_curl_transport",
        return_value=second,
    ) as curl, patch.object(collector.time, "sleep") as sleep:
        status, body, _headers, _final_url, retry = collector._fetch_detail_evidence(
            target,
            stem="001-rid",
            role="outer",
            url="https://example.test/detail",
            timeout=1,
            retries=1,
            resume=False,
            validator=collector._valid_outer_html,
            progress_callback=progress.append,
        )

    assert status == 200
    assert body.startswith(b"<html")
    assert target.read_bytes() == body
    assert retry["retry_count"] == 1
    assert (tmp_path / "details" / "attempts" / "001-rid-outer-attempt-1.json").read_bytes().startswith(b'{"code":800')
    standard.assert_called_once()
    curl.assert_called_once()
    sleep.assert_called_once_with(20.0)
    assert progress == [
        {
            "phase": "detail_rate_limit_backoff",
            "attempt": 1,
            "attempt_total": 2,
            "retry_in_seconds": 20,
            "business_code": 800,
            "page": 0,
            "transport": "curl",
            "next_transport": "curl",
            "role": "outer",
        }
    ]


def test_detail_business_error_exhaustion_does_not_create_formal_evidence(tmp_path):
    target = tmp_path / "details" / "001-rid-inner.html"
    response = (200, b'{"code":800,"message":"busy"}', {"Content-Type": "application/json"}, "https://example.test/detail")
    with patch.object(collector, "_request", return_value=response), patch.object(
        collector,
        "_request_via_curl_transport",
        return_value=response,
    ), patch.object(collector.time, "sleep"), pytest.raises(
        collector.ServiceAbortError,
        match="exhausted",
    ):
        collector._fetch_detail_evidence(
            target,
            stem="001-rid",
            role="inner",
            url="https://example.test/detail",
            timeout=1,
            retries=1,
            resume=False,
            validator=collector._table,
        )

    assert not target.exists()
    assert (tmp_path / "details" / "attempts" / "001-rid-inner-attempt-2.json").is_file()


def test_search_business_error_is_archived_as_attempt_then_retried(tmp_path):
    first = (
        200,
        b'{"code":800,"message":"busy"}',
        {"Content-Type": "application/json"},
        "https://example.test/search",
    )
    second = (
        200,
        b'{"code":200,"message":"success","data":{"records":[],"total":0,"pages":0}}',
        {"Content-Type": "application/json"},
        "https://example.test/search",
    )
    target = tmp_path / "search" / "page-00001.json"
    params = {"PAGENUMBER": "1"}
    with patch.object(collector, "_request", return_value=first), patch.object(
        collector,
        "_request_via_curl_transport",
        return_value=second,
    ), patch.object(collector.time, "sleep"):
        status, body, _headers, _final_url = collector._fetch_search_evidence(
            target,
            url="https://example.test/search",
            body=b"PAGENUMBER=1",
            params=params,
            timeout=1,
            retries=1,
            resume=False,
            page=1,
        )

    assert status == 200
    assert b'"pages":0' in body
    assert target.read_bytes() == body
    assert (tmp_path / "search" / "attempts" / "page-00001-attempt-1.json").is_file()


def test_search_code_800_uses_full_backoff_schedule_and_reports_next_transport(tmp_path):
    busy = (
        200,
        b'{"code":800,"message":"busy"}',
        {"Content-Type": "application/json"},
        "https://example.test/search",
    )
    success = (
        200,
        b'{"code":200,"message":"success","data":{"records":[],"total":0,"pages":0}}',
        {"Content-Type": "application/json"},
        "https://example.test/search",
    )
    progress = []

    with patch.object(collector, "_request", return_value=busy) as standard, patch.object(
        collector,
        "_request_via_curl_transport",
        return_value=busy,
    ) as curl, patch.object(
        collector,
        "_request_via_browser_transport",
        side_effect=[busy, success],
    ) as browser, patch.object(collector.time, "sleep") as sleep:
        collector._fetch_search_evidence(
            tmp_path / "search" / "page-00001.json",
            url="https://example.test/search",
            body=b"PAGENUMBER=1",
            params={"PAGENUMBER": "1"},
            timeout=1,
            retries=3,
            resume=False,
            page=1,
            progress_callback=progress.append,
        )

    standard.assert_called_once()
    curl.assert_called_once()
    assert browser.call_count == 2
    assert [call.args[0] for call in sleep.call_args_list] == [20.0, 60.0, 120.0]
    assert [event["attempt"] for event in progress] == [1, 2, 3]
    assert [event["retry_in_seconds"] for event in progress] == [20, 60, 120]
    assert {event["phase"] for event in progress} == {"search_rate_limit_backoff"}
    assert {event["business_code"] for event in progress} == {800}
    assert [event["transport"] for event in progress] == ["curl", "browser", "browser"]
    assert [event["next_transport"] for event in progress] == ["curl", "browser", "browser"]


def test_transport_retry_reports_curl_before_invoking_it(tmp_path):
    success = (
        200,
        b'{"code":200,"message":"success","data":{"records":[],"total":0,"pages":0}}',
        {"Content-Type": "application/json"},
        "https://example.test/search",
    )
    progress = []

    with patch.object(
        collector,
        "_request",
        side_effect=collector.CollectionError("standard transport failed"),
    ), patch.object(
        collector,
        "_request_via_curl_transport",
        return_value=success,
    ) as curl, patch.object(collector.time, "sleep") as sleep:
        collector._fetch_search_evidence(
            tmp_path / "search" / "page-00001.json",
            url="https://example.test/search",
            body=b"PAGENUMBER=1",
            params={"PAGENUMBER": "1"},
            timeout=1,
            retries=1,
            resume=False,
            page=1,
            progress_callback=progress.append,
        )

    curl.assert_called_once()
    sleep.assert_called_once_with(0.5)
    assert progress == [
        {
            "phase": "search_transport_backoff",
            "attempt": 1,
            "attempt_total": 2,
            "retry_in_seconds": 1,
            "business_code": 0,
            "page": 1,
            "transport": "curl",
            "next_transport": "curl",
            "role": "",
        }
    ]


@pytest.mark.parametrize("business_code", [801, 804, 829])
def test_search_non_retryable_business_codes_fail_immediately(tmp_path, business_code):
    response = (
        200,
        f'{{"code":{business_code},"message":"rejected"}}'.encode(),
        {"Content-Type": "application/json"},
        "https://example.test/search",
    )

    with patch.object(collector, "_request", return_value=response), patch.object(
        collector,
        "_request_via_browser_transport",
    ) as browser, patch.object(collector.time, "sleep") as sleep, pytest.raises(
        collector.CollectionError,
        match=f"business error {business_code}",
    ):
        collector._fetch_search_evidence(
            tmp_path / "search" / "page-00001.json",
            url="https://example.test/search",
            body=b"PAGENUMBER=1",
            params={"PAGENUMBER": "1"},
            timeout=1,
            retries=3,
            resume=False,
            page=1,
        )

    browser.assert_not_called()
    sleep.assert_not_called()
    assert (
        tmp_path / "search" / "attempts" / "page-00001-attempt-1.json"
    ).is_file()


def test_detail_captcha_business_code_fails_without_browser_retry(tmp_path):
    response = (
        200,
        b'{"code":829,"message":"captcha"}',
        {"Content-Type": "application/json"},
        "https://example.test/detail",
    )

    with patch.object(collector, "_request", return_value=response), patch.object(
        collector,
        "_request_via_browser_transport",
    ) as browser, patch.object(collector.time, "sleep") as sleep, pytest.raises(
        collector.ServiceAbortError,
        match="business error 829: captcha required",
    ):
        collector._fetch_detail_evidence(
            tmp_path / "details" / "001-rid-inner.html",
            stem="001-rid",
            role="inner",
            url="https://example.test/detail",
            timeout=1,
            retries=3,
            resume=False,
            validator=collector._table,
        )

    browser.assert_not_called()
    sleep.assert_not_called()


def test_search_invalid_success_envelope_is_archived_then_curl_retried(tmp_path):
    invalid = (
        200,
        b'{"code":200,"message":"success","data":{"records":"bad","total":1,"pages":1}}',
        {"Content-Type": "application/json"},
        "https://example.test/search",
    )
    success = (
        200,
        b'{"code":200,"message":"success","data":{"records":[],"total":0,"pages":0}}',
        {"Content-Type": "application/json"},
        "https://example.test/search",
    )
    target = tmp_path / "search" / "page-00001.json"

    with patch.object(collector, "_request", return_value=invalid), patch.object(
        collector,
        "_request_via_curl_transport",
        return_value=success,
    ) as curl, patch.object(collector.time, "sleep") as sleep:
        result = collector._fetch_search_evidence(
            target,
            url="https://example.test/search",
            body=b"PAGENUMBER=1",
            params={"PAGENUMBER": "1"},
            timeout=1,
            retries=1,
            resume=False,
            page=1,
        )

    assert result == success
    assert target.read_bytes() == success[1]
    assert (
        tmp_path
        / "search"
        / "attempts"
        / "page-00001-invalid-attempt-1.body"
    ).read_bytes() == invalid[1]
    curl.assert_called_once()
    sleep.assert_called_once_with(0.5)


def test_invalid_outer_html_is_not_promoted_and_curl_retry_must_match_record(tmp_path):
    outer_url = "https://www.ggzy.gov.cn/information/deal/html/a/rid-1.html"
    invalid = (
        200,
        b"<html><body>access denied</body></html>",
        {"Content-Type": "text/html"},
        outer_url,
    )
    valid_body = (
        b"<html><script>var firstLastUrl = "
        b"'/information/deal/html/b/rid-1.html';</script></html>"
    )
    valid = (200, valid_body, {"Content-Type": "text/html"}, outer_url)
    target = tmp_path / "details" / "001-rid-outer.html"

    with patch.object(collector, "_request", return_value=invalid), patch.object(
        collector,
        "_request_via_curl_transport",
        return_value=valid,
    ) as curl, patch.object(collector.time, "sleep") as sleep:
        result = collector._fetch_detail_evidence(
            target,
            stem="001-rid",
            role="outer",
            url=outer_url,
            timeout=1,
            retries=1,
            resume=False,
            validator=lambda body: collector._valid_outer_html(
                body,
                outer_url=outer_url,
            ),
        )

    assert result[1] == valid_body
    assert target.read_bytes() == valid_body
    assert (
        tmp_path
        / "details"
        / "attempts"
        / "001-rid-outer-invalid-attempt-1.body"
    ).read_bytes() == invalid[1]
    curl.assert_called_once()
    sleep.assert_called_once_with(0.5)


def test_outer_html_rejects_inner_url_for_a_different_record():
    outer_url = "https://www.ggzy.gov.cn/information/deal/html/a/rid-1.html"
    body = (
        b"<html><script>var firstLastUrl = "
        b"'/information/deal/html/b/rid-2.html';</script></html>"
    )

    with pytest.raises(
        collector.CollectionError,
        match="not bound to the listing record",
    ):
        collector._valid_outer_html(body, outer_url=outer_url)


def test_outer_html_accepts_current_first_last_url_with_stale_neighbor_iframe():
    outer_url = (
        "https://www.ggzy.gov.cn/information/deal/html/a/360000/0502/20260805/"
        "rid-1.html"
    )
    body = (
        b"<html><iframe src='/information/deal/html/b/360000/0501/20260804/"
        b"neighbor.html'></iframe><script>var firstLastUrl = "
        b"'/information/deal/html/b/360000/0502/20260805/rid-1.html';"
        b"</script></html>"
    )

    collector._valid_outer_html(body, outer_url=outer_url)


def test_existing_mhtml_rejects_changed_non_core_business_fact(tmp_path):
    target = tmp_path / "existing.mhtml"
    target.write_bytes(b"old snapshot")
    expected = {
        "交易所": "北交互联",
        "项目编号": "P-1",
        "项目名称": "项目",
        "交易方式": "协议转让",
        "受让方名称": "新受让方",
        "转让标的评估值或账面净值": "100",
        "成交金额": "120",
        "成交日期": "2026-08-05",
    }
    parsed = {**expected, "受让方名称": "旧受让方"}
    parsed["转让标的评估值"] = parsed.pop("转让标的评估值或账面净值")

    with patch.object(collector, "parse_mhtml_file", return_value=parsed), pytest.raises(
        collector.CollectionError,
        match="existing MHTML facts differ",
    ):
        collector._write_mhtml(target, b"new snapshot", expected)


def test_resume_reuses_equivalent_month_manifest_with_original_retry_evidence(tmp_path):
    path = tmp_path / "manifest.json"
    first = {
        "schema_version": 1,
        "month": "2026-08",
        "details": [
            {
                "record_id": "rid-1",
                "outer_sha256": "outer-hash",
                "inner_sha256": "inner-hash",
                "outcome": "selected",
                "outer_retry_count": 1,
                "outer_transient_attempts": [{"business_code": 800}],
                "inner_retry_count": 0,
                "inner_transient_attempts": [],
            }
        ],
    }
    resumed = {
        **first,
        "details": [
            {
                **first["details"][0],
                "outer_retry_count": 0,
                "outer_transient_attempts": [],
            }
        ],
    }
    collector._write_json(path, first)

    reused = collector._reuse_equivalent_month_manifest(
        path,
        resumed,
        resume=True,
    )

    assert reused == first
    changed = {
        **resumed,
        "details": [{**resumed["details"][0], "inner_sha256": "changed"}],
    }
    assert collector._reuse_equivalent_month_manifest(
        path,
        changed,
        resume=True,
    ) is None


def test_search_resume_quarantines_code_800_canonical_then_self_heals(tmp_path):
    target = tmp_path / "search" / "page-00001.json"
    params = {"PAGENUMBER": "1"}
    url = "https://example.test/search"
    stale = b'{"code":800,"message":"busy"}'
    success = (
        200,
        b'{"code":200,"message":"success","data":{"records":[],"total":0,"pages":0}}',
        {"Content-Type": "application/json"},
        url,
    )
    _seed_canonical(target, stale, url=url, role="search", params=params)
    before_network = MagicMock()

    with patch.object(collector, "_request", return_value=success) as standard, patch.object(
        collector,
        "_request_via_browser_transport",
    ) as browser:
        result = collector._fetch_search_evidence(
            target,
            url=url,
            body=b"PAGENUMBER=1",
            params=params,
            timeout=1,
            retries=1,
            resume=True,
            page=1,
            before_network_request=before_network,
        )

    assert result == success
    assert target.read_bytes() == success[1]
    quarantine = (
        tmp_path
        / "search"
        / "attempts"
        / "quarantine"
        / "page-00001.json"
    )
    assert quarantine.read_bytes() == stale
    assert Path(str(quarantine) + ".sidecar.json").is_file()
    assert Path(str(quarantine) + ".quarantine.json").is_file()
    before_network.assert_called_once()
    standard.assert_called_once()
    browser.assert_not_called()


def test_search_resume_quarantines_incomplete_canonical_then_self_heals(tmp_path):
    target = tmp_path / "search" / "page-00001.json"
    target.parent.mkdir(parents=True)
    stale = b"incomplete canonical without sidecar"
    target.write_bytes(stale)
    success = (
        200,
        b'{"code":200,"message":"success","data":{"records":[],"total":0,"pages":0}}',
        {"Content-Type": "application/json"},
        "https://example.test/search",
    )

    with patch.object(collector, "_request", return_value=success):
        collector._fetch_search_evidence(
            target,
            url="https://example.test/search",
            body=b"PAGENUMBER=1",
            params={"PAGENUMBER": "1"},
            timeout=1,
            retries=1,
            resume=True,
            page=1,
        )

    assert target.read_bytes() == success[1]
    quarantine = (
        tmp_path
        / "search"
        / "attempts"
        / "quarantine"
        / "page-00001.json"
    )
    assert quarantine.read_bytes() == stale
    assert not Path(str(quarantine) + ".sidecar.json").exists()
    assert Path(str(quarantine) + ".quarantine.json").is_file()


def test_detail_resume_quarantines_business_error_canonical_then_self_heals(tmp_path):
    target = tmp_path / "details" / "001-rid-outer.html"
    url = "https://example.test/detail"
    stale = b'{"code":800,"message":"busy"}'
    success = (
        200,
        b"<html><body>valid detail</body></html>",
        {"Content-Type": "text/html"},
        url,
    )
    _seed_canonical(target, stale, url=url, role="outer")

    with patch.object(collector, "_request", return_value=success) as standard:
        result = collector._fetch_detail_evidence(
            target,
            stem="001-rid",
            role="outer",
            url=url,
            timeout=1,
            retries=1,
            resume=True,
            validator=collector._valid_outer_html,
        )

    assert result[1] == success[1]
    assert target.read_bytes() == success[1]
    quarantine = (
        tmp_path
        / "details"
        / "attempts"
        / "quarantine"
        / "001-rid-outer.html"
    )
    assert quarantine.read_bytes() == stale
    assert Path(str(quarantine) + ".sidecar.json").is_file()
    standard.assert_called_once()


def test_transient_attempt_retries_preserve_prior_sidecar_provenance(tmp_path):
    target = tmp_path / "search" / "attempts" / "page-00001-attempt-1.json"
    body = b'{"code":800,"message":"busy"}'
    common = {
        "url": "https://example.test/search",
        "final_url": "https://example.test/search",
        "status": 200,
        "role": "search_transient_business_error",
        "params": {"attempt": "1", "business_code": "800"},
    }

    first = collector._write_transient_attempt(
        target,
        body,
        headers={"Date": "first"},
        **common,
    )
    second = collector._write_transient_attempt(
        target,
        body,
        headers={"Date": "second"},
        **common,
    )

    assert first == target
    assert second == target.with_name("page-00001-attempt-1-repeat-1.json")
    assert first.read_bytes() == second.read_bytes() == body
    assert collector.Path(str(first) + ".sidecar.json").read_text(encoding="utf-8") != collector.Path(str(second) + ".sidecar.json").read_text(encoding="utf-8")


def test_resume_repairs_payload_written_before_sidecar(tmp_path):
    target = tmp_path / "details" / "orphan.html"
    body = b"<html><body>stable evidence</body></html>"
    target.parent.mkdir(parents=True)
    target.write_bytes(body)

    with patch.object(
        collector,
        "_request",
        return_value=(200, body, {"Content-Type": "text/html"}, "https://example.test/detail"),
    ):
        result = collector._fetch_evidence(
            target,
            url="https://example.test/detail",
            body=None,
            timeout=1,
            retries=1,
            role="outer",
            params=None,
            resume=True,
        )

    assert result[0:2] == (200, body)
    assert Path(str(target) + ".sidecar.json").is_file()


def test_rows_accepts_official_empty_page_shape():
    records, total, pages = collector._rows(
        {"code": 200, "message": "success", "data": {"records": [], "total": 0, "pages": 0}}
    )
    assert records == []
    assert (total, pages) == (0, 0)


def test_collect_month_uses_twenty_second_default_between_search_pages(tmp_path):
    page_one = (
        b'{"code":200,"message":"success","data":{"records":['
        b'{"id":"rid-1","url":"/information/deal/html/a/rid-1.html",'
        b'"transactionSourcesPlatformText":"outside"}],"total":2,"pages":2}}'
    )
    page_two = (
        b'{"code":200,"message":"success","data":{"records":['
        b'{"id":"rid-2","url":"/information/deal/html/a/rid-2.html",'
        b'"transactionSourcesPlatformText":"outside"}],"total":2,"pages":2}}'
    )
    responses = iter([
        (200, page_one, {}, "https://example.test/search"),
        (200, page_two, {}, "https://example.test/search"),
    ])

    def fetch(*_args, before_network_request, **_kwargs):
        before_network_request()
        return next(responses)

    with patch.object(
        collector,
        "_fetch_search_evidence",
        side_effect=fetch,
    ), patch.object(collector.time, "sleep") as sleep:
        manifest = collector.collect_month(
            "2026-01",
            "2026-01-01",
            "2026-01-31",
            evidence_root=tmp_path / "evidence",
            manual_root=tmp_path / "manual",
            timeout=1,
            retries=1,
            detail_delay=0,
            resume=False,
        )

    assert manifest["official_pages"] == 2
    assert manifest["official_total"] == 2
    sleep.assert_called_once_with(collector.DEFAULT_SEARCH_DELAY_SECONDS)
    assert collector.DEFAULT_SEARCH_DELAY_SECONDS == 20.0


def test_collect_month_quarantines_all_pages_when_declared_totals_change(tmp_path):
    evidence_root = tmp_path / "evidence"
    page_bodies = {
        1: (
            b'{"code":200,"message":"success","data":{"records":['
            b'{"id":"rid-1","url":"/information/deal/html/a/rid-1.html",'
            b'"transactionSourcesPlatformText":"outside"}],"total":2,"pages":2}}'
        ),
        2: (
            b'{"code":200,"message":"success","data":{"records":['
            b'{"id":"rid-2","url":"/information/deal/html/a/rid-2.html",'
            b'"transactionSourcesPlatformText":"outside"}],"total":3,"pages":2}}'
        ),
    }

    def fetch(path, *, url, params, page, **_kwargs):
        raw = page_bodies[page]
        _seed_canonical(path, raw, url=url, role="search", params=params)
        return 200, raw, {}, url

    with patch.object(collector, "_fetch_search_evidence", side_effect=fetch), pytest.raises(
        collector.CollectionError,
        match="search total/pages changed on page 2",
    ):
        collector.collect_month(
            "2026-01",
            "2026-01-01",
            "2026-01-31",
            evidence_root=evidence_root,
            manual_root=tmp_path / "manual",
            timeout=1,
            retries=1,
            detail_delay=0,
            search_delay=0,
            resume=False,
        )

    for page, raw in page_bodies.items():
        canonical = evidence_root / "2026-01" / "search" / f"page-{page:05d}.json"
        quarantine = (
            canonical.parent
            / "attempts"
            / "quarantine"
            / canonical.name
        )
        assert not canonical.exists()
        assert quarantine.read_bytes() == raw
        metadata = Path(str(quarantine) + ".quarantine.json").read_text(
            encoding="utf-8"
        )
        assert "search_total_or_pages_changed" in metadata


def test_collect_month_search_resume_pages_do_not_sleep_or_use_network(tmp_path):
    evidence_root = tmp_path / "evidence"
    begin = "2026-01-01"
    end = "2026-01-31"
    pages = [
        (
            b'{"code":200,"message":"success","data":{"records":['
            b'{"id":"rid-1","url":"/information/deal/html/a/rid-1.html",'
            b'"transactionSourcesPlatformText":"outside"}],"total":2,"pages":2}}'
        ),
        (
            b'{"code":200,"message":"success","data":{"records":['
            b'{"id":"rid-2","url":"/information/deal/html/a/rid-2.html",'
            b'"transactionSourcesPlatformText":"outside"}],"total":2,"pages":2}}'
        ),
    ]
    for page, raw in enumerate(pages, 1):
        _seed_canonical(
            evidence_root / "2026-01" / "search" / f"page-{page:05d}.json",
            raw,
            url=collector.SEARCH_URL,
            role="search",
            params=collector._params(begin, end, page),
        )

    with patch.object(collector, "_request") as standard, patch.object(
        collector,
        "_request_via_browser_transport",
    ) as browser, patch.object(collector.time, "sleep") as sleep:
        manifest = collector.collect_month(
            "2026-01",
            begin,
            end,
            evidence_root=evidence_root,
            manual_root=tmp_path / "manual",
            timeout=1,
            retries=1,
            detail_delay=0.5,
            resume=True,
        )

    assert manifest["official_pages"] == 2
    standard.assert_not_called()
    browser.assert_not_called()
    sleep.assert_not_called()


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"transactionSourcesPlatformText": "北交互联", "province": "110000"}, True),
        ({"transactionSourcesPlatformText": "上海联合产权交易所", "province": "310000"}, True),
        ({"transactionSourcesPlatformText": "天津市公共资源交易平台交易系统", "province": "120000"}, True),
        ({"transactionSourcesPlatformText": None, "province": "500000"}, True),
        ({"transactionSourcesPlatformText": "云南省公共资源交易中心", "province": "530000"}, False),
    ],
)
def test_listing_candidate_scope_uses_official_platform_and_chongqing_fallback(row, expected):
    assert collector._is_target_listing_candidate(row) is expected


def test_non_target_listing_is_manifested_without_detail_download(tmp_path):
    search = '{"code":200,"message":"success","data":{"records":[{"id":"rid-1","url":"/information/deal/html/a/rid-1.html","transactionSourcesPlatformText":"云南省公共资源交易中心","province":"530000"}],"total":1,"pages":1}}'.encode()

    with patch.object(collector, "_fetch_search_evidence", return_value=(200, search, {}, "https://www.ggzy.gov.cn/search")), patch.object(collector, "_fetch_detail_evidence") as detail:
        manifest = collector.collect_month(
            "2026-01",
            "2026-01-01",
            "2026-01-31",
            evidence_root=tmp_path / "evidence",
            manual_root=tmp_path / "manual",
            timeout=1,
            retries=1,
            detail_delay=0.25,
            resume=False,
        )

    detail.assert_not_called()
    assert manifest["official_total"] == 1
    assert manifest["detail_candidates"] == 0
    assert manifest["listing_scope_excluded"] == 1
    assert manifest["excluded_records"][0]["reason"] == "listing_scope_not_target"
    assert manifest["details"][0]["outcome"] == "excluded_listing_scope"


def test_detail_delay_occurs_only_between_records_that_use_network(tmp_path):
    search = '{"code":200,"message":"success","data":{"records":[{"id":"rid-1","url":"/information/deal/html/a/rid-1.html","transactionSourcesPlatformText":"北交互联"},{"id":"rid-2","url":"/information/deal/html/a/rid-2.html","transactionSourcesPlatformText":"北交互联"}],"total":2,"pages":1}}'.encode()
    inner = '''<html><span id="platformName">未知平台</span><table class="detail_Table"><tr><th>项目编号</th><td>P-1</td></tr><tr><th>项目名称</th><td>测试</td></tr><tr><th>交易方式</th><td></td></tr><tr><th>受让方名称</th><td></td></tr><tr><th>转让标的评估值或账面净值</th><td>1</td></tr><tr><th>成交金额</th><td>2</td></tr><tr><th>成交日期</th><td>2026-01-01</td></tr></table></html>'''.encode()

    def detail(*_args, role, before_network_request, **_kwargs):
        before_network_request()
        body = b"<html><body>outer</body></html>" if role == "outer" else inner
        return 200, body, {"Content-Type": "text/html"}, "https://www.ggzy.gov.cn/detail", {"retry_count": 0, "transient_attempts": []}

    with patch.object(collector, "_fetch_search_evidence", return_value=(200, search, {}, "https://www.ggzy.gov.cn/search")), patch.object(collector, "_fetch_detail_evidence", side_effect=detail), patch.object(collector.time, "sleep") as sleep:
        manifest = collector.collect_month(
            "2026-01",
            "2026-01-01",
            "2026-01-31",
            evidence_root=tmp_path / "evidence",
            manual_root=tmp_path / "manual",
            timeout=1,
            retries=1,
            detail_delay=0.25,
            resume=False,
        )

    assert manifest["excluded"] == 2
    sleep.assert_called_once_with(0.25)


def test_detail_resume_records_do_not_sleep(tmp_path):
    search = '{"code":200,"message":"success","data":{"records":[{"id":"rid-1","url":"/information/deal/html/a/rid-1.html","transactionSourcesPlatformText":"北交互联"},{"id":"rid-2","url":"/information/deal/html/a/rid-2.html","transactionSourcesPlatformText":"北交互联"}],"total":2,"pages":1}}'.encode()
    inner = '''<html><span id="platformName">未知平台</span><table class="detail_Table"><tr><th>项目编号</th><td>P-1</td></tr><tr><th>项目名称</th><td>测试</td></tr><tr><th>交易方式</th><td></td></tr><tr><th>受让方名称</th><td></td></tr><tr><th>转让标的评估值或账面净值</th><td>1</td></tr><tr><th>成交金额</th><td>2</td></tr><tr><th>成交日期</th><td>2026-01-01</td></tr></table></html>'''.encode()

    def detail(*_args, role, resume, **_kwargs):
        assert resume is True
        body = b"<html><body>outer</body></html>" if role == "outer" else inner
        return 200, body, {"Content-Type": "text/html"}, "https://www.ggzy.gov.cn/detail", {"retry_count": 0, "transient_attempts": []}

    with patch.object(collector, "_fetch_search_evidence", return_value=(200, search, {}, "https://www.ggzy.gov.cn/search")), patch.object(
        collector,
        "_fetch_detail_evidence",
        side_effect=detail,
    ) as detail_fetch, patch.object(collector.time, "sleep") as sleep:
        manifest = collector.collect_month(
            "2026-01",
            "2026-01-01",
            "2026-01-31",
            evidence_root=tmp_path / "evidence",
            manual_root=tmp_path / "manual",
            timeout=1,
            retries=1,
            detail_delay=0.5,
            resume=True,
        )

    assert manifest["excluded"] == 2
    assert detail_fetch.call_count == 4
    sleep.assert_not_called()


def test_collect_month_service_abort_stops_before_next_record(tmp_path):
    search = '{"code":200,"message":"success","data":{"records":[{"id":"rid-1","url":"/information/deal/html/a/rid-1.html","transactionSourcesPlatformText":"北交互联"},{"id":"rid-2","url":"/information/deal/html/a/rid-2.html","transactionSourcesPlatformText":"北交互联"}],"total":2,"pages":1}}'.encode()

    with patch.object(collector, "_fetch_search_evidence", return_value=(200, search, {}, "https://www.ggzy.gov.cn/search")), patch.object(
        collector,
        "_fetch_detail_evidence",
        side_effect=collector.FailClosedHTTPError("HTTP 429 fail-closed"),
    ) as detail_fetch, pytest.raises(
        collector.ServiceAbortError,
        match="fail-closed",
    ):
        collector.collect_month(
            "2026-01",
            "2026-01-01",
            "2026-01-31",
            evidence_root=tmp_path / "evidence",
            manual_root=tmp_path / "manual",
            timeout=1,
            retries=3,
            detail_delay=0.5,
            resume=False,
        )

    detail_fetch.assert_called_once()


def test_collect_month_normal_record_failure_does_not_abort_remaining_records(tmp_path):
    search = '{"code":200,"message":"success","data":{"records":[{"id":"rid-1","url":"/information/deal/html/a/rid-1.html","transactionSourcesPlatformText":"北交互联"},{"id":"rid-2","url":"/information/deal/html/a/rid-2.html","transactionSourcesPlatformText":"北交互联"}],"total":2,"pages":1}}'.encode()
    inner = '''<html><span id="platformName">未知平台</span><table class="detail_Table"><tr><th>项目编号</th><td>P-2</td></tr><tr><th>项目名称</th><td>测试</td></tr><tr><th>交易方式</th><td></td></tr><tr><th>受让方名称</th><td></td></tr><tr><th>转让标的评估值或账面净值</th><td>1</td></tr><tr><th>成交金额</th><td>2</td></tr><tr><th>成交日期</th><td>2026-01-01</td></tr></table></html>'''.encode()

    def detail(path, *, role, **_kwargs):
        if "00001" in path.name:
            raise collector.CollectionError("record-specific parse failure")
        body = b"<html><body>outer</body></html>" if role == "outer" else inner
        return 200, body, {"Content-Type": "text/html"}, "https://www.ggzy.gov.cn/detail", {"retry_count": 0, "transient_attempts": []}

    with patch.object(collector, "_fetch_search_evidence", return_value=(200, search, {}, "https://www.ggzy.gov.cn/search")), patch.object(
        collector,
        "_fetch_detail_evidence",
        side_effect=detail,
    ) as detail_fetch, pytest.raises(
        collector.CollectionError,
        match="month 2026-01 has 1 detail/parser failures",
    ):
        collector.collect_month(
            "2026-01",
            "2026-01-01",
            "2026-01-31",
            evidence_root=tmp_path / "evidence",
            manual_root=tmp_path / "manual",
            timeout=1,
            retries=1,
            detail_delay=0,
            resume=False,
        )

    assert detail_fetch.call_count == 3


def test_collect_month_attributes_exchange_from_outer_page_label(tmp_path):
    search = (
        '{"code":200,"message":"success","data":{"records":'
        '[{"id":"rid-outer-label","url":"/information/deal/html/a/rid-outer-label.html",'
        '"transactionSourcesPlatformText":"北交互联"}],"total":1,"pages":1}}'
    ).encode()
    outer = (
        '<html><span id="platformName">北交互联</span></html>'
    ).encode()
    inner = (
        '<html><a href="https://www.cbex.com/detail/rid-outer-label">来源</a>'
        '<table class="detail_Table">'
        '<tr><th>项目编号</th><td>P-OUTER</td></tr>'
        '<tr><th>项目名称</th><td>测试项目</td></tr>'
        '<tr><th>交易方式</th><td>协议转让</td></tr>'
        '<tr><th>受让方名称</th><td>受让方</td></tr>'
        '<tr><th>转让标的评估值或账面净值</th><td>1</td></tr>'
        '<tr><th>成交金额</th><td>2</td></tr>'
        '<tr><th>成交日期</th><td>2026-01-01</td></tr>'
        '</table></html>'
    ).encode()

    def detail(*_args, role, **_kwargs):
        body = outer if role == "outer" else inner
        return (
            200,
            body,
            {"Content-Type": "text/html"},
            "https://www.ggzy.gov.cn/detail",
            {"retry_count": 0, "transient_attempts": []},
        )

    with (
        patch.object(
            collector,
            "_fetch_search_evidence",
            return_value=(200, search, {}, "https://www.ggzy.gov.cn/search"),
        ),
        patch.object(collector, "_fetch_detail_evidence", side_effect=detail),
        patch.object(collector.time, "sleep"),
    ):
        manifest = collector.collect_month(
            "2026-01",
            "2026-01-01",
            "2026-01-31",
            evidence_root=tmp_path / "evidence",
            manual_root=tmp_path / "manual",
            timeout=1,
            retries=1,
            detail_delay=0,
            resume=False,
        )

    assert manifest["selected"] == 1
    assert manifest["parser_failures"] == []
    assert manifest["records"][0]["exchange"] == "北交互联"
