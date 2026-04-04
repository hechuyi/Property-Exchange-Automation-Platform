"""Tests for download CLI payload helpers."""

from __future__ import annotations

import unittest

from peap.download_cli_payloads import download_result_to_summary_payload
from peap.download_models import DownloadRunResult, TaskTypedErrorList


class DownloadCliPayloadsTest(unittest.TestCase):
    def test_download_result_payload_preserves_task_new_downloads(self) -> None:
        """Verify new_downloads from task summaries survive CLI payload serialization."""
        result = DownloadRunResult(
            exit_code=0,
            task_count=1,
            aggregate_summary={"saved": 1, "errors": 0},
            task_summaries={
                "cbex:physical_asset": {
                    "display_name": "Beijing (CBEX) - Physical Asset",
                    "summary": {"saved": 1, "errors": 0},
                    "errors": [],
                    "new_downloads": ["2026年4月/GR2026BJ1001952-demo.html"],
                }
            },
            typed_errors=TaskTypedErrorList(),
            any_failure=False,
        )

        payload = download_result_to_summary_payload(result, log_file="x.log", split_plan_only=False)
        self.assertEqual(
            payload["task_summaries"]["cbex:physical_asset"]["new_downloads"],
            ["2026年4月/GR2026BJ1001952-demo.html"],
        )

    def test_build_task_result_accepts_new_downloads(self) -> None:
        """Verify build_task_result accepts and serializes new_downloads parameter."""
        from peap.download_reporting import build_task_result

        result = build_task_result(
            display_name="Test Task",
            summary={"saved": 1, "errors": 0},
            new_downloads=["2026年4月/test.html", "2026年4月/test2.html"],
        )
        self.assertEqual(result["new_downloads"], ["2026年4月/test.html", "2026年4月/test2.html"])

    def test_summary_metadata_to_dict_includes_downloaded_this_run(self) -> None:
        """Verify summary_metadata_to_dict extracts downloaded_this_run as new_downloads."""
        from peap.download_reporting import summary_metadata_to_dict
        from peap.downloaders.common import DownloadSummary

        summary = DownloadSummary()
        summary.downloaded_this_run.add("2026年4月/test.html")
        summary.downloaded_this_run.add("2026年4月/test2.html")

        metadata = summary_metadata_to_dict(summary)
        self.assertEqual(metadata["new_downloads"], ["2026年4月/test.html", "2026年4月/test2.html"])


if __name__ == "__main__":
    unittest.main()
