from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_peap_cli_only_exposes_maintenance_commands(self) -> None:
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
        self.assertEqual(parser.parse_args(["data-health", "--app-home", "C:\\temp\\app"]).command, "data-health")
        self.assertEqual(parser.parse_args(["repair-failures", "--app-home", "C:\\temp\\app"]).command, "repair-failures")
        with self.assertRaises(SystemExit):
            parser.parse_args(["--dry-run"])

    def test_peap_cli_does_not_reach_legacy_parser_to_workbook_path(self) -> None:
        import peap.cli as cli_module

        source = inspect.getsource(cli_module)
        self.assertNotIn("parser_runner", source)
        self.assertNotIn("ParserPipeline", source)
        self.assertNotIn("ExcelBatchWriter", source)

    def test_download_cli_build_parser_uses_injected_config(self) -> None:
        config = SimpleNamespace(
            AUTO_HTML_FOLDER="C:\\temp\\auto_html",
            HTML_FOLDER="C:\\temp\\manual_html",
            LOG_DIR="C:\\temp\\logs",
            DOWNLOADER_DEFAULTS={
                "exchange": "sse",
                "record_family": "listing",
                "business_id": "physical_asset",
                "concurrency": 4,
                "resume": False,
                "save_json": True,
                "auto_split": True,
                "split_candidates": 9,
                "split_min_days": 2,
                "split_max_depth": 5,
                "split_mode": "steady",
                "sse_ssl_verify": False,
                "sse_ca_bundle": "C:\\temp\\ca.pem",
            },
            DOWNLOADER_TASK_PAGE_SIZE={
                "sse:listing:physical_asset": 20,
                "cbex:listing:physical_asset": 20,
                "sse:listing:equity_transfer": 20,
                "sse:listing:capital_increase": 20,
                "sse:listing:pre_disclosure": 20,
                "cbex:listing:equity_transfer": 20,
                "cbex:listing:capital_increase": 20,
                "cbex:listing:pre_disclosure": 20,
                "tpre:listing:physical_asset": 20,
                "tpre:listing:equity_transfer": 20,
                "tpre:listing:capital_increase": 20,
                "tpre:listing:pre_disclosure": 20,
                "cquae:listing:physical_asset": 20,
                "cquae:listing:equity_transfer": 20,
                "cquae:listing:capital_increase": 20,
                "cquae:listing:pre_disclosure": 20,
                "shandong:listing:equity_transfer": 20,
                "shandong:listing:capital_increase": 20,
                "guangdong:listing:equity_transfer": 20,
                "guangdong:listing:capital_increase": 20,
                "shenzhen:listing:equity_transfer": 20,
                "shenzhen:listing:capital_increase": 20,
            },
        )

        parser = build_download_parser(config)
        args = parser.parse_args([])

        self.assertEqual(args.record_family, "listing")
        self.assertEqual(args.exchange, "sse")
        self.assertEqual(args.business_id, "physical_asset")
        self.assertEqual(args.output_root, "C:\\temp\\auto_html")
        self.assertEqual(args.concurrency, 4)
        self.assertFalse(args.resume)
        self.assertTrue(args.save_json)
        self.assertEqual(args.log_dir, "C:\\temp\\logs")
        self.assertTrue(args.auto_split)
        self.assertEqual(args.split_mode, "steady")
        self.assertFalse(args.sse_ssl_verify)
        self.assertEqual(args.sse_ca_bundle, "C:\\temp\\ca.pem")

    def test_download_cli_build_parser_accepts_non_default_family_scope_choices(self) -> None:
        config = SimpleNamespace(
            AUTO_HTML_FOLDER="C:\\temp\\auto_html",
            HTML_FOLDER="C:\\temp\\manual_html",
            LOG_DIR="C:\\temp\\logs",
            DOWNLOADER_DEFAULTS={
                "exchange": "sse",
                "record_family": "listing",
                "business_id": "physical_asset",
                "concurrency": 4,
                "resume": False,
                "save_json": True,
                "auto_split": True,
                "split_candidates": 9,
                "split_min_days": 2,
                "split_max_depth": 5,
                "split_mode": "steady",
                "sse_ssl_verify": False,
                "sse_ca_bundle": "C:\\temp\\ca.pem",
            },
            DOWNLOADER_TASK_PAGE_SIZE={},
        )

        def fake_exchange_choices(_config_obj=None, *, record_family=None, settings=None):
            if record_family in (None, "", "all"):
                return ["dealx", "sse"]
            if record_family == "deal":
                return ["dealx"]
            return ["sse"]

        def fake_business_choices(*, record_family=None):
            if record_family in (None, "", "all"):
                return ["deal_asset", "physical_asset"]
            if record_family == "deal":
                return ["deal_asset"]
            return ["physical_asset"]

        with (
            patch(
                "peap.download_cli.list_family_descriptors",
                return_value=[
                    SimpleNamespace(family_id="listing"),
                    SimpleNamespace(family_id="deal"),
                ],
            ),
            patch("peap.download_cli.exchange_choices", side_effect=fake_exchange_choices),
            patch("peap.download_cli.business_choices", side_effect=fake_business_choices),
        ):
            parser = build_download_parser(config)
            args = parser.parse_args(
                [
                    "--record-family",
                    "deal",
                    "--exchange",
                    "dealx",
                    "--business-id",
                    "deal_asset",
                ]
            )

        self.assertEqual(args.record_family, "deal")
        self.assertEqual(args.exchange, "dealx")
        self.assertEqual(args.business_id, "deal_asset")

    def test_download_task_registry_default_settings_can_be_overridden(self) -> None:
        settings = DownloadTaskRegistrySettings(
            task_page_size={"sse:listing:physical_asset": 77},
        )

        applied = set_default_download_task_registry_settings(settings)

        self.assertIs(applied, settings)
        self.assertEqual(
            get_default_download_task_registry_settings().task_page_size["sse:listing:physical_asset"],
            77,
        )

def test_tpre_physical_asset_stays_5000w_plus() -> None:
    from peap.downloaders.tpre import TianjinPhysicalAssetDownloader
    downloader = TianjinPhysicalAssetDownloader(html_root="/tmp/test")
    assert downloader.list_queries[0].extra_params["priceBegin"] == 5000


def test_cquae_physical_asset_stays_5000w_plus() -> None:
    from peap.downloaders.cquae import ChongqingPhysicalAssetDownloader
    downloader = ChongqingPhysicalAssetDownloader(html_root="/tmp/test")
    urls = [source.list_url for source in downloader.list_sources]
    assert any("price=5000" in url for url in urls)


if __name__ == "__main__":
    unittest.main()
