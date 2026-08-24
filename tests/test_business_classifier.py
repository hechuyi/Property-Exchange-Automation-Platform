from __future__ import annotations

import unittest

from peap.business_classifier import classify_record_business


class BusinessClassifierTest(unittest.TestCase):
    def test_omitted_parser_payload_remains_empty_payload_default(self) -> None:
        result = classify_record_business(project_type_fallback="未知")

        self.assertEqual(result.record_family, "listing")
        self.assertEqual(result.business_id, "")
        self.assertEqual(result.project_type_label, "")
        self.assertEqual(result.raw_business_label, "未知")

    def test_explicit_null_parser_payload_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "parser_payload must be a mapping"):
            classify_record_business(parser_payload=None)

    def test_non_mapping_parser_payload_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "parser_payload must be a mapping"):
            classify_record_business(parser_payload=[])

    def test_classifies_unknown_beijing_equity_from_name_and_code(self) -> None:
        result = classify_record_business(
            parser_payload={
                "项目编号": "G32025BJ1000444-6",
                "项目名称": "新疆凯宏投资有限公司66%股权",
                "项目类型": "未知",
                "项目状态": "挂牌",
                "交易所": "北交所",
            }
        )

        self.assertEqual(result.record_family, "listing")
        self.assertEqual(result.business_id, "equity_transfer")
        self.assertEqual(result.project_type_label, "股权转让")
        self.assertEqual(result.raw_business_label, "股权转让")

    def test_holding_ratio_alone_does_not_classify_listing_as_capital_increase(self) -> None:
        result = classify_record_business(
            parser_payload={
                "项目编号": "G32026GD0000081-4",
                "项目名称": "北京大唐永盛科技发展有限公司",
                "项目状态": "挂牌",
                "持股比例": "13.7267",
                "转让方": "电信科学技术研究院有限公司",
            },
            page_url="https://new.gduaee.com/xmzx.html#/equityDetail?XMID=160131",
        )

        self.assertEqual(result.business_id, "equity_transfer")

    def test_explicit_guangdong_capital_route_classifies_capital_without_shared_ratio_signal(
        self,
    ) -> None:
        result = classify_record_business(
            parser_payload={
                "项目编号": "G32026GD0000001",
                "项目名称": "广东测试企业",
                "项目状态": "挂牌",
                "持股比例": "35%",
            },
            page_url="https://new.gduaee.com/xmzx.html#/capital_increaseDetail?XMID=1",
        )

        self.assertEqual(result.business_id, "capital_increase")

    def test_guangdong_equity_page_truth_overrides_stale_capital_task_hint(self) -> None:
        result = classify_record_business(
            parser_payload={
                "项目编号": "G32026GD0000081-4",
                "项目名称": "北京大唐永盛科技发展有限公司",
                "项目类型": "股权转让",
                "项目状态": "挂牌",
            },
            record_family_hint="listing",
            business_id_hint="capital_increase",
            business_label_hint="增资扩股",
            project_type_fallback="增资扩股",
            page_url="https://new.gduaee.com/xmzx.html#/equityDetail?XMID=160131",
        )

        self.assertEqual(result.business_id, "equity_transfer")
        self.assertEqual(result.project_type_label, "股权转让")

    def test_parser_business_truth_overrides_stale_task_hint_without_route_metadata(self) -> None:
        result = classify_record_business(
            parser_payload={
                "项目编号": "G32026GD0000081-4",
                "项目名称": "北京大唐永盛科技发展有限公司",
                "项目类型": "股权转让",
                "项目状态": "挂牌",
            },
            record_family_hint="listing",
            business_id_hint="capital_increase",
            project_type_fallback="增资扩股",
        )

        self.assertEqual(result.business_id, "equity_transfer")
        self.assertEqual(result.project_type_label, "股权转让")

    def test_invalid_explicit_business_id_hint_is_rejected_before_natural_inference(self) -> None:
        with self.assertRaisesRegex(KeyError, "definitely_not_a_business"):
            classify_record_business(
                parser_payload={
                    "项目编号": "G32025BJ1000444-6",
                    "项目名称": "新疆凯宏投资有限公司66%股权",
                    "项目类型": "未知",
                    "项目状态": "挂牌",
                },
                business_id_hint="definitely_not_a_business",
            )

    def test_listing_suffix_zero_classifies_as_pre_disclosure_before_capital_markers(self) -> None:
        result = classify_record_business(
            parser_payload={
                "项目编号": "G62026BJ1000020-0",
                "项目名称": "湖南烁科晶磊半导体科技有限公司增资项目",
                "项目类型": "未知",
                "项目状态": "挂牌",
                "交易所": "北交所",
                "是否预披露": True,
            }
        )

        self.assertEqual(result.record_family, "listing")
        self.assertEqual(result.business_id, "pre_disclosure")
        self.assertEqual(result.project_type_label, "预披露")
        self.assertEqual(result.raw_business_label, "预披露")

    def test_listing_suffix_zero_pre_disclosure_overrides_formal_capital_hint(self) -> None:
        result = classify_record_business(
            parser_payload={
                "项目编号": "G62026BJ1000020-0",
                "项目名称": "湖南烁科晶磊半导体科技有限公司增资项目",
                "项目类型": "增资扩股",
                "项目状态": "挂牌",
            },
            record_family_hint="listing",
            business_id_hint="capital_increase",
            business_label_hint="增资扩股",
            project_type_fallback="增资扩股",
        )

        self.assertEqual(result.record_family, "listing")
        self.assertEqual(result.business_id, "pre_disclosure")
        self.assertEqual(result.project_type_label, "预披露")
        self.assertEqual(result.raw_business_label, "预披露")

    def test_classifies_deal_family_with_family_specific_business_id(self) -> None:
        result = classify_record_business(
            parser_payload={
                "项目编号": "D32026PR000001",
                "项目名称": "成交样例项目",
                "项目类型": "股权转让",
                "项目状态": "成交",
                "交易所": "public_resource",
            },
            page_url="https://example.test/information/deal/html/1.html",
        )

        self.assertEqual(result.record_family, "deal")
        self.assertEqual(result.business_id, "deal_equity_transfer")
        self.assertEqual(result.project_type_label, "股权转让")
        self.assertEqual(result.raw_business_label, "股权转让")

    def test_deal_status_without_business_evidence_stays_unresolved(self) -> None:
        result = classify_record_business(
            parser_payload={
                "项目编号": "CJ202604300001",
                "项目名称": "成交公告",
                "项目类型": "未知",
                "项目状态": "成交",
                "交易所": "上交所",
            },
            page_url="https://example.test/information/deal/html/unknown.html",
        )

        self.assertEqual(result.record_family, "deal")
        self.assertEqual(result.business_id, "")
        self.assertEqual(result.project_type_label, "")
        self.assertEqual(result.raw_business_label, "未知")

    def test_deal_family_equity_markers_outrank_physical_name_tokens_without_business_hint(
        self,
    ) -> None:
        names = (
            "北京建广资产管理有限公司51%股权",
            "玉门锦辉长城电力设备制造有限公司51%股权",
            "上海浦景废旧物资回收有限公司94%股权",
            "圣多金基（上海）资产管理有限公司27.37%股权",
            "天津国豪资产管理有限公司100%股权",
        )

        for name in names:
            with self.subTest(name=name):
                result = classify_record_business(
                    parser_payload={
                        "项目名称": name,
                        "项目类型": "未知",
                    },
                    record_family_hint="deal",
                )

                self.assertEqual(result.record_family, "deal")
                self.assertEqual(result.business_id, "deal_equity_transfer")
                self.assertEqual(result.project_type_label, "股权转让")
                self.assertEqual(result.raw_business_label, "股权转让")

    def test_deal_family_physical_markers_still_classify_physical_without_business_hint(
        self,
    ) -> None:
        result = classify_record_business(
            parser_payload={
                "项目名称": "报废车辆设备一批",
                "项目类型": "未知",
            },
            record_family_hint="deal",
        )

        self.assertEqual(result.record_family, "deal")
        self.assertEqual(result.business_id, "deal_physical_asset")
        self.assertEqual(result.project_type_label, "实物资产")
        self.assertEqual(result.raw_business_label, "实物资产")

    def test_payload_listing_family_is_overridden_by_strong_deal_status(self) -> None:
        status_signal = classify_record_business(
            parser_payload={
                "record_family": "listing",
                "项目编号": "D32026PR000101",
                "项目名称": "成交样例项目",
                "项目类型": "股权转让",
                "项目状态": "成交",
            }
        )
        self.assertEqual(status_signal.record_family, "deal")

    def test_payload_listing_family_is_not_overridden_by_deal_url_signal(self) -> None:
        url_signal = classify_record_business(
            parser_payload={
                "record_family": "listing",
                "项目编号": "D32026PR000102",
                "项目名称": "成交样例项目",
                "项目类型": "股权转让",
                "项目状态": "挂牌",
            },
            page_url="https://example.test/information/deal/html/102.html",
        )
        self.assertEqual(url_signal.record_family, "listing")

    def test_status_signal_does_not_treat_non_deal_phrase_as_deal(self) -> None:
        result = classify_record_business(
            parser_payload={
                "record_family": "listing",
                "项目编号": "D32026PR000130",
                "项目名称": "挂牌样例项目",
                "项目类型": "股权转让",
                "项目状态": "未成交",
            }
        )
        self.assertEqual(result.record_family, "listing")
        self.assertEqual(result.business_id, "equity_transfer")

    def test_deal_url_signal_requires_independent_path_segment(self) -> None:
        for url in (
            "https://example.test/ideal/project/1",
            "https://example.test/redeal/project/2",
        ):
            with self.subTest(url=url):
                result = classify_record_business(
                    parser_payload={
                        "record_family": "listing",
                        "项目编号": "D32026PR000120",
                        "项目名称": "挂牌样例项目",
                        "项目类型": "股权转让",
                        "项目状态": "挂牌",
                    },
                    page_url=url,
                )
                self.assertEqual(result.record_family, "listing")

        deal_segment = classify_record_business(
            parser_payload={
                "项目编号": "D32026PR000121",
                "项目名称": "挂牌样例项目",
                "项目类型": "股权转让",
                "项目状态": "挂牌",
            },
            page_url="https://example.test/path/deal/121",
        )
        self.assertEqual(deal_segment.record_family, "deal")

    def test_deal_url_signal_ignores_query_hash_and_domain_tokens(self) -> None:
        for url in (
            "https://example.test/list?tab=chengjiao",
            "https://example.test/list?tab=%E6%88%90%E4%BA%A4",
            "https://example.test/list#chengjiao",
            "https://chengjiao.example.test/list",
            "https://成交.example.test/list",
        ):
            with self.subTest(url=url):
                result = classify_record_business(
                    parser_payload={
                        "record_family": "listing",
                        "项目编号": "D32026PR000131",
                        "项目名称": "挂牌样例项目",
                        "项目类型": "股权转让",
                        "项目状态": "挂牌",
                    },
                    page_url=url,
                )
                self.assertEqual(result.record_family, "listing")

        for url in (
            "https://example.test/path/chengjiao/131",
            "https://example.test/path/成交/131",
        ):
            with self.subTest(url=url):
                result = classify_record_business(
                    parser_payload={
                        "项目编号": "D32026PR000132",
                        "项目名称": "挂牌样例项目",
                        "项目类型": "股权转让",
                        "项目状态": "挂牌",
                    },
                    page_url=url,
                )
                self.assertEqual(result.record_family, "deal")

    def test_family_hint_keeps_highest_priority_and_payload_family_remains_fallback(self) -> None:
        hinted = classify_record_business(
            parser_payload={
                "record_family": "listing",
                "项目编号": "D32026PR000103",
                "项目名称": "成交样例项目",
                "项目类型": "股权转让",
                "项目状态": "成交",
            },
            record_family_hint="listing",
            page_url="https://example.test/information/deal/html/103.html",
        )
        self.assertEqual(hinted.record_family, "listing")

        payload_fallback = classify_record_business(
            parser_payload={
                "record_family": "deal",
                "项目编号": "D32026PR000104",
                "项目名称": "成交样例项目",
                "项目类型": "股权转让",
                "项目状态": "挂牌",
            }
        )
        self.assertEqual(payload_fallback.record_family, "deal")


if __name__ == "__main__":
    unittest.main()
