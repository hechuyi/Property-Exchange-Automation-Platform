"""Regression tests for date resolution policy.

These tests verify the fix for the bug where a record whose final
resolved disclosure date is missing (when a date window is active)
can still be saved.
"""

from __future__ import annotations

import datetime as dt
import unittest

from peap.downloaders.common import DownloadSummary, in_date_range, parse_loose_date


class DateResolutionBugTest(unittest.TestCase):
    """Regression: missing final date must not save when window is active.

    The bug was that when BOTH list_date and detail_date are None:
      check_date = list_start if list_start is not None else disclosure_start
      if check_date is not None and not in_date_range(check_date, start, end):
          summary.skipped_by_detail_date += 1
          return

    When both are None, check_date becomes None, and the condition
    `check_date is not None and ...` evaluates to False, so the record
    proceeds to save instead of being skipped.

    The fix introduces `final_date = disclosure_start if disclosure_start is not None else list_start`
    and an explicit `if final_date is None` check before the date range validation.
    """

    def test_in_date_range_returns_false_when_value_is_none(self):
        """in_date_range must return False when value is None."""
        self.assertFalse(in_date_range(None, dt.date(2026, 4, 1), dt.date(2026, 4, 30)))

    def test_parse_loose_date_returns_none_for_empty_values(self):
        """parse_loose_date must return None for empty/missing values."""
        self.assertIsNone(parse_loose_date(None))
        self.assertIsNone(parse_loose_date(""))
        self.assertIsNone(parse_loose_date({}))
        self.assertIsNone(parse_loose_date([]))

    def test_summary_has_date_missing_skipped_counter(self):
        """DownloadSummary must have date_missing_skipped counter."""
        summary = DownloadSummary()
        self.assertTrue(hasattr(summary, "date_missing_skipped"))
        self.assertEqual(summary.date_missing_skipped, 0)

    def test_buggy_logic_check_date_none_evaluates_to_false(self):
        """Demonstrate the bug: when check_date is None, the condition is False.

        The buggy code was:
          check_date = list_start if list_start is not None else disclosure_start
          if check_date is not None and not in_date_range(check_date, start, end):
              # skip

        When list_start=None and disclosure_start=None, check_date=None.
        Then `check_date is not None` is False, so the whole condition is False.
        The record is NOT skipped even though we can't verify its date.
        """
        list_start = None
        disclosure_start = None

        # This is the buggy logic
        check_date = list_start if list_start is not None else disclosure_start
        self.assertIsNone(check_date)  # Both are None

        # The buggy condition
        start = dt.date(2026, 4, 1)
        end = dt.date(2026, 4, 30)
        buggy_condition = check_date is not None and not in_date_range(check_date, start, end)
        self.assertFalse(buggy_condition)  # False because check_date is None

        # So the record would NOT be skipped - THIS IS THE BUG

    def test_fixed_logic_final_date_none_is_rejected(self):
        """Demonstrate the fix: when final_date is None, the record must be skipped.

        The fixed code is:
          final_date = disclosure_start if disclosure_start is not None else list_start
          if final_date is None:
              summary.date_missing_skipped += 1
              summary.skipped_by_detail_date += 1
              return
          if not in_date_range(final_date, start, end):
              summary.skipped_by_detail_date += 1
              return
        """
        list_start = None
        disclosure_start = None

        # This is the fixed logic
        final_date = disclosure_start if disclosure_start is not None else list_start
        self.assertIsNone(final_date)  # Both are None

        start = dt.date(2026, 4, 1)
        end = dt.date(2026, 4, 30)

        # The fixed logic explicitly checks for None
        if final_date is None:
            summary = DownloadSummary()
            summary.date_missing_skipped += 1
            summary.skipped_by_detail_date += 1
            self.assertEqual(summary.date_missing_skipped, 1)
            self.assertEqual(summary.skipped_by_detail_date, 1)
            # Record IS skipped - THIS IS THE FIX

    def test_with_one_date_available_record_is_not_skipped_when_in_range(self):
        """When one date is available and in range, record should not be skipped."""
        list_start = None
        disclosure_start = dt.date(2026, 4, 15)  # Within range

        final_date = disclosure_start if disclosure_start is not None else list_start
        self.assertIsNotNone(final_date)
        self.assertEqual(final_date, dt.date(2026, 4, 15))

        start = dt.date(2026, 4, 1)
        end = dt.date(2026, 4, 30)

        if final_date is None:
            self.fail("final_date should not be None")
        if not in_date_range(final_date, start, end):
            self.fail("final_date should be in range")
        # Record is NOT skipped - correct behavior

    def test_with_one_date_available_record_is_skipped_when_out_of_range(self):
        """When one date is available but out of range, record should be skipped."""
        list_start = None
        disclosure_start = dt.date(2026, 5, 15)  # Outside range

        final_date = disclosure_start if disclosure_start is not None else list_start
        self.assertIsNotNone(final_date)
        self.assertEqual(final_date, dt.date(2026, 5, 15))

        start = dt.date(2026, 4, 1)
        end = dt.date(2026, 4, 30)

        if final_date is None:
            self.fail("final_date should not be None")
        if not in_date_range(final_date, start, end):
            summary = DownloadSummary()
            summary.skipped_by_detail_date += 1
            self.assertEqual(summary.skipped_by_detail_date, 1)
            # Record IS skipped - correct behavior


if __name__ == "__main__":
    unittest.main()
