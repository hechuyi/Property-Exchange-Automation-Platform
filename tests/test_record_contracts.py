from __future__ import annotations

import importlib
import unittest
from dataclasses import FrozenInstanceError


class RecordContractsTest(unittest.TestCase):
    def _load_record_contracts(self):
        try:
            return importlib.import_module("peap_core.record_contracts")
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised during RED step
            self.fail(f"peap_core.record_contracts is missing: {exc}")

    def _make_page_parse_result(self):
        page_contracts = importlib.import_module("peap_core.page_parse_contracts")
        source_match = page_contracts.SourceMatch(
            source_id="sse",
            page_kind="detail",
            confidence=0.95,
            status="matched",
            reasons=("matched source markers",),
            classifier_version="classifier/v1",
        )
        evidence = page_contracts.EvidenceRef(
            snapshot_id="snap-001",
            source_kind="dom",
            locator="#project-code",
            excerpt="项目编号 P001",
            transform_ids=("trim",),
            confidence=1.0,
        )
        diagnostic = page_contracts.Diagnostic(
            severity="info",
            type="parse_ok",
            message="detail page parsed",
            stage="parse",
            evidence_refs=(evidence,),
            recoverability="recoverable",
        )
        return page_contracts.PageParseResult(
            snapshot_id="snap-001",
            source_match=source_match,
            parser_family_id="sse-family",
            parser_family_version="family/v1",
            variant_id="detail-html",
            variant_version="variant/v1",
            page_identity={
                "page_kind": "detail",
                "project_code": "P001",
                "project_id": "ID-001",
                "page_url": "https://example.invalid/detail/1",
                "listing_date": "2026-03-30",
                "candidate_tokens": ("P001",),
            },
            facts=(
                {"field": "project_name", "value": "示例项目"},
            ),
            outgoing_refs=(),
            diagnostics=(diagnostic,),
            provenance=(evidence,),
            recoverability="recoverable",
        )

    def test_assembled_record_candidate_supports_all_completion_states(self) -> None:
        contracts = self._load_record_contracts()
        peap_core = importlib.import_module("peap_core")
        page_result = self._make_page_parse_result()

        observed_states: list[str] = []
        for state in ("partial", "sufficient", "conflicted", "blocked"):
            candidate = contracts.AssembledRecordCandidate(
                assembly_id=f"assembly-{state}",
                source_ids=("sse",),
                page_results=(page_result,),
                entity_keys=("P001",),
                completion_state=state,
                missing_requirements=("detail_page",) if state == "partial" else (),
                assembly_diagnostics=(
                    {"type": "assembly_conflict", "message": "identity conflict"},
                )
                if state == "conflicted"
                else (),
                raw_business_object={"project_code": "P001", "project_name": "示例项目"},
            )
            observed_states.append(candidate.to_dict()["completion_state"])

        self.assertEqual(observed_states, ["partial", "sufficient", "conflicted", "blocked"])
        self.assertEqual(peap_core.AssembledRecordCandidate, contracts.AssembledRecordCandidate)

    def test_canonical_record_is_frozen_and_serializes_core_fields(self) -> None:
        contracts = self._load_record_contracts()
        canonical = contracts.CanonicalRecord(
            record_id="record-001",
            record_family="listing",
            source_identity={
                "source_id": "sse",
                "snapshot_ids": ("snap-001",),
                "page_urls": ("https://example.invalid/detail/1",),
            },
            business_identity={
                "project_code": "P001",
                "project_name": "示例项目",
            },
            canonical_fields={
                "project_code": "P001",
                "project_name": "示例项目",
                "status": "listed",
            },
            field_provenance={
                "project_code": (
                    {"snapshot_id": "snap-001", "locator": "#project-code"},
                ),
            },
            diagnostics=(
                {"type": "normalize_ok", "stage": "normalize"},
            ),
            normalizer_version="normalizer/v1",
            policy_state={"applied_patches": (), "conflicts": ()},
        )

        with self.assertRaises(FrozenInstanceError):
            canonical.record_id = "record-002"

        payload = canonical.to_dict()
        self.assertEqual(payload["record_id"], "record-001")
        self.assertEqual(payload["record_family"], "listing")
        self.assertEqual(payload["source_identity"]["snapshot_ids"], ["snap-001"])
        self.assertEqual(payload["canonical_fields"]["status"], "listed")
        self.assertEqual(payload["field_provenance"]["project_code"][0]["locator"], "#project-code")
        self.assertEqual(payload["normalizer_version"], "normalizer/v1")
        self.assertEqual(payload["policy_state"]["applied_patches"], [])


if __name__ == "__main__":
    unittest.main()
