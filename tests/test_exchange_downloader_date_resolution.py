from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import unittest
import urllib.parse
from dataclasses import replace
from unittest.mock import patch

from peap.downloaders.common import HttpFetchedText
from peap.downloaders.deal_cbex import (
    CbexDealCapitalIncreaseDownloader,
    CbexDealEquityTransferDownloader,
    CbexDealPhysicalAssetDownloader,
)
from peap.downloaders.deal_cquae import (
    ChongqingDealCapitalIncreaseDownloader,
    ChongqingDealEquityTransferDownloader,
    ChongqingDealPhysicalAssetDownloader,
)
from peap.downloaders.deal_sse import (
    ShanghaiDealCapitalIncreaseDownloader,
    ShanghaiDealEquityTransferDownloader,
    ShanghaiDealPhysicalAssetDownloader,
)
from peap.downloaders.deal_sse import (
    _extract_total_pages as _extract_sse_deal_total_pages,
)
from peap.downloaders.deal_tpre import (
    TianjinDealCapitalIncreaseDownloader,
    TianjinDealEquityTransferDownloader,
    TianjinDealPhysicalAssetDownloader,
)

IMPUTED_SUFFIX = "成交日期缺失，按采集日填列"


class _JsonResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _HtmlResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        charset: str = "utf-8",
        url: str = "https://example.test/final",
        status: int = 200,
    ) -> None:
        self._payload = payload
        self._charset = charset
        self._url = url
        self.status = status
        self.headers = self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._payload

    def get_content_charset(self):
        return self._charset

    def geturl(self) -> str:
        return self._url


def _fetched_html(html: str, source_url: str) -> HttpFetchedText:
    return HttpFetchedText(
        html,
        source_url=source_url,
        final_url=source_url,
        http_status=200,
    )


class DealDateResolutionContractTest(unittest.TestCase):
    def _assert_common_metadata(self, entry: dict[str, object], *, source_id: str, business_id: str) -> None:
        self.assertEqual(entry.get("record_family"), "deal")
        self.assertEqual(entry.get("source_id"), source_id)
        self.assertEqual(entry.get("business_id"), business_id)
        self.assertIsInstance(entry.get("business_label"), str)
        self.assertTrue(str(entry.get("business_label") or ""))
        self.assertIsInstance(entry.get("source_url"), str)
        self.assertTrue(str(entry.get("source_url") or ""))
        self.assertRegex(str(entry.get("collection_date") or ""), r"^\d{4}-\d{2}-\d{2}$")
        deal_date = str(entry.get("deal_date") or "")
        if deal_date:
            self.assertRegex(deal_date, r"^\d{4}-\d{2}-\d{2}$")
        else:
            self.assertEqual(entry.get("deal_date_basis"), "collection_date")
            self.assertTrue(bool(entry.get("deal_date_is_imputed")))
        self.assertIn(entry.get("deal_date_basis"), {"CJRQ", "contractSignTime", "deal_date", "collection_date"})
        self.assertIn(entry.get("deal_date_is_imputed"), {True, False})

    def _run_sse_list_only_case(self, cls, *, business_id: str, fclass: str) -> dict[str, object]:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _JsonResponse(
                {
                    "code": 200,
                    "data": [
                        {
                            "XMID": f"SSE-{business_id}",
                            "XMBH": f"{fclass}2026SH0001",
                            "XMMC": f"{business_id} fixture",
                            "FCLASS": fclass,
                            "CJRQ": "2026-04-10",
                        }
                    ],
                    "extra": 1,
                }
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = cls(html_root=tmp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                summary = downloader.run(start_date="2026-04-10", end_date="2026-04-10", list_only=True)
        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(captured["url"], "https://www.suaee.com/si/notice/getDealNoticeList")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"]["FCLASS"], fclass)
        self.assertEqual(captured["body"]["pageNo"], 1)
        self.assertEqual(captured["body"]["pageSize"], downloader.page_size)
        self.assertEqual(captured["body"]["XMLX"], "")
        self.assertEqual(captured["body"]["XMBH"], "")
        self.assertEqual(captured["body"]["XMMC"], "")
        entry = summary.candidate_entries[0]
        self._assert_common_metadata(entry, source_id="sse", business_id=business_id)
        self.assertEqual(entry["deal_date"], "2026-04-10")
        self.assertEqual(entry["deal_date_basis"], "CJRQ")
        return entry

    def _run_tpre_list_only_case(
        self,
        cls,
        *,
        business_id: str,
        expected_list_path: str,
        expected_biz_type: str,
        expected_date: str,
        expected_basis: str,
        row_date_field: str | None,
    ) -> dict[str, object]:
        captured: dict[str, object] = {}
        row = {
            "id": f"TPRE-{business_id}",
            "projectCode": f"T32026TJ{len(business_id):04d}",
            "projectName": f"{business_id} fixture",
        }
        if row_date_field:
            row[row_date_field] = expected_date

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            return _JsonResponse({"code": 0, "data": {"total": 1, "records": [row]}})

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = cls(html_root=tmp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                summary = downloader.run(start_date=expected_date, end_date=expected_date, list_only=True)
        self.assertEqual(summary.detail_candidates, 1)
        parsed = urllib.parse.urlsplit(str(captured["url"]))
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(parsed.path, expected_list_path)
        self.assertEqual(query["bizType"], expected_biz_type)
        self.assertEqual(query["current"], "1")
        self.assertEqual(query["size"], str(downloader.page_size))
        entry = summary.candidate_entries[0]
        self._assert_common_metadata(entry, source_id="tpre", business_id=business_id)
        if expected_basis == "collection_date" and row_date_field is None:
            self.assertEqual(entry["deal_date"], "")
            self.assertEqual(entry["collection_date"], expected_date)
        else:
            self.assertEqual(entry["deal_date"], expected_date)
        self.assertEqual(entry["deal_date_basis"], expected_basis)
        return entry

    def _run_cquae_list_only_case(self, cls, *, business_id: str, suffix: str, expected_list_path: str) -> dict[str, object]:
        html = f"""
        <html><body>
          <table>
            <tr>
              <td><a href="/CquaeNews/cjgs/Detail/{suffix}.html">{business_id} fixture</a></td>
              <td>2026-04-12</td>
            </tr>
          </table>
        </body></html>
        """
        captured_urls: list[str] = []

        def fake_fetch_list_html(url: str) -> str:
            captured_urls.append(url)
            return html

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = cls(html_root=tmp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_fetch_list_html", side_effect=fake_fetch_list_html),
            ):
                summary = downloader.run(start_date="2026-04-12", end_date="2026-04-12", list_only=True)
        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(captured_urls, [urllib.parse.urljoin("https://www.cquae.com", expected_list_path)])
        entry = summary.candidate_entries[0]
        self._assert_common_metadata(entry, source_id="cquae", business_id=business_id)
        self.assertEqual(entry["deal_date"], "2026-04-12")
        self.assertEqual(entry["deal_date_basis"], "deal_date")
        return entry

    def _run_cbex_list_only_case(self, cls, *, business_id: str, detail_path: str, expected_list_path: str) -> dict[str, object]:
        html = f"""
        <html><body>
          <ul>
            <li>
              <a href="{detail_path}">{business_id} fixture</a>
              <span>2026-04-13</span>
            </li>
          </ul>
        </body></html>
        """
        captured_urls: list[str] = []

        def fake_fetch_list_html(url: str) -> str:
            captured_urls.append(url)
            return html

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = cls(html_root=tmp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_fetch_list_html", side_effect=fake_fetch_list_html),
            ):
                summary = downloader.run(start_date="2026-04-13", end_date="2026-04-13", list_only=True)
        self.assertEqual(summary.detail_candidates, 1)
        expected_root = urllib.parse.urljoin("https://www.cbex.com.cn", expected_list_path)
        self.assertEqual(captured_urls[0], expected_root)
        self.assertTrue(all(url.startswith(expected_root) for url in captured_urls))
        entry = summary.candidate_entries[0]
        self._assert_common_metadata(entry, source_id="cbex", business_id=business_id)
        self.assertEqual(entry["deal_date"], "2026-04-13")
        self.assertEqual(entry["deal_date_basis"], "deal_date")
        return entry

    def test_all_exchange_business_combinations_support_fixture_driven_list_only_metadata(self) -> None:
        cases = [
            ("sse-equity", lambda: self._run_sse_list_only_case(ShanghaiDealEquityTransferDownloader, business_id="deal_equity_transfer", fclass="GQ")),
            ("sse-physical", lambda: self._run_sse_list_only_case(ShanghaiDealPhysicalAssetDownloader, business_id="deal_physical_asset", fclass="SW")),
            ("sse-capital", lambda: self._run_sse_list_only_case(ShanghaiDealCapitalIncreaseDownloader, business_id="deal_capital_increase", fclass="1C")),
            ("tpre-equity", lambda: self._run_tpre_list_only_case(TianjinDealEquityTransferDownloader, business_id="deal_equity_transfer", expected_list_path="/transaction/biz/transaction-management/anmuas/result-notice/page", expected_biz_type="PROPERTY_RIGHT_TRANSFER", expected_date="2026-04-11", expected_basis="contractSignTime", row_date_field="contractSignTime")),
            ("tpre-physical", lambda: self._run_tpre_list_only_case(TianjinDealPhysicalAssetDownloader, business_id="deal_physical_asset", expected_list_path="/transaction/biz/transaction-management/anmuas/result-notice/page", expected_biz_type="ENTERPRISE_ASSETS", expected_date="2026-04-11", expected_basis="contractSignTime", row_date_field="contractSignTime")),
            ("tpre-capital", lambda: self._run_tpre_list_only_case(TianjinDealCapitalIncreaseDownloader, business_id="deal_capital_increase", expected_list_path="/transaction/biz/increase/transaction/anmuas/result-notice/page", expected_biz_type="ENTERPRISE_CAPITAL_INCREASE", expected_date="2026-04-20", expected_basis="collection_date", row_date_field=None)),
            ("cquae-equity", lambda: self._run_cquae_list_only_case(ChongqingDealEquityTransferDownloader, business_id="deal_equity_transfer", suffix="cq-equity", expected_list_path="/CquaeNews/cjgs/List.cshtml")),
            ("cquae-physical", lambda: self._run_cquae_list_only_case(ChongqingDealPhysicalAssetDownloader, business_id="deal_physical_asset", suffix="cq-physical", expected_list_path="/CquaeNews/cjgs/List.cshtml?type=3")),
            ("cquae-capital", lambda: self._run_cquae_list_only_case(ChongqingDealCapitalIncreaseDownloader, business_id="deal_capital_increase", suffix="cq-capital", expected_list_path="/CquaeNews/cjgs/List.cshtml?type=1")),
            ("cbex-equity", lambda: self._run_cbex_list_only_case(CbexDealEquityTransferDownloader, business_id="deal_equity_transfer", detail_path="/xm/cqzr/2026/04/13/equity.html", expected_list_path="/xm/cqzr/cjjggs/")),
            ("cbex-physical", lambda: self._run_cbex_list_only_case(CbexDealPhysicalAssetDownloader, business_id="deal_physical_asset", detail_path="/xm/zczr/2026/04/13/physical.html", expected_list_path="/xm/zczr/cjjggs/")),
            ("cbex-capital", lambda: self._run_cbex_list_only_case(CbexDealCapitalIncreaseDownloader, business_id="deal_capital_increase", detail_path="/xm/qyzz/2026/04/13/capital.html", expected_list_path="/xm/qyzz/cjjggs/")),
        ]
        for label, run_case in cases:
            with self.subTest(label=label):
                entry = run_case()
                self.assertTrue(str(entry["source_url"]).startswith("http"))
                self.assertEqual(entry["record_family"], "deal")

    def test_sse_list_request_body_uses_full_web_contract_fields(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _JsonResponse({"code": 200, "data": [], "extra": 1})

        downloader = ShanghaiDealEquityTransferDownloader(html_root="/tmp/test", page_size=37)
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            downloader._query_list_page(page_index=3)

        self.assertEqual(captured["url"], "https://www.suaee.com/si/notice/getDealNoticeList")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(
            captured["body"],
            {
                "pageNo": 3,
                "pageSize": 37,
                "FCLASS": "GQ",
                "XMLX": "",
                "XMBH": "",
                "XMMC": "",
            },
        )

    def test_sse_deal_list_paginates_when_official_total_is_nested_in_extra(self) -> None:
        calls: list[int] = []

        def row(index: int) -> dict[str, object]:
            return {
                "XMID": f"SSE-PAGE-{index}",
                "XMBH": f"GR2026SH1000{index:03d}",
                "XMMC": f"SSE deal paginated fixture {index}",
                "FCLASS": "SW",
                "CJRQ": "2026-06-04",
            }

        def fake_urlopen(request, timeout=None):
            body = json.loads(request.data.decode("utf-8"))
            page_no = int(body["pageNo"])
            calls.append(page_no)
            records = [row(1), row(2)] if page_no == 1 else [row(3)] if page_no == 2 else []
            return _JsonResponse({"code": 200, "data": records, "extra": {"total": 3}})

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiDealPhysicalAssetDownloader(html_root=tmp_dir, page_size=2)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 6, 10)),
                patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                summary = downloader.run(start_date="2026-06-01", end_date="2026-06-10", list_only=True)

        self.assertEqual(calls, [1, 2])
        self.assertEqual(summary.pages_requested, 2)
        self.assertEqual(summary.listed_items, 3)
        self.assertEqual(summary.detail_candidates, 3)
        self.assertEqual(summary.list_unaccounted, 0)

    def test_sse_deal_authoritative_page_count_overrides_smaller_max_pages(self) -> None:
        calls: list[int] = []

        def row(index: int) -> dict[str, object]:
            return {
                "XMID": f"SSE-OVERRIDE-{index}",
                "XMBH": f"GR2026SH2000{index:03d}",
                "XMMC": f"SSE deal override fixture {index}",
                "FCLASS": "SW",
                "CJRQ": "2026-06-04",
            }

        def query(*, page_index: int):
            calls.append(page_index)
            records = [row(1), row(2)] if page_index == 1 else [row(3)]
            return {
                "code": 200,
                "data": records,
                "extra": {"total": 3, "pageCount": 2},
            }

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiDealPhysicalAssetDownloader(
                html_root=tmp_dir,
                page_size=2,
                max_pages=1,
                run_id="run-sse-deal-override",
            )
            with patch.object(downloader, "_query_list_page", side_effect=query):
                summary = downloader.run(start_date=None, end_date=None, list_only=True)

            self.assertEqual(calls, [1, 2])
            self.assertEqual(summary.pages_requested, 2)
            self.assertEqual(summary.detail_candidates, 3)
            self.assertEqual(summary.typed_errors, [])
            self.assertEqual(
                summary.list_page_observations,
                [
                    {
                        "status": "max_pages_overridden",
                        "query_id": "deal-notice-list",
                        "requested_max_pages": 1,
                        "declared_total_pages": 2,
                        "reason": "authoritative_declared_pages_require_complete_discovery",
                    }
                ],
            )
            self.assertIsNotNone(summary.discovery_task_manifest)
            task_manifest_path = os.path.join(
                tmp_dir,
                str(summary.discovery_task_manifest["path"]),
            )
            query_manifest_path = os.path.join(
                os.path.dirname(task_manifest_path),
                "deal-notice-list",
                "manifest.json",
            )
            with open(query_manifest_path, encoding="utf-8") as handle:
                query_manifest = json.load(handle)
            self.assertEqual(query_manifest["save_status"], "complete")
            self.assertEqual(query_manifest["archived_page_count"], 2)
            self.assertEqual(
                query_manifest["termination_facts"],
                {
                    "requested_max_pages": 1,
                    "effective_max_pages": 2,
                    "max_pages_overridden": True,
                },
            )

    def test_sse_deal_scalar_extra_is_total_records_not_total_pages(self) -> None:
        self.assertEqual(
            _extract_sse_deal_total_pages(
                {"code": 200, "data": [], "extra": 1280},
                page_size=20,
                default_page=1,
            ),
            64,
        )

    def test_sse_detail_request_body_uses_xmid_fclass_and_skip_date_check(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _JsonResponse({"code": 200, "data": {"CJRQ": "2026-04-10"}})

        downloader = ShanghaiDealPhysicalAssetDownloader(html_root="/tmp/test")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            downloader._query_detail_payload(xmid="XM123")

        self.assertEqual(captured["url"], "https://www.suaee.com/si/notice/getNoticeDetail")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"], {"XMID": "XM123", "FCLASS": "SW", "skipDateCheck": True})

    def test_cbex_deal_fetch_detail_uses_strict_fallback_decoding(self) -> None:
        html = "<html><body>成交日期：2026年3月31日</body></html>"
        downloader = CbexDealEquityTransferDownloader(html_root="/tmp/test")

        with patch(
            "urllib.request.urlopen",
            return_value=_HtmlResponse(html.encode("gb18030"), charset="utf-8"),
        ):
            self.assertEqual(downloader._fetch_detail_html("https://www.cbex.com.cn/detail.html"), html)

        with patch(
            "urllib.request.urlopen",
            return_value=_HtmlResponse(b"\x80\x80\x80", charset="utf-8"),
        ):
            with self.assertRaises(UnicodeDecodeError):
                downloader._fetch_detail_html("https://www.cbex.com.cn/broken.html")

    def test_cquae_deal_fetch_list_uses_strict_fallback_decoding(self) -> None:
        html = "<html><body>成交公告 2026年3月31日</body></html>"
        downloader = ChongqingDealEquityTransferDownloader(html_root="/tmp/test")

        with patch(
            "urllib.request.urlopen",
            return_value=_HtmlResponse(html.encode("gb18030"), charset="utf-8"),
        ):
            self.assertEqual(downloader._fetch_list_html("https://www.cquae.com/list.html"), html)

        with patch(
            "urllib.request.urlopen",
            return_value=_HtmlResponse(b"\x80\x80\x80", charset="utf-8"),
        ):
            with self.assertRaises(UnicodeDecodeError):
                downloader._fetch_list_html("https://www.cquae.com/broken.html")

    def test_detail_deal_date_recomputes_archive_path_candidate_date_and_callback_date(self) -> None:
        cases = [
            (
                "sse",
                ShanghaiDealEquityTransferDownloader,
                {
                    "notice_id": "D2-SSE-001",
                    "xmid": "XM-D2-SSE-001",
                    "project_code": "D2SSE001",
                    "project_name": "SSE 详情成交日项目",
                    "source_url": "https://www.suaee.com/si/notice/getNoticeDetail?XMID=XM-D2-SSE-001",
                    "collection_date": "2026-04-20",
                    "deal_date_basis": "collection_date",
                    "deal_date_is_imputed": True,
                    "row": {"XMID": "XM-D2-SSE-001", "XMBH": "D2SSE001", "XMMC": "SSE 详情成交日项目"},
                },
                "_query_detail_payload",
                {"data": {"CJRQ": "2026-03-31"}},
            ),
            (
                "tpre",
                TianjinDealEquityTransferDownloader,
                {
                    "notice_id": "D2-TPRE-001",
                    "project_code": "D2TPRE001",
                    "project_name": "TPRE 详情成交日项目",
                    "source_url": "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement?id=D2-TPRE-001",
                    "collection_date": "2026-04-20",
                    "deal_date_basis": "collection_date",
                    "deal_date_is_imputed": True,
                    "row": {"id": "D2-TPRE-001", "projectCode": "D2TPRE001", "projectName": "TPRE 详情成交日项目"},
                },
                "_query_detail_payload",
                {"data": {"contractSignTime": "2026-03-31"}},
            ),
            (
                "cbex",
                CbexDealEquityTransferDownloader,
                {
                    "candidate_id": "D2CBEX001",
                    "project_code": "D2CBEX001",
                    "project_name": "CBEX 详情成交日项目",
                    "source_url": "https://www.cbex.com.cn/xm/cqzr/2026/04/20/d2cbex001.html",
                    "collection_date": "2026-04-20",
                    "deal_date_basis": "collection_date",
                    "deal_date_is_imputed": True,
                    "row": {"project_code": "D2CBEX001", "project_name": "CBEX 详情成交日项目"},
                },
                "_fetch_detail_html",
                _fetched_html(
                    "<html><body><p>成交日期：2026-03-31</p></body></html>",
                    "https://www.cbex.com.cn/xm/cqzr/2026/04/20/d2cbex001.html",
                ),
            ),
            (
                "cquae",
                ChongqingDealEquityTransferDownloader,
                {
                    "candidate_id": "D2CQUAE001",
                    "project_code": "D2CQUAE001",
                    "project_name": "CQUAE 详情成交日项目",
                    "source_url": "https://www.cquae.com/CquaeNews/cjgs/Detail/d2cquae001.html",
                    "collection_date": "2026-04-20",
                    "deal_date_basis": "collection_date",
                    "deal_date_is_imputed": True,
                    "row": {"project_code": "D2CQUAE001", "project_name": "CQUAE 详情成交日项目"},
                },
                "_fetch_detail_html",
                _fetched_html(
                    "<html><body><p>成交日期：2026-03-31</p></body></html>",
                    "https://www.cquae.com/CquaeNews/cjgs/Detail/d2cquae001.html",
                ),
            ),
        ]

        for source_id, downloader_cls, candidate, detail_method, detail_value in cases:
            with self.subTest(source_id=source_id):
                callbacks: list[dict[str, object]] = []
                with tempfile.TemporaryDirectory() as tmp_dir:
                    downloader = downloader_cls(html_root=tmp_dir, item_saved_callback=callbacks.append)
                    if source_id == "tpre":
                        downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
                    with (
                        patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                        patch.object(downloader, detail_method, return_value=detail_value),
                        patch.object(
                            downloader,
                            "_fetch_rendered_detail_html",
                            return_value=_fetched_html(
                                "<html><body>成交公告 项目编号 D2SSE001 成交日期 2026-03-31 SSE 详情成交日项目</body></html>",
                                str(candidate["source_url"]),
                            ),
                            create=True,
                        ),
                    ):
                        summary = downloader.run(
                            start_date="2026-03-01",
                            end_date="2026-04-30",
                            prefetched_candidates=[candidate],
                        )

                    self.assertEqual(summary.saved, 1)
                    self.assertEqual(summary.skipped_by_detail_date, 0)
                    self.assertEqual(summary.candidate_dates, ["2026-03-31"])
                    self.assertEqual(len(callbacks), 1)
                    callback = callbacks[0]
                    self.assertEqual(callback["listing_date"], "2026-03-31")
                    self.assertIn("2026年3月", str(callback["source_file"]))
                    self.assertTrue(os.path.isfile(str(callback["source_file"])))
                    self.assertFalse(os.path.isdir(os.path.join(tmp_dir, "2026年4月")))

    def test_detail_deal_date_outside_requested_range_is_skipped_after_detail_fetch(self) -> None:
        callbacks: list[dict[str, object]] = []
        candidate = {
            "notice_id": "D2-SSE-OUTSIDE",
            "xmid": "XM-D2-SSE-OUTSIDE",
            "project_code": "D2SSEOUT",
            "project_name": "详情成交日越界项目",
            "source_url": "https://www.suaee.com/si/notice/getNoticeDetail?XMID=XM-D2-SSE-OUTSIDE",
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {"XMID": "XM-D2-SSE-OUTSIDE", "XMBH": "D2SSEOUT", "XMMC": "详情成交日越界项目"},
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiDealEquityTransferDownloader(html_root=tmp_dir, item_saved_callback=callbacks.append)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", return_value={"data": {"CJRQ": "2026-03-31"}}),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value="<html><body>成交公告 详情成交日越界项目 D2SSEOUT 成交日期 2026-03-31</body></html>",
                ),
            ):
                summary = downloader.run(
                    start_date="2026-04-01",
                    end_date="2026-04-30",
                    prefetched_candidates=[candidate],
                )

        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(summary.detail_fetched, 1)
        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.skipped_by_detail_date, 1)
        self.assertEqual(summary.candidate_dates, ["2026-03-31"])
        self.assertEqual(callbacks, [])

    def test_sse_physical_callback_failure_is_counted_as_save_failure(self) -> None:
        def callback_boom(_item: dict[str, object]) -> None:
            raise RuntimeError("callback boom")

        candidate = {
            "notice_id": "CALLBACK-SSE-001",
            "xmid": "XM-CALLBACK-SSE-001",
            "project_code": "CALLBACKSSE001",
            "project_name": "SSE callback failure fixture",
            "source_url": "https://www.suaee.com/si/notice/getNoticeDetail?XMID=XM-CALLBACK-SSE-001",
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "XMID": "XM-CALLBACK-SSE-001",
                "XMBH": "CALLBACKSSE001",
                "XMMC": "SSE callback failure fixture",
            },
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiDealPhysicalAssetDownloader(html_root=tmp_dir, item_saved_callback=callback_boom)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", return_value={"data": {"CJRQ": "2026-04-20"}}),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_fetched_html(
                        "<html><body>成交公告 SSE callback failure fixture "
                        "CALLBACKSSE001 成交日期 2026-04-20</body></html>",
                        str(candidate["source_url"]),
                    ),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-04-20",
                    end_date="2026-04-20",
                    prefetched_candidates=[candidate],
                )
            sidecars = []
            for root, _, files in os.walk(tmp_dir):
                sidecars.extend(os.path.join(root, name) for name in files if name.endswith(".json"))

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(summary.detail_unaccounted, 0)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "save")
        self.assertIn("callback boom", summary.typed_errors[0].raw_reason)
        self.assertEqual(sidecars, [])

    def test_sse_physical_callback_failure_with_sidecar_cleanup_failure_does_not_leave_resume_marker(self) -> None:
        def callback_boom(_item: dict[str, object]) -> None:
            raise RuntimeError("callback boom")

        candidate = {
            "notice_id": "CALLBACK-SSE-002",
            "xmid": "XM-CALLBACK-SSE-002",
            "project_code": "CALLBACKSSE002",
            "project_name": "SSE callback cleanup failure fixture",
            "source_url": "https://www.suaee.com/si/notice/getNoticeDetail?XMID=XM-CALLBACK-SSE-002",
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "XMID": "XM-CALLBACK-SSE-002",
                "XMBH": "CALLBACKSSE002",
                "XMMC": "SSE callback cleanup failure fixture",
            },
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiDealPhysicalAssetDownloader(html_root=tmp_dir, item_saved_callback=callback_boom)
            original_remove = os.remove

            def fail_sidecar_cleanup(path: str) -> None:
                if path.endswith(".json"):
                    raise OSError("sidecar cleanup blocked")
                original_remove(path)

            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_query_detail_payload", return_value={"data": {"CJRQ": "2026-04-20"}}),
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_fetched_html(
                        "<html><body>成交公告 SSE callback cleanup failure fixture "
                        "CALLBACKSSE002 成交日期 2026-04-20</body></html>",
                        str(candidate["source_url"]),
                    ),
                ),
                patch("peap.downloaders.deal_sse.os.remove", side_effect=fail_sidecar_cleanup),
            ):
                summary = downloader.run(
                    start_date="2026-04-20",
                    end_date="2026-04-20",
                    prefetched_candidates=[candidate],
                )
            sidecars = []
            for root, _, files in os.walk(tmp_dir):
                sidecars.extend(os.path.join(root, name) for name in files if name.endswith(".json"))
            sidecar_payloads = [json.load(open(path, encoding="utf-8")) for path in sidecars]

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "save")
        self.assertIn("sidecar cleanup blocked", summary.typed_errors[0].raw_reason)
        self.assertEqual([payload.get("save_status") for payload in sidecar_payloads], ["failed"])

    def test_cquae_equity_callback_failure_is_counted_as_save_failure(self) -> None:
        def callback_boom(_item: dict[str, object]) -> None:
            raise RuntimeError("callback boom")

        candidate = {
            "candidate_id": "CALLBACKCQUAE001",
            "project_code": "CALLBACKCQUAE001",
            "project_name": "CQUAE callback failure fixture",
            "source_url": "https://www.cquae.com/CquaeNews/cjgs/Detail/callback-cquae-001.html",
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "project_code": "CALLBACKCQUAE001",
                "project_name": "CQUAE callback failure fixture",
            },
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ChongqingDealEquityTransferDownloader(html_root=tmp_dir, item_saved_callback=callback_boom)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_fetch_detail_html",
                    return_value=_fetched_html(
                        "<html><body>成交公告 CQUAE callback failure fixture "
                        "CALLBACKCQUAE001 成交日期：2026-04-20</body></html>",
                        str(candidate["source_url"]),
                    ),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-04-20",
                    end_date="2026-04-20",
                    prefetched_candidates=[candidate],
                )
            sidecars = []
            for root, _, files in os.walk(tmp_dir):
                sidecars.extend(os.path.join(root, name) for name in files if name.endswith(".json"))

        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(summary.downloaded_this_run, set())
        self.assertEqual(summary.detail_unaccounted, 0)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "save")
        self.assertIn("callback boom", summary.typed_errors[0].raw_reason)
        self.assertEqual(sidecars, [])

    def test_tpre_capital_download_merges_paginated_transferee_details_into_snapshot_payload(self) -> None:
        saved_payload: dict[str, object] = {}

        def capture_sidecar(*, json_path, metadata, detail_payload, detail_url=None, detail_payload_error="", **kwargs):
            saved_payload["detail_payload"] = detail_payload

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=tmp_dir, page_size=2)
            downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_query_list_page",
                    return_value={
                        "code": 0,
                        "data": {
                            "total": 1,
                            "records": [
                                {
                                    "id": "CAP-1",
                                    "projectCode": "Z32026TJ0001",
                                    "projectName": "增资成交项目",
                                    "projectLink": (
                                        "https://trade.tpre.cn/transaction-view/data/common/"
                                        "transaction-announcement?id=CAP-1"
                                    ),
                                }
                            ],
                        },
                    },
                ),
                patch.object(downloader, "_query_detail_payload", return_value={"data": {"projectCode": "Z32026TJ0001"}}),
                patch.object(
                    downloader,
                    "_query_capital_transferee_details_page",
                    side_effect=[
                        {"data": {"total": 3, "records": [{"investor": "A"}, {"investor": "B"}]}},
                        {"data": {"total": 3, "records": [{"investor": "C"}]}},
                    ],
                    create=True,
                ) as detail_pages,
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_fetched_html(
                        "<html><body>成交公告 增资成交项目 Z32026TJ0001</body></html>",
                        "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement?id=CAP-1",
                    ),
                ),
                patch.object(downloader, "_write_sidecar_json", side_effect=capture_sidecar),
            ):
                summary = downloader.run(start_date="2026-04-20", end_date="2026-04-20", list_only=False)

        self.assertEqual(summary.saved, 1)
        self.assertEqual([call.kwargs["current"] for call in detail_pages.call_args_list], [1, 2])
        self.assertEqual([call.kwargs["project_code"] for call in detail_pages.call_args_list], ["Z32026TJ0001", "Z32026TJ0001"])
        self.assertEqual(
            saved_payload["detail_payload"]["transferee_details"],
            [{"investor": "A"}, {"investor": "B"}, {"investor": "C"}],
        )

    def test_tpre_capital_transferee_pagination_stops_at_repeated_page_without_duplicate_records(self) -> None:
        calls: list[int] = []

        def repeated_page(*, project_code: str, current: int, size: int) -> dict[str, object]:
            self.assertEqual(project_code, "Z32026TJ0002")
            self.assertEqual(size, 2)
            calls.append(current)
            return {"data": {"total": 10, "records": [{"investor": "A"}, {"investor": "B"}]}}

        downloader = TianjinDealCapitalIncreaseDownloader(html_root="/tmp/test", page_size=2, max_detail_pages=20)
        with patch.object(downloader, "_query_capital_transferee_details_page", side_effect=repeated_page):
            records = downloader._collect_capital_transferee_details(project_code="Z32026TJ0002")

        self.assertEqual(calls, [1, 2])
        self.assertEqual(records, [{"investor": "A"}, {"investor": "B"}])

    def test_tpre_capital_transferee_pagination_respects_max_detail_pages(self) -> None:
        calls: list[int] = []

        def paginated_page(*, project_code: str, current: int, size: int) -> dict[str, object]:
            self.assertEqual(project_code, "Z32026TJ0003")
            calls.append(current)
            return {"data": {"total": 10, "records": [{"investor": f"P{current}"}]}}

        downloader = TianjinDealCapitalIncreaseDownloader(html_root="/tmp/test", page_size=1, max_detail_pages=2)
        with patch.object(downloader, "_query_capital_transferee_details_page", side_effect=paginated_page):
            records = downloader._collect_capital_transferee_details(project_code="Z32026TJ0003")

        self.assertEqual(calls, [1, 2])
        self.assertEqual(records, [{"investor": "P1"}, {"investor": "P2"}])

    def test_tpre_capital_download_uses_project_code_from_detail_payload_before_candidate_fallback(self) -> None:
        saved_payload: dict[str, object] = {}

        def capture_sidecar(*, json_path, metadata, detail_payload, detail_url=None, detail_payload_error="", **kwargs):
            saved_payload["detail_payload"] = detail_payload

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=tmp_dir, page_size=2)
            downloader.query = replace(downloader.query, detail_api_endpoint="/fake/detail")
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_query_list_page",
                    return_value={
                        "code": 0,
                        "data": {
                            "total": 1,
                            "records": [
                                {
                                    "id": "CAP-NOTICE-1",
                                    "projectName": "列表缺少项目编号的增资成交项目",
                                    "projectLink": (
                                        "https://trade.tpre.cn/transaction-view/data/common/"
                                        "transaction-announcement?id=CAP-NOTICE-1"
                                    ),
                                }
                            ],
                        },
                    },
                ),
                patch.object(downloader, "_query_detail_payload", return_value={"data": {"projectCode": "Z32026TJREAL"}}),
                patch.object(
                    downloader,
                    "_query_capital_transferee_details_page",
                    return_value={"data": {"total": 1, "records": [{"investor": "REAL"}]}},
                    create=True,
                ) as detail_pages,
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_fetched_html(
                        "<html><body>成交公告 列表缺少项目编号的增资成交项目 Z32026TJREAL</body></html>",
                        "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement?id=CAP-DETAIL-CODE-1",
                    ),
                ),
                patch.object(downloader, "_write_sidecar_json", side_effect=capture_sidecar),
            ):
                summary = downloader.run(start_date="2026-04-20", end_date="2026-04-20", list_only=False)

        self.assertEqual(summary.saved, 1)
        self.assertEqual([call.kwargs["project_code"] for call in detail_pages.call_args_list], ["Z32026TJREAL"])
        self.assertEqual(saved_payload["detail_payload"]["transferee_details"], [{"investor": "REAL"}])
        self.assertNotIn("transferee_details_warning", saved_payload["detail_payload"])

    def test_tpre_capital_download_warns_and_skips_transferee_pages_when_project_code_missing(self) -> None:
        saved_payload: dict[str, object] = {}

        def capture_sidecar(*, json_path, metadata, detail_payload, detail_url=None, detail_payload_error="", **kwargs):
            saved_payload["detail_payload"] = detail_payload

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=tmp_dir, page_size=2)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_query_list_page",
                    return_value={
                        "code": 0,
                        "data": {
                            "total": 1,
                            "records": [
                                {
                                    "id": "CAP-NOTICE-2",
                                    "projectName": "缺少项目编号的增资成交项目",
                                    "projectLink": (
                                        "https://trade.tpre.cn/transaction-view/data/common/"
                                        "transaction-announcement?id=CAP-NOTICE-2"
                                    ),
                                }
                            ],
                        },
                    },
                ),
                patch.object(downloader, "_query_detail_payload", return_value={"data": {"projectName": "缺少项目编号"}}),
                patch.object(
                    downloader,
                    "_query_capital_transferee_details_page",
                    return_value={"data": {"total": 1, "records": [{"investor": "SHOULD_NOT_FETCH"}]}},
                    create=True,
                ) as detail_pages,
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_fetched_html(
                        "<html><body>成交公告 缺少项目编号的增资成交项目</body></html>",
                        "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement?id=CAP-MISSING-CODE-1",
                    ),
                ),
                patch.object(downloader, "_write_sidecar_json", side_effect=capture_sidecar),
            ):
                summary = downloader.run(start_date="2026-04-20", end_date="2026-04-20", list_only=False)

        self.assertEqual(summary.saved, 1)
        self.assertEqual(detail_pages.call_count, 0)
        self.assertEqual(saved_payload["detail_payload"]["transferee_details"], [])
        self.assertEqual(saved_payload["detail_payload"]["transferee_details_warning"], "missing-project-code")

    def test_tpre_capital_download_without_detail_json_api_still_fetches_transferee_pages(self) -> None:
        saved_payload: dict[str, object] = {}
        saved_payload_error = ""

        def capture_sidecar(*, json_path, metadata, detail_payload, detail_url=None, detail_payload_error="", **kwargs):
            saved_payload["detail_payload"] = detail_payload
            nonlocal saved_payload_error
            saved_payload_error = detail_payload_error

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=tmp_dir, page_size=2)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_query_list_page",
                    return_value={
                        "code": 0,
                        "data": {
                            "total": 1,
                            "records": [
                                {
                                    "id": "CAP-NO-DETAIL-API-1",
                                    "projectCode": "Z32026TJ0099",
                                    "projectName": "无详情接口仍抓受让方",
                                    "projectLink": (
                                        "https://trade.tpre.cn/transaction-view/data/common/"
                                        "transaction-announcement?id=CAP-NO-DETAIL-API-1"
                                    ),
                                }
                            ],
                        },
                    },
                ),
                patch.object(
                    downloader,
                    "_query_capital_transferee_details_page",
                    return_value={"data": {"total": 1, "records": [{"investor": "ONLY"}]}},
                ) as detail_pages,
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_fetched_html(
                        "<html><body>成交公告 无详情接口仍抓受让方 Z32026TJ0099</body></html>",
                        "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement?id=CAP-NO-DETAIL-API-1",
                    ),
                ),
                patch.object(downloader, "_write_sidecar_json", side_effect=capture_sidecar),
            ):
                summary = downloader.run(start_date="2026-04-20", end_date="2026-04-20", list_only=False)

        self.assertEqual(summary.saved, 1)
        self.assertEqual(detail_pages.call_count, 1)
        self.assertEqual(
            detail_pages.call_args.kwargs,
            {"project_code": "Z32026TJ0099", "current": 1, "size": 2},
        )
        self.assertEqual(saved_payload["detail_payload"]["transferee_details"], [{"investor": "ONLY"}])
        self.assertEqual(saved_payload_error, "")

    def test_tpre_capital_download_uses_project_code_when_notice_id_is_same_project_code(self) -> None:
        saved_payload: dict[str, object] = {}

        def capture_sidecar(*, json_path, metadata, detail_payload, detail_url=None, detail_payload_error="", **kwargs):
            saved_payload["detail_payload"] = detail_payload

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=tmp_dir, page_size=2)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_query_list_page",
                    return_value={
                        "code": 0,
                        "data": {
                            "total": 1,
                            "records": [
                                {
                                    "id": "G62024TJ1000011",
                                    "projectCode": "G62024TJ1000011",
                                    "projectName": "项目编号即公告 id 的增资成交项目",
                                    "projectLink": (
                                        "https://trade.tpre.cn/transaction-view/data/common/"
                                        "transaction-announcement?id=G62024TJ1000011"
                                    ),
                                }
                            ],
                        },
                    },
                ),
                patch.object(
                    downloader,
                    "_query_capital_transferee_details_page",
                    return_value={"data": {"total": 1, "records": [{"investorName": "天津投资方"}]}},
                ) as detail_pages,
                patch.object(
                    downloader,
                    "_fetch_rendered_detail_html",
                    return_value=_fetched_html(
                        "<html><body>成交公告 项目编号即公告 id 的增资成交项目 G62024TJ1000011</body></html>",
                        "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement?id=G62024TJ1000011",
                    ),
                ),
                patch.object(downloader, "_write_sidecar_json", side_effect=capture_sidecar),
            ):
                summary = downloader.run(start_date="2026-04-20", end_date="2026-04-20", list_only=False)

        self.assertEqual(summary.saved, 1)
        self.assertEqual(detail_pages.call_count, 1)
        self.assertEqual(
            detail_pages.call_args.kwargs,
            {"project_code": "G62024TJ1000011", "current": 1, "size": 2},
        )
        self.assertEqual(saved_payload["detail_payload"]["transferee_details"], [{"investorName": "天津投资方"}])
        self.assertNotIn("transferee_details_warning", saved_payload["detail_payload"])

    def test_tpre_capital_prefetched_api_source_url_is_replaced_by_renderable_transaction_page(self) -> None:
        callbacks: list[dict[str, object]] = []
        candidate = {
            "notice_id": "CAP-URL-001",
            "project_code": "Z32026TJ0199",
            "project_name": "可渲染增资成交页",
            "source_url": (
                "https://trade.tpre.cn/transaction/biz/increase/transaction/transferee/anmuas/"
                "result-notice/details?id=CAP-URL-001"
            ),
            "collection_date": "2026-04-20",
            "deal_date_basis": "collection_date",
            "deal_date_is_imputed": True,
            "row": {
                "id": "CAP-URL-001",
                "projectCode": "Z32026TJ0199",
                "projectName": "可渲染增资成交页",
            },
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=tmp_dir, item_saved_callback=callbacks.append)
            with patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)):
                summary = downloader.run(
                    start_date="2026-04-20",
                    end_date="2026-04-20",
                    list_only=True,
                    prefetched_candidates=[candidate],
                )

        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(
            summary.candidate_entries[0]["source_url"],
            "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement?id=CAP-URL-001",
        )

    def test_tpre_equity_detail_payload_fetch_without_json_endpoint_does_not_use_spa_route_as_api(self) -> None:
        downloader = TianjinDealEquityTransferDownloader(html_root="/tmp/test")

        with self.assertRaisesRegex(RuntimeError, "detail-api-unavailable"):
            downloader._query_detail_payload(notice_id="TPRE-EQ-001")

    def test_cquae_excludes_protocol_transfer_at_table_level_before_row_processing(self) -> None:
        html = """
        <html><body>
          <table>
            <caption>协议转让成交结果</caption>
            <tr>
              <td><a href="/CquaeNews/cjgs/Detail/protocol.html">看似可下载的协议转让结果</a></td>
              <td>2026-04-12</td>
            </tr>
          </table>
          <table>
            <tr>
              <td><a href="/CquaeNews/cjgs/Detail/valid.html">正常成交公告</a></td>
              <td>2026-04-12</td>
            </tr>
          </table>
        </body></html>
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ChongqingDealEquityTransferDownloader(html_root=tmp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_fetch_list_html", return_value=html),
            ):
                summary = downloader.run(start_date="2026-04-12", end_date="2026-04-12", list_only=True)

        self.assertEqual(summary.detail_candidates, 1)
        self.assertTrue(str(summary.candidate_entries[0]["source_url"]).endswith("/valid.html"))

    def test_cquae_excludes_protocol_transfer_from_adjacent_heading_container_title_and_first_row_cells(self) -> None:
        html = """
        <html><body>
          <h2>协议转让成交结果</h2>
          <table>
            <tr>
              <td><a href="/CquaeNews/cjgs/Detail/by-heading.html">相邻标题标记的成交结果</a></td>
              <td>2026-04-12</td>
            </tr>
          </table>
          <section>
            <div class="title">协议转让成交结果</div>
            <table>
              <tr>
                <td><a href="/CquaeNews/cjgs/Detail/by-container-title.html">容器标题标记的成交结果</a></td>
                <td>2026-04-12</td>
              </tr>
            </table>
          </section>
          <table>
            <tr><td>协议转让成交结果</td><td>日期</td></tr>
            <tr>
              <td><a href="/CquaeNews/cjgs/Detail/by-first-row.html">首行单元格标记的成交结果</a></td>
              <td>2026-04-12</td>
            </tr>
          </table>
          <table>
            <tr>
              <td><a href="/CquaeNews/cjgs/Detail/valid-after-exclusions.html">正常成交公告</a></td>
              <td>2026-04-12</td>
            </tr>
          </table>
        </body></html>
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ChongqingDealEquityTransferDownloader(html_root=tmp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(downloader, "_fetch_list_html", return_value=html),
            ):
                summary = downloader.run(start_date="2026-04-12", end_date="2026-04-12", list_only=True)

        self.assertEqual(summary.detail_candidates, 1)
        self.assertTrue(str(summary.candidate_entries[0]["source_url"]).endswith("/valid-after-exclusions.html"))

    def test_sse_capital_increase_keeps_missing_deal_date_separate_from_collection_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShanghaiDealCapitalIncreaseDownloader(html_root=tmp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_query_list_page",
                    return_value={
                        "code": 200,
                        "data": [
                            {
                                "GGID": "N001",
                                "XMBH": "ZZ2026SH0001",
                                "XMMC": "某增资扩股成交公告",
                                "FCLASS": "1C",
                            }
                        ],
                        "extra": 1,
                    },
                ),
            ):
                summary = downloader.run(
                    start_date="2026-04-20",
                    end_date="2026-04-20",
                    list_only=True,
                )

        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(len(summary.candidate_entries), 1)
        entry = summary.candidate_entries[0]
        self.assertEqual(entry.get("record_family"), "deal")
        self.assertEqual(entry.get("source_id"), "sse")
        self.assertEqual(entry.get("business_id"), "deal_capital_increase")
        self.assertEqual(entry.get("collection_date"), "2026-04-20")
        self.assertEqual(entry.get("deal_date"), "")
        self.assertEqual(entry.get("deal_date_basis"), "collection_date")
        self.assertTrue(bool(entry.get("deal_date_is_imputed")))
        self.assertEqual(entry.get("remark_suffix"), IMPUTED_SUFFIX)
        self.assertTrue(str(entry.get("deal_date_remark_suffix") or "").endswith(IMPUTED_SUFFIX))
        self.assertIn("/jyxx.html#/xxggDetail", str(entry.get("source_url")))

    def test_tpre_equity_prefers_contract_sign_time_as_deal_date_basis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = TianjinDealEquityTransferDownloader(html_root=tmp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 3, 10)),
                patch.object(
                    downloader,
                    "_query_list_page",
                    return_value={
                        "code": 0,
                        "data": {
                            "total": 1,
                            "records": [
                                {
                                    "id": "T001",
                                    "projectCode": "G32026TJ1000001",
                                    "projectName": "某股权转让成交项目",
                                    "contractSignTime": "2026-03-06",
                                }
                            ],
                        },
                    },
                ),
            ):
                summary = downloader.run(
                    start_date="2026-03-06",
                    end_date="2026-03-06",
                    list_only=True,
                )

        self.assertEqual(summary.detail_candidates, 1)
        entry = summary.candidate_entries[0]
        self._assert_common_metadata(entry, source_id="tpre", business_id="deal_equity_transfer")
        self.assertEqual(entry.get("deal_date"), "2026-03-06")
        self.assertEqual(entry.get("deal_date_basis"), "contractSignTime")
        self.assertFalse(bool(entry.get("deal_date_is_imputed")))

    def test_cquae_list_only_respects_path_whitelist_and_excludes_protocol_or_leasing_rows(self) -> None:
        html = """
        <html><body>
          <table>
            <tr><th>标题</th></tr>
            <tr>
              <td><a href="/CquaeNews/cjgs/Detail/1001.html">某股权成交公告</a></td>
              <td>2026-03-11</td>
            </tr>
            <tr>
              <td><a href="/CquaeNews/cjgs/Detail/1002.html">某租赁成交公告</a></td>
              <td>2026-03-11</td>
            </tr>
            <tr>
              <td><a href="/outside/not-allowed.html">某协议成交公告</a></td>
              <td>2026-03-11</td>
            </tr>
          </table>
        </body></html>
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ChongqingDealEquityTransferDownloader(html_root=tmp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 3, 12)),
                patch.object(downloader, "_fetch_list_html", return_value=html),
            ):
                summary = downloader.run(
                    start_date="2026-03-11",
                    end_date="2026-03-11",
                    list_only=True,
                )

        self.assertEqual(summary.detail_candidates, 1)
        entry = summary.candidate_entries[0]
        self._assert_common_metadata(entry, source_id="cquae", business_id="deal_equity_transfer")
        self.assertTrue(str(entry.get("source_url")).startswith("https://www.cquae.com/CquaeNews/cjgs/"))
        self.assertEqual(entry.get("deal_date"), "2026-03-11")
        self.assertEqual(entry.get("deal_date_basis"), "deal_date")

    def test_cbex_capital_list_only_keeps_missing_deal_date_separate_from_collection_date(self) -> None:
        html = """
        <html><body>
          <ul>
            <li>
              <a href="/xm/qyzz/2026/04/01/1001.html">某增资扩股成交公告</a>
              <span class="code">ZZ2026BJ0001</span>
            </li>
          </ul>
        </body></html>
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = CbexDealCapitalIncreaseDownloader(html_root=tmp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 21)),
                patch.object(downloader, "_fetch_list_html", return_value=html),
            ):
                summary = downloader.run(
                    start_date="2026-04-21",
                    end_date="2026-04-21",
                    list_only=True,
                )

        self.assertEqual(summary.detail_candidates, 1)
        entry = summary.candidate_entries[0]
        self._assert_common_metadata(entry, source_id="cbex", business_id="deal_capital_increase")
        self.assertEqual(entry.get("collection_date"), "2026-04-21")
        self.assertEqual(entry.get("deal_date"), "")
        self.assertEqual(entry.get("deal_date_basis"), "collection_date")
        self.assertTrue(bool(entry.get("deal_date_is_imputed")))
        self.assertEqual(entry.get("remark_suffix"), IMPUTED_SUFFIX)
        self.assertTrue(str(entry.get("deal_date_remark_suffix") or "").endswith(IMPUTED_SUFFIX))

    def test_missing_real_deal_date_is_kept_for_detail_resolution_before_collection_date_filter(self) -> None:
        cases = [
            (
                "sse",
                "deal_capital_increase",
                ShanghaiDealCapitalIncreaseDownloader,
                lambda downloader: patch.object(
                    downloader,
                    "_query_list_page",
                    return_value={
                        "code": 200,
                        "data": [
                            {
                                "GGID": "MISS-SSE-001",
                                "XMBH": "ZZ2025SH0001",
                                "XMMC": "缺成交日期的上交所增资成交公告",
                                "FCLASS": "1C",
                            }
                        ],
                        "extra": 1,
                    },
                ),
            ),
            (
                "tpre",
                "deal_capital_increase",
                TianjinDealCapitalIncreaseDownloader,
                lambda downloader: patch.object(
                    downloader,
                    "_query_list_page",
                    return_value={
                        "code": 0,
                        "data": {
                            "total": 1,
                            "records": [
                                {
                                    "id": "MISS-TPRE-001",
                                    "projectCode": "Z32025TJ0001",
                                    "projectName": "缺成交日期的天津增资成交公告",
                                }
                            ],
                        },
                    },
                ),
            ),
            (
                "cquae",
                "deal_capital_increase",
                ChongqingDealCapitalIncreaseDownloader,
                lambda downloader: patch.object(
                    downloader,
                    "_fetch_list_html",
                    return_value="""
                    <html><body>
                      <table>
                        <tr>
                          <td><a href="/CquaeNews/cjgs/Detail/missing-date.html">缺成交日期的重庆增资成交公告</a></td>
                          <td class="code">ZZ2025CQ0001</td>
                        </tr>
                      </table>
                    </body></html>
                    """,
                ),
            ),
            (
                "cbex",
                "deal_capital_increase",
                CbexDealCapitalIncreaseDownloader,
                lambda downloader: patch.object(
                    downloader,
                    "_fetch_list_html",
                    return_value="""
                    <html><body>
                      <ul>
                        <li>
                          <a href="/xm/qyzz/notice/missing-date.html">缺成交日期的北交所增资成交公告</a>
                          <span class="code">ZZ2025BJ0001</span>
                        </li>
                      </ul>
                    </body></html>
                    """,
                ),
            ),
        ]

        for source_id, _business_id, downloader_cls, patch_list_source in cases:
            with self.subTest(source_id=source_id), tempfile.TemporaryDirectory() as tmp_dir:
                downloader = downloader_cls(html_root=tmp_dir)
                with (
                    patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                    patch_list_source(downloader),
                ):
                    summary = downloader.run(
                        start_date="2025-01-01",
                        end_date="2025-12-31",
                        list_only=True,
                    )

                self.assertGreaterEqual(summary.listed_items, 1)
                self.assertEqual(summary.skipped_by_list_date, 0)
                self.assertEqual(summary.detail_candidates, 1)
                self.assertEqual(summary.candidate_dates, ["2026-04-20"])
                entry = summary.candidate_entries[0]
                self._assert_common_metadata(entry, source_id=source_id, business_id=_business_id)
                self.assertEqual(entry.get("collection_date"), "2026-04-20")
                self.assertEqual(entry.get("deal_date"), "")
                self.assertEqual(entry.get("deal_date_basis"), "collection_date")
                self.assertTrue(bool(entry.get("deal_date_is_imputed")))

    def test_tpre_capital_missing_detail_deal_date_is_skipped_by_collection_date_outside_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = TianjinDealCapitalIncreaseDownloader(html_root=tmp_dir)
            with (
                patch.object(downloader, "_collection_date", return_value=dt.date(2026, 4, 20)),
                patch.object(
                    downloader,
                    "_query_list_page",
                    return_value={
                        "code": 0,
                        "data": {
                            "total": 1,
                            "records": [
                                {
                                    "id": "MISS-TPRE-DETAIL-001",
                                    "projectCode": "G62026TJ0001",
                                    "projectName": "详情仍缺成交日期的天津增资成交公告",
                                }
                            ],
                        },
                    },
                ),
                patch.object(downloader, "_query_detail_payload", return_value={"data": {}}),
                patch.object(downloader, "_merge_capital_transferee_details", return_value={"data": {}}),
                patch.object(downloader, "_fetch_rendered_detail_html") as fetch_rendered,
            ):
                summary = downloader.run(
                    start_date="2025-01-01",
                    end_date="2025-12-31",
                )

        self.assertEqual(summary.listed_items, 1)
        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(summary.skipped_by_list_date, 0)
        self.assertEqual(summary.skipped_by_detail_date, 1)
        self.assertEqual(summary.saved, 0)
        fetch_rendered.assert_not_called()


if __name__ == "__main__":
    unittest.main()
