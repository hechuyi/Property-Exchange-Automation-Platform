from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppService
from peap.migrations import MigrationRunner
from peap.streaming_store import SCHEMA_VERSION, StreamingStore

LEGACY_RECORDS_SQL = """
CREATE TABLE records (
    record_id TEXT PRIMARY KEY,
    business_key TEXT NOT NULL UNIQUE,
    record_family TEXT NOT NULL DEFAULT 'listing',
    project_code TEXT NOT NULL DEFAULT '',
    project_name TEXT NOT NULL DEFAULT '',
    project_type TEXT NOT NULL DEFAULT '',
    exchange TEXT NOT NULL DEFAULT '',
    listing_date TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    source_file TEXT NOT NULL DEFAULT '',
    archive_path TEXT NOT NULL DEFAULT '',
    latest_revision_id INTEGER,
    last_error_type TEXT NOT NULL DEFAULT '',
    last_error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

LEGACY_RECORD_REVISIONS_SQL = """
CREATE TABLE record_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL,
    revision_hash TEXT NOT NULL,
    parser_payload_json TEXT NOT NULL DEFAULT '{}',
    postprocess_payload_json TEXT NOT NULL DEFAULT '{}',
    findings_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL,
    source_file TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (record_id) REFERENCES records(record_id)
)
"""


def _table_names(db_path: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    return {str(row[0]) for row in rows}


def _user_version(db_path: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


class StreamingStoreMigrationContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "streaming.sqlite3")

    def test_constructor_does_not_create_schema_until_explicit_migrate(self) -> None:
        store = StreamingStore(self.db_path)

        self.assertFalse(os.path.exists(self.db_path))

        store.migrate()

        self.assertIn("records", _table_names(self.db_path))
        self.assertIn("record_revisions", _table_names(self.db_path))
        self.assertEqual(_user_version(self.db_path), SCHEMA_VERSION)

    def test_read_only_uri_reads_without_changing_file_mtime_or_user_version(self) -> None:
        writable_store = StreamingStore(self.db_path, auto_migrate=True)
        writable_store.list_jobs()
        before_stat = os.stat(self.db_path)
        before_user_version = _user_version(self.db_path)

        readonly_store = StreamingStore(f"{Path(self.db_path).as_uri()}?mode=ro")

        self.assertEqual(readonly_store.list_jobs(limit=5), [])
        self.assertEqual(readonly_store.count_pending_mappings(), 0)

        after_stat = os.stat(self.db_path)
        after_user_version = _user_version(self.db_path)
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(after_user_version, before_user_version)

    def test_migration_runner_creates_current_schema_and_sets_user_version(self) -> None:
        version = MigrationRunner.run(self.db_path)

        self.assertEqual(version, SCHEMA_VERSION)
        self.assertIn("jobs", _table_names(self.db_path))
        self.assertIn("settings", _table_names(self.db_path))
        self.assertIn("operation_journal", _table_names(self.db_path))
        self.assertIn("export_manifests", _table_names(self.db_path))
        self.assertIn("export_cursor_values", _table_names(self.db_path))
        self.assertNotIn("registry", _table_names(self.db_path))
        self.assertNotIn("raw_evidence", _table_names(self.db_path))
        self.assertNotIn("claimable_alias", _table_names(self.db_path))
        with sqlite3.connect(self.db_path) as conn:
            record_columns = {row[1] for row in conn.execute("PRAGMA table_info(records)").fetchall()}
            revision_columns = {row[1] for row in conn.execute("PRAGMA table_info(record_revisions)").fetchall()}
        self.assertIn("acknowledged_payload_json", record_columns)
        self.assertNotIn("registry_id", record_columns)
        self.assertNotIn("latest_evidence_id", record_columns)
        self.assertNotIn("evidence_id", revision_columns)
        self.assertEqual(_user_version(self.db_path), SCHEMA_VERSION)

    def test_migration_backfills_field_missing_ack_owner_and_audit(self) -> None:
        self._create_legacy_field_missing_db(self.db_path)

        version = MigrationRunner.run(self.db_path)

        self.assertEqual(version, SCHEMA_VERSION)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT state, acknowledged_payload_json
                FROM records
                WHERE record_id = ?
                """,
                ("rec-field-missing-legacy",),
            ).fetchone()
            audit_rows = conn.execute(
                """
                SELECT action, payload_json
                FROM audit_log
                WHERE action = 'field_missing_backfill'
                """
            ).fetchall()
        self.assertEqual(row["state"], "field_missing")
        ack_payload = json.loads(row["acknowledged_payload_json"])
        self.assertNotIn("acknowledged", ack_payload["field_missing"])
        self.assertEqual(ack_payload["field_missing"]["previous_state"], "skipped")
        self.assertEqual(ack_payload["field_missing"]["evidence_source"], "legacy_findings")
        self.assertTrue(ack_payload["field_missing"]["missing_fields_hash"])
        self.assertEqual(len(audit_rows), 1)
        audit_payload = json.loads(audit_rows[0]["payload_json"])
        self.assertEqual(audit_payload["record_id"], "rec-field-missing-legacy")
        self.assertEqual(audit_payload["previous_state"], "skipped")

    def test_sqlite_uri_modes_only_create_parent_dirs_for_writable_paths(self) -> None:
        readonly_db_path = os.path.join(self.temp_dir.name, "readonly", "db.sqlite3")
        readonly_store = StreamingStore(f"{Path(readonly_db_path).as_uri()}?mode=ro")

        self.assertFalse(os.path.exists(os.path.dirname(readonly_db_path)))
        with self.assertRaises(sqlite3.OperationalError):
            readonly_store.list_jobs(limit=5)
        self.assertFalse(os.path.exists(os.path.dirname(readonly_db_path)))

        writable_db_path = os.path.join(self.temp_dir.name, "nested", "db.sqlite3")
        writable_store = StreamingStore(f"{Path(writable_db_path).as_uri()}?mode=rwc", auto_migrate=True)

        self.assertTrue(os.path.isdir(os.path.dirname(writable_db_path)))
        self.assertEqual(writable_store.list_jobs(limit=5), [])
        self.assertEqual(_user_version(writable_db_path), SCHEMA_VERSION)

    def test_migration_runner_persists_user_version_and_backfills_failed_contracts(self) -> None:
        self._create_legacy_failed_record_db(self.db_path)

        version = MigrationRunner.run(self.db_path)

        self.assertEqual(version, SCHEMA_VERSION)
        self.assertEqual(_user_version(self.db_path), SCHEMA_VERSION)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT business_key, identity_anchor, source_identity_json
                FROM records
                WHERE record_id = ?
                """,
                ("rec-1",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(str(row[0]).startswith("failed:"))
        self.assertTrue(str(row[1]))
        self.assertNotEqual(str(row[2]), "{}")

    def test_auto_migrate_opt_in_bootstraps_usable_store(self) -> None:
        store = StreamingStore(self.db_path, auto_migrate=True)

        job_id = store.create_job("manual_import", metadata={"source": "test"})

        self.assertTrue(job_id)
        self.assertEqual(store.get_job(job_id)["job_type"], "manual_import")

    def test_app_service_startup_does_not_create_or_migrate_database(self) -> None:
        app_home = os.path.join(self.temp_dir.name, "app_home")
        docs_home = os.path.join(self.temp_dir.name, "docs_home")
        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": app_home,
                "PEAP_DOCUMENTS_HOME": docs_home,
            },
            clear=False,
        ):
            config = AppConfig.from_env(project_root=self.temp_dir.name)

        self.assertFalse(os.path.exists(config.STREAMING_DB_PATH))

        with patch.object(AppService, "_run_store_maintenance", autospec=True) as maintenance:
            service = AppService(config_obj=config)

        self.assertEqual(service.db_path, os.path.abspath(config.STREAMING_DB_PATH))
        maintenance.assert_not_called()
        self.assertFalse(os.path.exists(config.STREAMING_DB_PATH))

    def _create_legacy_failed_record_db(self, db_path: str) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(LEGACY_RECORDS_SQL)
            conn.executescript(LEGACY_RECORD_REVISIONS_SQL)
            conn.execute(
                """
                INSERT INTO record_revisions (
                    record_id,
                    revision_hash,
                    parser_payload_json,
                    postprocess_payload_json,
                    findings_json,
                    state,
                    source_file,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rec-1",
                    "hash-1",
                    "{}",
                    "{}",
                    "[]",
                    "parse_failed",
                    "folder/file.html",
                    "2024-01-01 00:00:00",
                ),
            )
            revision_id = conn.execute(
                "SELECT revision_id FROM record_revisions WHERE record_id = ?",
                ("rec-1",),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO records (
                    record_id,
                    business_key,
                    record_family,
                    project_code,
                    project_name,
                    project_type,
                    exchange,
                    listing_date,
                    state,
                    source_file,
                    archive_path,
                    latest_revision_id,
                    last_error_type,
                    last_error_message,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rec-1",
                    "legacy-key",
                    "",
                    "P",
                    "",
                    "",
                    "",
                    "",
                    "parse_failed",
                    "folder/file.html",
                    "",
                    revision_id,
                    "",
                    "",
                    "2024-01-01 00:00:00",
                    "2024-01-01 00:00:00",
                ),
            )
            conn.execute("PRAGMA user_version = 0")

    def _create_legacy_field_missing_db(self, db_path: str) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(LEGACY_RECORDS_SQL)
            conn.executescript(LEGACY_RECORD_REVISIONS_SQL)
            findings = json.dumps(
                [
                    {
                        "severity": "warn",
                        "type": "export_field_missing",
                        "message": "导出字段缺失：类型",
                        "evidence": {"missing_fields": ["类型"]},
                    }
                ],
                ensure_ascii=False,
            )
            conn.execute(
                """
                INSERT INTO record_revisions (
                    record_id,
                    revision_hash,
                    parser_payload_json,
                    postprocess_payload_json,
                    findings_json,
                    state,
                    source_file,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rec-field-missing-legacy",
                    "hash-field-missing",
                    "{}",
                    "{}",
                    findings,
                    "skipped",
                    "folder/field-missing.html",
                    "2024-01-01 00:00:00",
                ),
            )
            revision_id = conn.execute(
                "SELECT revision_id FROM record_revisions WHERE record_id = ?",
                ("rec-field-missing-legacy",),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO records (
                    record_id,
                    business_key,
                    record_family,
                    project_code,
                    project_name,
                    project_type,
                    exchange,
                    listing_date,
                    state,
                    source_file,
                    archive_path,
                    latest_revision_id,
                    last_error_type,
                    last_error_message,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rec-field-missing-legacy",
                    "legacy-field-missing-key",
                    "listing",
                    "G32024LEGACY",
                    "legacy missing",
                    "股权转让",
                    "shanghai",
                    "2024-01-01",
                    "skipped",
                    "folder/field-missing.html",
                    "",
                    revision_id,
                    "",
                    "",
                    "2024-01-01 00:00:00",
                    "2024-01-01 00:00:00",
                ),
            )
            conn.execute("PRAGMA user_version = 0")


if __name__ == "__main__":
    unittest.main()
