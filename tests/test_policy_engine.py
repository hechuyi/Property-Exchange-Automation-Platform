from __future__ import annotations

import unittest

from peap_core import CanonicalRecord


class PolicyEngineTest(unittest.TestCase):
    def test_policy_engine_applies_mapping_rules_to_canonical_record_with_typed_patches(self) -> None:
        from peap.policy_engine import apply_policies_to_canonical_record

        canonical = CanonicalRecord(
            record_id="rec-001",
            record_family="listing",
            source_identity={"source_id": "beijing"},
            business_identity={"project_code": "P001"},
            canonical_fields={
                "project_code": "P001",
                "project_name": "政策项目",
                "seller": "上海测试公司",
                "group_name": "",
                "source_type": "",
            },
            field_provenance={},
            normalizer_version="record_normalizer/v1",
        )
        mapping_entries = [
            {
                "company_name": "上海测试公司",
                "group_name": "上海测试集团",
                "source_type": "",
                "metadata": {"match_field": "transferor", "target_field": "group_name"},
            },
            {
                "company_name": "上海测试集团",
                "group_name": "",
                "source_type": "市属",
                "metadata": {"match_field": "group", "target_field": "source_type"},
            },
        ]

        updated, patches, diagnostics = apply_policies_to_canonical_record(
            canonical,
            mapping_entries=mapping_entries,
        )

        self.assertEqual(updated.canonical_fields["group_name"], "上海测试集团")
        self.assertEqual(updated.canonical_fields["source_type"], "市属")
        self.assertEqual(patches[0]["field"], "group_name")
        self.assertEqual(patches[0]["new_value"], "上海测试集团")
        self.assertEqual(patches[1]["field"], "source_type")
        self.assertTrue(any(item.type == "mapping_applied" for item in diagnostics))

    def test_policy_engine_surfaces_conflict_when_mapping_disagrees_with_existing_high_confidence_field(self) -> None:
        from peap.policy_engine import apply_policies_to_canonical_record

        canonical = CanonicalRecord(
            record_id="rec-002",
            record_family="listing",
            source_identity={"source_id": "beijing"},
            business_identity={"project_code": "P002"},
            canonical_fields={
                "project_code": "P002",
                "project_name": "冲突项目",
                "seller": "华润测试公司",
                "group_name": "华润",
                "source_type": "地方国企",
            },
            field_provenance={
                "source_type": {"confidence": 1.0, "snapshot_id": "snap-2"},
            },
            normalizer_version="record_normalizer/v1",
        )
        mapping_entries = [
            {
                "company_name": "华润测试公司",
                "group_name": "",
                "source_type": "央企",
                "metadata": {"match_field": "transferor", "target_field": "source_type"},
            }
        ]

        updated, patches, diagnostics = apply_policies_to_canonical_record(
            canonical,
            mapping_entries=mapping_entries,
        )

        self.assertEqual(updated.canonical_fields["source_type"], "地方国企")
        self.assertEqual(patches, ())
        self.assertTrue(any(item.type in {"mapping_conflict", "policy_conflict"} for item in diagnostics))


if __name__ == "__main__":
    unittest.main()
