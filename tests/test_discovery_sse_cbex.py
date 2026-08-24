from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from peap.constants import TYPE_PRE_DISCLOSURE
from peap.downloaders.cbex_physical import CbexPhysicalAssetDownloader, _ListSource
from peap.downloaders.common import DownloadSummary, HttpFetchedText
from peap.downloaders.discovery_evidence import DiscoveryEvidenceError
from peap.downloaders.sse_physical import ShanghaiPhysicalAssetDownloader


def _http_text(
    raw_bytes: bytes,
    *,
    source_url: str,
    final_url: str | None = None,
    status: int = 200,
) -> HttpFetchedText:
    return HttpFetchedText(
        raw_bytes.decode("utf-8"),
        source_url=source_url,
        final_url=final_url or source_url,
        http_status=status,
        raw_bytes=raw_bytes,
    )


def _sse_response(
    *,
    page: int,
    rows: list[dict[str, object]],
    total_records: int,
    page_count: int | None = None,
    project_type: str = "ZICHANZHUANRANG",
    raw_suffix: bytes = b"\n",
) -> HttpFetchedText:
    extra: object = total_records
    if page_count is not None:
        extra = {"total": total_records, "pageCount": page_count}
    payload = {"code": 200, "data": rows, "extra": extra}
    raw_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    ) + raw_suffix
    endpoint_by_project_type = {
        "ZICHANZHUANRANG": "/prjs/realright/list",
        "CHANQUAN": "/prjs/equity/list",
        "ZENGZI": "/prjs/capitalincrease/list",
    }
    endpoint = endpoint_by_project_type[project_type]
    source_url = f"https://www.suaee.com/si{endpoint}?pageNo={page}"
    return _http_text(
        raw_bytes,
        source_url=source_url,
        final_url=f"{source_url}&served=1",
        status=206,
    )


def _cbex_response(
    *,
    page: int,
    rows: list[dict[str, object]],
    total_pages: int,
    total_records: int | None,
    business_type: str = "SW",
    asset_type: str | None = "house",
    callback: str | None = None,
) -> HttpFetchedText:
    data: dict[str, object] = {
        "data": rows,
        "totalPage": total_pages,
    }
    if total_records is not None:
        data["total"] = total_records
    payload = {"data": data}
    callback = callback or f"jQuery_page_{page}"
    raw_bytes = (
        callback
        + "("
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ");\n"
    ).encode("utf-8")
    query = {
        "callback": callback,
        "fromPage": str(page),
        "pageSize": "2",
        "businessType": business_type,
    }
    if asset_type is not None:
        query["assetType"] = asset_type
    source_url = (
        "https://www.cbex.com.cn/onss-api/jsonp/project/search?"
        + urllib.parse.urlencode(query)
    )
    return _http_text(
        raw_bytes,
        source_url=source_url,
        final_url=f"{source_url}&edge=1",
    )


def _sse_row(identifier: str) -> dict[str, object]:
    return {
        "XMID": identifier,
        "XMBH": f"SSE-{identifier}",
        "XMMC": f"SSE project {identifier}",
        "PLKSRQ": "2026-07-10",
    }


def _cbex_row(identifier: str) -> dict[str, object]:
    return {
        "code": f"CBEX-{identifier}",
        "url": f"/xm/zczr/2026/07/{identifier}.html",
        "title": f"CBEX project {identifier}",
        "disclosuretime": "2026-07-10",
    }


def _manifest_paths(root: str) -> list[Path]:
    return sorted(Path(root).glob("_evidence/**/discovery/**/manifest.json"))


def _load_only_manifest(testcase: unittest.TestCase, root: str) -> tuple[Path, dict]:
    paths = _manifest_paths(root)
    testcase.assertEqual(len(paths), 1)
    return paths[0], json.loads(paths[0].read_text(encoding="utf-8"))


def _load_task_manifest(testcase: unittest.TestCase, root: str) -> tuple[Path, dict]:
    paths = sorted(Path(root).glob("_evidence/**/discovery/task_manifest.json"))
    testcase.assertEqual(len(paths), 1)
    return paths[0], json.loads(paths[0].read_text(encoding="utf-8"))


class _UrlopenResponse:
    def __init__(self, raw_bytes: bytes, *, final_url: str, status: int) -> None:
        self._raw_bytes = raw_bytes
        self._final_url = final_url
        self.status = status
        self.headers = SimpleNamespace(get_content_charset=lambda: "utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self._raw_bytes

    def geturl(self) -> str:
        return self._final_url

    def getcode(self) -> int:
        return self.status


class _AsyncApiResponse:
    def __init__(self, raw_bytes: bytes, *, final_url: str, status: int = 200) -> None:
        self._raw_bytes = raw_bytes
        self.url = final_url
        self.status = status

    async def body(self) -> bytes:
        return self._raw_bytes

    async def text(self) -> str:
        return self._raw_bytes.decode("utf-8")


class ListTransportEvidenceTest(unittest.TestCase):
    def test_sse_post_json_returns_exact_http_transport_evidence(self) -> None:
        raw_bytes = b'{"code":200,"data":[],"extra":0}\n'
        source_url = "https://www.suaee.com/si/prjs/realright/list"
        final_url = "https://www.suaee.com/si/prjs/realright/list?edge=1"
        downloader = ShanghaiPhysicalAssetDownloader(html_root="/tmp/test")

        with patch.object(
            downloader,
            "_urlopen",
            return_value=_UrlopenResponse(raw_bytes, final_url=final_url, status=206),
        ):
            response = downloader._post_json(source_url, {"pageNo": 1, "pageSize": 20})

        self.assertIsInstance(response, HttpFetchedText)
        self.assertEqual(response.raw_bytes, raw_bytes)
        self.assertEqual(response.source_url, source_url)
        self.assertEqual(response.final_url, final_url)
        self.assertEqual(response.http_status, 206)
        self.assertEqual(str(response), raw_bytes.decode("utf-8"))

    def test_cbex_api_one_returns_unmodified_jsonp_transport_evidence(self) -> None:
        callback = "jQuery123_1000"
        raw_bytes = (
            callback + '({"data":{"data":[],"totalPage":1,"total":0}});\n'
        ).encode("utf-8")
        api_response = _AsyncApiResponse(raw_bytes, final_url="https://unused.invalid")

        async def get(source_url: str, **_kwargs):
            api_response.url = f"{source_url}&served=1"
            return api_response

        request = SimpleNamespace(get=AsyncMock(side_effect=get))
        context = SimpleNamespace(request=request)
        source = _ListSource(
            label="house",
            business_type="SW",
            referer="https://www.cbex.com.cn/xm/zczr/fwtd/",
            asset_type="house",
        )
        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test")

        with (
            patch("peap.downloaders.cbex_physical.time.time", return_value=1),
            patch("peap.downloaders.cbex_physical.random.randint", return_value=123),
        ):
            response = asyncio.run(
                downloader._api_one(
                    context=context,
                    source=source,
                    page_index=2,
                )
            )

        self.assertIsInstance(response, HttpFetchedText)
        self.assertEqual(response.raw_bytes, raw_bytes)
        self.assertEqual(response.final_url, f"{response.source_url}&served=1")
        self.assertEqual(response.http_status, 200)
        parsed_source_url = urllib.parse.urlsplit(response.source_url)
        self.assertEqual(
            urllib.parse.urlunsplit(parsed_source_url._replace(query="")),
            "https://www.cbex.com.cn/onss-api/jsonp/project/search",
        )
        query = urllib.parse.parse_qs(parsed_source_url.query)
        self.assertEqual(query["callback"], [callback])
        self.assertEqual(query["fromPage"], ["2"])
        self.assertEqual(query["pageSize"], [str(downloader.page_size)])
        self.assertEqual(query["businessType"], ["SW"])
        self.assertEqual(query["assetType"], ["house"])
        self.assertEqual(str(response), raw_bytes.decode("utf-8"))

    def test_sse_decoder_uses_raw_bytes_when_string_content_diverges(self) -> None:
        original = _sse_response(
            page=1,
            rows=[_sse_row("RAW")],
            total_records=1,
        )
        response = HttpFetchedText(
            '{"code":500,"message":"diverged string"}',
            source_url=original.source_url,
            final_url=original.final_url,
            http_status=original.http_status,
            raw_bytes=original.raw_bytes,
        )
        downloader = ShanghaiPhysicalAssetDownloader(html_root="/tmp/test", page_size=2)

        _, rows, total_records, total_pages = downloader._decode_sse_list_response(
            response
        )

        self.assertEqual(rows, [_sse_row("RAW")])
        self.assertEqual(total_records, 1)
        self.assertEqual(total_pages, 1)

    def test_cbex_decoder_uses_raw_bytes_when_string_content_diverges(self) -> None:
        original = _cbex_response(
            page=1,
            rows=[_cbex_row("RAW")],
            total_pages=1,
            total_records=1,
        )
        response = HttpFetchedText(
            "__jsl_clearance_s=diverged",
            source_url=original.source_url,
            final_url=original.final_url,
            http_status=original.http_status,
            raw_bytes=original.raw_bytes,
        )
        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test", page_size=2)

        rows, total_pages, total_records = downloader._decode_cbex_list_response(
            response
        )

        self.assertEqual(rows, [_cbex_row("RAW")])
        self.assertEqual(total_pages, 1)
        self.assertEqual(total_records, 1)

    def test_cbex_decoder_accepts_anti_json_hijacking_prefix(self) -> None:
        original = _cbex_response(
            page=1,
            rows=[_cbex_row("PREFIX")],
            total_pages=1,
            total_records=1,
        )
        response = HttpFetchedText(
            str(original),
            source_url=original.source_url,
            final_url=original.final_url,
            http_status=original.http_status,
            raw_bytes=b"/**/" + original.raw_bytes,
        )
        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test", page_size=2)

        rows, total_pages, total_records = downloader._decode_cbex_list_response(
            response
        )

        self.assertEqual(rows, [_cbex_row("PREFIX")])
        self.assertEqual(total_pages, 1)
        self.assertEqual(total_records, 1)

    def test_cbex_decoder_accepts_live_total_record_num_field(self) -> None:
        original = _cbex_response(
            page=1,
            rows=[_cbex_row("LIVE")],
            total_pages=1,
            total_records=1,
        )
        raw_text = str(original).replace('"total":1', '"totalRecordNum":1')
        raw_bytes = ("/**/" + raw_text).encode("utf-8")
        response = HttpFetchedText(
            raw_text,
            source_url=original.source_url,
            final_url=original.final_url,
            http_status=original.http_status,
            raw_bytes=raw_bytes,
        )
        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test", page_size=2)

        rows, total_pages, total_records = downloader._decode_cbex_list_response(
            response
        )

        self.assertEqual(rows, [_cbex_row("LIVE")])
        self.assertEqual(total_pages, 1)
        self.assertEqual(total_records, 1)

    def test_decoders_reject_missing_original_response_bytes(self) -> None:
        cases = [
            (
                "sse",
                ShanghaiPhysicalAssetDownloader(
                    html_root="/tmp/test",
                    page_size=2,
                )._decode_sse_list_response,
                HttpFetchedText(
                    '{"code":200,"data":[],"extra":0}',
                    source_url="https://www.suaee.com/si/prjs/realright/list",
                    final_url="https://www.suaee.com/si/prjs/realright/list",
                    http_status=200,
                ),
            ),
            (
                "cbex",
                CbexPhysicalAssetDownloader(
                    html_root="/tmp/test",
                    page_size=2,
                )._decode_cbex_list_response,
                HttpFetchedText(
                    'jQuery_page_1({"data":{"data":[],"totalPage":0,"total":0}});',
                    source_url=(
                        "https://www.cbex.com.cn/onss-api/jsonp/project/search"
                        "?callback=jQuery_page_1&fromPage=1&pageSize=2"
                        "&businessType=SW&assetType=house"
                    ),
                    final_url=(
                        "https://www.cbex.com.cn/onss-api/jsonp/project/search"
                        "?callback=jQuery_page_1&fromPage=1&pageSize=2"
                        "&businessType=SW&assetType=house"
                    ),
                    http_status=200,
                ),
            ),
        ]

        for label, decode, response in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                ValueError,
                "original response bytes",
            ):
                decode(response)

    def test_dict_list_transport_is_rejected_without_reconstructed_source(self) -> None:
        sse = ShanghaiPhysicalAssetDownloader(html_root="/tmp/test")
        cbex = CbexPhysicalAssetDownloader(html_root="/tmp/test")
        source = _ListSource(
            label="house",
            business_type="SW",
            referer="https://www.cbex.com.cn/xm/zczr/fwtd/",
            asset_type="house",
        )

        with self.assertRaises(DiscoveryEvidenceError):
            sse._coerce_sse_list_response(
                {"code": 200, "data": [], "extra": 0},
                list_project_type="ZICHANZHUANRANG",
            )
        with self.assertRaises(DiscoveryEvidenceError):
            cbex._coerce_cbex_list_response(
                {"data": {"data": [], "totalPage": 0, "total": 0}},
                source=source,
                page_index=1,
            )

    def test_sse_transport_urls_must_match_authoritative_project_endpoint(self) -> None:
        valid = _sse_response(page=1, rows=[], total_records=0)
        downloader = ShanghaiPhysicalAssetDownloader(html_root="/tmp/test")
        cases = {
            "source-host": HttpFetchedText(
                str(valid),
                source_url="https://mirror.invalid/si/prjs/realright/list",
                final_url=valid.final_url,
                http_status=200,
                raw_bytes=valid.raw_bytes,
            ),
            "final-path": HttpFetchedText(
                str(valid),
                source_url=valid.source_url,
                final_url="https://www.suaee.com/si/prjs/equity/list",
                http_status=200,
                raw_bytes=valid.raw_bytes,
            ),
        }

        for label, response in cases.items():
            with self.subTest(label=label), self.assertRaisesRegex(
                DiscoveryEvidenceError,
                "authoritative endpoint",
            ):
                downloader._coerce_sse_list_response(
                    response,
                    list_project_type="ZICHANZHUANRANG",
                )

    def test_cbex_jsonp_callback_must_match_source_url_callback(self) -> None:
        response = _cbex_response(
            page=1,
            rows=[],
            total_pages=0,
            total_records=0,
            callback="jQuery_source",
        )
        mismatched_raw = response.raw_bytes.replace(
            b"jQuery_source(",
            b"jQuery_body(",
            1,
        )
        mismatched = HttpFetchedText(
            mismatched_raw.decode("utf-8"),
            source_url=response.source_url,
            final_url=response.final_url,
            http_status=response.http_status,
            raw_bytes=mismatched_raw,
        )
        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test", page_size=2)

        with self.assertRaisesRegex(ValueError, "callback"):
            downloader._decode_cbex_list_response(mismatched)

    def test_cbex_transport_urls_must_preserve_authoritative_query_scope(self) -> None:
        valid = _cbex_response(
            page=1,
            rows=[],
            total_pages=0,
            total_records=0,
        )
        source = _ListSource(
            label="house",
            business_type="SW",
            referer="https://www.cbex.com.cn/xm/zczr/fwtd/",
            asset_type="house",
        )
        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test", page_size=2)
        source_without_business_type = valid.source_url.replace("&businessType=SW", "")
        final_without_asset_type = valid.final_url.replace("&assetType=house", "")
        cases = {
            "source-host": HttpFetchedText(
                str(valid),
                source_url=valid.source_url.replace("www.cbex.com.cn", "mirror.invalid"),
                final_url=valid.final_url,
                http_status=200,
                raw_bytes=valid.raw_bytes,
            ),
            "source-business-type": HttpFetchedText(
                str(valid),
                source_url=source_without_business_type,
                final_url=valid.final_url,
                http_status=200,
                raw_bytes=valid.raw_bytes,
            ),
            "final-asset-type": HttpFetchedText(
                str(valid),
                source_url=valid.source_url,
                final_url=final_without_asset_type,
                http_status=200,
                raw_bytes=valid.raw_bytes,
            ),
        }

        for label, response in cases.items():
            with self.subTest(label=label), self.assertRaisesRegex(
                DiscoveryEvidenceError,
                "CBEX list URL",
            ):
                downloader._coerce_cbex_list_response(
                    response,
                    source=source,
                    page_index=1,
                )


class ListCountContractTest(unittest.TestCase):
    def test_sse_page_count_without_record_total_fails_closed(self) -> None:
        raw_bytes = (
            b'{"code":200,"data":[],"extra":{"pageCount":2}}\n'
        )
        response = _http_text(
            raw_bytes,
            source_url="https://www.suaee.com/si/prjs/realright/list",
        )
        downloader = ShanghaiPhysicalAssetDownloader(html_root="/tmp/test", page_size=2)

        with self.assertRaisesRegex(ValueError, "total records.*missing"):
            downloader._decode_sse_list_response(response)

    def test_sse_total_records_and_page_count_must_close_with_page_size(self) -> None:
        downloader = ShanghaiPhysicalAssetDownloader(html_root="/tmp/test", page_size=2)
        valid = _sse_response(
            page=1,
            rows=[_sse_row("A"), _sse_row("B")],
            total_records=3,
            page_count=2,
        )
        invalid = _sse_response(
            page=1,
            rows=[_sse_row("A"), _sse_row("B")],
            total_records=3,
            page_count=3,
        )

        _, _, total_records, total_pages = downloader._decode_sse_list_response(valid)
        self.assertEqual((total_records, total_pages), (3, 2))
        with self.assertRaisesRegex(ValueError, "pageCount.*does not close"):
            downloader._decode_sse_list_response(invalid)

    def test_cbex_total_records_is_required(self) -> None:
        response = _cbex_response(
            page=1,
            rows=[],
            total_pages=0,
            total_records=None,
        )
        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test", page_size=2)

        with self.assertRaisesRegex(ValueError, "total records.*missing"):
            downloader._decode_cbex_list_response(response)

    def test_cbex_total_records_total_page_and_page_size_must_close(self) -> None:
        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test", page_size=2)
        valid = _cbex_response(
            page=1,
            rows=[_cbex_row("A"), _cbex_row("B")],
            total_pages=2,
            total_records=3,
        )
        invalid = _cbex_response(
            page=1,
            rows=[_cbex_row("A"), _cbex_row("B")],
            total_pages=3,
            total_records=3,
        )

        rows, total_pages, total_records = downloader._decode_cbex_list_response(valid)
        self.assertEqual(len(rows), 2)
        self.assertEqual((total_records, total_pages), (3, 2))
        with self.assertRaisesRegex(ValueError, "totalPage.*does not close"):
            downloader._decode_cbex_list_response(invalid)

    def test_cbex_conflicting_total_record_fields_fail_closed(self) -> None:
        callback = "jQuery_conflicting_totals"
        raw_bytes = (
            callback
            + '({"data":{"data":[],"totalPage":2,"total":3,"totalCount":4}});'
        ).encode("utf-8")
        response = _http_text(
            raw_bytes,
            source_url=(
                "https://www.cbex.com.cn/onss-api/jsonp/project/search"
                f"?callback={callback}&fromPage=1&pageSize=2"
                "&businessType=SW&assetType=house"
            ),
        )
        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test", page_size=2)

        with self.assertRaisesRegex(ValueError, "conflicting declared total records"):
            downloader._decode_cbex_list_response(response)


class SseDiscoveryEvidenceTest(unittest.TestCase):
    def _downloader(self, root: str, **kwargs) -> ShanghaiPhysicalAssetDownloader:
        return ShanghaiPhysicalAssetDownloader(
            html_root=root,
            page_size=2,
            list_query_specs=[("ZICHANZHUANRANG", "2")],
            run_id="run-sse-discovery",
            **kwargs,
        )

    def _collect(self, downloader: ShanghaiPhysicalAssetDownloader) -> tuple[DownloadSummary, list]:
        summary = DownloadSummary()
        candidates: list = []
        downloader._collect_list_candidates(
            output_dir=downloader.html_root,
            summary=summary,
            candidates=candidates,
            start=None,
            end=None,
        )
        return summary, candidates

    def test_collect_rejects_empty_query_config_before_creating_complete_task(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            downloader = ShanghaiPhysicalAssetDownloader(
                html_root=root,
                page_size=2,
                list_query_specs=[],
                run_id="run-sse-empty-config",
            )
            downloader._query_list_page = Mock(  # type: ignore[method-assign]
                return_value=_sse_response(
                    page=1,
                    rows=[_sse_row("A")],
                    total_records=1,
                )
            )

            summary, candidates = self._collect(downloader)

            self.assertEqual(candidates, [])
            self.assertTrue(summary.typed_errors)
            self.assertIsNotNone(summary.discovery_task_manifest)
            downloader._query_list_page.assert_not_called()
            task_path, task_manifest = _load_task_manifest(self, root)
            self.assertEqual(task_manifest["save_status"], "failed")
            self.assertEqual(
                Path(root, summary.discovery_task_manifest["path"]),
                task_path,
            )

    def test_collect_rejects_authoritative_query_subset(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            downloader = ShanghaiPhysicalAssetDownloader(
                html_root=root,
                page_size=2,
                output_type=TYPE_PRE_DISCLOSURE,
                list_query_specs=[("CHANQUAN", "1")],
                run_id="run-sse-query-subset",
            )
            downloader._query_list_page = Mock(  # type: ignore[method-assign]
                return_value=_sse_response(
                    page=1,
                    rows=[_sse_row("A")],
                    total_records=1,
                    project_type="CHANQUAN",
                )
            )

            summary, candidates = self._collect(downloader)

            self.assertEqual(candidates, [])
            self.assertTrue(summary.typed_errors)
            self.assertIsNotNone(summary.discovery_task_manifest)
            _, task_manifest = _load_task_manifest(self, root)
            self.assertEqual(task_manifest["save_status"], "failed")
            downloader._query_list_page.assert_not_called()

    def test_complete_query_archives_each_raw_json_page_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            responses = [
                _sse_response(
                    page=1,
                    rows=[_sse_row("A"), _sse_row("B")],
                    total_records=3,
                ),
                _sse_response(page=2, rows=[_sse_row("C")], total_records=3),
            ]
            downloader = self._downloader(root)
            downloader._query_list_page = Mock(side_effect=responses)  # type: ignore[method-assign]

            summary, candidates = self._collect(downloader)

            self.assertEqual(summary.pages_requested, 2)
            self.assertEqual([candidate.xmid for candidate in candidates], ["A", "B", "C"])
            self.assertIsNotNone(summary.discovery_task_manifest)
            self.assertTrue(Path(root, summary.discovery_task_manifest["path"]).is_file())
            manifest_path, manifest = _load_only_manifest(self, root)
            self.assertEqual(manifest["save_status"], "complete")
            self.assertEqual(manifest["coverage_status"], "complete")
            self.assertEqual(manifest["termination_reason"], "declared_pages_exhausted")
            self.assertEqual(manifest["declared_total_items"], 3)
            self.assertEqual(manifest["declared_total_pages"], 2)
            self.assertEqual(manifest["observed_row_count"], 3)

            raw_paths = sorted(manifest_path.parent.glob("page_*.raw.json"))
            self.assertEqual([path.read_bytes() for path in raw_paths], [r.raw_bytes for r in responses])
            first_sidecar = json.loads(
                (manifest_path.parent / "page_000001.meta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(first_sidecar["source_url"], responses[0].source_url)
            self.assertEqual(first_sidecar["final_url"], responses[0].final_url)
            self.assertEqual(first_sidecar["http_status"], 206)
            self.assertEqual(first_sidecar["parse_status"], "complete")
            _, task_manifest = _load_task_manifest(self, root)
            self.assertEqual(task_manifest["save_status"], "complete")
            self.assertEqual(
                task_manifest["expected_query_ids"],
                ["ZICHANZHUANRANG-gplx-2"],
            )
            self.assertEqual(
                [entry["query_id"] for entry in task_manifest["queries"]],
                task_manifest["expected_query_ids"],
            )
            self.assertEqual(task_manifest["candidate_count"], 3)
            self.assertEqual(
                len(task_manifest["candidate_fingerprints"]),
                len(summary.candidate_entries),
            )

    def test_authoritative_declared_pages_override_smaller_operator_limit(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            responses = [
                _sse_response(
                    page=1,
                    rows=[_sse_row("A"), _sse_row("B")],
                    total_records=3,
                    page_count=2,
                ),
                _sse_response(
                    page=2,
                    rows=[_sse_row("C")],
                    total_records=3,
                    page_count=2,
                ),
            ]
            downloader = self._downloader(root, max_pages=1)
            downloader._query_list_page = Mock(side_effect=responses)  # type: ignore[method-assign]

            summary, candidates = self._collect(downloader)

            self.assertFalse(summary.typed_errors)
            self.assertEqual(summary.pages_requested, 2)
            self.assertEqual([candidate.xmid for candidate in candidates], ["A", "B", "C"])
            self.assertEqual(len(summary.list_page_observations), 1)
            self.assertEqual(summary.list_page_observations[0]["status"], "max_pages_overridden")
            _, manifest = _load_only_manifest(self, root)
            self.assertEqual(manifest["save_status"], "complete")
            self.assertEqual(manifest["termination_facts"]["requested_max_pages"], 1)
            self.assertTrue(manifest["termination_facts"]["max_pages_overridden"])

    def test_declared_total_mismatch_fails_closed_without_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            downloader = self._downloader(root)
            downloader._query_list_page = Mock(  # type: ignore[method-assign]
                side_effect=[
                    _sse_response(
                        page=1,
                        rows=[_sse_row("A"), _sse_row("B")],
                        total_records=3,
                    ),
                    _sse_response(page=2, rows=[], total_records=3),
                ]
            )

            summary, candidates = self._collect(downloader)

            self.assertEqual(candidates, [])
            self.assertTrue(summary.typed_errors)
            _, manifest = _load_only_manifest(self, root)
            self.assertEqual(manifest["save_status"], "failed")
            self.assertEqual(manifest["coverage_status"], "failed")
            self.assertIn("declared total", manifest["failure_details"]["error"])
            _, task_manifest = _load_task_manifest(self, root)
            self.assertEqual(task_manifest["save_status"], "failed")
            self.assertIsNotNone(summary.discovery_task_manifest)
            self.assertEqual(
                Path(root, summary.discovery_task_manifest["path"]),
                next(Path(root).glob("_evidence/**/discovery/task_manifest.json")),
            )

    def test_repeated_nonempty_page_fails_closed_without_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            downloader = self._downloader(root)
            repeated_rows = [_sse_row("A"), _sse_row("B")]
            downloader._query_list_page = Mock(  # type: ignore[method-assign]
                side_effect=[
                    _sse_response(page=1, rows=repeated_rows, total_records=4),
                    _sse_response(page=2, rows=repeated_rows, total_records=4),
                ]
            )

            summary, candidates = self._collect(downloader)

            self.assertEqual(candidates, [])
            self.assertTrue(summary.typed_errors)
            _, manifest = _load_only_manifest(self, root)
            self.assertEqual(manifest["save_status"], "failed")
            self.assertEqual(manifest["termination_reason"], "evidence_failed")
            self.assertIn("repeated page identity", manifest["failure_details"]["error"])

    def test_cross_page_partial_row_overlap_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            downloader = self._downloader(root)
            downloader._query_list_page = Mock(  # type: ignore[method-assign]
                side_effect=[
                    _sse_response(
                        page=1,
                        rows=[_sse_row("A"), _sse_row("B")],
                        total_records=4,
                    ),
                    _sse_response(
                        page=2,
                        rows=[_sse_row("B"), _sse_row("C")],
                        total_records=4,
                    ),
                ]
            )

            summary, candidates = self._collect(downloader)

            self.assertEqual(candidates, [])
            self.assertTrue(summary.typed_errors)
            _, manifest = _load_only_manifest(self, root)
            self.assertEqual(manifest["save_status"], "failed")
            self.assertIn(
                "overlapping row identities",
                manifest["failure_details"]["error"],
            )

    def test_task_manifest_declares_every_authoritative_sse_query(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            downloader = ShanghaiPhysicalAssetDownloader(
                html_root=root,
                page_size=2,
                output_type=TYPE_PRE_DISCLOSURE,
                list_query_specs=[
                    ("CHANQUAN", "1"),
                    ("ZENGZI", "1"),
                ],
                run_id="run-sse-multi-query",
            )
            downloader._query_list_page = Mock(  # type: ignore[method-assign]
                side_effect=[
                    _sse_response(
                        page=1,
                        rows=[_sse_row("A")],
                        total_records=1,
                        project_type="CHANQUAN",
                    ),
                    _sse_response(
                        page=1,
                        rows=[_sse_row("B")],
                        total_records=1,
                        project_type="ZENGZI",
                    ),
                ]
            )

            summary, candidates = self._collect(downloader)

            self.assertFalse(summary.typed_errors)
            self.assertEqual([candidate.xmid for candidate in candidates], ["A", "B"])
            _, task_manifest = _load_task_manifest(self, root)
            self.assertEqual(
                task_manifest["expected_query_ids"],
                ["CHANQUAN-gplx-1", "ZENGZI-gplx-1"],
            )
            self.assertEqual(task_manifest["save_status"], "complete")

    def test_request_and_structure_errors_write_failed_manifests(self) -> None:
        cases = {
            "request": RuntimeError("network stopped"),
            "structure": _http_text(
                b'{"code":200,"data":{"data":""},"extra":1}\n',
                source_url="https://www.suaee.com/si/prjs/realright/list?pageNo=1",
            ),
        }
        for label, outcome in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root:
                downloader = self._downloader(root)
                downloader._query_list_page = Mock(  # type: ignore[method-assign]
                    side_effect=outcome if isinstance(outcome, Exception) else None,
                    return_value=None if isinstance(outcome, Exception) else outcome,
                )

                summary, candidates = self._collect(downloader)

                self.assertEqual(candidates, [])
                self.assertTrue(summary.typed_errors)
                manifest_path, manifest = _load_only_manifest(self, root)
                self.assertEqual(manifest["save_status"], "failed")
                if label == "structure":
                    self.assertEqual(
                        (manifest_path.parent / "page_000001.raw.json").read_bytes(),
                        outcome.raw_bytes,
                    )
                    sidecar = json.loads(
                        (manifest_path.parent / "page_000001.meta.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    self.assertEqual(sidecar["parse_status"], "failed")


class CbexDiscoveryEvidenceTest(unittest.TestCase):
    source = _ListSource(
        label="house",
        business_type="SW",
        referer="https://www.cbex.com.cn/xm/zczr/fwtd/",
        asset_type="house",
    )
    transport_source = _ListSource(
        label="transport",
        business_type="SW",
        referer="https://www.cbex.com.cn/xm/zczr/jtysgj/",
        asset_type="transport",
    )
    equipment_source = _ListSource(
        label="equipment",
        business_type="SW",
        referer="https://www.cbex.com.cn/xm/zczr/sb/",
        asset_type="equipment",
    )
    authoritative_house_source = _ListSource(
        label="房屋土地",
        business_type="SW",
        referer="https://www.cbex.com.cn/xm/zczr/fwtd/",
        asset_type="house",
    )
    authoritative_transport_source = _ListSource(
        label="交通运输工具",
        business_type="SW",
        referer="https://www.cbex.com.cn/xm/zczr/jtysgj/",
        asset_type="transport",
    )
    authoritative_equipment_source = _ListSource(
        label="设备",
        business_type="SW",
        referer="https://www.cbex.com.cn/xm/zczr/sb/",
        asset_type="equipment",
    )

    def _downloader(self, root: str, **kwargs) -> CbexPhysicalAssetDownloader:
        return CbexPhysicalAssetDownloader(
            html_root=root,
            page_size=2,
            list_sources=[],
            run_id="run-cbex-discovery",
            **kwargs,
        )

    def _collect(self, downloader: CbexPhysicalAssetDownloader) -> tuple[DownloadSummary, list]:
        summary = DownloadSummary()
        candidates: list = []
        with patch("peap.downloaders.cbex_physical.asyncio.sleep", new=AsyncMock()):
            asyncio.run(
                downloader._collect_by_source(
                    context=SimpleNamespace(),
                    page=SimpleNamespace(),
                    source=self.source,
                    outdir=downloader.html_root,
                    summary=summary,
                    seen=set(),
                    cands=candidates,
                    start=None,
                    end=None,
                )
            )
        return summary, candidates

    def test_collect_rejects_empty_source_config_before_creating_complete_task(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            downloader = self._downloader(root)
            summary = DownloadSummary()
            candidates: list = []

            completed = asyncio.run(
                downloader._collect_list_sources_with_evidence(
                    context=SimpleNamespace(),
                    page=SimpleNamespace(),
                    outdir=root,
                    summary=summary,
                    seen=set(),
                    cands=candidates,
                    start=None,
                    end=None,
                )
            )

            self.assertFalse(completed)
            self.assertEqual(candidates, [])
            self.assertTrue(summary.typed_errors)
            self.assertIsNotNone(summary.discovery_task_manifest)
            task_path, task_manifest = _load_task_manifest(self, root)
            self.assertEqual(task_manifest["save_status"], "failed")
            self.assertEqual(
                Path(root, summary.discovery_task_manifest["path"]),
                task_path,
            )

    def test_collect_rejects_authoritative_source_subset(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            downloader = CbexPhysicalAssetDownloader(
                html_root=root,
                page_size=2,
                list_sources=[self.authoritative_house_source],
                run_id="run-cbex-source-subset",
            )
            downloader._api_with_retry = AsyncMock(  # type: ignore[method-assign]
                return_value=_cbex_response(
                    page=1,
                    rows=[_cbex_row("A")],
                    total_pages=1,
                    total_records=1,
                )
            )
            summary = DownloadSummary()
            candidates: list = []

            completed = asyncio.run(
                downloader._collect_list_sources_with_evidence(
                    context=SimpleNamespace(),
                    page=SimpleNamespace(),
                    outdir=root,
                    summary=summary,
                    seen=set(),
                    cands=candidates,
                    start=None,
                    end=None,
                )
            )

            self.assertFalse(completed)
            self.assertEqual(candidates, [])
            self.assertTrue(summary.typed_errors)
            self.assertIsNotNone(summary.discovery_task_manifest)
            _, task_manifest = _load_task_manifest(self, root)
            self.assertEqual(task_manifest["save_status"], "failed")
            downloader._api_with_retry.assert_not_awaited()

    def test_complete_query_archives_original_jsonp_pages_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            responses = [
                _cbex_response(
                    page=1,
                    rows=[_cbex_row("A"), _cbex_row("B")],
                    total_pages=2,
                    total_records=3,
                ),
                _cbex_response(
                    page=2,
                    rows=[_cbex_row("C")],
                    total_pages=2,
                    total_records=3,
                ),
            ]
            downloader = self._downloader(root)
            downloader._api_with_retry = AsyncMock(side_effect=responses)  # type: ignore[method-assign]

            summary, candidates = self._collect(downloader)

            self.assertEqual(summary.pages_requested, 2)
            self.assertEqual([candidate.code for candidate in candidates], ["CBEX-A", "CBEX-B", "CBEX-C"])
            self.assertIsNotNone(summary.discovery_task_manifest)
            self.assertTrue(Path(root, summary.discovery_task_manifest["path"]).is_file())
            manifest_path, manifest = _load_only_manifest(self, root)
            self.assertEqual(manifest["save_status"], "complete")
            self.assertEqual(manifest["termination_reason"], "declared_pages_exhausted")
            self.assertEqual(manifest["declared_total_pages"], 2)
            self.assertEqual(manifest["declared_total_items"], 3)
            self.assertEqual(manifest["observed_row_count"], 3)

            raw_paths = sorted(manifest_path.parent.glob("page_*.raw.jsonp"))
            self.assertEqual([path.read_bytes() for path in raw_paths], [r.raw_bytes for r in responses])
            self.assertTrue(raw_paths[0].read_bytes().startswith(b"jQuery_page_1("))
            first_sidecar = json.loads(
                (manifest_path.parent / "page_000001.meta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(first_sidecar["source_url"], responses[0].source_url)
            self.assertEqual(first_sidecar["final_url"], responses[0].final_url)
            self.assertEqual(first_sidecar["http_status"], 200)
            self.assertEqual(first_sidecar["parse_status"], "complete")
            _, task_manifest = _load_task_manifest(self, root)
            self.assertEqual(task_manifest["save_status"], "complete")
            self.assertEqual(task_manifest["expected_query_ids"], ["house-SW-house"])
            self.assertEqual(
                [entry["query_id"] for entry in task_manifest["queries"]],
                task_manifest["expected_query_ids"],
            )
            self.assertEqual(task_manifest["candidate_count"], 3)
            self.assertEqual(
                len(task_manifest["candidate_fingerprints"]),
                len(summary.candidate_entries),
            )

    def test_task_manifest_covers_every_configured_source_and_stages_candidates(self) -> None:
        cases = {
            "complete": [
                _cbex_response(
                    page=1,
                    rows=[_cbex_row("A")],
                    total_pages=1,
                    total_records=1,
                ),
                _cbex_response(
                    page=1,
                    rows=[_cbex_row("B")],
                    total_pages=1,
                    total_records=1,
                    asset_type="transport",
                ),
                _cbex_response(
                    page=1,
                    rows=[_cbex_row("C")],
                    total_pages=1,
                    total_records=1,
                    asset_type="equipment",
                ),
            ],
            "second-source-request-failed": [
                _cbex_response(
                    page=1,
                    rows=[_cbex_row("A")],
                    total_pages=1,
                    total_records=1,
                ),
                RuntimeError("second source stopped"),
            ],
        }
        for label, outcomes in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root:
                downloader = CbexPhysicalAssetDownloader(
                    html_root=root,
                    page_size=2,
                    list_sources=[
                        self.authoritative_house_source,
                        self.authoritative_transport_source,
                        self.authoritative_equipment_source,
                    ],
                    run_id="run-cbex-task",
                )
                downloader._api_with_retry = AsyncMock(side_effect=outcomes)  # type: ignore[method-assign]
                summary = DownloadSummary()
                candidates: list = []
                with patch(
                    "peap.downloaders.cbex_physical.asyncio.sleep",
                    new=AsyncMock(),
                ):
                    completed = asyncio.run(
                        downloader._collect_list_sources_with_evidence(
                            context=SimpleNamespace(),
                            page=SimpleNamespace(),
                            outdir=root,
                            summary=summary,
                            seen=set(),
                            cands=candidates,
                            start=None,
                            end=None,
                        )
                    )

                _, task_manifest = _load_task_manifest(self, root)
                self.assertEqual(
                    task_manifest["expected_query_ids"],
                    [
                        "房屋土地-SW-house",
                        "交通运输工具-SW-transport",
                        "设备-SW-equipment",
                    ],
                )
                if label == "complete":
                    self.assertTrue(completed)
                    self.assertEqual(task_manifest["save_status"], "complete")
                    self.assertEqual(
                        [candidate.code for candidate in candidates],
                        ["CBEX-A", "CBEX-B", "CBEX-C"],
                    )
                else:
                    self.assertFalse(completed)
                    self.assertEqual(task_manifest["save_status"], "failed")
                    self.assertEqual(candidates, [])
                    self.assertEqual(summary.candidate_entries, [])
                    self.assertEqual(summary.listed_items, 0)

    def test_declared_record_mismatch_fails_closed_without_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            downloader = self._downloader(root)
            downloader._api_with_retry = AsyncMock(  # type: ignore[method-assign]
                side_effect=[
                    _cbex_response(
                        page=1,
                        rows=[_cbex_row("A"), _cbex_row("B")],
                        total_pages=2,
                        total_records=3,
                    ),
                    _cbex_response(
                        page=2,
                        rows=[],
                        total_pages=2,
                        total_records=3,
                    ),
                ]
            )

            summary, candidates = self._collect(downloader)

            self.assertEqual(candidates, [])
            self.assertTrue(summary.typed_errors)
            _, manifest = _load_only_manifest(self, root)
            self.assertEqual(manifest["save_status"], "failed")
            self.assertIn("declared total", manifest["failure_details"]["error"])
            _, task_manifest = _load_task_manifest(self, root)
            self.assertEqual(task_manifest["save_status"], "failed")
            self.assertIsNotNone(summary.discovery_task_manifest)

    def test_changed_total_page_and_repeated_page_fail_closed(self) -> None:
        cases = {
            "changed-total-page": [
                _cbex_response(
                    page=1,
                    rows=[_cbex_row("A"), _cbex_row("B")],
                    total_pages=2,
                    total_records=4,
                ),
                _cbex_response(
                    page=2,
                    rows=[_cbex_row("C"), _cbex_row("D")],
                    total_pages=3,
                    total_records=4,
                ),
            ],
            "repeated-page": [
                _cbex_response(
                    page=1,
                    rows=[_cbex_row("A"), _cbex_row("B")],
                    total_pages=2,
                    total_records=4,
                ),
                _cbex_response(
                    page=2,
                    rows=[_cbex_row("A"), _cbex_row("B")],
                    total_pages=2,
                    total_records=4,
                ),
            ],
            "partially-overlapping-page": [
                _cbex_response(
                    page=1,
                    rows=[_cbex_row("A"), _cbex_row("B")],
                    total_pages=2,
                    total_records=4,
                ),
                _cbex_response(
                    page=2,
                    rows=[_cbex_row("B"), _cbex_row("C")],
                    total_pages=2,
                    total_records=4,
                ),
            ],
        }
        for label, responses in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root:
                downloader = self._downloader(root)
                downloader._api_with_retry = AsyncMock(side_effect=responses)  # type: ignore[method-assign]

                summary, candidates = self._collect(downloader)

                self.assertEqual(candidates, [])
                self.assertTrue(summary.typed_errors)
                _, manifest = _load_only_manifest(self, root)
                self.assertEqual(manifest["save_status"], "failed")

    def test_request_and_structure_errors_write_failed_manifests(self) -> None:
        structure_response = _http_text(
            b'jQuery_bad({"data":{"data":{},"totalPage":1,"total":0}});\n',
            source_url=(
                "https://www.cbex.com.cn/onss-api/jsonp/project/search"
                "?callback=jQuery_bad&fromPage=1&pageSize=2"
                "&businessType=SW&assetType=house"
            ),
        )
        cases = {
            "request": RuntimeError("network stopped"),
            "structure": structure_response,
        }
        for label, outcome in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root:
                downloader = self._downloader(root)
                downloader._api_with_retry = AsyncMock(  # type: ignore[method-assign]
                    side_effect=outcome if isinstance(outcome, Exception) else None,
                    return_value=None if isinstance(outcome, Exception) else outcome,
                )

                summary, candidates = self._collect(downloader)

                self.assertEqual(candidates, [])
                self.assertTrue(summary.typed_errors)
                manifest_path, manifest = _load_only_manifest(self, root)
                self.assertEqual(manifest["save_status"], "failed")
                if label == "structure":
                    self.assertEqual(
                        (manifest_path.parent / "page_000001.raw.jsonp").read_bytes(),
                        structure_response.raw_bytes,
                    )
                    sidecar = json.loads(
                        (manifest_path.parent / "page_000001.meta.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    self.assertEqual(sidecar["parse_status"], "failed")


if __name__ == "__main__":
    unittest.main()
