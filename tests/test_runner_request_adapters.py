from __future__ import annotations

import argparse
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_run_postprocess_cli_args_builds_postprocess_request(self) -> None:
        args = argparse.Namespace(
            config_path="C:\\temp\\postprocess.json",
            mode="apply",
            log_dir="C:\\temp\\logs",
            verbose=True,
            skip_unresolved_list=True,
        )
        captured: dict[str, object] = {}

        def fake_run_postprocess_request(request, *, emit_console):
            captured["request"] = request
            return PostProcessRunResult(
                exit_code=0,
                log_file="postprocess.log",
                audit_report="",
                output_files=[],
                unresolved_output_file="",
                export_exit_code=0,
                summary={},
                errors=[],
            )

        with patch("peap_postprocess.postprocess_engine.runner.run_postprocess_request", side_effect=fake_run_postprocess_request):
            result = run_postprocess_cli_args(args, emit_console=False)

        self.assertEqual(result.exit_code, 0)
        request = captured["request"]
        self.assertIsInstance(request, PostProcessRunRequest)
        self.assertTrue(request.config_path)
        self.assertEqual(request.mode, "apply")
        self.assertTrue(request.verbose)


if __name__ == "__main__":
    unittest.main()
