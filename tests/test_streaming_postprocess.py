from __future__ import annotations

import unittest

from peap.streaming_postprocess import analyze_mapping_candidates, apply_mapping_entries, apply_policy_engine_to_payload


class StreamingPostprocessMappingTest(unittest.TestCase):
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
