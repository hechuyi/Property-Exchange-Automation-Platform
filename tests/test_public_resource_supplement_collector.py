from __future__ import annotations

import csv
import hashlib
import io
import json
import ssl
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from scripts.collect_public_resource_deal_supplements import (
    ListingCandidate,
    StopRun,
    _request,
    audit_public_resource_evidence_dir,
    build_parser,
    collect,
    write_public_resource_evidence_html,
    write_public_resource_evidence_response,
)


class PublicResourceSupplementCollectorTest(unittest.TestCase):
    def test_request_uses_browser_transport_after_urllib_url_error(self) -> None:
        class Response:
            status = 207

            async def text(self) -> str:
                return '{"code":200,"data":{"records":[]}}'

        class RequestContext:
            def __init__(self) -> None:
                self.post_calls: list[tuple[str, dict[str, object]]] = []
                self.disposed = False

            async def post(self, url: str, **kwargs):
                self.post_calls.append((url, kwargs))
                return Response()

            async def get(self, _url: str, **_kwargs):
                raise AssertionError("POST fallback must not call GET")

            async def dispose(self) -> None:
                self.disposed = True

        request_context = RequestContext()

        class RequestFactory:
            async def new_context(self, **kwargs):
                self.kwargs = kwargs
                return request_context

        class PlaywrightContext:
            def __init__(self) -> None:
                self.request = RequestFactory()

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
                return False

        with (
            patch(
                "scripts.collect_public_resource_deal_supplements.urlopen",
                side_effect=URLError("TLS handshake timeout"),
            ),
            patch(
                "scripts.collect_public_resource_deal_supplements.async_playwright",
                return_value=PlaywrightContext(),
            ),
        ):
            status, text = _request(
                "https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList",
                data={"FINDTXT": "项目", "PAGENUMBER": "1"},
                timeout=7,
            )

        self.assertEqual(status, 207)
        self.assertEqual(text, '{"code":200,"data":{"records":[]}}')
        self.assertTrue(request_context.disposed)
        self.assertEqual(len(request_context.post_calls), 1)
        _url, kwargs = request_context.post_calls[0]
        self.assertEqual(kwargs["form"], {"FINDTXT": "项目", "PAGENUMBER": "1"})
        self.assertEqual(kwargs["timeout"], 7000)

    def test_request_does_not_fallback_after_http_error(self) -> None:
        error = HTTPError(
            "https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList",
            429,
            "Too Many Requests",
            hdrs=None,
            fp=None,
        )
        with (
            patch(
                "scripts.collect_public_resource_deal_supplements.urlopen",
                side_effect=error,
            ),
            patch(
                "scripts.collect_public_resource_deal_supplements._request_via_browser_transport"
            ) as fallback,
        ):
            with self.assertRaises(HTTPError):
                _request("https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList", timeout=7)

        fallback.assert_not_called()

    def test_request_uses_browser_transport_after_ssl_error(self) -> None:
        with (
            patch(
                "scripts.collect_public_resource_deal_supplements.urlopen",
                side_effect=ssl.SSLError("TLS connection failed"),
            ),
            patch(
                "scripts.collect_public_resource_deal_supplements._request_via_browser_transport",
                return_value=(200, "official response"),
            ) as fallback,
        ):
            status, text = _request("https://www.ggzy.gov.cn/deal/dealList.html", timeout=7)

        self.assertEqual((status, text), (200, "official response"))
        fallback.assert_called_once_with(
            "https://www.ggzy.gov.cn/deal/dealList.html",
            data=None,
            timeout=7,
        )

    def test_request_browser_transport_supports_get(self) -> None:
        class Response:
            status = 206

            async def text(self) -> str:
                return "official get response"

        class RequestContext:
            def __init__(self) -> None:
                self.get_calls: list[tuple[str, dict[str, object]]] = []
                self.disposed = False

            async def get(self, url: str, **kwargs):
                self.get_calls.append((url, kwargs))
                return Response()

            async def post(self, _url: str, **_kwargs):
                raise AssertionError("GET fallback must not call POST")

            async def dispose(self) -> None:
                self.disposed = True

        request_context = RequestContext()

        class RequestFactory:
            async def new_context(self, **_kwargs):
                return request_context

        class PlaywrightContext:
            request = RequestFactory()

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
                return False

        with (
            patch(
                "scripts.collect_public_resource_deal_supplements.urlopen",
                side_effect=URLError("TLS handshake timeout"),
            ),
            patch(
                "scripts.collect_public_resource_deal_supplements.async_playwright",
                return_value=PlaywrightContext(),
            ),
        ):
            status, text = _request("https://www.ggzy.gov.cn/deal/dealList.html", timeout=7)

        self.assertEqual(status, 206)
        self.assertEqual(text, "official get response")
        self.assertTrue(request_context.disposed)
        self.assertEqual(request_context.get_calls[0][1]["timeout"], 7000)

    def test_request_browser_transport_raises_http_error_for_rate_limit(self) -> None:
        class Response:
            status = 429

            async def text(self) -> str:
                return "rate limited"

        class RequestContext:
            disposed = False

            async def get(self, _url: str, **_kwargs):
                return Response()

            async def post(self, _url: str, **_kwargs):
                raise AssertionError("GET fallback must not call POST")

            async def dispose(self) -> None:
                self.disposed = True

        request_context = RequestContext()

        class RequestFactory:
            async def new_context(self, **_kwargs):
                return request_context

        class PlaywrightContext:
            request = RequestFactory()

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
                return False

        with (
            patch(
                "scripts.collect_public_resource_deal_supplements.urlopen",
                side_effect=URLError("TLS handshake timeout"),
            ),
            patch(
                "scripts.collect_public_resource_deal_supplements.async_playwright",
                return_value=PlaywrightContext(),
            ),
        ):
            with self.assertRaises(HTTPError) as raised:
                _request("https://www.ggzy.gov.cn/deal/dealList.html", timeout=7)

        self.assertEqual(raised.exception.code, 429)
        self.assertEqual(raised.exception.read(), b"rate limited")
        self.assertTrue(request_context.disposed)

    def test_request_normalizes_playwright_timeout_for_collector_accounting(self) -> None:
        class RequestContext:
            async def post(self, _url: str, **_kwargs):
                raise PlaywrightTimeoutError("APIRequestContext.post: timeout")

            async def dispose(self) -> None:
                pass

        class RequestFactory:
            async def new_context(self, **_kwargs):
                return RequestContext()

        class PlaywrightContext:
            request = RequestFactory()

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
                return False

        with (
            patch(
                "scripts.collect_public_resource_deal_supplements.urlopen",
                side_effect=URLError("TLS handshake timeout"),
            ),
            patch(
                "scripts.collect_public_resource_deal_supplements.async_playwright",
                return_value=PlaywrightContext(),
            ),
        ):
            with self.assertRaises(TimeoutError) as raised:
                _request(
                    "https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList",
                    data={"PAGENUMBER": "1"},
                    timeout=7,
                )

        self.assertIn("APIRequestContext.post", str(raised.exception))

    def test_collector_does_not_expose_unimplemented_pagination_flag(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["--fetch-extra-pages"])

    def test_write_evidence_html_creates_integrity_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = Path(temp_dir) / "evidence" / "outer.html"
            write_public_resource_evidence_html(
                html_path,
                "<html><body>deal</body></html>",
                source_url="https://www.ggzy.gov.cn/detail",
                http_status=200,
                evidence_role="outer_detail",
                run_id="run-1",
            )

            sidecar_path = html_path.with_suffix(".json")
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            audit = audit_public_resource_evidence_dir(Path(temp_dir))

        self.assertEqual(sidecar["source_id"], "public_resource")
        self.assertEqual(sidecar["record_family"], "deal")
        self.assertEqual(sidecar["business_id"], "deal_equity_transfer")
        self.assertEqual(sidecar["task_id"], "public_resource:deal:deal_equity_transfer")
        self.assertEqual(sidecar["save_status"], "complete")
        self.assertEqual(sidecar["source_url"], "https://www.ggzy.gov.cn/detail")
        self.assertTrue(str(sidecar["archive_content_sha256"]).startswith("sha256:"))
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["html_count"], 1)
        self.assertEqual(audit["issue_count"], 0)

    def test_evidence_audit_fails_on_missing_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = Path(temp_dir) / "evidence" / "missing.html"
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text("<html></html>", encoding="utf-8")

            audit = audit_public_resource_evidence_dir(Path(temp_dir))

        self.assertFalse(audit["ok"])
        self.assertEqual(audit["issues"][0]["code"], "missing_sidecar")

    def test_evidence_audit_fails_on_missing_required_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = Path(temp_dir) / "evidence" / "bad.html"
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html = "<html></html>"
            html_path.write_text(html, encoding="utf-8")
            html_path.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "save_status": "complete",
                        "archive_content_sha256": "sha256:"
                        + hashlib.sha256(html.encode("utf-8")).hexdigest(),
                        "archive_content_bytes": len(html.encode("utf-8")),
                    }
                ),
                encoding="utf-8",
            )

            audit = audit_public_resource_evidence_dir(Path(temp_dir))

        self.assertFalse(audit["ok"])
        self.assertEqual(audit["issues"][0]["code"], "missing_required_sidecar_field")

    def test_evidence_audit_fails_on_invalid_http_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = Path(temp_dir) / "evidence" / "bad-status.html"
            write_public_resource_evidence_html(
                html_path,
                "<html></html>",
                source_url="https://www.ggzy.gov.cn/detail",
                http_status=0,
                evidence_role="outer_detail",
                run_id="run-1",
            )

            audit = audit_public_resource_evidence_dir(Path(temp_dir))

        self.assertFalse(audit["ok"])
        self.assertEqual(audit["issues"][0]["code"], "invalid_http_status")

    def test_evidence_audit_reports_non_numeric_archive_size_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = Path(temp_dir) / "evidence" / "bad-size.html"
            write_public_resource_evidence_html(
                html_path,
                "<html></html>",
                source_url="https://www.ggzy.gov.cn/detail",
                http_status=200,
                evidence_role="outer_detail",
                run_id="run-1",
            )
            sidecar_path = html_path.with_suffix(".json")
            payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
            payload["archive_content_bytes"] = "not-a-number"
            sidecar_path.write_text(json.dumps(payload), encoding="utf-8")

            audit = audit_public_resource_evidence_dir(Path(temp_dir))

        self.assertFalse(audit["ok"])
        size_issues = [issue for issue in audit["issues"] if issue.get("code") == "archive_size_mismatch"]
        self.assertEqual(len(size_issues), 1)
        self.assertEqual(size_issues[0]["actual"], "not-a-number")

    def test_write_search_response_creates_integrity_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response_path = Path(temp_dir) / "evidence" / "cbex-G1-search-response.json"
            write_public_resource_evidence_response(
                response_path,
                '{"code":200,"data":{"records":[]}}',
                source_url="https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList",
                http_status=200,
                evidence_role="search_result",
                run_id="run-1",
                request_method="POST",
                request_params={"FINDTXT": "项目", "DEAL_STAGE": "0502"},
            )

            sidecar_path = Path(str(response_path) + ".sidecar.json")
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            audit = audit_public_resource_evidence_dir(Path(temp_dir))

        self.assertEqual(sidecar["source_id"], "public_resource")
        self.assertEqual(sidecar["record_family"], "deal")
        self.assertEqual(sidecar["business_id"], "deal_equity_transfer")
        self.assertEqual(sidecar["task_id"], "public_resource:deal:deal_equity_transfer")
        self.assertEqual(sidecar["evidence_role"], "search_result")
        self.assertEqual(sidecar["request_method"], "POST")
        self.assertEqual(sidecar["request_params"]["FINDTXT"], "项目")
        self.assertEqual(sidecar["save_status"], "complete")
        self.assertTrue(str(sidecar["archive_content_sha256"]).startswith("sha256:"))
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["json_count"], 1)
        self.assertEqual(audit["issue_count"], 0)

    def test_collect_archives_raw_search_response_before_derived_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "out"
            candidate = ListingCandidate(
                record_id="record-1",
                state="ready",
                exchange="cbex",
                business_id="equity_transfer",
                project_code="G1",
                project_name="项目一100%股权",
                listing_date="2026-07-01",
            )
            args = SimpleNamespace(
                run_id="run-search",
                output_dir=str(output_dir),
                db="unused.sqlite3",
                states="ready",
                business_ids="equity_transfer",
                exchanges="cbex",
                include_existing_deals=True,
                limit=0,
                order="asc",
                resume=True,
                retry_errors=False,
                time_begin="2026-01-01",
                time_end="2026-07-09",
                timeout=1,
                fetch_extra_pages=False,
                only_exact=True,
                min_delay=0,
                max_delay=0,
                detail_min_delay=0,
                detail_max_delay=0,
                max_consecutive_errors=3,
                progress_every=100,
            )
            raw_response = '{"code":200,"data":{"total":0,"records":[]}}'

            with (
                patch(
                    "scripts.collect_public_resource_deal_supplements.load_candidates",
                    return_value=[candidate],
                ),
                patch(
                    "scripts.collect_public_resource_deal_supplements.fetch_public_resource_search_response",
                    return_value=(
                        200,
                        raw_response,
                        {"FINDTXT": candidate.project_name, "DEAL_STAGE": "0502"},
                    ),
                ),
            ):
                summary = collect(args)

            search_files = sorted((output_dir / "evidence_html").glob("*-search-response.json"))
            self.assertEqual(summary["processed"], 1)
            self.assertEqual(len(search_files), 1)
            self.assertEqual(search_files[0].read_text(encoding="utf-8"), raw_response)
            self.assertTrue(Path(str(search_files[0]) + ".sidecar.json").is_file())
            self.assertTrue(summary["evidence_audit"]["ok"])
            self.assertEqual(summary["evidence_audit"]["json_count"], 1)

    def test_collect_archives_raw_search_response_when_json_parse_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "out"
            candidate = ListingCandidate(
                record_id="record-1",
                state="ready",
                exchange="cbex",
                business_id="equity_transfer",
                project_code="G1",
                project_name="项目一100%股权",
                listing_date="2026-07-01",
            )
            args = SimpleNamespace(
                run_id="run-bad-json",
                output_dir=str(output_dir),
                db="unused.sqlite3",
                states="ready",
                business_ids="equity_transfer",
                exchanges="cbex",
                include_existing_deals=True,
                limit=0,
                order="asc",
                resume=True,
                retry_errors=False,
                time_begin="2026-01-01",
                time_end="2026-07-09",
                timeout=1,
                fetch_extra_pages=False,
                only_exact=True,
                min_delay=0,
                max_delay=0,
                detail_min_delay=0,
                detail_max_delay=0,
                max_consecutive_errors=3,
                progress_every=100,
            )
            raw_response = "<html>not json</html>"

            with (
                patch(
                    "scripts.collect_public_resource_deal_supplements.load_candidates",
                    return_value=[candidate],
                ),
                patch(
                    "scripts.collect_public_resource_deal_supplements.fetch_public_resource_search_response",
                    return_value=(
                        200,
                        raw_response,
                        {"FINDTXT": candidate.project_name, "DEAL_STAGE": "0502"},
                    ),
                ),
            ):
                summary = collect(args)

            search_files = sorted((output_dir / "evidence_html").glob("*-search-response.json"))
            attempts = (output_dir / "attempts.csv").read_text(encoding="utf-8-sig")
            sidecar = json.loads(Path(str(search_files[0]) + ".sidecar.json").read_text(encoding="utf-8"))

            self.assertEqual(summary["processed"], 1)
            self.assertEqual(summary["matched_rows"], 0)
            self.assertEqual(len(search_files), 1)
            self.assertEqual(search_files[0].read_text(encoding="utf-8"), raw_response)
            self.assertEqual(sidecar["http_status"], 200)
            self.assertIn("JSONDecodeError", attempts)
            self.assertTrue(summary["evidence_audit"]["ok"])

    def test_collect_writes_one_attempt_when_error_reaches_stop_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "out"
            candidate = ListingCandidate(
                record_id="record-1",
                state="ready",
                exchange="cbex",
                business_id="equity_transfer",
                project_code="G1",
                project_name="项目一100%股权",
                listing_date="2026-07-01",
            )
            args = SimpleNamespace(
                run_id="run-stop-on-error",
                output_dir=str(output_dir),
                db="unused.sqlite3",
                states="ready",
                business_ids="equity_transfer",
                exchanges="cbex",
                include_existing_deals=True,
                limit=0,
                order="asc",
                resume=True,
                retry_errors=False,
                time_begin="2026-01-01",
                time_end="2026-07-09",
                timeout=1,
                fetch_extra_pages=False,
                only_exact=True,
                min_delay=0,
                max_delay=0,
                detail_min_delay=0,
                detail_max_delay=0,
                max_consecutive_errors=1,
                progress_every=100,
            )

            with (
                patch(
                    "scripts.collect_public_resource_deal_supplements.load_candidates",
                    return_value=[candidate],
                ),
                patch(
                    "scripts.collect_public_resource_deal_supplements.fetch_public_resource_search_response",
                    side_effect=HTTPError(
                        "https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList",
                        500,
                        "Server Error",
                        hdrs=None,
                        fp=io.BytesIO(b'{"code":800,"message":"temporary failure"}'),
                    ),
                ),
            ):
                with self.assertRaises(StopRun):
                    collect(args)

            with (output_dir / "attempts.csv").open(encoding="utf-8-sig", newline="") as handle:
                attempts = list(csv.DictReader(handle))
            error_files = list((output_dir / "evidence_html").glob("*-search-response.json"))
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["record_id"], candidate.record_id)
            self.assertEqual(attempts[0]["status"], "error")
            self.assertEqual(attempts[0]["error_type"], "HTTPError")
            self.assertEqual(len(error_files), 1)
            self.assertEqual(
                error_files[0].read_text(encoding="utf-8"),
                '{"code":800,"message":"temporary failure"}',
            )
            self.assertTrue(Path(str(error_files[0]) + ".sidecar.json").is_file())


if __name__ == "__main__":
    unittest.main()
