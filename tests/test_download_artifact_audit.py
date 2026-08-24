from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from path_isolation import PROTECTED_PEAP_HOME

from peap.download_artifact_audit import (
    STALE_DOWNLOAD_OPERATION_CODE,
    _evidence_verdict_to_dict,
    build_download_artifact_audit,
)
from peap.download_tasks import build_task_registry


class DownloadArtifactAuditTest(unittest.TestCase):
    REAL_PEAP_HOME = PROTECTED_PEAP_HOME

    @contextmanager
    def _isolated_peap_env(self, tmp_dir: str):
        env = {
            "PEAP_PROTECTED_WORKSPACE_ROOTS": self.REAL_PEAP_HOME,
            "PEAP_APP_HOME": os.path.join(tmp_dir, "app-home"),
            "PEAP_DATA_ROOT": os.path.join(tmp_dir, "data"),
            "PEAP_ARCHIVE_ROOT": os.path.join(tmp_dir, "archive"),
            "PEAP_EXPORT_ROOT": os.path.join(tmp_dir, "exports"),
            "PEAP_CACHE_DIR": os.path.join(tmp_dir, "cache"),
            "PEAP_STREAMING_DB_PATH": os.path.join(tmp_dir, "streaming.sqlite3"),
        }
        with patch.dict(os.environ, env, clear=False):
            yield

    def _init_db(self, db_path: str) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE records (
                    record_id TEXT PRIMARY KEY,
                    record_family TEXT NOT NULL,
                    business_id TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    listing_date TEXT NOT NULL DEFAULT '',
                    source_file TEXT NOT NULL DEFAULT '',
                    archive_path TEXT NOT NULL DEFAULT '',
                    latest_revision_id INTEGER,
                    source_identity_json TEXT NOT NULL DEFAULT '{}',
                    project_code TEXT NOT NULL DEFAULT '',
                    project_name TEXT NOT NULL DEFAULT '',
                    artifact_status TEXT NOT NULL DEFAULT 'ok',
                    last_operation_kind TEXT NOT NULL DEFAULT '',
                    last_operation_code TEXT NOT NULL DEFAULT '',
                    last_operation_message TEXT NOT NULL DEFAULT '',
                    last_operation_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE record_revisions (
                    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT NOT NULL,
                    canonical_record_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )

    def test_missing_streaming_db_path_fails_closed_without_creating_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                db_path = os.path.join(tmp_dir, "missing-streaming.sqlite3")
                args = SimpleNamespace(
                    exchange="sse",
                    record_family="listing",
                    business_id="physical_asset",
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                )
                config = SimpleNamespace(STREAMING_DB_PATH=db_path)
                spec = build_task_registry()["sse:listing:physical_asset"]

                with self.assertRaisesRegex(FileNotFoundError, "STREAMING_DB_PATH"):
                    build_download_artifact_audit(config, args=args, tasks=[spec])

                self.assertFalse(os.path.exists(db_path))

    def test_blank_streaming_db_path_fails_closed_before_current_directory_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                args = SimpleNamespace(
                    exchange="sse",
                    record_family="listing",
                    business_id="physical_asset",
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                )
                config = SimpleNamespace(STREAMING_DB_PATH="")
                spec = build_task_registry()["sse:listing:physical_asset"]

                with (
                    patch(
                        "peap.download_artifact_audit.os.path.isfile",
                        side_effect=AssertionError("blank STREAMING_DB_PATH probed filesystem"),
                    ),
                    self.assertRaisesRegex(ValueError, "STREAMING_DB_PATH"),
                ):
                    build_download_artifact_audit(config, args=args, tasks=[spec])

    def test_uses_latest_canonical_identity_when_record_scope_is_blank_for_task_matching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                db_path = os.path.join(tmp_dir, "streaming.sqlite3")
                missing_path = os.path.join(tmp_dir, "canonical-missing.bin")
                self._init_db(db_path)
                canonical_record = {
                    "business_identity": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                    },
                    "source_identity": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "source_id": "sse",
                    },
                    "canonical_fields": {"project_code": "XM-CANONICAL-AUDIT"},
                }
                with sqlite3.connect(db_path) as conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO record_revisions (record_id, canonical_record_json)
                        VALUES (?, ?)
                        """,
                        (
                            "canonical-scope-missing",
                            json.dumps(canonical_record, separators=(",", ":")),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO records (
                            record_id, record_family, business_id, exchange, listing_date,
                            source_file, archive_path, latest_revision_id, project_code, project_name
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "canonical-scope-missing",
                            "",
                            "",
                            "sse",
                            "2026-05-08",
                            missing_path,
                            missing_path,
                            cursor.lastrowid,
                            "XM-CANONICAL-AUDIT",
                            "canonical identity missing artifact",
                        ),
                    )
                args = SimpleNamespace(
                    exchange="sse",
                    record_family="listing",
                    business_id="physical_asset",
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                    dry_run=True,
                )
                config = SimpleNamespace(STREAMING_DB_PATH=db_path)
                spec = build_task_registry()["sse:listing:physical_asset"]

                audit = build_download_artifact_audit(config, args=args, tasks=[spec])

                self.assertEqual(audit.stale_count, 1)
                task_audit = audit.for_task("sse:listing:physical_asset")
                self.assertIsNotNone(task_audit)
                stale_record = task_audit.stale_records[0]
                self.assertEqual(stale_record.record_id, "canonical-scope-missing")
                self.assertEqual(stale_record.evidence_verdict["identity_confidence"], "verified")

    def test_marks_missing_declared_artifact_without_downloading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                self._assert_marks_missing_declared_artifact_without_downloading(tmp_dir)

    def _assert_marks_missing_declared_artifact_without_downloading(self, tmp_dir: str) -> None:
        db_path = os.path.join(tmp_dir, "streaming.sqlite3")
        existing_path = os.path.join(tmp_dir, "existing.html")
        source_only_path = os.path.join(tmp_dir, "source-only.html")
        with open(existing_path, "w", encoding="utf-8") as handle:
            handle.write("<html>ok</html>")
        with open(source_only_path, "w", encoding="utf-8") as handle:
            handle.write("<html>source exists</html>")
        missing_path = os.path.join(tmp_dir, "missing.html")
        missing_archive_path = os.path.join(tmp_dir, "missing-archive.html")
        self._init_db(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.executemany(
                """
                    INSERT INTO records (
                        record_id, record_family, business_id, exchange, listing_date,
                        source_file, archive_path, project_code, project_name
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                [
                    (
                        "missing",
                        "listing",
                        "physical_asset",
                        "sse",
                        "2026-05-08",
                        missing_path,
                        missing_path,
                        "XM-MISSING",
                        "missing artifact",
                    ),
                    (
                        "existing",
                        "listing",
                        "physical_asset",
                        "sse",
                        "2026-05-08",
                        existing_path,
                        existing_path,
                        "XM-OK",
                        "existing artifact",
                    ),
                    (
                        "missing-archive",
                        "listing",
                        "physical_asset",
                        "上交所",
                        "2026-05-08",
                        source_only_path,
                        missing_archive_path,
                        "XM-MISSING-ARCHIVE",
                        "missing archive artifact",
                    ),
                    (
                        "outside-date",
                        "listing",
                        "physical_asset",
                        "sse",
                        "2026-04-01",
                        os.path.join(tmp_dir, "old.html"),
                        os.path.join(tmp_dir, "old.html"),
                        "XM-OLD",
                        "outside date range",
                    ),
                ],
            )
        args = SimpleNamespace(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            start_date="2026-05-01",
            end_date="2026-05-31",
        )
        config = SimpleNamespace(STREAMING_DB_PATH=db_path)
        spec = build_task_registry()["sse:listing:physical_asset"]

        audit = build_download_artifact_audit(config, args=args, tasks=[spec])

        self.assertEqual(audit.stale_count, 2)
        task_audit = audit.for_task("sse:listing:physical_asset")
        self.assertIsNotNone(task_audit)
        self.assertEqual(task_audit.stale_records[0].record_id, "missing")
        self.assertEqual(
            {item.record_id for item in task_audit.stale_records},
            {"missing", "missing-archive"},
        )
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT artifact_status, last_operation_code FROM records WHERE record_id = 'missing'"
            ).fetchone()
        self.assertEqual(row, ("missing", STALE_DOWNLOAD_OPERATION_CODE))

    def test_dry_run_reports_stale_evidence_without_db_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                self._assert_dry_run_reports_stale_evidence_without_db_writes(tmp_dir)

    def _assert_dry_run_reports_stale_evidence_without_db_writes(self, tmp_dir: str) -> None:
        db_path = os.path.join(tmp_dir, "streaming.sqlite3")
        missing_path = os.path.join(tmp_dir, "missing.bin")
        self._init_db(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                    INSERT INTO records (
                        record_id, record_family, business_id, exchange, listing_date,
                        source_file, archive_path, project_code, project_name
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    "dry-run-missing",
                    "listing",
                    "physical_asset",
                    "sse",
                    "2026-05-08",
                    missing_path,
                    missing_path,
                    "XM-DRY-RUN",
                    "dry run missing artifact",
                ),
            )
        args = SimpleNamespace(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            start_date="2026-05-01",
            end_date="2026-05-31",
            dry_run=True,
        )
        config = SimpleNamespace(STREAMING_DB_PATH=db_path)
        spec = build_task_registry()["sse:listing:physical_asset"]

        audit = build_download_artifact_audit(config, args=args, tasks=[spec])

        self.assertEqual(audit.stale_count, 1)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """
                    SELECT artifact_status, last_operation_code
                    FROM records
                    WHERE record_id = 'dry-run-missing'
                    """
            ).fetchone()
        self.assertEqual(row, ("ok", ""))

    def test_audit_output_uses_safe_evidence_verdict_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                self._assert_audit_output_uses_safe_evidence_verdict_schema(tmp_dir)

    def _assert_audit_output_uses_safe_evidence_verdict_schema(self, tmp_dir: str) -> None:
        db_path = os.path.join(tmp_dir, "streaming.sqlite3")
        missing_path = os.path.join(tmp_dir, "missing.bin")
        self._init_db(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                    INSERT INTO records (
                        record_id, record_family, business_id, exchange, listing_date,
                        source_file, archive_path, project_code, project_name
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    "safe-evidence-missing",
                    "listing",
                    "physical_asset",
                    "sse",
                    "2026-05-08",
                    missing_path,
                    missing_path,
                    "XM-SAFE-EVIDENCE",
                    "safe evidence missing artifact",
                ),
            )
        args = SimpleNamespace(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            start_date="2026-05-01",
            end_date="2026-05-31",
            dry_run=True,
        )
        config = SimpleNamespace(STREAMING_DB_PATH=db_path)
        spec = build_task_registry()["sse:listing:physical_asset"]

        audit = build_download_artifact_audit(config, args=args, tasks=[spec])
        sample = audit.to_dict()["tasks"]["sse:listing:physical_asset"]["samples"][0]

        verdict = sample["evidence_verdict"]
        self.assertEqual(verdict["status"], "stale_reference")
        self.assertEqual(verdict["identity_confidence"], "verified")
        self.assertEqual(verdict["reason_code"], "authoritative_artifact_missing")
        self.assertEqual(verdict["safe_evidence"]["path_authority"], "archive_path")
        serialized = repr(verdict["safe_evidence"])
        self.assertIn(missing_path, serialized)
        self.assertNotIn("safe evidence missing artifact", serialized)

    def test_reports_present_unverified_and_identity_mismatch_as_unsafe_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                db_path = os.path.join(tmp_dir, "streaming.sqlite3")
                unverified_path = os.path.join(tmp_dir, "present-unverified.html")
                mismatch_path = os.path.join(tmp_dir, "identity-mismatch.html")
                with open(unverified_path, "w", encoding="utf-8") as handle:
                    handle.write("<html>source evidence</html>")
                with open(mismatch_path, "w", encoding="utf-8") as handle:
                    handle.write("<html>source evidence</html>")
                self._init_db(db_path)
                with sqlite3.connect(db_path) as conn:
                    conn.executemany(
                        """
                        INSERT INTO records (
                            record_id, record_family, business_id, exchange, listing_date,
                            source_file, archive_path, source_identity_json, project_code, project_name
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                "present-unverified",
                                "listing",
                                "physical_asset",
                                "sse",
                                "2026-05-08",
                                unverified_path,
                                unverified_path,
                                "{}",
                                "",
                                "unverified identity",
                            ),
                            (
                                "identity-mismatch",
                                "listing",
                                "physical_asset",
                                "sse",
                                "2026-05-08",
                                mismatch_path,
                                mismatch_path,
                                '{"project_code":"XM-EVIDENCE"}',
                                "XM-DB",
                                "identity mismatch",
                            ),
                        ],
                    )
                args = SimpleNamespace(
                    exchange="sse",
                    record_family="listing",
                    business_id="physical_asset",
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                    dry_run=True,
                )
                config = SimpleNamespace(STREAMING_DB_PATH=db_path)
                spec = build_task_registry()["sse:listing:physical_asset"]

                audit = build_download_artifact_audit(config, args=args, tasks=[spec])

                task_audit = audit.for_task("sse:listing:physical_asset")
                self.assertIsNotNone(task_audit)
                statuses_by_id = {
                    item.record_id: item.evidence_verdict["status"]
                    for item in task_audit.stale_records
                }
                self.assertEqual(
                    statuses_by_id,
                    {
                        "present-unverified": "present_unverified",
                        "identity-mismatch": "identity_mismatch",
                    },
                )

    def test_rejects_real_workspace_db_path_before_filesystem_probe(self) -> None:
        real_db_path = os.path.join(self.REAL_PEAP_HOME, "data", "streaming_ingest.sqlite3")
        args = SimpleNamespace(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            start_date="2026-05-01",
            end_date="2026-05-31",
        )
        config = SimpleNamespace(STREAMING_DB_PATH=real_db_path)
        spec = build_task_registry()["sse:listing:physical_asset"]

        def forbid_isfile(path: object) -> bool:
            if str(path).startswith(self.REAL_PEAP_HOME):
                raise AssertionError(f"attempted filesystem probe for forbidden PEAP path: {path}")
            return False

        with patch.dict(
            os.environ,
            {"PEAP_PROTECTED_WORKSPACE_ROOTS": self.REAL_PEAP_HOME},
            clear=False,
        ), patch("peap.download_artifact_audit.os.path.isfile", side_effect=forbid_isfile):
            with self.assertRaisesRegex(ValueError, "real PEAP workspace"):
                build_download_artifact_audit(config, args=args, tasks=[spec])

    def test_surfaces_schema_errors_instead_of_returning_empty_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                db_path = os.path.join(tmp_dir, "streaming.sqlite3")
                with sqlite3.connect(db_path):
                    pass
                args = SimpleNamespace(
                    exchange="sse",
                    record_family="listing",
                    business_id="physical_asset",
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                )
                config = SimpleNamespace(STREAMING_DB_PATH=db_path)
                spec = build_task_registry()["sse:listing:physical_asset"]

                with self.assertRaises(sqlite3.Error):
                    build_download_artifact_audit(config, args=args, tasks=[spec])

    def test_surfaces_invalid_canonical_record_json_instead_of_falling_back_to_empty_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                db_path = os.path.join(tmp_dir, "streaming.sqlite3")
                missing_path = os.path.join(tmp_dir, "invalid-canonical-json.bin")
                self._init_db(db_path)
                with sqlite3.connect(db_path) as conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO record_revisions (record_id, canonical_record_json)
                        VALUES (?, ?)
                        """,
                        ("invalid-canonical-json", "{"),
                    )
                    conn.execute(
                        """
                        INSERT INTO records (
                            record_id, record_family, business_id, exchange, listing_date,
                            source_file, archive_path, latest_revision_id, project_code, project_name
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "invalid-canonical-json",
                            "",
                            "",
                            "sse",
                            "2026-05-08",
                            missing_path,
                            missing_path,
                            cursor.lastrowid,
                            "XM-INVALID-CANONICAL",
                            "invalid canonical json",
                        ),
                    )
                args = SimpleNamespace(
                    exchange="sse",
                    record_family="listing",
                    business_id="physical_asset",
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                    dry_run=True,
                )
                config = SimpleNamespace(STREAMING_DB_PATH=db_path)
                spec = build_task_registry()["sse:listing:physical_asset"]

                with self.assertRaises(json.JSONDecodeError):
                    build_download_artifact_audit(config, args=args, tasks=[spec])

    def test_surfaces_invalid_source_identity_json_even_when_canonical_identity_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                db_path = os.path.join(tmp_dir, "streaming.sqlite3")
                missing_path = os.path.join(tmp_dir, "invalid-source-identity-json.bin")
                self._init_db(db_path)
                canonical_record = {
                    "business_identity": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                    },
                    "source_identity": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "source_id": "sse",
                        "project_code": "XM-CANONICAL-SOURCE",
                    },
                }
                with sqlite3.connect(db_path) as conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO record_revisions (record_id, canonical_record_json)
                        VALUES (?, ?)
                        """,
                        (
                            "invalid-source-identity-json",
                            json.dumps(canonical_record, separators=(",", ":")),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO records (
                            record_id, record_family, business_id, exchange, listing_date,
                            source_file, archive_path, latest_revision_id,
                            source_identity_json, project_code, project_name
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "invalid-source-identity-json",
                            "",
                            "",
                            "sse",
                            "2026-05-08",
                            missing_path,
                            missing_path,
                            cursor.lastrowid,
                            "{",
                            "XM-CANONICAL-SOURCE",
                            "invalid source identity json",
                        ),
                    )
                args = SimpleNamespace(
                    exchange="sse",
                    record_family="listing",
                    business_id="physical_asset",
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                    dry_run=True,
                )
                config = SimpleNamespace(STREAMING_DB_PATH=db_path)
                spec = build_task_registry()["sse:listing:physical_asset"]

                with self.assertRaises(json.JSONDecodeError):
                    build_download_artifact_audit(config, args=args, tasks=[spec])

    def test_rejects_non_object_canonical_identity_fields_before_artifact_truth(self) -> None:
        identity_shapes = {
            "business_identity": [],
            "source_identity": [],
        }
        for field_name, invalid_identity in identity_shapes.items():
            with self.subTest(field_name=field_name):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    with self._isolated_peap_env(tmp_dir):
                        db_path = os.path.join(tmp_dir, "streaming.sqlite3")
                        missing_path = os.path.join(tmp_dir, f"{field_name}-non-object.bin")
                        self._init_db(db_path)
                        canonical_record = {
                            "business_identity": {
                                "record_family": "listing",
                                "business_id": "physical_asset",
                            },
                            "source_identity": {
                                "record_family": "listing",
                                "business_id": "physical_asset",
                                "source_id": "sse",
                            },
                        }
                        canonical_record[field_name] = invalid_identity
                        with sqlite3.connect(db_path) as conn:
                            cursor = conn.execute(
                                """
                                INSERT INTO record_revisions (record_id, canonical_record_json)
                                VALUES (?, ?)
                                """,
                                (
                                    f"{field_name}-non-object",
                                    json.dumps(canonical_record, separators=(",", ":")),
                                ),
                            )
                            conn.execute(
                                """
                                INSERT INTO records (
                                    record_id, record_family, business_id, exchange, listing_date,
                                    source_file, archive_path, latest_revision_id, project_code, project_name
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    f"{field_name}-non-object",
                                    "listing",
                                    "physical_asset",
                                    "sse",
                                    "2026-05-08",
                                    missing_path,
                                    missing_path,
                                    cursor.lastrowid,
                                    "XM-NON-OBJECT-CANONICAL",
                                    "non-object canonical identity",
                                ),
                            )
                        args = SimpleNamespace(
                            exchange="sse",
                            record_family="listing",
                            business_id="physical_asset",
                            start_date="2026-05-01",
                            end_date="2026-05-31",
                            dry_run=True,
                        )
                        config = SimpleNamespace(STREAMING_DB_PATH=db_path)
                        spec = build_task_registry()["sse:listing:physical_asset"]
                        verdict_calls: list[object] = []

                        def stale_verdict(
                            record: object,
                            *,
                            verdict_calls: list[object] = verdict_calls,
                            missing_path: str = missing_path,
                        ) -> SimpleNamespace:
                            verdict_calls.append(record)
                            return SimpleNamespace(
                                status="stale_reference",
                                logical_record_identity="listing:physical_asset:XM-NON-OBJECT-CANONICAL",
                                identity_confidence="verified",
                                authoritative_path=missing_path,
                                inspection_openable_path="",
                                reason_code="authoritative_artifact_missing",
                                safe_evidence={"path_authority": "archive_path"},
                            )

                        with patch(
                            "peap.download_artifact_audit.resolve_artifact_evidence_verdict",
                            side_effect=stale_verdict,
                        ):
                            with self.assertRaisesRegex(TypeError, field_name):
                                build_download_artifact_audit(config, args=args, tasks=[spec])

                        self.assertEqual(verdict_calls, [])

    def test_rejects_non_object_source_identity_json_even_when_canonical_identity_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                db_path = os.path.join(tmp_dir, "streaming.sqlite3")
                missing_path = os.path.join(tmp_dir, "non-object-source-identity-json.bin")
                self._init_db(db_path)
                canonical_record = {
                    "business_identity": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                    },
                    "source_identity": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "source_id": "sse",
                        "project_code": "XM-CANONICAL-SOURCE",
                    },
                }
                with sqlite3.connect(db_path) as conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO record_revisions (record_id, canonical_record_json)
                        VALUES (?, ?)
                        """,
                        (
                            "non-object-source-identity-json",
                            json.dumps(canonical_record, separators=(",", ":")),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO records (
                            record_id, record_family, business_id, exchange, listing_date,
                            source_file, archive_path, latest_revision_id,
                            source_identity_json, project_code, project_name
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "non-object-source-identity-json",
                            "",
                            "",
                            "sse",
                            "2026-05-08",
                            missing_path,
                            missing_path,
                            cursor.lastrowid,
                            "[]",
                            "XM-CANONICAL-SOURCE",
                            "non-object source identity json",
                        ),
                    )
                args = SimpleNamespace(
                    exchange="sse",
                    record_family="listing",
                    business_id="physical_asset",
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                    dry_run=True,
                )
                config = SimpleNamespace(STREAMING_DB_PATH=db_path)
                spec = build_task_registry()["sse:listing:physical_asset"]
                verdict_calls: list[object] = []

                with patch(
                    "peap.download_artifact_audit.resolve_artifact_evidence_verdict",
                    side_effect=lambda record: verdict_calls.append(record),
                ):
                    with self.assertRaisesRegex(TypeError, "source_identity_json"):
                        build_download_artifact_audit(config, args=args, tasks=[spec])

                self.assertEqual(verdict_calls, [])

    def test_rejects_non_object_source_identity_json_even_without_canonical_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                db_path = os.path.join(tmp_dir, "streaming.sqlite3")
                missing_path = os.path.join(tmp_dir, "non-object-source-identity-json-no-canonical.bin")
                self._init_db(db_path)
                with sqlite3.connect(db_path) as conn:
                    conn.execute(
                        """
                        INSERT INTO records (
                            record_id, record_family, business_id, exchange, listing_date,
                            source_file, archive_path, source_identity_json, project_code, project_name
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "non-object-source-identity-json-no-canonical",
                            "listing",
                            "physical_asset",
                            "sse",
                            "2026-05-08",
                            missing_path,
                            missing_path,
                            "[]",
                            "XM-NO-CANONICAL-SOURCE",
                            "non-object source identity json without canonical",
                        ),
                    )
                args = SimpleNamespace(
                    exchange="sse",
                    record_family="listing",
                    business_id="physical_asset",
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                    dry_run=True,
                )
                config = SimpleNamespace(STREAMING_DB_PATH=db_path)
                spec = build_task_registry()["sse:listing:physical_asset"]

                with patch(
                    "peap.download_artifact_audit.resolve_artifact_evidence_verdict",
                    return_value=SimpleNamespace(
                        status="stale_reference",
                        logical_record_identity="listing:physical_asset:XM-NO-CANONICAL-SOURCE",
                        identity_confidence="unverified",
                        authoritative_path=missing_path,
                        inspection_openable_path="",
                        reason_code="authoritative_artifact_missing",
                        safe_evidence={"path_authority": "archive_path"},
                    ),
                ) as resolver:
                    with self.assertRaisesRegex(TypeError, "source_identity_json"):
                        build_download_artifact_audit(config, args=args, tasks=[spec])

                resolver.assert_not_called()

    def test_evidence_verdict_to_dict_rejects_missing_required_fields(self) -> None:
        verdict = SimpleNamespace(
            status="stale_reference",
            logical_record_identity="listing:physical_asset:XM-MISSING-FIELD",
            identity_confidence="verified",
            authoritative_path="/tmp/missing.bin",
            inspection_openable_path="",
            safe_evidence={"path_authority": "archive_path"},
        )

        with self.assertRaisesRegex(TypeError, "reason_code"):
            _evidence_verdict_to_dict(verdict)

    def test_evidence_verdict_to_dict_rejects_non_mapping_safe_evidence(self) -> None:
        verdict = SimpleNamespace(
            status="stale_reference",
            logical_record_identity="listing:physical_asset:XM-BAD-SAFE-EVIDENCE",
            identity_confidence="verified",
            authoritative_path="/tmp/missing.bin",
            inspection_openable_path="",
            reason_code="authoritative_artifact_missing",
            safe_evidence=[("path_authority", "archive_path")],
        )

        with self.assertRaisesRegex(TypeError, "safe_evidence"):
            _evidence_verdict_to_dict(verdict)

    def test_rejects_real_workspace_row_path_before_artifact_truth_or_db_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self._isolated_peap_env(tmp_dir):
                db_path = os.path.join(tmp_dir, "streaming.sqlite3")
                forbidden_path = os.path.join(self.REAL_PEAP_HOME, "archive", "forbidden-row.html")
                self._init_db(db_path)
                with sqlite3.connect(db_path) as conn:
                    conn.execute(
                        """
                        INSERT INTO records (
                            record_id, record_family, business_id, exchange, listing_date,
                            source_file, archive_path, project_code, project_name
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "forbidden-row-path",
                            "listing",
                            "physical_asset",
                            "sse",
                            "2026-05-08",
                            forbidden_path,
                            forbidden_path,
                            "XM-FORBIDDEN",
                            "forbidden row path",
                        ),
                    )
                args = SimpleNamespace(
                    exchange="sse",
                    record_family="listing",
                    business_id="physical_asset",
                    start_date="2026-05-01",
                    end_date="2026-05-31",
                )
                config = SimpleNamespace(STREAMING_DB_PATH=db_path)
                spec = build_task_registry()["sse:listing:physical_asset"]
                verdict_calls: list[str] = []

                def stale_verdict(record: object) -> SimpleNamespace:
                    verdict_calls.append(str(record["archive_path"]))
                    return SimpleNamespace(
                        status="stale_reference",
                        logical_record_identity="listing:physical_asset:XM-FORBIDDEN",
                        identity_confidence="verified",
                        authoritative_path=forbidden_path,
                        inspection_openable_path="",
                        reason_code="authoritative_artifact_missing",
                        safe_evidence={"authoritative_path": forbidden_path},
                    )

                caught: ValueError | None = None
                with patch("peap.download_artifact_audit.resolve_artifact_evidence_verdict", side_effect=stale_verdict):
                    try:
                        build_download_artifact_audit(config, args=args, tasks=[spec])
                    except ValueError as exc:
                        caught = exc

                with sqlite3.connect(db_path) as conn:
                    row = conn.execute(
                        """
                        SELECT artifact_status, last_operation_code
                        FROM records
                        WHERE record_id = 'forbidden-row-path'
                        """
                    ).fetchone()

                self.assertIsNotNone(caught)
                self.assertIn("real PEAP workspace", str(caught))
                self.assertEqual(verdict_calls, [])
                self.assertEqual(row, ("ok", ""))


if __name__ == "__main__":
    unittest.main()
