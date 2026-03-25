from __future__ import annotations

import unittest

from desktop_backend.record_identity import (
    FAILED_RECORD_STATES,
    build_identity_anchor,
    build_source_identity_payload,
    is_failed_record_state,
    pick_reprocess_evidence_path,
)


class RecordIdentityTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
