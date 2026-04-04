from __future__ import annotations

import unittest

from peap.download_errors import DownloadError, collect_failed_error


class DownloadErrorsTest(unittest.TestCase):
    def test_collect_failed_error_builds_typed_download_error(self) -> None:
        error = collect_failed_error(
            source_id="tpre",
            task_id="tpre:physical_asset",
            raw_reason="upstream 500",
        )

        self.assertIsInstance(error, DownloadError)
        self.assertEqual(error.error_code, "tpre_collect_failed")
        self.assertEqual(error.stage, "prepare_tasks")
        self.assertEqual(error.failure_kind, "collect")
        self.assertEqual(error.task_id, "tpre:physical_asset")
        self.assertEqual(error.raw_reason, "upstream 500")
        self.assertEqual(error.source_id, "tpre")
        self.assertEqual(error.error_message, "tpre: collect-failed: upstream 500")
        self.assertEqual(str(error), "tpre: collect-failed: upstream 500")

    def test_download_error_payload_uses_source_id_as_single_public_source_field(self) -> None:
        error = DownloadError(
            error_code="custom_execute_failed",
            error_message="Custom execute formatter message",
            stage="save_pages",
            failure_kind="execute",
            source_id="custom",
            task_id="custom:task",
            raw_reason="detail fetch timeout",
        )

        payload = error.to_presenter_payload()

        self.assertEqual(payload["error_details"]["source_id"], "custom")
        self.assertEqual(payload["error_details"]["task_id"], "custom:task")
        self.assertNotIn("exchange", payload["error_details"])
        self.assertEqual(
            set(payload["error_details"].keys()),
            {"source_id", "stage", "failure_kind", "raw_reason", "task_id"},
        )

    def test_collect_failed_error_preserves_source_id_without_exchange_alias(self) -> None:
        error = collect_failed_error(
            source_id="SSE",
            task_id="SSE:physical_asset",
            raw_reason="upstream 500",
        )

        payload = error.to_presenter_payload()

        self.assertEqual(error.source_id, "sse")
        self.assertEqual(error.task_id, "sse:physical_asset")
        self.assertEqual(payload["error_code"], "sse_collect_failed")
        self.assertEqual(payload["error_message"], "sse: collect-failed: upstream 500")
        self.assertEqual(payload["error_details"]["source_id"], "sse")
        self.assertNotIn("exchange", payload["error_details"])


if __name__ == "__main__":
    unittest.main()
