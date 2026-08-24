import unittest

from peap.export_projection import project_canonical_record_to_export_payload
from peap.output_contract import (
    BASE_FIELD_CANDIDATES,
    BASE_OUTPUT_COLUMNS,
    KIND_PRE,
    get_output_columns_for_kind,
)
from peap.output_mapping import get_output_mapping_contract, map_standard_to_excel_payload
from peap.standard_model import build_standard_project


class OutputContractTest(unittest.TestCase):
    def test_pre_disclosure_columns_use_disclosure_dates_without_listing_price(self) -> None:
        columns = get_output_columns_for_kind(KIND_PRE)

        self.assertIn("披露开始日期", columns)
        self.assertIn("披露截止日期", columns)
        self.assertNotIn("预披露开始日期", columns)
        self.assertNotIn("预披露截止日期", columns)
        self.assertNotIn("挂牌价格", columns)

        candidates = BASE_FIELD_CANDIDATES[KIND_PRE]
        self.assertEqual(candidates["披露开始日期"], ["披露开始日期", "预披露开始日期", "挂牌开始日期"])
        self.assertEqual(candidates["披露截止日期"], ["披露截止日期", "预披露截止日期", "挂牌截止日期"])
        self.assertNotIn("挂牌价格", candidates)

    def test_pre_disclosure_output_mapping_omits_listing_price_and_uses_disclosure_dates(self) -> None:
        mapping = get_output_mapping_contract()[KIND_PRE]

        self.assertEqual(mapping["披露开始日期"], "start_date")
        self.assertEqual(mapping["披露截止日期"], "end_date")
        self.assertNotIn("挂牌价格", mapping)
        self.assertNotIn("预披露开始日期", mapping)
        self.assertNotIn("预披露截止日期", mapping)

    def test_output_field_maps_cover_all_non_id_workbook_columns(self) -> None:
        mapping_contract = get_output_mapping_contract()

        for kind, columns in BASE_OUTPUT_COLUMNS.items():
            with self.subTest(kind=kind):
                self.assertEqual(set(mapping_contract[kind]), set(columns) - {"ID"})

    def test_output_mapping_preserves_profit_and_public_resource_remark(self) -> None:
        standard = build_standard_project(
            {
                "项目编号": "G32026SH1000999",
                "项目名称": "输出契约项目",
                "近一年净利润（万）": "12.50",
                "备注": "保留备注",
            }
        )

        equity = map_standard_to_excel_payload(standard, "挂牌_股权转让.xlsx")
        capital = map_standard_to_excel_payload(standard, "挂牌_增资扩股.xlsx")
        pre_disclosure = map_standard_to_excel_payload(standard, "挂牌_预披露.xlsx")
        public_resource = map_standard_to_excel_payload(
            standard,
            "公共资源网四大交易所股权转让成交信息统计.xlsx",
        )

        for payload in (equity, capital, pre_disclosure):
            self.assertEqual(payload["近一年净利润（万）"], "12.50")
            self.assertNotIn("近一年净利润", payload)
        self.assertEqual(public_resource["备注"], "保留备注")

    def test_pre_disclosure_export_readiness_does_not_require_listing_price(self) -> None:
        payload, findings = project_canonical_record_to_export_payload(
            {
                "record_family": "listing",
                "business_identity": {
                    "business_id": "pre_disclosure",
                    "raw_business_label": "预披露",
                },
                "canonical_fields": {
                    "project_code": "G32026CQ1000019-0",
                    "project_name": "预披露项目",
                    "project_type": "预披露",
                    "status": "挂牌中",
                    "seller": "重药控股（四川）有限公司",
                    "start_date": "2026/02/28",
                    "source_type": "央企",
                },
            },
            fail_on_missing=False,
        )

        self.assertEqual(findings, ())
        self.assertNotIn("挂牌价格", payload)
