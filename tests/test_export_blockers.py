import unittest

from desktop_backend.domain.export_blockers import summarize_export_blocker_categories
from peap_core.record_state_policy import ExportBlockerCategory


class ExportBlockerSummaryTest(unittest.TestCase):
    def test_summarize_export_blocker_categories_accepts_none_as_empty_counts(self) -> None:
        totals = summarize_export_blocker_categories(None)

        self.assertEqual(totals[ExportBlockerCategory.PENDING_MAPPING], 0)
        self.assertEqual(totals[ExportBlockerCategory.MAPPING_CONFLICT], 0)

    def test_summarize_export_blocker_categories_rejects_non_mapping_counts(self) -> None:
        invalid_inputs = [
            [],
            "pending_mapping",
            [("pending_mapping", 2)],
        ]

        for invalid_input in invalid_inputs:
            with self.subTest(invalid_input=invalid_input):
                with self.assertRaisesRegex(TypeError, "scope_state_counts must be a mapping"):
                    summarize_export_blocker_categories(invalid_input)  # type: ignore[arg-type]

    def test_summarize_export_blocker_categories_preserves_count_int_conversion(self) -> None:
        totals = summarize_export_blocker_categories({"pending_mapping": "2", "conflict": 1, "ready": 3})

        self.assertEqual(totals[ExportBlockerCategory.PENDING_MAPPING], 2)
        self.assertEqual(totals[ExportBlockerCategory.CONFLICT], 1)
        self.assertEqual(totals[ExportBlockerCategory.NONE], 3)


if __name__ == "__main__":
    unittest.main()
