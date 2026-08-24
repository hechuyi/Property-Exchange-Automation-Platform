from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from path_isolation import assert_paths_under_temp, isolated_peap_env

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppService
from peap.streaming_export import run_ready_export
from peap.streaming_models import ExportRequest, IngestedRecord
from peap.streaming_store import StreamingStore


class ExportHistoryRetentionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app_home = os.path.join(self.temp_dir.name, "app_home")
        self._env_patch = patch.dict(
            os.environ,
            isolated_peap_env(self.temp_dir.name, app_home=self.app_home),
            clear=True,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self.config = AppConfig.from_env(project_root=self.temp_dir.name)
        assert_paths_under_temp(
            self,
            self.temp_dir.name,
            (
                self.config.APP_HOME,
                self.config.DATA_ROOT,
                self.config.CACHE_DIR,
                self.config.ARCHIVE_ROOT,
                self.config.OUTPUT_EXCEL_DIR,
                self.config.STREAMING_DB_PATH,
            ),
        )
        self.store = StreamingStore(self.config.STREAMING_DB_PATH, auto_migrate=True)
        self.service = AppService(config_obj=self.config)

    def _insert_export(
        self,
        *,
        export_id: str,
        artifact_path: str | None = None,
        artifact_paths: list[str] | None = None,
        manifest: dict[str, object] | None = None,
        cursor_value: dict[str, object] | None = None,
        output_dir: str | None = None,
    ) -> None:
        artifacts = list(artifact_paths or ([artifact_path] if artifact_path else []))
        for path in artifacts:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                with open(path, "wb") as handle:
                    handle.write(b"xlsx")
        self.store.mark_exported(
            export_id=export_id,
            cursor_id=f"cursor-{export_id}",
            requested_export_mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="all",
            output_dir=output_dir or (os.path.dirname(artifacts[0]) if artifacts else self.temp_dir.name),
            summary={
                "artifacts": artifacts,
                "requested_export_mode": "incremental",
                "revision_watermark": 13,
                "retention_count": 20,
            },
            records=[],
            manifest=manifest,
            cursor_value=cursor_value,
        )

    def _remove_export_summary_fields(self, export_id: str, *field_names: str) -> None:
        with sqlite3.connect(self.config.STREAMING_DB_PATH) as conn:
            row = conn.execute(
                "SELECT summary_json FROM exports WHERE export_id = ?",
                (export_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            summary = json.loads(row[0])
            for field_name in field_names:
                summary.pop(field_name, None)
            conn.execute(
                "UPDATE exports SET summary_json = ? WHERE export_id = ?",
                (json.dumps(summary), export_id),
            )

    def _set_export_summary_fields(self, export_id: str, **fields: object) -> None:
        with sqlite3.connect(self.config.STREAMING_DB_PATH) as conn:
            row = conn.execute(
                "SELECT summary_json FROM exports WHERE export_id = ?",
                (export_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            summary = json.loads(row[0])
            summary.update(fields)
            conn.execute(
                "UPDATE exports SET summary_json = ? WHERE export_id = ?",
                (json.dumps(summary), export_id),
            )

    def test_history_marks_missing_artifact_as_non_openable_tombstone(self) -> None:
        missing_path = os.path.join(self.temp_dir.name, "exports", "missing.xlsx")
        self._insert_export(export_id="exp-missing", artifact_path=missing_path)
        os.remove(missing_path)
        payload = self.service.get_export_history_detail("exp-missing")
        self.assertTrue(payload["is_tombstone"])
        self.assertFalse(payload["openable"])
        self.assertFalse(payload["rebuildable"])

    def test_history_open_download_do_not_rebuild_tombstone(self) -> None:
        missing_path = os.path.join(self.temp_dir.name, "exports", "missing2.xlsx")
        self._insert_export(export_id="exp-missing-2", artifact_path=missing_path)
        os.remove(missing_path)
        open_payload = self.service.open_export_history("exp-missing-2")
        download_payload = self.service.download_export_history(
            "exp-missing-2",
            output_dir=os.path.join(self.temp_dir.name, "dl"),
        )
        self.assertFalse(open_payload["opened"])
        self.assertFalse(download_payload["downloaded"])
        self.assertFalse(open_payload["rebuildable"])
        self.assertFalse(download_payload["rebuildable"])

    def test_history_treats_existing_artifact_outside_export_root_as_unavailable(self) -> None:
        external_dir = os.path.join(self.temp_dir.name, "external-export")
        os.makedirs(external_dir, exist_ok=True)
        external_path = os.path.join(external_dir, "external.xlsx")
        with open(external_path, "wb") as handle:
            handle.write(b"xlsx")
        self._insert_export(
            export_id="exp-external",
            artifact_path=external_path,
            output_dir=external_dir,
        )
        with sqlite3.connect(self.config.STREAMING_DB_PATH) as conn:
            conn.execute(
                "UPDATE exports SET output_dir = ? WHERE export_id = ?",
                (self.config.OUTPUT_EXCEL_DIR, "exp-external"),
            )

        list_payload = self.service.list_exports_history(limit=10)["rows"][0]
        detail_payload = self.service.get_export_history_detail("exp-external")
        open_payload = self.service.open_export_history("exp-external")
        download_payload = self.service.download_export_history(
            "exp-external",
            output_dir=os.path.join(self.temp_dir.name, "dl"),
        )

        self.assertTrue(list_payload["is_tombstone"])
        self.assertTrue(detail_payload["is_tombstone"])
        self.assertEqual(detail_payload["retention_status"], "artifact_unavailable")
        self.assertFalse(list_payload["openable"])
        self.assertFalse(detail_payload["openable"])
        self.assertEqual(detail_payload["existing_artifacts"], [])
        self.assertEqual(detail_payload["missing_artifacts"], [external_path])
        self.assertFalse(open_payload["opened"])
        self.assertFalse(download_payload["downloaded"])

    def test_history_marks_empty_artifact_rows_unavailable_in_list_and_detail(self) -> None:
        self._insert_export(export_id="exp-empty-artifacts")

        list_payload = self.service.list_exports_history(limit=10)["rows"][0]
        detail_payload = self.service.get_export_history_detail("exp-empty-artifacts")

        for payload in (list_payload, detail_payload):
            self.assertTrue(payload["is_tombstone"])
            self.assertFalse(payload["openable"])
            self.assertFalse(payload["rebuildable"])
            self.assertEqual(payload["retention_status"], "artifact_unavailable")
        self.assertEqual(detail_payload["artifacts"], [])

    def test_history_rejects_non_string_artifact_elements_in_list_and_detail(self) -> None:
        self._insert_export(export_id="exp-bad-artifact-element")
        self._set_export_summary_fields(
            "exp-bad-artifact-element",
            artifacts=[{"path": "not-a-string"}],
        )

        with self.assertRaisesRegex(ValueError, r"summary\.artifacts\[0\]"):
            self.service.list_exports_history(limit=10)
        with self.assertRaisesRegex(ValueError, r"summary\.artifacts\[0\]"):
            self.service.get_export_history_detail("exp-bad-artifact-element")

    def test_history_uses_each_export_output_root_after_default_root_changes(self) -> None:
        old_root = os.path.join(self.temp_dir.name, "old-exports")
        os.makedirs(old_root, exist_ok=True)
        artifact_path = os.path.join(old_root, "old.xlsx")
        with open(artifact_path, "wb") as handle:
            handle.write(b"xlsx")
        self._insert_export(
            export_id="exp-old-root",
            artifact_path=artifact_path,
            output_dir=old_root,
        )
        self.service.default_export_root = os.path.join(self.temp_dir.name, "new-exports")

        detail = self.service.get_export_history_detail("exp-old-root")

        self.assertTrue(detail["openable"])
        self.assertFalse(detail["is_tombstone"])
        self.assertEqual(detail["existing_artifacts"], [artifact_path])

    def test_history_rejects_symlink_artifacts_even_when_link_is_inside_export_root(self) -> None:
        export_root = os.path.join(self.temp_dir.name, "symlink-exports")
        external_root = os.path.join(self.temp_dir.name, "external")
        os.makedirs(export_root, exist_ok=True)
        os.makedirs(external_root, exist_ok=True)
        target_path = os.path.join(external_root, "real.xlsx")
        link_path = os.path.join(export_root, "link.xlsx")
        with open(target_path, "wb") as handle:
            handle.write(b"xlsx")
        self._insert_export(
            export_id="exp-symlink",
            artifact_path=link_path,
            output_dir=export_root,
        )
        os.remove(link_path)
        try:
            os.symlink(target_path, link_path)
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("symbolic links are unavailable")

        detail = self.service.get_export_history_detail("exp-symlink")

        self.assertTrue(detail["is_tombstone"])
        self.assertFalse(detail["openable"])
        self.assertEqual(detail["existing_artifacts"], [])
        self.assertEqual(detail["missing_artifacts"], [link_path])

    def test_history_marks_partially_missing_artifacts_as_incomplete_and_non_openable(self) -> None:
        export_dir = self.config.OUTPUT_EXCEL_DIR
        os.makedirs(export_dir, exist_ok=True)
        live_path = os.path.join(export_dir, "live.xlsx")
        missing_path = os.path.join(export_dir, "missing.xlsx")
        with open(live_path, "wb") as handle:
            handle.write(b"xlsx")
        self._insert_export(
            export_id="exp-partial",
            artifact_paths=[live_path, missing_path],
        )
        os.remove(missing_path)

        list_payload = self.service.list_exports_history(limit=10)["rows"][0]
        detail_payload = self.service.get_export_history_detail("exp-partial")
        download_payload = self.service.download_export_history(
            "exp-partial",
            output_dir=os.path.join(self.temp_dir.name, "dl"),
        )

        self.assertTrue(list_payload["is_tombstone"])
        self.assertTrue(detail_payload["is_tombstone"])
        self.assertEqual(list_payload["retention_status"], "artifact_incomplete")
        self.assertEqual(detail_payload["retention_status"], "artifact_incomplete")
        self.assertFalse(list_payload["openable"])
        self.assertFalse(detail_payload["openable"])
        self.assertFalse(detail_payload["rebuildable"])
        self.assertEqual(detail_payload["artifacts"], [live_path, missing_path])
        self.assertEqual(detail_payload["existing_artifacts"], [live_path])
        self.assertEqual(detail_payload["missing_artifacts"], [missing_path])
        self.assertFalse(download_payload["downloaded"])
        self.assertEqual(download_payload["artifacts"], [])
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir.name, "dl")))

    def test_history_uses_cursor_id_and_never_returns_legacy_mode_or_cursor_key(self) -> None:
        artifact_path = os.path.join(self.config.OUTPUT_EXCEL_DIR, "live.xlsx")
        os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
        with open(artifact_path, "wb") as handle:
            handle.write(b"xlsx")
        self._insert_export(export_id="exp-live", artifact_path=artifact_path)

        list_payload = self.service.list_exports_history(limit=10)["rows"][0]
        detail_payload = self.service.get_export_history_detail("exp-live")

        self.assertEqual(list_payload["cursor_id"], "cursor-exp-live")
        self.assertEqual(detail_payload["cursor_id"], "cursor-exp-live")
        self.assertEqual(list_payload["requested_export_mode"], "incremental")
        self.assertEqual(list_payload["revision_watermark"], 13)
        self.assertEqual(detail_payload["revision_watermark"], 13)
        self.assertIn("manifest", detail_payload)
        self.assertIn("cursor_value", detail_payload)
        self.assertNotIn("cursor_key", list_payload)
        self.assertNotIn("cursor_key", detail_payload)
        self.assertNotIn("mode", list_payload)
        self.assertNotIn("mode", detail_payload)

    def test_detail_reads_manifest_and_cursor_value_from_tables_when_summary_is_trimmed(self) -> None:
        manifest = {
            "export_id": "exp-trimmed",
            "cursor_id": "cursor-exp-trimmed",
            "revision_watermark": 17,
            "cursor_basis": {"eligible_set_hash": "hash-from-manifest-table"},
        }
        cursor_value = {
            "last_successful_revision_watermark": 17,
            "last_successful_export_id": "exp-trimmed",
            "cursor_basis_export_id": "exp-trimmed",
            "eligible_set_hash": "hash-from-cursor-table",
        }
        self._insert_export(
            export_id="exp-trimmed",
            manifest=manifest,
            cursor_value=cursor_value,
        )
        self._remove_export_summary_fields("exp-trimmed", "manifest", "cursor_value")

        detail_payload = self.service.get_export_history_detail("exp-trimmed")

        self.assertEqual(detail_payload["manifest"], manifest)
        self.assertEqual(detail_payload["cursor_value"], cursor_value)

    def test_detail_marks_missing_snapshots_when_summary_and_tables_have_no_manifest_or_cursor_value(self) -> None:
        self._insert_export(export_id="exp-no-snapshots")
        self._remove_export_summary_fields("exp-no-snapshots", "manifest", "cursor_value")
        with sqlite3.connect(self.config.STREAMING_DB_PATH) as conn:
            conn.execute("DELETE FROM export_manifests WHERE export_id = ?", ("exp-no-snapshots",))
            conn.execute("DELETE FROM export_cursor_values WHERE cursor_id = ?", ("cursor-exp-no-snapshots",))

        detail_payload = self.service.get_export_history_detail("exp-no-snapshots")

        self.assertEqual(detail_payload["manifest"], {})
        self.assertEqual(detail_payload["cursor_value"], {})
        self.assertEqual(detail_payload["snapshot_status"], "missing")
        self.assertEqual(detail_payload["missing_snapshots"], ["manifest", "cursor_value"])

    def test_detail_marks_empty_summary_snapshots_missing_when_tables_have_no_manifest_or_cursor_value(
        self,
    ) -> None:
        self._insert_export(export_id="exp-empty-summary")
        self._set_export_summary_fields("exp-empty-summary", manifest={}, cursor_value={})
        with sqlite3.connect(self.config.STREAMING_DB_PATH) as conn:
            conn.execute("DELETE FROM export_manifests WHERE export_id = ?", ("exp-empty-summary",))
            conn.execute("DELETE FROM export_cursor_values WHERE cursor_id = ?", ("cursor-exp-empty-summary",))

        detail_payload = self.service.get_export_history_detail("exp-empty-summary")

        self.assertEqual(detail_payload["manifest"], {})
        self.assertEqual(detail_payload["cursor_value"], {})
        self.assertEqual(detail_payload["snapshot_status"], "missing")
        self.assertEqual(detail_payload["missing_snapshots"], ["manifest", "cursor_value"])

    def test_detail_reads_table_snapshots_when_summary_snapshots_are_empty(self) -> None:
        manifest = {
            "export_id": "exp-empty-summary-with-tables",
            "cursor_id": "cursor-exp-empty-summary-with-tables",
            "revision_watermark": 23,
            "cursor_basis": {"eligible_set_hash": "hash-from-manifest-table"},
        }
        cursor_value = {
            "last_successful_revision_watermark": 23,
            "last_successful_export_id": "exp-empty-summary-with-tables",
            "cursor_basis_export_id": "exp-empty-summary-with-tables",
            "eligible_set_hash": "hash-from-cursor-table",
        }
        self._insert_export(
            export_id="exp-empty-summary-with-tables",
            manifest=manifest,
            cursor_value=cursor_value,
        )
        self._set_export_summary_fields("exp-empty-summary-with-tables", manifest={}, cursor_value={})

        detail_payload = self.service.get_export_history_detail("exp-empty-summary-with-tables")

        self.assertEqual(detail_payload["manifest"], manifest)
        self.assertEqual(detail_payload["cursor_value"], cursor_value)
        self.assertEqual(detail_payload["snapshot_status"], "available")
        self.assertEqual(detail_payload["missing_snapshots"], [])

    def test_retention_prunes_old_exports_as_non_rebuildable_tombstones(self) -> None:
        export_dir = self.config.OUTPUT_EXCEL_DIR
        os.makedirs(export_dir, exist_ok=True)
        for index in range(3):
            artifact_path = os.path.join(export_dir, f"artifact-{index}.xlsx")
            with open(artifact_path, "wb") as handle:
                handle.write(b"xlsx")
            self.store.mark_exported(
                export_id=f"exp-{index}",
                cursor_id="cursor-retention",
                requested_export_mode="incremental",
                date_from="2026-03-01",
                date_to="2026-03-31",
                project_type="all",
                output_dir=export_dir,
                summary={
                    "artifacts": [artifact_path],
                    "requested_export_mode": "incremental",
                    "revision_watermark": index + 1,
                    "retention_count": 2,
                },
                records=[],
                retention_count=2,
            )

        oldest_detail = self.service.get_export_history_detail("exp-0")
        rows = {row["export_id"]: row for row in self.service.list_exports_history(limit=10)["rows"]}

        self.assertTrue(oldest_detail["is_tombstone"])
        self.assertTrue(oldest_detail["pruned_by_retention"])
        self.assertFalse(oldest_detail["openable"])
        self.assertFalse(oldest_detail["rebuildable"])
        self.assertEqual(oldest_detail["retention_count"], 2)
        self.assertEqual(rows["exp-0"]["cursor_id"], "cursor-retention")
        self.assertTrue(rows["exp-0"]["pruned_by_retention"])
        self.assertEqual(sum(1 for row in rows.values() if not row["is_tombstone"]), 2)

    def test_field_missing_only_export_does_not_create_rebuildable_history_row(self) -> None:
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-field-missing-history",
                revision_hash="hash-field-missing-history",
                project_code="G32026SH1009998",
                project_name="缺字段历史项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-22",
                state="field_missing",
                source_file=os.path.join(self.temp_dir.name, "raw.html"),
                archive_path=os.path.join(self.temp_dir.name, "archive.html"),
                parser_payload={"项目编号": "G32026SH1009998"},
                postprocess_payload={"项目编号": "G32026SH1009998"},
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "business_id": "equity_transfer",
                        "raw_business_label": "股权转让",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SH1009998",
                        "project_name": "缺字段历史项目",
                        "project_type": "股权转让",
                    },
                },
                findings=[],
            )
        )

        result = run_ready_export(
            self.store,
            ExportRequest(
                date_from="2026-03-22",
                date_to="2026-03-22",
                business_types=["equity_transfer"],
                requested_export_mode="full",
                output_dir=os.path.join(self.temp_dir.name, "exports"),
            ),
        )
        history = self.service.list_exports_history(limit=10)

        self.assertEqual(result.field_missing_blocked_records, 1)
        self.assertEqual(result.artifacts, [])
        self.assertEqual(history["rows"], [])
