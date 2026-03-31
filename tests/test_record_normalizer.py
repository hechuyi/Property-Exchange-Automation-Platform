from __future__ import annotations

import unittest

from peap_core import AssembledRecordCandidate, CanonicalRecord, PageParseResult, SourceMatch


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
