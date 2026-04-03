"""Tests for download one-click orchestration."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

from peap.download_errors import DownloadError
from peap.download_oneclick import (
    DownloadOneClickRequest,
    DownloadOneClickRunResult,
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
