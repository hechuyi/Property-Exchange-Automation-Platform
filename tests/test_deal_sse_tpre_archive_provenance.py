from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from peap.downloaders.common import HttpFetchedText
from peap.downloaders.deal_sse import ShanghaiDealPhysicalAssetDownloader
from peap.downloaders.deal_tpre import TianjinDealEquityTransferDownloader


class _RenderedPage:
    def __init__(
        self,
        *,
        html: str,
        response_status: int | None,
        response_url: str = "https://example.test/final",
    ) -> None:
        self.html = html
        self.response_status = response_status
        self.response_url = response_url
        self.content_calls = 0

    async def goto(self, _url: str, **_kwargs):
        if self.response_status is None:
            return None
        return SimpleNamespace(status=self.response_status, url=self.response_url)

    async def wait_for_selector(self, *_args, **_kwargs) -> None:
        return None

    async def wait_for_timeout(self, *_args, **_kwargs) -> None:
        return None

    async def content(self) -> str:
        self.content_calls += 1
        return self.html


class DealArchiveProvenanceTest(unittest.TestCase):
    SSE_URL = "https://www.suaee.com/jyxx.html#/xxggDetail?ID=31285&FCLASS=cjggSW&skipDateCheck=1"
    TPRE_URL = (
        "https://trade.tpre.cn/transaction-view/data/common/transaction-announcement"
        "?id=TPRE-ORIGINAL-001"
    )

    SSE_HTML = "<html><body>SSE deal GR2026SH1000563</body></html>"
    TPRE_HTML = "<html><body>TPRE deal G32026TJ1000001</body></html>"

    def test_rendered_fetches_return_actual_page_goto_status(self) -> None:
        cases = (
            (
                ShanghaiDealPhysicalAssetDownloader(html_root="/tmp/test"),
                self.SSE_URL,
                "GR2026SH1000563",
                self.SSE_HTML,
            ),
            (
                TianjinDealEquityTransferDownloader(html_root="/tmp/test"),
                self.TPRE_URL,
                "G32026TJ1000001",
                self.TPRE_HTML,
            ),
        )

        for downloader, source_url, project_code, rendered_html in cases:
            with self.subTest(downloader=type(downloader).__name__):
                final_url = source_url + "&redirected=1"
                page = _RenderedPage(
                    html=rendered_html,
                    response_status=206,
                    response_url=final_url,
                )
                with contextlib.ExitStack() as stack:
                    stack.enter_context(
                        patch.object(downloader, "_is_real_deal_detail_page", return_value=True)
                    )
                    if isinstance(downloader, TianjinDealEquityTransferDownloader):
                        stack.enter_context(
                            patch.object(
                                downloader,
                                "_select_result_tab_if_needed",
                                return_value=None,
                            )
                        )
                    result = asyncio.run(
                        downloader._fetch_rendered_html_from_page(
                            page=page,
                            page_url=source_url,
                            expected_project_code=project_code,
                            expected_project_name="Deal project",
                        )
                    )

                self.assertIsInstance(result, HttpFetchedText)
                self.assertEqual(str(result), rendered_html)
                self.assertEqual(result.source_url, source_url)
                self.assertEqual(result.final_url, final_url)
                self.assertEqual(result.http_status, 206)

    def test_rendered_fetches_reject_missing_or_non_2xx_navigation_response(self) -> None:
        cases = (
            (
                ShanghaiDealPhysicalAssetDownloader(html_root="/tmp/test"),
                self.SSE_URL,
                "GR2026SH1000563",
                self.SSE_HTML,
            ),
            (
                TianjinDealEquityTransferDownloader(html_root="/tmp/test"),
                self.TPRE_URL,
                "G32026TJ1000001",
                self.TPRE_HTML,
            ),
        )

        for downloader, source_url, project_code, rendered_html in cases:
            for response_status in (None, 503):
                with self.subTest(
                    downloader=type(downloader).__name__,
                    response_status=response_status,
                ):
                    page = _RenderedPage(
                        html=rendered_html,
                        response_status=response_status,
                    )
                    with contextlib.ExitStack() as stack:
                        stack.enter_context(
                            patch.object(
                                downloader,
                                "_is_real_deal_detail_page",
                                return_value=True,
                            )
                        )
                        if isinstance(downloader, TianjinDealEquityTransferDownloader):
                            stack.enter_context(
                                patch.object(
                                    downloader,
                                    "_select_result_tab_if_needed",
                                    return_value=None,
                                )
                            )
                        with self.assertRaises(ValueError):
                            asyncio.run(
                                downloader._fetch_rendered_html_from_page(
                                    page=page,
                                    page_url=source_url,
                                    expected_project_code=project_code,
                                    expected_project_name="Deal project",
                                )
                            )
                    self.assertEqual(page.content_calls, 0)

    def test_success_sidecars_store_render_navigation_provenance(self) -> None:
        for case in ("sse", "tpre"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp_dir:
                if case == "sse":
                    downloader = ShanghaiDealPhysicalAssetDownloader(html_root=temp_dir)
                    candidate = self._sse_candidate()
                    detail_payload = {"data": [{"XMBH": "GR2026SH1000563", "CJRQ": "2026-05-07"}]}
                    query_patch = patch.object(
                        downloader,
                        "_query_detail_payload",
                        return_value=detail_payload,
                    )
                    expected_html = self.SSE_HTML
                    expected_url = self.SSE_URL
                else:
                    downloader = TianjinDealEquityTransferDownloader(html_root=temp_dir)
                    candidate = self._tpre_candidate()
                    detail_payload = {
                        "data": {
                            "projectCode": "G32026TJ1000001",
                            "contractSignTime": "2026-03-31",
                        }
                    }
                    query_patch = patch.object(
                        downloader,
                        "_query_detail_payload",
                        return_value=detail_payload,
                    )
                    expected_html = self.TPRE_HTML
                    expected_url = self.TPRE_URL
                expected_final_url = expected_url + "&redirected=1"

                with (
                    query_patch,
                    patch.object(
                        downloader,
                        "_fetch_rendered_detail_html",
                        return_value=HttpFetchedText(
                            expected_html,
                            source_url=expected_url,
                            final_url=expected_final_url,
                            http_status=206,
                        ),
                    ),
                ):
                    summary = downloader.run(
                        start_date=None,
                        end_date=None,
                        prefetched_candidates=[candidate],
                    )

                self.assertEqual(summary.saved, 1)
                saved_path = Path(temp_dir) / next(iter(summary.downloaded_this_run))
                sidecar_path = saved_path.with_suffix(".json")
                self.assertTrue(saved_path.is_file())
                self.assertTrue(sidecar_path.is_file())
                sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                self.assertEqual(sidecar["save_status"], "complete")
                self.assertEqual(sidecar["source_url"], expected_url)
                self.assertEqual(sidecar["final_url"], expected_final_url)
                self.assertEqual(sidecar["http_status"], 206)
                self.assertEqual(sidecar["record_family"], "deal")
                self.assertEqual(sidecar["source_id"], "sse" if case == "sse" else "tpre")
                self.assertEqual(sidecar["business_id"], downloader.business_id)
                self.assertEqual(sidecar["task_id"], f"{sidecar['source_id']}:deal:{downloader.business_id}")
                self.assertEqual(
                    sidecar["archive_content_sha256"],
                    "sha256:" + hashlib.sha256(saved_path.read_bytes()).hexdigest(),
                )
                self.assertEqual(sidecar["archive_content_bytes"], saved_path.stat().st_size)

    def test_render_provenance_failure_is_not_counted_as_saved(self) -> None:
        for case in ("sse", "tpre"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp_dir:
                if case == "sse":
                    downloader = ShanghaiDealPhysicalAssetDownloader(html_root=temp_dir)
                    candidate = self._sse_candidate()
                    detail_payload = {"data": [{"CJRQ": "2026-05-07"}]}
                else:
                    downloader = TianjinDealEquityTransferDownloader(html_root=temp_dir)
                    candidate = self._tpre_candidate()
                    detail_payload = {"data": {"contractSignTime": "2026-03-31"}}

                with (
                    patch.object(
                        downloader,
                        "_query_detail_payload",
                        return_value=detail_payload,
                    ),
                    patch.object(
                        downloader,
                        "_fetch_rendered_detail_html",
                        side_effect=ValueError("missing navigation response"),
                    ),
                ):
                    summary = downloader.run(
                        start_date=None,
                        end_date=None,
                        prefetched_candidates=[candidate],
                    )

                self.assertEqual(summary.saved, 0)
                self.assertEqual(summary.detail_failed, 1)
                self.assertEqual(list(Path(temp_dir).rglob("*.html")), [])
                self.assertEqual(list(Path(temp_dir).rglob("*.json")), [])

    @classmethod
    def _sse_candidate(cls) -> dict[str, object]:
        return {
            "notice_id": "31285",
            "xmid": "31285",
            "project_code": "GR2026SH1000563",
            "project_name": "SSE deal",
            "source_url": cls.SSE_URL,
            "collection_date": "2026-05-08",
            "row": {
                "XMID": "31285",
                "XMBH": "GR2026SH1000563",
                "XMMC": "SSE deal",
                "FCLASS": "SW",
            },
        }

    @classmethod
    def _tpre_candidate(cls) -> dict[str, object]:
        return {
            "notice_id": "TPRE-ORIGINAL-001",
            "project_code": "G32026TJ1000001",
            "project_name": "TPRE deal",
            "source_url": cls.TPRE_URL,
            "collection_date": "2026-04-20",
            "row": {
                "id": "TPRE-ORIGINAL-001",
                "projectCode": "G32026TJ1000001",
                "projectName": "TPRE deal",
                "projectLink": cls.TPRE_URL,
            },
        }


if __name__ == "__main__":
    unittest.main()
