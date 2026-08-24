from __future__ import annotations

import json
import unittest

from peap_parsers.base import ParserContext
from peap_parsers.deal_cbex import DealCBEXParser


def _render_source_textarea(payload: dict[str, object]) -> str:
    return (
        "<textarea class='source'>"
        + json.dumps(payload, ensure_ascii=False).replace('"', "&quot;")
        + "</textarea>"
    )


class CbexTextareaSourceParserTest(unittest.TestCase):
    def _parse(self, html: str) -> dict[str, object]:
        parser = DealCBEXParser(html, context=ParserContext(source_file="fixture.html"))
        return dict(parser.parse().standard_payload)

    def test_uses_metadata_project_code_to_select_matching_textarea_source_payload(self) -> None:
        first_payload = {
            "utrgcemsproject": {
                "projectcode": "G32026BJ1000001",
                "object": "第一条股权项目",
                "tradevalue": "1111.00",
                "objectprice": "1000.00",
                "tradedate": "2026-05-01",
                "buyername": "第一受让方",
            },
            "utrgcemsobject": {
                "objectevaluatevalue": "1200.00",
            },
        }
        second_payload = {
            "utrmcemsproject": {
                "projectcode": "GR2026BJ1001513",
                "object": "第二条实物资产项目",
                "tradevalue": "15.191",
                "objectprice": "15.141",
                "evaluatevalue": "15.141",
                "tradedate": "2026-05-07",
                "buyername": "上海梦兴圆贸易有限公司",
            }
        }
        html = (
            "<html><body>"
            "<script id='deal_metadata' type='application/json'>"
            + json.dumps({"project_code": "GR2026BJ1001513"}, ensure_ascii=False)
            + "</script>"
            + _render_source_textarea(first_payload)
            + _render_source_textarea(second_payload)
            + "</body></html>"
        )

        payload = self._parse(html)

        self.assertEqual(payload["project_code"], "GR2026BJ1001513")
        self.assertEqual(payload["project_name"], "第二条实物资产项目")
        self.assertEqual(payload["business_type"], "实物资产")
        self.assertEqual(payload["deal_date"], "2026/05/07")
        self.assertEqual(payload["deal_price"], "15.191")
        self.assertEqual(payload["reserve_price"], "15.141")
        self.assertEqual(payload["valuation"], "15.141")
        self.assertEqual(payload["buyer_name"], "上海梦兴圆贸易有限公司")

    def test_multiple_textarea_source_payloads_require_reliable_metadata_binding(self) -> None:
        first_payload = {
            "utrgcemsproject": {
                "projectcode": "G32026BJ1000001",
                "object": "第一条股权项目",
                "tradevalue": "1111.00",
                "tradedate": "2026-05-01",
            }
        }
        second_payload = {
            "utrmcemsproject": {
                "projectcode": "GR2026BJ1001513",
                "object": "第二条实物资产项目",
                "tradevalue": "15.191",
                "tradedate": "2026-05-07",
            }
        }

        cases = (
            ("missing metadata", ""),
            (
                "metadata mismatch",
                "<script id='deal_metadata' type='application/json'>"
                + json.dumps({"project_code": "G32026BJ9999999"}, ensure_ascii=False)
                + "</script>",
            ),
        )
        for case_name, metadata_node in cases:
            with self.subTest(case_name=case_name):
                html = (
                    "<html><body>"
                    + metadata_node
                    + _render_source_textarea(first_payload)
                    + _render_source_textarea(second_payload)
                    + "</body></html>"
                )

                with self.assertRaisesRegex(ValueError, r"ambiguous textarea\.source"):
                    self._parse(html)

    def test_textarea_source_raises_when_present_but_corrupt_instead_of_falling_back(self) -> None:
        html = (
            "<html><body>"
            "<textarea class='source'>{&quot;utrmcemsproject&quot;:</textarea>"
            "<table>"
            "<tr><th>项目编号</th><td>GR2026BJ1001513</td></tr>"
            "<tr><th>项目名称</th><td>北交所 DOM 后备项目</td></tr>"
            "<tr><th>业务类型</th><td>实物资产</td></tr>"
            "<tr><th>成交金额</th><td>15.191</td></tr>"
            "<tr><th>评估值</th><td>15.141</td></tr>"
            "<tr><th>转让底价</th><td>15.141</td></tr>"
            "<tr><th>成交日期</th><td>2026-05-07</td></tr>"
            "</table>"
            "</body></html>"
        )

        with self.assertRaises(json.JSONDecodeError):
            self._parse(html)

    def test_textarea_source_raises_when_present_but_root_is_not_object(self) -> None:
        html = (
            "<html><body>"
            "<textarea class='source'>123</textarea>"
            "<table>"
            "<tr><th>项目编号</th><td>GR2026BJ1001513</td></tr>"
            "<tr><th>项目名称</th><td>北交所 DOM 后备项目</td></tr>"
            "<tr><th>业务类型</th><td>实物资产</td></tr>"
            "<tr><th>成交金额</th><td>15.191</td></tr>"
            "<tr><th>评估值</th><td>15.141</td></tr>"
            "<tr><th>转让底价</th><td>15.141</td></tr>"
            "<tr><th>成交日期</th><td>2026-05-07</td></tr>"
            "</table>"
            "</body></html>"
        )

        with self.assertRaisesRegex(ValueError, r"textarea\.source root must be an object"):
            self._parse(html)

    def test_absent_textarea_source_can_fall_back_to_rendered_table(self) -> None:
        html = (
            "<html><body>"
            "<table>"
            "<tr><th>项目编号</th><td>GR2026BJ1001513</td></tr>"
            "<tr><th>项目名称</th><td>北交所 DOM 后备项目</td></tr>"
            "<tr><th>业务类型</th><td>实物资产</td></tr>"
            "<tr><th>成交金额</th><td>15.191</td></tr>"
            "<tr><th>评估值</th><td>15.141</td></tr>"
            "<tr><th>转让底价</th><td>15.141</td></tr>"
            "<tr><th>成交日期</th><td>2026-05-07</td></tr>"
            "</table>"
            "</body></html>"
        )

        payload = self._parse(html)

        self.assertEqual(payload["project_code"], "GR2026BJ1001513")
        self.assertEqual(payload["project_name"], "北交所 DOM 后备项目")
        self.assertEqual(payload["deal_date"], "2026/05/07")

    def test_prefers_injected_detail_payload_over_textarea_source(self) -> None:
        textarea_payload = {
            "utrmcemsproject": {
                "projectcode": "GR2026BJ1001513",
                "object": "不应命中的原页项目",
                "tradevalue": "15.191",
                "objectprice": "15.141",
                "evaluatevalue": "15.141",
                "tradedate": "2026-05-07",
                "buyername": "原页买方",
            }
        }
        detail_payload = {
            "utrgcemsproject": {
                "projectcode": "G32026BJ1000085",
                "object": "东北中小企业融资再担保股份有限公司34,690,000股股份（占总股本的1.1365%）",
                "tradevalue": "4998.8605",
                "objectprice": "4998.8605万元",
                "tradedate": "2026-04-29",
                "buyername": "detail payload 受让方",
            },
            "utrgcemsobject": {
                "objectevaluatevalue": "4998.86",
            },
        }
        html = (
            "<html><body>"
            "<script id='deal_metadata' type='application/json'>"
            + json.dumps({"project_code": "G32026BJ1000085"}, ensure_ascii=False)
            + "</script>"
            + _render_source_textarea(textarea_payload)
            + "<script id='deal_detail' type='application/json'>"
            + json.dumps(detail_payload, ensure_ascii=False)
            + "</script>"
            + "</body></html>"
        )

        payload = self._parse(html)

        self.assertEqual(payload["project_code"], "G32026BJ1000085")
        self.assertEqual(payload["project_name"], detail_payload["utrgcemsproject"]["object"])
        self.assertEqual(payload["business_type"], "股权转让")
        self.assertEqual(payload["deal_date"], "2026/04/29")
        self.assertEqual(payload["deal_price"], "4998.8605")
        self.assertEqual(payload["reserve_price"], "4998.8605万元")
        self.assertEqual(payload["valuation"], "4998.86")
        self.assertEqual(payload["buyer_name"], "detail payload 受让方")

    def test_rejects_sidecar_metadata_when_injected_detail_identity_mismatches(self) -> None:
        detail_payload = {
            "utrgcemsproject": {
                "projectcode": "G32025BJ9999999",
                "object": "另一项目股权",
                "tradevalue": "9999.99",
                "objectprice": "8888.88万元",
                "tradedate": "2025-12-31",
                "buyername": "另一项目受让方",
            },
            "utrgcemsobject": {
                "objectevaluatevalue": "7777.77",
            },
        }
        html = (
            "<html><body>"
            "<script id='deal_metadata' type='application/json'>"
            + json.dumps(
                {
                    "project_code": "G32026BJ1000085",
                    "project_name": "目标项目股权",
                    "business_id": "deal_equity_transfer",
                },
                ensure_ascii=False,
            )
            + "</script>"
            "<script id='deal_detail' type='application/json'>"
            + json.dumps(detail_payload, ensure_ascii=False)
            + "</script>"
            "</body></html>"
        )

        with self.assertRaisesRegex(ValueError, r"identity mismatch"):
            self._parse(html)

    def test_prefers_sidecar_bound_payload_and_matching_visible_row_over_last_visible_row(self) -> None:
        detail_payload = {
            "utrgcemsproject": {
                "projectcode": "G32026BJ1000085",
                "object": "目标项目",
                "tradevalue": "4998.8605",
                "objectprice": "4998.8605万元",
                "tradedate": "2026-04-29",
            },
            "utrgcemsobject": {
                "objectevaluatevalue": "4998.86",
            },
        }
        last_row_payload = {
            "utrgcemsproject": {
                "projectcode": "G32025BJ1000729",
                "object": "最后一行项目",
                "tradevalue": "9999.99",
                "objectprice": "9999.99万元",
                "tradedate": "2025-12-31",
            },
            "utrgcemsobject": {
                "objectevaluatevalue": "8888.88",
            },
        }
        html = (
            "<html><body>"
            "<script id='deal_metadata' type='application/json'>"
            + json.dumps({"project_code": "G32026BJ1000085"}, ensure_ascii=False)
            + "</script>"
            + _render_source_textarea(detail_payload)
            + _render_source_textarea(last_row_payload)
            + "<script id='deal_detail' type='application/json'>"
            + json.dumps(detail_payload, ensure_ascii=False)
            + "</script>"
            + "<table><thead><tr><th>项目编号</th><th>标的名称</th><th>交易价格（万元）</th></tr></thead>"
            + "<tbody>"
            + "<tr><td>G32026BJ1000085</td><td>目标项目</td><td>4998.8605</td></tr>"
            + "<tr><td>G32025BJ1000729</td><td>最后一行项目</td><td>9999.99</td></tr>"
            + "</tbody></table>"
            + "</body></html>"
        )

        payload = self._parse(html)

        self.assertEqual(payload["project_code"], "G32026BJ1000085")
        self.assertEqual(payload["project_name"], "目标项目")
        self.assertEqual(payload["deal_price"], "4998.8605")
        self.assertEqual(payload["reserve_price"], "4998.8605万元")
        self.assertEqual(payload["valuation"], "4998.86")
        self.assertEqual(payload["deal_date"], "2026/04/29")

    def test_parses_capital_increase_textarea_source_with_trade_and_holder_lists(self) -> None:
        payload = {
            "utrzcemsproject": {
                "projectcode": "G62026BJ3000001",
                "object": "北交所增资成交项目",
                "tradevalue": "3600",
                "objectprice": "3500",
                "evaluatevalue": "3800",
                "tradedate": "2026-04-27",
            },
            "tradelist": {
                "utrzcemstrade": [
                    {"investorname": "投资方甲", "tradevalue": "1800", "stockpercent": "20%"},
                    {"investorname": "投资方乙", "tradevalue": "1800", "stockpercent": "30%"},
                ]
            },
            "holderlist": {
                "utrzcemsshareholder": [
                    {"holdername": "原股东甲", "holdingratio": "60%"},
                    {"holdername": "原股东乙", "holdingratio": "40%"},
                ]
            },
        }
        html = (
            "<html><body>"
            "<script id='deal_metadata' type='application/json'>"
            + json.dumps({"project_code": "G62026BJ3000001"}, ensure_ascii=False)
            + "</script>"
            + _render_source_textarea(payload)
            + "</body></html>"
        )

        parsed = self._parse(html)

        self.assertEqual(parsed["project_code"], "G62026BJ3000001")
        self.assertEqual(parsed["project_name"], "北交所增资成交项目")
        self.assertEqual(parsed["business_type"], "增资扩股")
        self.assertEqual(parsed["deal_date"], "2026/04/27")
        self.assertEqual(parsed["deal_price"], "3600")
        self.assertEqual(parsed["reserve_price"], "3500")
        self.assertEqual(parsed["valuation"], "3800")
        self.assertEqual(parsed["capital_company_name"], "原股东甲")
        self.assertEqual(
            parsed["investors"],
            [
                {"name": "投资方甲", "amount": "1800", "ratio": "20%"},
                {"name": "投资方乙", "amount": "1800", "ratio": "30%"},
            ],
        )

    def test_parses_capital_increase_trade_list_with_per_trade_fields(self) -> None:
        payload = {
            "utrzcemsproject": {
                "projectcode": "G62025BJ1000096",
                "object": "三门峡新华水工机械有限责任公司",
                "tradevalue": "2800",
                "tradedate": "2026-04-30",
            },
            "tradelist": {
                "utrzcemstrade": [
                    {
                        "investorname": "北京国泰新华实业有限公司",
                        "pertradevalue": "2500",
                        "pertradepercent": "10.11",
                    },
                    {
                        "investorname": "郑州国水机械设计研究所有限公司",
                        "pertradevalue": "300",
                        "pertradepercent": "1.21",
                    },
                ]
            },
        }
        html = (
            "<html><body>"
            "<script id='deal_metadata' type='application/json'>"
            + json.dumps({"project_code": "G62025BJ1000096"}, ensure_ascii=False)
            + "</script>"
            + _render_source_textarea(payload)
            + "</body></html>"
        )

        parsed = self._parse(html)

        self.assertEqual(
            parsed["investors"],
            [
                {"name": "北京国泰新华实业有限公司", "amount": "2500", "ratio": "10.11"},
                {"name": "郑州国水机械设计研究所有限公司", "amount": "300", "ratio": "1.21"},
            ],
        )

    def test_parses_capital_increase_trade_list_preserves_zero_values(self) -> None:
        payload = {
            "utrzcemsproject": {
                "projectcode": "G62026BJ3000002",
                "object": "北交所零值增资成交项目",
                "tradevalue": "0",
                "tradedate": "2026-04-30",
            },
            "tradelist": {
                "utrzcemstrade": [
                    {
                        "investorname": "投资方零值",
                        "pertradevalue": 0,
                        "pertradepercent": 0,
                    },
                ]
            },
        }
        html = (
            "<html><body>"
            "<script id='deal_metadata' type='application/json'>"
            + json.dumps({"project_code": "G62026BJ3000002"}, ensure_ascii=False)
            + "</script>"
            + _render_source_textarea(payload)
            + "</body></html>"
        )

        parsed = self._parse(html)

        self.assertEqual(parsed["investors"], [{"name": "投资方零值", "amount": "0", "ratio": "0"}])
