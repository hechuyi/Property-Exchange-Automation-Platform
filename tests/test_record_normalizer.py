from __future__ import annotations

import unittest

from peap_core import AssembledRecordCandidate, CanonicalRecord, PageParseResult, SourceMatch


class FalsyMapping(dict):
    def __bool__(self) -> bool:
        return False


class RecordNormalizerBusinessFieldsTest(unittest.TestCase):
    """Tests that normalize preserves all canonical fields end-to-end."""

    def _make_page_result_with_all_fields(
        self,
        *,
        snapshot_id: str,
        page_kind: str,
        project_code: str,
        project_name: str,
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
            facts.append({"field": "deal_price", "value": deal_price})
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
                "page_url": f"https://example.invalid/{page_kind}/{project_code}",
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

    def test_normalize_preserves_business_type(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        listing = self._make_page_result_with_all_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            business_type="股权转让",
        )
        assembled = assemble_page_results((listing,))
        canonical = normalize_assembled_record(assembled[0])
        self.assertEqual(canonical.record_family, "listing")
        self.assertEqual(canonical.business_identity.get("business_type"), "股权转让")
        self.assertEqual(canonical.canonical_fields.get("business_type"), "股权转让")
        self.assertEqual(canonical.canonical_fields.get("project_type"), "股权转让")

    def test_normalize_preserves_status(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        listing = self._make_page_result_with_all_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            status="挂牌",
        )
        assembled = assemble_page_results((listing,))
        canonical = normalize_assembled_record(assembled[0])
        self.assertEqual(canonical.canonical_fields.get("status"), "挂牌")

    def test_normalize_preserves_start_date(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        listing = self._make_page_result_with_all_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            start_date="2026-03-31",
        )
        assembled = assemble_page_results((listing,))
        canonical = normalize_assembled_record(assembled[0])
        self.assertEqual(canonical.canonical_fields.get("start_date"), "2026/03/31")

    def test_normalize_preserves_price(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        listing = self._make_page_result_with_all_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            price="108.00",
        )
        assembled = assemble_page_results((listing,))
        canonical = normalize_assembled_record(assembled[0])
        self.assertEqual(canonical.canonical_fields.get("price"), "108.00")

    def test_normalize_preserves_seller(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        listing = self._make_page_result_with_all_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            seller="上海测试公司",
        )
        assembled = assemble_page_results((listing,))
        canonical = normalize_assembled_record(assembled[0])
        self.assertEqual(canonical.canonical_fields.get("seller"), "上海测试公司")

    def test_normalize_preserves_source_type(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        listing = self._make_page_result_with_all_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            source_type="地方国企",
        )
        assembled = assemble_page_results((listing,))
        canonical = normalize_assembled_record(assembled[0])
        self.assertEqual(canonical.canonical_fields.get("source_type"), "地方国企")

    def test_normalize_preserves_group_name(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        listing = self._make_page_result_with_all_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            group_name="上海测试集团",
        )
        assembled = assemble_page_results((listing,))
        canonical = normalize_assembled_record(assembled[0])
        self.assertEqual(canonical.canonical_fields.get("group_name"), "上海测试集团")

    def test_normalize_preserves_all_fields_end_to_end(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        listing = self._make_page_result_with_all_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="完整项目",
            business_type="股权转让",
            status="挂牌",
            start_date="2026-03-31",
            price="108.00",
            seller="上海测试公司",
            source_type="地方国企",
            group_name="上海测试集团",
        )
        assembled = assemble_page_results((listing,))
        canonical = normalize_assembled_record(assembled[0])
        cf = canonical.canonical_fields
        self.assertEqual(canonical.business_identity.get("business_type"), "股权转让")
        self.assertEqual(cf.get("business_type"), "股权转让")
        self.assertEqual(cf.get("project_type"), "股权转让")
        self.assertEqual(cf.get("status"), "挂牌")
        self.assertEqual(cf.get("start_date"), "2026/03/31")
        self.assertEqual(cf.get("price"), "108.00")
        self.assertEqual(cf.get("seller"), "上海测试公司")
        self.assertEqual(cf.get("source_type"), "地方国企")
        self.assertEqual(cf.get("group_name"), "上海测试集团")

    def test_normalize_preserves_deal_specific_fields_and_investors(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        deal = self._make_page_result_with_all_fields(
            snapshot_id="snap-deal-fields",
            page_kind="deal",
            project_code="Q32026SH1000999",
            project_name="成交字段保真项目",
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
        canonical = normalize_assembled_record(assembled[0])

        cf = canonical.canonical_fields
        self.assertEqual(canonical.record_family, "deal")
        self.assertEqual(canonical.business_identity.get("business_id"), "deal_equity_transfer")
        self.assertEqual(canonical.source_identity.get("business_id"), "deal_equity_transfer")
        self.assertEqual(cf.get("deal_date"), "2026/04/18")
        self.assertEqual(cf.get("deal_price"), "1080.5")
        self.assertEqual(cf.get("valuation"), "1200")
        self.assertEqual(cf.get("reserve_price"), "1000")
        investors = canonical.export_extras.get("investors")
        self.assertEqual(
            [dict(item) for item in investors],
            [{"name": "投资方甲", "amount": "600", "actual_contribution": "580"}],
        )

    def test_normalize_preserves_deal_date_provenance_and_collection_date(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        deal = self._make_page_result_with_all_fields(
            snapshot_id="snap-deal-provenance",
            page_kind="deal",
            project_code="Q32026SH1000888",
            project_name="成交日期来源保真项目",
            business_type="股权转让",
            status="成交",
            start_date="2026-04-20",
            deal_date="2026-04-18",
            deal_date_basis="deal_date",
            deal_date_is_imputed=False,
            collection_date="2026-04-20",
            remark="原始备注",
            deal_price="980.5",
            source_id="sse",
            record_family="deal",
        )

        assembled = assemble_page_results((deal,))
        canonical = normalize_assembled_record(assembled[0])

        cf = canonical.canonical_fields
        self.assertEqual(cf.get("deal_date"), "2026/04/18")
        self.assertEqual(cf.get("deal_date_basis"), "deal_date")
        self.assertFalse(bool(cf.get("deal_date_is_imputed")))
        self.assertEqual(cf.get("collection_date"), "2026/04/20")
        self.assertEqual(cf.get("start_date"), "2026/04/18")
        self.assertEqual(canonical.export_extras.get("备注"), "原始备注")

    def test_normalize_keeps_missing_real_deal_date_separate_from_collection_date(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        deal = self._make_page_result_with_all_fields(
            snapshot_id="snap-deal-missing-real-date",
            page_kind="deal",
            project_code="Q32026SH1000777",
            project_name="缺真实成交日项目",
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
        canonical = normalize_assembled_record(assembled[0])

        cf = canonical.canonical_fields
        self.assertEqual(cf.get("deal_date"), "")
        self.assertEqual(cf.get("collection_date"), "2026/04/20")
        self.assertEqual(cf.get("deal_date_basis"), "collection_date")
        self.assertTrue(bool(cf.get("deal_date_is_imputed")))
        self.assertEqual(cf.get("start_date"), "2026/04/20")
        self.assertEqual(canonical.export_extras.get("备注"), "原始备注；成交日期缺失，按采集日填列")

    def test_normalize_does_not_fill_deal_collection_date_from_start_date(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        deal = self._make_page_result_with_all_fields(
            snapshot_id="snap-deal-start-date-only",
            page_kind="deal",
            project_code="Q32026SH1000666",
            project_name="仅有挂牌日期的成交项目",
            business_type="股权转让",
            status="成交",
            start_date="2026-03-20",
            source_id="sse",
            record_family="deal",
        )

        assembled = assemble_page_results((deal,))
        canonical = normalize_assembled_record(assembled[0])

        cf = canonical.canonical_fields
        self.assertEqual(cf.get("start_date"), "")
        self.assertEqual(cf.get("deal_date"), "")
        self.assertEqual(cf.get("collection_date", ""), "")


class RecordNormalizerTest(unittest.TestCase):
    def _make_page_result(
        self,
        *,
        snapshot_id: str,
        page_kind: str,
        project_code: str,
        project_name: str,
        source_id: str = "beijing",
        record_family: str = "listing",
        business_id: str = "",
    ) -> PageParseResult:
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
                "page_url": f"https://example.invalid/{page_kind}/{project_code}",
                "listing_date": "2026-03-31",
                "candidate_tokens": (project_code, project_name),
                "record_family": record_family,
                "business_id": business_id,
            },
            facts=(
                {"field": "project_code", "value": project_code},
                {"field": "project_name", "value": project_name},
                {"field": "business_type", "value": "股权转让"},
                {"field": "status", "value": "挂牌"},
                {"field": "start_date", "value": "2026-03-31"},
                {"field": "price", "value": "108.00"},
            ),
            outgoing_refs=(),
            diagnostics=(),
            provenance=(),
            recoverability="none",
        )

    def test_normalize_assembled_record_builds_canonical_record_with_invariants(self) -> None:
        from peap.record_normalizer import normalize_assembled_record

        assembled = AssembledRecordCandidate(
            assembly_id="asm-001",
            source_ids=("beijing",),
            page_results=(self._make_page_result(snapshot_id="snap-1", page_kind="listing", project_code="P001", project_name="规范化项目"),),
            entity_keys=("P001", "规范化项目"),
            completion_state="sufficient",
            raw_business_object={
                "record_family": "listing",
                "business_identity": {
                    "project_code": "P001",
                    "project_name": "规范化项目",
                    "business_type": "股权转让",
                },
                "business_fields": {
                    "status": "挂牌",
                    "start_date": "2026-03-31",
                    "price": "108.00",
                },
            },
        )

        canonical = normalize_assembled_record(assembled)

        self.assertIsInstance(canonical, CanonicalRecord)
        self.assertEqual(canonical.record_id, "asm-001")
        self.assertEqual(canonical.record_family, "listing")
        self.assertEqual(canonical.business_identity["record_family"], "listing")
        self.assertEqual(canonical.business_identity["business_type"], "股权转让")
        self.assertEqual(canonical.business_identity["project_code"], "P001")
        self.assertEqual(canonical.canonical_fields["project_name"], "规范化项目")
        self.assertEqual(canonical.canonical_fields["business_type"], "股权转让")
        self.assertEqual(canonical.canonical_fields["status"], "挂牌")
        self.assertEqual(canonical.canonical_fields["start_date"], "2026/03/31")
        self.assertEqual(canonical.canonical_fields["price"], "108.00")
        self.assertEqual(canonical.field_provenance["project_name"]["snapshot_id"], "snap-1")

    def test_normalize_assembled_record_rejects_missing_record_family(self) -> None:
        from peap.record_normalizer import normalize_assembled_record

        assembled = AssembledRecordCandidate(
            assembly_id="asm-missing-family",
            source_ids=("beijing",),
            page_results=(self._make_page_result(snapshot_id="snap-2", page_kind="listing", project_code="P002", project_name="缺 family 项目"),),
            entity_keys=("P002", "缺 family 项目"),
            completion_state="sufficient",
            raw_business_object={
                "business_identity": {
                    "project_code": "P002",
                    "project_name": "缺 family 项目",
                    "business_type": "股权转让",
                },
                "business_fields": {
                    "status": "挂牌",
                },
            },
        )

        with self.assertRaises(ValueError):
            normalize_assembled_record(assembled)

    def test_normalize_assembled_record_preserves_falsy_business_identity_record_family(self) -> None:
        from peap.record_normalizer import normalize_assembled_record

        assembled = AssembledRecordCandidate(
            assembly_id="asm-falsy-business-identity",
            source_ids=("beijing",),
            page_results=(
                self._make_page_result(
                    snapshot_id="snap-falsy-business-identity",
                    page_kind="listing",
                    project_code="P002",
                    project_name="falsy identity 项目",
                ),
            ),
            entity_keys=("P002", "falsy identity 项目"),
            completion_state="sufficient",
            raw_business_object={
                "business_identity": FalsyMapping(
                    {
                        "record_family": "listing",
                        "project_code": "P002",
                        "project_name": "falsy identity 项目",
                        "business_type": "股权转让",
                    }
                ),
                "business_fields": {
                    "status": "挂牌",
                },
            },
        )

        canonical = normalize_assembled_record(assembled)

        self.assertEqual(canonical.record_family, "listing")
        self.assertEqual(canonical.business_identity["record_family"], "listing")

    def test_normalize_assembled_record_rejects_explicit_non_mapping_business_object(self) -> None:
        from peap.record_normalizer import normalize_assembled_record

        assembled = AssembledRecordCandidate(
            assembly_id="asm-bad-business-object",
            source_ids=("beijing",),
            page_results=(self._make_page_result(snapshot_id="snap-bad-root", page_kind="listing", project_code="P003", project_name="坏 root 项目"),),
            entity_keys=("P003", "坏 root 项目"),
            completion_state="sufficient",
            raw_business_object=("record_family", "listing"),
        )

        with self.assertRaisesRegex(TypeError, "raw_business_object must be an object"):
            normalize_assembled_record(assembled)

    def test_normalize_assembled_record_rejects_explicit_non_mapping_nested_objects(self) -> None:
        from peap.record_normalizer import normalize_assembled_record

        for field_name in ("source_identity", "business_identity", "business_fields", "export_extras"):
            with self.subTest(field_name=field_name):
                raw_business_object: dict[str, object] = {
                    "record_family": "listing",
                    "business_identity": {
                        "project_code": "P004",
                        "project_name": "坏 nested 项目",
                        "business_type": "股权转让",
                    },
                    "business_fields": {
                        "status": "挂牌",
                    },
                    field_name: (),
                }
                if field_name == "business_identity":
                    raw_business_object["project_code"] = "P004"
                    raw_business_object["project_name"] = "坏 nested 项目"
                    raw_business_object["business_type"] = "股权转让"

                assembled = AssembledRecordCandidate(
                    assembly_id=f"asm-bad-{field_name}",
                    source_ids=("beijing",),
                    page_results=(self._make_page_result(snapshot_id=f"snap-bad-{field_name}", page_kind="listing", project_code="P004", project_name="坏 nested 项目"),),
                    entity_keys=("P004", "坏 nested 项目"),
                    completion_state="sufficient",
                    raw_business_object=raw_business_object,
                )

                with self.assertRaisesRegex(TypeError, f"raw_business_object.{field_name} must be an object"):
                    normalize_assembled_record(assembled)

    def test_normalize_assembled_record_keeps_explicit_business_id_and_source_id(self) -> None:
        from peap.record_normalizer import normalize_assembled_record

        assembled = AssembledRecordCandidate(
            assembly_id="asm-deal-001",
            source_ids=("sse",),
            page_results=(
                self._make_page_result(
                    snapshot_id="snap-deal-1",
                    page_kind="deal",
                    project_code="D001",
                    project_name="成交规范化项目",
                    source_id="shanghai",
                    record_family="deal",
                    business_id="deal_equity_transfer",
                ),
            ),
            entity_keys=("D001", "成交规范化项目"),
            completion_state="sufficient",
            raw_business_object={
                "record_family": "deal",
                "source_identity": {
                    "record_family": "deal",
                    "source_id": "sse",
                    "business_id": "deal_equity_transfer",
                },
                "business_identity": {
                    "record_family": "deal",
                    "project_code": "D001",
                    "project_name": "成交规范化项目",
                    "business_type": "股权转让",
                    "business_id": "deal_equity_transfer",
                },
                "business_fields": {
                    "status": "成交",
                    "start_date": "2026-04-02",
                },
            },
        )

        canonical = normalize_assembled_record(assembled)

        self.assertEqual(canonical.record_family, "deal")
        self.assertEqual(canonical.business_identity.get("business_id"), "deal_equity_transfer")
        self.assertEqual(canonical.source_identity.get("source_id"), "sse")

    def test_normalize_converts_legacy_imputed_deal_date_into_collection_date(self) -> None:
        from peap.record_normalizer import normalize_assembled_record

        assembled = AssembledRecordCandidate(
            assembly_id="asm-deal-legacy-imputed-date",
            source_ids=("sse",),
            page_results=(
                self._make_page_result(
                    snapshot_id="snap-deal-legacy-imputed-date",
                    page_kind="deal",
                    project_code="D002",
                    project_name="旧形态成交日项目",
                    source_id="shanghai",
                    record_family="deal",
                    business_id="deal_equity_transfer",
                ),
            ),
            entity_keys=("D002", "旧形态成交日项目"),
            completion_state="sufficient",
            raw_business_object={
                "record_family": "deal",
                "source_identity": {
                    "record_family": "deal",
                    "source_id": "sse",
                    "business_id": "deal_equity_transfer",
                },
                "business_identity": {
                    "record_family": "deal",
                    "project_code": "D002",
                    "project_name": "旧形态成交日项目",
                    "business_type": "股权转让",
                    "business_id": "deal_equity_transfer",
                },
                "business_fields": {
                    "status": "成交",
                    "deal_date": "2026-04-20",
                    "deal_date_basis": "collection_date",
                    "deal_date_is_imputed": True,
                },
            },
        )

        canonical = normalize_assembled_record(assembled)

        self.assertEqual(canonical.canonical_fields.get("deal_date"), "")
        self.assertEqual(canonical.canonical_fields.get("collection_date"), "2026/04/20")
        self.assertEqual(canonical.canonical_fields.get("deal_date_basis"), "collection_date")
        self.assertTrue(bool(canonical.canonical_fields.get("deal_date_is_imputed")))


if __name__ == "__main__":
    unittest.main()
