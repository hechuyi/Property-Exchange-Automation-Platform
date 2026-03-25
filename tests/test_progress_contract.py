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


if __name__ == "__main__":
    unittest.main()
