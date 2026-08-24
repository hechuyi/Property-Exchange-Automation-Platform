from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from peap.downloaders.common import HttpFetchedText
from peap.downloaders.deal_cbex import CbexDealEquityTransferDownloader
from peap.downloaders.deal_cquae import ChongqingDealEquityTransferDownloader


class _HtmlResponse:
    def __init__(self, *, html: str, status: int, final_url: str) -> None:
        self._raw = html.encode("utf-8")
        self.status = status
        self._final_url = final_url
        self.headers = SimpleNamespace(get_content_charset=lambda: "utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self._raw

    def geturl(self) -> str:
        return self._final_url


class DealCbexCquaeArchiveProvenanceTest(unittest.TestCase):
    def test_cbex_direct_detail_fetch_keeps_actual_status_and_final_url(self) -> None:
        source_url = "https://www.cbex.com.cn/project/P001"
        response = _HtmlResponse(
            html="<html><body>P001 北京成交</body></html>",
            status=206,
            final_url="https://www.cbex.com.cn/project/P001?resolved=1",
        )
        downloader = CbexDealEquityTransferDownloader(html_root="/tmp/test")

        with patch("urllib.request.urlopen", return_value=response):
            fetched = downloader._fetch_detail_html(source_url)

        self.assertIsInstance(fetched, HttpFetchedText)
        self.assertEqual(fetched.source_url, source_url)
        self.assertEqual(fetched.final_url, response.geturl())
        self.assertEqual(fetched.http_status, 206)

    def test_cquae_direct_detail_fetch_keeps_actual_status_and_final_url(self) -> None:
        source_url = "https://www.cquae.com/project/P001"
        response = _HtmlResponse(
            html="<html><body>P001 重庆成交</body></html>",
            status=207,
            final_url="https://www.cquae.com/project/P001?resolved=1",
        )
        downloader = ChongqingDealEquityTransferDownloader(html_root="/tmp/test")

        with patch("urllib.request.urlopen", return_value=response):
            fetched = downloader._fetch_detail_html(source_url)

        self.assertIsInstance(fetched, HttpFetchedText)
        self.assertEqual(fetched.source_url, source_url)
        self.assertEqual(fetched.final_url, response.geturl())
        self.assertEqual(fetched.http_status, 207)

    def test_cbex_and_cquae_sidecars_persist_transport_evidence(self) -> None:
        source_url = "https://exchange.example/project/P001"
        final_url = "https://exchange.example/project/P001?resolved=1"
        cases = (
            (CbexDealEquityTransferDownloader, "cbex"),
            (ChongqingDealEquityTransferDownloader, "cquae"),
        )
        for downloader_type, source_id in cases:
            with self.subTest(downloader=downloader_type.__name__), tempfile.TemporaryDirectory() as temp_dir:
                json_path = Path(temp_dir) / "P001.json"
                downloader = downloader_type(html_root=temp_dir)
                metadata = {
                    "task_id": f"{source_id}:deal:deal_equity_transfer",
                    "source_id": source_id,
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                }
                downloader._write_sidecar_json(
                    json_path=str(json_path),
                    metadata=metadata,
                    detail_url=source_url,
                    detail_payload={},
                    save_status="complete",
                    source_url=source_url,
                    final_url=final_url,
                    http_status=206,
                )

                payload = json.loads(json_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["source_url"], source_url)
                self.assertEqual(payload["final_url"], final_url)
                self.assertEqual(payload["http_status"], 206)
                self.assertEqual(payload["task_id"], metadata["task_id"])
                self.assertEqual(payload["source_id"], source_id)
                self.assertEqual(payload["record_family"], "deal")
                self.assertEqual(payload["business_id"], "deal_equity_transfer")


if __name__ == "__main__":
    unittest.main()
