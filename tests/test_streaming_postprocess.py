from __future__ import annotations

import unittest

from peap.streaming_postprocess import apply_mapping_entries


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

    def test_transferor_type_mapping_has_priority_over_group_type(self) -> None:
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

        resolved, _findings = apply_mapping_entries(payload, mapping_entries=entries)

        self.assertEqual(resolved["隶属集团"], "北京测试集团")
        self.assertEqual(resolved["类型"], "央企")

    def test_group_type_mapping_overwrites_existing_type_when_rule_matches(self) -> None:
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

        self.assertEqual(resolved["类型"], "央企")
        self.assertTrue(any(item.type == "mapping_applied" for item in findings))


if __name__ == "__main__":
    unittest.main()
