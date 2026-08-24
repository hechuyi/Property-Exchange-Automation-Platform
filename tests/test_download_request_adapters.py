from __future__ import annotations

import argparse
import tempfile
import unittest
from types import SimpleNamespace

from peap.download_runner import build_download_run_request


class DownloadRequestAdaptersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = SimpleNamespace(
            DOWNLOADER_DEFAULTS={
                "exchange": "all",
                "project_type": "all",
                "concurrency": 2,
                "resume": True,
                "save_json": False,
                "auto_split": False,
                "split_candidates": 10,
                "split_min_days": 1,
                "split_max_depth": 3,
                "split_mode": "fast",
                "sse_ssl_verify": True,
                "sse_ca_bundle": None,
            },
        )

    def test_build_download_run_request_uses_family_business_scope(self) -> None:
        args = argparse.Namespace(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            list_tasks=False,
            output_root="C:\\temp\\auto_html",
            force_manual_root=False,
            start_date="2026-01-01",
            end_date="2026-01-02",
            page_size=25,
            max_pages=6,
            concurrency=4,
            resume=False,
            save_json=True,
            sse_ssl_verify=False,
            sse_ca_bundle="C:\\temp\\ca.pem",
            log_dir="C:\\temp\\logs",
            log_file="C:\\temp\\logs\\download.log",
            verbose=True,
            auto_split=False,
            split_candidates=12,
            split_min_days=2,
            split_max_depth=4,
            split_plan_only=False,
            split_plan_file=None,
            split_use_plan=False,
            split_mode="steady",
            chunk_state_file="C:\\temp\\state.json",
        )

        request = build_download_run_request(args, config_obj=self.config)

        self.assertEqual(request.exchange, "sse")
        self.assertEqual(request.record_family, "listing")
        self.assertEqual(request.business_id, "physical_asset")
        self.assertFalse(hasattr(request, "project_type"))

    def test_build_download_run_request_requires_downloader_defaults_config_contract(self) -> None:
        with self.assertRaisesRegex(AttributeError, "DOWNLOADER_DEFAULTS"):
            build_download_run_request(argparse.Namespace(), config_obj=SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
