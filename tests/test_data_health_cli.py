from __future__ import annotations

import inspect
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from unittest.mock import patch

from path_isolation import assert_peap_env_under_temp, isolated_peap_env

from peap import failure_repair, operations_admin
from peap.cli import main as peap_main
from peap.migrations import MigrationRunner
from peap.streaming_models import IngestedRecord, PostProcessFinding
from peap.streaming_store import SCHEMA_VERSION, StreamingStore
from peap.streaming_store_maintenance import run_streaming_store_maintenance


@contextmanager
def _isolated_admin_env(temp_dir: str):
    with patch.dict(os.environ, isolated_peap_env(temp_dir, app_home=temp_dir), clear=True):
        assert_peap_env_under_temp(unittest.TestCase(), temp_dir)
        yield


class DataHealthCliTest(unittest.TestCase):
    REAL_PEAP_HOME = "/Users/rtoc/Documents/PEAP"

    def test_data_health_rejects_real_peap_workspace(self) -> None:
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = peap_main(["data-health", "--app-home", self.REAL_PEAP_HOME])

        payload = json.loads(stdout.getvalue())
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(payload["result"], "forbidden_real_workspace")

    def test_recover_operation_is_not_a_public_cli_command(self) -> None:
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            peap_main(["recover-operation", "--app-home", self.REAL_PEAP_HOME])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("recover-operation", stderr.getvalue())

    def test_admin_helpers_do_not_import_desktop_backend(self) -> None:
        self.assertNotIn("desktop_backend", inspect.getsource(operations_admin))
        self.assertNotIn("desktop_backend", inspect.getsource(failure_repair))

    def test_data_health_emits_json_summary_without_creating_export_or_archive_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["db"]["path"], db_path)
            self.assertTrue(payload["db"]["exists"])
            self.assertEqual(payload["schema"]["user_version"], payload["schema"]["expected_user_version"])
            self.assertTrue(payload["operation_journal"]["table_exists"])
            self.assertFalse(os.path.exists(os.path.join(temp_dir, "archive")))
            self.assertFalse(os.path.exists(os.path.join(temp_dir, "exports")))

    def test_data_health_fails_when_operation_journal_table_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            with sqlite3.connect(db_path) as conn:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.commit()

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(payload["result"], "operation_journal_missing")
            self.assertFalse(payload["healthy"])
            self.assertFalse(payload["operation_journal"]["table_exists"])
            finding_codes = {item["code"] for item in payload["findings"]}
            self.assertIn("operation_journal_missing", finding_codes)

    def test_data_health_fails_when_required_core_schema_tables_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            with sqlite3.connect(db_path) as conn:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.execute(
                    """
                    CREATE TABLE operation_journal (
                        operation_id TEXT PRIMARY KEY,
                        operation_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        recovery_state TEXT NOT NULL DEFAULT '',
                        started_at TEXT NOT NULL,
                        finished_at TEXT NOT NULL DEFAULT '',
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        manifest_json TEXT NOT NULL DEFAULT '{}',
                        error_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                conn.commit()

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(payload["result"], "schema_incomplete")
            self.assertFalse(payload["healthy"])
            self.assertTrue(payload["operation_journal"]["table_exists"])
            missing_tables = {
                item["table"]
                for item in payload["findings"]
                if item["code"] == "required_table_missing"
            }
            self.assertEqual(missing_tables, {"records", "jobs", "exports"})

    def test_data_health_reports_pending_and_failed_operation_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO operation_journal (
                        operation_id, operation_type, status, recovery_state, started_at, finished_at,
                        metadata_json, manifest_json, error_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("op-pending", "manual_import", "pending", "", "2026-01-01T00:00:00Z", "", "{}", "{}", "{}"),
                )
                conn.execute(
                    """
                    INSERT INTO operation_journal (
                        operation_id, operation_type, status, recovery_state, started_at, finished_at,
                        metadata_json, manifest_json, error_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("op-failed", "export_excel", "failed", "", "2026-01-01T01:00:00Z", "", "{}", "{}", "{}"),
                )
                conn.commit()

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(payload["result"], "operation_journal_unhealthy")
            self.assertFalse(payload["healthy"])
            self.assertEqual(payload["operation_journal"]["pending_count"], 1)
            self.assertEqual(payload["operation_journal"]["failed_count"], 1)
            finding_codes = {item["code"] for item in payload["findings"]}
            self.assertIn("pending_operations", finding_codes)
            self.assertIn("failed_operations", finding_codes)

    def test_data_health_reports_schema_version_mismatch_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)
            with sqlite3.connect(db_path) as conn:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
                conn.commit()

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(payload["result"], "schema_version_mismatch")
            self.assertFalse(payload["healthy"])
            self.assertEqual(payload["schema"]["expected_user_version"], SCHEMA_VERSION)
            self.assertEqual(payload["schema"]["user_version"], SCHEMA_VERSION - 1)
            self.assertFalse(payload["schema"]["matches"])
            finding_codes = {item["code"] for item in payload["findings"]}
            self.assertIn("schema_version_mismatch", finding_codes)

    def test_data_health_reports_present_unverified_artifact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            archive_dir = os.path.join(temp_dir, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            evidence_path = os.path.join(archive_dir, "present-unverified.bin")
            with open(evidence_path, "wb") as handle:
                handle.write(b"fixture evidence bytes")
            MigrationRunner.run(db_path)

            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_record(
                IngestedRecord(
                    record_id="rec-present-unverified",
                    revision_hash="hash-present-unverified",
                    project_code="G32026TEST201",
                    project_name="present unverified fixture",
                    project_type="",
                    exchange="sse",
                    listing_date="2026-05-01",
                    state="ready",
                    source_file=evidence_path,
                    archive_path=evidence_path,
                    parser_payload={"project_code": "G32026TEST201"},
                    postprocess_payload={"project_code": "G32026TEST201"},
                    findings=[],
                )
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            report = payload["artifact_evidence_report"]
            self.assertEqual(report["mode"], "report_only")
            self.assertEqual(report["present_unverified_count"], 1)
            self.assertEqual(report["records"], [
                {
                    "record_id": "rec-present-unverified",
                    "classification": "present_unverified",
                    "reason_code": "identity_unresolved_artifact_present",
                    "identity_confidence": "unresolved",
                    "inspection_eligible": True,
                }
            ])
            self.assertNotIn("fixture evidence bytes", json.dumps(report, ensure_ascii=False))
            self.assertNotIn("relink", json.dumps(report, ensure_ascii=False))
            self.assertNotIn("redownload", json.dumps(report, ensure_ascii=False))

    def test_data_health_reports_stale_reference_artifact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            archive_dir = os.path.join(temp_dir, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            source_path = os.path.join(archive_dir, "legacy-source.html")
            missing_archive_path = os.path.join(archive_dir, "missing-authoritative.html")
            with open(source_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>legacy source</body></html>")
            MigrationRunner.run(db_path)

            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_record(
                IngestedRecord(
                    record_id="rec-stale-reference",
                    revision_hash="hash-stale-reference",
                    project_code="G32026TEST202",
                    project_name="stale reference fixture",
                    project_type="",
                    exchange="sse",
                    listing_date="2026-05-01",
                    state="ready",
                    source_file=source_path,
                    archive_path=missing_archive_path,
                    parser_payload={"project_code": "G32026TEST202"},
                    postprocess_payload={"project_code": "G32026TEST202"},
                    findings=[],
                    source_identity={
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "project_code": "G32026TEST202",
                        "exchange": "sse",
                    },
                )
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            report = payload["artifact_evidence_report"]
            self.assertEqual(report["mode"], "report_only")
            self.assertEqual(report["stale_reference_count"], 1)
            self.assertEqual(report["records"], [
                {
                    "record_id": "rec-stale-reference",
                    "classification": "stale_reference",
                    "reason_code": "authoritative_artifact_missing",
                    "identity_confidence": "verified",
                    "inspection_eligible": False,
                }
            ])
            serialized_report = json.dumps(report, ensure_ascii=False)
            self.assertNotIn("legacy source", serialized_report)
            self.assertNotIn("relink", serialized_report)
            self.assertNotIn("redownload", serialized_report)

    def test_data_health_reports_undeclared_artifact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)

            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_record(
                IngestedRecord(
                    record_id="rec-undeclared",
                    revision_hash="hash-undeclared",
                    project_code="G32026TEST203",
                    project_name="undeclared fixture",
                    project_type="",
                    exchange="sse",
                    listing_date="2026-05-01",
                    state="ready",
                    source_file="",
                    archive_path="",
                    parser_payload={"project_code": "G32026TEST203"},
                    postprocess_payload={"project_code": "G32026TEST203"},
                    findings=[],
                    source_identity={
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "project_code": "G32026TEST203",
                        "exchange": "sse",
                    },
                )
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            report = payload["artifact_evidence_report"]
            self.assertEqual(report["mode"], "report_only")
            self.assertEqual(report["undeclared_count"], 1)
            self.assertEqual(report["records"], [
                {
                    "record_id": "rec-undeclared",
                    "classification": "undeclared",
                    "reason_code": "artifact_path_undeclared",
                    "identity_confidence": "verified",
                    "inspection_eligible": False,
                }
            ])
            serialized_report = json.dumps(report, ensure_ascii=False)
            self.assertNotIn("relink", serialized_report)
            self.assertNotIn("redownload", serialized_report)

    def test_data_health_reports_invalid_shell_artifact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            archive_dir = os.path.join(temp_dir, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            evidence_path = os.path.join(archive_dir, "invalid-shell.html")
            with open(evidence_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body><h1>SSE Deal Notice</h1></body></html>")
            MigrationRunner.run(db_path)

            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_record(
                IngestedRecord(
                    record_id="rec-invalid-shell",
                    revision_hash="hash-invalid-shell",
                    project_code="G32026TEST204",
                    project_name="invalid shell fixture",
                    project_type="",
                    exchange="sse",
                    listing_date="2026-05-01",
                    state="ready",
                    source_file=evidence_path,
                    archive_path=evidence_path,
                    parser_payload={"project_code": "G32026TEST204"},
                    postprocess_payload={"project_code": "G32026TEST204"},
                    findings=[],
                    record_family="deal",
                    source_identity={
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "project_code": "G32026TEST204",
                        "exchange": "sse",
                    },
                )
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            report = payload["artifact_evidence_report"]
            self.assertEqual(report["mode"], "report_only")
            self.assertEqual(report["invalid_shell_count"], 1)
            self.assertEqual(report["records"], [
                {
                    "record_id": "rec-invalid-shell",
                    "classification": "invalid_shell",
                    "reason_code": "sse_deal_notice_shell",
                    "identity_confidence": "verified",
                    "inspection_eligible": True,
                }
            ])
            serialized_report = json.dumps(report, ensure_ascii=False)
            self.assertNotIn("SSE Deal Notice", serialized_report)
            self.assertNotIn("relink", serialized_report)
            self.assertNotIn("redownload", serialized_report)

    def test_data_health_reports_identity_mismatch_artifact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            archive_dir = os.path.join(temp_dir, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            evidence_path = os.path.join(archive_dir, "identity-mismatch.html")
            with open(evidence_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>identity mismatch fixture</body></html>")
            MigrationRunner.run(db_path)

            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_record(
                IngestedRecord(
                    record_id="rec-identity-mismatch",
                    revision_hash="hash-identity-mismatch",
                    project_code="G32026TEST205",
                    project_name="identity mismatch fixture",
                    project_type="",
                    exchange="sse",
                    listing_date="2026-05-01",
                    state="ready",
                    source_file=evidence_path,
                    archive_path=evidence_path,
                    parser_payload={"project_code": "G32026TEST999"},
                    postprocess_payload={"project_code": "G32026TEST205"},
                    findings=[],
                    source_identity={
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "project_code": "G32026TEST999",
                        "exchange": "sse",
                    },
                )
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            report = payload["artifact_evidence_report"]
            self.assertEqual(report["mode"], "report_only")
            self.assertEqual(report["identity_mismatch_count"], 1)
            self.assertEqual(report["records"], [
                {
                    "record_id": "rec-identity-mismatch",
                    "classification": "identity_mismatch",
                    "reason_code": "project_code_mismatch",
                    "identity_confidence": "verified",
                    "inspection_eligible": True,
                }
            ])
            serialized_report = json.dumps(report, ensure_ascii=False)
            self.assertNotIn("identity mismatch fixture", serialized_report)
            self.assertNotIn("relink", serialized_report)
            self.assertNotIn("redownload", serialized_report)

    def test_data_health_reports_forbidden_real_workspace_artifact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            forbidden_path = os.path.join(self.REAL_PEAP_HOME, "archive", "forbidden-artifact.html")
            MigrationRunner.run(db_path)
            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_record(
                IngestedRecord(
                    record_id="rec-forbidden-artifact",
                    revision_hash="hash-forbidden-artifact",
                    project_code="G32026FORBID",
                    project_name="forbidden artifact fixture",
                    project_type="",
                    exchange="sse",
                    listing_date="2026-05-01",
                    state="ready",
                    source_file=forbidden_path,
                    archive_path=forbidden_path,
                    parser_payload={"project_code": "G32026FORBID"},
                    postprocess_payload={"project_code": "G32026FORBID"},
                    findings=[],
                    source_identity={
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "project_code": "G32026FORBID",
                        "exchange": "sse",
                    },
                )
            )

            stdout = io.StringIO()
            with patch(
                "peap.operations_admin.resolve_artifact_evidence_verdict",
                side_effect=AssertionError("artifact truth must not inspect forbidden PEAP paths"),
            ), redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(payload["result"], "forbidden_real_workspace")
            self.assertFalse(payload["healthy"])
            self.assertIn(
                {
                    "code": "forbidden_real_workspace",
                    "scope": "artifact_evidence_report",
                    "message": "artifact evidence references a forbidden real PEAP workspace path",
                },
                payload["findings"],
            )
            report = payload["artifact_evidence_report"]
            self.assertEqual(report["forbidden_real_workspace_count"], 1)
            self.assertEqual(report["records"], [
                {
                    "record_id": "rec-forbidden-artifact",
                    "classification": "forbidden_real_workspace",
                    "reason_code": "real_workspace_path_denied",
                    "identity_confidence": "unresolved",
                    "inspection_eligible": False,
                }
            ])
            serialized_report = json.dumps(report, ensure_ascii=False)
            self.assertNotIn(forbidden_path, serialized_report)
            self.assertNotIn("forbidden-artifact.html", serialized_report)

    def test_data_health_and_maintenance_manifest_share_artifact_classifications(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            archive_dir = os.path.join(temp_dir, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            MigrationRunner.run(db_path)
            store = StreamingStore(db_path, auto_migrate=False)

            fixture_rows = [
                (
                    "rec-shared-stale-reference",
                    "stale-reference.html",
                    "missing-stale-reference.html",
                    b"stale reference source bytes",
                    "listing",
                    {
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "project_code": "G32026TEST401",
                        "exchange": "sse",
                    },
                    "G32026TEST401",
                    "ready",
                ),
                (
                    "rec-shared-undeclared",
                    "",
                    "",
                    b"",
                    "listing",
                    {
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "project_code": "G32026TEST402",
                        "exchange": "sse",
                    },
                    "G32026TEST402",
                    "ready",
                ),
                (
                    "rec-shared-invalid-shell",
                    "invalid-shell.html",
                    "invalid-shell.html",
                    b"<html><body><h1>SSE Deal Notice</h1></body></html>",
                    "listing",
                    {
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "project_code": "G32026TEST403",
                        "exchange": "sse",
                    },
                    "G32026TEST403",
                    "ready",
                ),
                (
                    "rec-shared-present-unverified",
                    "present-unverified.html",
                    "present-unverified.html",
                    b"present unverified source bytes",
                    "listing",
                    {"record_family": "listing", "project_code": "G32026TEST404", "exchange": "sse"},
                    "G32026TEST404",
                    "ready",
                ),
                (
                    "rec-shared-identity-mismatch",
                    "identity-mismatch.html",
                    "identity-mismatch.html",
                    b"identity mismatch source bytes",
                    "listing",
                    {
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "project_code": "G32026TEST999",
                        "exchange": "sse",
                    },
                    "G32026TEST405",
                    "ready",
                ),
                (
                    "rec-shared-field-missing-stale",
                    "",
                    "field-missing-stale.html",
                    b"",
                    "listing",
                    {
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "project_code": "G32026TEST406",
                        "exchange": "sse",
                    },
                    "G32026TEST406",
                    "field_missing",
                ),
            ]
            for record_id, source_name, archive_name, payload, family, source_identity, project_code, state in fixture_rows:
                source_file = os.path.join(archive_dir, source_name) if source_name else ""
                archive_path = os.path.join(archive_dir, archive_name) if archive_name else ""
                if source_file:
                    with open(source_file, "wb") as handle:
                        handle.write(payload)
                store.upsert_record(
                    IngestedRecord(
                        record_id=record_id,
                        revision_hash=f"hash-{record_id}",
                        project_code=project_code,
                        project_name="shared classification fixture",
                        project_type="",
                        exchange="sse",
                        listing_date="2026-05-01",
                        state=state,
                        source_file=source_file,
                        archive_path=archive_path,
                        parser_payload={"project_code": project_code},
                        postprocess_payload={"project_code": project_code},
                        findings=[],
                        record_family=family,
                        source_identity=source_identity,
                    )
                )

            maintenance = run_streaming_store_maintenance(store)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            data_health_records = {
                row["record_id"]: row["classification"]
                for row in payload["artifact_evidence_report"]["records"]
            }
            maintenance_records = {
                row["record_id"]: row["maintenance_status"].removeprefix("source_evidence_")
                for row in maintenance.manifest["records"]
                if str(row["maintenance_status"]).startswith("source_evidence_")
            }
            self.assertEqual(maintenance.source_evidence_missing["records"], 6)
            self.assertEqual(maintenance.required_field_missing["records"], 1)
            self.assertEqual(data_health_records, maintenance_records)

    def test_data_health_reports_archive_conflict_paths_as_diagnostic_for_stale_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            archive_dir = os.path.join(temp_dir, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            MigrationRunner.run(db_path)

            conflict_new = os.path.join(archive_dir, "demo__conflict2.html")
            missing_authoritative_path = os.path.join(archive_dir, "demo__missing-authoritative.html")
            conflict_base = os.path.join(archive_dir, "demo.html")
            with open(conflict_new, "w", encoding="utf-8") as handle:
                handle.write("<html><body>new conflict</body></html>")
            with open(conflict_base, "w", encoding="utf-8") as handle:
                handle.write("<html><body>base snapshot</body></html>")

            missing_old_hint = os.path.join(archive_dir, "demo__conflict1.html")
            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_record(
                IngestedRecord(
                    record_id="rec-archive-1",
                    revision_hash="hash-archive-1",
                    project_code="G32026TEST001",
                    project_name="archive conflict sample",
                    project_type="股权转让",
                    exchange="sse",
                    listing_date="2026-05-01",
                    state="conflict",
                    source_file=conflict_new,
                    archive_path=missing_authoritative_path,
                    parser_payload={},
                    postprocess_payload={},
                    findings=[
                        PostProcessFinding(
                            type="archive_conflict",
                            severity="warn",
                            message="conflict",
                            evidence={"archive_path": conflict_new},
                        )
                    ],
                    source_identity={
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "original_source_file": missing_old_hint,
                        "project_code": "G32026TEST001",
                        "exchange": "sse",
                    },
                )
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            archive_classification = payload["archive_conflict_classification"]
            self.assertEqual(archive_classification["mode"], "report_only")
            self.assertEqual(archive_classification["total_archive_conflict_findings"], 1)
            self.assertEqual(archive_classification["counts"]["stale_reference"], 1)
            self.assertNotIn("resolvable_accept_newer", archive_classification["counts"])
            self.assertNotIn("source_missing_or_unpaired", archive_classification["counts"])
            self.assertEqual(len(archive_classification["records"]), 1)
            row = archive_classification["records"][0]
            self.assertEqual(row["record_id"], "rec-archive-1")
            self.assertEqual(row["classification"], "stale_reference")
            self.assertEqual(row["reason_code"], "authoritative_artifact_missing")
            self.assertEqual(row["identity_confidence"], "verified")
            self.assertNotIn("old_hint_exists", row)
            self.assertNotIn("new_exists", row)
            self.assertNotIn("base_exists", row)
            diagnostic = row["path_diagnostic"]
            self.assertFalse(diagnostic["old_hint_exists"])
            self.assertTrue(diagnostic["new_exists"])
            self.assertTrue(diagnostic["base_exists"])

    def test_data_health_archive_conflict_taxonomy_uses_artifact_evidence_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            archive_dir = os.path.join(temp_dir, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            MigrationRunner.run(db_path)

            # present_unverified even though old hint, new path, and base path all exist.
            r1_old = os.path.join(archive_dir, "alpha__conflict1.html")
            r1_new = os.path.join(archive_dir, "alpha__conflict2.html")
            r1_base = os.path.join(archive_dir, "alpha.html")
            for path, text in (
                (r1_old, "old"),
                (r1_new, "new"),
                (r1_base, "base"),
            ):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)

            # invalid_shell even though old hint and new path exist.
            r2_old = os.path.join(archive_dir, "beta__conflict1.html")
            r2_new = os.path.join(archive_dir, "beta__conflict2.html")
            with open(r2_old, "w", encoding="utf-8") as handle:
                handle.write("old")
            with open(r2_new, "w", encoding="utf-8") as handle:
                handle.write("<html><body><h1>SSE Deal Notice</h1></body></html>")

            # stale_reference even though old hint and base path exist.
            r3_old = os.path.join(archive_dir, "gamma__conflict1.html")
            r3_new = os.path.join(archive_dir, "gamma__conflict2.html")
            r3_base = os.path.join(archive_dir, "gamma.html")
            for path, text in (
                (r3_old, "old"),
                (r3_base, "base"),
            ):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)

            store = StreamingStore(db_path, auto_migrate=False)
            for record_id, source_file, _old_hint, project_code, source_identity in (
                (
                    "rec-archive-present-unverified",
                    r1_new,
                    r1_old,
                    "G32026TEST101",
                    {
                        "record_family": "listing",
                        "original_source_file": r1_old,
                        "project_code": "G32026TEST101",
                        "exchange": "sse",
                    },
                ),
                (
                    "rec-archive-invalid-shell",
                    r2_new,
                    r2_old,
                    "G32026TEST102",
                    {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "original_source_file": r2_old,
                        "project_code": "G32026TEST102",
                        "exchange": "sse",
                    },
                ),
                (
                    "rec-archive-stale-reference",
                    r3_new,
                    r3_old,
                    "G32026TEST103",
                    {
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "original_source_file": r3_old,
                        "project_code": "G32026TEST103",
                        "exchange": "sse",
                    },
                ),
            ):
                store.upsert_record(
                    IngestedRecord(
                        record_id=record_id,
                        revision_hash=f"hash-{record_id}",
                        project_code=project_code,
                        project_name="archive conflict sample",
                        project_type="",
                        exchange="sse",
                        listing_date="2026-05-01",
                        state="conflict",
                        source_file=source_file,
                        archive_path=source_file,
                        parser_payload={},
                        postprocess_payload={},
                        findings=[
                            PostProcessFinding(
                                type="archive_conflict",
                                severity="warn",
                                message="conflict",
                                evidence={},
                            )
                        ],
                        record_family=str(source_identity["record_family"]),
                        source_identity=source_identity,
                    )
                )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            archive_classification = payload["archive_conflict_classification"]
            self.assertEqual(archive_classification["mode"], "report_only")
            self.assertEqual(archive_classification["total_archive_conflict_findings"], 3)
            self.assertEqual(archive_classification["counts"]["present_unverified"], 1)
            self.assertEqual(archive_classification["counts"]["invalid_shell"], 1)
            self.assertEqual(archive_classification["counts"]["stale_reference"], 1)
            self.assertNotIn("resolvable_accept_newer", archive_classification["counts"])
            self.assertNotIn("ambiguous_requires_user", archive_classification["counts"])
            self.assertNotIn("source_missing_or_unpaired", archive_classification["counts"])

            by_record_id = {row["record_id"]: row for row in archive_classification["records"]}
            present_unverified = by_record_id["rec-archive-present-unverified"]
            self.assertEqual(present_unverified["classification"], "present_unverified")
            self.assertTrue(present_unverified["path_diagnostic"]["old_hint_exists"])
            self.assertTrue(present_unverified["path_diagnostic"]["new_exists"])
            self.assertTrue(present_unverified["path_diagnostic"]["base_exists"])

            invalid_shell = by_record_id["rec-archive-invalid-shell"]
            self.assertEqual(invalid_shell["classification"], "invalid_shell")
            self.assertTrue(invalid_shell["path_diagnostic"]["old_hint_exists"])
            self.assertTrue(invalid_shell["path_diagnostic"]["new_exists"])
            self.assertFalse(invalid_shell["path_diagnostic"]["base_exists"])

            stale_reference = by_record_id["rec-archive-stale-reference"]
            self.assertEqual(stale_reference["classification"], "stale_reference")
            self.assertTrue(stale_reference["path_diagnostic"]["old_hint_exists"])
            self.assertFalse(stale_reference["path_diagnostic"]["new_exists"])
            self.assertTrue(stale_reference["path_diagnostic"]["base_exists"])

    def test_archive_conflict_classification_denies_real_peap_paths_before_artifact_truth(self) -> None:
        real_peap_home = "/Users/rtoc/Documents/PEAP"
        forbidden_archive_path = os.path.join(real_peap_home, "archive", "conflict.html")
        forbidden_source_path = os.path.join(real_peap_home, "archive", "source.html")

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """
                CREATE TABLE records (
                    record_id TEXT,
                    latest_revision_id TEXT,
                    record_family TEXT,
                    business_id TEXT,
                    state TEXT,
                    project_code TEXT,
                    exchange TEXT,
                    source_file TEXT,
                    archive_path TEXT,
                    source_identity_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE record_revisions (
                    revision_id TEXT,
                    findings_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO records (
                    record_id, latest_revision_id, record_family, business_id, state,
                    project_code, exchange, source_file, archive_path, source_identity_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rec-forbidden-archive",
                    "rev-forbidden-archive",
                    "listing",
                    "asset_listing",
                    "conflict",
                    "G32026REAL001",
                    "sse",
                    forbidden_source_path,
                    forbidden_archive_path,
                    json.dumps(
                        {
                            "record_family": "listing",
                            "business_id": "asset_listing",
                            "project_code": "G32026REAL001",
                            "exchange": "sse",
                            "original_source_file": forbidden_source_path,
                        }
                    ),
                ),
            )
            conn.execute(
                "INSERT INTO record_revisions (revision_id, findings_json) VALUES (?, ?)",
                (
                    "rev-forbidden-archive",
                    json.dumps(
                        [
                            {
                                "type": "archive_conflict",
                                "evidence": {"archive_path": forbidden_archive_path},
                            }
                        ]
                    ),
                ),
            )

            def forbid_exists(path: object) -> bool:
                if str(path).startswith(real_peap_home):
                    raise AssertionError(f"attempted filesystem probe for forbidden PEAP path: {path}")
                return False

            with patch(
                "peap.operations_admin.resolve_artifact_evidence_verdict",
                side_effect=AssertionError("artifact truth must not inspect forbidden PEAP paths"),
            ) as verdict, patch("peap.operations_admin.os.path.exists", side_effect=forbid_exists):
                payload = operations_admin._build_archive_conflict_classification(conn)

            verdict.assert_not_called()
            self.assertEqual(payload["total_archive_conflict_findings"], 1)
            self.assertEqual(payload["counts"]["forbidden_real_workspace"], 1)
            self.assertEqual(payload["records"][0]["record_id"], "rec-forbidden-archive")
            self.assertEqual(payload["records"][0]["classification"], "forbidden_real_workspace")
            self.assertEqual(payload["records"][0]["reason_code"], "real_workspace_path_denied")
        finally:
            conn.close()

    def test_archive_conflict_classification_surfaces_invalid_source_identity_json(self) -> None:
        real_peap_home = "/Users/rtoc/Documents/PEAP"
        forbidden_archive_path = os.path.join(real_peap_home, "archive", "bad-json-conflict.html")
        forbidden_source_path = os.path.join(real_peap_home, "archive", "bad-json-source.html")

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """
                CREATE TABLE records (
                    record_id TEXT,
                    latest_revision_id TEXT,
                    record_family TEXT,
                    business_id TEXT,
                    state TEXT,
                    project_code TEXT,
                    exchange TEXT,
                    source_file TEXT,
                    archive_path TEXT,
                    source_identity_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE record_revisions (
                    revision_id TEXT,
                    findings_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO records (
                    record_id, latest_revision_id, record_family, business_id, state,
                    project_code, exchange, source_file, archive_path, source_identity_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rec-invalid-source-identity",
                    "rev-invalid-source-identity",
                    "listing",
                    "asset_listing",
                    "conflict",
                    "G32026BADJSON",
                    "sse",
                    forbidden_source_path,
                    forbidden_archive_path,
                    "{",
                ),
            )
            conn.execute(
                "INSERT INTO record_revisions (revision_id, findings_json) VALUES (?, ?)",
                (
                    "rev-invalid-source-identity",
                    json.dumps(
                        [
                            {
                                "type": "archive_conflict",
                                "evidence": {"archive_path": forbidden_archive_path},
                            }
                        ]
                    ),
                ),
            )

            with self.assertRaises(json.JSONDecodeError):
                operations_admin._build_archive_conflict_classification(conn)
        finally:
            conn.close()

    def test_archive_conflict_classification_surfaces_invalid_findings_json(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """
                CREATE TABLE records (
                    record_id TEXT,
                    latest_revision_id TEXT,
                    record_family TEXT,
                    business_id TEXT,
                    state TEXT,
                    project_code TEXT,
                    exchange TEXT,
                    source_file TEXT,
                    archive_path TEXT,
                    source_identity_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE record_revisions (
                    revision_id TEXT,
                    findings_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO records (
                    record_id, latest_revision_id, record_family, business_id, state,
                    project_code, exchange, source_file, archive_path, source_identity_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rec-invalid-findings",
                    "rev-invalid-findings",
                    "listing",
                    "asset_listing",
                    "conflict",
                    "G32026BADFIND",
                    "sse",
                    "/tmp/old-source.html",
                    "/tmp/new-conflict.html",
                    json.dumps({"record_family": "listing", "business_id": "asset_listing"}),
                ),
            )
            conn.execute(
                "INSERT INTO record_revisions (revision_id, findings_json) VALUES (?, ?)",
                ("rev-invalid-findings", "{"),
            )

            with self.assertRaises(json.JSONDecodeError):
                operations_admin._build_archive_conflict_classification(conn)
        finally:
            conn.close()

    def test_json_object_rejects_non_empty_json_non_objects(self) -> None:
        self.assertEqual(operations_admin._json_object(None), {})
        self.assertEqual(operations_admin._json_object(""), {})

        for raw in ("[]", "true", '"x"'):
            with self.assertRaisesRegex(ValueError, "expected JSON object"):
                operations_admin._json_object(raw)

    def test_json_list_rejects_non_empty_json_non_lists(self) -> None:
        self.assertEqual(operations_admin._json_list(None), [])
        self.assertEqual(operations_admin._json_list(""), [])

        for raw in ("{}", "true", '"x"'):
            with self.assertRaisesRegex(ValueError, "expected JSON list"):
                operations_admin._json_list(raw)

    def test_json_loads_rejects_non_empty_json_with_default_shape_mismatch(self) -> None:
        self.assertEqual(operations_admin._json_loads(None, default={}), {})
        self.assertEqual(operations_admin._json_loads("", default={}), {})
        self.assertEqual(operations_admin._json_loads(None, default=[]), [])
        self.assertEqual(operations_admin._json_loads("", default=[]), [])

        for raw in ("[]", "true", '"x"'):
            with self.assertRaisesRegex(ValueError, "expected JSON object"):
                operations_admin._json_loads(raw, default={})

        for raw in ("{}", "true", '"x"'):
            with self.assertRaisesRegex(ValueError, "expected JSON list"):
                operations_admin._json_loads(raw, default=[])

    def test_json_loads_surfaces_non_empty_invalid_json_for_shaped_defaults(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            operations_admin._json_loads("{", default={})

        with self.assertRaises(json.JSONDecodeError):
            operations_admin._json_loads("{", default=[])

        self.assertIsNone(operations_admin._json_loads("{", default=None))
        self.assertEqual(operations_admin._json_loads("{", default="fallback"), "fallback")

    def test_data_health_reports_corrupt_source_identity_json_as_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            archive_dir = os.path.join(temp_dir, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            evidence_path = os.path.join(archive_dir, "corrupt-source-identity.html")
            with open(evidence_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>corrupt source identity fixture</body></html>")
            MigrationRunner.run(db_path)
            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_record(
                IngestedRecord(
                    record_id="rec-corrupt-source-identity",
                    revision_hash="hash-corrupt-source-identity",
                    project_code="G32026BADJSON",
                    project_name="corrupt source identity fixture",
                    project_type="",
                    exchange="sse",
                    listing_date="2026-05-01",
                    state="ready",
                    source_file=evidence_path,
                    archive_path=evidence_path,
                    parser_payload={"project_code": "G32026BADJSON"},
                    postprocess_payload={"project_code": "G32026BADJSON"},
                    findings=[],
                    source_identity={
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "project_code": "G32026BADJSON",
                        "exchange": "sse",
                    },
                )
            )
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "UPDATE records SET source_identity_json = ? WHERE record_id = ?",
                    ("{", "rec-corrupt-source-identity"),
                )
                conn.commit()

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(payload["result"], "corrupt_json")
            self.assertFalse(payload["healthy"])
            self.assertEqual(payload["findings"][-1]["code"], "corrupt_json")
            self.assertEqual(payload["findings"][-1]["scope"], "artifact_evidence_report")

    def test_data_health_reports_corrupt_findings_json_as_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, _isolated_admin_env(temp_dir):
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            archive_dir = os.path.join(temp_dir, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            conflict_path = os.path.join(archive_dir, "corrupt-findings-conflict.html")
            with open(conflict_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>corrupt findings fixture</body></html>")
            MigrationRunner.run(db_path)
            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_record(
                IngestedRecord(
                    record_id="rec-corrupt-findings",
                    revision_hash="hash-corrupt-findings",
                    project_code="G32026BADFIND",
                    project_name="corrupt findings fixture",
                    project_type="",
                    exchange="sse",
                    listing_date="2026-05-01",
                    state="conflict",
                    source_file=conflict_path,
                    archive_path=conflict_path,
                    parser_payload={"project_code": "G32026BADFIND"},
                    postprocess_payload={"project_code": "G32026BADFIND"},
                    findings=[
                        PostProcessFinding(
                            type="archive_conflict",
                            severity="warn",
                            message="conflict",
                            evidence={"archive_path": conflict_path},
                        )
                    ],
                    source_identity={
                        "record_family": "listing",
                        "business_id": "asset_listing",
                        "project_code": "G32026BADFIND",
                        "exchange": "sse",
                    },
                )
            )
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    UPDATE record_revisions
                    SET findings_json = ?
                    WHERE record_id = ?
                    """,
                    ("{", "rec-corrupt-findings"),
                )
                conn.commit()

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["data-health", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(payload["result"], "corrupt_json")
            self.assertFalse(payload["healthy"])
            self.assertEqual(payload["findings"][-1]["code"], "corrupt_json")
            self.assertEqual(payload["findings"][-1]["scope"], "archive_conflict_classification")

    def test_archive_conflict_classification_surfaces_non_object_findings(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """
                CREATE TABLE records (
                    record_id TEXT,
                    latest_revision_id TEXT,
                    record_family TEXT,
                    business_id TEXT,
                    state TEXT,
                    project_code TEXT,
                    exchange TEXT,
                    source_file TEXT,
                    archive_path TEXT,
                    source_identity_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE record_revisions (
                    revision_id TEXT,
                    findings_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO records (
                    record_id, latest_revision_id, record_family, business_id, state,
                    project_code, exchange, source_file, archive_path, source_identity_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rec-non-object-finding",
                    "rev-non-object-finding",
                    "listing",
                    "asset_listing",
                    "conflict",
                    "G32026BADFINDING",
                    "sse",
                    "/tmp/old-source.html",
                    "/tmp/new-conflict.html",
                    json.dumps({"record_family": "listing", "business_id": "asset_listing"}),
                ),
            )
            conn.execute(
                "INSERT INTO record_revisions (revision_id, findings_json) VALUES (?, ?)",
                ("rev-non-object-finding", json.dumps(["not-an-object"])),
            )

            with self.assertRaisesRegex(ValueError, "finding at index 0 must be an object"):
                operations_admin._build_archive_conflict_classification(conn)
        finally:
            conn.close()

    def test_archive_conflict_classification_rejects_non_object_evidence(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                """
                CREATE TABLE records (
                    record_id TEXT,
                    latest_revision_id TEXT,
                    record_family TEXT,
                    business_id TEXT,
                    state TEXT,
                    project_code TEXT,
                    exchange TEXT,
                    source_file TEXT,
                    archive_path TEXT,
                    source_identity_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE record_revisions (
                    revision_id TEXT,
                    findings_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO records (
                    record_id, latest_revision_id, record_family, business_id, state,
                    project_code, exchange, source_file, archive_path, source_identity_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rec-non-object-evidence",
                    "rev-non-object-evidence",
                    "listing",
                    "asset_listing",
                    "conflict",
                    "G32026BADEVIDENCE",
                    "sse",
                    "/tmp/old-source.html",
                    "/tmp/new-conflict.html",
                    json.dumps({"record_family": "listing", "business_id": "asset_listing"}),
                ),
            )
            conn.execute(
                "INSERT INTO record_revisions (revision_id, findings_json) VALUES (?, ?)",
                (
                    "rev-non-object-evidence",
                    json.dumps([{"type": "archive_conflict", "evidence": "not-an-object"}]),
                ),
            )

            with self.assertRaisesRegex(ValueError, "archive_conflict evidence must be an object"):
                operations_admin._build_archive_conflict_classification(conn)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
