from __future__ import annotations

import unittest

from desktop_backend.legacy_contract import (
    legacy_mapping_source_name,
    legacy_record_scope_page_size,
)
from desktop_backend.request_contract import build_record_scope_payload_from_query


class LegacyContractTest(unittest.TestCase):
    def test_legacy_mapping_source_name_prefers_canonical_field(self) -> None:
        self.assertEqual(
            legacy_mapping_source_name({"source_name": "规范来源", "company_name": "历史来源"}),
            "规范来源",
        )

    def test_legacy_mapping_source_name_falls_back_to_company_name(self) -> None:
        self.assertEqual(
            legacy_mapping_source_name({"company_name": "历史来源"}),
            "历史来源",
        )

    def test_legacy_record_scope_page_size_prefers_page_size_before_limit(self) -> None:
        self.assertEqual(legacy_record_scope_page_size({"page_size": "25", "limit": "50"}), "25")
        self.assertEqual(legacy_record_scope_page_size({"limit": "50"}), "50")

    def test_build_record_scope_payload_from_query_keeps_limit_alias_in_one_place(self) -> None:
        scope = build_record_scope_payload_from_query({"limit": ["25"]})

        self.assertEqual(scope["page_size"], 25)
        self.assertEqual(scope["page"], 1)


if __name__ == "__main__":
    unittest.main()