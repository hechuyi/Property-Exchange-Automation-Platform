from __future__ import annotations

import unittest
from types import SimpleNamespace

from peap.cli import build_parser as build_parser_cli
from peap.download_cli import build_parser as build_download_parser
from peap.download_tasks import (
    DownloadTaskRegistrySettings,
    get_default_download_task_registry_settings,
    set_default_download_task_registry_settings,
)


class CliConfigInjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_task_settings = get_default_download_task_registry_settings()
        self.addCleanup(
            lambda: set_default_download_task_registry_settings(self.original_task_settings)
        )

    def test_parser_cli_build_parser_uses_injected_config(self) -> None:
        config = SimpleNamespace(
            PARSER_DEFAULTS={
                "limit": 7,
                "batch_flush_interval": 11,
                "compat_profile": "ppe_ready",
                "progress_interval": 13,
                "compare_fields": ["project_name", "project_id"],
            },
            DATA_ROOT="C:\\temp\\data",
            HTML_FOLDER="C:\\temp\\manual_html",
            LOG_DIR="C:\\temp\\logs",
            PARSER_CACHE_DB="C:\\temp\\parse_cache.sqlite3",
        )

        parser = build_parser_cli(config)
        args = parser.parse_args([])

        self.assertEqual(args.limit, 7)
        self.assertEqual(args.batch_flush_interval, 11)
        self.assertEqual(args.parser_compat_profile, "ppe_ready")
        self.assertEqual(args.progress_interval, 13)
        self.assertEqual(args.log_dir, "C:\\temp\\logs")
        self.assertEqual(args.parse_cache_db, "C:\\temp\\parse_cache.sqlite3")

    def test_download_cli_build_parser_uses_injected_config(self) -> None:
        config = SimpleNamespace(
            AUTO_HTML_FOLDER="C:\\temp\\auto_html",
            HTML_FOLDER="C:\\temp\\manual_html",
            LOG_DIR="C:\\temp\\logs",
            DOWNLOADER_DEFAULTS={
                "exchange": "sse",
                "project_type": "physical_asset",
                "concurrency": 4,
                "resume": False,
                "save_json": True,
                "auto_split": True,
                "split_candidates": 9,
                "split_min_days": 2,
                "split_max_depth": 5,
                "split_mode": "steady",
                "sse_ssl_verify": False,
                "sse_ssl_fallback_insecure": False,
                "sse_ca_bundle": "C:\\temp\\ca.pem",
            },
            DOWNLOADER_TASK_PAGE_SIZE={
                "sse:physical_asset": 20,
                "cbex:physical_asset": 20,
                "sse:equity_transfer": 20,
                "sse:capital_increase": 20,
                "sse:pre_disclosure": 20,
                "cbex:equity_transfer": 20,
                "cbex:capital_increase": 20,
                "cbex:pre_disclosure": 20,
                "tpre:physical_asset": 20,
                "tpre:equity_transfer": 20,
                "tpre:capital_increase": 20,
                "tpre:pre_disclosure": 20,
                "cquae:physical_asset": 20,
                "cquae:equity_transfer": 20,
                "cquae:capital_increase": 20,
                "cquae:pre_disclosure": 20,
            },
        )

        parser = build_download_parser(config)
        args = parser.parse_args([])

        self.assertEqual(args.exchange, "sse")
        self.assertEqual(args.project_type, "physical_asset")
        self.assertEqual(args.output_root, "C:\\temp\\auto_html")
        self.assertEqual(args.concurrency, 4)
        self.assertFalse(args.resume)
        self.assertTrue(args.save_json)
        self.assertEqual(args.log_dir, "C:\\temp\\logs")
        self.assertTrue(args.auto_split)
        self.assertEqual(args.split_mode, "steady")
        self.assertFalse(args.sse_ssl_verify)
        self.assertEqual(args.sse_ca_bundle, "C:\\temp\\ca.pem")

    def test_download_task_registry_default_settings_can_be_overridden(self) -> None:
        settings = DownloadTaskRegistrySettings(
            task_page_size={"sse:physical_asset": 77},
        )

        applied = set_default_download_task_registry_settings(settings)

        self.assertIs(applied, settings)
        self.assertEqual(
            get_default_download_task_registry_settings().task_page_size["sse:physical_asset"],
            77,
        )

if __name__ == "__main__":
    unittest.main()
