from __future__ import annotations

import unittest

from peap.finance_fallback import apply_finance_fallback, extract_latest_finance_from_html


class FinanceFallbackTest(unittest.TestCase):
    def test_annual_header_period_without_value_period_keeps_asset_column_aligned(self) -> None:
        html = """
        <table>
          <tr><td>主要财务指标（单位：万元）</td><td>近三年年度审计报告</td></tr>
          <tr><td>2025年度</td><td>资产总计</td><td>负债总计</td><td>所有者权益</td></tr>
          <tr><td>533.53</td><td>0.3</td><td>533.23</td></tr>
          <tr><td>营业收入</td><td>净利润</td><td></td></tr>
          <tr><td>0</td><td>250.29</td><td></td></tr>
          <tr><td>2024年度</td><td>资产总计</td><td>负债总计</td><td>所有者权益</td></tr>
          <tr><td>2357.99</td><td>51.01</td><td>2306.98</td></tr>
          <tr><td>营业收入</td><td>净利润</td><td></td></tr>
          <tr><td>584.49</td><td>10.34</td><td></td></tr>
        </table>
        """

        self.assertEqual(
            extract_latest_finance_from_html(html),
            {"近一年净利润": 250.29, "总资产": 533.53},
        )

    def test_standard_finance_alias_is_not_overwritten_by_html_fallback(self) -> None:
        data = {
            "项目编号": "G62026BJ1000001-0",
            "profit": 250.29,
            "asset_total": 533.53,
        }
        html = """
        <table>
          <tr><td>2025年度</td><td>资产总计</td><td>负债总计</td></tr>
          <tr><td>533.53</td><td>0.3</td></tr>
        </table>
        """

        apply_finance_fallback(data, html)

        self.assertEqual(data["近一年净利润"], 250.29)
        self.assertEqual(data["总资产"], 533.53)

    def test_annual_scope_is_inherited_when_year_blocks_are_out_of_order(self) -> None:
        html = """
        <table>
          <tr><td>主要财务指标（单位：万元）</td><td>近三年年度审计报告</td></tr>
          <tr><td>2023年度</td><td>资产总计</td><td>负债总计</td><td>所有者权益</td></tr>
          <tr><td>5489.97</td><td>688.36</td><td>4801.61</td></tr>
          <tr><td>营业收入</td><td>净利润</td><td></td></tr>
          <tr><td>0</td><td>1.61</td><td></td></tr>
          <tr><td>2025年度</td><td>资产总计</td><td>负债总计</td><td>所有者权益</td></tr>
          <tr><td>105862.94</td><td>60525.92</td><td>45337.02</td></tr>
          <tr><td>营业收入</td><td>净利润</td><td></td></tr>
          <tr><td>0</td><td>25.88</td><td></td></tr>
          <tr><td>2024年度</td><td>资产总计</td><td>负债总计</td><td>所有者权益</td></tr>
          <tr><td>55110.77</td><td>34299.64</td><td>20811.14</td></tr>
          <tr><td>营业收入</td><td>净利润</td><td></td></tr>
          <tr><td>0</td><td>9.53</td><td></td></tr>
        </table>
        """

        self.assertEqual(
            extract_latest_finance_from_html(html),
            {"近一年净利润": 25.88, "总资产": 105862.94},
        )

    def test_pre_disclosure_preserves_structured_zero_finance_values(self) -> None:
        data = {
            "项目编号": "G62026BJ1000001-0",
            "近一年净利润": 0,
            "总资产": 0,
        }
        html = """
        <table>
          <tr><td>年度审计报告</td></tr>
          <tr><th>项目/年度</th><th>净利润</th><th>总资产</th></tr>
          <tr><td>2024年度</td><td>5</td><td>100</td></tr>
        </table>
        """

        apply_finance_fallback(data, html)

        self.assertEqual(data["近一年净利润"], 0)
        self.assertEqual(data["总资产"], 0)


if __name__ == "__main__":
    unittest.main()
