from __future__ import annotations

import os
import tempfile
import unittest

from peap.streaming_models import IngestedRecord, PostProcessFinding
from peap.streaming_store import StreamingStore
from peap.streaming_store_maintenance import run_streaming_store_maintenance


class StreamingStoreMaintenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming.sqlite3")

    def _audit_actions(self) -> list[str]:
        with self.store._connect() as conn:
            rows = conn.execute("SELECT action FROM audit_log ORDER BY audit_id").fetchall()
        return [str(row["action"] or "") for row in rows]

    def test_run_streaming_store_maintenance_normalizes_legacy_state_and_writes_audits(self) -> None:
        failed_source_file = os.path.join(self.temp_dir.name, "legacy-skip-parse.html")
        with open(failed_source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy skip parse</body></html>")

        self.store.upsert_failed_record(
            project_code="FAILED-SKIP-001",
            source_file=failed_source_file,
            state="parse_failed",
            error_type="skip_parse",
            error_message="skip-cbex-otc-page",
            payload={"项目编号": "FAILED-SKIP-001"},
        )

        ready_source_file = os.path.join(self.temp_dir.name, "legacy-ready.html")
        with open(ready_source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy ready</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-ready",
                revision_hash="hash-maintenance-ready",
                project_code="G32026SH1999004",
                project_name="历史未知类型项目",
                project_type="",
                exchange="shanghai",
                listing_date="2026/03/21",
                state="ready",
                source_file=ready_source_file,
                archive_path=ready_source_file,
                parser_payload={"项目编号": "G32026SH1999004", "项目名称": "历史未知类型项目"},
                postprocess_payload={"项目编号": "G32026SH1999004", "项目名称": "历史未知类型项目"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="project_type_unknown",
                        message="项目类型无法识别",
                    )
                ],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET listing_date = '2026/03/21' WHERE record_id = ?",
                ("rec-maintenance-ready",),
            )

        summary = run_streaming_store_maintenance(self.store)

        self.assertEqual(summary.skip_parse["records"], 1)
        self.assertEqual(summary.listing_dates, 1)
        self.assertEqual(summary.required_mapping["records"], 1)
        pending_rows = self.store.iter_latest_records(states=["pending_mapping"])
        skipped_rows = self.store.iter_latest_records(states=["skipped"])
        self.assertEqual(len(pending_rows), 1)
        self.assertEqual(pending_rows[0]["listing_date"], "2026-03-21")
        self.assertEqual(len(skipped_rows), 1)
        self.assertIn("legacy_skip_parse_normalized", self._audit_actions())
        self.assertIn("legacy_listing_dates_normalized", self._audit_actions())
        self.assertIn("legacy_required_mapping_normalized", self._audit_actions())

    def test_run_streaming_store_maintenance_is_idempotent(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "idempotent.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>idempotent</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-maintenance-idempotent",
                revision_hash="hash-maintenance-idempotent",
                project_code="G32026SH1999005",
                project_name="历史未知类型项目",
                project_type="",
                exchange="shanghai",
                listing_date="2026/03/21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SH1999005", "项目名称": "历史未知类型项目"},
                postprocess_payload={"项目编号": "G32026SH1999005", "项目名称": "历史未知类型项目"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="project_type_unknown",
                        message="项目类型无法识别",
                    )
                ],
            )
        )

        first = run_streaming_store_maintenance(self.store)
        first_audits = list(self._audit_actions())
        second = run_streaming_store_maintenance(self.store)

        self.assertEqual(first.required_mapping["records"], 1)
        self.assertEqual(second.skip_parse["records"], 0)
        self.assertEqual(second.listing_dates, 0)
        self.assertEqual(second.required_mapping["records"], 0)
        self.assertEqual(self._audit_actions(), first_audits)


if __name__ == "__main__":
    unittest.main()
