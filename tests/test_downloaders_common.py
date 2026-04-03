from __future__ import annotations

import datetime as dt
import unittest

from peap.downloaders import DownloadSummary as ExportedDownloadSummary
from peap.download_errors import DownloadError
from peap.downloaders.common import (
    DownloadSummary,
    in_date_range,
    parse_bound,
    parse_loose_date,
    safe_filename,
)


class DownloadersCommonTest(unittest.TestCase):
    def test_parse_loose_date_accepts_epoch_and_localized_text(self) -> None:
        self.assertEqual(parse_loose_date(1704067200000), dt.date(2024, 1, 1))
        self.assertEqual(parse_loose_date("2026年3月10日"), dt.date(2026, 3, 10))
        self.assertEqual(parse_loose_date("2026/03/11"), dt.date(2026, 3, 11))

    def test_parse_bound_accepts_empty_and_rejects_invalid(self) -> None:
        self.assertIsNone(parse_bound("", "start-date"))
        self.assertIsNone(parse_bound(None, "end-date"))
        with self.assertRaisesRegex(ValueError, "invalid start-date"):
            parse_bound("not-a-date", "start-date")

    def test_in_date_range_and_safe_filename_share_common_rules(self) -> None:
        value = dt.date(2026, 3, 10)
        self.assertTrue(in_date_range(value, dt.date(2026, 3, 1), dt.date(2026, 3, 31)))
        self.assertFalse(in_date_range(value, dt.date(2026, 3, 11), dt.date(2026, 3, 31)))
        self.assertEqual(safe_filename('a/b:c*?"<>|'), "a_b_c_")

    def test_download_summary_exposes_shared_skip_counters(self) -> None:
        summary = DownloadSummary()
        exported_summary = ExportedDownloadSummary()
        self.assertEqual(summary.skipped_by_duplicate, 0)
        self.assertEqual(summary.skipped_by_missing_xmid, 0)
        self.assertEqual(summary.detail_candidates, 0)
        self.assertIsInstance(exported_summary, DownloadSummary)
        self.assertEqual(exported_summary.detail_candidates, 0)
        self.assertEqual(exported_summary.skipped_by_duplicate, 0)

    def test_download_summary_uses_typed_errors_without_legacy_string_channel(self) -> None:
        typed_error = DownloadError(
            error_code="sse_collect_failed",
            error_message="sse: collect-failed: upstream 500",
            stage="prepare_tasks",
            failure_kind="collect",
            source_id="sse",
            task_id="sse:physical_asset",
            raw_reason="upstream 500",
        )

        summary = DownloadSummary(
            typed_errors=[typed_error],
        )

        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors, [typed_error])
        self.assertEqual(summary.typed_errors[0].source_id, "sse")
        self.assertNotIn("exchange", summary.typed_errors[0].to_presenter_payload()["error_details"])
        self.assertEqual(summary.typed_errors[0].to_presenter_payload()["error_details"]["source_id"], "sse")
        self.assertEqual(str(summary.typed_errors[0]), "sse: collect-failed: upstream 500")


if __name__ == "__main__":
    unittest.main()
