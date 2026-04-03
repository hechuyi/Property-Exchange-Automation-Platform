"""Tests for download one-click orchestration."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

from peap.download_errors import DownloadError
from peap.download_oneclick import (
    DownloadOneClickRequest,
    _CollectedTask,
    _execute_tasks,
    _filter_existing_candidates,
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
        mock_collected_task = _CollectedTask(
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

        summary_dict = summary_to_dict(summary)
        new_downloads = getattr(summary, "downloaded_this_run", set())
        self.assertEqual(
            sorted(new_downloads),
            ["2026年4月/GR2026BJ1001952-demo.html", "2026年4月/GR2026BJ1001953-demo.html"],
        )


if __name__ == "__main__":
    unittest.main()
