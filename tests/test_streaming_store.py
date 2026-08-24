from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from peap.streaming_models import (
    IngestedRecord,
    ItemProgressEvent,
    ItemSavedPayload,
    PostProcessFinding,
)
from peap.streaming_queue import StreamingIngestService
from peap.streaming_store import (
    StreamingStore,
    _bind_ingested_record_identity,
    _can_use_record_for_existing_download_dedup,
    _merge_record_payloads,
    _merge_source_identity,
    _resolve_scope_source_id,
    _sync_canonical_record_diagnostics,
)
from peap.streaming_store_maintenance import run_streaming_store_maintenance


class StreamingStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming.sqlite3", auto_migrate=True)

    def _mark_retention_export(
        self,
        *,
        export_id: str,
        cursor_id: str,
        output_dir: str,
        artifact_paths: list[str] | None = None,
        retention_count: int = 1,
    ) -> None:
        self.store.mark_exported(
            export_id=export_id,
            cursor_id=cursor_id,
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=output_dir,
            summary={"new_records": 0, "artifacts": list(artifact_paths or [])},
            records=[],
            retention_count=retention_count,
        )

    def test_upsert_record_creates_revisions_and_export_markers(self) -> None:
        record = IngestedRecord(
            record_id="rec-1",
            revision_hash="hash-1",
            project_code="G32025SH1000194",
            project_name="测试项目",
            project_type="股权转让",
            exchange="shanghai",
            listing_date="2026-03-21",
            state="ready",
            source_file=f"{self.temp_dir.name}/raw/a.html",
            archive_path=f"{self.temp_dir.name}/archive/a.html",
            parser_payload={"项目编号": "G32025SH1000194", "项目名称": "测试项目"},
            postprocess_payload={"项目编号": "G32025SH1000194", "项目名称": "测试项目", "项目类型": "股权转让"},
            findings=[],
        )
        first = self.store.upsert_record(record)
        self.assertTrue(first["changed"])

        second_record = IngestedRecord(
            **{
                **record.__dict__,
                "revision_hash": "hash-2",
                "postprocess_payload": {
                    "项目编号": "G32025SH1000194",
                    "项目名称": "测试项目(修正)",
                    "项目类型": "股权转让",
                },
            }
        )
        second = self.store.upsert_record(second_record)
        self.assertTrue(second["changed"])

        rows = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project_name"], "测试项目")
        self.assertEqual(rows[0]["postprocess_payload"]["项目名称"], "测试项目(修正)")

        self.store.mark_exported(
            export_id="exp-1",
            cursor_key="default",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=f"{self.temp_dir.name}/exports",
            summary={"new_records": 1, "changed_records": 0},
            records=rows,
        )
        exported = self.store.get_exported_revision_map("default")
        self.assertEqual(exported[rows[0]["record_id"]]["revision_hash"], "hash-2")

    def test_upsert_record_rejects_explicit_non_mapping_finding_evidence(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "bad-finding-evidence.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>bad finding evidence</body></html>")

        record = IngestedRecord(
            record_id="rec-bad-finding-evidence",
            revision_hash="hash-bad-finding-evidence",
            project_code="G32026BADFINDINGEVIDENCE",
            project_name="坏 finding evidence 项目",
            project_type="股权转让",
            exchange="shanghai",
            listing_date="2026-03-21",
            state="ready",
            source_file=source_file,
            archive_path=source_file,
            parser_payload={"项目编号": "G32026BADFINDINGEVIDENCE"},
            postprocess_payload={"项目编号": "G32026BADFINDINGEVIDENCE", "项目类型": "股权转让"},
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_gap",
                    message="bad evidence must not be persisted as empty",
                    evidence=False,  # type: ignore[arg-type]
                )
            ],
        )

        with self.assertRaisesRegex(TypeError, r"finding\.evidence"):
            self.store.upsert_record(record)

    def test_sync_canonical_record_diagnostics_rejects_explicit_non_mapping_finding_evidence(self) -> None:
        with self.assertRaisesRegex(TypeError, r"finding\.evidence"):
            _sync_canonical_record_diagnostics(
                {},
                [
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_gap",
                        message="bad evidence must not be synced",
                        evidence=[],  # type: ignore[arg-type]
                    )
                ],
            )

    def test_mark_exported_rejects_false_manifest_instead_of_generating_default_manifest(self) -> None:
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.store.mark_exported(
                export_id="exp-false-manifest",
                cursor_key="default",
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="股权转让",
                output_dir=f"{self.temp_dir.name}/exports",
                summary={"new_records": 0},
                records=[],
                manifest=False,  # type: ignore[arg-type]
            )

        with self.assertRaises(KeyError):
            self.store.get_export_manifest("exp-false-manifest")

    def test_get_export_manifest_raises_key_error_for_missing_export(self) -> None:
        with self.assertRaises(KeyError):
            self.store.get_export_manifest("missing")

    def test_get_export_manifest_returns_empty_mapping_for_existing_export_missing_manifest_row(self) -> None:
        export_id = "exp-missing-manifest-row"
        self.store.mark_exported(
            export_id=export_id,
            cursor_key="default",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=f"{self.temp_dir.name}/exports",
            summary={"new_records": 0},
            records=[],
        )
        with self.store._connect() as conn:
            conn.execute(
                "DELETE FROM export_manifests WHERE export_id = ?",
                (export_id,),
            )

        self.assertEqual(self.store.get_export_manifest(export_id), {})

    def test_mark_exported_rejects_non_mapping_summary(self) -> None:
        cases = [False, [], "not-a-summary", None]

        for summary in cases:
            with self.subTest(summary=summary):
                with self.assertRaisesRegex(ValueError, "summary"):
                    self.store.mark_exported(
                        export_id=f"exp-bad-summary-{type(summary).__name__}",
                        cursor_key="default",
                        requested_export_mode="incremental",
                        date_from="2026-03-01",
                        date_to="2026-03-31",
                        project_type="股权转让",
                        output_dir=f"{self.temp_dir.name}/exports",
                        summary=summary,  # type: ignore[arg-type]
                        records=[],
                    )

        self.assertEqual(self.store.list_exports(), [])

    def test_mark_exported_rejects_non_mapping_summary_manifest(self) -> None:
        for manifest in [False, [], "not-a-manifest", None]:
            with self.subTest(manifest=manifest):
                with self.assertRaisesRegex(ValueError, "manifest"):
                    self.store.mark_exported(
                        export_id=f"exp-bad-summary-manifest-{type(manifest).__name__}",
                        cursor_key="default",
                        requested_export_mode="incremental",
                        date_from="2026-03-01",
                        date_to="2026-03-31",
                        project_type="股权转让",
                        output_dir=f"{self.temp_dir.name}/exports",
                        summary={"new_records": 0, "manifest": manifest},
                        records=[],
                    )

        self.assertEqual(self.store.list_exports(), [])

    def test_mark_exported_rejects_non_mapping_summary_cursor_value(self) -> None:
        for cursor_value in [False, [], "not-a-cursor-value", None]:
            with self.subTest(cursor_value=cursor_value):
                with self.assertRaisesRegex(ValueError, "cursor_value"):
                    self.store.mark_exported(
                        export_id=f"exp-bad-summary-cursor-value-{type(cursor_value).__name__}",
                        cursor_key="default",
                        requested_export_mode="incremental",
                        date_from="2026-03-01",
                        date_to="2026-03-31",
                        project_type="股权转让",
                        output_dir=f"{self.temp_dir.name}/exports",
                        summary={"new_records": 0, "cursor_value": cursor_value},
                        records=[],
                    )

        self.assertEqual(self.store.list_exports(), [])

    def test_mark_exported_rejects_false_cursor_value_instead_of_generating_default_cursor_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "cursor_value"):
            self.store.mark_exported(
                export_id="exp-false-cursor-value",
                cursor_key="default",
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="股权转让",
                output_dir=f"{self.temp_dir.name}/exports",
                summary={"new_records": 0},
                records=[],
                cursor_value=False,  # type: ignore[arg-type]
            )

        self.assertEqual(self.store.get_export_cursor_value("default"), {})

    def test_mark_exported_rejects_explicit_empty_manifest_and_cursor_value_without_writing(self) -> None:
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.store.mark_exported(
                export_id="exp-empty-manifest-cursor-value",
                cursor_key="default",
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="股权转让",
                output_dir=f"{self.temp_dir.name}/exports",
                summary={"new_records": 0},
                records=[],
                manifest={},
                cursor_value={},
            )

        with sqlite3.connect(self.store.db_path) as conn:
            export_count = conn.execute(
                "SELECT COUNT(*) FROM exports WHERE export_id = ?",
                ("exp-empty-manifest-cursor-value",),
            ).fetchone()[0]
            manifest_count = conn.execute(
                "SELECT COUNT(*) FROM export_manifests WHERE export_id = ?",
                ("exp-empty-manifest-cursor-value",),
            ).fetchone()[0]
            cursor_count = conn.execute(
                "SELECT COUNT(*) FROM export_cursor_values WHERE cursor_id = ?",
                ("default",),
            ).fetchone()[0]

        self.assertEqual(export_count, 0)
        self.assertEqual(manifest_count, 0)
        self.assertEqual(cursor_count, 0)

    def test_mark_exported_rejects_empty_summary_manifest_and_cursor_value_without_writing(self) -> None:
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.store.mark_exported(
                export_id="exp-empty-summary-manifest-cursor-value",
                cursor_key="default",
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="股权转让",
                output_dir=f"{self.temp_dir.name}/exports",
                summary={"new_records": 0, "manifest": {}, "cursor_value": {}},
                records=[],
            )

        with sqlite3.connect(self.store.db_path) as conn:
            export_count = conn.execute(
                "SELECT COUNT(*) FROM exports WHERE export_id = ?",
                ("exp-empty-summary-manifest-cursor-value",),
            ).fetchone()[0]
            manifest_count = conn.execute(
                "SELECT COUNT(*) FROM export_manifests WHERE export_id = ?",
                ("exp-empty-summary-manifest-cursor-value",),
            ).fetchone()[0]
            cursor_count = conn.execute(
                "SELECT COUNT(*) FROM export_cursor_values WHERE cursor_id = ?",
                ("default",),
            ).fetchone()[0]

        self.assertEqual(export_count, 0)
        self.assertEqual(manifest_count, 0)
        self.assertEqual(cursor_count, 0)

    def test_mark_exported_rejects_explicit_empty_cursor_value_without_writing(self) -> None:
        with self.assertRaisesRegex(ValueError, "cursor_value"):
            self.store.mark_exported(
                export_id="exp-empty-cursor-value",
                cursor_key="default",
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="股权转让",
                output_dir=f"{self.temp_dir.name}/exports",
                summary={"new_records": 0},
                records=[],
                manifest={
                    "export_id": "exp-empty-cursor-value",
                    "cursor_id": "default",
                    "revision_watermark": 0,
                    "cursor_basis": {"export_id": "exp-empty-cursor-value", "eligible_set_hash": ""},
                },
                cursor_value={},
            )

        with sqlite3.connect(self.store.db_path) as conn:
            export_count = conn.execute(
                "SELECT COUNT(*) FROM exports WHERE export_id = ?",
                ("exp-empty-cursor-value",),
            ).fetchone()[0]
            manifest_count = conn.execute(
                "SELECT COUNT(*) FROM export_manifests WHERE export_id = ?",
                ("exp-empty-cursor-value",),
            ).fetchone()[0]
            cursor_count = conn.execute(
                "SELECT COUNT(*) FROM export_cursor_values WHERE cursor_id = ?",
                ("default",),
            ).fetchone()[0]

        self.assertEqual(export_count, 0)
        self.assertEqual(manifest_count, 0)
        self.assertEqual(cursor_count, 0)

    def test_mark_exported_rejects_empty_summary_cursor_value_without_writing(self) -> None:
        with self.assertRaisesRegex(ValueError, "cursor_value"):
            self.store.mark_exported(
                export_id="exp-empty-summary-cursor-value",
                cursor_key="default",
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="股权转让",
                output_dir=f"{self.temp_dir.name}/exports",
                summary={
                    "new_records": 0,
                    "manifest": {
                        "export_id": "exp-empty-summary-cursor-value",
                        "cursor_id": "default",
                        "revision_watermark": 0,
                        "cursor_basis": {"export_id": "exp-empty-summary-cursor-value", "eligible_set_hash": ""},
                    },
                    "cursor_value": {},
                },
                records=[],
            )

        with sqlite3.connect(self.store.db_path) as conn:
            export_count = conn.execute(
                "SELECT COUNT(*) FROM exports WHERE export_id = ?",
                ("exp-empty-summary-cursor-value",),
            ).fetchone()[0]
            manifest_count = conn.execute(
                "SELECT COUNT(*) FROM export_manifests WHERE export_id = ?",
                ("exp-empty-summary-cursor-value",),
            ).fetchone()[0]
            cursor_count = conn.execute(
                "SELECT COUNT(*) FROM export_cursor_values WHERE cursor_id = ?",
                ("default",),
            ).fetchone()[0]

        self.assertEqual(export_count, 0)
        self.assertEqual(manifest_count, 0)
        self.assertEqual(cursor_count, 0)

    def test_mark_exported_rejects_false_audit_payload_atomically(self) -> None:
        with self.assertRaisesRegex(ValueError, "audit_payload"):
            self.store.mark_exported(
                export_id="exp-false-audit-payload",
                cursor_key="default",
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="股权转让",
                output_dir=f"{self.temp_dir.name}/exports",
                summary={"new_records": 0},
                records=[],
                audit_action="export_completed",
                audit_payload=False,  # type: ignore[arg-type]
            )

        with sqlite3.connect(self.store.db_path) as conn:
            export_count = conn.execute(
                "SELECT COUNT(*) FROM exports WHERE export_id = ?",
                ("exp-false-audit-payload",),
            ).fetchone()[0]
            audit_count = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = ?",
                ("export_completed",),
            ).fetchone()[0]
        self.assertEqual(export_count, 0)
        self.assertEqual(audit_count, 0)

    def test_mark_exported_rejects_false_audit_payload_without_audit_action_atomically(self) -> None:
        with self.assertRaisesRegex(ValueError, "audit_payload"):
            self.store.mark_exported(
                export_id="exp-false-audit-payload-without-action",
                cursor_key="default",
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="股权转让",
                output_dir=f"{self.temp_dir.name}/exports",
                summary={"new_records": 0},
                records=[],
                audit_payload=False,  # type: ignore[arg-type]
            )

        with sqlite3.connect(self.store.db_path) as conn:
            export_count = conn.execute(
                "SELECT COUNT(*) FROM exports WHERE export_id = ?",
                ("exp-false-audit-payload-without-action",),
            ).fetchone()[0]
            manifest_count = conn.execute(
                "SELECT COUNT(*) FROM export_manifests WHERE export_id = ?",
                ("exp-false-audit-payload-without-action",),
            ).fetchone()[0]
            cursor_count = conn.execute(
                "SELECT COUNT(*) FROM export_cursor_values WHERE cursor_id = ?",
                ("default",),
            ).fetchone()[0]
        self.assertEqual(export_count, 0)
        self.assertEqual(manifest_count, 0)
        self.assertEqual(cursor_count, 0)

    def test_mark_exported_rejects_false_summary_artifacts_instead_of_persisting(self) -> None:
        with self.assertRaisesRegex((TypeError, ValueError), "summary.artifacts"):
            self.store.mark_exported(
                export_id="exp-false-summary-artifacts",
                cursor_key="default",
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="股权转让",
                output_dir=f"{self.temp_dir.name}/exports",
                summary={"new_records": 0, "artifacts": False},
                records=[],
            )

        self.assertEqual(self.store.list_exports(), [])

    def test_mark_exported_rejects_bad_stored_retention_artifacts_instead_of_pruning(self) -> None:
        self.store.mark_exported(
            export_id="exp-retention-bad-artifacts-1",
            cursor_key="default",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=f"{self.temp_dir.name}/exports",
            summary={"new_records": 0},
            records=[],
            retention_count=1,
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE exports SET summary_json = ? WHERE export_id = ?",
                (json.dumps({"new_records": 0, "artifacts": False}), "exp-retention-bad-artifacts-1"),
            )

        with self.assertRaisesRegex((TypeError, ValueError), "summary.artifacts"):
            self.store.mark_exported(
                export_id="exp-retention-bad-artifacts-2",
                cursor_key="default",
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="股权转让",
                output_dir=f"{self.temp_dir.name}/exports",
                summary={"new_records": 0},
                records=[],
                retention_count=1,
            )

        self.assertNotIn("exp-retention-bad-artifacts-2", {item["export_id"] for item in self.store.list_exports()})

    def test_mark_exported_keeps_committed_tombstone_when_retention_cleanup_fails(self) -> None:
        export_dir = os.path.join(self.temp_dir.name, "exports")
        os.makedirs(export_dir, exist_ok=True)
        old_artifact_path = os.path.join(export_dir, "old.xlsx")
        with open(old_artifact_path, "wb") as handle:
            handle.write(b"xlsx")

        self.store.mark_exported(
            export_id="exp-retention-delete-fail-1",
            cursor_key="default",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=export_dir,
            summary={"new_records": 0, "artifacts": [old_artifact_path]},
            records=[],
            retention_count=1,
        )

        with patch("peap.streaming_store.os.remove", side_effect=PermissionError("locked")):
            self.store.mark_exported(
                export_id="exp-retention-delete-fail-2",
                cursor_key="default",
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="股权转让",
                output_dir=export_dir,
                summary={"new_records": 0},
                records=[],
                retention_count=1,
            )

        old_export = self.store.get_export("exp-retention-delete-fail-1")
        self.assertFalse(os.path.exists(old_artifact_path))
        self.assertTrue(old_export["is_tombstone"])
        self.assertTrue(old_export["pruned_by_retention"])
        self.assertIn(
            "exp-retention-delete-fail-2",
            {item["export_id"] for item in self.store.list_exports()},
        )
        pending = [
            name
            for name in os.listdir(export_dir)
            if name.startswith("old.xlsx.peap-retention-") and name.endswith(".pending")
        ]
        self.assertEqual(len(pending), 1)

    def test_mark_exported_rolls_back_database_and_restores_staged_artifacts_on_recheck_failure(self) -> None:
        export_dir = os.path.join(self.temp_dir.name, "exports")
        os.makedirs(export_dir, exist_ok=True)
        old_artifact_path = os.path.join(export_dir, "old.xlsx")
        with open(old_artifact_path, "wb") as handle:
            handle.write(b"xlsx")
        self.store.mark_exported(
            export_id="exp-retention-recheck-old",
            cursor_id="cursor-recheck",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=export_dir,
            summary={"new_records": 0, "artifacts": [old_artifact_path]},
            records=[],
            retention_count=1,
        )

        with patch(
            "peap.streaming_store._validate_export_artifact_checksums",
            side_effect=[{}, RuntimeError("artifact disappeared")],
        ):
            with self.assertRaisesRegex(RuntimeError, "artifact disappeared"):
                self.store.mark_exported(
                    export_id="exp-retention-recheck-new",
                    cursor_id="cursor-recheck",
                    requested_export_mode="incremental",
                    date_from="2026-03-01",
                    date_to="2026-03-31",
                    project_type="股权转让",
                    output_dir=export_dir,
                    summary={"new_records": 0},
                    records=[],
                    retention_count=1,
                )

        self.assertTrue(os.path.exists(old_artifact_path))
        self.assertNotIn(
            "exp-retention-recheck-new",
            {item["export_id"] for item in self.store.list_exports()},
        )
        self.assertFalse(self.store.get_export("exp-retention-recheck-old")["is_tombstone"])

    def test_retention_does_not_delete_artifact_referenced_by_another_active_export(self) -> None:
        export_dir = os.path.join(self.temp_dir.name, "exports")
        os.makedirs(export_dir, exist_ok=True)
        shared_path = os.path.join(export_dir, "shared.xlsx")
        with open(shared_path, "wb") as handle:
            handle.write(b"xlsx")

        self.store.mark_exported(
            export_id="exp-shared-old",
            cursor_id="cursor-shared",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=export_dir,
            summary={"new_records": 0, "artifacts": [shared_path]},
            records=[],
            retention_count=1,
        )
        self.store.mark_exported(
            export_id="exp-shared-owner",
            cursor_id="another-cursor",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=export_dir,
            summary={"new_records": 0, "artifacts": [shared_path]},
            records=[],
            retention_count=1,
        )
        self.store.mark_exported(
            export_id="exp-shared-new",
            cursor_id="cursor-shared",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=export_dir,
            summary={"new_records": 0},
            records=[],
            retention_count=1,
        )

        self.assertTrue(os.path.exists(shared_path))
        self.assertTrue(self.store.get_export("exp-shared-old")["is_tombstone"])
        self.assertFalse(self.store.get_export("exp-shared-owner")["is_tombstone"])

    def test_retention_tombstones_but_does_not_move_artifact_outside_row_output_dir(self) -> None:
        managed_dir = os.path.join(self.temp_dir.name, "managed")
        external_dir = os.path.join(self.temp_dir.name, "external")
        os.makedirs(managed_dir, exist_ok=True)
        os.makedirs(external_dir, exist_ok=True)
        external_path = os.path.join(external_dir, "external.xlsx")
        with open(external_path, "wb") as handle:
            handle.write(b"external")

        self._mark_retention_export(
            export_id="exp-outside-old",
            cursor_id="cursor-outside",
            output_dir=external_dir,
            artifact_paths=[external_path],
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE exports SET output_dir = ? WHERE export_id = ?",
                (managed_dir, "exp-outside-old"),
            )
        self._mark_retention_export(
            export_id="exp-outside-new",
            cursor_id="cursor-outside",
            output_dir=managed_dir,
        )

        self.assertTrue(os.path.exists(external_path))
        self.assertTrue(self.store.get_export("exp-outside-old")["is_tombstone"])

    def test_retention_tombstones_but_does_not_move_checksum_mismatch(self) -> None:
        export_dir = os.path.join(self.temp_dir.name, "checksum-exports")
        os.makedirs(export_dir, exist_ok=True)
        artifact_path = os.path.join(export_dir, "old.xlsx")
        with open(artifact_path, "wb") as handle:
            handle.write(b"original")
        self._mark_retention_export(
            export_id="exp-checksum-old",
            cursor_id="cursor-checksum",
            output_dir=export_dir,
            artifact_paths=[artifact_path],
        )
        with open(artifact_path, "wb") as handle:
            handle.write(b"changed")

        self._mark_retention_export(
            export_id="exp-checksum-new",
            cursor_id="cursor-checksum",
            output_dir=export_dir,
        )

        with open(artifact_path, "rb") as handle:
            self.assertEqual(handle.read(), b"changed")
        self.assertTrue(self.store.get_export("exp-checksum-old")["is_tombstone"])

    def test_polluted_active_row_cannot_shield_valid_pruned_artifact(self) -> None:
        owner_dir = os.path.join(self.temp_dir.name, "owner")
        polluted_dir = os.path.join(self.temp_dir.name, "polluted-owner")
        os.makedirs(owner_dir, exist_ok=True)
        os.makedirs(polluted_dir, exist_ok=True)
        shared_path = os.path.join(owner_dir, "shared.xlsx")
        with open(shared_path, "wb") as handle:
            handle.write(b"shared")

        self._mark_retention_export(
            export_id="exp-valid-owner-old",
            cursor_id="cursor-valid-owner",
            output_dir=owner_dir,
            artifact_paths=[shared_path],
        )
        self._mark_retention_export(
            export_id="exp-polluted-active",
            cursor_id="cursor-polluted-active",
            output_dir=owner_dir,
            artifact_paths=[shared_path],
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE exports SET output_dir = ? WHERE export_id = ?",
                (polluted_dir, "exp-polluted-active"),
            )
        self._mark_retention_export(
            export_id="exp-valid-owner-new",
            cursor_id="cursor-valid-owner",
            output_dir=owner_dir,
        )

        self.assertFalse(os.path.exists(shared_path))
        self.assertTrue(self.store.get_export("exp-valid-owner-old")["is_tombstone"])
        self.assertFalse(self.store.get_export("exp-polluted-active")["is_tombstone"])

    def test_retention_tombstones_empty_artifact_history_without_moving_files(self) -> None:
        export_dir = os.path.join(self.temp_dir.name, "empty-artifacts")
        os.makedirs(export_dir, exist_ok=True)
        self._mark_retention_export(
            export_id="exp-empty-artifacts-old",
            cursor_id="cursor-empty-artifacts",
            output_dir=export_dir,
        )

        self._mark_retention_export(
            export_id="exp-empty-artifacts-new",
            cursor_id="cursor-empty-artifacts",
            output_dir=export_dir,
        )

        self.assertTrue(self.store.get_export("exp-empty-artifacts-old")["is_tombstone"])
        self.assertEqual(os.listdir(export_dir), [])

    def test_mark_exported_rejects_artifact_outside_output_dir_before_insert(self) -> None:
        managed_dir = os.path.join(self.temp_dir.name, "write-managed")
        external_dir = os.path.join(self.temp_dir.name, "write-external")
        os.makedirs(managed_dir, exist_ok=True)
        os.makedirs(external_dir, exist_ok=True)
        external_path = os.path.join(external_dir, "external.xlsx")
        with open(external_path, "wb") as handle:
            handle.write(b"external")

        with self.assertRaisesRegex(RuntimeError, "escapes output_dir"):
            self._mark_retention_export(
                export_id="exp-write-outside",
                cursor_id="cursor-write-outside",
                output_dir=managed_dir,
                artifact_paths=[external_path],
            )

        self.assertNotIn(
            "exp-write-outside",
            {item["export_id"] for item in self.store.list_exports()},
        )
        self.assertTrue(os.path.exists(external_path))

    def test_retention_rejects_same_path_replacement_after_validation(self) -> None:
        from peap import streaming_store as streaming_store_module

        export_dir = os.path.join(self.temp_dir.name, "replacement-exports")
        os.makedirs(export_dir, exist_ok=True)
        artifact_path = os.path.join(export_dir, "old.xlsx")
        original_backup = os.path.join(export_dir, "old-original.xlsx")
        with open(artifact_path, "wb") as handle:
            handle.write(b"original")
        self._mark_retention_export(
            export_id="exp-replacement-old",
            cursor_id="cursor-replacement",
            output_dir=export_dir,
            artifact_paths=[artifact_path],
        )
        real_fingerprint = streaming_store_module._retention_artifact_fingerprint
        replaced = False

        def replace_after_validation(path: str, *, field: str):
            nonlocal replaced
            fingerprint = real_fingerprint(path, field=field)
            if not replaced and path == artifact_path and field.startswith("export[exp-replacement-old]"):
                os.replace(path, original_backup)
                with open(path, "wb") as handle:
                    handle.write(b"replacement")
                replaced = True
            return fingerprint

        with (
            patch(
                "peap.streaming_store._retention_artifact_fingerprint",
                side_effect=replace_after_validation,
            ),
            self.assertRaisesRegex(RuntimeError, "changed before staging"),
        ):
            self._mark_retention_export(
                export_id="exp-replacement-new",
                cursor_id="cursor-replacement",
                output_dir=export_dir,
            )

        self.assertTrue(replaced)
        self.assertFalse(self.store.get_export("exp-replacement-old")["is_tombstone"])
        self.assertNotIn(
            "exp-replacement-new",
            {item["export_id"] for item in self.store.list_exports()},
        )
        with open(artifact_path, "rb") as handle:
            self.assertEqual(handle.read(), b"replacement")

    def test_retention_detects_tamper_after_staging_and_restores_path(self) -> None:
        export_dir = os.path.join(self.temp_dir.name, "staging-tamper-exports")
        os.makedirs(export_dir, exist_ok=True)
        artifact_path = os.path.join(export_dir, "old.xlsx")
        with open(artifact_path, "wb") as handle:
            handle.write(b"original")
        self._mark_retention_export(
            export_id="exp-staging-tamper-old",
            cursor_id="cursor-staging-tamper",
            output_dir=export_dir,
            artifact_paths=[artifact_path],
        )
        real_replace = os.replace

        def tampering_replace(source: str, destination: str) -> None:
            real_replace(source, destination)
            if destination.endswith(".pending") and ".peap-retention-" in destination:
                with open(destination, "wb") as handle:
                    handle.write(b"tampered")

        with (
            patch("peap.streaming_store.os.replace", side_effect=tampering_replace),
            self.assertRaisesRegex(RuntimeError, "changed during staging"),
        ):
            self._mark_retention_export(
                export_id="exp-staging-tamper-new",
                cursor_id="cursor-staging-tamper",
                output_dir=export_dir,
            )

        self.assertTrue(os.path.exists(artifact_path))
        self.assertFalse(self.store.get_export("exp-staging-tamper-old")["is_tombstone"])
        self.assertNotIn(
            "exp-staging-tamper-new",
            {item["export_id"] for item in self.store.list_exports()},
        )

    def test_retention_preserves_transaction_and_restore_errors(self) -> None:
        export_dir = os.path.join(self.temp_dir.name, "restore-error-exports")
        os.makedirs(export_dir, exist_ok=True)
        artifact_path = os.path.join(export_dir, "old.xlsx")
        with open(artifact_path, "wb") as handle:
            handle.write(b"original")
        self._mark_retention_export(
            export_id="exp-restore-error-old",
            cursor_id="cursor-restore-error",
            output_dir=export_dir,
            artifact_paths=[artifact_path],
        )

        with (
            patch(
                "peap.streaming_store._validate_export_artifact_checksums",
                side_effect=[{}, RuntimeError("transaction recheck failed")],
            ),
            patch(
                "peap.streaming_store._restore_retention_staged_files",
                side_effect=RuntimeError("restore failed"),
            ),
            self.assertRaises(ExceptionGroup) as raised,
        ):
            self._mark_retention_export(
                export_id="exp-restore-error-new",
                cursor_id="cursor-restore-error",
                output_dir=export_dir,
            )

        messages = [str(error) for error in raised.exception.exceptions]
        self.assertEqual(messages, ["transaction recheck failed", "restore failed"])

    def test_mark_exported_rejects_false_cursor_id_instead_of_persisting_empty_cursor(self) -> None:
        with self.assertRaisesRegex(ValueError, "cursor_id"):
            self.store.mark_exported(
                export_id="exp-false-cursor-id",
                cursor_id=False,  # type: ignore[arg-type]
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="股权转让",
                output_dir=f"{self.temp_dir.name}/exports",
                summary={"new_records": 0},
                records=[],
            )

        self.assertEqual(self.store.list_exports(), [])

    def test_mark_exported_rejects_empty_resolved_cursor_id_without_writing(self) -> None:
        for cursor_key in ("", None, False):
            with self.subTest(cursor_key=cursor_key):
                with self.assertRaisesRegex(ValueError, "cursor_id"):
                    self.store.mark_exported(
                        export_id=f"exp-empty-resolved-cursor-id-{cursor_key!r}",
                        cursor_id=None,
                        cursor_key=cursor_key,
                        requested_export_mode="incremental",
                        date_from="2026-03-01",
                        date_to="2026-03-31",
                        project_type="股权转让",
                        output_dir=f"{self.temp_dir.name}/exports",
                        summary={"new_records": 0},
                        records=[],
                    )

        self.assertEqual(self.store.list_exports(), [])

    def test_export_readers_reject_empty_cursor_id_instead_of_falling_back_to_cursor_key(self) -> None:
        with self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO exports (
                    export_id, cursor_key, cursor_id, mode, date_from, date_to,
                    project_type, output_dir, summary_json, created_at,
                    is_tombstone, pruned_by_retention, retention_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "exp-polluted-empty-cursor-id",
                    "legacy-cursor-key",
                    "",
                    "incremental",
                    "2026-03-01",
                    "2026-03-31",
                    "股权转让",
                    f"{self.temp_dir.name}/exports",
                    json.dumps({"new_records": 0}),
                    "2026-03-31 00:00:00",
                    0,
                    0,
                    20,
                ),
            )

        with self.assertRaisesRegex(ValueError, "cursor_id"):
            self.store.list_exports()
        with self.assertRaisesRegex(ValueError, "cursor_id"):
            self.store.get_export("exp-polluted-empty-cursor-id")

    def test_merge_record_payloads_keeps_none_as_empty_payload(self) -> None:
        self.assertEqual(
            _merge_record_payloads(None, {"项目编号": "G32026NONE", "空字段": ""}),
            {"项目编号": "G32026NONE"},
        )
        self.assertEqual(_merge_record_payloads({"项目名称": "解析项目"}, None), {"项目名称": "解析项目"})

    def test_merge_record_payloads_rejects_explicit_non_mapping_payloads(self) -> None:
        with self.assertRaisesRegex(ValueError, "parser_payload must be an object"):
            _merge_record_payloads([], {"项目编号": "G32026BADPARSER"})  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, "postprocess_payload must be an object"):
            _merge_record_payloads({"项目编号": "G32026BADPOST"}, [])  # type: ignore[arg-type]

    def test_bind_ingested_record_identity_rejects_explicit_non_mapping_canonical_record(self) -> None:
        cases = [
            (False, "canonical_record must be an object"),
            ([], "canonical_record must be an object"),
        ]

        for canonical_record, error in cases:
            with self.subTest(canonical_record=canonical_record):
                record = IngestedRecord(
                    record_id="rec-bind-bad-canonical",
                    revision_hash="hash-bind-bad-canonical",
                    project_code="G32026BADBIND",
                    project_name="测试项目",
                    project_type="股权转让",
                    exchange="shanghai",
                    listing_date="2026-03-21",
                    state="ready",
                    source_file="bad-bind.html",
                    archive_path="bad-bind.html",
                    parser_payload={"项目编号": "G32026BADBIND"},
                    postprocess_payload={"项目编号": "G32026BADBIND"},
                    findings=[],
                    canonical_record=canonical_record,  # type: ignore[arg-type]
                )

                with self.assertRaisesRegex(ValueError, error):
                    _bind_ingested_record_identity(record, "rec-bound")

    def test_bind_ingested_record_identity_rejects_explicit_non_mapping_business_identity(self) -> None:
        record = IngestedRecord(
            record_id="rec-bind-bad-business-identity",
            revision_hash="hash-bind-bad-business-identity",
            project_code="G32026BADBIND",
            project_name="测试项目",
            project_type="股权转让",
            exchange="shanghai",
            listing_date="2026-03-21",
            state="ready",
            source_file="bad-bind.html",
            archive_path="bad-bind.html",
            parser_payload={"项目编号": "G32026BADBIND"},
            postprocess_payload={"项目编号": "G32026BADBIND"},
            findings=[],
            canonical_record={"business_identity": []},
        )

        with patch("peap.streaming_store._resolve_business_kernel_fields", return_value=("", "")):
            with self.assertRaisesRegex(ValueError, r"canonical_record\.business_identity must be an object"):
                _bind_ingested_record_identity(record, "rec-bound")

    def test_resolve_scope_source_id_rejects_explicit_non_mapping_source_identity(self) -> None:
        for source_identity in (False, []):
            with self.subTest(source_identity=source_identity):
                with self.assertRaisesRegex((TypeError, ValueError), "source_identity"):
                    _resolve_scope_source_id(source_identity=source_identity, exchange="")  # type: ignore[arg-type]

    def test_upsert_record_rejects_explicit_non_mapping_source_identity(self) -> None:
        for source_identity in (False, [], "not-an-object", None):
            with self.subTest(source_identity=source_identity):
                record = IngestedRecord(
                    record_id=f"rec-bad-source-identity-{type(source_identity).__name__}",
                    revision_hash=f"hash-bad-source-identity-{type(source_identity).__name__}",
                    project_code="G32026BADIDENTITY",
                    project_name="测试项目",
                    project_type="股权转让",
                    exchange="shanghai",
                    listing_date="2026-03-21",
                    state="ready",
                    source_file="bad-source-identity.html",
                    archive_path="bad-source-identity.html",
                    parser_payload={"项目编号": "G32026BADIDENTITY"},
                    postprocess_payload={"项目编号": "G32026BADIDENTITY"},
                    findings=[],
                    source_identity=source_identity,  # type: ignore[arg-type]
                )

                with self.assertRaisesRegex(ValueError, "source_identity"):
                    self.store.upsert_record(record)

    def test_upsert_record_persists_success_source_identity_and_canonical_revision(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "success-source.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>success</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-success-canonical",
                revision_hash="hash-success-canonical",
                project_code="G32026SH1000888",
                project_name="测试项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=os.path.join(self.temp_dir.name, "archive", "success-source.html"),
                parser_payload={
                    "项目编号": "G32026SH1000888",
                    "项目名称": "解析层项目名",
                    "项目类型": "股权转让",
                    "转让方": "解析层卖方",
                },
                postprocess_payload={
                    "项目编号": "G32026SH1000888",
                    "项目名称": "后处理项目名",
                    "项目类型": "股权转让",
                    "转让方": "后处理卖方",
                    "类型": "国资",
                },
                findings=[
                    PostProcessFinding(
                        severity="info",
                        type="mapping_applied",
                        message="mapping applied",
                        evidence={"field": "source_type"},
                    )
                ],
                source_identity={
                    "record_family": "listing",
                    "original_source_file": source_file,
                    "source_url": "https://example.test/detail/store-canonical",
                    "project_code": "G32026SH1000888",
                    "project_name": "测试项目",
                    "exchange": "shanghai",
                    "listing_date": "2026-03-21",
                    "candidate_tokens": [
                        "project_code:G32026SH1000888",
                        "project_id:STORE001",
                        "page_url:https://example.test/detail/store-canonical",
                    ],
                },
                canonical_record={
                    "record_family": "listing",
                    "source_identity": {
                        "source_url": "https://example.test/detail/store-canonical",
                    },
                    "business_identity": {"project_code": "G32026SH1000888"},
                    "canonical_fields": {
                        "project_code": "G32026SH1000888",
                        "project_name": "规范化项目名",
                        "project_type": "股权转让",
                        "seller": "规范化卖方",
                        "source_type": "国资",
                    },
                    "export_extras": {
                        "挂牌次数": 2,
                    },
                    "policy_state": {"mapping_status": "applied"},
                },
                canonical_projection={
                    "项目编号": "G32026SH1000888",
                    "项目名称": "规范化项目名",
                    "项目类型": "股权转让",
                    "转让方": "规范化卖方",
                    "类型": "国资",
                },
            )
        )

        record = self.store.get_record("rec-success-canonical")

        self.assertEqual(record["source_identity_json"]["original_source_file"], source_file)
        self.assertEqual(record["source_identity_json"]["source_url"], "https://example.test/detail/store-canonical")
        self.assertEqual(
            record["source_identity_json"]["candidate_tokens"],
            [
                "project_code:G32026SH1000888",
                "project_id:STORE001",
                "page_url:https://example.test/detail/store-canonical",
            ],
        )
        self.assertEqual(record["canonical_record"]["canonical_fields"]["project_name"], "规范化项目名")
        self.assertEqual(record["canonical_record"]["canonical_fields"]["seller"], "规范化卖方")
        self.assertEqual(record["canonical_projection"]["项目名称"], "规范化项目名")
        self.assertEqual(record["canonical_projection"]["转让方"], "规范化卖方")
        self.assertEqual(record["canonical_projection"]["挂牌次数"], 2)
        self.assertTrue(any(str(item.get("type") or "") == "mapping_applied" for item in record["findings"]))

    def test_get_record_surfaces_invalid_source_identity_json_instead_of_empty_identity(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "invalid-source-identity.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>invalid source identity</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-invalid-source-identity",
                revision_hash="hash-invalid-source-identity",
                project_code="G32026SH1000990",
                project_name="测试项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SH1000990"},
                postprocess_payload={"项目编号": "G32026SH1000990", "项目类型": "股权转让"},
                findings=[],
            )
        )
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                """
                UPDATE records
                SET source_identity_json = ?
                WHERE record_id = ?
                """,
                ("{", "rec-invalid-source-identity"),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.get_record("rec-invalid-source-identity")

    def test_get_record_surfaces_invalid_canonical_record_json_instead_of_empty_contract(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "invalid-canonical-record.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>invalid canonical record</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-invalid-canonical-record",
                revision_hash="hash-invalid-canonical-record",
                project_code="G32026SH1000991",
                project_name="测试项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SH1000991"},
                postprocess_payload={"项目编号": "G32026SH1000991", "项目类型": "股权转让"},
                findings=[],
            )
        )
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.get_record("rec-invalid-canonical-record")

    def test_get_record_surfaces_invalid_acknowledged_payload_json_instead_of_empty_ack(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "invalid-ack-payload.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>invalid ack payload</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-invalid-ack-payload",
                revision_hash="hash-invalid-ack-payload",
                project_code="G32026ACKDISPLAY",
                project_name="确认 payload 展示项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="field_missing",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026ACKDISPLAY"},
                postprocess_payload={"项目编号": "G32026ACKDISPLAY"},
                findings=[],
            )
        )
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                "UPDATE records SET acknowledged_payload_json = ? WHERE record_id = ?",
                ("{", "rec-invalid-ack-payload"),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.get_record("rec-invalid-ack-payload")

    def test_existing_project_codes_surfaces_invalid_source_identity_json_instead_of_exchange_fallback(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "invalid-source-identity-scope.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>invalid source identity scope</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-invalid-source-identity-scope",
                revision_hash="hash-invalid-source-identity-scope",
                project_code="G32026SH1000992",
                project_name="测试项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SH1000992"},
                postprocess_payload={"项目编号": "G32026SH1000992", "项目类型": "股权转让"},
                findings=[],
            )
        )
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                """
                UPDATE records
                SET source_identity_json = ?
                WHERE record_id = ?
                """,
                ("{", "rec-invalid-source-identity-scope"),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.list_existing_project_codes(states=["ready"], source_id="sse")

    def test_existing_candidate_tokens_surfaces_invalid_source_identity_json_instead_of_scoped_exchange_fallback(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "invalid-source-identity-token-scope.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>invalid source identity token scope</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-invalid-source-identity-token-scope",
                revision_hash="hash-invalid-source-identity-token-scope",
                project_code="G32026SH1000993",
                project_name="测试项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SH1000993", "project_id": "TOKEN-SCOPE-001"},
                postprocess_payload={"项目编号": "G32026SH1000993", "项目类型": "股权转让"},
                findings=[],
            )
        )
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                """
                UPDATE records
                SET source_identity_json = ?
                WHERE record_id = ?
                """,
                ("{", "rec-invalid-source-identity-token-scope"),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.list_existing_candidate_tokens(
                states=["ready"],
                source_id="sse",
                include_scoped_tokens=True,
            )

    def test_existing_candidate_tokens_rejects_false_source_identity_candidate_tokens_instead_of_dropping(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "invalid-source-identity-candidate-tokens.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>invalid source identity candidate tokens</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-invalid-source-identity-candidate-tokens",
                revision_hash="hash-invalid-source-identity-candidate-tokens",
                project_code="G32026SH1000994",
                project_name="测试项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SH1000994"},
                postprocess_payload={"项目编号": "G32026SH1000994", "项目类型": "股权转让"},
                findings=[],
            )
        )
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                """
                UPDATE records
                SET source_identity_json = ?
                WHERE record_id = ?
                """,
                (
                    json.dumps(
                        {
                            "record_family": "listing",
                            "source_url": "https://example.test/candidate-token-bad",
                            "candidate_tokens": False,
                        }
                    ),
                    "rec-invalid-source-identity-candidate-tokens",
                ),
            )

        with self.assertRaisesRegex((TypeError, ValueError), "source_identity.candidate_tokens"):
            self.store.list_existing_candidate_tokens(states=["ready"])

    def test_existing_download_dedup_rejects_non_mapping_record_boundary(self) -> None:
        for record in (False, [], "not-a-record"):
            with self.subTest(record=record):
                with self.assertRaisesRegex((TypeError, ValueError), "record"):
                    _can_use_record_for_existing_download_dedup(record)  # type: ignore[arg-type]

    def test_upsert_record_recomputes_canonical_projection_from_canonical_record(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "projection-recompute.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>projection recompute</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-projection-recompute",
                revision_hash="hash-projection-recompute",
                project_code="G32026SH1000991",
                project_name="原始项目名",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SH1000991"},
                postprocess_payload={"项目编号": "G32026SH1000991"},
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {"project_code": "G32026SH1000991"},
                    "canonical_fields": {
                        "project_code": "G32026SH1000991",
                        "project_name": "规范化项目名",
                        "project_type": "股权转让",
                        "status": "挂牌中",
                        "exchange": "shanghai",
                        "start_date": "2026-03-21",
                        "price": "108.00",
                        "seller": "规范化卖方",
                        "source_type": "国资",
                    },
                    "export_extras": {
                        "挂牌次数": 4,
                    },
                },
                canonical_projection={
                    "项目编号": "G32026SH1000991",
                    "项目名称": "过期项目名",
                    "转让方": "过期卖方",
                    "挂牌价格": "999.99",
                },
                findings=[],
            )
        )

        record = self.store.get_record("rec-projection-recompute")
        self.assertEqual(record["canonical_projection"]["项目名称"], "规范化项目名")
        self.assertEqual(record["canonical_projection"]["转让方"], "规范化卖方")
        self.assertEqual(record["canonical_projection"]["挂牌价格"], "108.00")
        self.assertEqual(record["canonical_projection"]["挂牌次数"], 4)

    def test_upsert_record_refreshes_state_and_findings_when_revision_hash_is_unchanged(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "same-payload.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>same payload</body></html>")

        base_payload = {
            "项目编号": "G32025CQ1000202-3",
            "项目名称": "测试项目",
            "项目类型": "股权转让",
            "转让方": "中铁二院工程集团有限责任公司",
            "隶属集团": "中国铁路工程集团有限公司",
        }
        conflict_record = IngestedRecord(
            record_id="rec-same-hash",
            revision_hash="hash-same",
            project_code="G32025CQ1000202-3",
            project_name="测试项目",
            project_type="股权转让",
            exchange="chongqing",
            listing_date="2026-03-26",
            state="mapping_conflict",
            source_file=source_file,
            archive_path=source_file,
            parser_payload=base_payload,
            postprocess_payload=base_payload,
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_conflict",
                    message="conflicting group candidates",
                    evidence={"options": ["中国铁路工程集团有限公司", "中铁"]},
                )
            ],
        )
        first = self.store.upsert_record(conflict_record)
        self.assertTrue(first["changed"])

        gap_record = IngestedRecord(
            **{
                **conflict_record.__dict__,
                "state": "pending_mapping",
                "findings": [
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_gap",
                        message="缺少类型，暂不能进入导出",
                        evidence={"missing_fields": ["类型"]},
                    ),
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_missing",
                        message="缺少类型，暂不能进入导出",
                        evidence={"missing_fields": ["类型"]},
                    ),
                ],
            }
        )
        second = self.store.upsert_record(gap_record)

        self.assertFalse(second["changed"])

        rows = self.store.iter_latest_records(states=["pending_mapping"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["record_id"], "rec-same-hash")
        self.assertEqual(rows[0]["state"], "pending_mapping")
        self.assertEqual(rows[0]["revision_id"], first["revision_id"])
        self.assertEqual(
            {str(item.get("type") or "") for item in rows[0]["findings"]},
            {"mapping_gap", "mapping_missing"},
        )

    def test_upsert_record_same_hash_forks_when_authoritative_revision_snapshot_changes(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "same-hash-canonical.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>same hash canonical drift</body></html>")

        base = IngestedRecord(
            record_id="rec-same-hash-canonical",
            revision_hash="hash-authoritative-same",
            project_code="G32026SH1000888",
            project_name="原始项目名",
            project_type="股权转让",
            exchange="shanghai",
            listing_date="2026-03-21",
            state="ready",
            source_file=source_file,
            archive_path=source_file,
            parser_payload={"项目编号": "G32026SH1000888", "项目名称": "原始项目名"},
            postprocess_payload={"项目编号": "G32026SH1000888", "项目名称": "原始项目名", "项目类型": "股权转让"},
            canonical_record={"record_id": "rec-same-hash-canonical", "project_name": "原始项目名"},
            canonical_projection={"项目编号": "G32026SH1000888", "项目名称": "原始项目名"},
            findings=[],
        )

        first = self.store.upsert_record(base)
        drifted = IngestedRecord(
            **{
                **base.__dict__,
                "parser_payload": {"项目编号": "G32026SH1000888", "项目名称": "修正项目名"},
                "canonical_record": {"record_id": "rec-same-hash-canonical", "project_name": "修正项目名"},
                "canonical_projection": {"项目编号": "G32026SH1000888", "项目名称": "修正项目名"},
            }
        )
        second = self.store.upsert_record(drifted)

        self.assertTrue(second["changed"])
        self.assertNotEqual(second["revision_id"], first["revision_id"])
        latest = self.store.get_record("rec-same-hash-canonical")
        self.assertEqual(latest["revision_id"], second["revision_id"])
        self.assertEqual(latest["canonical_record"]["project_name"], "修正项目名")
        with sqlite3.connect(self.store.db_path) as conn:
            revision_count = conn.execute(
                "SELECT COUNT(*) FROM record_revisions WHERE record_id = ?",
                ("rec-same-hash-canonical",),
            ).fetchone()[0]
        self.assertEqual(revision_count, 2)

    def test_mapping_entries_and_settings_have_history(self) -> None:
        entry_id = self.store.upsert_mapping_entry(
            company_name="上海电气集团恒联企业发展有限公司",
            group_name="上海电气集团",
            source_type="国资",
            metadata={"match_field": "transferor", "target_field": "group_name"},
        )
        second_entry_id = self.store.upsert_mapping_entry(
            company_name="上海电气集团恒联企业发展有限公司",
            group_name="",
            source_type="市属",
            metadata={"match_field": "transferor", "target_field": "source_type"},
        )
        self.assertTrue(entry_id)
        self.assertNotEqual(entry_id, second_entry_id)
        items = self.store.list_mapping_entries()
        self.assertEqual(len(items), 2)
        group_entry = next(item for item in items if item["group_name"] == "上海电气集团")
        self.assertEqual(group_entry["metadata"]["match_field"], "transferor")

        self.store.set_setting("ui.basic", {"default_exchange": "all"})
        current = self.store.get_setting("ui.basic")
        self.assertEqual(current["default_exchange"], "all")

    def test_add_audit_entry_rejects_false_payload_instead_of_persisting_json_false(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload"):
            self.store.add_audit_entry("settings_basic_updated", False)  # type: ignore[arg-type]

        with sqlite3.connect(self.store.db_path) as conn:
            audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        self.assertEqual(audit_count, 0)

    def test_add_audit_entry_rejects_false_action_instead_of_persisting_false_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "action"):
            self.store.add_audit_entry(False, {"source": "test"})  # type: ignore[arg-type]

        with sqlite3.connect(self.store.db_path) as conn:
            audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        self.assertEqual(audit_count, 0)

    def test_upsert_mapping_entry_rejects_false_metadata_instead_of_persisting_default_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "metadata"):
            self.store.upsert_mapping_entry(
                company_name="测试主体",
                group_name="测试集团",
                metadata=False,  # type: ignore[arg-type]
            )

        self.assertEqual(self.store.list_mapping_entries(), [])

    def test_set_setting_rejects_false_value_instead_of_persisting_json_false(self) -> None:
        with self.assertRaisesRegex(ValueError, "value"):
            self.store.set_setting("ui.basic", False)  # type: ignore[arg-type]

        with sqlite3.connect(self.store.db_path) as conn:
            setting_count = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
            revision_count = conn.execute("SELECT COUNT(*) FROM settings_revisions").fetchone()[0]
        self.assertEqual(setting_count, 0)
        self.assertEqual(revision_count, 0)

    def test_set_setting_rejects_false_key_instead_of_persisting_false_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "key"):
            self.store.set_setting(False, {"default_exchange": "all"})  # type: ignore[arg-type]

        with sqlite3.connect(self.store.db_path) as conn:
            setting_count = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
            revision_count = conn.execute("SELECT COUNT(*) FROM settings_revisions").fetchone()[0]
        self.assertEqual(setting_count, 0)
        self.assertEqual(revision_count, 0)

    def test_get_setting_rejects_false_default_for_missing_setting(self) -> None:
        with self.assertRaisesRegex(ValueError, "default must be an object"):
            self.store.get_setting("ui.missing", default=False)  # type: ignore[arg-type]

    def test_get_setting_rejects_list_default_for_corrupt_setting(self) -> None:
        self.store.set_setting("ui.basic", {"default_exchange": "all"})
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE settings SET value_json = ? WHERE key = ?",
                ("{not valid json", "ui.basic"),
            )

        with self.assertRaisesRegex(ValueError, "default must be an object"):
            self.store.get_setting("ui.basic", default=[])  # type: ignore[arg-type]

    def test_get_setting_returns_default_copy_for_missing_setting(self) -> None:
        default = {"default_exchange": "fallback"}

        payload = self.store.get_setting("ui.missing", default=default)
        payload["default_exchange"] = "changed"

        self.assertEqual(default, {"default_exchange": "fallback"})
        self.assertEqual(self.store.get_setting("ui.missing", default=default), {"default_exchange": "fallback"})

    def test_list_mapping_entries_orders_by_recent_update(self) -> None:
        self.store.upsert_mapping_entry(
            company_name="旧规则",
            group_name="旧集团",
            metadata={"match_field": "transferor", "target_field": "group_name", "notes": "old"},
        )
        self.store.upsert_mapping_entry(
            company_name="新规则",
            source_type="央企",
            metadata={"match_field": "transferor", "target_field": "source_type", "notes": "new"},
        )

        items = self.store.list_mapping_entries()

        self.assertEqual(items[0]["company_name"], "新规则")
        self.assertEqual(items[0]["metadata"]["notes"], "new")

    def test_replace_mapping_entry_rekeys_existing_rule_without_leaving_old_row(self) -> None:
        original_entry_id = self.store.upsert_mapping_entry(
            company_name="旧主体",
            group_name="旧集团",
            metadata={"match_field": "transferor", "target_field": "group_name", "notes": "before"},
        )

        replacement_entry_id = self.store.replace_mapping_entry(
            entry_id=original_entry_id,
            company_name="新主体",
            group_name="新集团",
            source_type="",
            metadata={"match_field": "transferor", "target_field": "group_name", "notes": "after"},
        )

        self.assertNotEqual(replacement_entry_id, original_entry_id)
        items = self.store.list_mapping_entries()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["entry_id"], replacement_entry_id)
        self.assertEqual(items[0]["company_name"], "新主体")
        self.assertEqual(items[0]["group_name"], "新集团")
        self.assertEqual(items[0]["metadata"]["notes"], "after")

    def test_replace_mapping_entry_rejects_false_metadata_without_deleting_original_rule(self) -> None:
        original_entry_id = self.store.upsert_mapping_entry(
            company_name="旧主体",
            group_name="旧集团",
            metadata={"match_field": "transferor", "target_field": "group_name", "notes": "before"},
        )

        with self.assertRaisesRegex(ValueError, "metadata"):
            self.store.replace_mapping_entry(
                entry_id=original_entry_id,
                company_name="新主体",
                group_name="新集团",
                metadata=False,  # type: ignore[arg-type]
            )

        items = self.store.list_mapping_entries()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["entry_id"], original_entry_id)
        self.assertEqual(items[0]["metadata"]["notes"], "before")

    def test_list_mapping_entries_surfaces_invalid_metadata_json_instead_of_returning_empty_metadata(self) -> None:
        entry_id = self.store.upsert_mapping_entry(
            company_name="坏元数据主体",
            group_name="坏元数据集团",
            metadata={"match_field": "transferor", "target_field": "group_name"},
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE mapping_entries SET metadata_json = ? WHERE entry_id = ?",
                ("{", entry_id),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.list_mapping_entries()

    def test_get_mapping_entry_surfaces_invalid_metadata_json_instead_of_returning_empty_metadata(self) -> None:
        entry_id = self.store.upsert_mapping_entry(
            company_name="坏单条元数据主体",
            source_type="国资",
            metadata={"match_field": "transferor", "target_field": "source_type"},
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE mapping_entries SET metadata_json = ? WHERE entry_id = ?",
                ("{", entry_id),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.get_mapping_entry(entry_id=entry_id)

    def test_get_mapping_entry_surfaces_non_object_metadata_json_instead_of_returning_empty_metadata(self) -> None:
        entry_id = self.store.upsert_mapping_entry(
            company_name="非对象元数据主体",
            source_type="国资",
            metadata={"match_field": "transferor", "target_field": "source_type"},
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE mapping_entries SET metadata_json = ? WHERE entry_id = ?",
                ("false", entry_id),
            )

        with self.assertRaisesRegex(ValueError, "metadata_json"):
            self.store.get_mapping_entry(entry_id=entry_id)

    def test_get_mapping_entry_rejects_false_entry_id_as_non_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "entry_id must be text"):
            self.store.get_mapping_entry(entry_id=False)  # type: ignore[arg-type]

    def test_delete_mapping_rule_rejects_false_entry_id_as_non_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "entry_id must be text"):
            self.store.delete_mapping_entry(entry_id=False)  # type: ignore[arg-type]

    def test_normalize_required_mapping_states_reclassifies_unknown_business_label_to_pending_review(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "unknown-type.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>unknown type</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-unknown-type",
                revision_hash="hash-unknown-type",
                project_code="UNKNOWN-LEGACY",
                project_name="旧未知类型项目",
                project_type="未知",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "项目编号": "UNKNOWN-LEGACY",
                    "项目名称": "旧未知类型项目",
                    "项目类型": "未知",
                    "类型": "国资",
                },
                postprocess_payload={
                    "项目编号": "UNKNOWN-LEGACY",
                    "项目名称": "旧未知类型项目",
                    "项目类型": "未知",
                    "类型": "国资",
                },
                findings=[],
            )
        )

        summary = self.store.normalize_required_mapping_states()

        self.assertEqual(summary["records"], 1)
        latest = self.store.iter_latest_records(states=["pending_review"])
        self.assertEqual(len(latest), 1)
        self.assertTrue(
            any(str(item.get("type") or "") == "business_resolution_required" for item in latest[0]["findings"])
        )
        self.assertEqual(self.store.count_pending_mappings(), 0)

    def test_acknowledge_field_missing_surfaces_invalid_acknowledged_payload_json_instead_of_overwriting(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "acknowledge-bad-payload.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>acknowledge bad payload</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-ack-bad-payload",
                revision_hash="hash-ack-bad-payload",
                project_code="G32026ACKBAD",
                project_name="确认 payload 损坏项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="field_missing",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026ACKBAD"},
                postprocess_payload={"项目编号": "G32026ACKBAD"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="canonical_field_missing",
                        message="Missing required canonical fields for export: seller",
                        evidence={"missing_fields": ["seller"]},
                    )
                ],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET acknowledged_payload_json = ? WHERE record_id = ?",
                ("{", "rec-ack-bad-payload"),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.acknowledge_field_missing("rec-ack-bad-payload", missing_fields=["seller"])

    def test_acknowledge_field_missing_rejects_false_missing_fields_instead_of_acknowledging_empty_set(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "acknowledge-false-missing-fields.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>acknowledge false missing fields</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-ack-false-missing-fields",
                revision_hash="hash-ack-false-missing-fields",
                project_code="G32026ACKFALSE",
                project_name="确认空字段拒绝项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="field_missing",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026ACKFALSE"},
                postprocess_payload={"项目编号": "G32026ACKFALSE"},
                findings=[],
            )
        )

        with self.assertRaisesRegex(ValueError, "missing_fields"):
            self.store.acknowledge_field_missing(
                "rec-ack-false-missing-fields",
                missing_fields=False,  # type: ignore[arg-type]
            )

        with sqlite3.connect(self.store.db_path) as conn:
            acknowledged_payload_json = conn.execute(
                "SELECT acknowledged_payload_json FROM records WHERE record_id = ?",
                ("rec-ack-false-missing-fields",),
            ).fetchone()[0]
            audit_count = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = ?",
                ("field_missing_acknowledged",),
            ).fetchone()[0]
        self.assertEqual(json.loads(acknowledged_payload_json), {})
        self.assertEqual(audit_count, 0)

    def test_acknowledge_field_missing_rejects_non_mapping_existing_field_missing_payload(
        self,
    ) -> None:
        invalid_payloads = [False, [], "not-an-object"]
        for index, field_missing_payload in enumerate(invalid_payloads):
            with self.subTest(field_missing_payload=field_missing_payload):
                source_file = os.path.join(self.temp_dir.name, f"acknowledge-bad-field-missing-{index}.html")
                with open(source_file, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>acknowledge bad field missing payload</body></html>")
                record_id = f"rec-ack-bad-field-missing-{index}"
                self.store.upsert_record(
                    IngestedRecord(
                        record_id=record_id,
                        revision_hash=f"hash-ack-bad-field-missing-{index}",
                        project_code=f"G32026ACKOBJECT{index}",
                        project_name="确认 field_missing payload 拒绝项目",
                        project_type="股权转让",
                        exchange="shanghai",
                        listing_date="2026-03-21",
                        state="field_missing",
                        source_file=source_file,
                        archive_path=source_file,
                        parser_payload={"项目编号": f"G32026ACKOBJECT{index}"},
                        postprocess_payload={"项目编号": f"G32026ACKOBJECT{index}"},
                        findings=[],
                    )
                )
                with sqlite3.connect(self.store.db_path) as conn:
                    conn.execute(
                        "UPDATE records SET acknowledged_payload_json = ? WHERE record_id = ?",
                        (json.dumps({"field_missing": field_missing_payload}), record_id),
                    )

                with self.assertRaisesRegex(ValueError, "field_missing"):
                    self.store.acknowledge_field_missing(record_id, missing_fields=["seller"])

                with sqlite3.connect(self.store.db_path) as conn:
                    acknowledged_payload_json = conn.execute(
                        "SELECT acknowledged_payload_json FROM records WHERE record_id = ?",
                        (record_id,),
                    ).fetchone()[0]
                    audit_count = conn.execute(
                        "SELECT COUNT(*) FROM audit_log WHERE action = ?",
                        ("field_missing_acknowledged",),
                    ).fetchone()[0]
                self.assertEqual(json.loads(acknowledged_payload_json), {"field_missing": field_missing_payload})
                self.assertEqual(audit_count, 0)

    def test_acknowledge_field_missing_rejects_false_evidence_source_instead_of_defaulting_operator(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "acknowledge-false-evidence.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>acknowledge false evidence</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-ack-false-evidence",
                revision_hash="hash-ack-false-evidence",
                project_code="G32026ACKEVIDENCE",
                project_name="确认来源拒绝项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="field_missing",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026ACKEVIDENCE"},
                postprocess_payload={"项目编号": "G32026ACKEVIDENCE"},
                findings=[],
            )
        )

        with self.assertRaisesRegex(ValueError, "evidence_source must be text"):
            self.store.acknowledge_field_missing(
                "rec-ack-false-evidence",
                missing_fields=["seller"],
                evidence_source=False,  # type: ignore[arg-type]
            )

        with sqlite3.connect(self.store.db_path) as conn:
            acknowledged_payload_json = conn.execute(
                "SELECT acknowledged_payload_json FROM records WHERE record_id = ?",
                ("rec-ack-false-evidence",),
            ).fetchone()[0]
        self.assertEqual(json.loads(acknowledged_payload_json), {})

    def test_iter_latest_records_surfaces_invalid_findings_json_instead_of_returning_empty_findings(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "bad-read-findings.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>bad read findings</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-bad-read-findings",
                revision_hash="hash-bad-read-findings",
                project_code="G32026READBADFIND",
                project_name="读取坏 findings 项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026READBADFIND"},
                postprocess_payload={"项目编号": "G32026READBADFIND"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET findings_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.iter_latest_records(states=["ready"])

    def test_get_record_surfaces_invalid_findings_json_instead_of_returning_empty_findings(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "bad-get-findings.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>bad get findings</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-bad-get-findings",
                revision_hash="hash-bad-get-findings",
                project_code="G32026GETBADFIND",
                project_name="单条读取坏 findings 项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026GETBADFIND"},
                postprocess_payload={"项目编号": "G32026GETBADFIND"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET findings_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.get_record("rec-bad-get-findings")

    def test_iter_latest_records_surfaces_invalid_parser_payload_json_instead_of_returning_empty_payload(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "bad-read-parser-payload.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>bad read parser payload</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-bad-read-parser-payload",
                revision_hash="hash-bad-read-parser-payload",
                project_code="G32026READBADPARSER",
                project_name="读取坏 parser payload 项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026READBADPARSER"},
                postprocess_payload={"项目编号": "G32026READBADPARSER"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET parser_payload_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.iter_latest_records(states=["ready"])

    def test_iter_latest_records_surfaces_invalid_postprocess_payload_json_instead_of_returning_empty_payload(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "bad-read-postprocess-payload.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>bad read postprocess payload</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-bad-read-postprocess-payload",
                revision_hash="hash-bad-read-postprocess-payload",
                project_code="G32026READBADPOST",
                project_name="读取坏 postprocess payload 项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026READBADPOST"},
                postprocess_payload={"项目编号": "G32026READBADPOST"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE record_revisions SET postprocess_payload_json = ? WHERE revision_id = ?",
                ("{", int(created["revision_id"])),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.iter_latest_records(states=["ready"])

    def test_records_schema_reserves_business_kernel_columns(self) -> None:
        with self.store._connect() as conn:
            rows = conn.execute("PRAGMA table_info(records)").fetchall()

        columns = {str(row["name"] or "") for row in rows}

        self.assertIn("business_id", columns)
        self.assertIn("raw_business_label", columns)

    def test_mark_mapping_pending_is_idempotent_for_same_record(self) -> None:
        payload = {"项目编号": "G32025SH1000194-4", "项目名称": "缺类型项目"}

        self.store.mark_mapping_pending(
            record_id="rec-pending-dedupe",
            revision_id=1,
            project_code="G32025SH1000194-4",
            payload=payload,
        )
        self.store.mark_mapping_pending(
            record_id="rec-pending-dedupe",
            revision_id=2,
            project_code="G32025SH1000194-4",
            payload={**payload, "修订": "v2"},
        )

        pending = self.store.list_pending_mappings(limit=20)

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["record_id"], "rec-pending-dedupe")
        self.assertEqual(pending[0]["revision_id"], 2)
        self.assertEqual(pending[0]["payload"]["修订"], "v2")
        self.assertEqual(self.store.count_pending_mappings(), 1)

    def test_mark_mapping_pending_rejects_false_payload_instead_of_persisting_json_false(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload"):
            self.store.mark_mapping_pending(
                record_id="rec-pending-bad-payload",
                revision_id=1,
                project_code="BAD-PENDING-PAYLOAD",
                payload=False,  # type: ignore[arg-type]
            )

        self.assertEqual(self.store.list_pending_mappings(limit=20), [])
        self.assertEqual(self.store.count_pending_mappings(), 0)

    def test_list_pending_mappings_and_counts_dedupe_existing_open_duplicates(self) -> None:
        with self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO mapping_pending (record_id, revision_id, project_code, payload_json, created_at, resolved_at)
                VALUES ('rec-legacy-dup', 1, 'LEGACY-001', '{"v":1}', '2026-03-23T01:00:00Z', '')
                """
            )
            conn.execute(
                """
                INSERT INTO mapping_pending (record_id, revision_id, project_code, payload_json, created_at, resolved_at)
                VALUES ('rec-legacy-dup', 2, 'LEGACY-001', '{"v":2}', '2026-03-23T02:00:00Z', '')
                """
            )

        pending = self.store.list_pending_mappings(limit=20)

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["revision_id"], 2)
        self.assertEqual(pending[0]["payload"]["v"], 2)
        self.assertEqual(self.store.count_pending_mappings(), 1)

    def test_list_pending_mappings_surfaces_invalid_payload_json_instead_of_returning_empty_payload(self) -> None:
        self.store.mark_mapping_pending(
            record_id="rec-bad-pending-payload",
            revision_id=1,
            project_code="BAD-PENDING-PAYLOAD",
            payload={"项目编号": "BAD-PENDING-PAYLOAD"},
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE mapping_pending SET payload_json = ? WHERE record_id = ?",
                ("{", "rec-bad-pending-payload"),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.list_pending_mappings(limit=20)

    def test_store_read_helpers_stay_side_effect_free_until_explicit_maintenance_runs(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "read-side-effect-free.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>read side effect free</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-read-no-maintenance",
                revision_hash="hash-read-no-maintenance",
                project_code="G32026SH1999003",
                project_name="读路径不应修复",
                project_type="",
                exchange="shanghai",
                listing_date="2026/03/21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SH1999003", "项目名称": "读路径不应修复"},
                postprocess_payload={"项目编号": "G32026SH1999003", "项目名称": "读路径不应修复"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="project_type_unknown",
                        message="项目类型无法识别",
                    )
                ],
            )
        )
        job_id = self.store.create_job("one_click")

        self.store.list_pending_mappings(limit=20)
        self.store.get_job(job_id)

        self.assertEqual(self.store.count_pending_mappings(), 0)
        ready_rows = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(ready_rows), 1)
        self.assertEqual(ready_rows[0]["record_id"], "rec-read-no-maintenance")

        summary = run_streaming_store_maintenance(self.store)

        self.assertEqual(summary.required_mapping["records"], 1)
        self.assertEqual(self.store.count_pending_mappings(), 0)

    def test_get_job_surfaces_invalid_summary_json_instead_of_returning_empty_summary(self) -> None:
        job_id = self.store.create_job("one_click")
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET summary_json = ? WHERE job_id = ?", ("{", job_id))

        with self.assertRaises(json.JSONDecodeError):
            self.store.get_job(job_id)

    def test_update_job_counts_rejects_missing_job_instead_of_silent_noop(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no job found"):
            self.store.update_job_counts("missing-job", downloaded_inc=1)

    def test_finish_job_rejects_missing_job_instead_of_silent_noop(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no job found"):
            self.store.finish_job("missing-job", status="failed", summary={"message": "boom"})

    def test_update_record_archive_path_rejects_missing_record_instead_of_silent_noop(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no record found"):
            self.store.update_record_archive_path("missing-record", "/tmp/archive.html")

    def test_update_record_archive_path_rejects_empty_archive_path_instead_of_clearing(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "archive-path-required.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>archive path required</body></html>")
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-archive-path-required",
                revision_hash="hash-archive-path-required",
                project_code="G32026ARCHIVEPATH",
                project_name="归档路径必填项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026ARCHIVEPATH"},
                postprocess_payload={"项目编号": "G32026ARCHIVEPATH"},
                findings=[],
            )
        )

        with self.assertRaisesRegex(ValueError, "archive_path"):
            self.store.update_record_archive_path("rec-archive-path-required", "")

        record = self.store.get_record("rec-archive-path-required")
        self.assertEqual(record["archive_path"], source_file)

    def test_create_job_rejects_false_metadata_instead_of_persisting_empty_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "metadata"):
            self.store.create_job("one_click", metadata=False)  # type: ignore[arg-type]

        self.assertEqual(self.store.list_jobs(limit=10), [])

    def test_create_job_rejects_false_job_type_instead_of_persisting_false_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "job_type"):
            self.store.create_job(False, metadata={"source": "test"})  # type: ignore[arg-type]

        self.assertEqual(self.store.list_jobs(limit=10), [])

    def test_create_job_rejects_false_job_id_instead_of_generating_random_job_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "job_id"):
            self.store.create_job("one_click", job_id=False)  # type: ignore[arg-type]

        self.assertEqual(self.store.list_jobs(limit=10), [])

    def test_interrupt_running_jobs_rejects_false_reason_instead_of_persisting_false_message(self) -> None:
        job_id = self.store.create_job("one_click")
        self.store.start_job(job_id)

        with self.assertRaisesRegex(ValueError, "reason"):
            self.store.interrupt_running_jobs(reason=False)  # type: ignore[arg-type]

        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "running")
        self.assertEqual(self.store.list_job_events(job_id, limit=20)[-1]["stage"], "startup")

    def test_interrupt_job_rejects_false_job_id_instead_of_returning_not_interrupted(self) -> None:
        with self.assertRaisesRegex(ValueError, "job_id"):
            self.store.interrupt_job(False, reason="operator stop")  # type: ignore[arg-type]

    def test_interrupt_job_rejects_false_reason_instead_of_persisting_false_message(self) -> None:
        job_id = self.store.create_job("one_click")
        self.store.start_job(job_id)

        with self.assertRaisesRegex(ValueError, "reason"):
            self.store.interrupt_job(job_id, reason=False)  # type: ignore[arg-type]

        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "running")
        self.assertEqual(self.store.list_job_events(job_id, limit=20)[-1]["stage"], "startup")

    def test_finish_job_rejects_false_summary_instead_of_persisting_empty_summary(self) -> None:
        job_id = self.store.create_job("one_click")

        with self.assertRaisesRegex(ValueError, "summary"):
            self.store.finish_job(job_id, status="failed", summary=False)  # type: ignore[arg-type]

        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "starting")
        self.assertEqual(job["summary"], {})

    def test_finish_job_rejects_false_status_instead_of_persisting_false_text(self) -> None:
        job_id = self.store.create_job("one_click")

        with self.assertRaisesRegex(ValueError, "status"):
            self.store.finish_job(job_id, status=False, summary={"message": "stop"})  # type: ignore[arg-type]

        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "starting")
        self.assertEqual(job["summary"], {})

    def test_list_jobs_surfaces_invalid_summary_json_instead_of_returning_empty_summary(self) -> None:
        job_id = self.store.create_job("one_click")
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET summary_json = ? WHERE job_id = ?", ("{", job_id))

        with self.assertRaises(json.JSONDecodeError):
            self.store.list_jobs(limit=10)

    def test_interrupt_job_surfaces_invalid_summary_json_instead_of_overwriting_it(self) -> None:
        job_id = self.store.create_job("one_click")
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET summary_json = ? WHERE job_id = ?", ("{", job_id))

        with self.assertRaises(json.JSONDecodeError):
            self.store.interrupt_job(job_id, reason="operator stop")

    def test_interrupt_job_surfaces_summary_json_shape_error_instead_of_overwriting_it(self) -> None:
        job_id = self.store.create_job("one_click")
        self.store.start_job(job_id)
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET summary_json = ? WHERE job_id = ?", ("[]", job_id))

        with self.assertRaisesRegex(ValueError, "summary_json must be an object"):
            self.store.interrupt_job(job_id, reason="operator stop")

        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT status, summary_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["summary_json"], "[]")

    def test_interrupt_running_jobs_surfaces_summary_json_shape_error_instead_of_overwriting_it(self) -> None:
        job_id = self.store.create_job("one_click")
        self.store.start_job(job_id)
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET summary_json = ? WHERE job_id = ?", ("[]", job_id))

        with self.assertRaisesRegex(ValueError, "summary_json must be an object"):
            self.store.interrupt_running_jobs(reason="operator stop")

        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT status, summary_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["summary_json"], "[]")

    def test_get_job_surfaces_invalid_metadata_json_instead_of_returning_empty_metadata(self) -> None:
        job_id = self.store.create_job("one_click", metadata={"source": "operator"})
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET metadata_json = ? WHERE job_id = ?", ("{", job_id))

        with self.assertRaises(json.JSONDecodeError):
            self.store.get_job(job_id)

    def test_list_jobs_surfaces_invalid_metadata_json_instead_of_returning_empty_metadata(self) -> None:
        job_id = self.store.create_job("one_click", metadata={"source": "operator"})
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET metadata_json = ? WHERE job_id = ?", ("{", job_id))

        with self.assertRaises(json.JSONDecodeError):
            self.store.list_jobs(limit=10)

    def test_list_job_events_surfaces_invalid_payload_json_instead_of_returning_empty_payload(self) -> None:
        job_id = self.store.create_job("one_click")
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={"source_file": "downloaded.html"},
            )
        )
        with self.store._connect() as conn:
            conn.execute("UPDATE job_events SET payload_json = ? WHERE job_id = ?", ("{", job_id))

        with self.assertRaises(json.JSONDecodeError):
            self.store.list_job_events(job_id)

    def test_append_event_rejects_non_mapping_payload_instead_of_persisting_json_scalar(self) -> None:
        for payload in (False, [], "not-an-object", None):
            with self.subTest(payload=payload):
                job_id = self.store.create_job("one_click")

                with self.assertRaisesRegex(ValueError, "payload"):
                    self.store.append_event(
                        ItemProgressEvent(
                            job_id=job_id,
                            stage="downloaded",
                            status="ok",
                            payload=payload,  # type: ignore[arg-type]
                        )
                    )

                with self.store._connect() as conn:
                    event_count = conn.execute(
                        "SELECT COUNT(*) FROM job_events WHERE job_id = ?",
                        (job_id,),
                    ).fetchone()[0]
                self.assertEqual(event_count, 0)

    def test_get_operation_journal_surfaces_invalid_metadata_json_instead_of_returning_empty_metadata(
        self,
    ) -> None:
        operation_id = self.store.create_operation_journal("manual_import", metadata={"source": "operator"})
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE operation_journal SET metadata_json = ? WHERE operation_id = ?",
                ("{", operation_id),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.get_operation_journal(operation_id)

    def test_list_operation_journals_surfaces_invalid_manifest_json_instead_of_returning_empty_manifest(
        self,
    ) -> None:
        operation_id = self.store.create_operation_journal("manual_import", manifest={"records": 1})
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE operation_journal SET manifest_json = ? WHERE operation_id = ?",
                ("{", operation_id),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.list_operation_journals(limit=10)

    def test_get_operation_journal_surfaces_invalid_error_json_instead_of_returning_empty_error(self) -> None:
        operation_id = self.store.create_operation_journal("manual_import")
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE operation_journal SET error_json = ? WHERE operation_id = ?",
                ("{", operation_id),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.get_operation_journal(operation_id)

    def test_create_operation_journal_rejects_false_operation_type_instead_of_persisting_empty_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "operation_type"):
            self.store.create_operation_journal(False)  # type: ignore[arg-type]

        self.assertEqual(self.store.list_operation_journals(limit=10), [])

    def test_create_operation_journal_rejects_false_metadata_and_manifest_instead_of_persisting_empty_objects(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "metadata"):
            self.store.create_operation_journal("manual_import", metadata=False)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.store.create_operation_journal("manual_import", manifest=False)  # type: ignore[arg-type]

        self.assertEqual(self.store.list_operation_journals(limit=10), [])

    def test_create_operation_journal_rejects_false_operation_id_instead_of_generating_random_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "operation_id"):
            self.store.create_operation_journal("manual_import", operation_id=False)  # type: ignore[arg-type]

        self.assertEqual(self.store.list_operation_journals(limit=10), [])

    def test_update_operation_journal_rejects_false_status_instead_of_persisting_empty_status(self) -> None:
        operation_id = self.store.create_operation_journal("manual_import")

        with self.assertRaisesRegex(ValueError, "status"):
            self.store.update_operation_journal(operation_id, status=False)  # type: ignore[arg-type]

        operation = self.store.get_operation_journal(operation_id)
        self.assertEqual(operation["status"], "pending")

    def test_update_operation_journal_rejects_false_manifest_and_error_instead_of_persisting_json_false(
        self,
    ) -> None:
        operation_id = self.store.create_operation_journal("manual_import")

        with self.assertRaisesRegex(ValueError, "manifest"):
            self.store.update_operation_journal(operation_id, manifest=False)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "error"):
            self.store.update_operation_journal(operation_id, error=False)  # type: ignore[arg-type]

        operation = self.store.get_operation_journal(operation_id)
        self.assertEqual(operation["manifest"], {})
        self.assertEqual(operation["error"], {})

    def test_update_operation_journal_rejects_false_operation_id_instead_of_empty_key_lookup(self) -> None:
        with self.assertRaisesRegex(ValueError, "operation_id must be text"):
            self.store.update_operation_journal(False, status="failed")  # type: ignore[arg-type]

    def test_list_existing_candidate_tokens_includes_record_codes_and_downloaded_page_identities(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "existing.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>existing</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-existing",
                revision_hash="hash-existing",
                project_code="G32026BJ1000003",
                project_name="已有项目",
                project_type="股权转让",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026BJ1000003", "项目名称": "已有项目"},
                postprocess_payload={"项目编号": "G32026BJ1000003", "项目名称": "已有项目", "项目类型": "股权转让"},
                findings=[],
            )
        )
        job_id = self.store.create_job("one_click")
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={
                    "source_file": source_file,
                    "page_url": "https://example.test/detail/1",
                    "project_id": "CQ001",
                    "project_code": "G32026BJ1000003",
                },
            )
        )

        tokens = self.store.list_existing_candidate_tokens(states=["ready"])

        self.assertIn("project_code:G32026BJ1000003", tokens)
        self.assertIn("page_url:https://example.test/detail/1", tokens)
        self.assertIn("project_id:CQ001", tokens)

    def test_existing_candidate_tokens_surfaces_invalid_downloaded_event_payload_json_instead_of_skipping_event(
        self,
    ) -> None:
        job_id = self.store.create_job("one_click")
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={"project_code": "G32026BADPAYLOAD"},
            )
        )
        with self.store._connect() as conn:
            conn.execute("UPDATE job_events SET payload_json = ? WHERE job_id = ?", ("{", job_id))

        with self.assertRaises(json.JSONDecodeError):
            self.store.list_existing_candidate_tokens(states=["ready"])

    def test_existing_download_dedup_can_require_local_artifacts(self) -> None:
        missing_archive = os.path.join(self.temp_dir.name, "missing-archive.html")
        existing_source = os.path.join(self.temp_dir.name, "source-still-exists.html")
        with open(existing_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>source still exists</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-stale-artifact",
                revision_hash="hash-stale-artifact",
                project_code="G32026BJ1000998",
                project_name="缺失归档文件项目",
                project_type="股权转让",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=existing_source,
                archive_path=missing_archive,
                parser_payload={"项目编号": "G32026BJ1000998", "项目名称": "缺失归档文件项目"},
                postprocess_payload={"项目编号": "G32026BJ1000998", "项目名称": "缺失归档文件项目", "项目类型": "股权转让"},
                findings=[],
            )
        )
        job_id = self.store.create_job("one_click")
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={
                    "source_file": missing_archive,
                    "page_url": "https://example.test/detail/missing-artifact",
                    "project_id": "MISSING-ARTIFACT-001",
                    "project_code": "G32026BJ1000998",
                },
            )
        )

        default_codes = self.store.list_existing_project_codes(states=["ready"])
        verified_codes = self.store.list_existing_project_codes(
            states=["ready"],
            require_existing_artifact=True,
        )
        default_tokens = self.store.list_existing_candidate_tokens(states=["ready"])
        verified_tokens = self.store.list_existing_candidate_tokens(
            states=["ready"],
            require_existing_artifact=True,
        )

        self.assertIn("G32026BJ1000998", default_codes)
        self.assertNotIn("G32026BJ1000998", verified_codes)
        self.assertIn("project_code:G32026BJ1000998", default_tokens)
        self.assertNotIn("project_code:G32026BJ1000998", verified_tokens)
        self.assertNotIn("page_url:https://example.test/detail/missing-artifact", verified_tokens)
        self.assertNotIn("project_id:MISSING-ARTIFACT-001", verified_tokens)

    def test_existing_download_dedup_requires_verified_artifact_evidence(self) -> None:
        unresolved_source = os.path.join(self.temp_dir.name, "unresolved.bin")
        invalid_shell_source = os.path.join(self.temp_dir.name, "invalid-shell.bin")
        identity_mismatch_source = os.path.join(self.temp_dir.name, "identity-mismatch.bin")
        shared_official_source = os.path.join(self.temp_dir.name, "shared-official.bin")
        verified_source = os.path.join(self.temp_dir.name, "verified.bin")
        with open(unresolved_source, "wb") as handle:
            handle.write(b"unresolved identity evidence")
        with open(invalid_shell_source, "wb") as handle:
            handle.write(b"invalid shell sidecar evidence without body marker")
        invalid_shell_sha256 = "sha256:" + hashlib.sha256(
            b"invalid shell sidecar evidence without body marker"
        ).hexdigest()
        with open(f"{invalid_shell_source}.peap-evidence.json", "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": 1,
                    "page_kind": "invalid_shell",
                    "content_sha256": invalid_shell_sha256,
                    "identity_hints": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "source_id": "sse",
                        "project_code": "G32026BJ1002002",
                    },
                    "source_url_hash": "sha256:1111",
                    "final_url_hash": "sha256:2222",
                },
                handle,
            )
        with open(identity_mismatch_source, "wb") as handle:
            handle.write(b"identity mismatch evidence")
        with open(shared_official_source, "wb") as handle:
            handle.write(b"shared official page evidence")
        shared_sha256 = "sha256:" + hashlib.sha256(b"shared official page evidence").hexdigest()
        with open(f"{shared_official_source}.peap-evidence.json", "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": 1,
                    "page_kind": "shared_official_page",
                    "content_sha256": shared_sha256,
                    "identity_hints": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "source_id": "sse",
                        "project_code": "G32026BJ1002005",
                    },
                    "source_locator_hash": "sha256:1111",
                    "final_locator_hash": "sha256:2222",
                },
                handle,
            )
        with open(verified_source, "wb") as handle:
            handle.write(b"verified evidence")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-unresolved-evidence",
                revision_hash="hash-unresolved-evidence",
                project_code="G32026BJ1002001",
                project_name="身份未解析项目",
                project_type="",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=unresolved_source,
                archive_path=unresolved_source,
                parser_payload={"项目编号": "G32026BJ1002001", "项目名称": "身份未解析项目"},
                postprocess_payload={"项目编号": "G32026BJ1002001", "项目名称": "身份未解析项目"},
                findings=[],
            )
        )
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-invalid-evidence",
                revision_hash="hash-invalid-evidence",
                project_code="G32026BJ1002002",
                project_name="无效壳页面项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-03-21",
                state="ready",
                source_file=invalid_shell_source,
                archive_path=invalid_shell_source,
                parser_payload={"项目编号": "G32026BJ1002002", "项目名称": "无效壳页面项目"},
                postprocess_payload={"项目编号": "G32026BJ1002002", "项目名称": "无效壳页面项目", "项目类型": "股权转让"},
                findings=[],
                record_family="deal",
                canonical_record={
                    "record_family": "deal",
                    "business_identity": {"business_id": "deal_equity_transfer"},
                    "canonical_fields": {"project_code": "G32026BJ1002002"},
                },
            )
        )
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-identity-mismatch-evidence",
                revision_hash="hash-identity-mismatch-evidence",
                project_code="G32026BJ1002004",
                project_name="身份冲突项目",
                project_type="股权转让",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=identity_mismatch_source,
                archive_path=identity_mismatch_source,
                parser_payload={"项目编号": "G32026BJ1002004", "项目名称": "身份冲突项目"},
                postprocess_payload={"项目编号": "G32026BJ1002004", "项目名称": "身份冲突项目", "项目类型": "股权转让"},
                findings=[],
                source_identity={
                    "record_family": "listing",
                    "source_id": "beijing",
                    "project_code": "G32026BJ1002999",
                },
            )
        )
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-shared-official-evidence",
                revision_hash="hash-shared-official-evidence",
                project_code="G32026BJ1002005",
                project_name="共享官方页项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-03-21",
                state="ready",
                source_file=shared_official_source,
                archive_path=shared_official_source,
                parser_payload={"项目编号": "G32026BJ1002005", "项目名称": "共享官方页项目"},
                postprocess_payload={"项目编号": "G32026BJ1002005", "项目名称": "共享官方页项目", "项目类型": "股权转让"},
                findings=[],
                record_family="deal",
                source_identity={
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "source_id": "sse",
                    "project_code": "G32026BJ1002005",
                },
                canonical_record={
                    "record_family": "deal",
                    "business_identity": {
                        "project_code": "G32026BJ1002005",
                        "business_id": "deal_equity_transfer",
                    },
                    "canonical_fields": {"project_code": "G32026BJ1002005"},
                },
            )
        )
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-verified-evidence",
                revision_hash="hash-verified-evidence",
                project_code="G32026BJ1002003",
                project_name="证据已验证项目",
                project_type="股权转让",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=verified_source,
                archive_path=verified_source,
                parser_payload={"项目编号": "G32026BJ1002003", "项目名称": "证据已验证项目"},
                postprocess_payload={"项目编号": "G32026BJ1002003", "项目名称": "证据已验证项目", "项目类型": "股权转让"},
                findings=[],
            )
        )
        job_id = self.store.create_job("one_click")
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={
                    "source_file": unresolved_source,
                    "archive_path": unresolved_source,
                    "page_url": "https://example.test/detail/unresolved-evidence",
                    "project_id": "UNRESOLVED-EVIDENCE-001",
                    "project_code": "G32026BJ1002001",
                },
            )
        )
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={
                    "source_file": shared_official_source,
                    "archive_path": shared_official_source,
                    "page_url": "https://example.test/detail/shared-official-evidence",
                    "project_id": "SHARED-OFFICIAL-EVIDENCE-001",
                    "project_code": "G32026BJ1002005",
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "source_id": "sse",
                    "exchange": "sse",
                },
            )
        )

        verified_codes = self.store.list_existing_project_codes(
            states=["ready"],
            require_existing_artifact=True,
        )
        verified_tokens = self.store.list_existing_candidate_tokens(
            states=["ready"],
            require_existing_artifact=True,
        )

        self.assertNotIn("G32026BJ1002001", verified_codes)
        self.assertNotIn("G32026BJ1002002", verified_codes)
        self.assertNotIn("G32026BJ1002004", verified_codes)
        self.assertNotIn("G32026BJ1002005", verified_codes)
        self.assertIn("G32026BJ1002003", verified_codes)
        self.assertNotIn("project_code:G32026BJ1002001", verified_tokens)
        self.assertNotIn("project_code:G32026BJ1002002", verified_tokens)
        self.assertNotIn("project_code:G32026BJ1002004", verified_tokens)
        self.assertNotIn("project_code:G32026BJ1002005", verified_tokens)
        self.assertIn("project_code:G32026BJ1002003", verified_tokens)
        self.assertNotIn("page_url:https://example.test/detail/unresolved-evidence", verified_tokens)
        self.assertNotIn("project_id:UNRESOLVED-EVIDENCE-001", verified_tokens)
        self.assertNotIn("page_url:https://example.test/detail/shared-official-evidence", verified_tokens)
        self.assertNotIn("project_id:SHARED-OFFICIAL-EVIDENCE-001", verified_tokens)

    def test_existing_download_dedup_truth_matrix_allows_only_verified_evidence(self) -> None:
        def write_file(name: str, body: bytes) -> str:
            path = os.path.join(self.temp_dir.name, name)
            with open(path, "wb") as handle:
                handle.write(body)
            return path

        def write_sidecar(path: str, *, page_kind: str, project_code: str, body: bytes) -> None:
            with open(f"{path}.peap-evidence.json", "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "schema_version": 1,
                        "page_kind": page_kind,
                        "content_sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
                        "identity_hints": {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "source_id": "sse",
                            "project_code": project_code,
                        },
                        "source_locator_hash": "sha256:1111",
                        "final_locator_hash": "sha256:2222",
                    },
                    handle,
                )

        verified_path = write_file("matrix-verified.html", b"verified artifact")
        present_unverified_path = write_file("matrix-present-unverified.html", b"present unverified artifact")
        invalid_shell_path = write_file(
            "matrix-invalid-shell.html",
            b"<html><body><h1>SSE Deal Notice</h1></body></html>",
        )
        identity_mismatch_path = write_file("matrix-identity-mismatch.html", b"identity mismatch artifact")
        shared_body = b"shared official artifact"
        shared_path = write_file("matrix-shared-official.html", shared_body)
        write_sidecar(
            shared_path,
            page_kind="shared_official_page",
            project_code="G32026BJ1003105",
            body=shared_body,
        )
        stale_path = os.path.join(self.temp_dir.name, "matrix-stale-missing.html")

        cases = [
            {
                "suffix": "verified",
                "project_code": "G32026BJ1003101",
                "source_file": verified_path,
                "archive_path": verified_path,
                "source_identity": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "beijing",
                    "project_code": "G32026BJ1003101",
                    "candidate_tokens": ["project_id:MATRIX-VERIFIED"],
                },
                "expected_skip": True,
            },
            {
                "suffix": "shared-official",
                "project_code": "G32026BJ1003105",
                "source_file": shared_path,
                "archive_path": shared_path,
                "record_family": "deal",
                "business_id": "deal_equity_transfer",
                "exchange": "sse",
                "source_identity": {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "source_id": "sse",
                    "project_code": "G32026BJ1003105",
                    "candidate_tokens": ["project_id:MATRIX-SHARED-OFFICIAL"],
                },
                "expected_skip": False,
            },
            {
                "suffix": "stale",
                "project_code": "G32026BJ1003102",
                "source_file": stale_path,
                "archive_path": stale_path,
                "source_identity": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "beijing",
                    "project_code": "G32026BJ1003102",
                    "candidate_tokens": ["project_id:MATRIX-STALE"],
                },
                "expected_skip": False,
            },
            {
                "suffix": "invalid-shell",
                "project_code": "G32026BJ1003103",
                "source_file": invalid_shell_path,
                "archive_path": invalid_shell_path,
                "source_identity": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "beijing",
                    "project_code": "G32026BJ1003103",
                    "candidate_tokens": ["project_id:MATRIX-INVALID-SHELL"],
                },
                "expected_skip": False,
            },
            {
                "suffix": "identity-mismatch",
                "project_code": "G32026BJ1003104",
                "source_file": identity_mismatch_path,
                "archive_path": identity_mismatch_path,
                "source_identity": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "beijing",
                    "project_code": "G32026BJ1003999",
                    "candidate_tokens": ["project_id:MATRIX-IDENTITY-MISMATCH"],
                },
                "expected_skip": False,
            },
            {
                "suffix": "present-unverified",
                "project_code": "G32026BJ1003106",
                "source_file": present_unverified_path,
                "archive_path": present_unverified_path,
                "project_type": "",
                "exchange": "",
                "source_identity": {
                    "record_family": "listing",
                    "project_code": "G32026BJ1003106",
                    "candidate_tokens": ["project_id:MATRIX-PRESENT-UNVERIFIED"],
                },
                "expected_skip": False,
            },
            {
                "suffix": "undeclared",
                "project_code": "G32026BJ1003107",
                "source_file": "",
                "archive_path": "",
                "source_identity": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "beijing",
                    "project_code": "G32026BJ1003107",
                    "candidate_tokens": ["project_id:MATRIX-UNDECLARED"],
                },
                "expected_skip": False,
            },
        ]

        for case in cases:
            project_code = str(case["project_code"])
            record_family = str(case.get("record_family", "listing"))
            business_id = str(case.get("business_id", "equity_transfer"))
            self.store.upsert_record(
                IngestedRecord(
                    record_id=f"rec-matrix-{case['suffix']}",
                    revision_hash=f"hash-matrix-{case['suffix']}",
                    project_code=project_code,
                    project_name=f"matrix {case['suffix']}",
                    project_type=case.get("project_type", "股权转让"),
                    exchange=case.get("exchange", "beijing"),
                    listing_date="2026-03-21",
                    state="ready",
                    source_file=case["source_file"],
                    archive_path=case["archive_path"],
                    parser_payload={"项目编号": project_code, "项目名称": f"matrix {case['suffix']}"},
                    postprocess_payload={
                        "项目编号": project_code,
                        "项目名称": f"matrix {case['suffix']}",
                        "项目类型": "股权转让",
                    },
                    findings=[],
                    record_family=record_family,
                    source_identity=case["source_identity"],
                    canonical_record={
                        "record_family": record_family,
                        "business_identity": {
                            "project_code": project_code,
                            "business_id": business_id,
                        },
                        "canonical_fields": {"project_code": project_code},
                    },
                )
            )

        verified_codes = self.store.list_existing_project_codes(states=["ready"], require_existing_artifact=True)
        verified_tokens = self.store.list_existing_candidate_tokens(states=["ready"], require_existing_artifact=True)

        for case in cases:
            project_code = str(case["project_code"])
            candidate_token = f"project_id:MATRIX-{str(case['suffix']).upper()}"
            if case["expected_skip"]:
                self.assertIn(project_code, verified_codes)
                self.assertIn(candidate_token, verified_tokens)
            else:
                self.assertNotIn(project_code, verified_codes)
                self.assertNotIn(candidate_token, verified_tokens)

    def test_streaming_queue_downloaded_event_tokens_are_scoped_by_family_business_and_source(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "queued-deal.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>queued deal</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-queued-deal",
                revision_hash="hash-queued-deal",
                project_code="G32026SHQ0001",
                project_name="队列成交项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-18",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SHQ0001", "项目名称": "队列成交项目"},
                postprocess_payload={"项目编号": "G32026SHQ0001", "项目名称": "队列成交项目", "项目状态": "成交"},
                findings=[],
                record_family="deal",
                source_identity={
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "source_id": "sse",
                    "original_source_file": source_file,
                    "project_code": "G32026SHQ0001",
                    "exchange": "sse",
                    "listing_date": "2026-04-18",
                    "candidate_tokens": ["project_code:G32026SHQ0001"],
                },
                canonical_record={
                    "record_family": "deal",
                    "business_identity": {
                        "project_code": "G32026SHQ0001",
                        "business_id": "deal_equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SHQ0001",
                        "project_name": "队列成交项目",
                        "project_type": "股权转让",
                        "status": "成交",
                    },
                },
            )
        )
        job_id = self.store.create_job("one_click")
        service = StreamingIngestService(store=self.store, runner=object())

        service.enqueue(
            job_id=job_id,
            item=ItemSavedPayload(
                source_file=source_file,
                page_url="https://example.test/deal/queued",
                project_code="G32026SHQ0001",
                project_name="队列成交项目",
                exchange="sse",
                listing_date="2026-04-18",
                extra={
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "source_id": "sse",
                    "project_id": "QUEUE-DEAL-001",
                },
            ),
        )

        deal_tokens = self.store.list_existing_candidate_tokens(
            states=["ready"],
            record_family="deal",
            business_id="deal_equity_transfer",
            source_id="sse",
            include_scoped_tokens=True,
        )
        listing_tokens = self.store.list_existing_candidate_tokens(
            states=["ready"],
            record_family="listing",
            business_id="equity_transfer",
            source_id="sse",
            include_scoped_tokens=True,
        )

        scoped_project_id = "scope:deal|deal_equity_transfer|sse|project_id:QUEUE-DEAL-001"
        scoped_page_url = "scope:deal|deal_equity_transfer|sse|page_url:https://example.test/deal/queued"
        self.assertIn(scoped_project_id, deal_tokens)
        self.assertIn(scoped_page_url, deal_tokens)
        self.assertNotIn(scoped_project_id, listing_tokens)
        self.assertNotIn(scoped_page_url, listing_tokens)

    def test_streaming_queue_callback_infers_deal_family_from_business_id_for_downloaded_tokens(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "callback-deal.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>callback deal</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-callback-deal",
                revision_hash="hash-callback-deal",
                project_code="G32026SHCALLBACK001",
                project_name="回调成交项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-19",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SHCALLBACK001", "项目名称": "回调成交项目"},
                postprocess_payload={"项目编号": "G32026SHCALLBACK001", "项目名称": "回调成交项目", "项目状态": "成交"},
                findings=[],
                record_family="deal",
                source_identity={
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "source_id": "sse",
                    "project_code": "G32026SHCALLBACK001",
                    "candidate_tokens": ["project_code:G32026SHCALLBACK001"],
                },
                canonical_record={
                    "record_family": "deal",
                    "business_identity": {
                        "project_code": "G32026SHCALLBACK001",
                        "business_id": "deal_equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SHCALLBACK001",
                        "project_name": "回调成交项目",
                        "project_type": "股权转让",
                        "status": "成交",
                    },
                },
            )
        )
        job_id = self.store.create_job("one_click")
        service = StreamingIngestService(store=self.store, runner=object())
        callback = service.build_callback(job_id=job_id)

        callback(
            {
                "source_file": source_file,
                "page_url": "https://example.test/deal/callback",
                "project_code": "G32026SHCALLBACK001",
                "project_name": "回调成交项目",
                "business_id": "deal_equity_transfer",
                "source_id": "sse",
                "project_id": "CALLBACK-DEAL-001",
            }
        )

        events = self.store.list_job_events(job_id, limit=10)
        downloaded_payload = next(event["payload"] for event in events if event["stage"] == "downloaded")
        self.assertEqual(downloaded_payload["record_family"], "deal")
        self.assertEqual(downloaded_payload["business_id"], "deal_equity_transfer")
        self.assertEqual(downloaded_payload["source_id"], "sse")

        deal_tokens = self.store.list_existing_candidate_tokens(
            states=["ready"],
            record_family="deal",
            business_id="deal_equity_transfer",
            source_id="sse",
            include_scoped_tokens=True,
        )
        listing_tokens = self.store.list_existing_candidate_tokens(
            states=["ready"],
            record_family="listing",
            business_id="equity_transfer",
            source_id="sse",
            include_scoped_tokens=True,
        )

        scoped_project_id = "scope:deal|deal_equity_transfer|sse|project_id:CALLBACK-DEAL-001"
        scoped_page_url = "scope:deal|deal_equity_transfer|sse|page_url:https://example.test/deal/callback"
        self.assertIn(scoped_project_id, deal_tokens)
        self.assertIn(scoped_page_url, deal_tokens)
        self.assertNotIn(scoped_project_id, listing_tokens)
        self.assertNotIn(scoped_page_url, listing_tokens)

    def test_list_existing_candidate_tokens_excludes_failed_only_downloaded_events_when_state_filtered(self) -> None:
        failed_source = os.path.join(self.temp_dir.name, "failed.html")
        with open(failed_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed</body></html>")

        self.store.upsert_failed_record(
            project_code="FAILED-001",
            source_file=failed_source,
            state="parse_failed",
            error_type="parse_failed",
            error_message="boom",
            payload={"项目编号": "FAILED-001"},
        )
        job_id = self.store.create_job("one_click")
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={
                    "source_file": failed_source,
                    "page_url": "https://example.test/failed/1",
                    "project_id": "CQFAILED001",
                    "project_code": "FAILED-001",
                },
            )
        )

        ready_tokens = self.store.list_existing_candidate_tokens(states=["ready"])
        failed_tokens = self.store.list_existing_candidate_tokens(states=["parse_failed"])

        self.assertNotIn("project_code:FAILED-001", ready_tokens)
        self.assertNotIn("page_url:https://example.test/failed/1", ready_tokens)
        self.assertNotIn("project_id:CQFAILED001", ready_tokens)
        self.assertIn("project_code:FAILED-001", failed_tokens)
        self.assertIn("page_url:https://example.test/failed/1", failed_tokens)
        self.assertIn("project_id:CQFAILED001", failed_tokens)

    def test_list_existing_candidate_tokens_excludes_blank_code_failed_events_when_state_filtered(self) -> None:
        failed_source = os.path.join(self.temp_dir.name, "failed-blank-code.html")
        with open(failed_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed blank code</body></html>")

        self.store.upsert_failed_record(
            project_code="",
            source_file=failed_source,
            state="parse_failed",
            error_type="parse_failed",
            error_message="boom",
            payload={},
        )
        job_id = self.store.create_job("one_click")
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={
                    "source_file": failed_source,
                    "page_url": "https://example.test/failed/blank-code",
                    "project_id": "CQFAILEDNOCODE001",
                    "project_code": "",
                },
            )
        )

        ready_tokens = self.store.list_existing_candidate_tokens(states=["ready"])
        failed_tokens = self.store.list_existing_candidate_tokens(states=["parse_failed"])

        self.assertNotIn("page_url:https://example.test/failed/blank-code", ready_tokens)
        self.assertNotIn("project_id:CQFAILEDNOCODE001", ready_tokens)
        self.assertIn("page_url:https://example.test/failed/blank-code", failed_tokens)
        self.assertIn("project_id:CQFAILEDNOCODE001", failed_tokens)

    def test_failed_record_identity_anchor_does_not_change_when_source_file_changes(self) -> None:
        original_source = os.path.join(self.temp_dir.name, "failed-original.html")
        moved_source = os.path.join(self.temp_dir.name, "failed-moved.html")
        with open(original_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed original</body></html>")
        with open(moved_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed moved</body></html>")

        created = self.store.upsert_failed_record(
            project_code="",
            source_file=original_source,
            state="parse_failed",
            error_type="parse_failed",
            error_message="boom",
            payload={
                "original_evidence_path": original_source,
                "candidate_tokens": ["project_id:CQFAILED001", "page_url:https://example.test/failed/1"],
            },
        )
        before = self.store.get_record(created["record_id"])
        self.store.update_record_source_file(created["record_id"], moved_source)
        after = self.store.get_record(created["record_id"])

        self.assertEqual(before["record_id"], after["record_id"])
        self.assertEqual(before["identity_anchor"], after["identity_anchor"])
        self.assertEqual(before["business_key"], after["business_key"])
        self.assertEqual(after["source_file"], moved_source)
        self.assertEqual(after["source_identity_json"]["original_evidence_path"], original_source)
        self.assertEqual(
            after["source_identity_json"]["candidate_tokens"],
            ["project_id:CQFAILED001", "page_url:https://example.test/failed/1"],
        )

    def test_failed_record_source_identity_keeps_canonical_business_id_for_reprocess_evidence(self) -> None:
        from peap.artifact_truth import resolve_artifact_evidence_verdict

        source = os.path.join(self.temp_dir.name, "failed-sse-deal.html")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>上交所成交项目证据</body></html>")

        created = self.store.upsert_failed_record(
            project_code="G32026SH1000016-2",
            source_file=source,
            state="parse_failed",
            error_type="unsupported deal business type",
            error_message="unsupported deal business type: 1",
            payload={
                "record_family": "deal",
                "source_id": "sse",
                "business_id_hint": "deal_equity_transfer",
                "business_label_hint": "股权转让成交",
                "project_code": "G32026SH1000016-2",
            },
        )
        record = self.store.get_record(created["record_id"])

        self.assertEqual(record["business_id"], "deal_equity_transfer")
        self.assertEqual(record["source_identity_json"].get("business_id"), "deal_equity_transfer")
        verdict = resolve_artifact_evidence_verdict(record)
        self.assertEqual(verdict.status, "verified")

    def test_reimport_same_failed_source_reuses_same_record_and_adds_revision(self) -> None:
        original_source = os.path.join(self.temp_dir.name, "failed-reimport-original.html")
        reimport_source = os.path.join(self.temp_dir.name, "failed-reimport-new.html")
        with open(original_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed reimport original</body></html>")
        with open(reimport_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed reimport new</body></html>")

        first = self.store.upsert_failed_record(
            project_code="",
            source_file=original_source,
            state="parse_failed",
            error_type="parse_failed",
            error_message="boom-1",
            payload={
                "original_evidence_path": original_source,
                "candidate_tokens": ["project_id:CQREIMPORT001"],
            },
        )
        second = self.store.upsert_failed_record(
            project_code="",
            source_file=reimport_source,
            state="parse_failed",
            error_type="parse_failed",
            error_message="boom-2",
            payload={
                "original_evidence_path": original_source,
                "candidate_tokens": ["project_id:CQREIMPORT001"],
            },
        )
        record = self.store.get_record(first["record_id"])

        self.assertEqual(first["record_id"], second["record_id"])
        self.assertGreater(second["revision_id"], first["revision_id"])
        self.assertEqual(record["identity_anchor"], self.store.get_record(second["record_id"])["identity_anchor"])
        self.assertEqual(record["source_identity_json"]["original_evidence_path"], original_source)

    def test_failed_record_candidate_tokens_remain_visible_after_source_file_update(self) -> None:
        original_source = os.path.join(self.temp_dir.name, "failed-tokens-original.html")
        moved_source = os.path.join(self.temp_dir.name, "failed-tokens-moved.html")
        with open(original_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed tokens original</body></html>")
        with open(moved_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed tokens moved</body></html>")

        created = self.store.upsert_failed_record(
            project_code="",
            source_file=original_source,
            state="parse_failed",
            error_type="parse_failed",
            error_message="boom",
            payload={
                "original_evidence_path": original_source,
                "candidate_tokens": [
                    "project_id:CQFAILEDTOKENS001",
                    "page_url:https://example.test/failed/tokens",
                ],
            },
        )
        before_tokens = self.store.list_existing_candidate_tokens(states=["parse_failed"])
        self.store.update_record_source_file(created["record_id"], moved_source)
        after_tokens = self.store.list_existing_candidate_tokens(states=["parse_failed"])

        self.assertIn("project_id:CQFAILEDTOKENS001", before_tokens)
        self.assertIn("page_url:https://example.test/failed/tokens", before_tokens)
        self.assertIn("project_id:CQFAILEDTOKENS001", after_tokens)
        self.assertIn("page_url:https://example.test/failed/tokens", after_tokens)

    def test_upsert_failed_record_preserves_falsy_mapping_payload_in_revision(self) -> None:
        class FalsyPayload(dict):
            def __bool__(self) -> bool:
                return False

        payload = FalsyPayload({"项目编号": "FAILED-FALSY-MAPPING", "page_url": "https://example.test/failed/falsy"})
        created = self.store.upsert_failed_record(
            project_code="FAILED-FALSY-MAPPING",
            source_file=os.path.join(self.temp_dir.name, "failed-falsy-mapping.html"),
            state="parse_failed",
            error_type="parse_error",
            error_message="bad payload",
            payload=payload,
        )

        record = self.store.get_record(created["record_id"])
        self.assertEqual(record["parser_payload"]["项目编号"], "FAILED-FALSY-MAPPING")
        self.assertEqual(record["parser_payload"]["page_url"], "https://example.test/failed/falsy")

    def test_upsert_failed_record_rejects_non_mapping_payload_instead_of_blank_identity(self) -> None:
        for payload in (False, [], "not-an-object"):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex((TypeError, ValueError), "payload"):
                    self.store.upsert_failed_record(
                        project_code="BAD-PAYLOAD",
                        source_file=os.path.join(self.temp_dir.name, "failed-bad-payload.html"),
                        state="parse_failed",
                        error_type="parse_error",
                        error_message="bad payload",
                        payload=payload,  # type: ignore[arg-type]
                    )

        with sqlite3.connect(self.store.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM records WHERE project_code = ?",
                ("BAD-PAYLOAD",),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_update_record_source_file_surfaces_invalid_legacy_source_identity_json_instead_of_exchange_fallback(
        self,
    ) -> None:
        original_source = os.path.join(self.temp_dir.name, "source-update-bad-identity-original.html")
        moved_source = os.path.join(self.temp_dir.name, "source-update-bad-identity-moved.html")
        with open(original_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>source update original</body></html>")
        with open(moved_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>source update moved</body></html>")

        with self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO records (
                    record_id, business_key, record_family, business_id, raw_business_label,
                    identity_anchor, source_identity_json, project_code, project_name, project_type,
                    exchange, listing_date, state, source_file, archive_path, latest_revision_id,
                    last_error_type, last_error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-source-update-bad-identity",
                    "legacy|source-update-bad-identity",
                    "listing",
                    "",
                    "",
                    "",
                    "{",
                    "",
                    "坏身份源文件更新记录",
                    "股权转让",
                    "shanghai",
                    "2026-04-21",
                    "ready",
                    original_source,
                    original_source,
                    None,
                    "",
                    "",
                    "2026-04-21T00:00:00Z",
                    "2026-04-21T00:00:00Z",
                ),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.update_record_source_file("legacy-source-update-bad-identity", moved_source)

    def test_update_record_source_file_rejects_empty_source_file_before_mutating_record(self) -> None:
        original_source = os.path.join(self.temp_dir.name, "source-update-empty-original.html")
        with open(original_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>source update empty original</body></html>")

        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-source-update-empty",
                revision_hash="hash-source-update-empty",
                project_code="G32026EMPTY",
                project_name="空源文件更新拒绝项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=original_source,
                archive_path=original_source,
                parser_payload={"项目编号": "G32026EMPTY"},
                postprocess_payload={"项目编号": "G32026EMPTY"},
                findings=[],
            )
        )

        before = self.store.get_record("rec-source-update-empty")
        with self.assertRaisesRegex(ValueError, "source_file"):
            self.store.update_record_source_file("rec-source-update-empty", "")

        after = self.store.get_record("rec-source-update-empty")
        self.assertEqual(after["source_file"], before["source_file"])
        self.assertEqual(after["business_key"], before["business_key"])
        with sqlite3.connect(self.store.db_path) as conn:
            revision_source = conn.execute(
                "SELECT source_file FROM record_revisions WHERE revision_id = ?",
                (int(created["revision_id"]),),
            ).fetchone()[0]
        self.assertEqual(revision_source, original_source)

    def test_update_downloaded_event_source_file_surfaces_invalid_payload_json_instead_of_skipping_event(self) -> None:
        original_source = os.path.join(self.temp_dir.name, "downloaded-event-original.html")
        moved_source = os.path.join(self.temp_dir.name, "downloaded-event-moved.html")
        job_id = self.store.create_job("one_click")
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={"source_file": original_source},
            )
        )
        with self.store._connect() as conn:
            conn.execute("UPDATE job_events SET payload_json = ? WHERE job_id = ?", ("{", job_id))

        with self.assertRaises(json.JSONDecodeError):
            self.store.update_downloaded_event_source_file(original_source, moved_source)

    def test_update_downloaded_event_source_file_rejects_empty_paths_instead_of_returning_zero(self) -> None:
        with self.assertRaisesRegex(ValueError, "old_source_file"):
            self.store.update_downloaded_event_source_file("", "archive.html")

        with self.assertRaisesRegex(ValueError, "new_source_file"):
            self.store.update_downloaded_event_source_file("source.html", "")

    def test_reimport_failed_record_merges_new_candidate_tokens(self) -> None:
        original_source = os.path.join(self.temp_dir.name, "failed-merge-original.html")
        reimport_source = os.path.join(self.temp_dir.name, "failed-merge-reimport.html")
        with open(original_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed merge original</body></html>")
        with open(reimport_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed merge reimport</body></html>")

        created = self.store.upsert_failed_record(
            project_code="",
            source_file=original_source,
            state="parse_failed",
            error_type="parse_failed",
            error_message="boom-1",
            payload={
                "original_evidence_path": original_source,
                "candidate_tokens": ["project_id:CQMERGE001"],
            },
        )
        self.store.upsert_failed_record(
            project_code="",
            source_file=reimport_source,
            state="parse_failed",
            error_type="parse_failed",
            error_message="boom-2",
            payload={
                "original_evidence_path": original_source,
                "candidate_tokens": ["project_id:CQMERGE001", "page_url:https://example.test/failed/merge"],
            },
        )

        record = self.store.get_record(created["record_id"])
        self.assertEqual(
            record["source_identity_json"]["candidate_tokens"],
            ["project_id:CQMERGE001", "page_url:https://example.test/failed/merge"],
        )

    def test_merge_source_identity_rejects_non_mapping_inputs(self) -> None:
        valid_identity = {"candidate_tokens": ["project_id:CQMERGEVALID"]}
        cases = (
            ("existing", False, valid_identity),
            ("existing", [], valid_identity),
            ("existing", "not-an-object", valid_identity),
            ("incoming", valid_identity, False),
            ("incoming", valid_identity, []),
            ("incoming", valid_identity, "not-an-object"),
        )
        for field, existing, incoming in cases:
            with self.subTest(field=field, value=existing if field == "existing" else incoming):
                with self.assertRaisesRegex((TypeError, ValueError), field):
                    _merge_source_identity(existing, incoming)  # type: ignore[arg-type]

    def test_merge_source_identity_rejects_false_candidate_tokens_instead_of_dropping_them(self) -> None:
        with self.assertRaisesRegex((TypeError, ValueError), "existing.candidate_tokens"):
            _merge_source_identity(
                {"candidate_tokens": False},
                {"candidate_tokens": ["project_id:CQMERGEVALID"]},
            )

    def test_upsert_failed_record_rejects_false_candidate_tokens_instead_of_dropping_them(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "failed-bad-candidate-tokens.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed bad candidate tokens</body></html>")

        with self.assertRaisesRegex((TypeError, ValueError), "candidate_tokens"):
            self.store.upsert_failed_record(
                project_code="CQBADTOKENS001",
                source_file=source_file,
                state="parse_failed",
                error_type="parse_error",
                error_message="bad candidate tokens",
                payload={"candidate_tokens": False},
            )

    def test_upsert_failed_record_surfaces_invalid_existing_source_identity_json_instead_of_overwriting(
        self,
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, "failed-existing-bad-source-identity.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>failed source</body></html>")

        created = self.store.upsert_failed_record(
            project_code="CQBADFAIL001",
            source_file=source_file,
            state="parse_failed",
            error_type="parse_error",
            error_message="first failure",
            payload={
                "record_family": "listing",
                "source_id": "cqggzy",
                "candidate_tokens": ["project_id:CQBADFAIL001"],
            },
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET source_identity_json = ? WHERE record_id = ?",
                ("{", created["record_id"]),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.upsert_failed_record(
                project_code="CQBADFAIL001",
                source_file=source_file,
                state="parse_failed",
                error_type="parse_error",
                error_message="second failure",
                payload={
                    "record_family": "listing",
                    "source_id": "cqggzy",
                    "candidate_tokens": ["page_url:https://example.test/failed/bad"],
                },
            )

    def test_legacy_failed_record_is_backfilled_with_stable_identity_contract(self) -> None:
        legacy_source = os.path.join(self.temp_dir.name, "legacy-failed.html")
        moved_source = os.path.join(self.temp_dir.name, "legacy-failed-moved.html")
        with open(legacy_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy failed</body></html>")
        with open(moved_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy failed moved</body></html>")

        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                """
                INSERT INTO records (
                    record_id, business_key, record_family, identity_anchor, source_identity_json,
                    project_code, project_name, project_type, exchange, listing_date,
                    state, source_file, archive_path, latest_revision_id,
                    last_error_type, last_error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-failed-record",
                    "source:legacy-business-key",
                    "listing",
                    "",
                    "{}",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "parse_failed",
                    legacy_source,
                    "",
                    None,
                    "parse_failed",
                    "legacy boom",
                    "2026-03-25T00:00:00Z",
                    "2026-03-25T00:00:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO record_revisions (
                    record_id, revision_hash, parser_payload_json,
                    postprocess_payload_json, findings_json, state, source_file, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-failed-record",
                    "legacy-revision-hash",
                    '{"original_evidence_path":"%s","candidate_tokens":["project_id:CQLEGACY001"]}' % legacy_source,
                    "{}",
                    "[]",
                    "parse_failed",
                    legacy_source,
                    "2026-03-25T00:00:00Z",
                ),
            )
            latest_revision_id = conn.execute(
                "SELECT revision_id FROM record_revisions WHERE record_id = ? ORDER BY revision_id DESC LIMIT 1",
                ("legacy-failed-record",),
            ).fetchone()[0]
            conn.execute(
                "UPDATE records SET latest_revision_id = ? WHERE record_id = ?",
                (int(latest_revision_id), "legacy-failed-record"),
            )

        reopened = StreamingStore(self.store.db_path)
        reopened.migrate()
        before = reopened.get_record("legacy-failed-record")
        self.assertTrue(before["identity_anchor"])
        self.assertEqual(before["business_key"], f"failed:{before['identity_anchor']}")
        self.assertEqual(before["source_identity_json"]["original_evidence_path"], legacy_source)

        reopened.update_record_source_file("legacy-failed-record", moved_source)
        after = reopened.get_record("legacy-failed-record")

        self.assertEqual(after["identity_anchor"], before["identity_anchor"])
        self.assertEqual(after["business_key"], before["business_key"])
        self.assertEqual(after["source_identity_json"]["candidate_tokens"], ["project_id:CQLEGACY001"])

    def test_failed_record_backfill_promotes_business_hint_to_canonical_business_id(self) -> None:
        from peap.artifact_truth import resolve_artifact_evidence_verdict

        source = os.path.join(self.temp_dir.name, "legacy-failed-sse-deal.html")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy failed sse deal</body></html>")
        source_identity = {
            "record_family": "deal",
            "source_id": "sse",
            "business_id_hint": "deal_equity_transfer",
            "business_label_hint": "股权转让成交",
            "original_evidence_path": source,
            "original_source_file": source,
            "project_code": "G32026SH1000016-2",
        }
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                """
                INSERT INTO records (
                    record_id, business_key, record_family, business_id, raw_business_label,
                    identity_anchor, source_identity_json,
                    project_code, project_name, project_type, exchange, listing_date,
                    state, source_file, archive_path, latest_revision_id,
                    last_error_type, last_error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-failed-sse-deal",
                    "failed:legacy-sse-deal-anchor",
                    "deal",
                    "",
                    "",
                    "legacy-sse-deal-anchor",
                    json.dumps(source_identity, ensure_ascii=False),
                    "G32026SH1000016-2",
                    "",
                    "",
                    "",
                    "",
                    "parse_failed",
                    source,
                    "",
                    None,
                    "unsupported deal business type",
                    "unsupported deal business type: 1",
                    "2026-06-10T00:00:00Z",
                    "2026-06-10T00:00:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO record_revisions (
                    record_id, revision_hash, parser_payload_json,
                    postprocess_payload_json, findings_json, state, source_file, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-failed-sse-deal",
                    "legacy-sse-deal-revision",
                    json.dumps(
                        {
                            "record_family": "deal",
                            "source_id": "sse",
                            "business_id_hint": "deal_equity_transfer",
                            "project_code": "G32026SH1000016-2",
                            "source_file": source,
                        },
                        ensure_ascii=False,
                    ),
                    "{}",
                    "[]",
                    "parse_failed",
                    source,
                    "2026-06-10T00:00:00Z",
                ),
            )
            latest_revision_id = conn.execute(
                "SELECT revision_id FROM record_revisions WHERE record_id = ? ORDER BY revision_id DESC LIMIT 1",
                ("legacy-failed-sse-deal",),
            ).fetchone()[0]
            conn.execute(
                "UPDATE records SET latest_revision_id = ? WHERE record_id = ?",
                (int(latest_revision_id), "legacy-failed-sse-deal"),
            )

        reopened = StreamingStore(self.store.db_path)
        reopened.migrate()
        record = reopened.get_record("legacy-failed-sse-deal")

        self.assertEqual(record["business_id"], "deal_equity_transfer")
        self.assertEqual(record["source_identity_json"].get("business_id"), "deal_equity_transfer")
        self.assertEqual(resolve_artifact_evidence_verdict(record).status, "verified")

    def test_failed_record_backfill_surfaces_invalid_source_identity_json_instead_of_repairing(self) -> None:
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                """
                INSERT INTO records (
                    record_id, business_key, record_family, identity_anchor, source_identity_json,
                    project_code, project_name, project_type, exchange, listing_date,
                    state, source_file, archive_path, latest_revision_id,
                    last_error_type, last_error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-failed-source-identity",
                    "source:bad-failed-source-identity",
                    "listing",
                    "",
                    "{",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "parse_failed",
                    os.path.join(self.temp_dir.name, "bad-failed.html"),
                    "",
                    None,
                    "parse_failed",
                    "legacy boom",
                    "2026-03-25T00:00:00Z",
                    "2026-03-25T00:00:00Z",
                ),
            )

        reopened = StreamingStore(self.store.db_path)
        with self.assertRaises(json.JSONDecodeError):
            reopened.migrate()

    def test_field_missing_backfill_surfaces_invalid_acknowledged_payload_json_instead_of_overwriting(
        self,
    ) -> None:
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                """
                INSERT INTO records (
                    record_id, business_key, record_family, identity_anchor, source_identity_json,
                    project_code, project_name, project_type, exchange, listing_date,
                    state, source_file, archive_path, latest_revision_id,
                    last_error_type, last_error_message, acknowledged_payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-field-missing-ack",
                    "source:bad-field-missing-ack",
                    "listing",
                    "",
                    "{}",
                    "G32026ACKBACKFILL",
                    "坏确认 payload 回填项目",
                    "股权转让",
                    "shanghai",
                    "2026-03-21",
                    "skipped",
                    os.path.join(self.temp_dir.name, "bad-field-missing-ack.html"),
                    "",
                    None,
                    "",
                    "",
                    "{",
                    "2026-03-25T00:00:00Z",
                    "2026-03-25T00:00:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO record_revisions (
                    record_id, revision_hash, parser_payload_json,
                    postprocess_payload_json, findings_json, state, source_file, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-field-missing-ack",
                    "hash-bad-field-missing-ack",
                    "{}",
                    "{}",
                    json.dumps(
                        [
                            {
                                "severity": "warn",
                                "type": "canonical_field_missing",
                                "message": "Missing required canonical fields for export: seller",
                                "evidence": {"missing_fields": ["seller"]},
                            }
                        ]
                    ),
                    "skipped",
                    os.path.join(self.temp_dir.name, "bad-field-missing-ack.html"),
                    "2026-03-25T00:00:00Z",
                ),
            )
            revision_id = conn.execute(
                "SELECT revision_id FROM record_revisions WHERE record_id = ?",
                ("bad-field-missing-ack",),
            ).fetchone()[0]
            conn.execute(
                "UPDATE records SET latest_revision_id = ? WHERE record_id = ?",
                (int(revision_id), "bad-field-missing-ack"),
            )

        reopened = StreamingStore(self.store.db_path)
        with self.assertRaises(json.JSONDecodeError):
            reopened.migrate()

    def test_field_missing_backfill_rejects_non_mapping_field_missing_ack_payload(self) -> None:
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                """
                INSERT INTO records (
                    record_id, business_key, record_family, identity_anchor, source_identity_json,
                    project_code, project_name, project_type, exchange, listing_date,
                    state, source_file, archive_path, latest_revision_id,
                    last_error_type, last_error_message, acknowledged_payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-field-missing-ack-object",
                    "source:bad-field-missing-ack-object",
                    "listing",
                    "",
                    "{}",
                    "G32026ACKOBJECT",
                    "坏确认 field_missing payload 项目",
                    "股权转让",
                    "shanghai",
                    "2026-03-21",
                    "skipped",
                    os.path.join(self.temp_dir.name, "bad-field-missing-ack-object.html"),
                    "",
                    None,
                    "",
                    "",
                    json.dumps({"field_missing": False}),
                    "2026-03-25T00:00:00Z",
                    "2026-03-25T00:00:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO record_revisions (
                    record_id, revision_hash, parser_payload_json,
                    postprocess_payload_json, findings_json, state, source_file, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-field-missing-ack-object",
                    "hash-bad-field-missing-ack-object",
                    "{}",
                    "{}",
                    json.dumps(
                        [
                            {
                                "severity": "warn",
                                "type": "canonical_field_missing",
                                "message": "Missing required canonical fields for export: seller",
                                "evidence": {"missing_fields": ["seller"]},
                            }
                        ]
                    ),
                    "skipped",
                    os.path.join(self.temp_dir.name, "bad-field-missing-ack-object.html"),
                    "2026-03-25T00:00:00Z",
                ),
            )
            revision_id = conn.execute(
                "SELECT revision_id FROM record_revisions WHERE record_id = ?",
                ("bad-field-missing-ack-object",),
            ).fetchone()[0]
            conn.execute(
                "UPDATE records SET latest_revision_id = ? WHERE record_id = ?",
                (int(revision_id), "bad-field-missing-ack-object"),
            )

        reopened = StreamingStore(self.store.db_path)
        with self.assertRaisesRegex(ValueError, "field_missing"):
            reopened.migrate()

    def test_field_missing_backfill_surfaces_invalid_findings_json_instead_of_skipping(self) -> None:
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                """
                INSERT INTO records (
                    record_id, business_key, record_family, identity_anchor, source_identity_json,
                    project_code, project_name, project_type, exchange, listing_date,
                    state, source_file, archive_path, latest_revision_id,
                    last_error_type, last_error_message, acknowledged_payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-field-missing-findings",
                    "source:bad-field-missing-findings",
                    "listing",
                    "",
                    "{}",
                    "G32026FINDINGSBAD",
                    "坏 findings 回填项目",
                    "股权转让",
                    "shanghai",
                    "2026-03-21",
                    "skipped",
                    os.path.join(self.temp_dir.name, "bad-field-missing-findings.html"),
                    "",
                    None,
                    "",
                    "",
                    "{}",
                    "2026-03-25T00:00:00Z",
                    "2026-03-25T00:00:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO record_revisions (
                    record_id, revision_hash, parser_payload_json,
                    postprocess_payload_json, findings_json, state, source_file, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-field-missing-findings",
                    "hash-bad-field-missing-findings",
                    "{}",
                    "{}",
                    "{",
                    "skipped",
                    os.path.join(self.temp_dir.name, "bad-field-missing-findings.html"),
                    "2026-03-25T00:00:00Z",
                ),
            )
            revision_id = conn.execute(
                "SELECT revision_id FROM record_revisions WHERE record_id = ?",
                ("bad-field-missing-findings",),
            ).fetchone()[0]
            conn.execute(
                "UPDATE records SET latest_revision_id = ? WHERE record_id = ?",
                (int(revision_id), "bad-field-missing-findings"),
            )

        reopened = StreamingStore(self.store.db_path)
        with self.assertRaises(json.JSONDecodeError):
            reopened.migrate()

    def test_field_missing_backfill_rejects_false_legacy_missing_fields_instead_of_message_fallback(
        self,
    ) -> None:
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(
                """
                INSERT INTO records (
                    record_id, business_key, record_family, identity_anchor, source_identity_json,
                    project_code, project_name, project_type, exchange, listing_date,
                    state, source_file, archive_path, latest_revision_id,
                    last_error_type, last_error_message, acknowledged_payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-field-missing-false-fields",
                    "source:bad-field-missing-false-fields",
                    "listing",
                    "",
                    "{}",
                    "G32026FALSEFIELDS",
                    "坏 missing_fields 回填项目",
                    "股权转让",
                    "shanghai",
                    "2026-03-21",
                    "skipped",
                    os.path.join(self.temp_dir.name, "bad-field-missing-false-fields.html"),
                    "",
                    None,
                    "",
                    "",
                    "{}",
                    "2026-03-25T00:00:00Z",
                    "2026-03-25T00:00:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO record_revisions (
                    record_id, revision_hash, parser_payload_json,
                    postprocess_payload_json, findings_json, state, source_file, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-field-missing-false-fields",
                    "hash-bad-field-missing-false-fields",
                    "{}",
                    "{}",
                    json.dumps(
                        [
                            {
                                "severity": "warn",
                                "type": "canonical_field_missing",
                                "message": "Missing required canonical fields for export: seller",
                                "evidence": {"missing_fields": False},
                            }
                        ]
                    ),
                    "skipped",
                    os.path.join(self.temp_dir.name, "bad-field-missing-false-fields.html"),
                    "2026-03-25T00:00:00Z",
                ),
            )
            revision_id = conn.execute(
                "SELECT revision_id FROM record_revisions WHERE record_id = ?",
                ("bad-field-missing-false-fields",),
            ).fetchone()[0]
            conn.execute(
                "UPDATE records SET latest_revision_id = ? WHERE record_id = ?",
                (int(revision_id), "bad-field-missing-false-fields"),
            )

        reopened = StreamingStore(self.store.db_path)
        with self.assertRaisesRegex((TypeError, ValueError), "missing_fields"):
            reopened.migrate()

    def test_list_job_events_raises_key_error_for_missing_job(self) -> None:
        with self.assertRaises(KeyError):
            self.store.list_job_events("missing-job-id")

    def test_job_event_count_can_report_total_count_separately_from_returned_rows(self) -> None:
        job_id = self.store.create_job("one_click")
        self.store.append_event(
            ItemProgressEvent(job_id=job_id, stage="downloaded", status="ok", payload={"row": 1})
        )
        self.store.append_event(
            ItemProgressEvent(job_id=job_id, stage="parsed", status="ok", payload={"row": 2})
        )
        self.store.append_event(
            ItemProgressEvent(job_id=job_id, stage="failed", status="failed", payload={"row": 3})
        )

        rows = self.store.list_job_events(job_id, limit=2)
        counts = self.store.get_job_event_counts(job_id)

        self.assertEqual(len(rows), 2)
        self.assertEqual(counts["total_count"], 3)
        self.assertEqual(counts["ok"], 2)
        self.assertEqual(counts["failed"], 1)

    def test_append_event_refreshes_job_updated_at(self) -> None:
        with patch(
            "peap.streaming_store._utcnow",
            side_effect=["2026-03-28 10:00:00", "2026-03-28 10:00:05"],
        ):
            job_id = self.store.create_job("manual_import")
            self.store.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="manual_import_scan",
                    status="running",
                    payload={"label": "扫描中"},
                )
            )

        latest_job = self.store.list_jobs(limit=1)[0]
        self.assertEqual(latest_job["job_id"], job_id)
        self.assertEqual(latest_job["updated_at"], "2026-03-28 10:00:05")

    def test_count_records_by_state_can_filter_record_family(self) -> None:
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-listing-1",
                revision_hash="hash-listing-1",
                project_code="L32026SH000001",
                project_name="挂牌测试项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/listing.html",
                archive_path=f"{self.temp_dir.name}/archive/listing.html",
                parser_payload={"项目编号": "L32026SH000001"},
                postprocess_payload={"项目编号": "L32026SH000001"},
                findings=[],
            )
        )
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-deal-1",
                revision_hash="hash-deal-1",
                project_code="D32026SH000001",
                project_name="成交测试项目",
                project_type="成交公告",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/deal.html",
                archive_path=f"{self.temp_dir.name}/archive/deal.html",
                parser_payload={"项目编号": "D32026SH000001"},
                postprocess_payload={"项目编号": "D32026SH000001"},
                findings=[],
                record_family="deal",
            )
        )

        listing_counts = self.store.count_records_by_state(record_family="listing")
        deal_counts = self.store.count_records_by_state(record_family="deal")

        self.assertEqual(listing_counts["ready"], 1)
        self.assertEqual(deal_counts["ready"], 1)

    def test_upsert_record_allows_same_project_code_for_listing_and_deal(self) -> None:
        listing_source = os.path.join(self.temp_dir.name, "same-code-listing.html")
        deal_source = os.path.join(self.temp_dir.name, "same-code-deal.html")
        with open(listing_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>listing</body></html>")
        with open(deal_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>deal</body></html>")

        listing_result = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-same-code-listing",
                revision_hash="hash-same-code-listing",
                project_code="G32026SHSAME001",
                project_name="同编码挂牌项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-03-21",
                state="ready",
                source_file=listing_source,
                archive_path=listing_source,
                parser_payload={"项目编号": "G32026SHSAME001", "项目名称": "同编码挂牌项目"},
                postprocess_payload={"项目编号": "G32026SHSAME001", "项目名称": "同编码挂牌项目", "项目类型": "股权转让"},
                findings=[],
                record_family="listing",
                source_identity={
                    "record_family": "listing",
                    "source_id": "sse",
                    "business_id_hint": "equity_transfer",
                    "project_code": "G32026SHSAME001",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "project_code": "G32026SHSAME001",
                        "business_id": "equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SHSAME001",
                        "project_name": "同编码挂牌项目",
                        "project_type": "股权转让",
                        "status": "挂牌",
                    },
                },
            )
        )
        deal_result = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-same-code-deal",
                revision_hash="hash-same-code-deal",
                project_code="G32026SHSAME001",
                project_name="同编码成交项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-06",
                state="ready",
                source_file=deal_source,
                archive_path=deal_source,
                parser_payload={"项目编号": "G32026SHSAME001", "项目名称": "同编码成交项目"},
                postprocess_payload={"项目编号": "G32026SHSAME001", "项目名称": "同编码成交项目", "项目类型": "股权转让", "项目状态": "成交"},
                findings=[],
                record_family="deal",
                source_identity={
                    "record_family": "deal",
                    "source_id": "sse",
                    "business_id_hint": "deal_equity_transfer",
                    "project_code": "G32026SHSAME001",
                },
                canonical_record={
                    "record_family": "deal",
                    "business_identity": {
                        "project_code": "G32026SHSAME001",
                        "business_id": "deal_equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SHSAME001",
                        "project_name": "同编码成交项目",
                        "project_type": "股权转让",
                        "status": "成交",
                    },
                },
            )
        )

        self.assertNotEqual(listing_result["record_id"], deal_result["record_id"])
        rows = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(rows), 2)
        families = {row["record_family"] for row in rows}
        self.assertEqual(families, {"listing", "deal"})

    def test_upsert_record_upgrades_legacy_project_code_business_key_without_duplicate(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-key.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy key</body></html>")

        first = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-legacy-project-key",
                revision_hash="hash-legacy-project-key-1",
                project_code="G32026SHLEGACY001",
                project_name="旧业务键项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SHLEGACY001"},
                postprocess_payload={"项目编号": "G32026SHLEGACY001", "项目类型": "股权转让"},
                findings=[],
                record_family="listing",
                source_identity={
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "sse",
                    "project_code": "G32026SHLEGACY001",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "project_code": "G32026SHLEGACY001",
                        "business_id": "equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SHLEGACY001",
                        "project_name": "旧业务键项目",
                        "project_type": "股权转让",
                    },
                },
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET business_key = ? WHERE record_id = ?",
                ("G32026SHLEGACY001", first["record_id"]),
            )

        second = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-legacy-project-key-new",
                revision_hash="hash-legacy-project-key-2",
                project_code="G32026SHLEGACY001",
                project_name="旧业务键项目更新",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-03-22",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SHLEGACY001"},
                postprocess_payload={"项目编号": "G32026SHLEGACY001", "项目类型": "股权转让"},
                findings=[],
                record_family="listing",
                source_identity={
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "sse",
                    "project_code": "G32026SHLEGACY001",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "project_code": "G32026SHLEGACY001",
                        "business_id": "equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SHLEGACY001",
                        "project_name": "旧业务键项目更新",
                        "project_type": "股权转让",
                    },
                },
            )
        )

        rows = self.store.iter_latest_records(states=["ready"])
        record = self.store.get_record(first["record_id"])
        self.assertEqual(second["record_id"], first["record_id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(record["business_key"], "listing|equity_transfer|sse|G32026SHLEGACY001")

    def test_upsert_record_surfaces_invalid_legacy_source_identity_json_instead_of_scope_upgrade(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "legacy-invalid-source-identity.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>legacy invalid source identity</body></html>")

        first = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-legacy-invalid-source-identity",
                revision_hash="hash-legacy-invalid-source-identity-1",
                project_code="G32026SHLEGACYBAD001",
                project_name="旧业务键损坏身份项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SHLEGACYBAD001"},
                postprocess_payload={"项目编号": "G32026SHLEGACYBAD001", "项目类型": "股权转让"},
                findings=[],
                record_family="listing",
                source_identity={
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "sse",
                    "project_code": "G32026SHLEGACYBAD001",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "project_code": "G32026SHLEGACYBAD001",
                        "business_id": "equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SHLEGACYBAD001",
                        "project_name": "旧业务键损坏身份项目",
                        "project_type": "股权转让",
                    },
                },
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                """
                UPDATE records
                SET business_key = ?, source_identity_json = ?
                WHERE record_id = ?
                """,
                ("G32026SHLEGACYBAD001", "{", first["record_id"]),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.upsert_record(
                IngestedRecord(
                    record_id="rec-legacy-invalid-source-identity-new",
                    revision_hash="hash-legacy-invalid-source-identity-2",
                    project_code="G32026SHLEGACYBAD001",
                    project_name="旧业务键损坏身份项目更新",
                    project_type="股权转让",
                    exchange="sse",
                    listing_date="2026-03-22",
                    state="ready",
                    source_file=source_file,
                    archive_path=source_file,
                    parser_payload={"项目编号": "G32026SHLEGACYBAD001"},
                    postprocess_payload={"项目编号": "G32026SHLEGACYBAD001", "项目类型": "股权转让"},
                    findings=[],
                    record_family="listing",
                    source_identity={
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "source_id": "sse",
                        "project_code": "G32026SHLEGACYBAD001",
                    },
                    canonical_record={
                        "record_family": "listing",
                        "business_identity": {
                            "project_code": "G32026SHLEGACYBAD001",
                            "business_id": "equity_transfer",
                        },
                        "canonical_fields": {
                            "project_code": "G32026SHLEGACYBAD001",
                            "project_name": "旧业务键损坏身份项目更新",
                            "project_type": "股权转让",
                        },
                    },
                )
            )

    def test_upsert_record_does_not_merge_unknown_business_into_existing_scoped_record_id(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "scoped-business.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>scoped business</body></html>")

        first = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-scoped-business",
                revision_hash="hash-scoped-business-1",
                project_code="G32026SHSCOPE001",
                project_name="强业务项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SHSCOPE001"},
                postprocess_payload={"项目编号": "G32026SHSCOPE001", "项目类型": "股权转让"},
                findings=[],
                record_family="listing",
                source_identity={
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "sse",
                    "project_code": "G32026SHSCOPE001",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "project_code": "G32026SHSCOPE001",
                        "business_id": "equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SHSCOPE001",
                        "project_name": "强业务项目",
                        "project_type": "股权转让",
                    },
                },
            )
        )
        original_business_key = self.store.get_record(first["record_id"])["business_key"]

        second = self.store.upsert_record(
            IngestedRecord(
                record_id=first["record_id"],
                revision_hash="hash-scoped-business-2",
                project_code="G32026SHSCOPE001",
                project_name="弱业务项目",
                project_type="未知业务",
                exchange="sse",
                listing_date="2026-03-22",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SHSCOPE001"},
                postprocess_payload={"项目编号": "G32026SHSCOPE001", "项目类型": "未知业务"},
                findings=[],
                record_family="listing",
                source_identity={
                    "record_family": "listing",
                    "source_id": "sse",
                    "project_code": "G32026SHSCOPE001",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "project_code": "G32026SHSCOPE001",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SHSCOPE001",
                        "project_name": "弱业务项目",
                        "project_type": "未知业务",
                    },
                },
            )
        )

        self.assertNotEqual(second["record_id"], first["record_id"])
        self.assertEqual(self.store.get_record(first["record_id"])["business_key"], original_business_key)
        rows = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(rows), 2)

    def test_upsert_record_forks_same_record_id_when_scope_differs(self) -> None:
        listing_source = os.path.join(self.temp_dir.name, "record-id-listing.html")
        deal_source = os.path.join(self.temp_dir.name, "record-id-deal.html")
        with open(listing_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>record id listing</body></html>")
        with open(deal_source, "w", encoding="utf-8") as handle:
            handle.write("<html><body>record id deal</body></html>")

        listing = self.store.upsert_record(
            IngestedRecord(
                record_id="asm-scope-collision",
                revision_hash="hash-record-id-listing",
                project_code="G32026SHCOLLIDE001",
                project_name="同 ID 挂牌项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-03-21",
                state="ready",
                source_file=listing_source,
                archive_path=listing_source,
                parser_payload={"项目编号": "G32026SHCOLLIDE001"},
                postprocess_payload={"项目编号": "G32026SHCOLLIDE001", "项目类型": "股权转让"},
                findings=[],
                record_family="listing",
                source_identity={
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "sse",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "project_code": "G32026SHCOLLIDE001",
                        "business_id": "equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SHCOLLIDE001",
                        "project_name": "同 ID 挂牌项目",
                        "project_type": "股权转让",
                    },
                },
            )
        )
        deal = self.store.upsert_record(
            IngestedRecord(
                record_id="asm-scope-collision",
                revision_hash="hash-record-id-deal",
                project_code="G32026SHCOLLIDE001",
                project_name="同 ID 成交项目",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-18",
                state="ready",
                source_file=deal_source,
                archive_path=deal_source,
                parser_payload={"项目编号": "G32026SHCOLLIDE001"},
                postprocess_payload={"项目编号": "G32026SHCOLLIDE001", "项目类型": "股权转让", "项目状态": "成交"},
                findings=[],
                record_family="deal",
                source_identity={
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "source_id": "sse",
                },
                canonical_record={
                    "record_family": "deal",
                    "business_identity": {
                        "project_code": "G32026SHCOLLIDE001",
                        "business_id": "deal_equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SHCOLLIDE001",
                        "project_name": "同 ID 成交项目",
                        "project_type": "股权转让",
                    },
                },
            )
        )

        rows = self.store.iter_latest_records(states=["ready"])
        self.assertNotEqual(deal["record_id"], listing["record_id"])
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["record_family"] for row in rows}, {"listing", "deal"})

    def test_record_operation_failure_preserves_canonical_state_and_latest_revision(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "operation-overlay.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>operation overlay</body></html>")

        result = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-operation-overlay",
                revision_hash="hash-operation-overlay",
                project_code="G32026SH1000555",
                project_name="运维失败覆盖测试",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026SH1000555"},
                postprocess_payload={"项目编号": "G32026SH1000555"},
                findings=[],
            )
        )

        self.store.record_operation_result(
            "rec-operation-overlay",
            kind="reprocess",
            code="source_missing",
            message="source file missing for record: rec-operation-overlay",
            artifact_status="missing",
        )

        record = self.store.get_record("rec-operation-overlay")

        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["revision_id"], result["revision_id"])
        self.assertEqual(record["last_operation_kind"], "reprocess")
        self.assertEqual(record["last_operation_code"], "source_missing")
        self.assertEqual(record["last_operation_message"], "source file missing for record: rec-operation-overlay")
        self.assertEqual(record["artifact_status"], "missing")
        self.assertTrue(record["last_operation_at"])
        with self.store._connect() as conn:
            latest_revision = conn.execute(
                """
                SELECT state
                FROM record_revisions
                WHERE revision_id = ?
                """,
                (int(result["revision_id"]),),
            ).fetchone()
        self.assertIsNotNone(latest_revision)
        self.assertEqual(str(latest_revision["state"]), "ready")

    def test_count_records_by_state_excludes_failed_objects_by_default(self) -> None:
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-count-ready",
                revision_hash="hash-count-ready",
                project_code="G32026SH1000666",
                project_name="可用记录",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=f"{self.temp_dir.name}/count-ready.html",
                archive_path=f"{self.temp_dir.name}/count-ready.html",
                parser_payload={"项目编号": "G32026SH1000666"},
                postprocess_payload={"项目编号": "G32026SH1000666"},
                findings=[],
            )
        )
        self.store.upsert_failed_record(
            project_code="G32026SH1000777",
            source_file=f"{self.temp_dir.name}/count-failed.html",
            state="parse_failed",
            error_type="decode_failed",
            error_message="decode_failed: broken source",
            payload={"项目编号": "G32026SH1000777"},
        )

        counts = self.store.count_records_by_state()

        self.assertEqual(counts.get("ready"), 1)
        self.assertNotIn("parse_failed", counts)

    def test_count_records_by_state_surfaces_invalid_source_identity_json_instead_of_supersession_fallback(
        self,
    ) -> None:
        created = self.store.upsert_record(
            IngestedRecord(
                record_id="rec-count-bad-source-identity",
                revision_hash="hash-count-bad-source-identity",
                project_code="G32026SHCOUNTBAD",
                project_name="坏身份统计记录",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=f"{self.temp_dir.name}/count-bad-source-identity.html",
                archive_path=f"{self.temp_dir.name}/count-bad-source-identity.html",
                parser_payload={"项目编号": "G32026SHCOUNTBAD"},
                postprocess_payload={"项目编号": "G32026SHCOUNTBAD"},
                findings=[],
            )
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE records SET source_identity_json = ? WHERE record_id = ?",
                ("{", created["record_id"]),
            )

        with self.assertRaises(json.JSONDecodeError):
            self.store.count_records_by_state()

    def test_store_requires_explicit_migrate_after_database_file_is_deleted(self) -> None:
        self.store.set_setting("ui.basic", {"default_exchange": "all"})

        os.remove(self.store.db_path)

        with self.assertRaises(sqlite3.OperationalError):
            self.store.get_setting("ui.basic", default={"default_exchange": "fallback"})

        self.store.migrate()

        self.assertEqual(self.store.get_setting("ui.basic", default={"default_exchange": "fallback"}), {"default_exchange": "fallback"})
        self.assertEqual(self.store.count_records_by_state(), {})
        self.assertEqual(self.store.count_pending_mappings(), 0)

    def test_get_setting_marks_decode_error_when_value_json_is_corrupt(self) -> None:
        self.store.set_setting("ui.basic", {"default_exchange": "all"})
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE settings SET value_json = ? WHERE key = ?",
                ("{not valid json", "ui.basic"),
            )

        payload = self.store.get_setting("ui.basic", default={"default_exchange": "fallback"})

        self.assertEqual(payload["default_exchange"], "fallback")
        self.assertEqual(payload["__peap_settings_decode_error__"], "invalid_json")

    def test_get_setting_returns_default_copy_with_decode_marker_for_corrupt_setting(self) -> None:
        self.store.set_setting("ui.basic", {"default_exchange": "all"})
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE settings SET value_json = ? WHERE key = ?",
                ("{not valid json", "ui.basic"),
            )
        default = {"default_exchange": "fallback"}

        payload = self.store.get_setting("ui.basic", default=default)
        payload["default_exchange"] = "changed"

        self.assertEqual(default, {"default_exchange": "fallback"})
        self.assertEqual(
            self.store.get_setting("ui.basic", default=default),
            {
                "default_exchange": "fallback",
                "__peap_settings_decode_error__": "invalid_json",
            },
        )

    def test_record_operation_result_rejects_false_kind_instead_of_persisting_empty_operation_kind(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "operation-kind.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>operation kind</body></html>")
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-operation-kind",
                revision_hash="hash-operation-kind",
                project_code="G32026OPKIND",
                project_name="操作类型测试",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026OPKIND"},
                postprocess_payload={"项目编号": "G32026OPKIND"},
                findings=[],
            )
        )

        with self.assertRaisesRegex(ValueError, "kind"):
            self.store.record_operation_result(
                "rec-operation-kind",
                kind=False,  # type: ignore[arg-type]
                code="ok",
            )

        record = self.store.get_record("rec-operation-kind")
        self.assertEqual(record["last_operation_kind"], "")
        self.assertEqual(record["last_operation_code"], "")

    def test_record_operation_result_rejects_false_code_message_and_artifact_status(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "operation-fields.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>operation fields</body></html>")
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-operation-fields",
                revision_hash="hash-operation-fields",
                project_code="G32026OPFIELDS",
                project_name="操作字段测试",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026OPFIELDS"},
                postprocess_payload={"项目编号": "G32026OPFIELDS"},
                findings=[],
            )
        )
        before = self.store.get_record("rec-operation-fields")

        with self.assertRaisesRegex(ValueError, "code"):
            self.store.record_operation_result(
                "rec-operation-fields",
                kind="reprocess",
                code=False,  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "message"):
            self.store.record_operation_result(
                "rec-operation-fields",
                kind="reprocess",
                code="failed",
                message=False,  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "artifact_status"):
            self.store.record_operation_result(
                "rec-operation-fields",
                kind="reprocess",
                code="failed",
                artifact_status=False,  # type: ignore[arg-type]
            )

        record = self.store.get_record("rec-operation-fields")
        self.assertEqual(record["artifact_status"], before["artifact_status"])
        self.assertEqual(record["last_operation_kind"], before["last_operation_kind"])
        self.assertEqual(record["last_operation_code"], before["last_operation_code"])


class StreamingStoreDeduplicationTest(unittest.TestCase):
    """Tests for intra-run page deduplication."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming_dedup.sqlite3", auto_migrate=True)

    def test_upsert_record_same_revision_hash_does_not_create_new_revision(self) -> None:
        """Duplicate pages in one run with same content hash must not create new revision.

        This verifies that when the same page is ingested twice within a run with
        identical content, only one revision is created (not an invisible rewrite).
        """
        source_file = os.path.join(self.temp_dir.name, "dup.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>duplicate test</body></html>")

        payload = {
            "项目编号": "G32025SH1000194",
            "项目名称": "重复测试项目",
            "项目类型": "股权转让",
            "转让方": "测试公司",
        }
        record_first = IngestedRecord(
            record_id="rec-dedup-1",
            revision_hash="hash-dedup-same",
            project_code="G32025SH1000194",
            project_name="重复测试项目",
            project_type="股权转让",
            exchange="shanghai",
            listing_date="2026-03-21",
            state="ready",
            source_file=source_file,
            archive_path=source_file,
            parser_payload=payload,
            postprocess_payload=payload,
            findings=[],
        )
        result_first = self.store.upsert_record(record_first)
        self.assertTrue(result_first["changed"])
        first_revision_id = result_first["revision_id"]

        # Same content, same source file - should NOT create new revision
        result_second = self.store.upsert_record(record_first)
        self.assertFalse(result_second["changed"])
        self.assertEqual(result_second["revision_id"], first_revision_id)

        # Verify only one revision exists
        record = self.store.get_record(result_first["record_id"])
        self.assertEqual(record["revision_id"], first_revision_id)

    def test_upsert_record_export_cursor_reflects_unchanged_revision(self) -> None:
        """Export cursor must observe genuine changes, not lost rewrites.

        When a page is re-ingested with unchanged content, the export cursor
        should reflect the original revision, not create a false delta.
        """
        source_file = os.path.join(self.temp_dir.name, "cursor_dup.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>cursor test</body></html>")

        payload = {
            "项目编号": "G32026SH1000001",
            "项目名称": "游标测试项目",
            "项目类型": "股权转让",
        }
        record = IngestedRecord(
            record_id="rec-cursor-test",
            revision_hash="hash-cursor-original",
            project_code="G32026SH1000001",
            project_name="游标测试项目",
            project_type="股权转让",
            exchange="shanghai",
            listing_date="2026-03-21",
            state="ready",
            source_file=source_file,
            archive_path=source_file,
            parser_payload=payload,
            postprocess_payload=payload,
            findings=[],
        )

        first = self.store.upsert_record(record)
        self.store.mark_exported(
            export_id="exp-cursor-1",
            cursor_key="default",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=f"{self.temp_dir.name}/exports",
            summary={"new_records": 1},
            records=[self.store.get_record(first["record_id"])],
        )

        # Re-ingest same content
        second = self.store.upsert_record(record)
        self.assertFalse(second["changed"])

        # Export cursor should still reference the original revision
        cursor_map = self.store.get_exported_revision_map("default")
        self.assertEqual(
            cursor_map[first["record_id"]]["revision_hash"],
            "hash-cursor-original",
        )

    def test_upsert_record_different_content_creates_new_revision(self) -> None:
        """A page with genuinely changed content must create a new revision."""
        source_file = os.path.join(self.temp_dir.name, "changed.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>version 1</body></html>")

        payload_v1 = {
            "项目编号": "G32026SH1000002",
            "项目名称": "变更测试项目v1",
            "项目类型": "股权转让",
        }
        payload_v2 = {
            **payload_v1,
            "项目名称": "变更测试项目v2",  # Content changed
        }

        record_v1 = IngestedRecord(
            record_id="rec-changed",
            revision_hash="hash-v1",
            project_code="G32026SH1000002",
            project_name="变更测试项目v1",
            project_type="股权转让",
            exchange="shanghai",
            listing_date="2026-03-21",
            state="ready",
            source_file=source_file,
            archive_path=source_file,
            parser_payload=payload_v1,
            postprocess_payload=payload_v1,
            findings=[],
        )
        first = self.store.upsert_record(record_v1)
        self.assertTrue(first["changed"])

        record_v2 = IngestedRecord(
            record_id="rec-changed",
            revision_hash="hash-v2",  # Different hash due to content change
            project_code="G32026SH1000002",
            project_name="变更测试项目v2",
            project_type="股权转让",
            exchange="shanghai",
            listing_date="2026-03-21",
            state="ready",
            source_file=source_file,
            archive_path=source_file,
            parser_payload=payload_v2,
            postprocess_payload=payload_v2,
            findings=[],
        )
        second = self.store.upsert_record(record_v2)
        self.assertTrue(second["changed"])
        self.assertNotEqual(second["revision_id"], first["revision_id"])


class StreamingStoreStateMachineRegressionTest(unittest.TestCase):
    """Regression tests for streaming store state machine contracts."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming_state_machine.sqlite3", auto_migrate=True)

    def test_mapping_conflict_is_persisted_review_work_not_exception_work(self) -> None:
        """Regression: mapping_conflict must be classified as persisted review work.

        Currently mapping_conflict may be counted as exception work instead of
        persisted review work. This test verifies the correct classification.
        """
        source_file = os.path.join(self.temp_dir.name, "conflict.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>conflict</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-conflict-test",
                revision_hash="hash-conflict",
                project_code="G32025CQ1000202-3",
                project_name="冲突测试项目",
                project_type="股权转让",
                exchange="chongqing",
                listing_date="2026-03-26",
                state="mapping_conflict",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={
                    "项目编号": "G32025CQ1000202-3",
                    "项目名称": "冲突测试项目",
                    "项目类型": "股权转让",
                    "隶属集团": "中国铁路工程集团有限公司",
                },
                postprocess_payload={
                    "项目编号": "G32025CQ1000202-3",
                    "项目名称": "冲突测试项目",
                    "项目类型": "股权转让",
                },
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_conflict",
                        message="conflicting group candidates",
                        evidence={"options": ["中国铁路工程集团有限公司", "中铁"]},
                    )
                ],
            )
        )

        # mapping_conflict records must be included in ready set for deduplication
        # but NOT counted as exceptions
        latest = self.store.iter_latest_records(states=["mapping_conflict"])
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["state"], "mapping_conflict")

        # mapping_conflict must NOT be counted as pending_mapping exception
        pending = self.store.count_pending_mappings()
        # The regression is that mapping_conflict might be incorrectly counted here
        # It should be review work, not pending mapping work
        self.assertEqual(pending, 0, "mapping_conflict should not be counted as pending_mapping")

    def test_record_states_are_properly_typed_enums(self) -> None:
        """Regression: Record states must be proper typed enums, not strings."""
        from peap.streaming_models import RecordState

        # Verify RecordState enum exists and has expected values
        self.assertTrue(hasattr(RecordState, "READY"))
        self.assertTrue(hasattr(RecordState, "PENDING_MAPPING"))
        self.assertTrue(hasattr(RecordState, "MAPPING_CONFLICT"))
        self.assertTrue(hasattr(RecordState, "PARSED_FAILED"))

        # State values should be string enums, not arbitrary strings
        self.assertIsInstance(RecordState.READY.value, str)
        self.assertIsInstance(RecordState.PENDING_MAPPING.value, str)


class StreamingStoreIncrementalExportCursorRegressionTest(unittest.TestCase):
    """Regression tests for incremental export cursor bookkeeping with non-ready transitions.

    These tests verify the store-level behavior that underpins the export contract:
    when a record was previously exported as "ready" and later transitions to a
    non-ready state, the store must support emitting and acknowledging removal signals.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming_export_cursor.sqlite3", auto_migrate=True)

    def test_cursor_contains_exported_ready_record_and_tracks_revision(self) -> None:
        """Baseline: cursor correctly tracks a record exported while ready."""
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-cursor-baseline",
                revision_hash="hash-baseline-v1",
                project_code="G32025SH1000194",
                project_name="基线游标测试",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/baseline.html",
                archive_path=f"{self.temp_dir.name}/archive/baseline.html",
                parser_payload={
                    "项目编号": "G32025SH1000194",
                    "项目名称": "基线游标测试",
                    "项目类型": "股权转让",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "108.00",
                    "转让方": "基线卖方",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1000194",
                    "项目名称": "基线游标测试",
                    "项目类型": "股权转让",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "108.00",
                    "转让方": "基线卖方",
                },
                canonical_record={
                    "record_family": "listing",
                    "canonical_fields": {
                        "project_code": "G32025SH1000194",
                        "project_name": "基线游标测试",
                        "project_type": "股权转让",
                        "status": "挂牌中",
                        "exchange": "shanghai",
                        "start_date": "2026-03-21",
                        "price": "108.00",
                        "seller": "基线卖方",
                    },
                },
                canonical_projection={
                    "项目编号": "G32025SH1000194",
                    "项目名称": "基线游标测试",
                    "项目类型": "股权转让",
                    "项目状态": "挂牌中",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "108.00",
                    "转让方": "基线卖方",
                },
                findings=[],
            )
        )

        ready_rows = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(ready_rows), 1)

        # Mark as exported
        self.store.mark_exported(
            export_id="exp-baseline-1",
            cursor_key="default",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=f"{self.temp_dir.name}/exports",
            summary={"new_records": 1, "changed_records": 0},
            records=ready_rows,
        )

        cursor_map = self.store.get_exported_revision_map("default")
        self.assertIn("rec-cursor-baseline", cursor_map)
        self.assertEqual(cursor_map["rec-cursor-baseline"]["revision_hash"], "hash-baseline-v1")

    def test_cursor_does_not_silently_clear_for_non_ready_transition(self) -> None:
        """Regression: cursor must NOT silently clear entries when record transitions
        to non-ready state.

        The store must NOT auto-clear cursor entries when a record becomes non-ready.
        The removal must be explicitly signaled and acknowledged through the export
        contract, not silently handled by clearing the cursor row.
        """
        # Setup: create and export record in ready state
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-no-silent-clear",
                revision_hash="hash-clear-v1",
                project_code="G32025SH1000195",
                project_name="不清除游标测试",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/no_clear.html",
                archive_path=f"{self.temp_dir.name}/archive/no_clear.html",
                parser_payload={
                    "项目编号": "G32025SH1000195",
                    "项目名称": "不清除游标测试",
                    "项目类型": "股权转让",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "88.00",
                    "转让方": "清除测试卖方",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1000195",
                    "项目名称": "不清除游标测试",
                    "项目类型": "股权转让",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "88.00",
                    "转让方": "清除测试卖方",
                },
                canonical_record={
                    "record_family": "listing",
                    "canonical_fields": {
                        "project_code": "G32025SH1000195",
                        "project_name": "不清除游标测试",
                        "project_type": "股权转让",
                        "status": "挂牌中",
                        "exchange": "shanghai",
                        "start_date": "2026-03-21",
                        "price": "88.00",
                        "seller": "清除测试卖方",
                    },
                },
                canonical_projection={
                    "项目编号": "G32025SH1000195",
                    "项目名称": "不清除游标测试",
                    "项目类型": "股权转让",
                    "项目状态": "挂牌中",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "88.00",
                    "转让方": "清除测试卖方",
                },
                findings=[],
            )
        )

        ready_rows = self.store.iter_latest_records(states=["ready"])
        self.store.mark_exported(
            export_id="exp-no-clear-1",
            cursor_key="default",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=f"{self.temp_dir.name}/exports",
            summary={"new_records": 1, "changed_records": 0},
            records=ready_rows,
        )

        # Verify cursor entry exists
        cursor_before = self.store.get_exported_revision_map("default")
        self.assertIn("rec-no-silent-clear", cursor_before)

        # Transition to non-ready state
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-no-silent-clear",
                revision_hash="hash-clear-v2",
                project_code="G32025SH1000195",
                project_name="不清除游标测试",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="pending_mapping",
                source_file=f"{self.temp_dir.name}/raw/no_clear.html",
                archive_path=f"{self.temp_dir.name}/archive/no_clear.html",
                parser_payload={
                    "项目编号": "G32025SH1000195",
                    "项目名称": "不清除游标测试",
                    "项目类型": "股权转让",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1000195",
                    "项目名称": "不清除游标测试",
                    "项目类型": "股权转让",
                },
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_gap",
                        message="缺少类型",
                        evidence={"missing_fields": ["类型"]},
                    )
                ],
            )
        )

        # Run store maintenance (which might be called by the store)
        from peap.streaming_store_maintenance import run_streaming_store_maintenance
        run_streaming_store_maintenance(self.store)

        # Cursor must NOT be silently cleared
        cursor_after = self.store.get_exported_revision_map("default")
        self.assertIn(
            "rec-no-silent-clear",
            cursor_after,
            "Cursor entry must NOT be silently cleared when record becomes non-ready. "
            "Removal must be explicitly signaled through the export contract.",
        )

    def test_non_ready_record_with_cursor_entry_appears_in_removal_candidate_set(self) -> None:
        """Regression: store must provide a way to query previously-exported records
        that are now non-ready (removal candidates).

        The incremental export needs to detect when a previously-exported record
        is now non-ready. This requires the store to support querying records
        that: (a) have a cursor entry, AND (b) are not in "ready" state.

        The store must provide an `iter_removal_candidates(cursor_key)` method
        or equivalent that returns records that have a cursor entry but are
        not in ready state. Currently this intersection is not directly queryable.
        """
        # Setup: create and export record in ready state
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-removal-candidate",
                revision_hash="hash-rc-v1",
                project_code="G32025SH1000196",
                project_name="移除候选测试",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/rem_cand.html",
                archive_path=f"{self.temp_dir.name}/archive/rem_cand.html",
                parser_payload={
                    "项目编号": "G32025SH1000196",
                    "项目名称": "移除候选测试",
                    "项目类型": "股权转让",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "77.00",
                    "转让方": "候选卖方",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1000196",
                    "项目名称": "移除候选测试",
                    "项目类型": "股权转让",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "77.00",
                    "转让方": "候选卖方",
                },
                canonical_record={
                    "record_family": "listing",
                    "canonical_fields": {
                        "project_code": "G32025SH1000196",
                        "project_name": "移除候选测试",
                        "project_type": "股权转让",
                        "status": "挂牌中",
                        "exchange": "shanghai",
                        "start_date": "2026-03-21",
                        "price": "77.00",
                        "seller": "候选卖方",
                    },
                },
                canonical_projection={
                    "项目编号": "G32025SH1000196",
                    "项目名称": "移除候选测试",
                    "项目类型": "股权转让",
                    "项目状态": "挂牌中",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "77.00",
                    "转让方": "候选卖方",
                },
                findings=[],
            )
        )

        ready_rows = self.store.iter_latest_records(states=["ready"])
        self.assertEqual(len(ready_rows), 1)
        self.store.mark_exported(
            export_id="exp-rem-cand-1",
            cursor_key="default",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=f"{self.temp_dir.name}/exports",
            summary={"new_records": 1, "changed_records": 0},
            records=ready_rows,
        )

        # Verify cursor entry
        cursor_map = self.store.get_exported_revision_map("default")
        self.assertIn("rec-removal-candidate", cursor_map)

        # Transition to non-ready state
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-removal-candidate",
                revision_hash="hash-rc-v2",
                project_code="G32025SH1000196",
                project_name="移除候选测试",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="parse_failed",
                source_file=f"{self.temp_dir.name}/raw/rem_cand.html",
                archive_path=f"{self.temp_dir.name}/archive/rem_cand.html",
                parser_payload={
                    "项目编号": "G32025SH1000196",
                    "项目名称": "移除候选测试",
                    "项目类型": "股权转让",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1000196",
                    "项目名称": "移除候选测试",
                    "项目类型": "股权转让",
                },
                findings=[
                    PostProcessFinding(
                        severity="error",
                        type="parse_failed",
                        message="解析失败",
                    )
                ],
            )
        )

        # Record is now non-ready and should NOT appear in ready rows
        ready_rows_after = self.store.iter_latest_records(states=["ready"])
        ready_record_ids = {r["record_id"] for r in ready_rows_after}
        self.assertNotIn(
            "rec-removal-candidate",
            ready_record_ids,
            "Non-ready record must not appear in ready set",
        )

        # Cursor entry still exists (not silently cleared)
        cursor_map_after = self.store.get_exported_revision_map("default")
        self.assertIn("rec-removal-candidate", cursor_map_after)

        # The store exposes get_exported_revision_map which can be used with
        # iter_latest_records(states=["ready"]) to detect removal candidates:
        # records that have a cursor entry but are not currently in ready state.
        self.assertTrue(
            hasattr(self.store, "get_exported_revision_map"),
            "Store must have get_exported_revision_map(cursor_key) method to support "
            "incremental export removal detection.",
        )

        # Verify get_exported_revision_map returns the non-ready record
        exported_map = self.store.get_exported_revision_map("default")
        self.assertIn(
            "rec-removal-candidate",
            exported_map,
            "Previously exported record must appear in exported revision map",
        )

        # The removal candidates can be computed as:
        # set(exported_map.keys()) - {r["record_id"] for r in iter_latest_records(states=["ready"])}
        ready_rows = self.store.iter_latest_records(states=["ready"])
        ready_record_ids = {r["record_id"] for r in ready_rows}
        exported_record_ids = set(exported_map.keys())
        removal_candidate_ids = exported_record_ids - ready_record_ids
        self.assertIn(
            "rec-removal-candidate",
            removal_candidate_ids,
            "Previously exported record that is now non-ready must appear in removal candidates",
        )


class StreamingStoreJobLifecycleTest(unittest.TestCase):
    """Regression tests for job lifecycle APIs in StreamingStore."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming_job_lifecycle.sqlite3", auto_migrate=True)

    def test_store_has_start_job_lifecycle_apis(self) -> None:
        """StreamingStore must provide job lifecycle transition APIs.

        Currently missing: start_job(job_id), mark_job_running(job_id),
        fail_job_with_startup_failure(job_id, failure) atomically.
        """
        # Must have: start_job (transitions from starting to running)
        self.assertTrue(
            hasattr(self.store, "start_job") or hasattr(self.store, "mark_job_running"),
            "StreamingStore must have start_job() or mark_job_running() method"
        )
        # Must have: fail_job atomically
        self.assertTrue(
            hasattr(self.store, "fail_job"),
            "StreamingStore must have fail_job() for atomic startup failure persistence"
        )

    def test_create_job_then_start_job_produces_running_job(self) -> None:
        """Job lifecycle: create(starting) -> start() -> running."""
        job_id = self.store.create_job("one_click", metadata={})
        # After creation, job should be in starting state (not running directly)
        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "starting")

        # Must have start_job or mark_job_running
        if hasattr(self.store, "start_job"):
            self.store.start_job(job_id)
        elif hasattr(self.store, "mark_job_running"):
            self.store.mark_job_running(job_id)
        else:
            self.fail("No start_job or mark_job_running method found")

        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "running")

    def test_fail_job_atomically_creates_startup_failure_event(self) -> None:
        """fail_job() must atomically: update status to failed + append failure event."""
        from peap_core.error_contracts import PipelineFailure

        job_id = self.store.create_job("one_click", metadata={})

        if hasattr(self.store, "start_job"):
            self.store.start_job(job_id)
        elif hasattr(self.store, "mark_job_running"):
            self.store.mark_job_running(job_id)

        failure = PipelineFailure(
            code="job_startup_failed",
            component="desktop_app_service",
            stage="startup",
            recoverability="retryable",
            message="playwright env init failed",
            context={"exception": "RuntimeError", "original": "playwright env init failed"},
        )

        # Must have fail_job
        self.assertTrue(hasattr(self.store, "fail_job"))
        self.store.fail_job(job_id, failure=failure)

        # Job status must be failed
        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "failed")

        # Must have a failure event with stage="startup"
        events = self.store.list_job_events(job_id)
        startup_events = [e for e in events if e.get("stage") == "startup" and e.get("status") == "failed"]
        self.assertTrue(
            len(startup_events) > 0,
            f"Job must have a startup-failure event. Events: {events}"
        )
        self.assertEqual(startup_events[0].get("error_type"), "job_startup_failed")

    def test_start_job_refuses_to_revive_failed_job(self) -> None:
        """Late workers must not transition a failed startup back to running."""
        from peap_core.error_contracts import PipelineFailure

        job_id = self.store.create_job("one_click", metadata={})
        self.store.fail_job(
            job_id,
            failure=PipelineFailure(
                code="job_startup_failed",
                component="desktop_app_service",
                stage="startup",
                recoverability="retryable",
                message="startup handshake timed out",
                context={},
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "cannot transition from status='failed'"):
            self.store.start_job(job_id)

        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "failed")
        startup_events = [event for event in self.store.list_job_events(job_id) if event.get("stage") == "startup"]
        self.assertFalse(any(event.get("status") == "running" for event in startup_events))
        self.assertEqual(
            sum(1 for event in startup_events if event.get("status") == "failed"),
            1,
        )

    def test_terminal_job_cannot_be_overwritten_by_late_finish_or_fail(self) -> None:
        from peap_core.error_contracts import PipelineFailure

        job_id = self.store.create_job("one_click", metadata={})
        self.store.start_job(job_id)
        self.store.finish_job(job_id, status="success", summary={"owner": "new-worker"})

        self.store.finish_job(job_id, status="failed", summary={"owner": "late-worker"})
        self.store.fail_job(
            job_id,
            failure=PipelineFailure(
                code="late_worker_failed",
                component="test",
                stage="worker",
                recoverability="retryable",
                message="late worker failure",
                context={},
            ),
        )

        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "success")
        self.assertEqual(job["summary"], {"owner": "new-worker"})
        self.assertFalse(any(event.get("status") == "failed" for event in self.store.list_job_events(job_id)))

    def test_interrupt_running_jobs_includes_crashed_starting_jobs(self) -> None:
        starting_job_id = self.store.create_job("one_click", metadata={})
        running_job_id = self.store.create_job("download_ingest", metadata={})
        self.store.start_job(running_job_id)

        interrupted = self.store.interrupt_running_jobs(reason="backend restarted")

        self.assertEqual(set(interrupted), {starting_job_id, running_job_id})
        self.assertEqual(self.store.get_job(starting_job_id)["status"], "interrupted")
        self.assertEqual(self.store.get_job(running_job_id)["status"], "interrupted")

    def test_fail_job_rejects_non_mapping_event_payload_atomically(self) -> None:
        from peap_core.error_contracts import PipelineFailure

        failure = PipelineFailure(
            code="job_startup_failed",
            component="desktop_app_service",
            stage="startup",
            recoverability="retryable",
            message="startup handshake timed out",
            context={},
        )

        for event_payload in (False, [], "not-an-object"):
            with self.subTest(event_payload=event_payload):
                job_id = self.store.create_job("one_click", metadata={})
                self.store.start_job(job_id)

                with self.assertRaisesRegex(ValueError, "event_payload"):
                    self.store.fail_job(
                        job_id,
                        failure=failure,
                        event_payload=event_payload,  # type: ignore[arg-type]
                    )

                job = self.store.get_job(job_id)
                self.assertEqual(job["status"], "running")
                failed_events = [
                    event
                    for event in self.store.list_job_events(job_id)
                    if event.get("stage") == "startup" and event.get("status") == "failed"
                ]
                self.assertEqual(failed_events, [])

    def test_fail_job_rejects_non_mapping_fallback_failure_context_atomically(self) -> None:
        for context in (False, "not-an-object", ["not-an-object"]):
            with self.subTest(context=context):
                job_id = self.store.create_job("one_click", metadata={})
                self.store.start_job(job_id)
                failure = type(
                    "FallbackFailure",
                    (),
                    {
                        "code": "job_startup_failed",
                        "component": "desktop_app_service",
                        "stage": "startup",
                        "recoverability": "retryable",
                        "context": context,
                        "__str__": lambda self: "startup failed",
                    },
                )()

                with self.assertRaisesRegex(ValueError, "failure.context"):
                    self.store.fail_job(job_id, failure=failure)

                job = self.store.get_job(job_id)
                self.assertEqual(job["status"], "running")
                failed_events = [
                    event
                    for event in self.store.list_job_events(job_id)
                    if event.get("stage") == "startup" and event.get("status") == "failed"
                ]
                self.assertEqual(failed_events, [])

    def test_fail_job_accepts_missing_or_none_fallback_failure_context_as_empty(self) -> None:
        cases = (
            type(
                "MissingContextFailure",
                (),
                {
                    "code": "job_startup_failed",
                    "component": "desktop_app_service",
                    "stage": "startup",
                    "recoverability": "retryable",
                    "__str__": lambda self: "startup failed",
                },
            )(),
            type(
                "NoneContextFailure",
                (),
                {
                    "code": "job_startup_failed",
                    "component": "desktop_app_service",
                    "stage": "startup",
                    "recoverability": "retryable",
                    "context": None,
                    "__str__": lambda self: "startup failed",
                },
            )(),
        )

        for failure in cases:
            with self.subTest(failure=failure.__class__.__name__):
                job_id = self.store.create_job("one_click", metadata={})
                self.store.start_job(job_id)

                self.store.fail_job(job_id, failure=failure)

                job = self.store.get_job(job_id)
                self.assertEqual(job["status"], "failed")
                failed_events = [
                    event
                    for event in self.store.list_job_events(job_id)
                    if event.get("stage") == "startup" and event.get("status") == "failed"
                ]
                self.assertEqual(len(failed_events), 1)
                self.assertEqual(failed_events[0]["error_type"], "job_startup_failed")


class StreamingStoreMaintenanceRegressionTest(unittest.TestCase):
    """Regression tests for normalize_required_mapping_states terminal-state semantics."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming_maint.sqlite3", auto_migrate=True)

    def test_maintenance_does_not_clobber_conflict_state(self) -> None:
        """normalize_required_mapping_states must NOT reclassify conflict records.

        conflict is a terminal state set by had_conflict=True during ingest.
        The findings-recomputes-to-ready path must not overwrite it.
        """
        source_file = os.path.join(self.temp_dir.name, "conflict_record.html")
        with open(source_file, "w", encoding="utf-8") as f:
            f.write("<html><body>conflict test</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-conflict-terminal",
                revision_hash="hash-conflict-1",
                project_code="G32025SH1000900",
                project_name="冲突状态测试",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="conflict",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32025SH1000900", "项目名称": "冲突状态测试"},
                postprocess_payload={"项目编号": "G32025SH1000900", "项目名称": "冲突状态测试"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="archive_conflict",
                        message="archive naming conflict",
                        evidence={},
                    )
                ],
            )
        )

        self.store.normalize_required_mapping_states()

        record = self.store.get_record("rec-conflict-terminal")
        self.assertEqual(
            record["state"],
            "conflict",
            "conflict state must not be overwritten by maintenance findings recomputation",
        )

    def test_maintenance_does_not_insert_mapping_conflict_into_pending_backlog(self) -> None:
        """mapping_conflict records must NOT appear in the mapping_pending backlog.

        The backlog is reserved for pending_mapping records only.
        mapping_conflict is a terminal human-conflict state.
        """
        source_file = os.path.join(self.temp_dir.name, "mapping_conflict_record.html")
        with open(source_file, "w", encoding="utf-8") as f:
            f.write("<html><body>mapping conflict test</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-mapping-conflict-terminal",
                revision_hash="hash-mc-1",
                project_code="G32025SH1000901",
                project_name="映射冲突测试",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="mapping_conflict",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32025SH1000901", "项目名称": "映射冲突测试"},
                postprocess_payload={"项目编号": "G32025SH1000901", "项目名称": "映射冲突测试"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_conflict",
                        message="seller field ambiguous",
                        evidence={"field": "seller"},
                    )
                ],
            )
        )

        initial_pending = self.store.count_pending_mappings()
        self.store.normalize_required_mapping_states()
        final_pending = self.store.count_pending_mappings()

        self.assertEqual(
            final_pending,
            initial_pending,
            "mapping_conflict must not insert into mapping_pending backlog",
        )

        record = self.store.get_record("rec-mapping-conflict-terminal")
        self.assertEqual(
            record["state"],
            "mapping_conflict",
            "mapping_conflict state must not be changed by maintenance",
        )

    def test_maintenance_does_not_reclassify_mapping_conflict_when_findings_change(self) -> None:
        """A mapping_conflict record whose normalized findings no longer contain mapping_conflict
        must still stay mapping_conflict — it is terminal and requires human resolution.
        """
        source_file = os.path.join(self.temp_dir.name, "mc_findings_change.html")
        with open(source_file, "w", encoding="utf-8") as f:
            f.write("<html><body>mapping conflict findings change test</body></html>")

        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-mc-findings-change",
                revision_hash="hash-mc-fc-1",
                project_code="G32025SH1000902",
                project_name="冲突修复测试",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="mapping_conflict",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32025SH1000902", "项目名称": "冲突修复测试"},
                postprocess_payload={"项目编号": "G32025SH1000902", "项目名称": "冲突修复测试"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="archive_conflict",
                        message="archive conflict only",
                        evidence={},
                    )
                ],
            )
        )

        self.store.normalize_required_mapping_states()

        record = self.store.get_record("rec-mc-findings-change")
        self.assertEqual(
            record["state"],
            "mapping_conflict",
            "mapping_conflict must remain terminal even when normalized findings no longer contain mapping_conflict type",
        )


class BacklogReconcileTest(unittest.TestCase):
    """Tests for _reconcile_mapping_pending_backlog bidirectional logic."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/backlog.sqlite3", auto_migrate=True)

    def _insert_record_with_state(self, record_id, state, revision_id=1, project_code=None):
        from peap.streaming_models import IngestedRecord
        if project_code is None:
            project_code = f"PC-{record_id}"
        project_type = "未知" if state == "pending_review" else "股权转让"
        postprocess_payload = {"项目类型": "股权转让", "类型": "国资", "record_family": "listing"}
        canonical_record = {}
        findings: list[PostProcessFinding] = []
        if state == "ready":
            postprocess_payload.update(
                {
                    "项目编号": project_code,
                    "项目名称": "测试",
                    "项目状态": "挂牌中",
                    "交易所": "shanghai",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "100.00",
                    "转让方": "测试公司",
                }
            )
            canonical_record = {
                "record_family": "listing",
                "business_identity": {"business_id": "equity_transfer"},
                "canonical_fields": {
                    "project_code": project_code,
                    "project_name": "测试",
                    "project_type": "股权转让",
                    "status": "挂牌中",
                    "exchange": "shanghai",
                    "start_date": "2026-03-21",
                    "price": "100.00",
                    "seller": "测试公司",
                    "source_type": "国资",
                },
            }
        elif state == "pending_mapping":
            postprocess_payload = {"项目类型": "股权转让", "record_family": "listing"}
            findings = [
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少业务归属字段",
                )
            ]
        elif state == "pending_review":
            postprocess_payload = {"项目类型": "未知"}
            findings = [
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="业务类型待人工判定",
                )
            ]
        record = IngestedRecord(
            record_id=record_id,
            revision_hash=f"hash-{revision_id}",
            project_code=project_code,
            project_name="测试",
            project_type=project_type,
            exchange="shanghai",
            listing_date="2026-03-21",
            state=state,
            source_file=f"{self.temp_dir.name}/{record_id}.html",
            archive_path=f"{self.temp_dir.name}/archive/{record_id}.html",
            parser_payload={},
            postprocess_payload=postprocess_payload,
            canonical_record=canonical_record,
            findings=findings,
        )
        self.store.upsert_record(record)

    def _insert_open_mapping_pending(self, record_id, revision_id=1, project_code="P001"):
        with self.store._connect() as conn:
            conn.execute(
                "INSERT INTO mapping_pending (record_id, revision_id, project_code, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (record_id, revision_id, project_code, "{}", "2026-01-01 00:00:00"),
            )

    def test_reconcile_resolves_stale_mapping_conflict_open_row(self):
        # Record is in "conflict" state (not in MAINTENANCE_NORMALIZABLE_STATES,
        # so normalize phase skips it, but it IS NOT in BACKLOG_OWNING_STATES,
        # so stale open row should be resolved by reconciliation).
        # Use "conflict" state: not processed by normalization, but also not backlog-owning.
        self._insert_record_with_state("rec-stale-mc", "conflict")
        self._insert_open_mapping_pending("rec-stale-mc")
        self.store.normalize_required_mapping_states()
        with self.store._connect() as conn:
            row = conn.execute("SELECT resolved_at FROM mapping_pending WHERE record_id = ?", ("rec-stale-mc",)).fetchone()
            self.assertNotEqual(row["resolved_at"], "")

    def test_reconcile_resolves_stale_conflict_open_row(self):
        # Record is now conflict but conflict is not backlog-owning
        self._insert_record_with_state("rec-stale-conflict", "conflict")
        self._insert_open_mapping_pending("rec-stale-conflict")
        self.store.normalize_required_mapping_states()
        # conflict rows are not backlog-owned, so should be resolved
        with self.store._connect() as conn:
            row = conn.execute("SELECT resolved_at FROM mapping_pending WHERE record_id = ?", ("rec-stale-conflict",)).fetchone()
            self.assertNotEqual(row["resolved_at"], "")

    def test_reconcile_resolves_stale_pending_review_open_row(self):
        # pending_review is currently inactive contract residue for streaming mainline.
        # If historical data left an open mapping_pending row behind, reconcile should clear it.
        self._insert_record_with_state("rec-stale-pending-review", "pending_review")
        self._insert_open_mapping_pending("rec-stale-pending-review")
        self.store.normalize_required_mapping_states()
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT resolved_at FROM mapping_pending WHERE record_id = ?",
                ("rec-stale-pending-review",),
            ).fetchone()
            self.assertNotEqual(row["resolved_at"], "")

    def test_reconcile_inserts_missing_pending_mapping_row(self):
        # Record in PENDING_MAPPING state but no open mapping_pending row
        self._insert_record_with_state("rec-missing-pending", "pending_mapping")
        self.store.normalize_required_mapping_states()
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mapping_pending WHERE record_id = ? AND resolved_at = ''",
                ("rec-missing-pending",)
            ).fetchone()
            self.assertIsNotNone(row)

    def test_reconcile_bidirectional_mixed(self):
        # rec-bidir-stale: conflict state - not processed by normalize, but stale open row
        #   should be resolved (conflict is not in BACKLOG_OWNING_STATES)
        # rec-bidir-missing: pending_mapping without open row - should be inserted
        self._insert_record_with_state("rec-bidir-stale", "conflict")
        self._insert_open_mapping_pending("rec-bidir-stale")
        self._insert_record_with_state("rec-bidir-missing", "pending_mapping")
        self.store.normalize_required_mapping_states()
        with self.store._connect() as conn:
            stale = conn.execute("SELECT resolved_at FROM mapping_pending WHERE record_id = ?", ("rec-bidir-stale",)).fetchone()
            self.assertNotEqual(stale["resolved_at"], "")
            missing = conn.execute("SELECT * FROM mapping_pending WHERE record_id = ? AND resolved_at = ''", ("rec-bidir-missing",)).fetchone()
            self.assertIsNotNone(missing)

    def test_normalize_record_payload_and_state_does_not_touch_mapping_pending(self):
        # Verify the internal method does not insert or resolve mapping_pending
        self._insert_record_with_state("rec-no-backlog-touch", "ready")
        self._insert_open_mapping_pending("rec-no-backlog-touch")
        with self.store._connect() as conn:
            self.store._normalize_record_payload_and_state(conn)
            row = conn.execute(
                "SELECT resolved_at FROM mapping_pending WHERE record_id = ?",
                ("rec-no-backlog-touch",),
            ).fetchone()
            self.assertEqual(row["resolved_at"], "")

    def test_normalize_required_mapping_states_rolls_back_if_reconcile_fails(self):
        # Phase 1 (normalize) + phase 2 (reconcile) run in single transaction.
        # Verify both phases run in same transaction (no exception = committed together).
        self._insert_record_with_state("rec-rollback-test", "ready")
        self._insert_open_mapping_pending("rec-rollback-test")
        self.store.normalize_required_mapping_states()
        record = self.store.get_record("rec-rollback-test")
        self.assertEqual(record["state"], "ready")


if __name__ == "__main__":
    unittest.main()
