from __future__ import annotations

import ast
import inspect
import textwrap
import unittest
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

import peap.streaming_postprocess as streaming_postprocess
from peap.streaming_models import PostProcessFinding
from peap.streaming_postprocess import (
    RecordPostprocessContext,
    analyze_mapping_candidates,
    apply_mapping_entries,
    apply_policy_engine_to_payload,
    finalize_streaming_payload,
    is_optional_rule_finding,
    is_summary_investor_name,
    normalize_record_payload,
    reapply_optional_rule_findings,
    run_record_postprocess,
)
from peap_core.pipeline_state_contracts import RecordState
from peap_core.record_state_policy import classify_record_state


def _family_descriptor(family_id: str) -> SimpleNamespace:
    return SimpleNamespace(family_id=family_id)


def _fake_family_catalog(mapping: dict[str, str]):
    normalized_mapping = {key.lower(): value for key, value in mapping.items()}

    def fake_get_family_descriptor(value: object) -> SimpleNamespace:
        key = str(value or "").strip().lower()
        if key not in normalized_mapping:
            raise KeyError(value)
        return _family_descriptor(normalized_mapping[key])

    return fake_get_family_descriptor


class StreamingPostprocessFamilyCatalogTest(unittest.TestCase):
    def test_finalize_streaming_payload_normalizes_catalog_alias_outside_local_family_constants(self) -> None:
        family_catalog = _fake_family_catalog(
            {
                "CATALOG_ALIAS": "catalog_family",
                "catalog_family": "catalog_family",
            }
        )

        with patch("peap.streaming_postprocess.get_family_descriptor", create=True, side_effect=family_catalog) as get_family_descriptor:
            payload, findings = finalize_streaming_payload(
                {
                    "record_family": "CATALOG_ALIAS",
                    "项目编号": "G32026BJ1000901",
                    "项目名称": "目录别名记录",
                    "项目类型": "股权转让",
                }
            )

        self.assertEqual(payload.get("record_family"), "catalog_family")
        self.assertFalse(
            any(
                item.type == "business_resolution_required"
                and item.evidence.get("reason_code") == "invalid_record_family"
                for item in findings
            )
        )
        get_family_descriptor.assert_any_call("CATALOG_ALIAS")

    def test_context_family_conflict_reports_catalog_canonical_family_ids(self) -> None:
        family_catalog = _fake_family_catalog(
            {
                "PAYLOAD_ALIAS": "payload_family",
                "payload_family": "payload_family",
                "CONTEXT_ALIAS": "context_family",
                "context_family": "context_family",
            }
        )

        with patch("peap.streaming_postprocess.get_family_descriptor", create=True, side_effect=family_catalog):
            payload, findings = normalize_record_payload(
                parser_payload={
                    "record_family": "PAYLOAD_ALIAS",
                    "项目编号": "G32026BJ1000902",
                    "项目名称": "上下文族冲突记录",
                    "项目类型": "股权转让",
                },
                postprocess_payload={},
                context=RecordPostprocessContext(record_family="CONTEXT_ALIAS"),
            )

        self.assertEqual(payload.get("record_family"), "context_family")
        family_conflict = next(item for item in findings if item.type == "business_resolution_required")
        self.assertEqual(family_conflict.evidence.get("reason_code"), "record_family_conflict")
        self.assertEqual(family_conflict.evidence.get("payload_record_family"), "payload_family")
        self.assertEqual(family_conflict.evidence.get("context_record_family"), "context_family")

    def test_normalize_record_family_has_no_local_listing_deal_identity_set_gate(self) -> None:
        source = textwrap.dedent(inspect.getsource(streaming_postprocess._normalize_record_family))
        tree = ast.parse(source)

        forbidden_sets: list[ast.Set] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Set):
                continue
            literal_values = {
                item.value
                for item in node.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
            symbol_names = {item.id for item in node.elts if isinstance(item, ast.Name)}
            if literal_values == {"listing", "deal"} or symbol_names == {"LISTING_RECORD_FAMILY", "DEAL_RECORD_FAMILY"}:
                forbidden_sets.append(node)

        self.assertEqual(forbidden_sets, [])


class StreamingPostprocessPayloadContractTest(unittest.TestCase):
    def test_finalize_streaming_payload_rejects_explicit_non_mapping_payload(self) -> None:
        with self.assertRaisesRegex(TypeError, "payload must be a dict"):
            streaming_postprocess.finalize_streaming_payload([])

    def test_analyze_mapping_candidates_rejects_explicit_non_mapping_payload(self) -> None:
        with self.assertRaisesRegex(TypeError, "payload must be a dict"):
            streaming_postprocess.analyze_mapping_candidates([])

    def test_resolve_record_family_rejects_explicit_non_mapping_payload(self) -> None:
        with self.assertRaisesRegex(TypeError, "payload must be a dict"):
            streaming_postprocess._resolve_record_family([])

    def test_apply_mapping_entries_rejects_explicit_non_mapping_payload(self) -> None:
        with self.assertRaisesRegex(TypeError, "payload must be a dict"):
            streaming_postprocess.apply_mapping_entries([])

    def test_run_record_postprocess_rejects_explicit_non_mapping_payload(self) -> None:
        with self.assertRaisesRegex(TypeError, "payload must be a dict"):
            streaming_postprocess.run_record_postprocess([], source_file="sample.json")

    def test_apply_postprocess_context_rejects_explicit_non_mapping_payload(self) -> None:
        with self.assertRaisesRegex(TypeError, "payload must be a dict"):
            streaming_postprocess.apply_postprocess_context([])

    def test_merge_postprocess_payloads_rejects_explicit_non_mapping_parser_payload(self) -> None:
        with self.assertRaisesRegex(TypeError, "parser_payload must be a dict"):
            streaming_postprocess._merge_postprocess_payloads(
                parser_payload=[],
                postprocess_payload={},
            )

    def test_merge_postprocess_payloads_rejects_explicit_non_mapping_postprocess_payload(self) -> None:
        with self.assertRaisesRegex(TypeError, "postprocess_payload must be a dict"):
            streaming_postprocess._merge_postprocess_payloads(
                parser_payload={},
                postprocess_payload=[],
            )

    def test_postprocess_payload_contract_preserves_none_as_empty_payload(self) -> None:
        self.assertEqual(streaming_postprocess.apply_postprocess_context(None), {})
        self.assertEqual(
            streaming_postprocess._merge_postprocess_payloads(
                parser_payload=None,
                postprocess_payload=None,
            ),
            {},
        )

    def test_analyze_mapping_candidates_rejects_non_iterable_mapping_entries(self) -> None:
        with self.assertRaisesRegex(TypeError, "mapping_entries must be an iterable of mappings"):
            streaming_postprocess.analyze_mapping_candidates(
                {"转让方": "上海测试公司"},
                mapping_entries=False,  # type: ignore[arg-type]
            )

    def test_analyze_mapping_candidates_rejects_mapping_entries_container_mapping(self) -> None:
        with self.assertRaisesRegex(TypeError, "mapping_entries must be an iterable of mappings"):
            streaming_postprocess.analyze_mapping_candidates(
                {"转让方": "上海测试公司"},
                mapping_entries={},  # type: ignore[arg-type]
            )

    def test_analyze_mapping_candidates_rejects_non_mapping_mapping_entry(self) -> None:
        with self.assertRaisesRegex(TypeError, r"mapping_entries\[\*\] must be a dict"):
            streaming_postprocess.analyze_mapping_candidates(
                {"转让方": "上海测试公司"},
                mapping_entries=[[]],  # type: ignore[list-item]
            )


class StreamingPostprocessMappingTest(unittest.TestCase):
    def test_anonymous_pre_disclosure_uses_supervision_field_for_type_without_mapping_gap(self) -> None:
        payload, findings = run_record_postprocess(
            {
                "record_family": "listing",
                "项目编号": "G32026BJ1000182-0",
                "项目名称": "人形机器人（上海）有限公司25%股权",
                "项目类型": "预披露",
                "转让方": "某企业",
                "state_asset_supervisor": "国务院国资委监管",
                "economic_type": "国有控股企业",
                "seller_credit_code": "91110106MA7H5M9J58",
            },
            source_file="/tmp/cbex-anonymous-pre-disclosure.html",
            mapping_entries=[],
        )

        self.assertEqual(payload.get("类型"), "央企")
        self.assertEqual(classify_record_state(findings), RecordState.READY)
        self.assertFalse(any(item.type == "mapping_gap" for item in findings))
        self.assertFalse(any(item.type == "mapping_missing" for item in findings))
        self.assertFalse(
            any(
                item.type == "mapping_advisory"
                and item.evidence.get("recommended_rule", {}).get("source_name") == "某企业"
                for item in findings
            )
        )

    def test_transferor_mapping_uses_primary_ratio_subject_for_group_and_type(self) -> None:
        payload = {
            "项目编号": "G32026SH1000100",
            "转让方": "烟台顺达海洋工程服务有限责任公司(99.5%) 上海诺亚船舶修理有限公司(0.5%)",
        }
        entries = [
            {
                "company_name": "烟台顺达海洋工程服务有限责任公司",
                "group_name": "烟台顺达集团",
                "source_type": "",
                "metadata": {"match_field": "transferor", "target_field": "group_name"},
            },
            {
                "company_name": "烟台顺达集团",
                "group_name": "",
                "source_type": "民营",
                "metadata": {"match_field": "group", "target_field": "source_type"},
            },
        ]

        analysis = analyze_mapping_candidates(payload, mapping_entries=entries)
        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertEqual(analysis["company_name"], "烟台顺达海洋工程服务有限责任公司")
        self.assertEqual(analysis["resolved_group"], "烟台顺达集团")
        self.assertEqual(analysis["resolved_type"], "民营")
        self.assertEqual(resolved["隶属集团"], "烟台顺达集团")
        self.assertEqual(resolved["类型"], "民营")
        self.assertTrue(any(item.type == "mapping_applied" for item in findings))

    def test_transferor_mapping_does_not_apply_minor_ratio_subject(self) -> None:
        payload = {
            "项目编号": "G32026SH1000101",
            "转让方": "烟台顺达海洋工程服务有限责任公司(99.5%) 上海诺亚船舶修理有限公司(0.5%)",
        }
        entries = [
            {
                "company_name": "上海诺亚船舶修理有限公司",
                "group_name": "上海诺亚集团",
                "source_type": "",
                "metadata": {"match_field": "transferor", "target_field": "group_name"},
            }
        ]

        analysis = analyze_mapping_candidates(payload, mapping_entries=entries)
        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertEqual(analysis["company_name"], "烟台顺达海洋工程服务有限责任公司")
        self.assertNotIn("隶属集团", resolved)
        self.assertFalse(any(item.type == "mapping_applied" for item in findings))

    def test_placeholder_current_group_does_not_conflict_with_transferor_group_mapping(self) -> None:
        payload = {
            "record_family": "listing",
            "项目编号": "GR2026BJ1003341",
            "项目类型": "实物资产",
            "转让方": "莒县中联水泥有限公司港中分公司",
            "隶属集团": "-",
        }
        entries = [
            {
                "company_name": "莒县中联水泥有限公司港中分公司",
                "group_name": "中国建材集团有限公司",
                "source_type": "",
                "metadata": {"match_field": "transferor", "target_field": "group_name"},
            },
            {
                "company_name": "中国建材集团有限公司",
                "group_name": "",
                "source_type": "央企",
                "metadata": {"match_field": "group", "target_field": "source_type"},
            },
        ]

        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertEqual(resolved["隶属集团"], "中国建材集团有限公司")
        self.assertEqual(resolved["类型"], "央企")
        self.assertFalse(any(item.type == "mapping_conflict" for item in findings))

    def test_current_group_is_normalized_through_group_chain_before_conflict_detection(self) -> None:
        payload = {
            "record_family": "listing",
            "项目编号": "G32026SH1000136",
            "项目类型": "股权转让",
            "转让方": "华润江中药业股份有限公司",
            "隶属集团": "中国华润有限公司",
        }
        entries = [
            {
                "company_name": "华润江中药业股份有限公司",
                "group_name": "华润（集团）有限公司",
                "source_type": "",
                "metadata": {"match_field": "transferor", "target_field": "group_name"},
            },
            {
                "company_name": "中国华润有限公司",
                "group_name": "华润（集团）有限公司",
                "source_type": "",
                "metadata": {"match_field": "group", "target_field": "group_name"},
            },
            {
                "company_name": "华润（集团）有限公司",
                "group_name": "",
                "source_type": "央企",
                "metadata": {"match_field": "group", "target_field": "source_type"},
            },
        ]

        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertEqual(resolved["隶属集团"], "华润（集团）有限公司")
        self.assertEqual(resolved["类型"], "央企")
        self.assertFalse(any(item.type == "mapping_conflict" for item in findings))

    def test_transferor_group_chain_and_group_type_are_applied(self) -> None:
        payload = {
            "项目编号": "G32026SH1000001",
            "转让方": "上海测试公司",
        }
        entries = [
            {
                "company_name": "上海测试公司",
                "group_name": "上海测试集团二级公司",
                "source_type": "",
                "metadata": {"match_field": "transferor", "target_field": "group_name"},
            },
            {
                "company_name": "上海测试集团二级公司",
                "group_name": "上海测试集团",
                "source_type": "",
                "metadata": {"match_field": "group", "target_field": "group_name"},
            },
            {
                "company_name": "上海测试集团",
                "group_name": "",
                "source_type": "市属",
                "metadata": {"match_field": "group", "target_field": "source_type"},
            },
        ]

        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertEqual(resolved["隶属集团"], "上海测试集团")
        self.assertEqual(resolved["类型"], "市属")
        self.assertTrue(any(item.type == "mapping_applied" for item in findings))

    def test_conflicting_transferor_type_and_group_type_become_explicit_conflict(self) -> None:
        payload = {
            "项目编号": "G32026SH1000002",
            "转让方": "北京测试公司",
        }
        entries = [
            {
                "company_name": "北京测试公司",
                "group_name": "北京测试集团",
                "source_type": "",
                "metadata": {"match_field": "transferor", "target_field": "group_name"},
            },
            {
                "company_name": "北京测试公司",
                "group_name": "",
                "source_type": "央企",
                "metadata": {"match_field": "transferor", "target_field": "source_type"},
            },
            {
                "company_name": "北京测试集团",
                "group_name": "",
                "source_type": "市属",
                "metadata": {"match_field": "group", "target_field": "source_type"},
            },
        ]

        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertEqual(resolved["隶属集团"], "北京测试集团")
        self.assertNotIn("类型", resolved)
        self.assertTrue(any(item.type == "mapping_conflict" for item in findings))

    def test_deal_family_ignores_listing_only_mapping_conflict_blocker(self) -> None:
        payload = {
            "record_family": "deal",
            "项目编号": "D32026SH1000002",
            "项目类型": "股权转让",
            "转让方": "北京测试公司",
        }
        entries = [
            {
                "company_name": "北京测试公司",
                "group_name": "北京测试集团A",
                "source_type": "",
                "metadata": {"match_field": "transferor", "target_field": "group_name"},
            },
            {
                "company_name": "北京测试公司",
                "group_name": "北京测试集团B",
                "source_type": "",
                "metadata": {"match_field": "transferor", "target_field": "group_name"},
            },
        ]

        _, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertFalse(any(item.type == "mapping_conflict" for item in findings))
        non_blocking = [item for item in findings if item.type == "mapping_conflict_non_blocking"]
        self.assertEqual(len(non_blocking), 1)
        self.assertEqual(non_blocking[0].severity, "info")
        self.assertTrue(non_blocking[0].evidence.get("candidate_resolutions"))
        self.assertNotEqual(classify_record_state(findings), RecordState.MAPPING_CONFLICT)

    def test_analyze_mapping_candidates_recommends_group_type_after_group_is_resolved(self) -> None:
        payload = {
            "项目编号": "G32026SH1000004",
            "转让方": "中铁二院工程集团有限责任公司",
        }
        entries = [
            {
                "company_name": "中铁二院工程集团有限责任公司",
                "group_name": "中铁",
                "source_type": "",
                "metadata": {"match_field": "transferor", "target_field": "group_name"},
            }
        ]

        analysis = analyze_mapping_candidates(payload, mapping_entries=entries)

        self.assertEqual(analysis["resolved_group"], "中铁")
        self.assertEqual(analysis["gap_codes"], ["missing_type"])
        self.assertEqual(analysis["recommended_rule"]["rule_kind"], "group_type")
        self.assertEqual(analysis["recommended_rule"]["source_name"], "中铁")

    def test_analyze_mapping_candidates_recommends_group_type_for_group_only_payload(self) -> None:
        payload = {
            "项目编号": "G32026SH1000004-1",
            "隶属集团": "中粮生物科技股份有限公司",
        }

        analysis = analyze_mapping_candidates(payload, mapping_entries=[])

        self.assertEqual(analysis["resolved_group"], "中粮生物科技股份有限公司")
        self.assertEqual(analysis["gap_codes"], ["missing_type"])
        self.assertEqual(analysis["recommended_rule"]["rule_kind"], "group_type")
        self.assertEqual(analysis["recommended_rule"]["source_name"], "中粮生物科技股份有限公司")

    def test_analyze_mapping_candidates_marks_missing_type_even_when_group_is_unresolved(self) -> None:
        analysis = analyze_mapping_candidates(
            {
                "项目编号": "G32026SH1000004-0",
                "转让方": "上海测试公司",
            },
            mapping_entries=[],
        )

        self.assertEqual(analysis["gap_codes"], ["missing_group", "missing_type"])
        self.assertEqual(analysis["recommended_rule"]["rule_kind"], "transferor_type")
        self.assertEqual(analysis["recommended_rule"]["source_name"], "上海测试公司")

    def test_group_only_payload_can_apply_group_type_mapping(self) -> None:
        payload = {
            "项目编号": "G32026SH1000004-2",
            "隶属集团": "中粮生物科技股份有限公司",
        }
        entries = [
            {
                "company_name": "中粮生物科技股份有限公司",
                "group_name": "",
                "source_type": "央企",
                "metadata": {"match_field": "group", "target_field": "source_type"},
            }
        ]

        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertEqual(resolved["类型"], "央企")
        self.assertTrue(any(item.type == "mapping_applied" for item in findings))

    def test_mapping_chain_syncs_canonical_aliases_before_export_projection(self) -> None:
        from peap.export_projection import project_canonical_record_to_export_payload
        from peap.standard_model import build_standard_project
        from peap_core import CanonicalRecord

        payload = {
            "record_family": "listing",
            "business_id": "equity_transfer",
            "项目编号": "G32026BJ1000999",
            "项目名称": "中粮映射链回归项目",
            "项目类型": "股权转让",
            "项目状态": "挂牌",
            "交易所": "北交所",
            "挂牌开始日期": "2026/07/01",
            "挂牌价格": "100.00",
            "转让方": "中粮贸易有限公司",
            "group_name": "中粮贸易有限公司",
            "隶属集团": "中粮贸易有限公司",
            "source_type": "地方国企",
            "类型": "地方国企",
        }
        entries = [
            {
                "company_name": "中粮贸易有限公司",
                "group_name": "中粮集团有限公司",
                "source_type": "",
                "metadata": {
                    "match_field": "group",
                    "target_field": "group_name",
                    "authoritative": True,
                },
            },
            {
                "company_name": "中粮集团有限公司",
                "group_name": "",
                "source_type": "央企",
                "metadata": {
                    "match_field": "group",
                    "target_field": "source_type",
                    "authoritative": True,
                },
            },
        ]

        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertFalse(any(item.type == "mapping_conflict" for item in findings))
        self.assertEqual(resolved["group_name"], "中粮集团有限公司")
        self.assertEqual(resolved["隶属集团"], "中粮集团有限公司")
        self.assertEqual(resolved["source_type"], "央企")
        self.assertEqual(resolved["类型"], "央企")

        canonical_fields = build_standard_project(resolved).to_standard_dict()
        self.assertEqual(canonical_fields["group_name"], "中粮集团有限公司")
        self.assertEqual(canonical_fields["source_type"], "央企")

        canonical = CanonicalRecord(
            record_id="rec-mapping-chain",
            record_family="listing",
            source_identity={"source_id": "cbex", "business_id": "equity_transfer"},
            business_identity={
                "project_code": "G32026BJ1000999",
                "business_id": "equity_transfer",
                "raw_business_label": "股权转让",
            },
            canonical_fields=canonical_fields,
            field_provenance={},
            normalizer_version="streaming_ingest/v1",
        )
        projection, _ = project_canonical_record_to_export_payload(canonical, fail_on_missing=False)

        self.assertEqual(projection["隶属集团"], "中粮集团有限公司")
        self.assertEqual(projection["类型"], "央企")

    def test_mapping_syncs_stale_english_aliases_when_chinese_fields_are_resolved(self) -> None:
        payload = {
            "record_family": "listing",
            "项目编号": "G32026BJ1001000",
            "转让方": "中粮贸易有限公司",
            "隶属集团": "中粮集团有限公司",
            "group_name": "中粮贸易有限公司",
            "类型": "央企",
            "source_type": "地方国企",
        }

        resolved, _ = apply_mapping_entries(payload, mapping_entries=[])

        self.assertEqual(resolved["隶属集团"], "中粮集团有限公司")
        self.assertEqual(resolved["group_name"], "中粮集团有限公司")
        self.assertEqual(resolved["类型"], "央企")
        self.assertEqual(resolved["source_type"], "央企")

    def test_optional_person_rule_syncs_mapping_type_aliases_before_export(self) -> None:
        from peap.export_projection import project_canonical_record_to_export_payload
        from peap.standard_model import build_standard_project
        from peap_core import CanonicalRecord

        payload = {
            "record_family": "listing",
            "business_id": "physical_asset",
            "项目编号": "TA2026BJ1006501",
            "项目名称": "福州市测试房产转让",
            "项目类型": "实物资产",
            "挂牌开始日期": "2026/08/11",
            "挂牌价格": 300.0,
            "交易所": "北交所",
            "转让方": "何洁如",
            "隶属集团": "中共北京市委办公厅",
            "group_name": "中共北京市委办公厅",
        }
        entries = [
            {
                "company_name": "中共北京市委办公厅",
                "group_name": "",
                "source_type": "市属",
                "metadata": {"match_field": "group", "target_field": "source_type"},
            }
        ]

        resolved, findings = run_record_postprocess(
            payload,
            source_file="/tmp/TA2026BJ1006501.html",
            mapping_entries=entries,
            rules_config={
                "R011_person_transferor_private": {
                    "enabled": True,
                    "priority": 40,
                    "params": {"override_existing": True},
                }
            },
        )

        self.assertTrue(any(item.type == "person_transferor_marked_private" for item in findings))
        self.assertEqual(resolved["类型"], "民营")
        self.assertEqual(resolved["source_type"], "民营")

        canonical_fields = build_standard_project(resolved).to_standard_dict()
        self.assertEqual(canonical_fields["source_type"], "民营")
        canonical = CanonicalRecord(
            record_id="rec-person-transferor-private",
            record_family="listing",
            source_identity={"source_id": "cbex", "business_id": "physical_asset"},
            business_identity={
                "project_code": "TA2026BJ1006501",
                "business_id": "physical_asset",
                "raw_business_label": "实物资产",
            },
            canonical_fields=canonical_fields,
            field_provenance={},
            normalizer_version="streaming_ingest/v1",
        )
        projection, _ = project_canonical_record_to_export_payload(canonical, fail_on_missing=False)
        self.assertEqual(projection["类型"], "民营")

    def test_missing_group_with_existing_type_is_advisory_instead_of_blocking(self) -> None:
        resolved, findings = apply_mapping_entries(
            {
                "项目编号": "G32026SH1000004-3",
                "转让方": "上海测试公司",
                "类型": "央企",
            },
            mapping_entries=[],
        )

        self.assertEqual(resolved["类型"], "央企")
        self.assertFalse(any(item.type == "mapping_gap" for item in findings))
        self.assertFalse(any(item.type == "mapping_missing" for item in findings))

    def test_listing_physical_asset_without_mapping_subject_does_not_block_on_missing_type(self) -> None:
        payload, findings = run_record_postprocess(
            {
                "record_family": "listing",
                "business_id": "physical_asset",
                "项目编号": "TR2026TJ1000024",
                "项目名称": "江苏省南通市优山美地花园165幢房产",
                "项目类型": "实物资产",
                "挂牌价格": 6000.0,
                "挂牌开始日期": "2026/06/02",
                "挂牌截止日期": "2026/06/16",
                "交易所": "天交所",
            },
            source_file="/tmp/tpre-physical-without-transferor.html",
            mapping_entries=[],
        )

        self.assertEqual(payload["项目类型"], "实物资产")
        self.assertEqual(classify_record_state(findings), RecordState.READY)
        self.assertFalse(any(item.type == "mapping_gap" for item in findings))
        self.assertFalse(any(item.type == "mapping_missing" for item in findings))

    def test_normalize_record_payload_drops_legacy_missing_group_blocker_when_type_exists(self) -> None:
        payload, findings = normalize_record_payload(
            parser_payload={
                "项目编号": "G32026SH1000004-3",
                "项目类型": "股权转让",
                "转让方": "上海测试公司",
                "类型": "央企",
            },
            postprocess_payload={},
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_gap",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )

        self.assertEqual(payload["类型"], "央企")
        self.assertFalse(any(item.type == "mapping_gap" for item in findings))
        self.assertFalse(any("缺少集团" in item.message for item in findings))

    def test_normalize_record_payload_rewrites_legacy_group_and_type_gap_to_type_only_blocker(self) -> None:
        _, findings = normalize_record_payload(
            parser_payload={
                "record_family": "listing",
                "项目编号": "G32026SH1000004-4",
                "项目类型": "股权转让",
                "转让方": "上海测试公司",
            },
            postprocess_payload={},
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_gap",
                    message="缺少集团、类型，暂不能进入导出",
                    evidence={"missing_fields": ["集团", "类型"]},
                )
            ],
        )

        messages = [item.message for item in findings]
        self.assertIn("缺少类型，暂不能进入导出", messages)
        self.assertFalse(any("缺少集团" in message for message in messages))

    def test_run_record_postprocess_persists_optional_rule_id_for_listing_times_findings(self) -> None:
        _, findings = run_record_postprocess(
            {
                "项目编号": "G32026SH1000004-5",
                "项目类型": "股权转让",
                "record_family": "listing",
                "挂牌次数": "首次挂牌",
            },
            source_file="/tmp/sample.html",
            rules_config={
                "R006_derive_listing_times": {
                    "enabled": True,
                    "priority": 5,
                    "params": {},
                }
            },
        )

        listing_conflict = next(item for item in findings if item.type == "listing_times_conflict")
        self.assertEqual(listing_conflict.evidence.get("rule_id"), "R006_derive_listing_times")

    def test_optional_rule_apply_error_blocks_ready_classification(self) -> None:
        class ExplodingRule:
            @classmethod
            def rule_id(cls) -> str:
                return "R_TEST_EXPLODING"

            def apply(self, record: object, context: dict[str, object]) -> object:  # noqa: ARG002
                raise RuntimeError("optional rule failed")

        class FakeRuleRegistry:
            def build_plan(self, rules_config: dict[str, object], *, record_family: str | None = None) -> tuple[list[object], list[str]]:  # noqa: ARG002
                return [SimpleNamespace(rule=ExplodingRule())], []

        from peap_postprocess.postprocess_engine import rules as postprocess_rules

        with patch.object(postprocess_rules, "RuleRegistry", FakeRuleRegistry):
            _, findings = run_record_postprocess(
                {
                    "record_family": "listing",
                    "项目编号": "G32026SH1000004",
                    "项目名称": "规则异常项目",
                    "项目类型": "股权转让",
                    "类型": "央企",
                    "挂牌次数": "首次挂牌",
                },
                source_file="/tmp/sample_rule_error.html",
                rules_config={"R_TEST_EXPLODING": {"enabled": True}},
            )

        rule_error = next(item for item in findings if item.type == "rule_error")
        self.assertEqual(rule_error.severity, "error")
        self.assertEqual(rule_error.evidence.get("rule_id"), "R_TEST_EXPLODING")
        self.assertEqual(classify_record_state(findings), RecordState.PENDING_REVIEW)

    def test_run_record_postprocess_rejects_optional_rule_non_mapping_finding_evidence(self) -> None:
        from peap_postprocess.postprocess_engine.contracts import Finding, RuleResult

        class BadEvidenceRule:
            @classmethod
            def rule_id(cls) -> str:
                return "R_TEST_BAD_EVIDENCE"

            def apply(self, record: object, context: dict[str, object]) -> object:  # noqa: ARG002
                return RuleResult(
                    findings=[
                        Finding(
                            rule_id="R_TEST_BAD_EVIDENCE",
                            severity="warn",
                            type="rule_bad_evidence",
                            message="bad evidence shape",
                            evidence=False,  # type: ignore[arg-type]
                        )
                    ]
                )

        class FakeRuleRegistry:
            def build_plan(self, rules_config: dict[str, object], *, record_family: str | None = None) -> tuple[list[object], list[str]]:  # noqa: ARG002
                return [SimpleNamespace(rule=BadEvidenceRule())], []

        from peap_postprocess.postprocess_engine import rules as postprocess_rules

        with patch.object(postprocess_rules, "RuleRegistry", FakeRuleRegistry):
            with self.assertRaisesRegex(TypeError, "finding.evidence must be a mapping"):
                run_record_postprocess(
                    {
                        "record_family": "listing",
                        "项目编号": "G32026SH1000008",
                        "项目名称": "规则坏证据项目",
                        "项目类型": "股权转让",
                        "类型": "央企",
                        "转让方": "测试转让方",
                    },
                    source_file="/tmp/sample_rule_bad_evidence.html",
                    rules_config={"R_TEST_BAD_EVIDENCE": {"enabled": True}},
                )

    def test_reapply_optional_rule_findings_drops_legacy_listing_times_conflict_without_rule_id(self) -> None:
        _, findings = reapply_optional_rule_findings(
            parser_payload={
                "项目编号": "G32026SH1000004",
                "项目类型": "股权转让",
            },
            postprocess_payload={
                "项目编号": "G32026SH1000004",
                "项目类型": "股权转让",
                "挂牌次数": "首次挂牌",
                "listing_times": "1",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="listing_times_conflict",
                    message="listing_times conflict current=首次挂牌 derived=1",
                    evidence={"field": "挂牌次数"},
                )
            ],
            source_file="/tmp/sample.html",
            rules_config={
                "R006_derive_listing_times": {
                    "enabled": True,
                    "priority": 5,
                    "params": {},
                }
            },
        )

        self.assertFalse(any(item.type == "listing_times_conflict" for item in findings))

    def test_is_optional_rule_finding_rejects_explicit_non_mapping_evidence(self) -> None:
        with self.assertRaisesRegex(TypeError, "finding.evidence must be a mapping"):
            is_optional_rule_finding({"type": "listing_times_conflict", "evidence": []})
        with self.assertRaisesRegex(TypeError, "finding.evidence must be a mapping"):
            is_optional_rule_finding(
                PostProcessFinding(
                    severity="warn",
                    type="listing_times_conflict",
                    message="bad evidence shape",
                    evidence=[],  # type: ignore[arg-type]
                )
            )

    def test_finalize_streaming_payload_rejects_explicit_non_mapping_finding_evidence(self) -> None:
        with self.assertRaisesRegex(TypeError, "finding.evidence must be a mapping"):
            finalize_streaming_payload(
                {
                    "项目编号": "G32026SH1000007",
                    "项目名称": "坏证据对象项目",
                    "项目类型": "股权转让",
                    "类型": "央企",
                    "转让方": "测试转让方",
                },
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="business_resolution_required",
                        message="bad evidence shape",
                        evidence=False,  # type: ignore[arg-type]
                    )
                ],
            )

    def test_is_optional_rule_finding_accepts_mapping_evidence(self) -> None:
        self.assertTrue(
            is_optional_rule_finding(
                {
                    "type": "listing_times_conflict",
                    "evidence": MappingProxyType({"rule_id": "R006_derive_listing_times"}),
                }
            )
        )

    def test_apply_mapping_entries_supports_streaming_english_field_payloads(self) -> None:
        payload = {
            "project_code": "G62026SH1000006-0",
            "project_name": "环天智慧科技股份有限公司增资项目",
            "project_type": "预披露",
            "seller": "环天智慧科技股份有限公司",
            "group_name": "环天智慧科技股份有限公司",
            "source_type": "",
        }
        entries = [
            {
                "company_name": "环天智慧科技股份有限公司",
                "group_name": "",
                "source_type": "民营",
                "metadata": {"match_field": "group", "target_field": "source_type"},
            }
        ]

        analysis = analyze_mapping_candidates(payload, mapping_entries=entries)
        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertEqual(analysis["resolved_group"], "环天智慧科技股份有限公司")
        self.assertEqual(analysis["resolved_type"], "民营")
        self.assertEqual(resolved["类型"], "民营")
        self.assertTrue(any(item.type == "mapping_applied" for item in findings))

    def test_group_type_mapping_conflict_is_explicit_when_existing_type_differs(self) -> None:
        payload = {
            "项目编号": "G32026SH1000003",
            "转让方": "华润测试公司",
            "隶属集团": "华润",
            "类型": "地方国企",
        }
        entries = [
            {
                "company_name": "华润",
                "group_name": "",
                "source_type": "央企",
                "metadata": {"match_field": "group", "target_field": "source_type"},
            }
        ]

        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertEqual(resolved["类型"], "地方国企")
        self.assertTrue(any(item.type == "mapping_conflict" for item in findings))

    def test_explicit_group_rule_overrides_stale_current_group_without_reintroducing_conflict(self) -> None:
        payload = {
            "项目编号": "G32026SH1000005",
            "转让方": "中铁二院工程集团有限责任公司",
            "隶属集团": "中铁",
        }
        entries = [
            {
                "company_name": "中铁二院工程集团有限责任公司",
                "group_name": "中国铁路工程集团有限公司",
                "source_type": "",
                "metadata": {"match_field": "transferor", "target_field": "group_name", "authoritative": True},
            },
            {
                "company_name": "中国铁路工程集团有限公司",
                "group_name": "中铁",
                "source_type": "",
                "metadata": {"match_field": "group", "target_field": "group_name"},
            },
            {
                "company_name": "中铁",
                "group_name": "",
                "source_type": "央企",
                "metadata": {"match_field": "group", "target_field": "source_type"},
            },
        ]

        analysis = analyze_mapping_candidates(payload, mapping_entries=entries)
        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertFalse(analysis["has_conflict"])
        self.assertEqual(analysis["resolved_group"], "中铁")
        self.assertEqual(analysis["resolved_type"], "央企")
        self.assertEqual(resolved["隶属集团"], "中铁")
        self.assertEqual(resolved["类型"], "央企")
        self.assertFalse(any(item.type == "mapping_conflict" for item in findings))

    def test_authoritative_group_chain_rule_overrides_ordinary_group_chain_conflict(self) -> None:
        payload = {
            "项目编号": "G32026CQ1000022",
            "转让方": "重庆中梁山煤电气有限公司",
            "隶属集团": "华润（集团）有限公司",
            "类型": "央企",
        }
        entries = [
            {
                "company_name": "重庆中梁山煤电气有限公司",
                "group_name": "中国华润有限公司",
                "source_type": "",
                "metadata": {"match_field": "transferor", "target_field": "group_name", "authoritative": True},
            },
            {
                "company_name": "中国华润有限公司",
                "group_name": "旧普通集团",
                "source_type": "",
                "metadata": {"match_field": "group", "target_field": "group_name"},
            },
            {
                "company_name": "中国华润有限公司",
                "group_name": "华润（集团）有限公司",
                "source_type": "",
                "metadata": {"match_field": "group", "target_field": "group_name", "authoritative": True},
            },
        ]

        analysis = analyze_mapping_candidates(payload, mapping_entries=entries)
        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertFalse(analysis["has_conflict"])
        self.assertEqual(analysis["resolved_group"], "华润（集团）有限公司")
        self.assertEqual(resolved["隶属集团"], "华润（集团）有限公司")
        self.assertFalse(any(item.type == "mapping_conflict" for item in findings))

    def test_explicit_type_rule_overrides_stale_current_type_without_reintroducing_conflict(self) -> None:
        payload = {
            "项目编号": "G32026SH1000006",
            "转让方": "华润测试公司",
            "隶属集团": "华润",
            "类型": "地方国企",
        }
        entries = [
            {
                "company_name": "华润",
                "group_name": "",
                "source_type": "央企",
                "metadata": {"match_field": "group", "target_field": "source_type", "authoritative": True},
            }
        ]

        analysis = analyze_mapping_candidates(payload, mapping_entries=entries)
        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertFalse(analysis["has_conflict"])
        self.assertEqual(analysis["resolved_type"], "央企")
        self.assertEqual(resolved["类型"], "央企")
        self.assertFalse(any(item.type == "mapping_conflict" for item in findings))

    def test_authoritative_transferor_type_rule_overrides_conflicting_group_type(self) -> None:
        payload = {
            "项目编号": "G32026SH1000008",
            "转让方": "中铁二院工程集团有限责任公司",
            "隶属集团": "中铁",
        }
        entries = [
            {
                "company_name": "中铁二院工程集团有限责任公司",
                "group_name": "",
                "source_type": "部委",
                "metadata": {"match_field": "transferor", "target_field": "source_type", "authoritative": True},
            },
            {
                "company_name": "中铁",
                "group_name": "",
                "source_type": "央企",
                "metadata": {"match_field": "group", "target_field": "source_type"},
            },
        ]

        analysis = analyze_mapping_candidates(payload, mapping_entries=entries)
        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertFalse(analysis["has_conflict"])
        self.assertEqual(analysis["resolved_type"], "部委")
        self.assertEqual(resolved["类型"], "部委")
        self.assertFalse(any(item.type == "mapping_conflict" for item in findings))

    def test_authoritative_group_type_rule_overrides_conflicting_transferor_type(self) -> None:
        payload = {
            "项目编号": "G32026SH1000009",
            "转让方": "中铁二院工程集团有限责任公司",
            "隶属集团": "中铁",
        }
        entries = [
            {
                "company_name": "中铁二院工程集团有限责任公司",
                "group_name": "",
                "source_type": "部委",
                "metadata": {"match_field": "transferor", "target_field": "source_type"},
            },
            {
                "company_name": "中铁",
                "group_name": "",
                "source_type": "央企",
                "metadata": {"match_field": "group", "target_field": "source_type", "authoritative": True},
            },
        ]

        analysis = analyze_mapping_candidates(payload, mapping_entries=entries)
        resolved, findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertFalse(analysis["has_conflict"])
        self.assertEqual(analysis["resolved_type"], "央企")
        self.assertEqual(resolved["类型"], "央企")
        self.assertFalse(any(item.type == "mapping_conflict" for item in findings))

    def test_apply_policy_engine_to_payload_keeps_streaming_wrapper_shape(self) -> None:
        payload = {
            "项目编号": "G32026SH1000007",
            "转让方": "上海测试公司",
        }
        entries = [
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

        resolved, findings = apply_policy_engine_to_payload(payload, mapping_entries=entries)

        self.assertEqual(resolved["隶属集团"], "上海测试集团")
        self.assertEqual(resolved["类型"], "市属")
        self.assertTrue(any(item.type == "mapping_applied" for item in findings))


class StreamingPostprocessBusinessResolutionTest(unittest.TestCase):
    def test_finalize_streaming_payload_emits_business_resolution_finding_for_unknown_business_label(self) -> None:
        resolved, findings = finalize_streaming_payload(
            {
                "项目编号": "G32026BJ1999001",
                "项目名称": "未知业务项目",
                "项目类型": "火星资产",
                "转让方": "测试公司",
                "类型": "国资",
            }
        )

        self.assertEqual(resolved["项目类型"], "火星资产")
        self.assertFalse(any(item.type == "project_type_unknown" for item in findings))
        business_blocker = next(item for item in findings if item.type == "business_resolution_required")
        self.assertEqual(business_blocker.evidence.get("raw_business_label", ""), "")

    def test_finalize_streaming_payload_keeps_mapping_gap_only_as_business_resolution_diagnostic(self) -> None:
        _, findings = finalize_streaming_payload(
            {
                "record_family": "listing",
                "项目编号": "G32026BJ1999002",
                "项目名称": "未知业务且缺类型映射项目",
                "项目类型": "火星资产",
                "转让方": "测试公司",
            }
        )

        business_blocker = next(item for item in findings if item.type == "business_resolution_required")
        self.assertEqual(business_blocker.evidence.get("diagnostic_gap_codes"), ["missing_type"])
        self.assertFalse(any(item.type == "mapping_missing" for item in findings))


class StreamingPostprocessDealFamilyReadinessTest(unittest.TestCase):
    def test_finalize_streaming_payload_deal_does_not_require_source_type(self) -> None:
        _, findings = finalize_streaming_payload(
            {
                "record_family": "deal",
                "项目编号": "G32026BJ1000801",
                "项目名称": "成交项目",
                "项目类型": "股权转让",
                "转让方": "测试公司",
            }
        )

        self.assertFalse(any(item.type == "mapping_gap" for item in findings))
        self.assertFalse(any(item.type == "mapping_missing" for item in findings))

    def test_normalize_record_payload_applies_record_family_from_context(self) -> None:
        payload, findings = normalize_record_payload(
            parser_payload={
                "项目编号": "G32026BJ1000802",
                "项目名称": "成交项目",
                "项目类型": "股权转让",
            },
            postprocess_payload={},
            context=RecordPostprocessContext(record_family="deal"),
        )

        self.assertEqual(payload.get("record_family"), "deal")
        self.assertFalse(any(item.type == "mapping_gap" for item in findings))
        self.assertFalse(any(item.type == "mapping_missing" for item in findings))

    def test_context_family_conflict_blocks_stale_payload_family(self) -> None:
        payload, findings = run_record_postprocess(
            {
                "record_family": "listing",
                "项目编号": "D32026SH1000802A",
                "项目名称": "成交项目",
                "项目类型": "股权转让",
                "转让方": "测试公司",
            },
            source_file="/tmp/sample_deal_stale_family.html",
            context=RecordPostprocessContext(record_family="deal"),
        )

        self.assertEqual(payload.get("record_family"), "deal")
        family_conflict = next(item for item in findings if item.type == "business_resolution_required")
        self.assertEqual(family_conflict.evidence.get("reason_code"), "record_family_conflict")
        self.assertEqual(family_conflict.evidence.get("payload_record_family"), "listing")
        self.assertEqual(family_conflict.evidence.get("context_record_family"), "deal")
        self.assertFalse(any(item.type == "mapping_gap" for item in findings))
        self.assertFalse(any(item.type == "mapping_missing" for item in findings))
        self.assertEqual(classify_record_state(findings), RecordState.PENDING_REVIEW)

    def test_deal_business_identity_without_family_does_not_receive_listing_rules(self) -> None:
        payload, findings = run_record_postprocess(
            {
                "business_id": "deal_equity_transfer",
                "项目编号": "D32026SH1000802-2",
                "项目名称": "成交项目",
                "项目类型": "股权转让",
                "转让方": "测试公司",
            },
            source_file="/tmp/sample_deal_without_family.html",
            rules_config={
                "R006_derive_listing_times": {
                    "enabled": True,
                    "priority": 5,
                    "params": {},
                }
            },
        )

        self.assertEqual(payload.get("record_family"), "deal")
        self.assertNotIn("挂牌次数", payload)
        self.assertEqual(classify_record_state(findings), RecordState.READY)
        self.assertFalse(any(item.type == "mapping_gap" for item in findings))
        self.assertFalse(any(item.type == "mapping_missing" for item in findings))
        self.assertFalse(any(item.type == "listing_times_conflict" for item in findings))

    def test_missing_family_does_not_default_to_listing_rules(self) -> None:
        payload, findings = run_record_postprocess(
            {
                "项目编号": "G32026BJ1000802C-3",
                "项目名称": "未标记族的记录",
                "项目类型": "股权转让",
                "转让方": "测试公司",
            },
            source_file="/tmp/sample_unknown_family.html",
            rules_config={
                "R006_derive_listing_times": {
                    "enabled": True,
                    "priority": 5,
                    "params": {},
                }
            },
        )

        self.assertNotIn("record_family", payload)
        self.assertNotIn("挂牌次数", payload)
        self.assertFalse(any(item.type == "listing_times_conflict" for item in findings))

    def test_normalize_record_payload_context_family_overrides_invalid_payload_family(self) -> None:
        payload, findings = normalize_record_payload(
            parser_payload={
                "record_family": "listing_bad",
                "项目编号": "G32026BJ1000802A",
                "项目名称": "成交项目",
                "项目类型": "股权转让",
            },
            postprocess_payload={},
            context=RecordPostprocessContext(record_family="deal"),
        )

        self.assertEqual(payload.get("record_family"), "deal")
        self.assertEqual(classify_record_state(findings), RecordState.READY)
        self.assertFalse(any(item.type == "mapping_gap" for item in findings))
        self.assertFalse(any(item.type == "mapping_missing" for item in findings))

    def test_invalid_record_family_is_not_coerced_to_listing_mapping_blockers(self) -> None:
        payload, findings = finalize_streaming_payload(
            {
                "record_family": "listing_bad",
                "项目编号": "G32026BJ1000802B",
                "项目名称": "挂牌项目",
                "项目类型": "股权转让",
                "转让方": "测试公司",
            }
        )

        self.assertEqual(payload.get("record_family"), "listing_bad")
        family_resolution = next(item for item in findings if item.type == "business_resolution_required")
        self.assertEqual(family_resolution.evidence.get("reason_code"), "invalid_record_family")
        self.assertEqual(family_resolution.evidence.get("payload_record_family"), "listing_bad")
        self.assertEqual(classify_record_state(findings), RecordState.PENDING_REVIEW)
        self.assertFalse(any(item.type == "mapping_gap" for item in findings))
        self.assertFalse(any(item.type == "mapping_missing" for item in findings))

    def test_capital_increase_deal_without_non_summary_investor_enters_pending_review(self) -> None:
        _, findings = run_record_postprocess(
            {
                "record_family": "deal",
                "项目编号": "G62026SH1000803",
                "项目名称": "增资成交项目",
                "项目类型": "增资扩股",
                "investors": [{"investor_name": "总计", "investment_amount": "1000"}],
            },
            source_file="/tmp/sample_deal_capital.html",
        )

        self.assertTrue(any(item.type == "business_resolution_required" for item in findings))
        self.assertEqual(classify_record_state(findings), RecordState.PENDING_REVIEW)

    def test_capital_increase_deal_without_non_summary_investor_amount_enters_pending_review(self) -> None:
        for investor in ({"investor_name": "投资方甲", "investment_amount": ""}, {"investor_name": "投资方甲"}):
            with self.subTest(investor=investor):
                _, findings = run_record_postprocess(
                    {
                        "record_family": "deal",
                        "项目编号": "G62026SH1000803A",
                        "项目名称": "增资成交项目",
                        "项目类型": "增资扩股",
                        "investors": [investor],
                    },
                    source_file="/tmp/sample_deal_capital_missing_investor_amount.html",
                )

                self.assertTrue(
                    any(
                        item.type == "business_resolution_required"
                        and item.evidence.get("reason_code") == "deal_capital_increase_missing_investor_amount"
                        for item in findings
                    )
                )
                self.assertEqual(classify_record_state(findings), RecordState.PENDING_REVIEW)

    def test_capital_increase_deal_with_non_summary_investor_amount_skips_pending_review(self) -> None:
        _, findings = run_record_postprocess(
            {
                "record_family": "deal",
                "项目编号": "G62024SH1000060",
                "项目名称": "上海新微科技集团有限公司增资项目",
                "项目类型": "增资扩股",
                "investors": [
                    {
                        "investor_name": "上海思秘科企业管理服务合伙企业（有限合伙）",
                        "investment_amount": "20000.000000",
                        "holding_ratio": "2.059949",
                    },
                    {
                        "investor_name": "总计",
                        "investment_amount": "20000.000000",
                        "holding_ratio": "2.059949",
                    },
                ],
            },
            source_file="/tmp/sse_capital_increase_real_deal.html",
        )

        self.assertFalse(
            any(
                item.type == "business_resolution_required"
                and item.evidence.get("reason_code") == "deal_capital_increase_missing_investor_amount"
                for item in findings
            )
        )
        self.assertEqual(classify_record_state(findings), RecordState.READY)

    def test_capital_increase_deal_keeps_totalenergies_as_non_summary_investor(self) -> None:
        _, findings = run_record_postprocess(
            {
                "record_family": "deal",
                "项目编号": "G62026SH1000804",
                "项目名称": "增资成交项目",
                "项目类型": "增资扩股",
                "investors": [{"investor_name": "TotalEnergies", "investment_amount": "1000"}],
            },
            source_file="/tmp/sample_deal_capital_totalenergies.html",
        )

        self.assertFalse(any(item.type == "business_resolution_required" for item in findings))

    def test_capital_increase_deal_keeps_heji_company_as_non_summary_investor(self) -> None:
        _, findings = run_record_postprocess(
            {
                "record_family": "deal",
                "项目编号": "G62026SH1000805",
                "项目名称": "增资成交项目",
                "项目类型": "增资扩股",
                "investors": [{"investor_name": "合计投资有限公司", "investment_amount": "1000"}],
            },
            source_file="/tmp/sample_deal_capital_heji_company.html",
        )

        self.assertFalse(any(item.type == "business_resolution_required" for item in findings))

    def test_capital_increase_deal_treats_summary_marker_with_colon_as_summary(self) -> None:
        _, findings = run_record_postprocess(
            {
                "record_family": "deal",
                "项目编号": "G62026SH1000806",
                "项目名称": "增资成交项目",
                "项目类型": "增资扩股",
                "investors": [{"investor_name": "合计："}],
            },
            source_file="/tmp/sample_deal_capital_summary_colon.html",
        )

        self.assertTrue(any(item.type == "business_resolution_required" for item in findings))

    def test_capital_increase_deal_treats_semantic_summary_suffix_as_summary(self) -> None:
        for name in ("总计(万元)", "总计 1000", "total amount"):
            with self.subTest(name=name):
                _, findings = run_record_postprocess(
                    {
                        "record_family": "deal",
                        "项目编号": "G62026SH1000806A",
                        "项目名称": "增资成交项目",
                        "项目类型": "增资扩股",
                        "investors": [{"investor_name": name}],
                    },
                    source_file=f"/tmp/sample_deal_capital_{name}.html",
                )

                self.assertTrue(any(item.type == "business_resolution_required" for item in findings))

    def test_capital_increase_deal_summary_investor_with_punctuation_numeric_suffix_is_filtered(self) -> None:
        summary_names = ("总计：1000万元", "total:1000", "合计：1,000")
        for name in summary_names:
            with self.subTest(name=name):
                _, findings = run_record_postprocess(
                    {
                        "record_family": "deal",
                        "项目编号": "G62026SH1000806B",
                        "项目名称": "增资成交项目",
                        "项目类型": "增资扩股",
                        "investors": [{"investor_name": name}],
                    },
                    source_file=f"/tmp/sample_deal_capital_{name}.html",
                )
                self.assertTrue(any(item.type == "business_resolution_required" for item in findings))

        non_summary_names = ("TotalEnergies", "合计投资有限公司")
        for name in non_summary_names:
            with self.subTest(name=name):
                _, findings = run_record_postprocess(
                    {
                        "record_family": "deal",
                        "项目编号": "G62026SH1000806C",
                        "项目名称": "增资成交项目",
                        "项目类型": "增资扩股",
                        "investors": [{"investor_name": name, "investment_amount": "1000"}],
                    },
                    source_file=f"/tmp/sample_deal_capital_{name}.html",
                )
                self.assertFalse(any(item.type == "business_resolution_required" for item in findings))

    def test_is_summary_investor_name_exposes_stable_public_helper(self) -> None:
        self.assertTrue(is_summary_investor_name("总计：1000万元"))
        self.assertTrue(is_summary_investor_name("total amount"))
        self.assertFalse(is_summary_investor_name("TotalEnergies"))
        self.assertFalse(is_summary_investor_name("合计投资有限公司"))

    def test_capital_increase_deal_top_level_name_does_not_count_as_investor(self) -> None:
        _, findings = run_record_postprocess(
            {
                "record_family": "deal",
                "项目编号": "G62026SH1000807",
                "项目名称": "增资成交项目",
                "项目类型": "增资扩股",
                "name": "普通项目名称",
            },
            source_file="/tmp/sample_deal_capital_top_level_name.html",
        )

        self.assertTrue(any(item.type == "business_resolution_required" for item in findings))

    def test_capital_increase_deal_ignores_malformed_top_level_investor_name(self) -> None:
        _, findings = run_record_postprocess(
            {
                "record_family": "deal",
                "项目编号": "G62026SH1000808",
                "项目名称": "增资成交项目",
                "项目类型": "增资扩股",
                "investor_name": {"name": "总计"},
            },
            source_file="/tmp/sample_deal_capital_top_level_investor_dict.html",
        )

        self.assertTrue(any(item.type == "business_resolution_required" for item in findings))

    def test_capital_increase_deal_top_level_investor_name_without_amount_enters_pending_review(self) -> None:
        _, findings = run_record_postprocess(
            {
                "record_family": "deal",
                "项目编号": "G62026SH1000808A",
                "项目名称": "增资成交项目",
                "项目类型": "增资扩股",
                "投资方名称": "投资方甲",
            },
            source_file="/tmp/sample_deal_capital_top_level_investor_name_without_amount.html",
        )

        self.assertTrue(
            any(
                item.type == "business_resolution_required"
                and item.evidence.get("reason_code") == "deal_capital_increase_missing_investor_amount"
                for item in findings
            )
        )
        self.assertEqual(classify_record_state(findings), RecordState.PENDING_REVIEW)

    def test_capital_increase_deal_top_level_investor_with_malformed_amount_enters_pending_review(self) -> None:
        _, findings = run_record_postprocess(
            {
                "record_family": "deal",
                "项目编号": "G62026SH1000808AA",
                "项目名称": "增资成交项目",
                "项目类型": "增资扩股",
                "投资方名称": "投资方甲",
                "投资金额（万元）": {"bad": "data"},
            },
            source_file="/tmp/sample_deal_capital_top_level_investor_malformed_amount.html",
        )

        self.assertTrue(
            any(
                item.type == "business_resolution_required"
                and item.evidence.get("reason_code") == "deal_capital_increase_missing_investor_amount"
                for item in findings
            )
        )
        self.assertEqual(classify_record_state(findings), RecordState.PENDING_REVIEW)

    def test_capital_increase_deal_top_level_summary_investor_does_not_bypass_pending_review(self) -> None:
        _, findings = run_record_postprocess(
            {
                "record_family": "deal",
                "项目编号": "G62026SH1000808B",
                "项目名称": "增资成交项目",
                "项目类型": "增资扩股",
                "投资方名称": "小计",
                "投资金额": "1000",
            },
            source_file="/tmp/sample_deal_capital_top_level_summary_investor.html",
        )

        self.assertTrue(
            any(
                item.type == "business_resolution_required"
                and item.evidence.get("reason_code") == "deal_capital_increase_missing_investor"
                for item in findings
            )
        )
        self.assertEqual(classify_record_state(findings), RecordState.PENDING_REVIEW)
