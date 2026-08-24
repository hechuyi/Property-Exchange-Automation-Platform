from __future__ import annotations

import unittest

from peap.mapping_subjects import (
    match_subject_names,
    normalize_match_text,
    resolve_mapping_subject,
    subject_matches_source,
)


class MappingSubjectsTest(unittest.TestCase):
    def test_transferor_subject_uses_largest_explicit_ratio_as_primary(self) -> None:
        resolution = resolve_mapping_subject(
            "烟台顺达海洋工程服务有限责任公司(99.5%) 上海诺亚船舶修理有限公司(0.5%)",
            match_field="transferor",
        )

        self.assertEqual(resolution.primary_subject, "烟台顺达海洋工程服务有限责任公司")
        self.assertEqual(resolution.reason_code, "primary_ratio")
        self.assertEqual(match_subject_names(resolution.raw_value, match_field="transferor"), ("烟台顺达海洋工程服务有限责任公司",))

    def test_transferor_subject_uses_fullwidth_percent_and_ratio_prefixes(self) -> None:
        raw = "烟台顺达海洋工程服务有限责任公司（占99.5％） 上海诺亚船舶修理有限公司（持股比例0.5％）"

        resolution = resolve_mapping_subject(raw, match_field="transferor")

        self.assertEqual(resolution.primary_subject, "烟台顺达海洋工程服务有限责任公司")
        self.assertEqual(resolution.reason_code, "primary_ratio")
        self.assertEqual(match_subject_names(raw, match_field="transferor"), ("烟台顺达海洋工程服务有限责任公司",))

    def test_transferor_subject_accepts_colon_ratio_format(self) -> None:
        raw = "烟台顺达海洋工程服务有限责任公司:99.5%;上海诺亚船舶修理有限公司:0.5%"

        resolution = resolve_mapping_subject(raw, match_field="transferor")

        self.assertEqual(resolution.primary_subject, "烟台顺达海洋工程服务有限责任公司")
        self.assertEqual(resolution.reason_code, "primary_ratio")
        self.assertEqual(match_subject_names(raw, match_field="transferor"), ("烟台顺达海洋工程服务有限责任公司",))

    def test_transferor_subject_accepts_inline_holding_ratio_format(self) -> None:
        raw = "烟台顺达海洋工程服务有限责任公司持股99.5%；上海诺亚船舶修理有限公司持股0.5%"

        resolution = resolve_mapping_subject(raw, match_field="transferor")

        self.assertEqual(resolution.primary_subject, "烟台顺达海洋工程服务有限责任公司")
        self.assertEqual(resolution.reason_code, "primary_ratio")

    def test_transferor_subject_accepts_combined_parenthesized_ratio_prefix(self) -> None:
        raw = "烟台顺达海洋工程服务有限责任公司(占出资比例99.5%) 上海诺亚船舶修理有限公司(占出资比例0.5%)"

        resolution = resolve_mapping_subject(raw, match_field="transferor")

        self.assertEqual(resolution.primary_subject, "烟台顺达海洋工程服务有限责任公司")
        self.assertEqual(resolution.reason_code, "primary_ratio")

    def test_transferor_subject_with_partial_ratio_coverage_is_ambiguous(self) -> None:
        raw = "烟台顺达海洋工程服务有限责任公司(99.5%) 上海诺亚船舶修理有限公司"

        resolution = resolve_mapping_subject(raw, match_field="transferor")

        self.assertEqual(resolution.primary_subject, "")
        self.assertTrue(resolution.ambiguous)
        self.assertEqual(resolution.reason_code, "partial_ratio_coverage")
        self.assertEqual(match_subject_names(raw, match_field="transferor"), (raw,))

    def test_transferor_subject_strips_known_field_label_before_matching(self) -> None:
        raw = "转让方：烟台顺达海洋工程服务有限责任公司(99.5%)；上海诺亚船舶修理有限公司(0.5%)"

        resolution = resolve_mapping_subject(raw, match_field="transferor")

        self.assertEqual(resolution.primary_subject, "烟台顺达海洋工程服务有限责任公司")
        self.assertEqual(resolution.reason_code, "primary_ratio")

    def test_transferor_subject_with_tied_ratio_keeps_raw_subject_conservatively(self) -> None:
        raw = "上海松江交通投资运营集团有限公司(50%) 上海锦江汽车服务有限公司(50%)"

        resolution = resolve_mapping_subject(raw, match_field="transferor")

        self.assertEqual(resolution.primary_subject, "")
        self.assertTrue(resolution.ambiguous)
        self.assertEqual(resolution.reason_code, "tied_primary_ratio")
        self.assertEqual(match_subject_names(raw, match_field="transferor"), (raw,))

    def test_transferor_subject_without_ratio_keeps_raw_subject_conservatively(self) -> None:
        raw = "上海测试公司 上海另一测试公司"

        resolution = resolve_mapping_subject(raw, match_field="transferor")

        self.assertEqual(resolution.primary_subject, "")
        self.assertTrue(resolution.ambiguous)
        self.assertEqual(resolution.reason_code, "multiple_subjects_without_ratios")
        self.assertEqual(match_subject_names(raw, match_field="transferor"), (raw,))

    def test_group_subject_is_exact_scalar(self) -> None:
        self.assertEqual(match_subject_names("烟台顺达集团", match_field="group"), ("烟台顺达集团",))

    def test_match_key_normalizes_fullwidth_compatibility_forms(self) -> None:
        self.assertEqual(
            normalize_match_text("中国ＡＢＣ（西安）１２３"),
            normalize_match_text("中国ABC(西安)123"),
        )

    def test_subject_mapping_matches_fullwidth_source_without_changing_display_name(self) -> None:
        raw = "中核承影(西安)医疗设备有限公司"
        source_name = "中核承影（西安）医疗设备有限公司"

        self.assertTrue(subject_matches_source(raw, match_field="transferor", source_name=source_name))
        self.assertEqual(match_subject_names(raw, match_field="transferor"), (raw,))


if __name__ == "__main__":
    unittest.main()
