from __future__ import annotations

import unittest

from desktop_backend.progress_contract import (
    TERMINAL_JOB_STATUSES,
    build_progress_view,
    is_terminal_job_status,
    sanitize_terminal_progress,
)


class ProgressContractTest(unittest.TestCase):
    def test_terminal_status_detection_includes_success_and_failure_states(self) -> None:
        self.assertIn("success", TERMINAL_JOB_STATUSES)
        self.assertTrue(is_terminal_job_status("success"))
        self.assertTrue(is_terminal_job_status("failed"))
        self.assertFalse(is_terminal_job_status("running"))

    def test_build_progress_view_clears_running_context_and_keeps_metrics_as_list(self) -> None:
        view = build_progress_view(
            job={
                "job_id": "job-1",
                "job_type": "one_click",
                "record_family": "listing",
                "status": "success",
            },
            raw_progress={
                "job_status": "success",
                "phase_code": "save_pages",
                "phase_label": "正在保存网页",
                "current_item_label": "任务 A",
                "current_index": 7,
                "current_total": 9,
                "latest_stage_code": "save_pages",
                "latest_stage_label": "正在保存网页",
                "latest_stage_summary": "done",
            },
            summary={
                "downloaded_count": 4,
                "persisted_count": 3,
                "exception_count": 1,
                "pending_mapping_count": 2,
                "skipped_count": 1,
            },
        )

        self.assertEqual(view["job_id"], "job-1")
        self.assertEqual(view["record_family"], "listing")
        self.assertTrue(view["is_terminal"])
        self.assertEqual(view["current_item_label"], "")
        self.assertEqual(view["current_index"], 0)
        self.assertEqual(view["current_total"], 0)
        self.assertIsInstance(view["metrics"], list)
        self.assertEqual(view["latest_stage_summary"], {"text": "done"})
        self.assertNotIn("archive_pending_count", view)
        self.assertNotIn("archive_completed_count", view)

        sanitized = sanitize_terminal_progress(
            {
                "job_status": "failed",
                "current_item_label": "任务 B",
                "current_index": 2,
                "current_total": 5,
                "metrics": [{"key": "x", "label": "X", "value": 1}],
            }
        )
        self.assertEqual(sanitized["current_item_label"], "")
        self.assertEqual(sanitized["current_index"], 0)
        self.assertEqual(sanitized["current_total"], 0)
        self.assertIsInstance(sanitized["metrics"], list)

    def test_build_progress_view_keeps_stage_summary_separate_from_metrics_and_drops_unknown_keys(self) -> None:
        view = build_progress_view(
            job={"job_id": "job-1", "job_type": "one_click", "status": "running"},
            raw_progress={
                "job_status": "running",
                "latest_stage_summary": {
                    "collected_candidates": 11,
                    "detail_candidates": 10,
                    "detail_date_skipped": 8,
                    "internal_only_count": 99,
                },
            },
            summary={
                "downloaded_count": 12,
                "persisted_count": 7,
                "pending_review_count": 1,
                "pending_mapping_count": 2,
                "mapping_conflict_count": 3,
                "failed_count": 4,
            },
        )

        self.assertEqual(
            view["latest_stage_summary"],
            {
                "collected_candidates": 11,
                "detail_candidates": 10,
                "detail_date_skipped": 8,
            },
        )
        self.assertEqual(
            view["metrics"],
            [
                {"key": "downloaded_count", "label": "已下载", "value": 12},
                {"key": "persisted_count", "label": "已归档", "value": 7},
                {"key": "pending_review_count", "label": "待人工复核", "value": 1},
                {"key": "pending_mapping_count", "label": "待补映射", "value": 2},
                {"key": "mapping_conflict_count", "label": "映射冲突", "value": 3},
                {"key": "failed_count", "label": "失败", "value": 4},
            ],
        )

    def test_build_progress_view_preserves_extended_stage_summary_skip_metrics(self) -> None:
        view = build_progress_view(
            job={"job_id": "job-1", "job_type": "one_click", "status": "success_with_warnings"},
            raw_progress={
                "job_status": "success_with_warnings",
                "latest_stage_summary": {
                    "listed": 30,
                    "list_date_skipped": 30,
                    "date_missing_skipped": 0,
                    "resume_skipped": 0,
                    "business_filter_skipped": 4,
                    "list_unaccounted": 0,
                    "warning_code": "all_listed_rows_outside_date_range",
                    "warning_message": "已列出 30 条，30 条因披露日期不在范围内被跳过",
                    "internal_only_count": 99,
                },
            },
        )

        self.assertEqual(
            view["latest_stage_summary"],
            {
                "listed": 30,
                "list_date_skipped": 30,
                "date_missing_skipped": 0,
                "resume_skipped": 0,
                "business_filter_skipped": 4,
                "list_unaccounted": 0,
                "warning_code": "all_listed_rows_outside_date_range",
                "warning_message": "已列出 30 条，30 条因披露日期不在范围内被跳过",
            },
        )

    def test_build_progress_view_rejects_corrupt_present_progress_counters(self) -> None:
        for field_name in ("current_index", "current_total"):
            with self.subTest(field_name=field_name):
                raw_progress = {"job_status": "running", field_name: {"bad": 1}}
                with self.assertRaisesRegex(ValueError, field_name):
                    build_progress_view(
                        job={"job_id": "job-1", "job_type": "one_click", "status": "running"},
                        raw_progress=raw_progress,
                    )

    def test_build_progress_view_rejects_corrupt_known_stage_summary_numbers(self) -> None:
        with self.assertRaisesRegex(ValueError, "latest_stage_summary.listed"):
            build_progress_view(
                job={"job_id": "job-1", "job_type": "one_click", "status": "running"},
                raw_progress={
                    "job_status": "running",
                    "latest_stage_summary": {"listed": "not-int"},
                },
            )

    def test_build_progress_view_rejects_unknown_record_family_instead_of_falling_back(self) -> None:
        with self.assertRaises(ValueError):
            build_progress_view(
                job={
                    "job_id": "job-unknown-family",
                    "job_type": "one_click",
                    "status": "running",
                    "metadata": {"record_family": "unknown_family"},
                },
                raw_progress={"job_status": "running"},
            )

    def test_build_progress_view_treats_all_record_family_as_aggregate_job_scope(self) -> None:
        view = build_progress_view(
            job={
                "job_id": "job-multi-family",
                "job_type": "one_click",
                "status": "running",
                "metadata": {
                    "record_family": "all",
                    "record_families": ["listing", "deal"],
                    "family_scopes": [
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "business_label": "股权转让",
                            "exchange": "sse",
                        },
                        {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "business_label": "股权转让成交",
                            "exchange": "sse",
                        },
                    ],
                },
            },
            raw_progress={"job_status": "running"},
        )

        self.assertEqual(view["record_family"], "")
        self.assertEqual(view["business_id"], "")
        self.assertEqual(
            view["scope"],
            {
                "record_families": ["listing", "deal"],
                "family_scopes": [
                    {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "business_label": "股权转让",
                        "exchange": "sse",
                    },
                    {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "business_label": "股权转让成交",
                        "exchange": "sse",
                    },
                ],
            },
        )

    def test_build_progress_view_exposes_business_identity_and_scope_from_job_metadata(self) -> None:
        view = build_progress_view(
            job={
                "job_id": "job-business-scope",
                "job_type": "one_click",
                "status": "running",
                "metadata": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "business_label": "股权转让",
                    "scope": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "business_label": "股权转让",
                        "exchange": "sse",
                    },
                },
            },
            raw_progress={"job_status": "running"},
        )

        self.assertEqual(view["record_family"], "listing")
        self.assertIn("business_id", view)
        self.assertEqual(view["business_id"], "equity_transfer")
        self.assertIn("business_label", view)
        self.assertEqual(view["business_label"], "股权转让")
        self.assertIn("scope", view)
        self.assertEqual(
            view["scope"],
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "sse",
            },
        )

    def test_build_progress_view_preserves_business_re_evaluation_metrics(self) -> None:
        view = build_progress_view(
            job={
                "job_id": "job-business-re-eval",
                "job_type": "business_re_evaluation",
                "status": "running",
            },
            raw_progress={"job_status": "running"},
            summary={
                "pending_review_count": 3,
                "accepted_completed_count": 7,
                "skipped_count": 1,
                "failed_count": 0,
                "pending_mapping_count": 99,
                "mapping_conflict_count": 2,
            },
        )

        self.assertEqual(
            view["metrics"],
            [
                {"key": "pending_review_count", "label": "待人工复核", "value": 3},
                {"key": "pending_mapping_count", "label": "待补映射", "value": 99},
                {"key": "mapping_conflict_count", "label": "映射冲突", "value": 2},
                {"key": "accepted_completed_count", "label": "已采纳", "value": 7},
                {"key": "skipped_count", "label": "已跳过", "value": 1},
                {"key": "failed_count", "label": "失败", "value": 0},
            ],
        )

    def test_sanitize_terminal_progress_rejects_non_mapping_progress(self) -> None:
        with self.assertRaises(TypeError):
            sanitize_terminal_progress([("job_status", "failed")])

    def test_sanitize_terminal_progress_rejects_non_list_metrics(self) -> None:
        for metrics in ({}, "abc", False):
            with self.subTest(metrics=metrics):
                with self.assertRaisesRegex(TypeError, "raw_progress.metrics must be a list"):
                    sanitize_terminal_progress(
                        {
                            "job_status": "running",
                            "metrics": metrics,
                        }
                    )

    def test_sanitize_terminal_progress_allows_missing_or_null_metrics(self) -> None:
        for raw_progress in ({"job_status": "running"}, {"job_status": "failed", "metrics": None}):
            with self.subTest(raw_progress=raw_progress):
                sanitized = sanitize_terminal_progress(raw_progress)

                self.assertEqual(sanitized["metrics"], [])

    def test_build_progress_view_rejects_non_mapping_top_level_inputs(self) -> None:
        with self.assertRaises(TypeError):
            build_progress_view(job=[("job_id", "job-1")], raw_progress={"job_status": "running"})

        with self.assertRaises(TypeError):
            build_progress_view(job=None, raw_progress=[("job_status", "running")])

    def test_build_progress_view_still_allows_missing_job(self) -> None:
        view = build_progress_view(job=None, raw_progress={"job_id": "job-1", "job_status": "running"})

        self.assertEqual(view["job_id"], "job-1")
        self.assertEqual(view["job_status"], "running")


if __name__ == "__main__":
    unittest.main()
