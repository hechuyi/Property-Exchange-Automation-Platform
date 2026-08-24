from __future__ import annotations

import unittest

from desktop_backend.domain.legacy_mapping_export import build_legacy_mapping_export_files


class LegacyMappingExportTest(unittest.TestCase):
    def test_build_legacy_mapping_export_files_rejects_missing_entries_instead_of_defaulting_empty(
        self,
    ) -> None:
        for entries in (None, False):
            with self.subTest(entries=entries):
                with self.assertRaisesRegex(TypeError, "legacy mapping export entries"):
                    build_legacy_mapping_export_files(entries)  # type: ignore[arg-type]

    def test_build_legacy_mapping_export_files_rejects_non_mapping_entries(self) -> None:
        for raw_entry in ([], [("source_name", "华润")], "source"):
            with self.subTest(raw_entry=raw_entry):
                with self.assertRaisesRegex(
                    TypeError, "legacy mapping export entries must be objects"
                ):
                    build_legacy_mapping_export_files([raw_entry])  # type: ignore[list-item]

    def test_build_legacy_mapping_export_files_rejects_malformed_entries_with_index_and_field(
        self,
    ) -> None:
        valid_entry = {
            "source_name": "华润置地",
            "rule_kind": "transferor_group",
            "target_value": "央企",
        }
        malformed_cases = (
            ({"rule_kind": "transferor_group", "target_value": "央企"}, "source_name"),
            ({"source_name": "华润置地", "target_value": "央企"}, "rule_kind"),
            ({"source_name": "华润置地", "rule_kind": "transferor_group"}, "target_value"),
            (
                {"source_name": "华润置地", "rule_kind": "unknown", "target_value": "央企"},
                "rule_kind",
            ),
        )
        for malformed_entry, field in malformed_cases:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, rf"entry 1.*{field}"):
                    build_legacy_mapping_export_files([valid_entry, malformed_entry])


if __name__ == "__main__":
    unittest.main()
