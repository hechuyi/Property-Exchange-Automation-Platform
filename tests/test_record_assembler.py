from __future__ import annotations

import unittest

from peap_core import AssembledRecordCandidate, PageParseResult, SourceMatch


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
    ) -> PageParseResult:
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
                "page_url": page_url,
                "listing_date": "2026-03-31",
                "candidate_tokens": (project_code, project_name),
            },
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
        self.assertEqual(candidate.source_ids, ("beijing",))
        self.assertEqual({result.snapshot_id for result in candidate.page_results}, {"snap-listing-1", "snap-detail-1"})
        self.assertEqual(candidate.raw_business_object["project_code"], "P001")
        self.assertEqual(candidate.raw_business_object["page_kinds"], ("listing", "detail"))

    def test_assemble_page_results_keeps_detail_and_announcement_partial_until_listing_exists(self) -> None:
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
        self.assertEqual(candidate.completion_state, "partial")
        self.assertIn("listing", candidate.missing_requirements)
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


if __name__ == "__main__":
    unittest.main()
