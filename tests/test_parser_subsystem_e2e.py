from __future__ import annotations

import unittest

from peap_core import PageParseResult, SourceMatch


class ParserSubsystemE2ETest(unittest.TestCase):
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

    def test_page_results_assemble_into_one_candidate_with_lineage(self) -> None:
        from peap.record_assembler import assemble_page_results

        listing = self._make_page_result(
            snapshot_id="snap-listing-e2e",
            page_kind="listing",
            project_code="P900",
            project_name="端到端项目",
            page_url="https://example.invalid/list/900",
            outgoing_refs=(
                {
                    "target_kind": "detail",
                    "target_url": "https://example.invalid/detail/900",
                    "ref_reason": "detail link",
                    "correlation_hints": ("P900", "端到端项目"),
                },
            ),
        )
        detail = self._make_page_result(
            snapshot_id="snap-detail-e2e",
            page_kind="detail",
            project_code="P900",
            project_name="端到端项目",
            page_url="https://example.invalid/detail/900",
        )

        assembled = assemble_page_results((listing, detail))

        self.assertEqual(len(assembled), 1)
        candidate = assembled[0]
        self.assertEqual(candidate.completion_state, "sufficient")
        self.assertEqual(candidate.raw_business_object["project_name"], "端到端项目")
        self.assertEqual([result.snapshot_id for result in candidate.page_results], ["snap-listing-e2e", "snap-detail-e2e"])


if __name__ == "__main__":
    unittest.main()
