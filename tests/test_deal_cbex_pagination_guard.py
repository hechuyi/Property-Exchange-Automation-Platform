from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from peap.downloaders.deal_cbex import CbexDealEquityTransferDownloader


class CbexDealPaginationGuardTest(unittest.TestCase):
    def test_max_pages_reports_discovered_page_that_would_be_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealEquityTransferDownloader(
                html_root=temp_dir,
                max_pages=1,
            )
            first_page = "https://example.test/deals"
            second_page = "https://example.test/deals?page=2"
            row = {
                "source_url": (
                    "https://www.cbex.com.cn/xm/cqzr/2026/08/15/"
                    "G32026BJSMOKE1.html"
                ),
                "project_code": "G32026BJSMOKE1",
                "project_name": "pagination guard fixture",
                "deal_date": "2026-08-15",
            }

            with (
                patch.object(downloader, "_initial_list_page_urls", return_value=[first_page]),
                patch.object(downloader, "_fetch_list_html", return_value="<html></html>"),
                patch.object(downloader, "_extract_list_rows", return_value=[row]),
                patch.object(downloader, "_list_page_failure_reason", return_value=""),
                patch.object(
                    downloader,
                    "_extract_pagination_urls",
                    return_value=[second_page],
                ),
            ):
                summary = downloader.run(
                    start_date=None,
                    end_date=None,
                    list_only=True,
                )

        self.assertEqual(summary.pages_requested, 1)
        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].failure_kind, "list")
        self.assertIn("explicit-max-pages-truncates-discovery", summary.typed_errors[0].raw_reason)

    def test_textarea_source_without_page_count_does_not_synthesize_next_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealEquityTransferDownloader(
                html_root=temp_dir,
                max_pages=1,
            )
            row = {
                "source_url": (
                    "https://www.cbex.com.cn/xm/cqzr/2026/08/15/"
                    "G32026BJSMOKE2.html"
                ),
                "project_code": "G32026BJSMOKE2",
                "project_name": "single page fixture",
                "deal_date": "2026-08-15",
            }
            html = '<textarea class="source">official payload</textarea>'

            with (
                patch.object(
                    downloader,
                    "_initial_list_page_urls",
                    return_value=["https://example.test/deals"],
                ),
                patch.object(downloader, "_fetch_list_html", return_value=html),
                patch.object(downloader, "_extract_list_rows", return_value=[row]),
            ):
                summary = downloader.run(
                    start_date=None,
                    end_date=None,
                    list_only=True,
                )

        self.assertEqual(summary.pages_requested, 1)
        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(summary.typed_errors, [])

    def test_explicit_static_page_count_still_reports_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealEquityTransferDownloader(
                html_root=temp_dir,
                max_pages=1,
            )
            row = {
                "source_url": (
                    "https://www.cbex.com.cn/xm/cqzr/2026/08/15/"
                    "G32026BJSMOKE3.html"
                ),
                "project_code": "G32026BJSMOKE3",
                "project_name": "multi page fixture",
                "deal_date": "2026-08-15",
            }
            html = (
                '<script>var currentPage = 0; var countPage = 2;</script>'
                '<textarea class="source">official payload</textarea>'
            )

            with (
                patch.object(
                    downloader,
                    "_initial_list_page_urls",
                    return_value=["https://www.cbex.com.cn/xm/cqzr/cjjggs/"],
                ),
                patch.object(downloader, "_fetch_list_html", return_value=html),
                patch.object(downloader, "_extract_list_rows", return_value=[row]),
            ):
                summary = downloader.run(
                    start_date=None,
                    end_date=None,
                    list_only=True,
                )

        self.assertEqual(summary.pages_requested, 1)
        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertIn("explicit-max-pages-truncates-discovery", summary.typed_errors[0].raw_reason)

    def test_single_static_page_count_does_not_probe_synthetic_next_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = CbexDealEquityTransferDownloader(
                html_root=temp_dir,
                max_pages=1,
            )
            row = {
                "source_url": (
                    "https://www.cbex.com.cn/xm/cqzr/2026/08/15/"
                    "G32026BJSMOKE4.html"
                ),
                "project_code": "G32026BJSMOKE4",
                "project_name": "singleton static page fixture",
                "deal_date": "2026-08-15",
            }
            html = (
                '<script>var currentPage = 0; var countPage = 1;</script>'
                '<textarea class="source">official payload</textarea>'
            )
            fetched_urls: list[str] = []

            def fake_fetch(url: str) -> str:
                fetched_urls.append(url)
                return html

            with (
                patch.object(
                    downloader,
                    "_initial_list_page_urls",
                    return_value=["https://www.cbex.com.cn/xm/cqzr/cjjggs/"],
                ),
                patch.object(downloader, "_fetch_list_html", side_effect=fake_fetch),
                patch.object(downloader, "_extract_list_rows", return_value=[row]),
            ):
                summary = downloader.run(
                    start_date=None,
                    end_date=None,
                    list_only=True,
                )

        self.assertEqual(summary.pages_requested, 1)
        self.assertEqual(summary.detail_candidates, 1)
        self.assertEqual(summary.typed_errors, [])
        self.assertEqual(fetched_urls, ["https://www.cbex.com.cn/xm/cqzr/cjjggs/"])


if __name__ == "__main__":
    unittest.main()
