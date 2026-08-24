from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from peap.download_archive_audit import audit_download_archive_root
from peap.downloaders.cbex_physical import CbexPhysicalAssetDownloader
from peap.downloaders.cquae import ChongqingProjectDownloader
from peap.downloaders.listing_exchanges import (
    GuangdongEquityTransferDownloader,
    ShandongEquityTransferDownloader,
    ShenzhenEquityTransferDownloader,
)
from peap.downloaders.sse_physical import ShanghaiPhysicalAssetDownloader
from peap.downloaders.tpre import TpreProjectDownloader


class _RenderedPage:
    def __init__(self, *, html: str, status: int = 206, title: str = "detail") -> None:
        self.html = html
        self.status = status
        self._title = title
        self.url = ""

    async def goto(self, url: str, **_kwargs):
        self.url = url
        return SimpleNamespace(status=self.status)

    async def wait_for_selector(self, *_args, **_kwargs) -> None:
        return None

    async def wait_for_function(self, *_args, **_kwargs) -> None:
        return None

    async def wait_for_timeout(self, *_args, **_kwargs) -> None:
        return None

    async def content(self) -> str:
        return self.html

    async def title(self) -> str:
        return self._title


class ListingArchiveProvenanceTest(unittest.TestCase):
    def test_tpre_rendered_fetch_returns_actual_navigation_status(self) -> None:
        downloader = TpreProjectDownloader(html_root="/tmp/test")
        page = _RenderedPage(html="<html><body>T32026TJ000001 天津项目</body></html>", status=206)

        html, http_status = asyncio.run(
            downloader._fetch_rendered_html(
                page=page,
                page_url="https://trade.tpre.cn/project/T32026TJ000001",
                expected_project_code="T32026TJ000001",
                expected_project_name="天津项目",
            )
        )

        self.assertIn("T32026TJ000001", html)
        self.assertEqual(http_status, 206)

    def test_cquae_rendered_fetch_returns_detail_navigation_status(self) -> None:
        downloader = ChongqingProjectDownloader(html_root="/tmp/test")
        page = _RenderedPage(html="<html><body>重庆项目详情</body></html>", status=207)
        candidate = SimpleNamespace(
            list_url="https://www.cquae.com/project/list",
            page_url="https://www.cquae.com/project/detail/1",
            project_name="重庆项目",
            project_id="1",
        )

        with patch.object(downloader, "_is_real_detail_page", return_value=True):
            html, final_url, http_status = asyncio.run(
                downloader._fetch_rendered_html(page=page, candidate=candidate)
            )

        self.assertIn("重庆项目详情", html)
        self.assertEqual(final_url, candidate.page_url)
        self.assertEqual(http_status, 207)

    def test_sse_rendered_fetch_returns_actual_navigation_status(self) -> None:
        downloader = ShanghaiPhysicalAssetDownloader(html_root="/tmp/test")
        page = _RenderedPage(html="<html><body>SH001 上海项目</body></html>", status=208)

        with patch.object(downloader, "_is_real_detail_page", return_value=True):
            html, http_status = asyncio.run(
                downloader._fetch_rendered_html(
                    page=page,
                    page_url="https://www.suaee.com/project/SH001",
                    expected_project_code="SH001",
                )
            )

        self.assertIn("SH001", html)
        self.assertEqual(http_status, 208)

    def test_cbex_rendered_fetch_returns_actual_navigation_status(self) -> None:
        downloader = CbexPhysicalAssetDownloader(html_root="/tmp/test")
        page = _RenderedPage(html="<html><body>项目编号 BJ001</body></html>", status=206)

        html, http_status = asyncio.run(
            downloader._fetch_html(
                page=page,
                url="https://www.cbex.com.cn/project/BJ001",
                code="BJ001",
            )
        )

        self.assertIn("BJ001", html)
        self.assertEqual(http_status, 206)

    def test_plain_html_status_sidecars_store_provenance_for_all_listing_downloaders(self) -> None:
        source_url = "https://exchange.example/project/P001"
        cases = (
            (TpreProjectDownloader, "positional", "tpre", "equity_transfer"),
            (ChongqingProjectDownloader, "positional", "cquae", "equity_transfer"),
            (ShanghaiPhysicalAssetDownloader, "keyword", "sse", "physical_asset"),
            (CbexPhysicalAssetDownloader, "keyword", "cbex", "physical_asset"),
            (ShandongEquityTransferDownloader, "positional", "shandong", "equity_transfer"),
            (GuangdongEquityTransferDownloader, "positional", "guangdong", "equity_transfer"),
            (ShenzhenEquityTransferDownloader, "positional", "shenzhen", "equity_transfer"),
        )
        for downloader_type, calling_style, source_id, business_id in cases:
            with self.subTest(downloader=downloader_type.__name__), tempfile.TemporaryDirectory() as temp_dir:
                task_root = Path(temp_dir) / f"{source_id}__listing__{business_id}"
                task_root.mkdir()
                html_path = task_root / "P001.html"
                html_path.write_text("<html><body>P001</body></html>", encoding="utf-8")
                downloader = downloader_type(html_root=str(task_root))
                if calling_style == "positional":
                    downloader._write_resume_status(
                        str(html_path),
                        "complete",
                        source_url=source_url,
                        http_status=206,
                    )
                else:
                    downloader._write_resume_status(
                        html_path=str(html_path),
                        save_status="complete",
                        source_url=source_url,
                        http_status=206,
                    )

                marker = json.loads(
                    Path(f"{html_path}.peap-save-status.json").read_text(encoding="utf-8")
                )
                self.assertEqual(marker["source_url"], source_url)
                self.assertEqual(marker["http_status"], 206)
                self.assertEqual(marker["task_id"], f"{source_id}:listing:{business_id}")
                self.assertEqual(marker["source_id"], source_id)
                self.assertEqual(marker["record_family"], "listing")
                self.assertEqual(marker["business_id"], business_id)
                self.assertEqual(marker["save_status"], "complete")
                self.assertEqual(
                    marker["archive_content_sha256"],
                    "sha256:" + hashlib.sha256(html_path.read_bytes()).hexdigest(),
                )
                self.assertEqual(marker["archive_content_bytes"], html_path.stat().st_size)
                audit = audit_download_archive_root(temp_dir)
                self.assertTrue(audit.ok, [issue.to_dict() for issue in audit.issues])


if __name__ == "__main__":
    unittest.main()
