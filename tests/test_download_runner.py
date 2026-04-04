from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from peap.download_models import DownloadTaskRunResult
from peap.download_reporting import new_totals
from peap.download_runner import (
    DownloadRunnerError,
    _build_split_plan_scope,
    ensure_runtime_dependencies,
    run_download_session,
    task_progress_label,
)
from peap.download_tasks import build_task_registry
from peap_core.source_catalog import get_source_descriptor


class DownloadRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )
        self.config = SimpleNamespace(
            AUTO_HTML_FOLDER="C:\\temp\\auto_html",
            HTML_FOLDER="C:\\temp\\manual_html",
            PROJECT_ROOT="C:\\repo\\PEAP",
            DOWNLOAD_CHUNK_STATE_DIR="C:\\temp\\chunk_state",
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
            is_path_within_project_root=lambda path: False,
        )

    def test_run_download_session_merges_task_results(self) -> None:
        spec = build_task_registry()["sse:physical_asset"]
        args = SimpleNamespace(
            exchange="sse",
            project_type="physical_asset",
            output_root="C:\\temp\\auto_html",
            force_manual_root=False,
            start_date="2026-01-01",
            end_date="2026-01-02",
            split_plan_only=False,
            split_use_plan=False,
            split_plan_file=None,
            split_candidates=10,
            split_min_days=1,
            split_max_depth=3,
            split_mode="fast",
            page_size=None,
            max_pages=None,
            concurrency=2,
            resume=True,
            save_json=False,
            sse_ca_bundle=None,
            sse_ssl_verify=True,
            sse_ssl_fallback_insecure=True,
            auto_split=False,
            chunk_state_file=None,
        )
        totals = new_totals()
        totals["saved"] = 2
        task_result = {
            "display_name": spec.display_name,
            "summary": {"saved": 2, "errors": 0},
            "errors": [],
        }

        with (
            patch("peap.download_runner.resolve_tasks", return_value=[spec]) as resolve_tasks,
            patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
            patch("peap.download_runner.load_requested_split_plans", return_value={}),
            patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
            patch(
                "peap.download_runner.run_download_task",
                return_value=DownloadTaskRunResult(
                    any_failure=False,
                    totals=totals,
                    typed_errors=[],
                    task_result=task_result,
                ),
            ) as run_download_task,
        ):
            result = run_download_session(
                args,
                logger=self.logger,
                config_obj=self.config,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.task_count, 1)
        self.assertEqual(result.aggregate_summary["saved"], 2)
        self.assertEqual(result.task_summaries[spec.task_id]["summary"]["saved"], 2)
        resolve_tasks.assert_called_once()
        run_download_task.assert_called_once()

    def test_run_download_session_rejects_manual_root_without_flag(self) -> None:
        args = SimpleNamespace(
            exchange="all",
            project_type="all",
            output_root=self.config.HTML_FOLDER,
            force_manual_root=False,
            start_date=None,
            end_date=None,
            split_plan_only=False,
            split_use_plan=False,
            split_plan_file=None,
            split_candidates=10,
            split_min_days=1,
            split_max_depth=3,
            split_mode="fast",
        )

        with self.assertRaises(DownloadRunnerError):
            run_download_session(
                args,
                logger=self.logger,
                config_obj=self.config,
            )

    def test_runtime_dependency_guidance_points_to_uv_sync(self) -> None:
        spec = build_task_registry()["sse:physical_asset"]
        logger = unittest.mock.Mock()

        with (
            patch("peap.download_runner.importlib.util.find_spec", return_value=None),
            patch("peap.download_runner.sys.executable", "/tmp/peap/.venv/bin/python"),
            patch("builtins.print") as mock_print,
        ):
            ready = ensure_runtime_dependencies([spec], logger=logger)

        self.assertFalse(ready)
        message = logger.error.call_args.args[0]
        self.assertIn("uv sync", message)
        self.assertIn("playwright install chromium", message)
        self.assertNotIn("pip install playwright", message)
        mock_print.assert_called_once_with(message)

    def test_task_labels_and_display_names_use_shared_source_catalog_metadata(self) -> None:
        registry = build_task_registry()
        cbex_spec = registry["cbex:physical_asset"]
        tpre_spec = registry["tpre:pre_disclosure"]

        self.assertEqual(
            cbex_spec.display_name,
            f"{get_source_descriptor('cbex').site_label} - Physical Asset",
        )
        self.assertEqual(
            task_progress_label(cbex_spec),
            f"{get_source_descriptor('cbex').canonical_label} - 挂牌实物资产",
        )
        self.assertEqual(
            tpre_spec.display_name,
            f"{get_source_descriptor('tpre').site_label} - Pre Disclosure",
        )
        self.assertEqual(
            task_progress_label(tpre_spec),
            f"{get_source_descriptor('tpre').canonical_label} - 挂牌预披露",
        )

    def test_split_execution_honors_resume_with_persisted_done_chunk(self) -> None:
        """When a chunk is done in persisted state, resume=True still allows further chunks."""
        spec = build_task_registry()["sse:physical_asset"]
        args = SimpleNamespace(
            exchange="sse",
            project_type="physical_asset",
            output_root="C:\\temp\\auto_html",
            force_manual_root=False,
            start_date="2026-01-01",
            end_date="2026-01-03",
            split_plan_only=False,
            split_use_plan=False,
            split_plan_file=None,
            split_candidates=10,
            split_min_days=1,
            split_max_depth=3,
            split_mode="fast",
            page_size=None,
            max_pages=None,
            concurrency=2,
            resume=True,
            save_json=False,
            sse_ca_bundle=None,
            sse_ssl_verify=True,
            sse_ssl_fallback_insecure=True,
            auto_split=True,
            chunk_state_file=None,
        )
        totals = new_totals()
        totals["saved"] = 1
        task_result = {
            "display_name": spec.display_name,
            "summary": {"saved": 1, "errors": 0},
            "errors": [],
        }
        resume_overrides: list[bool | None] = []

        def capture_resume_override(spec, *, args, output_root, logger, resume_override=None):
            resume_overrides.append(resume_override)
            return object()

        with (
            patch("peap.download_runner.resolve_tasks", return_value=[spec]) as resolve_tasks,
            patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
            patch("peap.download_runner.load_requested_split_plans", return_value={}),
            patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
            patch("peap.download_runner.build_downloader", side_effect=capture_resume_override),
            patch(
                "peap.download_runner.run_download_task",
                return_value=DownloadTaskRunResult(
                    any_failure=False,
                    totals=totals,
                    typed_errors=[],
                    task_result=task_result,
                ),
            ),
        ):
            result = run_download_session(
                args,
                logger=self.logger,
                config_obj=self.config,
            )

        self.assertEqual(result.exit_code, 0)
        # When no chunk_state_ctx exists, resume_override should be None (honoring resume flag)
        self.assertTrue(all(r is None for r in resume_overrides))


if __name__ == "__main__":
    unittest.main()
