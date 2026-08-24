from __future__ import annotations

import json
import unittest
from pathlib import Path

from peap_core.business_hint import build_business_hint, build_business_hint_from_scope
from peap_core.record_identity import (
    FAILED_RECORD_STATES,
    build_identity_anchor,
    build_source_identity_payload,
    is_failed_record_state,
    pick_reprocess_evidence_path,
    resolve_logical_record_identity,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class RecordIdentityTest(unittest.TestCase):
    def test_desktop_backend_record_identity_is_only_a_compatibility_wrapper(self) -> None:
        module_text = (REPO_ROOT / "desktop_backend" / "record_identity.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("from peap_core.record_identity import", module_text)
        self.assertNotIn("import hashlib", module_text)
        self.assertNotIn("def build_identity_anchor(", module_text)

    def test_failed_record_states_include_parse_and_postprocess_failures(self) -> None:
        self.assertIn("parse_failed", FAILED_RECORD_STATES)
        self.assertIn("postprocess_failed", FAILED_RECORD_STATES)
        self.assertTrue(is_failed_record_state("parse_failed"))
        self.assertFalse(is_failed_record_state("conflict"))

    def test_identity_anchor_does_not_depend_on_current_source_file_path(self) -> None:
        identity_a = build_source_identity_payload(
            record_family="listing",
            source_file="/tmp/current/a.html",
            source_url="https://example.test/item/1",
            project_code="CODE-1",
            project_name="示例项目",
            exchange="shanghai",
            listing_date="2026-03-21",
            candidate_tokens=["project_code:CODE-1"],
        )
        identity_b = build_source_identity_payload(
            record_family="listing",
            source_file="/tmp/elsewhere/b.html",
            source_url="https://example.test/item/1",
            project_code="CODE-1",
            project_name="示例项目",
            exchange="shanghai",
            listing_date="2026-03-21",
            candidate_tokens=["project_code:CODE-1"],
        )

        self.assertEqual(identity_a["original_source_file"], "/tmp/current/a.html")
        self.assertEqual(identity_b["original_source_file"], "/tmp/elsewhere/b.html")
        self.assertEqual(build_identity_anchor(record_state="parse_failed", source_identity=identity_a), build_identity_anchor(record_state="parse_failed", source_identity=identity_b))

    def test_logical_identity_is_stable_across_ready_failed_and_reprocess_shapes(self) -> None:
        ready = {
            "record_family": "deal",
            "business_id": "deal_equity_transfer",
            "exchange": "sse",
            "project_code": "PRJ-001",
            "state": "ready",
            "parser_outcome": "parsed",
            "ui_tab": "Ready",
            "source_file": "/tmp/current/ready.html",
            "archive_path": "/tmp/archive/ready.html",
            "cache_key": "cache-ready",
            "run_id": "run-ready",
            "local_file_mtime": "100",
        }
        failed = {
            "record_family": "deal",
            "business_id": "deal_equity_transfer",
            "exchange": "sse",
            "project_code": "PRJ-001",
            "state": "parse_failed",
            "postprocess_outcome": "failed",
            "label": "Needs Review",
            "source_file": "/tmp/current/failed.html",
            "archive_path": "/tmp/archive/failed.html",
            "cache_key": "cache-failed",
            "run_id": "run-failed",
            "local_file_mtime": "200",
        }
        reprocess = {
            "record_family": "deal",
            "business_id": "deal_equity_transfer",
            "source_identity_json": {
                "source_id": "sse",
                "project_code": "PRJ-001",
                "original_source_file": "/tmp/original/reprocess.html",
            },
            "record_state": "postprocess_failed",
            "frontend_scope": "review",
            "source_file": "/tmp/current/reprocess.html",
            "archive_path": "/tmp/archive/reprocess.html",
            "cache_key": "cache-reprocess",
            "run_id": "run-reprocess",
            "local_file_mtime": "300",
        }

        identities = [
            resolve_logical_record_identity(record)
            for record in (ready, failed, reprocess)
        ]

        self.assertEqual(
            [identity.identity_confidence for identity in identities],
            ["verified", "verified", "verified"],
        )
        self.assertEqual(
            len({identity.logical_record_identity for identity in identities}),
            1,
        )

    def test_logical_identity_does_not_depend_on_replaced_source_file(self) -> None:
        original = resolve_logical_record_identity(
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "cbex",
                "project_code": "LST-001",
                "source_file": "/tmp/source/a.html",
            }
        )
        replaced = resolve_logical_record_identity(
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "cbex",
                "project_code": "LST-001",
                "source_file": "/tmp/source/b.html",
            }
        )

        self.assertEqual(original.identity_confidence, "verified")
        self.assertEqual(original.logical_record_identity, replaced.logical_record_identity)

    def test_missing_project_code_returns_unresolved_logical_identity(self) -> None:
        identity = resolve_logical_record_identity(
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "cbex",
                "source_file": "/tmp/source/a.html",
            }
        )

        self.assertEqual(identity.identity_confidence, "unresolved")

    def test_logical_identity_rejects_explicit_non_mapping_record(self) -> None:
        with self.assertRaisesRegex(TypeError, "record"):
            resolve_logical_record_identity(
                [
                    ("record_family", "listing"),
                    ("business_id", "equity_transfer"),
                    ("exchange", "cbex"),
                    ("project_code", "LST-001"),
                ]
            )

    def test_source_identity_candidate_tokens_rejects_text_containers(self) -> None:
        for candidate_tokens in ("project_code:CODE-1", b"project_code:CODE-1"):
            with self.subTest(candidate_tokens=type(candidate_tokens).__name__):
                with self.assertRaises(TypeError):
                    build_source_identity_payload(
                        record_family="listing",
                        source_file="/tmp/source/a.html",
                        candidate_tokens=candidate_tokens,
                    )

    def test_source_identity_candidate_tokens_rejects_non_text_elements(self) -> None:
        with self.assertRaises(TypeError):
            build_source_identity_payload(
                record_family="listing",
                source_file="/tmp/source/a.html",
                candidate_tokens=["project_code:CODE-1", 123],
            )

    def test_invalid_source_identity_json_is_not_silently_downgraded_to_unresolved(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            resolve_logical_record_identity(
                {
                    "source_identity_json": "{",
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "cbex",
                    "project_code": "LST-001",
                }
            )

    def test_non_object_source_identity_json_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            resolve_logical_record_identity(
                {
                    "source_identity_json": "[]",
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "cbex",
                    "project_code": "LST-001",
                }
            )

    def test_non_object_source_identity_json_does_not_fallback_to_source_identity(self) -> None:
        with self.assertRaises(TypeError):
            resolve_logical_record_identity(
                {
                    "source_identity_json": "[]",
                    "source_identity": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "exchange": "cbex",
                        "project_code": "LST-001",
                    },
                }
            )

    def test_falsey_non_text_source_identity_json_does_not_fallback_to_source_identity(self) -> None:
        for source_identity_json in (0, False):
            with self.subTest(source_identity_json=source_identity_json):
                with self.assertRaises(TypeError):
                    resolve_logical_record_identity(
                        {
                            "source_identity_json": source_identity_json,
                            "source_identity": {
                                "record_family": "listing",
                                "business_id": "equity_transfer",
                                "exchange": "cbex",
                                "project_code": "LST-001",
                            },
                        }
                    )

    def test_logical_identity_rejects_explicit_non_text_components(self) -> None:
        component_records = {
            "record_family": {
                "record_family": 123,
                "business_id": "equity_transfer",
                "exchange": "cbex",
                "project_code": "LST-001",
            },
            "business_id": {
                "record_family": "listing",
                "business_id": 123,
                "exchange": "cbex",
                "project_code": "LST-001",
            },
            "exchange": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": 123,
                "project_code": "LST-001",
            },
            "project_code": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "cbex",
                "project_code": 123,
            },
        }
        for field_name, record in component_records.items():
            with self.subTest(field_name=field_name):
                with self.assertRaises(TypeError):
                    resolve_logical_record_identity(record)

    def test_legacy_source_sha1_anchor_is_not_verified_logical_identity(self) -> None:
        identity = resolve_logical_record_identity(
            {
                "record_family": "listing",
                "business_id": "source:0123456789abcdef0123456789abcdef01234567",
                "exchange": "cbex",
                "project_code": "LST-001",
            }
        )

        self.assertEqual(identity.identity_confidence, "unresolved")

    def test_path_hash_cannot_drive_verified_logical_identity(self) -> None:
        identity = resolve_logical_record_identity(
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "cbex",
                "source_path_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            }
        )

        self.assertEqual(identity.identity_confidence, "unresolved")

    def test_pick_reprocess_evidence_path_prefers_original_evidence_path(self) -> None:
        record = {
            "original_evidence_path": "/tmp/record/original-evidence.html",
            "source_file": "/tmp/current/path.html",
            "source_identity": {
                "original_evidence_path": "/tmp/original/evidence.html",
                "original_source_file": "/tmp/source/original-source.html",
            },
        }

        self.assertEqual(pick_reprocess_evidence_path(record), "/tmp/original/evidence.html")

    def test_pick_reprocess_evidence_path_surfaces_invalid_source_identity_json(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            pick_reprocess_evidence_path(
                {
                    "source_identity": "{",
                    "original_evidence_path": "/tmp/record/original-evidence.html",
                }
            )

    def test_pick_reprocess_evidence_path_rejects_non_object_source_identity(self) -> None:
        for source_identity in ("[]", []):
            with self.subTest(source_identity=type(source_identity).__name__):
                with self.assertRaises(TypeError):
                    pick_reprocess_evidence_path(
                        {
                            "source_identity": source_identity,
                            "original_evidence_path": "/tmp/record/original-evidence.html",
                        }
                    )

    def test_build_source_identity_payload_rejects_unknown_record_family(self) -> None:
        with self.assertRaises(ValueError):
            build_source_identity_payload(
                record_family="unknown_family",
                source_file="/tmp/source/a.html",
                source_url="https://example.test/item/1",
                project_code="CODE-1",
                project_name="示例项目",
                exchange="shanghai",
                listing_date="2026-03-21",
            )

    def test_identity_anchor_rejects_missing_record_family_in_source_identity(self) -> None:
        with self.assertRaises(ValueError):
            build_identity_anchor(
                record_state="parse_failed",
                source_identity={
                    "record_family": "",
                    "source_url": "https://example.test/item/1",
                    "project_code": "CODE-1",
                    "project_name": "示例项目",
                    "exchange": "shanghai",
                    "listing_date": "2026-03-21",
                },
            )

    def test_identity_anchor_rejects_non_object_source_identity(self) -> None:
        for source_identity in (None, [], ""):
            with self.subTest(source_identity=type(source_identity).__name__):
                with self.assertRaisesRegex(TypeError, "source_identity"):
                    build_identity_anchor(
                        record_state="parse_failed",
                        source_identity=source_identity,
                    )

    def test_identity_anchor_remains_state_sensitive_for_legacy_failed_records(self) -> None:
        source_identity = build_source_identity_payload(
            record_family="listing",
            source_file="/tmp/current/a.html",
            source_url="https://example.test/item/1",
            project_code="CODE-1",
            project_name="Example Project",
            exchange="shanghai",
            listing_date="2026-03-21",
        )

        self.assertNotEqual(
            build_identity_anchor(
                record_state="parse_failed",
                source_identity=source_identity,
            ),
            build_identity_anchor(
                record_state="postprocess_failed",
                source_identity=source_identity,
            ),
        )

    def test_build_business_hint_rejects_unknown_explicit_record_family(self) -> None:
        with self.assertRaises(ValueError):
            build_business_hint(record_family="unknown_family", business_id="equity_transfer")

    def test_build_business_hint_rejects_unknown_explicit_business_id(self) -> None:
        with self.assertRaises(ValueError):
            build_business_hint(record_family="listing", business_id="unknown_business")

    def test_build_business_hint_keeps_empty_and_all_business_id_as_no_hint(self) -> None:
        self.assertEqual(build_business_hint(record_family="unknown_family", business_id=""), {})
        self.assertEqual(build_business_hint(record_family="unknown_family", business_id="all"), {})

    def test_business_hint_from_scope_rejects_explicit_non_mapping_scope(self) -> None:
        with self.assertRaisesRegex(TypeError, "scope"):
            build_business_hint_from_scope(
                [
                    ("record_family", "listing"),
                    ("business_id", "equity_transfer"),
                ]
            )


if __name__ == "__main__":
    unittest.main()
