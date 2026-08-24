import unittest

from peap.deal_amounts import (
    apply_deal_price_amount_fields,
    infer_deal_price_unit_hint_from_html,
    normalize_deal_amount_to_wan,
)


class DealAmountNormalizationTest(unittest.TestCase):
    def test_normalizes_explicit_wan_amount_and_preserves_raw_evidence(self) -> None:
        normalized = normalize_deal_amount_to_wan("20.995922（万元）")

        self.assertEqual(normalized.normalized_text, "20.995922")
        self.assertEqual(normalized.raw_text, "20.995922（万元）")
        self.assertEqual(normalized.unit, "万元")
        self.assertEqual(normalized.unit_basis, "raw_unit")
        self.assertTrue(normalized.unit_verified)

    def test_converts_yuan_amount_to_wan(self) -> None:
        normalized = normalize_deal_amount_to_wan("209959.22元")

        self.assertEqual(normalized.normalized_text, "20.995922")
        self.assertEqual(normalized.unit, "万元")
        self.assertEqual(normalized.source_unit, "元")
        self.assertEqual(normalized.unit_basis, "converted_from_yuan")
        self.assertTrue(normalized.unit_verified)

    def test_converts_by_field_header_when_value_has_no_unit(self) -> None:
        normalized = normalize_deal_amount_to_wan("209959.22", unit_hint="交易价格（元）")

        self.assertEqual(normalized.normalized_text, "20.995922")
        self.assertEqual(normalized.unit, "万元")
        self.assertEqual(normalized.source_unit, "元")
        self.assertEqual(normalized.unit_basis, "converted_from_field_yuan")
        self.assertTrue(normalized.unit_verified)

    def test_uses_field_header_wan_when_value_has_no_unit(self) -> None:
        normalized = normalize_deal_amount_to_wan("20.995922", unit_hint="交易价格（万元）")

        self.assertEqual(normalized.normalized_text, "20.995922")
        self.assertEqual(normalized.unit, "万元")
        self.assertEqual(normalized.source_unit, "万元")
        self.assertEqual(normalized.unit_basis, "field_unit_wan")
        self.assertTrue(normalized.unit_verified)

    def test_value_unit_overrides_field_header_unit(self) -> None:
        normalized = normalize_deal_amount_to_wan("209959.22元", unit_hint="交易价格（万元）")

        self.assertEqual(normalized.normalized_text, "20.995922")
        self.assertEqual(normalized.source_unit, "元")
        self.assertEqual(normalized.unit_basis, "converted_from_yuan")

    def test_treats_numeric_amount_without_unit_as_default_wan(self) -> None:
        normalized = normalize_deal_amount_to_wan("20.995922")

        self.assertEqual(normalized.normalized_text, "20.995922")
        self.assertEqual(normalized.unit, "万元")
        self.assertEqual(normalized.unit_basis, "default_wan")
        self.assertTrue(normalized.unit_verified)

    def test_infers_unit_hint_from_deal_price_header(self) -> None:
        html = "<table><tr><th>项目编号</th><th>交易价格（万元）</th></tr><tr><td>A</td><td>20</td></tr></table>"

        self.assertEqual(infer_deal_price_unit_hint_from_html(html), "交易价格（万元）")

    def test_infers_unit_hint_from_page_level_unit_when_header_is_unitless(self) -> None:
        html = "<p>产权转让成交公告 单位:万元</p><table><tr><th>交易价格</th><td>20</td></tr></table>"

        self.assertEqual(infer_deal_price_unit_hint_from_html(html), "交易价格 单位:万元")

    def test_infers_unit_hint_from_cbex_json_priceunit(self) -> None:
        html = '<textarea class="source">{"tradevalue":"425.19","priceunit":"万元"}</textarea>'

        self.assertEqual(infer_deal_price_unit_hint_from_html(html), "交易价格单位:万元")

    def test_apply_deal_price_amount_fields_rejects_non_dict_canonical_fields(self) -> None:
        for canonical_fields in ([], None):
            with self.subTest(canonical_fields=canonical_fields):
                with self.assertRaisesRegex(TypeError, "canonical_fields must be a dict"):
                    apply_deal_price_amount_fields(canonical_fields)


if __name__ == "__main__":
    unittest.main()
