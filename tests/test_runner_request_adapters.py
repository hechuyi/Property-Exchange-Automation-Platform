from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from peap.download_models import DownloadRunResult
from peap.download_runner import (
    DownloadRunnerSettings,
    DownloadRunRequest,
    run_download_cli_args,
)
from peap.download_tasks import DownloadTaskRegistrySettings
from peap.excel_handler import ExcelOutputRuntime
from peap.parser_runner import (
    ParserRunRequest,
    ParserRunResult,
    run_parser_cli_args,
    run_parser_request,
)
from peap.pipeline import ParserPipelineSettings
from peap.targeting import OutputTargetSettings
from peap_postprocess.postprocess_engine.runner import (
    PostProcessRunRequest,
    PostProcessRunResult,
    run_postprocess_cli_args,
)


class RunnerRequestAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = SimpleNamespace(
            LOG_DIR=self.temp_dir.name,
            DATA_ROOT=self.temp_dir.name,
            AUTO_HTML_FOLDER=f"{self.temp_dir.name}\\auto_html",
            HTML_FOLDER=f"{self.temp_dir.name}\\manual_html",
            PROJECT_ROOT=f"{self.temp_dir.name}\\repo_root",
            DOWNLOAD_CHUNK_STATE_DIR=f"{self.temp_dir.name}\\chunk_state",
            DOWNLOADER_TASK_PAGE_SIZE={
                "sse:physical_asset": 91,
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
            is_path_within_project_root=lambda _path: False,
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
                "sse_ssl_fallback_insecure": True,
                "sse_ca_bundle": None,
            },
            PARSER_CACHE_DB=f"{self.temp_dir.name}\\parse_cache.sqlite3",
            COMPARE_REPORT_DIR=f"{self.temp_dir.name}\\compare_reports",
            EXCEL_SCHEMA_FILE=f"{self.temp_dir.name}\\excel_schema.json",
            OUTPUT_EXCEL_DIR=f"{self.temp_dir.name}\\excel",
            OUTPUT_FILES={
                "equity_transfer": f"{self.temp_dir.name}\\excel\\挂牌_股权转让.xlsx",
                "pre_disclosure": f"{self.temp_dir.name}\\excel\\挂牌_预披露.xlsx",
                "physical_asset": f"{self.temp_dir.name}\\excel\\挂牌_实物资产.xlsx",
                "capital_increase": f"{self.temp_dir.name}\\excel\\挂牌_增资扩股.xlsx",
            },
            DEAL_FILES={
                "equity_transfer": f"{self.temp_dir.name}\\excel\\成交_股权转让.xlsx",
                "physical_asset": f"{self.temp_dir.name}\\excel\\成交_实物资产.xlsx",
                "capital_increase": f"{self.temp_dir.name}\\excel\\成交_增资扩股.xlsx",
            },
            PARSER_DEFAULTS={
                "compare_fields": ["project_name", "project_id"],
                "parse_cache_enabled": True,
            },
        )

    def test_run_download_cli_args_builds_download_request(self) -> None:
        args = argparse.Namespace(
            exchange="sse",
            project_type="physical_asset",
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
            sse_ssl_fallback_insecure=False,
            sse_ca_bundle="C:\\temp\\ca.pem",
            log_dir="C:\\temp\\logs",
            log_file="C:\\temp\\logs\\download.log",
            verbose=True,
            auto_split=False,
            split_candidates=12,
            split_min_days=2,
            split_max_depth=4,
            split_plan_only=True,
            split_plan_file="C:\\temp\\plan.json",
            split_use_plan=False,
            split_mode="steady",
            chunk_state_file="C:\\temp\\state.json",
        )
        captured: dict[str, object] = {}
        logger = SimpleNamespace(error=lambda *args, **kwargs: None)

        def fake_run_download_request(request, *, logger, config_obj, settings=None):
            captured["request"] = request
            captured["settings"] = settings
            return DownloadRunResult(
                exit_code=0,
                task_count=1,
                aggregate_summary={},
                task_summaries={},
                any_failure=False,
            )

        with patch("peap.download_runner.run_download_request", side_effect=fake_run_download_request):
            result = run_download_cli_args(args, logger=logger, config_obj=self.config)

        self.assertEqual(result.exit_code, 0)
        request = captured["request"]
        self.assertIsInstance(request, DownloadRunRequest)
        self.assertEqual(request.exchange, "sse")
        self.assertEqual(request.project_type, "physical_asset")
        self.assertEqual(request.output_root, "C:\\temp\\auto_html")
        self.assertEqual(request.page_size, 25)
        self.assertEqual(request.max_pages, 6)
        self.assertFalse(request.resume)
        self.assertTrue(request.save_json)
        self.assertTrue(request.auto_split)
        self.assertTrue(request.split_plan_only)
        self.assertEqual(request.split_plan_file, "C:\\temp\\plan.json")
        self.assertEqual(request.split_mode, "steady")
        self.assertEqual(request.chunk_state_file, "C:\\temp\\state.json")
        settings = captured["settings"]
        self.assertIsInstance(settings, DownloadRunnerSettings)
        self.assertEqual(settings.auto_html_root, self.config.AUTO_HTML_FOLDER)
        self.assertEqual(settings.manual_html_root, self.config.HTML_FOLDER)
        self.assertEqual(settings.download_chunk_state_dir, self.config.DOWNLOAD_CHUNK_STATE_DIR)
        self.assertIsInstance(settings.task_registry_settings, DownloadTaskRegistrySettings)
        self.assertEqual(
            settings.task_registry_settings.task_page_size["sse:physical_asset"],
            91,
        )

    def test_run_parser_cli_args_builds_parser_request(self) -> None:
        args = argparse.Namespace(
            self_check=False,
            dry_run=True,
            limit=5,
            batch_flush_interval=10,
            html_root="C:\\temp\\html",
            log_dir="C:\\temp\\logs",
            log_file="C:\\temp\\logs\\parser.log",
                        compare_report_file="C:\\temp\\compare.jsonl",
            compare_fields="project_name, project_id",
            no_parse_cache=True,
            parse_cache_db="C:\\temp\\parse_cache.sqlite3",
            progress_interval=25,
            verbose=True,
        )
        captured: dict[str, object] = {}

        def fake_run_parser_request(request, *, config_obj, emit_console):
            captured["request"] = request
            return ParserRunResult(kind="parser", exit_code=0, log_file="parser.log")

        with patch("peap.parser_runner.run_parser_request", side_effect=fake_run_parser_request):
            result = run_parser_cli_args(args, config_obj=self.config, emit_console=False)

        self.assertEqual(result.exit_code, 0)
        request = captured["request"]
        self.assertIsInstance(request, ParserRunRequest)
        self.assertTrue(request.dry_run)
        self.assertEqual(request.limit, 5)
        self.assertEqual(request.compare_fields, ["project_name", "project_id"])
        self.assertFalse(request.parse_cache_enabled)

    def test_run_parser_request_injects_pipeline_settings(self) -> None:
        request = ParserRunRequest(
            html_root="C:\\temp\\html",
            dry_run=True,
            parse_cache_db="C:\\temp\\override_cache.sqlite3",
            compare_fields=["project_name"],
        )
        captured: dict[str, object] = {}
        logger = SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            debug=lambda *args, **kwargs: None,
        )

        class FakePipeline:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def run(self):
                return SimpleNamespace(
                    processed=1,
                    succeeded=1,
                    failed=0,
                    compare_diffs=0,
                    excel_upsert_skipped=0,
                    parse_cache_hits=0,
                    parse_cache_misses=0,
                    parse_cache_writes=0,
                    compare_report_file="",
                    errors=[],
                )

        with (
            patch("peap.parser_runner.setup_parser_logger", return_value=(logger, "parser.log")),
            patch("peap.parser_runner.close_cli_logger"),
            patch("peap.parser_runner.ParserPipeline", FakePipeline),
        ):
            result = run_parser_request(
                request,
                config_obj=self.config,
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 0)
        settings = captured["settings"]
        self.assertIsInstance(settings, ParserPipelineSettings)
        self.assertEqual(settings.parse_cache_db, "C:\\temp\\override_cache.sqlite3")
        self.assertEqual(settings.compare_report_dir, self.config.COMPARE_REPORT_DIR)
        self.assertIsInstance(settings.output_target_settings, OutputTargetSettings)
        self.assertEqual(
            settings.output_target_settings.output_files["physical_asset"],
            self.config.OUTPUT_FILES["physical_asset"],
        )
        self.assertEqual(
            settings.excel_schema_settings.schema_path,
            self.config.EXCEL_SCHEMA_FILE,
        )
        self.assertIsInstance(settings.excel_output_runtime, ExcelOutputRuntime)
        self.assertEqual(
            settings.excel_output_runtime.schema_status["path"],
            os.path.abspath(self.config.EXCEL_SCHEMA_FILE),
        )

    def test_run_parser_request_passes_config_to_self_check(self) -> None:
        request = ParserRunRequest(
            self_check=True,
            html_root="C:\\temp\\html",
            parse_cache_enabled=False,
            parse_cache_db="C:\\temp\\self_check_cache.sqlite3",
        )
        captured: dict[str, object] = {}
        logger = SimpleNamespace(info=lambda *args, **kwargs: None)

        def fake_run_self_check(
            html_root,
            *,
            logger,
            config_obj,
            pipeline_settings,
            parse_cache_enabled,
        ):
            captured["html_root"] = html_root
            captured["config_obj"] = config_obj
            captured["pipeline_settings"] = pipeline_settings
            captured["parse_cache_enabled"] = parse_cache_enabled
            return []

        with (
            patch("peap.parser_runner.setup_parser_logger", return_value=(logger, "parser.log")),
            patch("peap.parser_runner.close_cli_logger"),
            patch("peap.parser_runner.run_self_check", side_effect=fake_run_self_check),
        ):
            result = run_parser_request(
                request,
                config_obj=self.config,
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(captured["html_root"], "C:\\temp\\html")
        self.assertIs(captured["config_obj"], self.config)
        self.assertFalse(captured["parse_cache_enabled"])
        self.assertIsInstance(captured["pipeline_settings"], ParserPipelineSettings)
        self.assertEqual(
            captured["pipeline_settings"].parse_cache_db,
            "C:\\temp\\self_check_cache.sqlite3",
        )

    def test_run_postprocess_cli_args_builds_postprocess_request(self) -> None:
        args = argparse.Namespace(
            command="run",
            config="C:\\temp\\postprocess.json",
            mode="apply",
            log_dir="C:\\temp\\logs",
            log_file="C:\\temp\\logs\\ppe.log",
            summary_json="C:\\temp\\ppe_summary.json",
            verbose=True,
            skip_unresolved_list=True,
        )
        captured: dict[str, object] = {}

        def fake_run_postprocess_request(request, *, emit_console):
            captured["request"] = request
            return PostProcessRunResult(exit_code=0, log_file="ppe.log")

        with patch(
            "peap_postprocess.postprocess_engine.runner.run_postprocess_request",
            side_effect=fake_run_postprocess_request,
        ):
            result = run_postprocess_cli_args(args, emit_console=False)

        self.assertEqual(result.exit_code, 0)
        request = captured["request"]
        self.assertIsInstance(request, PostProcessRunRequest)
        self.assertEqual(request.config_path, "C:\\temp\\postprocess.json")
        self.assertEqual(request.mode, "apply")
        self.assertEqual(request.log_dir, "C:\\temp\\logs")
        self.assertTrue(request.verbose)
        self.assertTrue(request.skip_unresolved_list)


if __name__ == "__main__":
    unittest.main()
