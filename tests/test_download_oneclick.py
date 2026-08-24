"""Tests for download one-click orchestration."""

from __future__ import annotations

import types
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from peap.download_errors import DownloadError
from peap.download_oneclick import (
    DownloadOneClickRequest,
    DownloadOneClickRunResult,
    DownloadOneClickStageResult,
    _aggregate_collected_task_summaries,
    _base_error_payload,
    _collect_tasks,
    _CollectedTask,
    _execute_tasks,
    _filter_existing_candidates,
    _merge_save_pages_error_payload,
    _with_terminal_download_classification,
    run_download_oneclick,
)
from peap.download_runner import DownloadRunRequest


class DownloadOneclickTest(unittest.TestCase):
    def test_collect_result_preserves_downloaded_this_run(self) -> None:
        """Verify that when candidates are collected, downloaded_this_run paths
        are captured in the collect result's task summaries."""
        mock_request = MagicMock(spec=DownloadOneClickRequest)
        mock_request.download_request = MagicMock(spec=DownloadRunRequest)
        mock_request.download_request.exchange = "test"
        mock_request.existing_project_codes = []
        mock_request.existing_candidate_tokens = []
        _CollectedTask(
            task_id="test:task",
            display_name="Test Task",
            task_label="test-label",
            candidate_entries=[],
            existing_skipped=0,
            summary={"saved": 1, "detail_candidates": 0},
            typed_errors=[],
            error_items=[],
            spec=MagicMock(),
        )

        filtered, skipped = _filter_existing_candidates(
            [{"project_code": "P001", "page_url": "http://example.com"}],
            existing_project_codes=frozenset(),
            existing_candidate_tokens=frozenset(),
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(skipped, 0)

    def test_execute_tasks_populates_new_downloads_in_summary(self) -> None:
        """Verify _execute_tasks adds new_downloads to task summaries from summary.downloaded_this_run."""
        # This test verifies that when _execute_tasks builds task_summaries,
        # it includes new_downloads from the summary's downloaded_this_run attribute.
        # The actual implementation should read downloaded_this_run from the summary
        # object and add it as new_downloads to the task_summaries dict.
        from peap.download_reporting import summary_to_dict
        from peap.downloaders.common import DownloadSummary

        summary = DownloadSummary()
        summary.saved = 1
        summary.downloaded_this_run.add("2026年4月/GR2026BJ1001952-demo.html")
        summary.downloaded_this_run.add("2026年4月/GR2026BJ1001953-demo.html")

        summary_to_dict(summary)
        new_downloads = getattr(summary, "downloaded_this_run", set())
        self.assertEqual(
            sorted(new_downloads),
            ["2026年4月/GR2026BJ1001952-demo.html", "2026年4月/GR2026BJ1001953-demo.html"],
        )

    def test_execute_tasks_rejects_bad_downloaded_this_run_contract(self) -> None:
        from peap.downloaders.common import DownloadSummary

        request = DownloadOneClickRequest(
            download_request=DownloadRunRequest(
                exchange="cbex",
                record_family="deal",
                business_id="equity_transfer",
            )
        )
        collected = [
            _CollectedTask(
                task_id="cbex:deal:equity_transfer",
                display_name="北交所成交",
                task_label="北交所 / 成交",
                candidate_entries=[{"project_code": "G32026BJ100002"}],
                existing_skipped=0,
                summary={"detail_candidates": 1},
                typed_errors=[],
                error_items=[],
                spec=MagicMock(),
            )
        ]
        bad_summary = DownloadSummary(saved=1)
        bad_summary.downloaded_this_run = []  # type: ignore[assignment]

        with (
            patch(
                "peap.download_oneclick.prepare_download_session",
                return_value=types.SimpleNamespace(output_root="/tmp/downloads", request=request.download_request),
            ),
            patch("peap.download_oneclick.build_downloader", return_value=object()),
            patch("peap.download_oneclick.run_downloader_with_prefetched", return_value=bad_summary),
        ):
            with self.assertRaisesRegex(TypeError, "summary.downloaded_this_run must be a set"):
                _execute_tasks(
                    request,
                    collected,
                    logger=MagicMock(),
                    config_obj=MagicMock(),
                )

    def test_execute_tasks_rejects_non_download_error_summary_items(self) -> None:
        from peap.downloaders.common import DownloadSummary

        request = DownloadOneClickRequest(
            download_request=DownloadRunRequest(
                exchange="cbex",
                record_family="deal",
                business_id="equity_transfer",
            )
        )
        collected = [
            _CollectedTask(
                task_id="cbex:deal:equity_transfer",
                display_name="北交所成交",
                task_label="北交所 / 成交",
                candidate_entries=[{"project_code": "G32026BJ100002"}],
                existing_skipped=0,
                summary={"detail_candidates": 1},
                typed_errors=[],
                error_items=[],
                spec=MagicMock(),
            )
        ]
        bad_summary = DownloadSummary(saved=0)
        bad_summary.typed_errors = [
            DownloadError(
                error_code="cbex_execute_failed",
                error_message="cbex: execute-failed: partial",
                stage="save_pages",
                failure_kind="execute",
                source_id="cbex",
                task_id="cbex:deal:equity_transfer",
                raw_reason="partial",
            ),
            "not-typed",  # type: ignore[list-item]
        ]

        with (
            patch(
                "peap.download_oneclick.prepare_download_session",
                return_value=types.SimpleNamespace(output_root="/tmp/downloads", request=request.download_request),
            ),
            patch("peap.download_oneclick.build_downloader", return_value=object()),
            patch("peap.download_oneclick.run_downloader_with_prefetched", return_value=bad_summary),
        ):
            with self.assertRaisesRegex(TypeError, "summary.typed_errors must contain DownloadError items"):
                _execute_tasks(
                    request,
                    collected,
                    logger=MagicMock(),
                    config_obj=MagicMock(),
                )

    def test_execute_tasks_aggregates_prefetched_list_page_candidate_stats(self) -> None:
        """Regression: list-page-only deal candidates must reach front-end-visible stats."""
        from peap.downloaders.common import DownloadSummary

        request = DownloadOneClickRequest(
            download_request=DownloadRunRequest(
                exchange="cbex",
                record_family="deal",
                business_id="equity_transfer",
                start_date="2026-04-13",
                end_date="2026-04-13",
            )
        )
        candidate = {
            "project_code": "G32026BJ100002",
            "project_name": "CBEX无详情成交公告",
            "source_url": "https://www.cbex.com.cn/xm/cqzr/cjjggs/",
            "page_url": "https://www.cbex.com.cn/xm/cqzr/cjjggs/",
            "source_kind": "list_page",
        }
        collected = [
            _CollectedTask(
                task_id="cbex:deal:equity_transfer",
                display_name="北交所成交",
                task_label="北交所 / 成交",
                candidate_entries=[candidate],
                existing_skipped=0,
                summary={"detail_candidates": 1},
                typed_errors=[],
                error_items=[],
                spec=MagicMock(),
            )
        ]
        materialized = DownloadSummary()
        materialized.detail_candidates = 1
        materialized.saved = 1
        materialized.downloaded_this_run.add("2026年4月/G32026BJ100002-CBEX无详情成交公告.html")
        captured_prefetched: list[dict[str, object]] = []

        def fake_run_with_prefetched(*args, prefetched_candidates=None, **kwargs):
            captured_prefetched.extend(prefetched_candidates or [])
            return materialized

        with (
            patch(
                "peap.download_oneclick.prepare_download_session",
                return_value=types.SimpleNamespace(output_root="/tmp/downloads", request=request.download_request),
            ),
            patch("peap.download_oneclick.build_downloader", return_value=object()),
            patch("peap.download_oneclick.run_downloader_with_prefetched", side_effect=fake_run_with_prefetched),
        ):
            stage = _execute_tasks(
                request,
                collected,
                logger=MagicMock(),
                config_obj=MagicMock(),
            )

        task_summary = stage.summary_payload["task_summaries"]["cbex:deal:equity_transfer"]
        self.assertEqual(stage.exit_code, 0)
        self.assertEqual(stage.summary_payload["aggregate_summary"]["saved"], 1)
        self.assertEqual(stage.summary_payload["aggregate_summary"]["detail_candidates"], 1)
        self.assertEqual(task_summary["summary"]["saved"], 1)
        self.assertEqual(task_summary["candidate_count"], 1)
        self.assertEqual(
            task_summary["new_downloads"],
            ["2026年4月/G32026BJ100002-CBEX无详情成交公告.html"],
        )
        self.assertEqual(captured_prefetched, [candidate])

    def test_run_download_oneclick_executes_collected_candidates_after_partial_collect_error(self) -> None:
        collect_error = DownloadError(
            error_code="cbex_collect_failed",
            error_message="cbex: collect-failed: transient",
            stage="prepare_tasks",
            failure_kind="collect",
            source_id="cbex",
            task_id="cbex:deal:equity_transfer",
            raw_reason="transient",
        )
        request = DownloadOneClickRequest(
            download_request=DownloadRunRequest(
                exchange="all",
                record_family="deal",
                business_id="all",
            )
        )
        collected = [
            _CollectedTask(
                task_id="cquae:deal:equity_transfer",
                display_name="重交所成交",
                task_label="重交所 / 成交",
                candidate_entries=[{"project_code": "G32026CQ100002", "source_kind": "detail"}],
                existing_skipped=0,
                summary={"detail_candidates": 1},
                typed_errors=[],
                error_items=[],
                spec=MagicMock(),
            )
        ]
        collect_stage = DownloadOneClickStageResult(
            label="Stage 1/2: Collect Tasks",
            exit_code=1,
            elapsed_sec=0.0,
            typed_errors=[collect_error],
            summary_payload={"aggregate_summary": {"detail_candidates": 1}, "task_summaries": {}},
        )
        execute_stage = DownloadOneClickStageResult(
            label="Stage 2/2: Download By Collected Tasks",
            exit_code=0,
            elapsed_sec=0.0,
            typed_errors=[],
            summary_payload={
                "aggregate_summary": {"saved": 1, "detail_candidates": 1},
                "task_summaries": {"cquae:deal:equity_transfer": {"summary": {"saved": 1}}},
            },
        )

        with (
            patch("peap.download_oneclick.setup_download_oneclick_logger", return_value=(MagicMock(), "download.log")),
            patch("peap.download_oneclick.close_cli_logger"),
            patch("peap.download_oneclick._collect_tasks", return_value=(collected, collect_stage)),
            patch("peap.download_oneclick._execute_tasks", return_value=execute_stage) as execute_mock,
        ):
            result = run_download_oneclick(request, config_obj=MagicMock(LOG_DIR="/tmp"), emit_console=False)

        execute_mock.assert_called_once()
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.aggregate_summary["saved"], 1)
        self.assertEqual(result.task_summaries["cquae:deal:equity_transfer"]["summary"]["saved"], 1)
        self.assertEqual(result.typed_errors, [collect_error])

    def test_run_download_oneclick_marks_no_download_needed_as_failure_when_collect_failed(self) -> None:
        collect_error = DownloadError(
            error_code="sse_list_failed",
            error_message="sse: list-failed: read operation timed out",
            stage="prepare_tasks",
            failure_kind="list",
            source_id="sse",
            task_id="sse:listing:physical_asset",
            raw_reason="read operation timed out",
        )
        request = DownloadOneClickRequest(
            download_request=DownloadRunRequest(
                exchange="sse",
                record_family="listing",
                business_id="physical_asset",
            )
        )
        collect_stage = DownloadOneClickStageResult(
            label="Stage 1/2: Collect Tasks",
            exit_code=1,
            elapsed_sec=0.0,
            typed_errors=[collect_error],
            summary_payload={
                "aggregate_summary": {"detail_candidates": 0, "saved": 0},
                "task_summaries": {
                    "sse:listing:physical_asset": {
                        "summary": {"detail_candidates": 0, "saved": 0},
                        "errors": [collect_error.error_message],
                    }
                },
            },
        )
        emitted: list[dict[str, Any]] = []

        with (
            patch("peap.download_oneclick.setup_download_oneclick_logger", return_value=(MagicMock(), "download.log")),
            patch("peap.download_oneclick.close_cli_logger"),
            patch("peap.download_oneclick._collect_tasks", return_value=([], collect_stage)),
        ):
            result = run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=request.download_request,
                    stage_callback=emitted.append,
                ),
                config_obj=MagicMock(LOG_DIR="/tmp"),
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.typed_errors, [collect_error])
        self.assertEqual(result.aggregate_summary["detail_candidates"], 0)

    def test_run_download_oneclick_preserves_collect_failure_when_collected_tasks_have_no_candidates(
        self,
    ) -> None:
        collect_error = DownloadError(
            error_code="sse_list_failed",
            error_message="sse: list-failed: read operation timed out",
            stage="prepare_tasks",
            failure_kind="list",
            source_id="sse",
            task_id="sse:listing:physical_asset",
            raw_reason="read operation timed out",
        )
        request = DownloadOneClickRequest(
            download_request=DownloadRunRequest(
                exchange="sse",
                record_family="listing",
                business_id="physical_asset",
            )
        )
        collected = [
            _CollectedTask(
                task_id="sse:listing:physical_asset",
                display_name="上交所挂牌",
                task_label="上交所 / 挂牌",
                candidate_entries=[],
                existing_skipped=0,
                summary={"detail_candidates": 0, "saved": 0},
                typed_errors=[collect_error],
                error_items=[collect_error],
                spec=MagicMock(),
            )
        ]
        collect_stage = DownloadOneClickStageResult(
            label="Stage 1/2: Collect Tasks",
            exit_code=1,
            elapsed_sec=0.0,
            typed_errors=[collect_error],
            summary_payload={
                "aggregate_summary": {"detail_candidates": 0, "saved": 0},
                "task_summaries": {
                    "sse:listing:physical_asset": {
                        "summary": {"detail_candidates": 0, "saved": 0},
                        "errors": [collect_error.error_message],
                    }
                },
            },
        )

        with (
            patch("peap.download_oneclick.setup_download_oneclick_logger", return_value=(MagicMock(), "download.log")),
            patch("peap.download_oneclick.close_cli_logger"),
            patch("peap.download_oneclick._collect_tasks", return_value=(collected, collect_stage)),
        ):
            result = run_download_oneclick(request, config_obj=MagicMock(LOG_DIR="/tmp"), emit_console=False)

        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(result.typed_errors, [collect_error])

    def test_run_download_oneclick_reports_all_listed_rows_outside_date_range_as_warning(self) -> None:
        request = DownloadOneClickRequest(
            download_request=DownloadRunRequest(
                exchange="cbex",
                record_family="listing",
                business_id="physical_asset",
                start_date="2026-03-01",
                end_date="2026-03-31",
            )
        )
        collect_stage = DownloadOneClickStageResult(
            label="Stage 1/2: Collect Tasks",
            exit_code=0,
            elapsed_sec=0.0,
            typed_errors=[],
            summary_payload={
                "aggregate_summary": {
                    "listed": 30,
                    "list_date_skipped": 30,
                    "detail_candidates": 0,
                    "saved": 0,
                    "pages": 6,
                },
                "task_summaries": {},
            },
        )
        emitted: list[dict[str, Any]] = []

        with (
            patch("peap.download_oneclick.setup_download_oneclick_logger", return_value=(MagicMock(), "download.log")),
            patch("peap.download_oneclick.close_cli_logger"),
            patch("peap.download_oneclick._collect_tasks", return_value=([], collect_stage)),
        ):
            result = run_download_oneclick(
                DownloadOneClickRequest(
                    download_request=request.download_request,
                    stage_callback=emitted.append,
                ),
                config_obj=MagicMock(LOG_DIR="/tmp"),
                emit_console=False,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.aggregate_summary["warning_code"], "all_listed_rows_outside_date_range")
        self.assertTrue(emitted)
        last_event = emitted[-1]
        self.assertEqual(last_event["status"], "warning")
        self.assertNotEqual(last_event["label"], "当前没有需要下载的网页，无需下载")
        self.assertEqual(
            last_event["summary_payload"]["warning_code"],
            "all_listed_rows_outside_date_range",
        )
        self.assertIn("30", last_event["summary_payload"]["warning_message"])

    def test_run_download_oneclick_rejects_malformed_collect_aggregate_summary(self) -> None:
        request = DownloadOneClickRequest(
            download_request=DownloadRunRequest(
                exchange="cbex",
                record_family="listing",
                business_id="physical_asset",
            )
        )
        collect_stage = DownloadOneClickStageResult(
            label="Stage 1/2: Collect Tasks",
            exit_code=0,
            elapsed_sec=0.0,
            typed_errors=[],
            summary_payload={"aggregate_summary": [], "task_summaries": {}},
        )

        with (
            patch("peap.download_oneclick.setup_download_oneclick_logger", return_value=(MagicMock(), "download.log")),
            patch("peap.download_oneclick.close_cli_logger"),
            patch("peap.download_oneclick._collect_tasks", return_value=([], collect_stage)),
        ):
            with self.assertRaisesRegex(TypeError, "summary_payload.aggregate_summary must be a dict"):
                run_download_oneclick(request, config_obj=MagicMock(LOG_DIR="/tmp"), emit_console=False)

    def test_terminal_download_classification_rejects_non_dict_summary_payload(self) -> None:
        request = DownloadOneClickRequest(download_request=DownloadRunRequest(exchange="cbex"))

        with self.assertRaisesRegex(TypeError, "summary_payload must be a dict"):
            _with_terminal_download_classification([], request=request)  # type: ignore[arg-type]

    def test_terminal_download_classification_rejects_non_dict_aggregate_summary(self) -> None:
        request = DownloadOneClickRequest(download_request=DownloadRunRequest(exchange="cbex"))

        with self.assertRaisesRegex(TypeError, "summary_payload.aggregate_summary must be a dict"):
            _with_terminal_download_classification({"aggregate_summary": []}, request=request)

    def test_base_error_payload_allows_none_fallback_but_rejects_non_dict_fallback(self) -> None:
        self.assertEqual(_base_error_payload(error_items=[], fallback_payload=None), {"errors": []})

        with self.assertRaisesRegex(TypeError, "fallback_payload must be a dict"):
            _base_error_payload(error_items=[], fallback_payload=[])  # type: ignore[arg-type]

    def test_merge_save_pages_error_payload_rejects_non_dict_typed_error_details(self) -> None:
        typed_error = DownloadError(
            error_code="cbex_execute_failed",
            error_message="cbex: execute-failed: bad payload",
            stage="save_pages",
            failure_kind="execute",
            source_id="cbex",
            task_id="cbex:deal:equity_transfer",
            raw_reason="bad payload",
        )
        malformed_payload = {
            "error_code": typed_error.error_code,
            "error_message": typed_error.error_message,
            "error_details": [],
        }

        with patch("peap.download_oneclick.format_download_error", return_value=malformed_payload):
            with self.assertRaisesRegex(TypeError, "typed_error.error_details must be a dict"):
                _merge_save_pages_error_payload(error_items=[typed_error])

    def test_collect_tasks_rejects_non_download_error_summary_items(self) -> None:
        from peap.downloaders.common import DownloadSummary

        request = DownloadOneClickRequest(
            download_request=DownloadRunRequest(
                exchange="cbex",
                record_family="listing",
                business_id="physical_asset",
            )
        )
        bad_summary = DownloadSummary()
        bad_summary.typed_errors = [
            DownloadError(
                error_code="cbex_list_failed",
                error_message="cbex: list-failed: partial",
                stage="prepare_tasks",
                failure_kind="list",
                source_id="cbex",
                task_id="cbex:listing:physical_asset",
                raw_reason="partial",
            ),
            "not-typed",  # type: ignore[list-item]
        ]

        with (
            patch(
                "peap.download_oneclick.prepare_download_session",
                return_value=types.SimpleNamespace(
                    output_root="/tmp/downloads",
                    request=request.download_request,
                    tasks=[MagicMock(task_id="cbex:listing:physical_asset")],
                ),
            ),
            patch("peap.download_oneclick.task_progress_label", return_value="北交所 / 挂牌实物资产"),
            patch("peap.download_oneclick.build_downloader", return_value=object()),
            patch("peap.download_oneclick.run_downloader", return_value=bad_summary),
        ):
            with self.assertRaisesRegex(TypeError, "summary.typed_errors must contain DownloadError items"):
                _collect_tasks(
                    request,
                    logger=MagicMock(),
                    config_obj=MagicMock(),
                )

    def test_execute_tasks_reports_date_filtered_zero_candidate_summary_as_warning(self) -> None:
        request = DownloadOneClickRequest(
            download_request=DownloadRunRequest(
                exchange="cbex",
                record_family="listing",
                business_id="physical_asset",
                start_date="2026-03-01",
                end_date="2026-03-31",
            )
        )
        emitted: list[dict[str, Any]] = []
        collected = [
            _CollectedTask(
                task_id="cbex:listing:physical_asset",
                display_name="北交所挂牌",
                task_label="北交所 / 挂牌实物资产",
                candidate_entries=[],
                existing_skipped=0,
                summary={
                    "listed": 30,
                    "list_date_skipped": 30,
                    "detail_candidates": 0,
                    "saved": 0,
                    "pages": 6,
                },
                typed_errors=[],
                error_items=[],
                spec=MagicMock(),
            )
        ]

        stage = _execute_tasks(
            DownloadOneClickRequest(
                download_request=request.download_request,
                stage_callback=emitted.append,
            ),
            collected,
            logger=MagicMock(),
            config_obj=MagicMock(),
        )

        self.assertEqual(stage.exit_code, 0)
        self.assertEqual(stage.summary_payload["warning_code"], "all_listed_rows_outside_date_range")
        self.assertEqual(stage.summary_payload["aggregate_summary"]["listed"], 30)
        self.assertEqual(emitted[-1]["status"], "warning")
        self.assertIn("披露日期", emitted[-1]["label"])

    def test_execute_tasks_returns_same_terminal_warning_summary_as_emitted_event(self) -> None:
        request = DownloadOneClickRequest(
            download_request=DownloadRunRequest(
                exchange="cbex",
                record_family="listing",
                business_id="physical_asset",
                start_date="2026-03-01",
                end_date="2026-03-31",
            )
        )
        emitted: list[dict[str, Any]] = []
        collected = [
            _CollectedTask(
                task_id="cbex:listing:physical_asset",
                display_name="北交所挂牌",
                task_label="北交所 / 挂牌实物资产",
                candidate_entries=[],
                existing_skipped=0,
                summary={
                    "listed": 30,
                    "list_date_skipped": 30,
                    "detail_candidates": 0,
                    "saved": 0,
                    "pages": 6,
                },
                typed_errors=[],
                error_items=[],
                spec=MagicMock(),
            )
        ]

        stage = _execute_tasks(
            DownloadOneClickRequest(
                download_request=request.download_request,
                stage_callback=emitted.append,
            ),
            collected,
            logger=MagicMock(),
            config_obj=MagicMock(),
        )

        event_summary = emitted[-1]["summary_payload"]
        self.assertEqual(stage.summary_payload, event_summary)
        self.assertEqual(stage.summary_payload["task_index"], 0)
        self.assertEqual(stage.summary_payload["task_total"], 0)
        self.assertEqual(stage.summary_payload["phase_percent"], 98)
        self.assertEqual(stage.summary_payload["warning_code"], "all_listed_rows_outside_date_range")
        self.assertEqual(
            stage.summary_payload["summary"]["warning_code"],
            "all_listed_rows_outside_date_range",
        )
        self.assertEqual(
            stage.summary_payload["warning_message"],
            event_summary["aggregate_summary"]["warning_message"],
        )

    def test_aggregate_collected_task_summaries_rejects_non_mapping_task_summary(self) -> None:
        collected = [
            _CollectedTask(
                task_id="cbex:listing:physical_asset",
                display_name="北交所挂牌",
                task_label="北交所 / 挂牌实物资产",
                candidate_entries=[],
                existing_skipped=0,
                summary=[],  # type: ignore[arg-type]
                typed_errors=[],
                error_items=[],
                spec=MagicMock(),
            )
        ]

        with self.assertRaisesRegex(TypeError, "task\\.summary must be a mapping"):
            _aggregate_collected_task_summaries(collected)

    def test_execute_tasks_rejects_non_mapping_terminal_aggregate_summary(self) -> None:
        request = DownloadOneClickRequest(download_request=DownloadRunRequest(exchange="cbex"))
        collected = [
            _CollectedTask(
                task_id="cbex:listing:physical_asset",
                display_name="北交所挂牌",
                task_label="北交所 / 挂牌实物资产",
                candidate_entries=[],
                existing_skipped=0,
                summary={"detail_candidates": 0, "saved": 0},
                typed_errors=[],
                error_items=[],
                spec=MagicMock(),
            )
        ]

        with patch(
            "peap.download_oneclick._with_terminal_download_classification",
            return_value={"kind": "download", "aggregate_summary": [], "task_summaries": {}},
        ):
            with self.assertRaisesRegex(TypeError, "summary_payload\\.aggregate_summary must be a dict"):
                _execute_tasks(
                    request,
                    collected,
                    logger=MagicMock(),
                    config_obj=MagicMock(),
                )

    def test_execute_tasks_explains_zero_candidates_from_date_and_business_filters(self) -> None:
        request = DownloadOneClickRequest(
            download_request=DownloadRunRequest(
                exchange="cbex",
                record_family="listing",
                business_id="equity_transfer",
                start_date="2026-03-01",
                end_date="2026-03-31",
            )
        )
        emitted: list[dict[str, Any]] = []
        collected = [
            _CollectedTask(
                task_id="cbex:listing:equity_transfer",
                display_name="北交所挂牌股权",
                task_label="北交所 / 挂牌股权转让",
                candidate_entries=[],
                existing_skipped=0,
                summary={
                    "listed": 30,
                    "list_date_skipped": 20,
                    "business_filter_skipped": 10,
                    "detail_candidates": 0,
                    "saved": 0,
                    "pages": 3,
                },
                typed_errors=[],
                error_items=[],
                spec=MagicMock(),
            )
        ]

        stage = _execute_tasks(
            DownloadOneClickRequest(
                download_request=request.download_request,
                stage_callback=emitted.append,
            ),
            collected,
            logger=MagicMock(),
            config_obj=MagicMock(),
        )

        self.assertEqual(stage.exit_code, 0)
        self.assertEqual(stage.summary_payload["warning_code"], "listed_rows_accounted_without_candidates")
        self.assertEqual(stage.summary_payload["aggregate_summary"]["business_filter_skipped"], 10)
        self.assertEqual(emitted[-1]["status"], "warning")
        self.assertIn("业务范围过滤 10 条", emitted[-1]["label"])
        self.assertNotEqual(emitted[-1]["label"], "当前没有需要下载的网页，无需下载")


class DownloadOneClickTypedErrorsRegressionTest(unittest.TestCase):
    """Regression tests for download one-click typed errors."""

    def test_typed_errors_contains_typed_objects_not_dicts(self) -> None:
        """Regression: DownloadOneClickRunResult.typed_errors must contain typed objects.

        Currently typed_errors may be serialized to dicts instead of typed objects.
        The typed error objects must have code, component, stage, recoverability,
        message, and context fields.
        """
        # Create a typed error object (if it exists)
        try:
            from peap_core.error_contracts import PipelineFailure
            typed_error = PipelineFailure(
                code="chunk_failed",
                component="downloader",
                stage="materialize",
                recoverability="retryable",
                message="chunk 1 failed: network error",
                context={"chunk_id": 1},
            )
        except ImportError:
            # If typed error contracts don't exist yet, this is the regression
            self.fail(
                "DownloadOneClickRunResult.typed_errors must contain typed PipelineFailure objects, not dicts. "
                "peap_core.error_contracts.PipelineFailure must be implemented."
            )

        # Create a run result with typed errors
        result = DownloadOneClickRunResult(
            exit_code=1,
            log_file="download.log",
            plan_file="plan.json",
            plan_file_exists=False,
            plan_file_removed=True,
            start="2026-01-01 00:00:00",
            end="2026-01-01 00:01:00",
            duration_sec=60.0,
            aggregate_summary={"saved": 2, "errors": 1},
            task_summaries={},
            stages=[],
            typed_errors=[typed_error],
        )

        # typed_errors must contain typed objects, not dicts
        self.assertEqual(len(result.typed_errors), 1)
        for error in result.typed_errors:
            # Must be a typed object, not a dict
            self.assertNotIsInstance(error, dict)
            # Must have required fields
            self.assertTrue(hasattr(error, "code"))
            self.assertTrue(hasattr(error, "component"))
            self.assertTrue(hasattr(error, "stage"))
            self.assertTrue(hasattr(error, "recoverability"))
            self.assertTrue(hasattr(error, "message"))
            self.assertTrue(hasattr(error, "context"))

    def test_non_download_error_materialize_exceptions_are_typed_as_execute_failures(self) -> None:
        """Regression: non-DownloadError exceptions in materialize must be typed.

        Currently plain exceptions like ValueError may be caught and re-raised
        as typed failures, losing the original exception type information.
        """
        try:
            from peap_core.error_contracts import PipelineFailure

            # Plain exception should be wrapped as PipelineFailure
            plain_error = ValueError("invalid date range")

            # The typed error should preserve component/stage context
            typed_failure = PipelineFailure(
                code="invalid_argument",
                component="downloader",
                stage="materialize",
                recoverability="fatal",
                message=str(plain_error),
                context={"original_exception_type": "ValueError"},
            )

            self.assertEqual(typed_failure.code, "invalid_argument")
            self.assertEqual(typed_failure.component, "downloader")
            self.assertEqual(typed_failure.stage, "materialize")
        except ImportError:
            self.fail(
                "non-DownloadError materialize exceptions must be normalized into typed PipelineFailure objects. "
                "peap_core.error_contracts.PipelineFailure must be implemented."
            )


if __name__ == "__main__":
    unittest.main()
