from __future__ import annotations

import importlib
import unittest
from dataclasses import FrozenInstanceError


class PageParseContractsTest(unittest.TestCase):
    def _load_page_parse_contracts(self):
        try:
            return importlib.import_module("peap_core.page_parse_contracts")
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised during RED step
            self.fail(f"peap_core.page_parse_contracts is missing: {exc}")

    def test_page_parse_result_serializes_typed_contract_shape(self) -> None:
        contracts = self._load_page_parse_contracts()
        peap_core = importlib.import_module("peap_core")

        evidence = contracts.EvidenceRef(
            snapshot_id="snap-001",
            source_kind="dom",
            locator="#project-code",
            excerpt="项目编号: P001",
            transform_ids=("trim", "normalize-space"),
            confidence=0.98,
        )
        diagnostic = contracts.Diagnostic(
            severity="warn",
            type="parse_partial",
            message="缺少挂牌截止日期",
            stage="parse",
            evidence_refs=(evidence,),
            recoverability="partial",
        )
        source_match = contracts.SourceMatch(
            source_id="sse",
            page_kind="detail",
            confidence=0.91,
            status="matched",
            reasons=("matched meta keywords",),
            classifier_version="classifier/v1",
        )
        result = contracts.PageParseResult(
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
                "candidate_tokens": ("P001", "示例项目"),
            },
            facts=(
                {"field": "project_name", "value": "示例项目"},
            ),
            outgoing_refs=(
                {
                    "target_kind": "announcement",
                    "target_url": "https://example.invalid/notice/1",
                    "ref_reason": "notice link",
                    "correlation_hints": ("P001",),
                },
            ),
            diagnostics=(diagnostic,),
            provenance=(evidence,),
            recoverability="partial",
        )

        with self.assertRaises(FrozenInstanceError):
            result.parser_family_id = "other-family"

        payload = result.to_dict()
        self.assertEqual(payload["snapshot_id"], "snap-001")
        self.assertEqual(payload["source_match"]["status"], "matched")
        self.assertEqual(payload["source_match"]["reasons"], ["matched meta keywords"])
        self.assertEqual(payload["diagnostics"][0]["type"], "parse_partial")
        self.assertEqual(payload["diagnostics"][0]["evidence_refs"][0]["locator"], "#project-code")
        self.assertEqual(payload["page_identity"]["project_code"], "P001")
        self.assertEqual(payload["page_identity"]["candidate_tokens"], ["P001", "示例项目"])
        self.assertEqual(payload["outgoing_refs"][0]["target_kind"], "announcement")
        self.assertEqual(payload["outgoing_refs"][0]["correlation_hints"], ["P001"])
        self.assertEqual(payload["recoverability"], "partial")
        self.assertEqual(peap_core.SourceMatch, contracts.SourceMatch)
        self.assertEqual(peap_core.PageParseResult, contracts.PageParseResult)

    def test_source_match_unknown_and_diagnostic_unrecoverable_states_are_representable(self) -> None:
        contracts = self._load_page_parse_contracts()

        source_match = contracts.SourceMatch(
            source_id="",
            page_kind="listing",
            confidence=0.0,
            status="unknown",
            reasons=("no source markers matched",),
            classifier_version="classifier/v1",
        )
        diagnostic = contracts.Diagnostic(
            severity="error",
            type="parse_unrecoverable",
            message="页面缺少必要身份字段",
            stage="parse",
            evidence_refs=(),
            recoverability="unrecoverable",
        )

        self.assertEqual(source_match.to_dict()["status"], "unknown")
        self.assertEqual(diagnostic.to_dict()["recoverability"], "unrecoverable")
        self.assertEqual(diagnostic.to_dict()["stage"], "parse")


if __name__ == "__main__":
    unittest.main()
