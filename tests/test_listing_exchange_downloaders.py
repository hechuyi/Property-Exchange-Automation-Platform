from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from peap.constants import TYPE_CAPITAL_INCREASE, TYPE_EQUITY_TRANSFER
from peap.downloaders import (
    CbexCapitalIncreaseDownloader,
    CbexEquityTransferDownloader,
    CbexPhysicalAssetDownloader,
    CbexPreDisclosureDownloader,
    ChongqingCapitalIncreaseDownloader,
    ChongqingEquityTransferDownloader,
    ChongqingPhysicalAssetDownloader,
    ChongqingPreDisclosureDownloader,
    GuangdongCapitalIncreaseDownloader,
    GuangdongEquityTransferDownloader,
    ShandongCapitalIncreaseDownloader,
    ShandongEquityTransferDownloader,
    ShanghaiCapitalIncreaseDownloader,
    ShanghaiEquityTransferDownloader,
    ShanghaiPhysicalAssetDownloader,
    ShanghaiPreDisclosureDownloader,
    ShenzhenCapitalIncreaseDownloader,
    ShenzhenEquityTransferDownloader,
    TianjinCapitalIncreaseDownloader,
    TianjinEquityTransferDownloader,
    TianjinPhysicalAssetDownloader,
    TianjinPreDisclosureDownloader,
)
from peap.downloaders.common import HttpFetchedText
from peap.output_mapping import map_standard_to_excel_payload
from peap.parsing import parse_file
from peap_core.source_business_contract import get_source_business_requirement

RAW_DETAIL_HTML = (
    "<html><head><script src='/assets/app.js'></script></head>"
    "<body><img src='/assets/logo.png'><div>rendered detail</div>"
    "<div>SD001 山东股权 SDCI001 山东增资 GD001 广东股权 GDCI001 广东增资</div>"
    "<div>SZGQ001 深圳股权 SZZZ001 深圳增资 SDREL001 relative SZ001 深圳项目1 SZ002 深圳项目2</div>"
    "<div>SZPW001 playwright SZSAVEJSON save json SZCLOSEOK close ok</div>"
    "<div>SZCB001 callback SZPF001 prefetched</div></body></html>"
)


def _fetched_list_text(text: str, *, page_num: int = 1) -> HttpFetchedText:
    url = f"https://exchange.example/list?page={page_num}"
    return HttpFetchedText(
        text,
        source_url=url,
        final_url=url,
        http_status=200,
        raw_bytes=text.encode("utf-8"),
    )


class _FakePage:
    def __init__(self, html: str = RAW_DETAIL_HTML) -> None:
        self.html = html
        self.urls: list[str] = []
        self.evaluated: list[str] = []
        self.closed = False

    async def goto(self, url: str, **_kwargs):
        self.urls.append(url)
        return SimpleNamespace(status=200)

    async def wait_for_selector(self, *_args, **_kwargs) -> None:
        return None

    async def wait_for_timeout(self, *_args, **_kwargs) -> None:
        return None

    async def evaluate(self, script: str) -> int:
        self.evaluated.append(script)
        return 100

    async def content(self) -> str:
        return self.html

    async def close(self) -> None:
        self.closed = True


class _ConcurrentPage(_FakePage):
    active = 0
    max_active = 0

    async def goto(self, url: str, **_kwargs):
        self.urls.append(url)
        type(self).active += 1
        type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            await asyncio.sleep(0.02)
        finally:
            type(self).active -= 1
        return SimpleNamespace(status=200)


class _ChangingPage(_FakePage):
    def __init__(self, html_samples: list[str]) -> None:
        super().__init__(html="")
        self.html_samples = list(html_samples)
        self.content_calls = 0

    async def content(self) -> str:
        index = min(self.content_calls, len(self.html_samples) - 1)
        self.content_calls += 1
        return self.html_samples[index]


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.closed = False

    async def new_page(self) -> _FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class _SequenceBrowser:
    def __init__(self, pages: list[_FakePage]) -> None:
        self.pages = pages
        self.closed = False
        self.page_index = 0

    async def new_page(self) -> _FakePage:
        page = self.pages[self.page_index]
        self.page_index += 1
        return page

    async def close(self) -> None:
        self.closed = True


class _FailingPage(_FakePage):
    async def goto(self, url: str, **_kwargs) -> None:
        await super().goto(url, **_kwargs)
        raise RuntimeError("detail boom")


class _FakeAsyncPlaywrightContext:
    def __init__(self, playwright: object) -> None:
        self.playwright = playwright
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> object:
        self.entered = True
        return self.playwright

    async def __aexit__(self, *_args) -> None:
        self.exited = True


class ListingExchangeDownloaderEvidenceTest(unittest.TestCase):
    def _logger(self) -> object:
        return SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )

    def _run_with_response(
        self,
        downloader,
        response: dict[str, object],
        *,
        list_only: bool = False,
    ) -> tuple[Path | None, list[dict[str, object]], _FakePage, str]:
        page = _FakePage()
        fake_context = _FakeAsyncPlaywrightContext(object())
        requests: list[dict[str, object]] = []
        raw_response = json.dumps(response, ensure_ascii=False, separators=(",", ":"))

        def fake_fetch_list_page(
            *, page_num: int, payload: dict[str, object]
        ) -> HttpFetchedText:
            requests.append(payload)
            return _fetched_list_text(raw_response, page_num=page_num)

        with (
            patch.object(downloader, "_fetch_list_page", side_effect=fake_fetch_list_page),
            patch(
                "peap.downloaders.listing_exchanges.async_playwright",
                return_value=fake_context,
            ),
            patch(
                "peap.downloaders.listing_exchanges.launch_chromium_browser",
                return_value=_FakeBrowser(page),
            ),
        ):
            summary = downloader.run(
                start_date="2026-01-01",
                end_date="2026-01-31",
                list_only=list_only,
            )

        self.assertEqual(summary.saved, 0 if list_only else 1)
        html_paths = [p for p in Path(downloader.html_root).rglob("*.html")]
        self.assertEqual(len(html_paths), 0 if list_only else 1)
        return html_paths[0] if html_paths else None, requests, page, raw_response

    def test_allowed_listing_scopes_save_raw_browser_html_and_run_scoped_evidence(self) -> None:
        cases = [
            (
                "shandong:listing:equity_transfer",
                ShandongEquityTransferDownloader,
                TYPE_EQUITY_TRANSFER,
                {
                    "data": {
                        "list": [
                            {
                                "id": "sd-1",
                                "projectCode": "SD001",
                                "title": "山东股权",
                                "publishDate": "2026-01-10",
                                "url": "https://sd.example/detail/sd-1",
                            }
                        ]
                    }
                },
            ),
            (
                "shandong:listing:capital_increase",
                ShandongCapitalIncreaseDownloader,
                TYPE_CAPITAL_INCREASE,
                {
                    "data": {
                        "list": [
                            {
                                "id": "sd-ci-1",
                                "projectCode": "SDCI001",
                                "title": "山东增资",
                                "publishDate": "2026-01-10",
                                "url": "https://sd.example/detail/sd-ci-1",
                            }
                        ]
                    }
                },
            ),
            (
                "guangdong:listing:equity_transfer",
                GuangdongEquityTransferDownloader,
                TYPE_EQUITY_TRANSFER,
                {
                    "data": [
                        {
                            "id": "gd-1",
                            "projectCode": "GD001",
                            "title": "广东股权",
                            "publishDate": "2026-01-10",
                            "url": "https://new.gduaee.com/xmzx.html#/equityDetail?XMID=gd-1",
                            "FCLASS": "GQ",
                            "CQLSGX": "GQ100101",
                        }
                    ]
                },
            ),
            (
                "guangdong:listing:capital_increase",
                GuangdongCapitalIncreaseDownloader,
                TYPE_CAPITAL_INCREASE,
                {
                    "data": [
                        {
                            "id": "gd-ci-1",
                            "projectCode": "GDCI001",
                            "title": "广东增资",
                            "publishDate": "2026-01-10",
                            "url": (
                                "https://new.gduaee.com/xmzx.html#/capital_increaseDetail"
                                "?XMID=gd-ci-1"
                            ),
                            "FCLASS": "1C",
                            "CQLSGX": "1C100301",
                        }
                    ]
                },
            ),
            (
                "shenzhen:listing:equity_transfer",
                ShenzhenEquityTransferDownloader,
                TYPE_EQUITY_TRANSFER,
                {
                    "data": {
                        "content": [
                            {
                                "id": "sz-gq-1",
                                "projectCode": "SZGQ001",
                                "title": "深圳股权",
                                "publishDate": "2026-01-10",
                                "url": "https://sz.example/detail/gq",
                                "projectSubjection": "地方",
                            }
                        ]
                    }
                },
            ),
            (
                "shenzhen:listing:capital_increase",
                ShenzhenCapitalIncreaseDownloader,
                TYPE_CAPITAL_INCREASE,
                {
                    "data": {
                        "content": [
                            {
                                "id": "sz-zz-1",
                                "projectCode": "SZZZ001",
                                "title": "深圳增资",
                                "publishDate": "2026-01-10",
                                "url": "https://sz.example/detail/zz",
                                "projectSubjection": "地方",
                            }
                        ]
                    }
                },
            ),
        ]

        for task_id, downloader_cls, output_type, response in cases:
            with self.subTest(task_id=task_id), tempfile.TemporaryDirectory() as tmp_dir:
                downloader = downloader_cls(
                    html_root=tmp_dir,
                    page_size=20,
                    max_pages=1,
                    concurrency=1,
                    resume=False,
                    save_json=True,
                    logger=self._logger(),
                    output_type=output_type,
                    run_id="job-123",
                )

                html_path, _requests, page, raw_response = self._run_with_response(
                    downloader, response
                )
                self.assertIsNotNone(html_path)
                assert html_path is not None

                self.assertEqual(html_path.read_text(encoding="utf-8"), RAW_DETAIL_HTML)
                self.assertIn("window.scrollTo", "\n".join(page.evaluated))
                self.assertFalse(Path(str(html_path.with_suffix("")) + "_files").exists())

                sidecars = sorted(
                    p for p in html_path.parent.glob(f"{html_path.stem}.*") if p != html_path
                )
                self.assertEqual([p.suffix for p in sidecars], [".json"])
                metadata = json.loads(sidecars[0].read_text(encoding="utf-8"))
                self.assertEqual(metadata["archive_path"], str(html_path))
                self.assertEqual(metadata["task_id"], task_id)

                evidence_dir = Path(tmp_dir) / "_evidence" / "job-123" / task_id.replace(":", "__")
                list_evidence = evidence_dir / "list_page_1.json"
                self.assertTrue(list_evidence.is_file())
                self.assertEqual(list_evidence.read_text(encoding="utf-8"), raw_response)
                self.assertTrue((evidence_dir / "candidates.jsonl").is_file())
                decisions = [
                    json.loads(line)
                    for line in (evidence_dir / "candidates.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
                self.assertEqual([row["decision"] for row in decisions], ["accepted"])
                self.assertEqual(decisions[0]["archive_path"], str(html_path))

    def test_exchange_list_request_contracts_are_encoded(self) -> None:
        cases = [
            (
                ShandongEquityTransferDownloader,
                {"path": "yqgq", "systemSource": "CQY"},
                {"path": "zrzz", "channelIds": None},
            ),
            (
                ShandongCapitalIncreaseDownloader,
                {"path": "zrzz", "systemSource": "CQY"},
                {"path": "yqgq", "channelIds": None},
            ),
            (
                GuangdongEquityTransferDownloader,
                {"pageNo": 1, "IN_CQLSGX": "'GQ100101'", "QueryFlag": 4, "SQJL": 1},
                {"pageNum": None, "businessType": "capital_increase"},
            ),
            (
                GuangdongCapitalIncreaseDownloader,
                {
                    "pageNo": 1,
                    "IN_CQLSGX": "'1C100301'",
                    "QueryFlag": 4,
                    "SQJL": 1,
                },
                {"pageNum": None, "businessType": "capital_increase"},
            ),
            (
                ShenzhenEquityTransferDownloader,
                {"channelIds": object, "targetColumnIds": object, "projectSubjections": ["央属"]},
                {},
            ),
            (
                ShenzhenCapitalIncreaseDownloader,
                {"channelIds": object, "targetColumnIds": object, "projectSubjections": ["央属"]},
                {},
            ),
        ]

        for downloader_cls, expected, forbidden in cases:
            with (
                self.subTest(cls=downloader_cls.__name__),
                tempfile.TemporaryDirectory() as tmp_dir,
            ):
                downloader = downloader_cls(
                    html_root=tmp_dir,
                    page_size=20,
                    max_pages=1,
                    concurrency=1,
                    resume=False,
                    save_json=False,
                    logger=self._logger(),
                    run_id="job-params",
                )
                response = {"data": {"list": []}}
                _html_path, requests, _page, _raw_response = self._run_with_response(
                    downloader,
                    response,
                    list_only=True,
                )
                self.assertEqual(len(requests), 1)
                payload = requests[0]
                for key, value in expected.items():
                    if value is object:
                        self.assertIn(key, payload)
                        self.assertTrue(payload[key])
                    else:
                        self.assertEqual(payload.get(key), value)
            for key, value in forbidden.items():
                if value is None:
                    self.assertNotIn(key, payload)
                else:
                    self.assertNotEqual(payload.get(key), value)

    def test_list_fetch_retries_transient_network_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-list-retry",
            )
            response = {
                "data": {
                    "content": [
                        {
                            "contentId": 2418408,
                            "objectId": "a8fccddb830045c0979f789051486bf4",
                            "channelId": 3961,
                            "isObject": 1,
                            "projectNo": "SZRETRY001",
                            "title": "深圳重试项目",
                            "registerFrom": "2026-05-12 00:00:00",
                        }
                    ]
                }
            }
            post_json = Mock(
                side_effect=[
                    urllib.error.URLError(TimeoutError("handshake timed out")),
                    _fetched_list_text(json.dumps(response, ensure_ascii=False)),
                ]
            )

            with patch.object(downloader, "_post_json", post_json), patch("time.sleep") as sleep:
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-12-31",
                    list_only=True,
                )

            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(len(summary.typed_errors), 0)
            self.assertEqual(post_json.call_count, 2)
            sleep.assert_called_once()

    def test_list_fetch_falls_back_to_browser_request_after_network_retries_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-list-browser-fallback",
            )
            downloader.list_fetch_attempts = 2
            response = {
                "data": {
                    "content": [
                        {
                            "contentId": 2418408,
                            "objectId": "a8fccddb830045c0979f789051486bf4",
                            "channelId": 3961,
                            "isObject": 1,
                            "projectNo": "SZFALLBACK001",
                            "title": "深圳 fallback 项目",
                            "registerFrom": "2026-05-12 00:00:00",
                        }
                    ]
                }
            }
            post_json = Mock(
                side_effect=urllib.error.URLError(
                    TimeoutError("_ssl.c:989: The handshake operation timed out")
                )
            )
            browser_request = Mock(
                return_value=_fetched_list_text(json.dumps(response, ensure_ascii=False))
            )

            with (
                patch.object(downloader, "_post_json", post_json),
                patch.object(
                    downloader,
                    "_fetch_list_page_via_browser_request",
                    browser_request,
                    create=True,
                ),
                patch("time.sleep"),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-12-31",
                    list_only=True,
                )

            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(len(summary.typed_errors), 0)
            self.assertEqual(post_json.call_count, 2)
            browser_request.assert_called_once()

    def test_cquae_list_fetch_falls_back_to_browser_request_after_incomplete_read(self) -> None:
        class IncompleteReadResponse:
            headers = SimpleNamespace(get_content_charset=lambda: "utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                raise http.client.IncompleteRead(b"<html>partial", 91831)

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ChongqingEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=10,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
            )
            html = """
            <html>
              <head><title>项目中心- 重庆产权交易网</title></head>
              <body>
                <div>共找到1条项目记录</div>
                <div class="n2_List itcon">
                  <a class="P_List_A" href="/Project/Show?id=12345">重庆股权 fallback 项目</a>
                  <span>挂牌开始日期：2026-05-12</span>
                  <span>挂牌期满日期：2026-06-12</span>
                  <span>转让底价：100 万元</span>
                </div>
              </body>
            </html>
            """
            source_url = downloader.list_sources[0].list_url
            browser_request = Mock(
                return_value=HttpFetchedText(
                    html,
                    source_url=source_url,
                    final_url=source_url,
                    http_status=200,
                    raw_bytes=html.encode("utf-8"),
                )
            )

            with (
                patch("urllib.request.urlopen", return_value=IncompleteReadResponse()) as urlopen,
                patch.object(
                    downloader,
                    "_fetch_list_html_via_browser_request",
                    browser_request,
                    create=True,
                ),
                patch("time.sleep"),
            ):
                summary = downloader.run(
                    start_date="2026-04-01",
                    end_date="2026-06-03",
                    list_only=True,
                )

            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(len(summary.typed_errors), 0)
            self.assertEqual(urlopen.call_count, 3)
            browser_request.assert_called_once()

    def test_regional_scope_contract_filters_are_encoded_in_list_payloads(self) -> None:
        cases = [
            ("shandong", "equity_transfer", ShandongEquityTransferDownloader),
            ("shandong", "capital_increase", ShandongCapitalIncreaseDownloader),
            ("guangdong", "equity_transfer", GuangdongEquityTransferDownloader),
            ("guangdong", "capital_increase", GuangdongCapitalIncreaseDownloader),
            ("shenzhen", "equity_transfer", ShenzhenEquityTransferDownloader),
            ("shenzhen", "capital_increase", ShenzhenCapitalIncreaseDownloader),
        ]

        for source_id, business_id, downloader_cls in cases:
            with (
                self.subTest(source_id=source_id, business_id=business_id),
                tempfile.TemporaryDirectory() as tmp_dir,
            ):
                downloader = downloader_cls(
                    html_root=tmp_dir,
                    page_size=20,
                    max_pages=1,
                    concurrency=1,
                    resume=False,
                    save_json=False,
                    logger=self._logger(),
                    run_id="job-scope-contract",
                )
                _html_path, requests, _page, _raw_response = self._run_with_response(
                    downloader,
                    {"data": []},
                    list_only=True,
                )
                self.assertEqual(len(requests), 1)
                descriptor = get_source_business_requirement(source_id, "listing", business_id)
                self.assertEqual(descriptor.scope_policy, "central_soe_ministry_only")
                actual_filters = {
                    key: tuple(value) if isinstance(value, list) else value
                    for key in descriptor.required_query_filters
                    for value in [requests[0].get(key)]
                }
                self.assertEqual(
                    actual_filters,
                    descriptor.required_query_filters,
                )

    def test_regional_scope_payload_filters_are_read_from_contract_at_runtime(self) -> None:
        cases = [
            (
                ShandongEquityTransferDownloader,
                {"systemSource": "CONTRACT-SD"},
            ),
            (
                GuangdongEquityTransferDownloader,
                {"IN_CQLSGX": "'CONTRACT-GD'"},
            ),
            (
                ShenzhenEquityTransferDownloader,
                {
                    "channelIds": ("CONTRACT-CHANNEL",),
                    "targetColumnIds": ("CONTRACT-COLUMN",),
                    "projectSubjections": ("CONTRACT-SUBJECTION",),
                },
            ),
        ]

        for downloader_cls, contract_filters in cases:
            with self.subTest(cls=downloader_cls.__name__), tempfile.TemporaryDirectory() as tmp_dir:
                downloader = downloader_cls(
                    html_root=tmp_dir,
                    page_size=20,
                    logger=self._logger(),
                )
                descriptor = SimpleNamespace(required_query_filters=contract_filters)

                with patch(
                    "peap.downloaders.listing_exchanges.get_source_business_requirement",
                    return_value=descriptor,
                ):
                    payload = downloader._list_payload(1)

                for key, value in contract_filters.items():
                    expected_value = list(value) if isinstance(value, tuple) else value
                    self.assertEqual(payload[key], expected_value)

    def test_tpre_cquae_physical_asset_minimum_price_filters_match_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tpre = TianjinPhysicalAssetDownloader(html_root=tmp_dir, logger=self._logger())
            descriptor = get_source_business_requirement("tpre", "listing", "physical_asset")
            self.assertEqual(descriptor.scope_policy, "physical_asset_min_price_5000w")

            self.assertEqual(len(tpre.list_queries), 1)
            actual_filters = {
                key: tpre.list_queries[0].extra_params.get(key)
                for key in descriptor.required_query_filters
            }
            self.assertEqual(actual_filters, descriptor.required_query_filters)

        with tempfile.TemporaryDirectory() as tmp_dir:
            cquae = ChongqingPhysicalAssetDownloader(html_root=tmp_dir, logger=self._logger())
            descriptor = get_source_business_requirement("cquae", "listing", "physical_asset")
            self.assertEqual(descriptor.scope_policy, "physical_asset_min_price_5000w")

            expected_prices = tuple(descriptor.required_query_filters["price"])
            actual_prices = tuple(
                urllib.parse.parse_qs(urllib.parse.urlsplit(source.list_url).query).get(
                    "price",
                    [None],
                )[0]
                for source in cquae.list_sources
            )
            self.assertEqual(actual_prices, expected_prices)
            first_bucket_label = expected_prices[0].replace("万", "w").replace("亿", "y").replace("-", "-to-")
            second_bucket_label = "over-" + expected_prices[1].removesuffix("以上").replace("亿", "y")
            self.assertEqual(
                {source.label for source in cquae.list_sources},
                {
                    f"physical-{first_bucket_label}",
                    f"physical-{second_bucket_label}",
                },
            )

    def test_core_listing_downloaders_match_declared_query_taxonomy(self) -> None:
        cases = [
            ("sse", "physical_asset", ShanghaiPhysicalAssetDownloader),
            ("sse", "equity_transfer", ShanghaiEquityTransferDownloader),
            ("sse", "capital_increase", ShanghaiCapitalIncreaseDownloader),
            ("sse", "pre_disclosure", ShanghaiPreDisclosureDownloader),
            ("tpre", "physical_asset", TianjinPhysicalAssetDownloader),
            ("tpre", "equity_transfer", TianjinEquityTransferDownloader),
            ("tpre", "capital_increase", TianjinCapitalIncreaseDownloader),
            ("tpre", "pre_disclosure", TianjinPreDisclosureDownloader),
            ("cquae", "physical_asset", ChongqingPhysicalAssetDownloader),
            ("cquae", "equity_transfer", ChongqingEquityTransferDownloader),
            ("cquae", "capital_increase", ChongqingCapitalIncreaseDownloader),
            ("cquae", "pre_disclosure", ChongqingPreDisclosureDownloader),
            ("cbex", "physical_asset", CbexPhysicalAssetDownloader),
            ("cbex", "equity_transfer", CbexEquityTransferDownloader),
            ("cbex", "capital_increase", CbexCapitalIncreaseDownloader),
            ("cbex", "pre_disclosure", CbexPreDisclosureDownloader),
        ]

        for source_id, business_id, downloader_cls in cases:
            with self.subTest(source_id=source_id, business_id=business_id), tempfile.TemporaryDirectory() as tmp_dir:
                downloader = downloader_cls(html_root=tmp_dir, logger=self._logger())
                descriptor = get_source_business_requirement(source_id, "listing", business_id)
                self.assertEqual(downloader.manifest_list_endpoint, descriptor.list_endpoint)
                self.assertEqual(
                    self._declared_list_specs_for_downloader(source_id, downloader),
                    tuple(dict(spec) for spec in descriptor.list_query_specs),
                )

    def _declared_list_specs_for_downloader(self, source_id: str, downloader) -> tuple[dict[str, object], ...]:
        if source_id == "sse":
            from peap.downloaders.common import business_id_key
            from peap.downloaders.sse_contracts import get_sse_task_contract

            contract = get_sse_task_contract(business_id_key(downloader.output_type))
            project_types = {
                "realright": "ZICHANZHUANRANG",
                "equity": "CHANQUAN",
                "capitalincrease": "ZENGZI",
            }
            gplx_by_project_type = dict(downloader.list_query_specs)
            rows = []
            for request in contract.list_requests:
                project_type = next(
                    token for endpoint_token, token in project_types.items() if endpoint_token in request.endpoint
                )
                row = {
                    "endpoint": request.endpoint,
                    "project_type": project_type,
                    "gplx": gplx_by_project_type[project_type],
                }
                if request.xmlx is not None:
                    row["XMLX"] = request.xmlx
                rows.append(row)
            return tuple(rows)
        if source_id == "tpre":
            return tuple(
                {
                    "label": query.label,
                    "systemCode": query.system_code,
                    "bizTypeCode": query.biz_type_code,
                    **dict(query.extra_params),
                }
                for query in downloader.list_queries
            )
        if source_id == "cquae":
            return tuple(
                {
                    "label": source.label,
                    **{
                        key: self._maybe_int(value[0])
                        if len(value) == 1
                        else tuple(self._maybe_int(item) for item in value)
                        for key, value in urllib.parse.parse_qs(
                            urllib.parse.urlsplit(source.list_url).query
                        ).items()
                    },
                }
                for source in downloader.list_sources
            )
        if source_id == "cbex":
            rows = []
            for source in downloader.list_sources:
                row = {"label": source.label, "businessType": source.business_type}
                if source.asset_type:
                    row["assetType"] = source.asset_type
                if downloader.include_pre_disclosure is not None:
                    row["include_pre_disclosure"] = downloader.include_pre_disclosure
                rows.append(row)
            return tuple(rows)
        raise AssertionError(f"unexpected source_id: {source_id}")

    def _maybe_int(self, value: str) -> object:
        return int(value) if str(value).isdigit() else value

    def test_exchange_live_endpoint_contracts_are_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            shandong = ShandongEquityTransferDownloader(html_root=tmp_dir, logger=self._logger())
            shandong_capital = ShandongCapitalIncreaseDownloader(html_root=tmp_dir, logger=self._logger())
            guangdong = GuangdongEquityTransferDownloader(html_root=tmp_dir, logger=self._logger())
            guangdong_capital = GuangdongCapitalIncreaseDownloader(html_root=tmp_dir, logger=self._logger())
            shenzhen_equity = ShenzhenEquityTransferDownloader(html_root=tmp_dir, logger=self._logger())
            shenzhen_capital = ShenzhenCapitalIncreaseDownloader(html_root=tmp_dir, logger=self._logger())

        self.assertEqual(shandong.list_api_url, "http://www.sdcqjy.com/projlist/xmpd/yqgq")
        self.assertEqual(shandong_capital.list_api_url, "http://www.sdcqjy.com/projlist/xmpd/zrzz")
        self.assertEqual(guangdong.list_api_url, "https://new.gduaee.com/si/prjs/equity/list")
        self.assertEqual(
            guangdong_capital.list_api_url,
            "https://new.gduaee.com/si/prjs/capitalincrease/list",
        )
        self.assertEqual(
            shenzhen_equity.list_api_url,
            "https://www.sotcbb.com/api/v1/sotcbb/local/project/list",
        )
        self.assertEqual(
            shenzhen_capital.list_api_url,
            "https://www.sotcbb.com/api/v1/sotcbb/local/project/list",
        )
        self.assertEqual(shenzhen_equity.channel_ids, ("3226",))
        self.assertEqual(shenzhen_equity.target_column_ids, ("3961",))
        self.assertEqual(shenzhen_capital.channel_ids, ("3238",))
        self.assertEqual(shenzhen_capital.target_column_ids, ("3966",))
        self.assertGreaterEqual(guangdong.timeout, 30)
        self.assertEqual(guangdong._render_timeout_ms, guangdong.timeout * 1000)

    def test_actual_listing_response_shapes_map_to_candidates_explicitly(self) -> None:
        cases = [
            (
                ShandongEquityTransferDownloader,
                (
                    '<tr data-proId="sd-id"><td class="o_line code" title="SD001">SD001</td>'
                    '<td class="o_line name"><a onclick="linkToDetail({&#34;id&#34;:'
                    '&#34;sd-id&#34;,&#34;code&#34;:&#34;SD001&#34;,&#34;name&#34;:'
                    '&#34;山东项目&#34;,&#34;systemSource&#34;:&#34;CQY&#34;,'
                    '&#34;endDate&#34;:&#34;2026/05/28&#34;})">山东项目</a></td></tr>'
                ),
                "SD001",
                "山东项目",
                "http://www.sdcqjy.com/proj/tc/sd-id",
            ),
            (
                GuangdongEquityTransferDownloader,
                {
                    "data": [
                        {
                            "XMID": 159132,
                            "XMBH": "G32026GD0000048-4",
                            "XMMC": "广东项目",
                            "KSRQ": 20260514,
                            "FCLASS": "GQ",
                            "CQLSGX": "GQ100101",
                        }
                    ]
                },
                "G32026GD0000048-4",
                "广东项目",
                "https://new.gduaee.com/xmzx.html#/equityDetail?XMID=159132",
            ),
            (
                GuangdongCapitalIncreaseDownloader,
                {
                    "data": [
                        {
                            "XMID": 159133,
                            "XMBH": "G62026GD0000048",
                            "XMMC": "广东增资项目",
                            "KSRQ": 20260514,
                            "FCLASS": "1C",
                            "CQLSGX": "1C100301",
                        }
                    ]
                },
                "G62026GD0000048",
                "广东增资项目",
                "https://new.gduaee.com/xmzx.html#/capital_increaseDetail?XMID=159133",
            ),
            (
                ShenzhenEquityTransferDownloader,
                {
                    "data": {
                        "content": [
                            {
                                "contentId": 2418408,
                                "objectId": "a8fccddb830045c0979f789051486bf4",
                                "channelId": 3961,
                                "isObject": 1,
                                "projectNo": "DISCLOSURE_a8fccddb830045c0979f789051486bf4",
                                "title": "深圳项目",
                                "registerFrom": "2026-05-12 00:00:00",
                                "projectSubjection": "地方",
                            }
                        ]
                    }
                },
                "DISCLOSURE_a8fccddb830045c0979f789051486bf4",
                "深圳项目",
                (
                    "https://www.sotcbb.com/bdDetail.htm?contentId=a8fccddb830045c0979f789051486bf4"
                    "&channelId=3961&id=2418408"
                ),
            ),
        ]

        for downloader_cls, response, project_code, project_name, page_url in cases:
            with self.subTest(cls=downloader_cls.__name__), tempfile.TemporaryDirectory() as tmp_dir:
                downloader = downloader_cls(
                    html_root=tmp_dir,
                    page_size=20,
                    max_pages=1,
                    concurrency=1,
                    resume=False,
                    save_json=False,
                    logger=self._logger(),
                    run_id="job-shapes",
                )
                if isinstance(response, str):
                    rows = downloader._extract_rows(response)
                else:
                    rows = downloader._extract_rows(response)
                self.assertEqual(len(rows), 1)
                summary = type("Summary", (), {
                    "listed_items": 0,
                    "skipped_by_missing_xmid": 0,
                    "typed_errors": [],
                    "skipped_by_duplicate": 0,
                    "skipped_by_business_filter": 0,
                    "skipped_by_list_date": 0,
                    "skipped_by_resume": 0,
                    "candidate_entries": [],
                    "candidate_dates": [],
                })()
                evidence_dir = str(Path(tmp_dir) / "_evidence" / "job-shapes")
                candidate = downloader._candidate_from_row(
                    row=rows[0],
                    output_dir=tmp_dir,
                    summary=summary,
                    evidence_dir=evidence_dir,
                    start=None,
                    end=None,
                    seen=set(),
                )
                self.assertIsNotNone(candidate)
                assert candidate is not None
                self.assertEqual(candidate.project_code, project_code)
                self.assertEqual(candidate.project_name, project_name)
                self.assertEqual(candidate.page_url, page_url)

    def test_guangdong_capital_candidates_require_authoritative_business_markers_and_route(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = GuangdongCapitalIncreaseDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-guangdong-capital-business-filter",
            )
            summary = downloader.run(
                start_date="2026-01-01",
                end_date="2026-01-31",
                list_only=True,
                prefetched_candidates=[
                    {
                        "XMID": "wrong-markers",
                        "XMBH": "G32026GD0000001",
                        "XMMC": "误入增资任务的股权项目",
                        "KSRQ": "2026-01-10",
                        "FCLASS": "GQ",
                        "CQLSGX": "GQ100101",
                        "page_url": (
                            "https://new.gduaee.com/xmzx.html#/equityDetail"
                            "?XMID=wrong-markers"
                        ),
                    },
                    {
                        "XMID": "wrong-route",
                        "XMBH": "G62026GD0000002",
                        "XMMC": "详情路由错误的增资项目",
                        "KSRQ": "2026-01-10",
                        "FCLASS": "1C",
                        "CQLSGX": "1C100301",
                        "page_url": (
                            "https://new.gduaee.com/xmzx.html#/equityDetail"
                            "?XMID=wrong-route"
                        ),
                    },
                    {
                        "XMID": "capital-ok",
                        # The project-code prefix is deliberately equity-shaped: it cannot
                        # override authoritative Guangdong business fields and route data.
                        "XMBH": "G32026GD0000003",
                        "XMMC": "权威业务字段正确的增资项目",
                        "KSRQ": "2026-01-10",
                        "FCLASS": "1C",
                        "CQLSGX": "1C100301",
                        "page_url": (
                            "https://new.gduaee.com/xmzx.html#/capital_increaseDetail"
                            "?XMID=capital-ok"
                        ),
                    },
                ],
            )

            self.assertEqual(summary.listed_items, 3)
            self.assertEqual(summary.skipped_by_business_filter, 2)
            self.assertEqual(summary.detail_candidates, 1)
            self.assertEqual(summary.list_unaccounted, 0)
            self.assertEqual(
                [entry["id"] for entry in summary.candidate_entries],
                ["capital-ok"],
            )
            evidence_path = (
                Path(tmp_dir)
                / "_evidence"
                / "job-guangdong-capital-business-filter"
                / "guangdong__listing__capital_increase"
                / "candidates.jsonl"
            )
            decisions = [
                json.loads(line)
                for line in evidence_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [decision["decision"] for decision in decisions],
                ["excluded", "excluded", "accepted"],
            )
            self.assertEqual(
                {decision.get("reason") for decision in decisions[:2]},
                {"business-filter"},
            )

    def test_page_url_fallbacks_preserve_falsy_candidate_rows(self) -> None:
        class FalsyRow(dict):
            def __bool__(self) -> bool:
                return False

        cases = [
            (
                GuangdongEquityTransferDownloader,
                FalsyRow(
                    {
                        "XMID": 159132,
                        "XMBH": "G32026GD0000048-4",
                        "XMMC": "广东项目",
                        "KSRQ": "2026-05-14",
                        "FCLASS": "GQ",
                        "CQLSGX": "GQ100101",
                    }
                ),
                "https://new.gduaee.com/xmzx.html#/equityDetail?XMID=159132",
            ),
            (
                ShenzhenEquityTransferDownloader,
                FalsyRow(
                    {
                        "contentId": 2418408,
                        "objectId": "a8fccddb830045c0979f789051486bf4",
                        "channelId": 3961,
                        "isObject": 1,
                        "projectNo": "DISCLOSURE_a8fccddb830045c0979f789051486bf4",
                        "title": "深圳项目",
                        "registerFrom": "2026-05-12 00:00:00",
                    }
                ),
                (
                    "https://www.sotcbb.com/bdDetail.htm?contentId=a8fccddb830045c0979f789051486bf4"
                    "&channelId=3961&id=2418408"
                ),
            ),
        ]

        for downloader_cls, row, page_url in cases:
            with self.subTest(cls=downloader_cls.__name__), tempfile.TemporaryDirectory() as tmp_dir:
                downloader = downloader_cls(
                    html_root=tmp_dir,
                    page_size=20,
                    max_pages=1,
                    concurrency=1,
                    resume=False,
                    save_json=False,
                    logger=self._logger(),
                    run_id="job-falsy-row",
                )
                summary = SimpleNamespace(
                    listed_items=0,
                    skipped_by_missing_xmid=0,
                    typed_errors=[],
                    skipped_by_duplicate=0,
                    skipped_by_list_date=0,
                    skipped_by_resume=0,
                    candidate_entries=[],
                    candidate_dates=[],
                )
                candidate = downloader._candidate_from_row(
                    row=row,
                    output_dir=tmp_dir,
                    summary=summary,
                    evidence_dir=str(Path(tmp_dir) / "_evidence" / "job-falsy-row"),
                    start=None,
                    end=None,
                    seen=set(),
                )

                self.assertIsNotNone(candidate)
                assert candidate is not None
                self.assertEqual(candidate.page_url, page_url)
                self.assertEqual(summary.candidate_entries[0]["row"], row)

    def test_detail_download_uses_configured_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=2,
                max_pages=1,
                concurrency=2,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-concurrency",
            )
            response = {
                "data": {
                    "content": [
                        {
                            "contentId": 1,
                            "objectId": "obj-1",
                            "channelId": 3961,
                            "isObject": 1,
                            "projectNo": "SZ001",
                            "title": "深圳项目1",
                            "registerFrom": "2026-05-12 00:00:00",
                        },
                        {
                            "contentId": 2,
                            "objectId": "obj-2",
                            "channelId": 3961,
                            "isObject": 1,
                            "projectNo": "SZ002",
                            "title": "深圳项目2",
                            "registerFrom": "2026-05-12 00:00:00",
                        },
                    ]
                }
            }
            pages = [_ConcurrentPage(), _ConcurrentPage()]
            _ConcurrentPage.active = 0
            _ConcurrentPage.max_active = 0
            fake_context = _FakeAsyncPlaywrightContext(object())

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    return_value=_fetched_list_text(json.dumps(response)),
                ),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_SequenceBrowser(pages),
                ),
            ):
                summary = downloader.run(start_date=None, end_date=None)

            self.assertEqual(summary.saved, 2)
            self.assertEqual(_ConcurrentPage.max_active, 2)

    def test_default_pagination_continues_until_short_page_when_max_pages_is_unset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShandongEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=2,
                max_pages=None,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-pagination",
            )
            responses = [
                {
                    "data": {
                        "list": [
                            {
                                "id": "sd-1",
                                "projectCode": "SD001",
                                "title": "one",
                                "publishDate": "2026-01-10",
                                "url": "https://sd.example/1",
                            },
                            {
                                "id": "sd-2",
                                "projectCode": "SD002",
                                "title": "two",
                                "publishDate": "2026-01-11",
                                "url": "https://sd.example/2",
                            },
                        ]
                    }
                },
                {
                    "data": {
                        "list": [
                            {
                                "id": "sd-3",
                                "projectCode": "SD003",
                                "title": "three",
                                "publishDate": "2026-01-12",
                                "url": "https://sd.example/3",
                            },
                            {
                                "id": "sd-4",
                                "projectCode": "SD004",
                                "title": "four",
                                "publishDate": "2026-01-13",
                                "url": "https://sd.example/4",
                            },
                        ]
                    }
                },
                {
                    "data": {
                        "list": [
                            {
                                "id": "sd-5",
                                "projectCode": "SD005",
                                "title": "five",
                                "publishDate": "2026-01-14",
                                "url": "https://sd.example/5",
                            }
                        ]
                    }
                },
            ]
            requests: list[dict[str, object]] = []

            def fake_fetch_list_page(
                *, page_num: int, payload: dict[str, object]
            ) -> HttpFetchedText:
                requests.append(payload)
                return _fetched_list_text(
                    json.dumps(responses[len(requests) - 1], ensure_ascii=False),
                    page_num=page_num,
                )

            with patch.object(downloader, "_fetch_list_page", side_effect=fake_fetch_list_page):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=True,
                )

            self.assertEqual([request["pageNum"] for request in requests], [1, 2, 3])
            self.assertEqual(summary.pages_requested, 3)
            self.assertEqual(summary.detail_candidates, 5)

    def test_relative_detail_page_url_is_resolved_against_source_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShandongEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-relative-url",
            )

            _html_path, _requests, page, _raw_response = self._run_with_response(
                downloader,
                {
                    "data": {
                        "list": [
                            {
                                "id": "sd-relative-1",
                                "projectCode": "SDREL001",
                                "title": "relative",
                                "publishDate": "2026-01-10",
                                "url": "/detail/abc",
                            }
                        ]
                    }
                },
            )

            self.assertEqual(page.urls, ["http://www.sdcqjy.com/detail/abc"])

    def test_default_pagination_fails_closed_when_a_full_page_repeats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShandongEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=2,
                max_pages=None,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-pagination-repeat",
            )
            repeated_page = {
                "data": {
                    "list": [
                        {
                            "id": "sd-1",
                            "projectCode": "SD001",
                            "title": "one",
                            "publishDate": "2026-01-10",
                            "url": "https://sd.example/1",
                        },
                        {
                            "id": "sd-2",
                            "projectCode": "SD002",
                            "title": "two",
                            "publishDate": "2026-01-11",
                            "url": "https://sd.example/2",
                        },
                    ]
                }
            }
            requests: list[dict[str, object]] = []

            def fake_fetch_list_page(
                *, page_num: int, payload: dict[str, object]
            ) -> HttpFetchedText:
                requests.append(payload)
                if len(requests) > 2:
                    raise AssertionError("pagination should stop after repeated full page")
                return _fetched_list_text(
                    json.dumps(repeated_page, ensure_ascii=False),
                    page_num=page_num,
                )

            with patch.object(downloader, "_fetch_list_page", side_effect=fake_fetch_list_page):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=True,
                )

            self.assertEqual([request["pageNum"] for request in requests], [1, 2])
            self.assertEqual(summary.pages_requested, 2)
            self.assertEqual(summary.detail_candidates, 2)
            self.assertEqual(summary.skipped_by_duplicate, 0)
            self.assertEqual(len(summary.typed_errors), 1)
            self.assertIn("repeated page identity", summary.typed_errors[0].raw_reason)
            manifest_path = next(Path(tmp_dir).glob("_evidence/**/discovery/**/manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["coverage_status"], "failed")

    def test_detail_pages_are_closed_after_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-close-pages",
            )
            success_page = _FakePage()
            failing_page = _FailingPage()
            browser = _SequenceBrowser([success_page, failing_page])
            fake_context = _FakeAsyncPlaywrightContext(object())

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    side_effect=AssertionError("list API must not be called"),
                ),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=browser,
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "sz-close-ok",
                            "project_code": "SZCLOSEOK",
                            "project_name": "close ok",
                            "page_url": "https://sz.example/detail/ok",
                            "disclosure_start": "2026-01-10",
                            "row": {},
                        },
                        {
                            "id": "sz-close-fail",
                            "project_code": "SZCLOSEFAIL",
                            "project_name": "close fail",
                            "page_url": "https://sz.example/detail/fail",
                            "disclosure_start": "2026-01-11",
                            "row": {},
                        },
                    ],
                )

            self.assertEqual(summary.saved, 1)
            self.assertEqual(summary.detail_failed, 1)
            self.assertTrue(success_page.closed)
            self.assertTrue(failing_page.closed)

    def test_prefetched_candidates_execute_without_list_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=True,
                logger=self._logger(),
                run_id="job-prefetched",
            )
            page = _FakePage()
            fake_context = _FakeAsyncPlaywrightContext(object())
            with (
                patch.object(
                    downloader,
                    "_post_json",
                    side_effect=AssertionError("list API must not be called"),
                ),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_FakeBrowser(page),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "sz-prefetched-1",
                            "project_code": "SZPF001",
                            "project_name": "prefetched",
                            "page_url": "https://sz.example/detail/prefetched",
                            "disclosure_start": "2026-01-10",
                            "row": {"projectSubjection": "地方"},
                        }
                    ],
                )

            self.assertEqual(summary.saved, 1)
            evidence_dir = (
                Path(tmp_dir)
                / "_evidence"
                / "job-prefetched"
                / "shenzhen__listing__equity_transfer"
            )
            self.assertFalse((evidence_dir / "list_page_1.json").exists())
            decisions = [
                json.loads(line)
                for line in (evidence_dir / "candidates.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual([row["decision"] for row in decisions], ["accepted"])
            self.assertEqual(page.urls, ["https://sz.example/detail/prefetched"])

    def test_prefetched_candidate_rejects_explicit_non_mapping_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-prefetched-invalid-row",
            )

            with patch.object(
                downloader,
                "_post_json",
                side_effect=AssertionError("list API must not be called"),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=True,
                    prefetched_candidates=[
                        {
                            "id": "sz-invalid-row",
                            "project_code": "SZBADROW",
                            "project_name": "invalid row",
                            "page_url": "https://sz.example/detail/invalid-row",
                            "disclosure_start": "2026-01-10",
                            "row": [],
                        }
                    ],
                )

        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(summary.listed_items, 1)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "validation")
        self.assertIn("row", summary.typed_errors[0].raw_reason)
        self.assertIn("non-mapping", summary.typed_errors[0].raw_reason)

    def test_get_list_fetch_encodes_payload_query_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShandongEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-shandong-get-query",
            )
            requested_urls: list[str] = []

            def fake_get_text(url: str) -> str:
                requested_urls.append(url)
                return json.dumps({"data": []})

            with patch.object(downloader, "_get_text", side_effect=fake_get_text):
                downloader._fetch_list_page(page_num=3, payload=downloader._list_payload(3))

        self.assertEqual(len(requested_urls), 1)
        parsed = urllib.parse.urlparse(requested_urls[0])
        params = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "http")
        self.assertEqual(parsed.netloc, "www.sdcqjy.com")
        self.assertEqual(parsed.path, "/projlist/xmpd/yqgq")
        self.assertEqual(params["path"], ["yqgq"])
        self.assertEqual(params["systemSource"], ["CQY"])
        self.assertEqual(params["pageNum"], ["3"])
        self.assertEqual(params["pageSize"], ["20"])

    def test_shandong_official_empty_html_list_page_is_not_invalid_json(self) -> None:
        empty_list_html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>SPREC山东产权交易中心--项目频道</title></head>"
            "<body><div class='project_cont'>"
            "<div class='proj_data_cont'><div id='projDataCont'></div>"
            "<div id='pagination' class='pagination'></div></div></div>"
            "<script>$.ajax({url:'/projlist/getdata', data:{categoryId:'xmpd',"
            "typeId:'zrzz', page: page, projType:''}});"
            "new Page({id:'pagination', pageAmount: 15 || 10, dataTotal: 0 || 0,"
            "curPage:1});</script></body></html>"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShandongCapitalIncreaseDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-shandong-empty-html",
            )

            with patch.object(
                downloader, "_get_text", return_value=_fetched_list_text(empty_list_html)
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=True,
                )

        self.assertEqual(summary.pages_requested, 1)
        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(summary.typed_errors, [])
        self.assertEqual(summary.list_page_observations[0]["status"], "empty")
        self.assertEqual(summary.list_page_observations[0]["declared_total"], 0)

    def test_shandong_html_list_with_positive_total_without_rows_is_list_error(self) -> None:
        broken_list_html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>SPREC山东产权交易中心--项目频道</title></head>"
            "<body><div class='project_cont'>"
            "<div class='proj_data_cont'><div id='projDataCont'></div>"
            "<div id='pagination' class='pagination'></div></div></div>"
            "<script>$.ajax({url:'/projlist/getdata', data:{categoryId:'xmpd',"
            "typeId:'zrzz', page: page, projType:''}});"
            "new Page({id:'pagination', pageAmount: 15 || 10, dataTotal: 7 || 0,"
            "curPage:1});</script></body></html>"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShandongCapitalIncreaseDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-shandong-positive-empty-html",
            )

            with patch.object(
                downloader, "_get_text", return_value=_fetched_list_text(broken_list_html)
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=True,
                )

        self.assertEqual(summary.pages_requested, 1)
        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "list")
        self.assertIn("positive-total-without-items", summary.typed_errors[0].raw_reason)
        self.assertEqual(
            summary.list_page_observations[0]["status"],
            "positive-total-without-items",
        )

    def test_shandong_truncated_json_list_response_records_typed_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShandongEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-shandong-bad-json",
            )

            with patch.object(
                downloader, "_get_text", return_value=_fetched_list_text("{bad json")
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=True,
                )

        self.assertEqual(summary.pages_requested, 1)
        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "list")
        self.assertIn("invalid-json", summary.typed_errors[0].raw_reason)

    def test_declared_rows_path_schema_failure_does_not_fallback_to_bypass_rows(self) -> None:
        class DeclaredRowsPathDownloader(GuangdongEquityTransferDownloader):
            rows_path = ("data", "records")

            def _list_payload(self, page_num: int) -> dict[str, object]:
                return {"pageNum": page_num, "pageSize": self.page_size}

        bypass_row = {
            "XMID": "bypass-1",
            "XMBH": "BYPASS001",
            "XMMC": "bypass row",
            "KSRQ": "2026-01-10",
        }
        response = {"data": {"error": "bad"}, "records": [bypass_row]}

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = DeclaredRowsPathDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-declared-rows-path-schema-failure",
            )

            with patch.object(
                downloader,
                "_post_json",
                return_value=_fetched_list_text(json.dumps(response, ensure_ascii=False)),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=True,
                )

        self.assertEqual(summary.detail_candidates, 0)
        self.assertEqual(summary.listed_items, 0)
        self.assertEqual(summary.candidate_entries, [])
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "list")
        self.assertIn("schema", summary.typed_errors[0].raw_reason)
        self.assertIn("data.records", summary.typed_errors[0].raw_reason)

    def test_malicious_run_id_cannot_escape_evidence_root(self) -> None:
        with tempfile.TemporaryDirectory() as parent_dir:
            tmp_dir = str(Path(parent_dir) / "html")
            downloader = ShandongEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="../../escape",
            )

            with patch.object(
                downloader,
                "_get_text",
                return_value=_fetched_list_text(json.dumps({"data": {"list": []}})),
            ):
                downloader.run(start_date="2026-01-01", end_date="2026-01-31", list_only=True)

            evidence_root = (Path(tmp_dir) / "_evidence").resolve()
            evidence_files = list(evidence_root.rglob("list_page_1.json"))
            self.assertEqual(len(evidence_files), 1)
            self.assertTrue(evidence_files[0].resolve().is_relative_to(evidence_root))
            self.assertFalse((Path(tmp_dir).parent / "escape").exists())

    def test_detail_json_sidecar_respects_save_json_flag(self) -> None:
        for save_json in (False, True):
            with self.subTest(save_json=save_json), tempfile.TemporaryDirectory() as tmp_dir:
                downloader = ShenzhenEquityTransferDownloader(
                    html_root=tmp_dir,
                    page_size=20,
                    max_pages=1,
                    concurrency=1,
                    resume=False,
                    save_json=save_json,
                    logger=self._logger(),
                    run_id=f"job-save-json-{save_json}",
                )
                page = _FakePage()
                fake_context = _FakeAsyncPlaywrightContext(object())

                with (
                    patch.object(
                        downloader,
                        "_post_json",
                        side_effect=AssertionError("list API must not be called"),
                    ),
                    patch(
                        "peap.downloaders.listing_exchanges.async_playwright",
                        return_value=fake_context,
                    ),
                    patch(
                        "peap.downloaders.listing_exchanges.launch_chromium_browser",
                        return_value=_FakeBrowser(page),
                    ),
                ):
                    summary = downloader.run(
                        start_date="2026-01-01",
                        end_date="2026-01-31",
                        list_only=False,
                        prefetched_candidates=[
                            {
                                "id": "sz-save-json",
                                "project_code": "SZSAVEJSON",
                                "project_name": "save json",
                                "page_url": "https://sz.example/detail/save-json",
                                "disclosure_start": "2026-01-10",
                                "row": {},
                            }
                        ],
                    )

                self.assertEqual(summary.saved, 1)
                html_paths = list(Path(tmp_dir).rglob("*.html"))
                self.assertEqual(len(html_paths), 1)
                self.assertEqual(html_paths[0].read_text(encoding="utf-8"), RAW_DETAIL_HTML)
                sidecar_path = html_paths[0].with_suffix(".json")
                self.assertEqual(sidecar_path.exists(), save_json)

    def test_success_sidecar_records_actual_navigation_provenance(self) -> None:
        class PartialContentPage(_FakePage):
            async def goto(self, url: str, **kwargs):
                await super().goto(url, **kwargs)
                return SimpleNamespace(status=206)

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = GuangdongEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=True,
                logger=self._logger(),
                run_id="job-source-provenance",
            )
            source_url = "https://new.gduaee.com/xmzx.html#/equityDetail?XMID=159132"
            page = PartialContentPage()
            fake_context = _FakeAsyncPlaywrightContext(object())

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    side_effect=AssertionError("list API must not be called"),
                ),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_FakeBrowser(page),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "159132",
                            "project_code": "GD001",
                            "project_name": "广东股权",
                            "page_url": source_url,
                            "disclosure_start": "2026-01-10",
                            "row": {"FCLASS": "GQ", "CQLSGX": "GQ100101"},
                        }
                    ],
                )

            self.assertEqual(summary.saved, 1)
            html_path = next(Path(tmp_dir).rglob("*.html"))
            sidecar = json.loads(html_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["source_url"], source_url)
            self.assertEqual(sidecar["http_status"], 206)

    def test_guangdong_detail_api_fallback_archives_provenance_and_parses(self) -> None:
        cases = (
            {
                "downloader_cls": GuangdongEquityTransferDownloader,
                "xmid": "159421",
                "project_code": "G32026GD0000421",
                "project_name": "广东接口股权项目",
                "route": "equityDetail",
                "fclass": "GQ",
                "relation": "GQ100101",
                "seller": "广东接口转让方有限公司",
                "expected_seller": "广东接口转让方有限公司(51%)",
                "group": "中国接口集团有限公司",
                "business_type": "股权转让",
            },
            {
                "downloader_cls": GuangdongCapitalIncreaseDownloader,
                "xmid": "159422",
                "project_code": "G62026GD0000422",
                "project_name": "广东接口增资项目",
                "route": "capital_increaseDetail",
                "fclass": "1C",
                "relation": "1C100301",
                "seller": "广东接口融资方有限公司",
                "expected_seller": "广东接口融资方有限公司",
                "group": "中国增资集团有限公司",
                "business_type": "增资扩股",
            },
        )

        for case in cases:
            with self.subTest(business_type=case["business_type"]), tempfile.TemporaryDirectory() as tmp_dir:
                downloader = case["downloader_cls"](
                    html_root=tmp_dir,
                    page_size=20,
                    max_pages=1,
                    concurrency=1,
                    resume=False,
                    save_json=True,
                    logger=self._logger(),
                    run_id=f"job-guangdong-api-{case['xmid']}",
                )
                page_url = (
                    f"https://new.gduaee.com/xmzx.html#/{case['route']}?XMID={case['xmid']}"
                )
                project = {
                    "XMID": case["xmid"],
                    "XMBH": case["project_code"],
                    "XMMC": case["project_name"],
                    "MC": case["seller"],
                    "FCLASS": case["fclass"],
                    # Real detail responses may omit CQLSGX; FCLASS remains authoritative.
                    "PLKSRQ": "2026-07-01",
                    "PLJSRQ": "2026-07-31",
                    "SSJT": case["group"],
                    "SSHYMC": "软件和信息技术服务业",
                    "XMFZRXM": "测试联系人",
                    "ZRDJ": 1234.5,
                }
                sellers = []
                if case["business_type"] == "股权转让":
                    sellers = [{"ZRFMC": case["seller"], "SSJT": case["group"], "CCGQBL": 51}]
                payload = {
                    "code": 200,
                    "data": {
                        "PLXmMap": project,
                        "ZrfList": sellers,
                        "PLNsList": [{"ND": 2025, "JLR": -12.5, "ZCZJ": 456.75}],
                    },
                }
                raw_bytes = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                final_url = f"{downloader.detail_api_url}?edge=primary"
                response = HttpFetchedText(
                    raw_bytes.decode("utf-8"),
                    source_url=downloader.detail_api_url,
                    final_url=final_url,
                    http_status=200,
                    raw_bytes=raw_bytes,
                )
                fake_context = _FakeAsyncPlaywrightContext(object())

                with (
                    patch.object(downloader, "_post_json", return_value=response) as post_json,
                    patch(
                        "peap.downloaders.listing_exchanges.async_playwright",
                        return_value=fake_context,
                    ),
                    patch(
                        "peap.downloaders.listing_exchanges.launch_chromium_browser",
                        return_value=_FakeBrowser(_FailingPage()),
                    ),
                ):
                    summary = downloader.run(
                        start_date="2026-07-01",
                        end_date="2026-07-31",
                        list_only=False,
                        prefetched_candidates=[
                            {
                                # Deliberately differs from URL XMID to cover provenance lookup.
                                "id": f"prefetched-{case['xmid']}",
                                "XMBH": case["project_code"],
                                "XMMC": case["project_name"],
                                "KSRQ": "2026-07-01",
                                "FCLASS": case["fclass"],
                                "CQLSGX": case["relation"],
                                "page_url": page_url,
                            }
                        ],
                    )

                self.assertEqual(summary.saved, 1)
                self.assertEqual(summary.detail_failed, 0)
                post_json.assert_called_once_with(
                    downloader.detail_api_url,
                    {"XMID": int(case["xmid"]), "SQJL": 1},
                )
                html_path = next(Path(tmp_dir).rglob("*.html"))
                html_bytes = html_path.read_bytes()
                sidecar = json.loads(html_path.with_suffix(".json").read_text(encoding="utf-8"))
                self.assertEqual(sidecar["page_url"], page_url)
                self.assertEqual(sidecar["source_url"], downloader.detail_api_url)
                self.assertEqual(sidecar["detail_api_url"], downloader.detail_api_url)
                self.assertEqual(sidecar["detail_api_final_url"], final_url)
                self.assertEqual(sidecar["detail_payload"], payload)
                self.assertEqual(
                    sidecar["detail_payload_content_sha256"],
                    f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}",
                )
                self.assertEqual(sidecar["detail_payload_content_bytes"], len(raw_bytes))
                self.assertEqual(
                    sidecar["archive_content_sha256"],
                    f"sha256:{hashlib.sha256(html_bytes).hexdigest()}",
                )
                self.assertEqual(sidecar["archive_content_bytes"], len(html_bytes))

                parsed = parse_file(str(html_path))
                self.assertEqual(parsed.standard_record.project_code, case["project_code"])
                self.assertEqual(parsed.standard_record.project_name, case["project_name"])
                self.assertEqual(parsed.standard_record.business_type, case["business_type"])
                self.assertEqual(parsed.standard_record.seller, case["expected_seller"])
                self.assertEqual(parsed.standard_record.group_name, case["group"])
                self.assertEqual(parsed.standard_record.profit, -12.5)
                self.assertEqual(parsed.standard_record.asset_total, 456.75)
                if case["business_type"] == "增资扩股":
                    output = map_standard_to_excel_payload(parsed, "挂牌_增资扩股.xlsx")
                    self.assertEqual(output["融资方"], parsed.standard_record.seller)

    def test_guangdong_detail_api_fallback_fails_closed_on_identity_mismatch(self) -> None:
        downloader = GuangdongEquityTransferDownloader(html_root="/tmp", logger=self._logger())
        xmid = "159500"
        project_code = "G32026GD0000500"
        project_name = "广东身份校验项目"

        def payload_with(**overrides):
            project = {
                "XMID": xmid,
                "XMBH": project_code,
                "XMMC": project_name,
                "FCLASS": "GQ",
                "CQLSGX": "GQ100101",
                **overrides,
            }
            for key in tuple(project):
                if project[key] is None:
                    project.pop(key)
            return {"code": 200, "data": {"PLXmMap": project, "ZrfList": [], "PLNsList": []}}

        invalid_cases = (
            ("missing-xmid", {"XMID": None}, "xmid-mismatch"),
            ("wrong-xmid", {"XMID": "159501"}, "xmid-mismatch"),
            ("missing-code", {"XMBH": None}, "project-code-mismatch"),
            ("wrong-code", {"XMBH": "G32026GD0000501"}, "project-code-mismatch"),
            ("missing-name", {"XMMC": None}, "project-name-mismatch"),
            ("wrong-name", {"XMMC": "错误项目"}, "project-name-mismatch"),
            ("missing-fclass", {"FCLASS": None}, "business-mismatch"),
            ("wrong-fclass", {"FCLASS": "1C"}, "business-mismatch"),
            ("wrong-relation", {"CQLSGX": "1C100301"}, "business-mismatch"),
        )
        for label, overrides, expected_error in invalid_cases:
            with self.subTest(label=label):
                payload = payload_with(**overrides)
                raw_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                response = HttpFetchedText(
                    raw_bytes.decode("utf-8"),
                    source_url=downloader.detail_api_url,
                    final_url=downloader.detail_api_url,
                    http_status=200,
                    raw_bytes=raw_bytes,
                )
                with patch.object(downloader, "_post_json", return_value=response):
                    with self.assertRaisesRegex(RuntimeError, expected_error):
                        asyncio.run(
                            downloader._fetch_rendered_html(
                                page=_FailingPage(),
                                page_url=(
                                    "https://new.gduaee.com/xmzx.html#/equityDetail"
                                    f"?XMID={xmid}"
                                ),
                                expected_project_code=project_code,
                                expected_project_name=project_name,
                            )
                        )

        accepted_without_relation = payload_with(CQLSGX=None)
        validated = downloader._validate_detail_payload(
            accepted_without_relation,
            xmid=xmid,
            expected_project_code=project_code,
            expected_project_name=project_name,
        )
        self.assertEqual(validated["PLXmMap"]["XMID"], xmid)

    def test_shenzhen_bd_detail_save_json_sidecar_includes_package_view_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=True,
                logger=self._logger(),
                run_id="job-shenzhen-package-view",
            )
            page = _FakePage("<html><body>G32026SZ1000999 深圳接口项目</body></html>")
            fake_context = _FakeAsyncPlaywrightContext(object())
            package_payload = {
                "code": 200,
                "data": {
                    "form": [
                        {"label": "转让方名称", "value": "深圳接口转让方有限公司"},
                        {"label": "国资监管机构", "value": "国务院国资委监管"},
                    ]
                },
            }

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    return_value=json.dumps(package_payload, ensure_ascii=False),
                ) as post_json,
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_FakeBrowser(page),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "2418408",
                            "project_code": "G32026SZ1000999",
                            "project_name": "深圳接口项目",
                            "page_url": (
                                "https://www.sotcbb.com/bdDetail.htm?"
                                "contentId=object-abc&channelId=3961&id=2418408"
                            ),
                            "disclosure_start": "2026-01-10",
                            "row": {"objectId": "object-abc", "contentId": "2418408", "isObject": 1},
                        }
                    ],
                )

            self.assertEqual(summary.saved, 1)
            html_paths = list(Path(tmp_dir).rglob("*.html"))
            self.assertEqual(len(html_paths), 1)
            sidecar = json.loads(html_paths[0].with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["detail_payload"], package_payload)
            post_json.assert_called_once_with(
                "https://www.sotcbb.com/cqjy-api/package/view?id=object-abc",
                {},
            )

    def test_shenzhen_bd_detail_writes_required_package_view_sidecar_when_save_json_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-shenzhen-package-view-required",
            )
            page = _FakePage("<html><body>G32026SZ1000998 深圳接口项目二</body></html>")
            fake_context = _FakeAsyncPlaywrightContext(object())
            package_payload = {
                "code": 200,
                "data": {"form": [{"label": "近一年净利润", "value": "-100.5"}]},
            }

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    return_value=json.dumps(package_payload, ensure_ascii=False),
                ) as post_json,
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_FakeBrowser(page),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "2418407",
                            "project_code": "G32026SZ1000998",
                            "project_name": "深圳接口项目二",
                            "page_url": (
                                "https://www.sotcbb.com/bdDetail.htm?"
                                "contentId=object-required&channelId=3961&id=2418407"
                            ),
                            "disclosure_start": "2026-01-10",
                            "row": {"objectId": "object-required", "contentId": "2418407", "isObject": 1},
                        }
                    ],
                )

            self.assertEqual(summary.saved, 1)
            html_paths = list(Path(tmp_dir).rglob("*.html"))
            self.assertEqual(len(html_paths), 1)
            sidecar_path = html_paths[0].with_suffix(".json")
            self.assertTrue(sidecar_path.is_file())
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(sidecar["detail_payload"], package_payload)
            marker = json.loads(
                Path(f"{html_paths[0]}.peap-save-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(marker["save_status"], "complete")
            post_json.assert_called_once_with(
                "https://www.sotcbb.com/cqjy-api/package/view?id=object-required",
                {},
            )

    def test_save_json_resume_requires_valid_detail_sidecar(self) -> None:
        for sidecar_state in ("missing", "corrupt"):
            with self.subTest(sidecar_state=sidecar_state), tempfile.TemporaryDirectory() as tmp_dir:
                html_path = Path(tmp_dir) / "2026年1月" / "SZSAVEJSON-save json.html"
                html_path.parent.mkdir(parents=True, exist_ok=True)
                html_path.write_text("<html><body>html without sidecar</body></html>", encoding="utf-8")
                sidecar_path = html_path.with_suffix(".json")
                if sidecar_state == "corrupt":
                    sidecar_path.write_text("{bad json", encoding="utf-8")
                downloader = ShenzhenEquityTransferDownloader(
                    html_root=tmp_dir,
                    page_size=20,
                    max_pages=1,
                    concurrency=1,
                    resume=True,
                    save_json=True,
                    logger=self._logger(),
                    run_id=f"job-resume-sidecar-{sidecar_state}",
                )
                page = _FakePage()
                fake_context = _FakeAsyncPlaywrightContext(object())

                with (
                    patch.object(
                        downloader,
                        "_post_json",
                        side_effect=AssertionError("list API must not be called"),
                    ),
                    patch(
                        "peap.downloaders.listing_exchanges.async_playwright",
                        return_value=fake_context,
                    ),
                    patch(
                        "peap.downloaders.listing_exchanges.launch_chromium_browser",
                        return_value=_FakeBrowser(page),
                    ),
                ):
                    summary = downloader.run(
                        start_date="2026-01-01",
                        end_date="2026-01-31",
                        list_only=False,
                        prefetched_candidates=[
                            {
                                "id": "sz-save-json",
                                "project_code": "SZSAVEJSON",
                                "project_name": "save json",
                                "page_url": "https://sz.example/detail/save-json",
                                "disclosure_start": "2026-01-10",
                                "row": {},
                            }
                        ],
                    )

                self.assertEqual(summary.skipped_by_resume, 0)
                self.assertEqual(summary.saved, 1)
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                self.assertEqual(sidecar["project_code"], "SZSAVEJSON")

    def test_save_json_resume_rejects_sidecar_without_save_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = Path(tmp_dir) / "2026年1月" / "SZSAVEJSON-save json.html"
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text("<html><body>stale complete-looking html</body></html>", encoding="utf-8")
            html_path.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "task_id": "shenzhen:listing:equity_transfer",
                        "source_id": "shenzhen",
                        "business_id": "equity_transfer",
                        "project_code": "SZSAVEJSON",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=True,
                save_json=True,
                logger=self._logger(),
                run_id="job-resume-sidecar-missing-status",
            )
            page = _FakePage()
            fake_context = _FakeAsyncPlaywrightContext(object())

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    side_effect=AssertionError("list API must not be called"),
                ),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_FakeBrowser(page),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "sz-save-json",
                            "project_code": "SZSAVEJSON",
                            "project_name": "save json",
                            "page_url": "https://sz.example/detail/save-json",
                            "disclosure_start": "2026-01-10",
                            "row": {},
                        }
                    ],
                )

            self.assertEqual(summary.skipped_by_resume, 0)
            self.assertEqual(summary.saved, 1)
            sidecar = json.loads(html_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["save_status"], "complete")

    def test_plain_html_resume_requires_complete_status_marker_when_save_json_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = Path(tmp_dir) / "2026年1月" / "SZSAVEJSON-save json.html"
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text("<html><body>naked html without save evidence</body></html>", encoding="utf-8")
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=True,
                save_json=False,
                logger=self._logger(),
                run_id="job-resume-naked-html",
            )
            page = _FakePage()
            fake_context = _FakeAsyncPlaywrightContext(object())

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    side_effect=AssertionError("list API must not be called"),
                ),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_FakeBrowser(page),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "sz-save-json",
                            "project_code": "SZSAVEJSON",
                            "project_name": "save json",
                            "page_url": "https://sz.example/detail/save-json",
                            "disclosure_start": "2026-01-10",
                            "row": {},
                        }
                    ],
                )

            self.assertEqual(summary.skipped_by_resume, 0)
            self.assertEqual(summary.saved, 1)
            marker_path = Path(f"{html_path}.peap-save-status.json")
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(marker["save_status"], "complete")

    def test_resume_rejects_invalid_shell_evidence_even_with_complete_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = Path(tmp_dir) / "2026年1月" / "SZSAVEJSON-save json.html"
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text("<html><body>invalid shell html</body></html>", encoding="utf-8")
            Path(f"{html_path}.peap-save-status.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "task_id": "shenzhen:listing:equity_transfer",
                        "source_id": "shenzhen",
                        "business_id": "equity_transfer",
                        "save_status": "complete",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            Path(f"{html_path}.peap-evidence.json").write_text(
                json.dumps({"schema_version": 1, "page_kind": "invalid_shell"}),
                encoding="utf-8",
            )
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=True,
                save_json=False,
                logger=self._logger(),
                run_id="job-resume-invalid-shell",
            )
            page = _FakePage()
            fake_context = _FakeAsyncPlaywrightContext(object())

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    side_effect=AssertionError("list API must not be called"),
                ),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_FakeBrowser(page),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "sz-save-json",
                            "project_code": "SZSAVEJSON",
                            "project_name": "save json",
                            "page_url": "https://sz.example/detail/save-json",
                            "disclosure_start": "2026-01-10",
                            "row": {},
                        }
                    ],
                )

            self.assertEqual(summary.skipped_by_resume, 0)
            self.assertEqual(summary.saved, 1)

    def test_resume_rejects_complete_marker_with_task_id_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = Path(tmp_dir) / "2026年1月" / "SZSAVEJSON-save json.html"
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text("<html><body>different task artifact</body></html>", encoding="utf-8")
            Path(f"{html_path}.peap-save-status.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "task_id": "shenzhen:listing:capital_increase",
                        "source_id": "shenzhen",
                        "business_id": "capital_increase",
                        "save_status": "complete",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=True,
                save_json=False,
                logger=self._logger(),
                run_id="job-resume-task-mismatch",
            )
            page = _FakePage()
            fake_context = _FakeAsyncPlaywrightContext(object())

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    side_effect=AssertionError("list API must not be called"),
                ),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_FakeBrowser(page),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "sz-save-json",
                            "project_code": "SZSAVEJSON",
                            "project_name": "save json",
                            "page_url": "https://sz.example/detail/save-json",
                            "disclosure_start": "2026-01-10",
                            "row": {},
                        }
                    ],
                )

            self.assertEqual(summary.skipped_by_resume, 0)
            self.assertEqual(summary.saved, 1)

    def test_shandong_invalid_shell_snapshot_writes_safe_artifact_evidence_metadata(self) -> None:
        shell_marker = bytes(
            (95, 95, 106, 115, 108, 95, 99, 108, 101, 97, 114, 97, 110, 99, 101, 95, 115)
        ).decode("ascii")
        page_url = "https://sd.example/detail/sd-shell"
        project_code = "SD-SHELL-001"
        project_name = "Shandong shell fixture"
        rendered_html = f"<html><body><script>{shell_marker}=1</script><p>{project_code}</p></body></html>"

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShandongEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=self._logger(),
                run_id="job-shell",
            )
            page = _FakePage(rendered_html)
            fake_context = _FakeAsyncPlaywrightContext(object())
            with (
                patch.object(
                    downloader,
                    "_get_text",
                    side_effect=AssertionError("list API must not be called"),
                ),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_FakeBrowser(page),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "sd-shell",
                            "project_code": project_code,
                            "project_name": project_name,
                            "page_url": page_url,
                            "disclosure_start": "2026-01-10",
                            "row": {},
                        }
                    ],
                )

            html_path = next(Path(tmp_dir).rglob("*.html"))
            saved_html = html_path.read_text(encoding="utf-8")
            evidence_path = Path(f"{html_path}.peap-evidence.json")
            evidence_text = evidence_path.read_text(encoding="utf-8")
            evidence = json.loads(evidence_text)

        self.assertEqual(summary.saved, 1)
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["page_kind"], "invalid_shell")
        self.assertEqual(evidence["source_url_hash"], "sha256:" + hashlib.sha256(page_url.encode("utf-8")).hexdigest())
        self.assertEqual(evidence["final_url_hash"], evidence["source_url_hash"])
        self.assertEqual(
            evidence["content_sha256"],
            "sha256:" + hashlib.sha256(saved_html.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["identity_hints"]["project_code_hash"],
            "sha256:" + hashlib.sha256(project_code.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            evidence["identity_hints"]["project_name_hash"],
            "sha256:" + hashlib.sha256(project_name.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn(page_url, evidence_text)
        self.assertNotIn(shell_marker, evidence_text)
        self.assertNotIn(saved_html, evidence_text)

    def test_detail_download_uses_project_playwright_runtime_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=True,
                logger=self._logger(),
                run_id="job-playwright",
            )
            page = _FakePage()
            fake_playwright = object()
            fake_context = _FakeAsyncPlaywrightContext(fake_playwright)
            captured_playwrights: list[object] = []

            async def fake_launch(playwright, *, headless: bool):
                captured_playwrights.append(playwright)
                self.assertTrue(headless)
                return _FakeBrowser(page)

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    side_effect=AssertionError("list API must not be called"),
                ),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    create=True,
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    side_effect=fake_launch,
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "sz-playwright-1",
                            "project_code": "SZPW001",
                            "project_name": "playwright",
                            "page_url": "https://sz.example/detail/playwright",
                            "disclosure_start": "2026-01-10",
                            "row": {},
                        }
                    ],
                )

            self.assertEqual(summary.saved, 1)
            self.assertEqual(captured_playwrights, [fake_playwright])
            self.assertTrue(fake_context.entered)
            self.assertTrue(fake_context.exited)

    def test_item_saved_callback_failure_marks_detail_failed_without_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            logger = Mock()
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=False,
                logger=logger,
                item_saved_callback=Mock(side_effect=RuntimeError("callback boom")),
                run_id="job-callback-failure",
            )
            page = _FakePage()
            fake_context = _FakeAsyncPlaywrightContext(object())

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    side_effect=AssertionError("list API must not be called"),
                ),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_FakeBrowser(page),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "sz-callback-1",
                            "project_code": "SZCB001",
                            "project_name": "callback",
                            "page_url": "https://sz.example/detail/callback",
                            "disclosure_start": "2026-01-10",
                            "row": {},
                        }
                    ],
                )

            self.assertEqual(summary.saved, 0)
            self.assertEqual(summary.detail_failed, 1)
            self.assertEqual(len(summary.typed_errors), 1)
            self.assertEqual(summary.typed_errors[0].failure_kind, "save")
            self.assertIn("callback boom", summary.typed_errors[0].raw_reason)
            self.assertEqual(summary.downloaded_this_run, set())
            self.assertEqual(len(list(Path(tmp_dir).rglob("*.html"))), 1)
            logger.warning.assert_not_called()

    def test_callback_failure_artifact_is_not_resume_complete_on_next_run(self) -> None:
        for save_json in (False, True):
            with self.subTest(save_json=save_json), tempfile.TemporaryDirectory() as tmp_dir:
                candidate = {
                    "id": "sz-callback-resume",
                    "project_code": "SZCB001",
                    "project_name": "callback",
                    "page_url": "https://sz.example/detail/callback",
                    "disclosure_start": "2026-01-10",
                    "row": {},
                }
                first_downloader = ShenzhenEquityTransferDownloader(
                    html_root=tmp_dir,
                    page_size=20,
                    max_pages=1,
                    concurrency=1,
                    resume=False,
                    save_json=save_json,
                    logger=self._logger(),
                    item_saved_callback=Mock(side_effect=RuntimeError("callback boom")),
                    run_id=f"job-callback-resume-first-{save_json}",
                )
                first_context = _FakeAsyncPlaywrightContext(object())

                with (
                    patch.object(
                        first_downloader,
                        "_post_json",
                        side_effect=AssertionError("list API must not be called"),
                    ),
                    patch(
                        "peap.downloaders.listing_exchanges.async_playwright",
                        return_value=first_context,
                    ),
                    patch(
                        "peap.downloaders.listing_exchanges.launch_chromium_browser",
                        return_value=_FakeBrowser(_FakePage()),
                    ),
                ):
                    first_summary = first_downloader.run(
                        start_date="2026-01-01",
                        end_date="2026-01-31",
                        list_only=False,
                        prefetched_candidates=[candidate],
                    )

                self.assertEqual(first_summary.saved, 0)
                self.assertEqual(first_summary.detail_failed, 1)
                self.assertEqual(len(list(Path(tmp_dir).rglob("*.html"))), 1)

                callback = Mock()
                second_downloader = ShenzhenEquityTransferDownloader(
                    html_root=tmp_dir,
                    page_size=20,
                    max_pages=1,
                    concurrency=1,
                    resume=True,
                    save_json=save_json,
                    logger=self._logger(),
                    item_saved_callback=callback,
                    run_id=f"job-callback-resume-second-{save_json}",
                )
                second_context = _FakeAsyncPlaywrightContext(object())

                with (
                    patch.object(
                        second_downloader,
                        "_post_json",
                        side_effect=AssertionError("list API must not be called"),
                    ),
                    patch(
                        "peap.downloaders.listing_exchanges.async_playwright",
                        return_value=second_context,
                    ),
                    patch(
                        "peap.downloaders.listing_exchanges.launch_chromium_browser",
                        return_value=_FakeBrowser(_FakePage()),
                    ),
                ):
                    second_summary = second_downloader.run(
                        start_date="2026-01-01",
                        end_date="2026-01-31",
                        list_only=False,
                        prefetched_candidates=[candidate],
                    )

                self.assertEqual(second_summary.skipped_by_resume, 0)
                self.assertEqual(second_summary.saved, 1)
                callback.assert_called_once()

    def test_complete_sidecar_failure_does_not_emit_item_saved_callback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            callback = Mock()
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=True,
                logger=self._logger(),
                item_saved_callback=callback,
                run_id="job-complete-sidecar-failure",
            )
            fake_context = _FakeAsyncPlaywrightContext(object())

            def fail_complete_json(_path: str, payload: dict[str, object]) -> None:
                if payload.get("save_status") == "complete":
                    raise OSError("complete sidecar write failed")

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    side_effect=AssertionError("list API must not be called"),
                ),
                patch.object(downloader, "_write_json", side_effect=fail_complete_json),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_FakeBrowser(_FakePage()),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "sz-complete-sidecar-failure",
                            "project_code": "SZCB001",
                            "project_name": "callback",
                            "page_url": "https://sz.example/detail/callback",
                            "disclosure_start": "2026-01-10",
                            "row": {},
                        }
                    ],
                )

        callback.assert_not_called()
        self.assertEqual(summary.saved, 0)
        self.assertEqual(summary.detail_failed, 1)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "save")
        self.assertIn("complete sidecar write failed", summary.typed_errors[0].raw_reason)

    def test_fetch_rendered_html_waits_for_repeated_stable_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                logger=self._logger(),
            )
            page = _ChangingPage(
                [
                    "<html><body>first</body></html>",
                    "<html><body>second</body></html>",
                    RAW_DETAIL_HTML,
                    RAW_DETAIL_HTML,
                ]
            )

            html, http_status = asyncio.run(
                downloader._fetch_rendered_html(
                    page=page, page_url="https://sz.example/detail/stable"
                )
            )

            self.assertEqual(html, RAW_DETAIL_HTML)
            self.assertEqual(http_status, 200)
            self.assertEqual(page.content_calls, 4)

    def test_fetch_rendered_html_rejects_page_without_expected_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShandongEquityTransferDownloader(html_root=tmp_dir, logger=self._logger())
            page = _FakePage(
                "<html><body><h1>山东产权交易中心</h1>"
                "<div>项目编号：G32026SD9999999</div>"
                "<div>项目名称：其他股权项目</div></body></html>"
            )

            with self.assertRaisesRegex(RuntimeError, "detail-page-mismatch"):
                asyncio.run(
                    downloader._fetch_rendered_html(
                        page=page,
                        page_url="http://www.sdcqjy.com/proj/tc/12345",
                        expected_project_code="G32026SD1000001",
                        expected_project_name="目标股权项目",
                    )
                )

    def test_fetch_rendered_html_waits_past_stable_shell_until_expected_identity_appears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShandongEquityTransferDownloader(html_root=tmp_dir, logger=self._logger())
            page = _ChangingPage(
                [
                    "<html><body><noscript>JavaScript required</noscript><div id='app'></div></body></html>",
                    "<html><body><noscript>JavaScript required</noscript><div id='app'></div></body></html>",
                    "<html><body><h1>广东联合产权交易中心</h1>"
                    "<div>项目编号：G32025GD0000294</div>"
                    "<div>项目名称：上海上电石化有限公司50%股权</div></body></html>",
                ]
            )

            (rendered_html, http_status) = asyncio.run(
                downloader._fetch_rendered_html(
                    page=page,
                    page_url="https://new.gduaee.com/xmzx.html#/equityDetail?XMID=158351",
                    expected_project_code="G32025GD0000294",
                    expected_project_name="上海上电石化有限公司50%股权",
                )
            )

            self.assertIn("G32025GD0000294", rendered_html)
            self.assertEqual(http_status, 200)
            self.assertEqual(page.content_calls, 3)

    def test_fetch_rendered_html_retries_transient_detail_navigation_timeout(self) -> None:
        class RetryPage(_FakePage):
            def __init__(self) -> None:
                super().__init__("<html><body><div>项目名称：目标增资项目</div></body></html>")
                self.goto_calls = 0

            async def goto(self, *args, **kwargs):
                self.goto_calls += 1
                if self.goto_calls == 1:
                    raise RuntimeError("Page.goto: net::ERR_TIMED_OUT")
                return SimpleNamespace(status=200)

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShandongEquityTransferDownloader(html_root=tmp_dir, logger=self._logger())
            page = RetryPage()

            rendered_html, http_status = asyncio.run(
                downloader._fetch_rendered_html(
                    page=page,
                    page_url="https://www.sotcbb.com/bdDetail.htm?contentId=demo",
                    expected_project_code="DISCLOSURE_demo",
                    expected_project_name="目标增资项目",
                )
            )

            self.assertEqual(page.goto_calls, 2)
            self.assertIn("目标增资项目", rendered_html)
            self.assertEqual(http_status, 200)

    def test_shenzhen_detail_waits_for_commit_and_attached_body(self) -> None:
        class TrackingPage(_FakePage):
            def __init__(self) -> None:
                super().__init__("<html><body>SZCOMMIT001 深圳提交页面</body></html>")
                self.goto_kwargs: dict[str, object] = {}
                self.selector_kwargs: dict[str, object] = {}

            async def goto(self, url: str, **kwargs):
                self.urls.append(url)
                self.goto_kwargs = kwargs
                return SimpleNamespace(status=200)

            async def wait_for_selector(self, selector: str, **kwargs) -> None:
                self.selector_kwargs = {"selector": selector, **kwargs}

        downloader = ShenzhenEquityTransferDownloader(html_root="/tmp", timeout=8)
        page = TrackingPage()

        html, status = asyncio.run(
            downloader._fetch_rendered_html(
                page=page,
                page_url="https://www.sotcbb.com/bdDetail.htm?contentId=demo",
                expected_project_code="SZCOMMIT001",
                expected_project_name="深圳提交页面",
            )
        )

        self.assertEqual(status, 200)
        self.assertIn("SZCOMMIT001", html)
        self.assertEqual(page.goto_kwargs["wait_until"], "commit")
        self.assertEqual(page.selector_kwargs["selector"], "body")
        self.assertEqual(page.selector_kwargs["state"], "attached")

    def test_guangdong_detail_waits_for_commit_and_attached_body(self) -> None:
        class TrackingPage(_FakePage):
            def __init__(self) -> None:
                super().__init__("<html><body>GD_COMMIT001 广东提交页面</body></html>")
                self.goto_kwargs: dict[str, object] = {}
                self.selector_kwargs: dict[str, object] = {}

            async def goto(self, url: str, **kwargs):
                self.urls.append(url)
                self.goto_kwargs = kwargs
                return SimpleNamespace(status=200)

            async def wait_for_selector(self, selector: str, **kwargs) -> None:
                self.selector_kwargs = {"selector": selector, **kwargs}

        downloader = GuangdongEquityTransferDownloader(html_root="/tmp", timeout=8)
        page = TrackingPage()

        html, status = asyncio.run(
            downloader._fetch_rendered_html(
                page=page,
                page_url="https://new.gduaee.com/xmzx.html#/equityDetail?XMID=demo",
                expected_project_code="GD_COMMIT001",
                expected_project_name="广东提交页面",
            )
        )

        self.assertEqual(status, 200)
        self.assertIn("GD_COMMIT001", html)
        self.assertEqual(page.goto_kwargs["wait_until"], "commit")
        self.assertEqual(page.selector_kwargs["selector"], "body")
        self.assertEqual(page.selector_kwargs["state"], "attached")

    def test_unstable_rendered_html_fails_without_archiving_intermediate_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = ShenzhenEquityTransferDownloader(
                html_root=tmp_dir,
                page_size=20,
                max_pages=1,
                concurrency=1,
                resume=False,
                save_json=True,
                logger=self._logger(),
                run_id="job-unstable",
            )
            page = _ChangingPage(
                [
                    "<html><body>first</body></html>",
                    "<html><body>second</body></html>",
                    "<html><body>third</body></html>",
                    "<html><body>fourth</body></html>",
                    "<html><body>fifth</body></html>",
                ]
            )
            fake_context = _FakeAsyncPlaywrightContext(object())

            with (
                patch.object(
                    downloader,
                    "_post_json",
                    side_effect=AssertionError("list API must not be called"),
                ),
                patch(
                    "peap.downloaders.listing_exchanges.async_playwright",
                    return_value=fake_context,
                ),
                patch(
                    "peap.downloaders.listing_exchanges.launch_chromium_browser",
                    return_value=_FakeBrowser(page),
                ),
            ):
                summary = downloader.run(
                    start_date="2026-01-01",
                    end_date="2026-01-31",
                    list_only=False,
                    prefetched_candidates=[
                        {
                            "id": "sz-unstable-1",
                            "project_code": "SZUN001",
                            "project_name": "unstable",
                            "page_url": "https://sz.example/detail/unstable",
                            "disclosure_start": "2026-01-10",
                            "row": {},
                        }
                    ],
                )

            self.assertEqual(summary.saved, 0)
            self.assertEqual(summary.detail_failed, 1)
            self.assertEqual(list(Path(tmp_dir).rglob("*.html")), [])


if __name__ == "__main__":
    unittest.main()
