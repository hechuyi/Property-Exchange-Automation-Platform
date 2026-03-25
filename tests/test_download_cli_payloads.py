from __future__ import annotations

import unittest

from peap.download_cli_payloads import (
    download_error_to_summary_payload,
    download_result_to_summary_payload,
    download_run_finished_message,
    download_task_list_to_summary_payload,
    format_task_list_lines,
)
from peap.download_models import DownloadRunResult


class DownloadCliPayloadsTest(unittest.TestCase):
    def test_format_task_list_lines_and_summary_payload(self) -> None:
        tasks = [
            {
                "task_id": "sse:physical_asset",
                "display_name": "SSE Physical",
                "default_page_size": 25,
            }
        ]

        lines = format_task_list_lines(tasks)
        payload = download_task_list_to_summary_payload(
            tasks,
            generated_at="2026-03-13 17:10:00",
        )

        self.assertEqual(lines[0], "Registered downloader tasks:")
        self.assertEqual(lines[1], "- sse:physical_asset | SSE Physical | default_page_size=25")
        self.assertEqual(payload["kind"], "download_task_list")
        self.assertEqual(payload["generated_at"], "2026-03-13 17:10:00")
        self.assertEqual(payload["tasks"][0]["task_id"], "sse:physical_asset")

    def test_download_result_to_summary_payload_includes_optional_metadata(self) -> None:
        result = DownloadRunResult(
            exit_code=1,
            task_count=2,
            aggregate_summary={"saved": 3, "errors": 1},
            task_summaries={
                "sse:physical_asset": {
                    "display_name": "SSE Physical",
                    "summary": {"saved": 3, "errors": 1},
                    "errors": ["detail failed"],
                }
            },
            errors=["detail failed"],
            any_failure=True,
        )

        payload = download_result_to_summary_payload(
            result,
            log_file="C:\\temp\\download.log",
            split_plan_only=True,
            generated_at="2026-03-13 17:11:00",
            start="2026-03-13 17:00:00",
            end="2026-03-13 17:11:00",
            duration_sec=11.236,
        )

        self.assertEqual(payload["kind"], "download")
        self.assertEqual(payload["exit_code"], 1)
        self.assertEqual(payload["log_file"], "C:\\temp\\download.log")
        self.assertEqual(payload["start"], "2026-03-13 17:00:00")
        self.assertEqual(payload["end"], "2026-03-13 17:11:00")
        self.assertEqual(payload["duration_sec"], 11.236)
        self.assertTrue(payload["split_plan_only"])
        self.assertEqual(payload["aggregate_summary"]["saved"], 3)
        self.assertEqual(payload["errors"], ["detail failed"])

    def test_download_error_to_summary_payload_and_finished_message(self) -> None:
        result = DownloadRunResult(
            exit_code=0,
            task_count=1,
            aggregate_summary={},
            task_summaries={},
            errors=[],
            any_failure=False,
        )

        payload = download_error_to_summary_payload(
            log_file="C:\\temp\\download.log",
            split_plan_only=False,
            error="boom",
        )
        message = download_run_finished_message(
            result=result,
            log_file="C:\\temp\\download.log",
            start="2026-03-13 17:00:00",
            end="2026-03-13 17:01:00",
            duration_sec=60.0,
        )

        self.assertEqual(payload["exit_code"], 2)
        self.assertEqual(payload["errors"], ["boom"])
        self.assertIn("status=OK", message)
        self.assertIn("tasks=1", message)


if __name__ == "__main__":
    unittest.main()
