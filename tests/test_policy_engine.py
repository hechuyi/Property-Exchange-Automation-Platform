from __future__ import annotations

import unittest

from peap_core import CanonicalRecord


class PolicyEngineEndToEndFieldPreservationTest(unittest.TestCase):
    """Tests that policy engine preserves all required canonical fields."""

    def test_policy_engine_preserves_all_required_canonical_fields_without_project_type(self) -> None:
        """Policy engine should preserve canonical business fields without requiring project_type."""
        from peap.policy_engine import apply_policies_to_canonical_record

        canonical = CanonicalRecord(
            record_id="rec-001",
            record_family="listing",
            source_identity={"source_id": "beijing"},
            business_identity={"project_code": "P001"},
            canonical_fields={
                "project_code": "P001",
                "project_name": "测试项目",
                "business_type": "股权转让",
                "status": "挂牌",
                "start_date": "2026/03/31",
                "price": "108.00",
                "seller": "上海测试公司",
                "source_type": "",
                "group_name": "",
            },
            field_provenance={},
            normalizer_version="record_normalizer/v1",
        )

        updated, patches, diagnostics = apply_policies_to_canonical_record(
            canonical,
            mapping_entries=[],
        )

        # All required fields must be preserved
        self.assertEqual(updated.canonical_fields.get("business_type"), "股权转让")
        self.assertNotIn("project_type", updated.canonical_fields)
        self.assertEqual(updated.canonical_fields.get("status"), "挂牌")
        self.assertEqual(updated.canonical_fields.get("start_date"), "2026/03/31")
        self.assertEqual(updated.canonical_fields.get("price"), "108.00")
        self.assertEqual(updated.canonical_fields.get("seller"), "上海测试公司")
        self.assertEqual(updated.canonical_fields.get("source_type"), "")
        self.assertEqual(updated.canonical_fields.get("group_name"), "")

    def test_policy_engine_preserves_fields_with_existing_group_and_type_without_project_type(self) -> None:
        """Policy engine should preserve business fields even when enrichment fields are already set."""
        from peap.policy_engine import apply_policies_to_canonical_record

        canonical = CanonicalRecord(
            record_id="rec-002",
            record_family="listing",
            source_identity={"source_id": "beijing"},
            business_identity={"project_code": "P002"},
            canonical_fields={
                "project_code": "P002",
                "project_name": "已有集团项目",
                "business_type": "股权转让",
                "status": "挂牌",
                "start_date": "2026/03/31",
                "price": "200.00",
                "seller": "华润测试公司",
                "group_name": "华润集团",
                "source_type": "央企",
            },
            field_provenance={},
            normalizer_version="record_normalizer/v1",
        )
        mapping_entries = [
            {
                "company_name": "华润测试公司",
                "group_name": "华润集团",
                "source_type": "央企",
                "metadata": {"match_field": "transferor", "target_field": "group_name"},
            },
        ]

        updated, patches, diagnostics = apply_policies_to_canonical_record(
            canonical,
            mapping_entries=mapping_entries,
        )

        # All required fields must be preserved
        self.assertEqual(updated.canonical_fields.get("business_type"), "股权转让")
        self.assertNotIn("project_type", updated.canonical_fields)
        self.assertEqual(updated.canonical_fields.get("status"), "挂牌")
        self.assertEqual(updated.canonical_fields.get("start_date"), "2026/03/31")
        self.assertEqual(updated.canonical_fields.get("price"), "200.00")
        self.assertEqual(updated.canonical_fields.get("seller"), "华润测试公司")
        self.assertEqual(updated.canonical_fields.get("group_name"), "华润集团")
        self.assertEqual(updated.canonical_fields.get("source_type"), "央企")


class RecordStatePolicyBusinessResolutionTest(unittest.TestCase):
    def test_classify_record_state_prefers_business_resolution_over_mapping_gap(self) -> None:
        from peap.streaming_models import PostProcessFinding
        from peap_core.pipeline_state_contracts import RecordState
        from peap_core.record_state_policy import classify_record_state

        findings = [
            PostProcessFinding(
                severity="warn",
                type="business_resolution_required",
                message="业务类型未识别",
                evidence={"raw_business_label": "未知业务"},
            ),
            PostProcessFinding(
                severity="warn",
                type="mapping_missing",
                message="缺少类型映射",
                evidence={"missing_fields": ["类型"]},
            ),
        ]

        self.assertEqual(classify_record_state(findings), RecordState.PENDING_REVIEW)

    def test_classify_record_state_does_not_treat_legacy_project_type_unknown_as_active_vocab(self) -> None:
        from peap.streaming_models import PostProcessFinding
        from peap_core.pipeline_state_contracts import RecordState
        from peap_core.record_state_policy import classify_record_state

        findings = [
            PostProcessFinding(
                severity="warn",
                type="project_type_unknown",
                message="历史词汇仅允许出现在显式 maintenance 归一化路径",
            )
        ]

        self.assertEqual(classify_record_state(findings), RecordState.READY)

    def test_business_resolution_state_does_not_reuse_mapping_backlog(self) -> None:
        from peap_core.pipeline_state_contracts import RecordState
        from peap_core.record_state_policy import state_requires_mapping_pending

        self.assertFalse(state_requires_mapping_pending(RecordState.PENDING_REVIEW))


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
