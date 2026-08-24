from __future__ import annotations

import unittest

from peap.export_projection import project_canonical_record_to_export_payload
from peap.record_normalizer import normalize_assembled_record
from peap_core import AssembledRecordCandidate, PageParseResult, SourceMatch


class RecordNormalizerProjectTypeAliasTest(unittest.TestCase):
    def test_business_type_populates_project_type_alias_for_export_projection(self) -> None:
        page = PageParseResult(
            snapshot_id="snap-project-type-alias",
            source_match=SourceMatch(
                source_id="sse",
                page_kind="listing",
                confidence=0.95,
                status="matched",
            ),
            parser_family_id="sse",
            parser_family_version="test/sse/v1",
            variant_id="listing",
            variant_version="test/sse/listing/v1",
            page_identity={"page_kind": "listing"},
        )
        assembled = AssembledRecordCandidate(
            assembly_id="assembly-project-type-alias",
            source_ids=("sse",),
            page_results=(page,),
            entity_keys=("P001",),
            completion_state="sufficient",
            raw_business_object={
                "record_family": "listing",
                "business_identity": {
                    "project_code": "P001",
                    "project_name": "测试项目",
                },
                "business_fields": {
                    "business_type": "股权转让",
                    "status": "挂牌",
                    "start_date": "2026-03-31",
                    "price": "108.00",
                    "seller": "上海测试公司",
                    "source_type": "地方国企",
                },
            },
        )

        canonical = normalize_assembled_record(assembled)

        self.assertEqual(canonical.canonical_fields["project_type"], "股权转让")
        self.assertEqual(canonical.canonical_fields["business_type"], "股权转让")
        payload, findings = project_canonical_record_to_export_payload(
            canonical,
            fail_on_missing=False,
        )
        self.assertEqual(findings, ())
        self.assertEqual(payload["项目类型"], "股权转让")

    def test_deal_price_normalization_uses_header_unit_hint(self) -> None:
        page = PageParseResult(
            snapshot_id="snap-deal-price-unit-hint",
            source_match=SourceMatch(
                source_id="cbex",
                page_kind="deal",
                confidence=0.95,
                status="matched",
            ),
            parser_family_id="cbex",
            parser_family_version="test/cbex/v1",
            variant_id="deal",
            variant_version="test/cbex/deal/v1",
            page_identity={"page_kind": "deal"},
        )
        assembled = AssembledRecordCandidate(
            assembly_id="assembly-deal-price-unit-hint",
            source_ids=("cbex",),
            page_results=(page,),
            entity_keys=("D001",),
            completion_state="sufficient",
            raw_business_object={
                "record_family": "deal",
                "business_identity": {
                    "business_id": "deal_physical_asset",
                    "project_code": "D001",
                    "project_name": "成交金额单位项目",
                },
                "business_fields": {
                    "business_type": "实物资产",
                    "status": "成交",
                    "deal_date": "2026-05-01",
                    "deal_price": "209959.22",
                    "deal_price_unit_hint": "交易价格（元）",
                },
            },
        )

        canonical = normalize_assembled_record(assembled)
        fields = canonical.canonical_fields

        self.assertEqual(fields["deal_price"], "20.995922")
        self.assertEqual(fields["deal_price_raw"], "209959.22")
        self.assertEqual(fields["deal_price_unit"], "万元")
        self.assertEqual(fields["deal_price_source_unit"], "元")
        self.assertEqual(fields["deal_price_unit_basis"], "converted_from_field_yuan")


if __name__ == "__main__":
    unittest.main()
