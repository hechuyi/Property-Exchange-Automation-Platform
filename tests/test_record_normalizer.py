from __future__ import annotations

import unittest

from peap_core import AssembledRecordCandidate, CanonicalRecord, PageParseResult, SourceMatch


class RecordNormalizerBusinessFieldsTest(unittest.TestCase):
    """Tests that normalize preserves all canonical fields end-to-end."""

    def _make_page_result_with_all_fields(
        self,
        *,
        snapshot_id: str,
        page_kind: str,
        project_code: str,
        project_name: str,
        project_type: str = "",
        status: str = "",
        start_date: str = "",
        price: str = "",
        seller: str = "",
        source_type: str = "",
        group_name: str = "",
    ) -> PageParseResult:
        facts = [
            {"field": "project_code", "value": project_code},
            {"field": "project_name", "value": project_name},
        ]
        if project_type:
            facts.append({"field": "project_type", "value": project_type})
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

        return PageParseResult(
            snapshot_id=snapshot_id,
            source_match=SourceMatch(
                source_id="beijing",
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
            },
            facts=tuple(facts),
            outgoing_refs=(),
            diagnostics=(),
            provenance=(),
            recoverability="none",
        )

    def test_normalize_preserves_project_type(self) -> None:
        from peap.record_assembler import assemble_page_results
        from peap.record_normalizer import normalize_assembled_record

        listing = self._make_page_result_with_all_fields(
            snapshot_id="snap-1",
            page_kind="listing",
            project_code="P001",
            project_name="测试项目",
            project_type="股权转让",
        )
        assembled = assemble_page_results((listing,))
        canonical = normalize_assembled_record(assembled[0])
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
            project_type="股权转让",
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
        self.assertEqual(cf.get("project_type"), "股权转让")
        self.assertEqual(cf.get("status"), "挂牌")
        self.assertEqual(cf.get("start_date"), "2026/03/31")
        self.assertEqual(cf.get("price"), "108.00")
        self.assertEqual(cf.get("seller"), "上海测试公司")
        self.assertEqual(cf.get("source_type"), "地方国企")
        self.assertEqual(cf.get("group_name"), "上海测试集团")


class RecordNormalizerTest(unittest.TestCase):
    def _make_page_result(self, *, snapshot_id: str, page_kind: str, project_code: str, project_name: str) -> PageParseResult:
        return PageParseResult(
            snapshot_id=snapshot_id,
            source_match=SourceMatch(
                source_id="beijing",
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
            },
            facts=(
                {"field": "project_code", "value": project_code},
                {"field": "project_name", "value": project_name},
                {"field": "project_type", "value": "股权转让"},
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
                "project_code": "P001",
                "project_name": "规范化项目",
                "project_type": "股权转让",
                "status": "挂牌",
                "start_date": "2026-03-31",
                "price": "108.00",
            },
        )

        canonical = normalize_assembled_record(assembled)

        self.assertIsInstance(canonical, CanonicalRecord)
        self.assertEqual(canonical.record_id, "asm-001")
        self.assertEqual(canonical.record_family, "listing")
        self.assertEqual(canonical.business_identity["project_code"], "P001")
        self.assertEqual(canonical.canonical_fields["project_name"], "规范化项目")
        self.assertEqual(canonical.canonical_fields["project_type"], "股权转让")
        self.assertEqual(canonical.canonical_fields["status"], "挂牌")
        self.assertEqual(canonical.canonical_fields["start_date"], "2026/03/31")
        self.assertEqual(canonical.canonical_fields["price"], "108.00")
        self.assertEqual(canonical.field_provenance["project_name"]["snapshot_id"], "snap-1")


if __name__ == "__main__":
    unittest.main()
