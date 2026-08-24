import unittest

from peap.streaming_postprocess import finalize_streaming_payload
from peap_postprocess.postprocess_engine.contracts import CanonicalRecord
from peap_postprocess.postprocess_engine.rules.builtin import (
    R005NormalizeSourceTypeRule,
    R006DeriveListingTimesRule,
)


class ListingTimesRuleTest(unittest.TestCase):
    def _record(self, project_code: str, *, listing_times: str = "") -> CanonicalRecord:
        return CanonicalRecord(
            source_file="/tmp/sample.xlsx",
            file_name="挂牌_实物资产.xlsx",
            sheet_name="Sheet1",
            row_index=2,
            project_code=project_code,
            company_name_primary="",
            group_name="",
            raw_fields={
                "项目编号": project_code,
                "挂牌次数": listing_times,
            },
        )

    def test_r006_derives_numeric_listing_times_from_suffix(self) -> None:
        result = R006DeriveListingTimesRule().apply(self._record("GF2025SH1000254-3"), {})

        self.assertEqual(len(result.patches), 1)
        self.assertEqual(result.patches[0].field, "挂牌次数")
        self.assertEqual(result.patches[0].new_value, "三次挂牌")

    def test_r006_derives_first_listing_as_numeric_one_without_suffix(self) -> None:
        result = R006DeriveListingTimesRule().apply(self._record("CP2025BJ1000506"), {})

        self.assertEqual(len(result.patches), 1)
        self.assertEqual(result.patches[0].new_value, "首次挂牌")

    def test_r006_normalizes_matching_numeric_listing_times_to_chinese(self) -> None:
        result = R006DeriveListingTimesRule().apply(self._record("GF2025SH1000254-5", listing_times="5"), {})

        self.assertEqual(len(result.patches), 1)
        self.assertEqual(result.patches[0].new_value, "五次挂牌")
        self.assertEqual(result.findings, [])

    def test_r006_skips_zero_suffix_pre_disclosure_marker(self) -> None:
        result = R006DeriveListingTimesRule().apply(self._record("G32025BJ1000692-0"), {})

        self.assertEqual(result.patches, [])
        self.assertEqual(result.findings, [])


class NormalizeSourceTypeRuleTest(unittest.TestCase):
    def _record(self, source_type: str) -> CanonicalRecord:
        return CanonicalRecord(
            source_file="/tmp/sample.xlsx",
            file_name="挂牌_股权转让.xlsx",
            sheet_name="Sheet1",
            row_index=2,
            project_code="G32026SH1000008",
            company_name_primary="中铁二院工程集团有限责任公司",
            group_name="中铁",
            raw_fields={
                "项目编号": "G32026SH1000008",
                "转让方": "中铁二院工程集团有限责任公司",
                "隶属集团": "中铁",
                "类型": source_type,
            },
        )

    def test_r005_rejects_research_institute_source_type_outside_four_type_taxonomy(self) -> None:
        result = R005NormalizeSourceTypeRule().apply(self._record("科研院所"), {})

        self.assertEqual(len(result.patches), 1)
        self.assertEqual(result.patches[0].new_value, "")
        self.assertTrue(any(item.type == "source_type_unsupported" for item in result.findings))


class DealReadinessPostprocessBoundaryTest(unittest.TestCase):
    def test_finalize_streaming_payload_skips_listing_times_derivation_for_deal(self) -> None:
        resolved, _ = finalize_streaming_payload(
            {
                "record_family": "deal",
                "项目编号": "GF2026SH1000254-3",
                "项目类型": "股权转让",
            }
        )

        self.assertNotIn("挂牌次数", resolved)


if __name__ == "__main__":
    unittest.main()
