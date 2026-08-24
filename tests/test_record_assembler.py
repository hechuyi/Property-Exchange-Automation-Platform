from __future__ import annotations

import ast
import inspect
import textwrap
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from peap_core import AssembledRecordCandidate, PageParseResult, SourceMatch


class RecordAssemblerBusinessFieldsTest(unittest.TestCase):
    """Tests that record assembler preserves business fields needed for policy and export."""

    def _make_page_result_with_business_fields(
        self,
        *,
        snapshot_id: str,
        page_kind: str,
        project_code: str,
        project_name: str,
        page_url: str,
        business_type: str = "",
        status: str = "",
        start_date: str = "",
        price: str = "",
        seller: str = "",
        source_type: str = "",
        group_name: str = "",
        deal_date: str = "",
        deal_date_basis: str = "",
        deal_date_is_imputed: bool | None = None,
        collection_date: str = "",
        remark: str = "",
        deal_price: str = "",
        valuation: str = "",
        reserve_price: str = "",
        investors: list[dict[str, object]] | None = None,
        source_id: str = "beijing",
        record_family: str = "listing",
        business_id: str = "",
        deal_price_field: str = "deal_price",
    ) -> PageParseResult:
        facts = [
            {"field": "project_code", "value": project_code},
            {"field": "project_name", "value": project_name},
        ]
        if business_type:
            facts.append({"field": "business_type", "value": business_type})
        if status:
            facts.append({"field": "status", "value": status})
        if start_date:
            facts.append({"field": "start_date", "value": start_date})
        if price:
            facts.append({"field": "price", "value": price})
        if seller:
            facts.append({"field": "seller", "value": seller})
        if source_type:
            facts.append({"field": "source_type", "value": source_type})
        if group_name:
            facts.append({"field": "group_name", "value": group_name})
        if deal_date:
            facts.append({"field": "deal_date", "value": deal_date})
        if deal_date_basis:
            facts.append({"field": "deal_date_basis", "value": deal_date_basis})
        if deal_date_is_imputed is not None:
            facts.append({"field": "deal_date_is_imputed", "value": deal_date_is_imputed})
        if collection_date:
            facts.append({"field": "collection_date", "value": collection_date})
        if remark:
            facts.append({"field": "remark", "value": remark})
        if deal_price:
            facts.append({"field": deal_price_field, "value": deal_price})
        if valuation:
            facts.append({"field": "valuation", "value": valuation})
        if reserve_price:
            facts.append({"field": "reserve_price", "value": reserve_price})
        if investors is not None:
            facts.append({"field": "investors", "value": investors})

        return PageParseResult(
            snapshot_id=snapshot_id,
            source_match=SourceMatch(
                source_id=source_id,
                page_kind=page_kind,
                confidence=0.95,
                status="matched",
                reasons=("fixture",),
                classifier_version="source_classifier/v1",
            ),
            parser_family_id="beijing",
            parser_family_version="builtin/beijing/v1",
            variant_id=page_kind,
            variant_version=f"builtin/beijing/{page_kind}/v1",
            page_identity={
                "page_kind": page_kind,
                "project_code": project_code,
                "project_id": project_code,
                "page_url": page_url,
                "listing_date": start_date,
                "candidate_tokens": (project_code, project_name),
                "record_family": record_family,
                "business_id": business_id,
            },
            facts=tuple(facts),
            outgoing_refs=(),
            diagnostics=(),
            provenance=(),
            recoverability="none",
        )

    def test_assemble_surfaces_business_type_in_nested_business_object(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result_with_business_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            page_url="https://example.invalid/list/1",
            business_type="股权转让",
        )

        assembled = assemble_page_results((listing,))
        self.assertEqual(len(assembled), 1)
        raw_bo = assembled[0].raw_business_object
        self.assertEqual(raw_bo.get("record_family"), "listing")
        self.assertEqual(raw_bo.get("business_identity", {}).get("business_type"), "股权转让")
        self.assertEqual(raw_bo.get("business_fields", {}).get("business_type"), "股权转让")
        self.assertNotIn("project_type", raw_bo)

    def test_assemble_preserves_status(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result_with_business_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            page_url="https://example.invalid/list/1",
            status="挂牌",
        )

        assembled = assemble_page_results((listing,))
        self.assertEqual(len(assembled), 1)
        self.assertEqual(assembled[0].raw_business_object.get("business_fields", {}).get("status"), "挂牌")

    def test_assemble_preserves_start_date(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result_with_business_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            page_url="https://example.invalid/list/1",
            start_date="2026-03-31",
        )

        assembled = assemble_page_results((listing,))
        self.assertEqual(len(assembled), 1)
        self.assertEqual(assembled[0].raw_business_object.get("business_fields", {}).get("start_date"), "2026-03-31")

    def test_assemble_preserves_price(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result_with_business_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            page_url="https://example.invalid/list/1",
            price="108.00",
        )

        assembled = assemble_page_results((listing,))
        self.assertEqual(len(assembled), 1)
        self.assertEqual(assembled[0].raw_business_object.get("business_fields", {}).get("price"), "108.00")

    def test_assemble_preserves_seller(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result_with_business_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            page_url="https://example.invalid/list/1",
            seller="上海测试公司",
        )

        assembled = assemble_page_results((listing,))
        self.assertEqual(len(assembled), 1)
        self.assertEqual(assembled[0].raw_business_object.get("business_fields", {}).get("seller"), "上海测试公司")

    def test_assemble_preserves_source_type(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result_with_business_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            page_url="https://example.invalid/list/1",
            source_type="地方国企",
        )

        assembled = assemble_page_results((listing,))
        self.assertEqual(len(assembled), 1)
        self.assertEqual(assembled[0].raw_business_object.get("business_fields", {}).get("source_type"), "地方国企")

    def test_assemble_preserves_group_name(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result_with_business_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            page_url="https://example.invalid/list/1",
            group_name="上海测试集团",
        )

        assembled = assemble_page_results((listing,))
        self.assertEqual(len(assembled), 1)
        self.assertEqual(assembled[0].raw_business_object.get("business_fields", {}).get("group_name"), "上海测试集团")

    def test_assemble_preserves_all_business_fields_end_to_end(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result_with_business_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="完整项目",
            page_url="https://example.invalid/list/1",
            business_type="股权转让",
            status="挂牌",
            start_date="2026-03-31",
            price="108.00",
            seller="上海测试公司",
            source_type="地方国企",
            group_name="上海测试集团",
        )

        assembled = assemble_page_results((listing,))
        self.assertEqual(len(assembled), 1)
        raw_bo = assembled[0].raw_business_object
        self.assertEqual(raw_bo.get("record_family"), "listing")
        self.assertEqual(raw_bo.get("business_identity", {}).get("business_type"), "股权转让")
        self.assertEqual(raw_bo.get("business_fields", {}).get("status"), "挂牌")
        self.assertEqual(raw_bo.get("business_fields", {}).get("start_date"), "2026-03-31")
        self.assertEqual(raw_bo.get("business_fields", {}).get("price"), "108.00")
        self.assertEqual(raw_bo.get("business_fields", {}).get("seller"), "上海测试公司")
        self.assertEqual(raw_bo.get("business_fields", {}).get("source_type"), "地方国企")
        self.assertEqual(raw_bo.get("business_fields", {}).get("group_name"), "上海测试集团")

    def test_assemble_preserves_deal_specific_fields_and_investors(self) -> None:
        from peap.record_assembler import assemble_page_results

        deal = self._make_page_result_with_business_fields(
            snapshot_id="snap-deal-fields",
            page_kind="deal",
            project_code="Q32026SH1000999",
            project_name="成交字段保真项目",
            page_url="https://example.invalid/deal/fields",
            business_type="股权转让",
            status="成交",
            deal_date="2026-04-18",
            deal_price="1080.5",
            valuation="1200",
            reserve_price="1000",
            investors=[{"name": "投资方甲", "amount": "600", "actual_contribution": "580"}],
            source_id="sse",
            record_family="deal",
        )

        assembled = assemble_page_results((deal,))

        self.assertEqual(len(assembled), 1)
        raw_bo = assembled[0].raw_business_object
        self.assertEqual(raw_bo.get("record_family"), "deal")
        self.assertEqual(raw_bo.get("business_identity", {}).get("business_id"), "deal_equity_transfer")
        self.assertEqual(raw_bo.get("source_identity", {}).get("business_id"), "deal_equity_transfer")
        self.assertEqual(raw_bo.get("business_fields", {}).get("deal_date"), "2026-04-18")
        self.assertEqual(raw_bo.get("business_fields", {}).get("deal_price"), "1080.5")
        self.assertEqual(raw_bo.get("business_fields", {}).get("deal_price_unit_hint"), "deal_price")
        self.assertEqual(raw_bo.get("business_fields", {}).get("valuation"), "1200")
        self.assertEqual(raw_bo.get("business_fields", {}).get("reserve_price"), "1000")
        investors = raw_bo.get("export_extras", {}).get("investors")
        self.assertEqual(
            [dict(item) for item in investors],
            [{"name": "投资方甲", "amount": "600", "actual_contribution": "580"}],
        )

    def test_assemble_preserves_deal_price_field_header_as_unit_hint(self) -> None:
        from peap.record_assembler import assemble_page_results

        deal = self._make_page_result_with_business_fields(
            snapshot_id="snap-deal-price-header",
            page_kind="deal",
            project_code="GR2026TEST0001",
            project_name="成交金额表头单位项目",
            page_url="https://example.invalid/deal/price-header",
            business_type="实物资产",
            status="成交",
            deal_date="2026-04-18",
            deal_price="209959.22",
            deal_price_field="交易价格（元）",
            source_id="cbex",
            record_family="deal",
        )

        assembled = assemble_page_results((deal,))
        raw_bo = assembled[0].raw_business_object

        self.assertEqual(raw_bo.get("business_fields", {}).get("deal_price"), "209959.22")
        self.assertEqual(raw_bo.get("business_fields", {}).get("deal_price_unit_hint"), "交易价格（元）")

    def test_assemble_uses_deal_date_as_start_date_for_deals(self) -> None:
        from peap.record_assembler import assemble_page_results

        deal = self._make_page_result_with_business_fields(
            snapshot_id="snap-deal-provenance",
            page_kind="deal",
            project_code="Q32026SH1000888",
            project_name="成交日期补齐来源项目",
            page_url="https://example.invalid/deal/provenance",
            business_type="实物资产",
            status="成交",
            deal_date="2026-04-18",
            deal_date_basis="deal_date",
            deal_date_is_imputed=False,
            collection_date="2026-04-20",
            remark="原始备注",
            source_id="sse",
            record_family="deal",
        )

        assembled = assemble_page_results((deal,))

        self.assertEqual(len(assembled), 1)
        raw_bo = assembled[0].raw_business_object
        business_fields = raw_bo.get("business_fields", {})
        self.assertEqual(business_fields.get("deal_date"), "2026-04-18")
        self.assertEqual(business_fields.get("deal_date_basis"), "deal_date")
        self.assertFalse(bool(business_fields.get("deal_date_is_imputed")))
        self.assertEqual(business_fields.get("collection_date"), "2026-04-20")
        self.assertEqual(business_fields.get("start_date"), "2026-04-18")
        self.assertEqual(
            raw_bo.get("export_extras", {}).get("备注"),
            "原始备注",
        )

    def test_assemble_keeps_missing_real_deal_date_separate_from_collection_date(self) -> None:
        from peap.record_assembler import assemble_page_results

        deal = self._make_page_result_with_business_fields(
            snapshot_id="snap-deal-missing-real-date",
            page_kind="deal",
            project_code="Q32026SH1000777",
            project_name="缺真实成交日项目",
            page_url="https://example.invalid/deal/missing-real-date",
            business_type="实物资产",
            status="成交",
            deal_date_basis="collection_date",
            deal_date_is_imputed=True,
            collection_date="2026-04-20",
            remark="原始备注；成交日期缺失，按采集日填列",
            source_id="sse",
            record_family="deal",
        )

        assembled = assemble_page_results((deal,))

        self.assertEqual(len(assembled), 1)
        business_fields = assembled[0].raw_business_object.get("business_fields", {})
        self.assertEqual(business_fields.get("deal_date"), "")
        self.assertEqual(business_fields.get("collection_date"), "2026-04-20")
        self.assertEqual(business_fields.get("deal_date_basis"), "collection_date")
        self.assertTrue(bool(business_fields.get("deal_date_is_imputed")))
        self.assertEqual(business_fields.get("start_date"), "2026-04-20")

    def test_assemble_converts_legacy_imputed_deal_date_into_collection_date(self) -> None:
        from peap.record_assembler import assemble_page_results

        deal = self._make_page_result_with_business_fields(
            snapshot_id="snap-deal-legacy-imputed-date",
            page_kind="deal",
            project_code="Q32026SH1000666",
            project_name="旧形态成交日项目",
            page_url="https://example.invalid/deal/legacy-imputed-date",
            business_type="实物资产",
            status="成交",
            deal_date="2026-04-20",
            deal_date_basis="collection_date",
            deal_date_is_imputed=True,
            source_id="sse",
            record_family="deal",
        )

        assembled = assemble_page_results((deal,))

        business_fields = assembled[0].raw_business_object.get("business_fields", {})
        self.assertEqual(business_fields.get("deal_date"), "")
        self.assertEqual(business_fields.get("collection_date"), "2026-04-20")
        self.assertEqual(business_fields.get("deal_date_basis"), "collection_date")
        self.assertTrue(bool(business_fields.get("deal_date_is_imputed")))


class RecordAssemblerContractTest(unittest.TestCase):
    def _make_page_result(
        self,
        *,
        snapshot_id: str,
        page_kind: str,
        project_code: str,
        project_name: str,
        page_url: str,
        outgoing_refs: tuple[dict[str, object], ...] = (),
        source_id: str = "beijing",
        record_family: str | None = "listing",
        business_id: str = "",
    ) -> PageParseResult:
        page_identity = {
            "page_kind": page_kind,
            "project_code": project_code,
            "project_id": project_code,
            "page_url": page_url,
            "listing_date": "2026-03-31",
            "candidate_tokens": (project_code, project_name),
            "business_id": business_id,
        }
        if record_family is not None:
            page_identity["record_family"] = record_family

        return PageParseResult(
            snapshot_id=snapshot_id,
            source_match=SourceMatch(
                source_id=source_id,
                page_kind=page_kind,
                confidence=0.95,
                status="matched",
                reasons=("fixture",),
                classifier_version="source_classifier/v1",
            ),
            parser_family_id="beijing",
            parser_family_version="builtin/beijing/v1",
            variant_id=page_kind,
            variant_version=f"builtin/beijing/{page_kind}/v1",
            page_identity=page_identity,
            facts=(
                {"field": "project_code", "value": project_code},
                {"field": "project_name", "value": project_name},
            ),
            outgoing_refs=outgoing_refs,
            diagnostics=(),
            provenance=(),
            recoverability="none",
        )

    def test_assemble_page_results_merges_listing_and_detail_by_candidate_tokens_and_refs(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result(
            snapshot_id="snap-listing-1",
            page_kind="listing",
            project_code="P001",
            project_name="示例项目",
            page_url="https://example.invalid/list/1",
            outgoing_refs=(
                {
                    "target_kind": "detail",
                    "target_url": "https://example.invalid/detail/1",
                    "ref_reason": "detail link",
                    "correlation_hints": ("P001", "示例项目"),
                },
            ),
        )
        detail = self._make_page_result(
            snapshot_id="snap-detail-1",
            page_kind="detail",
            project_code="P001",
            project_name="示例项目",
            page_url="https://example.invalid/detail/1",
        )

        assembled = assemble_page_results((listing, detail))

        self.assertEqual(len(assembled), 1)
        candidate = assembled[0]
        self.assertIsInstance(candidate, AssembledRecordCandidate)
        self.assertEqual(candidate.completion_state, "sufficient")
        self.assertEqual(candidate.entity_keys, ("P001", "示例项目"))
        self.assertEqual(candidate.source_ids, ("cbex",))
        self.assertEqual({result.snapshot_id for result in candidate.page_results}, {"snap-listing-1", "snap-detail-1"})
        self.assertEqual(candidate.raw_business_object["project_code"], "P001")
        self.assertEqual(candidate.raw_business_object["page_kinds"], ("listing", "detail"))

    def test_assemble_page_results_treats_detail_and_announcement_as_sufficient_when_identity_is_complete(self) -> None:
        from peap.record_assembler import assemble_page_results

        detail = self._make_page_result(
            snapshot_id="snap-detail-2",
            page_kind="detail",
            project_code="P002",
            project_name="待补项目",
            page_url="https://example.invalid/detail/2",
            outgoing_refs=(
                {
                    "target_kind": "announcement",
                    "target_url": "https://example.invalid/notice/2",
                    "ref_reason": "notice link",
                    "correlation_hints": ("P002",),
                },
            ),
        )
        announcement = self._make_page_result(
            snapshot_id="snap-announce-2",
            page_kind="announcement",
            project_code="P002",
            project_name="待补项目",
            page_url="https://example.invalid/notice/2",
        )

        assembled = assemble_page_results((detail, announcement))

        self.assertEqual(len(assembled), 1)
        candidate = assembled[0]
        self.assertEqual(candidate.completion_state, "sufficient")
        self.assertEqual(candidate.missing_requirements, ())
        self.assertEqual({result.snapshot_id for result in candidate.page_results}, {"snap-detail-2", "snap-announce-2"})

    def test_assemble_page_results_marks_conflicted_when_candidate_tokens_disagree(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result(
            snapshot_id="snap-listing-3",
            page_kind="listing",
            project_code="P003",
            project_name="项目甲",
            page_url="https://example.invalid/list/3",
            outgoing_refs=(
                {
                    "target_kind": "detail",
                    "target_url": "https://example.invalid/detail/3",
                    "ref_reason": "detail link",
                    "correlation_hints": ("P003",),
                },
            ),
        )
        detail = self._make_page_result(
            snapshot_id="snap-detail-3",
            page_kind="detail",
            project_code="P003",
            project_name="项目乙",
            page_url="https://example.invalid/detail/3",
        )

        assembled = assemble_page_results((listing, detail))

        self.assertEqual(len(assembled), 1)
        candidate = assembled[0]
        self.assertEqual(candidate.completion_state, "conflicted")
        self.assertIn("project_name_conflict", candidate.missing_requirements)

    def test_assemble_page_results_uses_outgoing_refs_and_tokens_not_dom(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result(
            snapshot_id="snap-listing-4",
            page_kind="listing",
            project_code="P004",
            project_name="仅靠引用关联",
            page_url="https://example.invalid/list/4",
            outgoing_refs=(
                {
                    "target_kind": "detail",
                    "target_url": "https://example.invalid/detail/4",
                    "ref_reason": "detail link",
                    "correlation_hints": ("P004", "仅靠引用关联"),
                },
            ),
        )
        detail = self._make_page_result(
            snapshot_id="snap-detail-4",
            page_kind="detail",
            project_code="P004",
            project_name="仅靠引用关联",
            page_url="https://example.invalid/detail/4",
        )

        assembled = assemble_page_results((listing, detail))

        self.assertEqual(len(assembled), 1)
        self.assertEqual(assembled[0].completion_state, "sufficient")
        self.assertEqual(assembled[0].entity_keys, ("P004", "仅靠引用关联"))

    def test_assemble_page_results_does_not_merge_listing_and_deal_with_same_project_code(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result(
            snapshot_id="snap-listing-same-code",
            page_kind="listing",
            project_code="P9001",
            project_name="同编码挂牌",
            page_url="https://example.invalid/list/same-code",
            source_id="sse",
            record_family="listing",
            business_id="equity_transfer",
        )
        deal = self._make_page_result(
            snapshot_id="snap-deal-same-code",
            page_kind="deal",
            project_code="P9001",
            project_name="同编码成交",
            page_url="https://example.invalid/deal/same-code",
            source_id="sse",
            record_family="deal",
            business_id="deal_equity_transfer",
        )

        assembled = assemble_page_results((listing, deal))

        self.assertEqual(len(assembled), 2)
        families = {item.raw_business_object.get("record_family") for item in assembled}
        self.assertEqual(families, {"listing", "deal"})

    def test_assemble_page_results_includes_scope_in_assembly_id(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result(
            snapshot_id="snap-listing-same-identity",
            page_kind="listing",
            project_code="P9003",
            project_name="同编码同名称项目",
            page_url="https://example.invalid/list/same-identity",
            source_id="sse",
            record_family="listing",
            business_id="equity_transfer",
        )
        deal = self._make_page_result(
            snapshot_id="snap-deal-same-identity",
            page_kind="deal",
            project_code="P9003",
            project_name="同编码同名称项目",
            page_url="https://example.invalid/deal/same-identity",
            source_id="sse",
            record_family="deal",
            business_id="deal_equity_transfer",
        )

        assembled = assemble_page_results((listing, deal))

        self.assertEqual(len(assembled), 2)
        assembly_ids = {item.assembly_id for item in assembled}
        self.assertEqual(len(assembly_ids), 2)

    def test_assemble_page_results_uses_explicit_record_family_instead_of_source_id_inference(self) -> None:
        from peap.record_assembler import assemble_page_results

        deal = self._make_page_result(
            snapshot_id="snap-deal-explicit-family",
            page_kind="detail",
            project_code="P9002",
            project_name="显式成交项目",
            page_url="https://example.invalid/deal/explicit-family",
            source_id="sse",
            record_family="deal",
            business_id="deal_equity_transfer",
        )

        assembled = assemble_page_results((deal,))

        self.assertEqual(len(assembled), 1)
        candidate = assembled[0]
        self.assertEqual(candidate.raw_business_object.get("record_family"), "deal")
        self.assertEqual(candidate.raw_business_object.get("business_identity", {}).get("business_id"), "deal_equity_transfer")
        self.assertEqual(candidate.raw_business_object.get("source_identity", {}).get("record_family"), "deal")

    def test_assemble_page_results_resolves_page_kind_fallback_through_family_catalog(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result(
            snapshot_id="snap-page-kind-alias-family",
            page_kind="PAGE_KIND_ALIAS",
            project_code="P9004",
            project_name="页面类型别名项目",
            page_url="https://example.invalid/list/page-kind-alias",
            record_family=None,
        )

        with patch(
            "peap.record_assembler.get_family_descriptor",
            return_value=SimpleNamespace(family_id="listing"),
        ) as get_family_descriptor:
            assembled = assemble_page_results((listing,))

        self.assertEqual(len(assembled), 1)
        candidate = assembled[0]
        self.assertEqual(candidate.raw_business_object.get("record_family"), "listing")
        self.assertEqual(candidate.completion_state, "sufficient")
        self.assertNotIn("record_family", candidate.missing_requirements)
        get_family_descriptor.assert_any_call("PAGE_KIND_ALIAS")

    def test_assemble_page_results_keeps_unknown_page_kind_family_missing(self) -> None:
        from peap.record_assembler import assemble_page_results

        result = self._make_page_result(
            snapshot_id="snap-unknown-page-kind-family",
            page_kind="UNKNOWN_PAGE_KIND",
            project_code="P9005",
            project_name="未知页面类型项目",
            page_url="https://example.invalid/list/unknown-page-kind",
            record_family=None,
        )

        with patch("peap.record_assembler.get_family_descriptor", side_effect=KeyError) as get_family_descriptor:
            assembled = assemble_page_results((result,))

        self.assertEqual(len(assembled), 1)
        candidate = assembled[0]
        self.assertEqual(candidate.raw_business_object.get("record_family"), "")
        self.assertEqual(candidate.completion_state, "partial")
        self.assertIn("record_family", candidate.missing_requirements)
        get_family_descriptor.assert_any_call("UNKNOWN_PAGE_KIND")

    def test_assemble_page_results_marks_unknown_source_identity_missing(self) -> None:
        from peap.record_assembler import assemble_page_results

        result = self._make_page_result(
            snapshot_id="snap-unknown-source",
            page_kind="listing",
            project_code="P9006",
            project_name="未知交易所项目",
            page_url="https://example.invalid/list/unknown-source",
            source_id="mystery_exchange",
            record_family="listing",
        )

        assembled = assemble_page_results((result,))

        self.assertEqual(len(assembled), 1)
        candidate = assembled[0]
        self.assertEqual(candidate.completion_state, "partial")
        self.assertIn("source_id", candidate.missing_requirements)
        self.assertEqual(candidate.source_ids, ())
        self.assertEqual(candidate.raw_business_object.get("source_identity", {}).get("source_id"), "")
        self.assertIn(
            {
                "code": "unknown_source_identity",
                "source_id": "mystery_exchange",
                "snapshot_id": "snap-unknown-source",
            },
            candidate.assembly_diagnostics,
        )

    def test_record_family_page_kind_fallback_does_not_use_local_listing_deal_gate(self) -> None:
        import peap.record_assembler as record_assembler

        tree = ast.parse(textwrap.dedent(inspect.getsource(record_assembler._record_family)))
        listing_deal_sets = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Set):
                continue
            values = {
                element.value
                for element in node.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            if len(node.elts) == 2 and values == {"listing", "deal"}:
                listing_deal_sets.append(node)

        self.assertFalse(listing_deal_sets)


if __name__ == "__main__":
    unittest.main()
