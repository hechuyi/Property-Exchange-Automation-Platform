from __future__ import annotations

import argparse
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from peap.daily_pipeline import run_daily_pipeline
from peap.download_oneclick import DownloadOneClickRunResult
from peap.parser_runner import ParserRunRequest, ParserRunResult
from peap_postprocess.postprocess_engine.runner import PostProcessRunRequest, PostProcessRunResult


class DailyPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = SimpleNamespace(
            LOG_DIR=self.temp_dir.name,
            DATA_ROOT=self.temp_dir.name,
            HTML_FOLDER=self.temp_dir.name,
            PARSER_CACHE_DB=f"{self.temp_dir.name}\\parse_cache.sqlite3",
            DOWNLOADER_DEFAULTS={
                "concurrency": 2,
                "resume": True,
                "save_json": False,
                "auto_split": True,
                "split_candidates": 10,
                "split_min_days": 1,
                "split_max_depth": 3,
                "split_mode": "fast",
                "sse_ssl_verify": True,
                "sse_ssl_fallback_insecure": True,
                "sse_ca_bundle": None,
            },
            PARSER_DEFAULTS={
                "limit": None,
                "batch_flush_interval": 50,
                "compat_profile": "full",
                "compare_fields": ["project_name"],
                "parse_cache_enabled": True,
                "progress_interval": 50,
            },
        )

    def _build_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            start_date="2026-01-01",
            end_date="2026-01-02",
            exchange="sse",
            project_type="physical_asset",
            concurrency=4,
            page_size=20,
            max_pages=5,
            with_refresh=True,
            no_resume=False,
            save_json=True,
            html_root="C:\\temp\\html",
            postprocess_config="C:\\temp\\postprocess.json",
            postprocess_mode="apply",
            verbose=False,
        )

    def test_run_daily_pipeline_calls_structured_stage_apis(self) -> None:
        captured_request = {}
        captured_parser_request = {}
        captured_postprocess_request = {}

        def fake_run_download_oneclick(request, *, config_obj, emit_console):
            captured_request["request"] = request
            return DownloadOneClickRunResult(
                exit_code=0,
                log_file="download.log",
                plan_file="plan.json",
                plan_file_exists=False,
                plan_file_removed=True,
                start="2026-01-01 00:00:00",
                end="2026-01-01 00:01:00",
                duration_sec=60.0,
                aggregate_summary={"saved": 3, "errors": 0},
                task_summaries={},
                typed_errors=[],
                stages=[],
            )

        def fake_run_parser_request(request, *, config_obj, emit_console):
            captured_parser_request["request"] = request
            return ParserRunResult(
                kind="parser",
                exit_code=0,
                log_file="parser.log",
                summary={"succeeded": 2, "failed": 0, "upsert_skipped": 1},
                errors=[],
            )

        def fake_run_postprocess_request(request, *, emit_console):
            captured_postprocess_request["request"] = request
            return PostProcessRunResult(
                exit_code=0,
                log_file="ppe.log",
                summary={"discovered_files": 4, "failed_files": 0},
                errors=[],
            )

        with (
            patch("peap.daily_pipeline.run_download_oneclick", side_effect=fake_run_download_oneclick),
            patch("peap.daily_pipeline.run_parser_request", side_effect=fake_run_parser_request),
            patch("peap.daily_pipeline.run_postprocess_request", side_effect=fake_run_postprocess_request),
        ):
            result = run_daily_pipeline(
                self._build_args(),
                config_obj=self.config,
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 0)
        request = captured_request["request"]
        self.assertEqual(request.download_request.start_date, "2026-01-01")
        self.assertEqual(request.download_request.end_date, "2026-01-02")
        self.assertEqual(request.download_request.page_size, 20)
        self.assertTrue(request.with_refresh)
        parser_request = captured_parser_request["request"]
        self.assertIsInstance(parser_request, ParserRunRequest)
        self.assertEqual(parser_request.html_root, "C:\\temp\\html")
        self.assertEqual(parser_request.compare_fields, ["project_name"])
        postprocess_request = captured_postprocess_request["request"]
        self.assertIsInstance(postprocess_request, PostProcessRunRequest)
        self.assertEqual(postprocess_request.config_path, "C:\\temp\\postprocess.json")
        self.assertEqual(postprocess_request.mode, "apply")
        self.assertEqual(result.download_result.aggregate_summary["saved"], 3)
        self.assertEqual(result.parser_result.summary["succeeded"], 2)
        self.assertEqual(result.postprocess_result.summary["discovered_files"], 4)

    def test_run_daily_pipeline_rejects_reversed_dates(self) -> None:
        args = self._build_args()
        args.start_date = "2026-01-03"
        args.end_date = "2026-01-01"

        with (
            patch("peap.daily_pipeline.run_download_oneclick") as run_download_oneclick,
            patch("peap.daily_pipeline.run_parser_request") as run_parser_request,
            patch("peap.daily_pipeline.run_postprocess_request") as run_postprocess_request,
        ):
            result = run_daily_pipeline(
                args,
                config_obj=self.config,
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 2)
        run_download_oneclick.assert_not_called()
        run_parser_request.assert_not_called()
        run_postprocess_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
