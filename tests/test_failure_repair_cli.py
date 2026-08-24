from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from peap.cli import main as peap_main
from peap.failure_repair import (
    FailureRepairRuntime,
    _classify_record,
    apply_failure_repair_plan,
    build_failure_repair_plan,
)
from peap.migrations import MigrationRunner
from peap.streaming_models import IngestedRecord, PostProcessFinding
from peap.streaming_store import StreamingStore


class FailureRepairCliTest(unittest.TestCase):
    def test_repair_plan_rejects_string_findings_shape(self) -> None:
        with self.assertRaisesRegex(ValueError, "findings must be a list"):
            _classify_record(
                {
                    "record_id": "rec-bad-findings-shape",
                    "state": "pending_review",
                    "findings": "business_resolution_required",
                }
            )

    def test_repair_plan_rejects_invalid_superseding_record_shape(self) -> None:
        record = {
            "record_id": "rec-failed",
            "state": "parse_failed",
            "source_file": "/tmp/missing.html",
            "archive_path": "/tmp/missing.html",
        }
        for superseding_record in ({}, {"state": "ready"}, "rec-ready"):
            with self.subTest(superseding_record=superseding_record):
                with self.assertRaisesRegex(ValueError, "superseding_record"):
                    _classify_record(record, superseding_record)  # type: ignore[arg-type]

    def test_repair_failures_dry_run_classifies_repairable_and_blocked_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)
            evidence = os.path.join(temp_dir, "archive", "tpre.html")
            os.makedirs(os.path.dirname(evidence), exist_ok=True)
            with open(evidence, "w", encoding="utf-8") as handle:
                handle.write("<html><head><title>天津交易集团</title></head><body>ok</body></html>")
            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_failed_record(
                project_code="G32026TJ1000008",
                source_file=evidence,
                state="parse_failed",
                error_type="exchange-detect-failed",
                error_message=f"exchange-detect-failed: {evidence}",
                payload={"project_code": "G32026TJ1000008"},
            )
            store.upsert_failed_record(
                project_code="G32025TJ1000102",
                source_file=os.path.join(temp_dir, "archive", "synthetic.html"),
                state="parse_failed",
                error_type="synthetic_archive_quarantined",
                error_message="历史成交归档为人造 synthetic/list-row fallback snapshot",
                payload={"project_code": "G32025TJ1000102"},
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["repair-failures", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertFalse(payload["applied"])
            by_code = {item["project_code"]: item for item in payload["items"]}
            self.assertEqual(by_code["G32026TJ1000008"]["action"], "record_reprocess_from_evidence")
            self.assertTrue(by_code["G32026TJ1000008"]["apply_supported"])
            self.assertEqual(by_code["G32025TJ1000102"]["action"], "source_refetch_required")
            self.assertFalse(by_code["G32025TJ1000102"]["apply_supported"])

    def test_repair_failures_cli_rejects_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)
            store = StreamingStore(db_path, auto_migrate=False)
            for index in range(2):
                evidence = os.path.join(temp_dir, "archive", f"tpre-{index}.html")
                os.makedirs(os.path.dirname(evidence), exist_ok=True)
                with open(evidence, "w", encoding="utf-8") as handle:
                    handle.write("<html><head><title>天津交易集团</title></head><body>ok</body></html>")
                store.upsert_failed_record(
                    project_code=f"G32026TJ100000{index}",
                    source_file=evidence,
                    state="parse_failed",
                    error_type="exchange-detect-failed",
                    error_message=f"exchange-detect-failed: {evidence}",
                    payload={"project_code": f"G32026TJ100000{index}"},
                )

            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                peap_main(["repair-failures", "--app-home", temp_dir, "--apply"])

            self.assertEqual(raised.exception.code, 2)
            self.assertIn("unrecognized arguments: --apply", stderr.getvalue())

    def test_repair_failures_classifies_pending_review_and_field_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)
            source = os.path.join(temp_dir, "archive", "review.html")
            os.makedirs(os.path.dirname(source), exist_ok=True)
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_record(
                IngestedRecord(
                    record_id="rec-review",
                    revision_hash="hash-review",
                    project_code="G32026TJ1000008",
                    project_name="业务缺失",
                    project_type="",
                    exchange="天交所",
                    listing_date="2026-05-18",
                    state="pending_review",
                    source_file=source,
                    archive_path=source,
                    parser_payload={},
                    postprocess_payload={},
                    findings=[
                        PostProcessFinding(
                            severity="warn",
                            type="business_resolution_required",
                            message="缺少业务类型",
                            evidence={"blocker_kind": "business_resolution"},
                        )
                    ],
                )
            )
            store.upsert_record(
                IngestedRecord(
                    record_id="rec-field",
                    revision_hash="hash-field",
                    project_code="ZCZR",
                    project_name="字段缺失",
                    project_type="实物资产",
                    exchange="北交所",
                    listing_date="2026-05-18",
                    state="field_missing",
                    source_file=source,
                    archive_path=source,
                    parser_payload={},
                    postprocess_payload={},
                    findings=[
                        PostProcessFinding(
                            severity="warn",
                            type="canonical_field_missing",
                            message="缺少成交金额",
                            evidence={"missing_fields": ["deal_price"]},
                        )
                    ],
                )
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(["repair-failures", "--app-home", temp_dir])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            by_id = {item["record_id"]: item for item in payload["items"]}
            self.assertEqual(by_id["rec-review"]["action"], "business_re_evaluation_required")
            self.assertEqual(by_id["rec-review"]["reason_code"], "business_resolution_required")
            self.assertFalse(by_id["rec-review"]["apply_supported"])
            self.assertEqual(by_id["rec-field"]["action"], "source_data_required")
            self.assertFalse(by_id["rec-field"]["apply_supported"])

    def test_repair_plan_projects_business_id_from_canonical_identity_when_record_column_blank(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)
            source = os.path.join(temp_dir, "archive", "deal.html")
            os.makedirs(os.path.dirname(source), exist_ok=True)
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("<html><body>fixture</body></html>")
            store = StreamingStore(db_path, auto_migrate=False)
            record_id = "rec-canonical-business-id"
            canonical_record = {
                "business_identity": {
                    "business_id": "deal_equity_transfer",
                    "record_family": "deal",
                },
                "source_identity": {
                    "business_id": "deal_equity_transfer",
                    "record_family": "deal",
                },
                "canonical_fields": {
                    "project_code": "G32026TJ1000008",
                    "project_name": "成交业务身份项目",
                },
            }
            store.upsert_record(
                IngestedRecord(
                    record_id=record_id,
                    revision_hash="hash-canonical-business-id",
                    project_code="G32026TJ1000008",
                    project_name="成交业务身份项目",
                    project_type="股权转让",
                    exchange="天交所",
                    listing_date="2026-05-18",
                    state="pending_mapping",
                    source_file=source,
                    archive_path=source,
                    parser_payload={},
                    postprocess_payload={},
                    findings=[],
                    record_family="listing",
                    canonical_record=canonical_record,
                )
            )
            with store._connect() as conn:
                conn.execute(
                    "UPDATE records SET business_id = '' WHERE record_id = ?",
                    (record_id,),
                )

            plan = build_failure_repair_plan(app_home=temp_dir, record_ids=[record_id])

            self.assertEqual(plan["items"][0]["business_id"], "deal_equity_transfer")

    def test_repair_plan_rejects_invalid_record_ids_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)

            for record_ids in ("rec-1", ["rec-1", {"record_id": "rec-2"}]):
                with self.subTest(record_ids=record_ids):
                    with self.assertRaisesRegex(ValueError, "record_ids"):
                        build_failure_repair_plan(app_home=temp_dir, record_ids=record_ids)  # type: ignore[arg-type]

    def test_repair_failures_does_not_retry_failed_shell_superseded_by_canonical_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)
            evidence = os.path.join(temp_dir, "archive", "tpre.html")
            os.makedirs(os.path.dirname(evidence), exist_ok=True)
            with open(evidence, "w", encoding="utf-8") as handle:
                handle.write("<html><head><title>天津交易集团</title></head><body>ok</body></html>")
            store = StreamingStore(db_path, auto_migrate=False)
            failed = store.upsert_failed_record(
                project_code="G32026TJ1000008",
                source_file=evidence,
                state="parse_failed",
                error_type="exchange-detect-failed",
                error_message=f"exchange-detect-failed: {evidence}",
                payload={"project_code": "G32026TJ1000008"},
            )
            store.upsert_record(
                IngestedRecord(
                    record_id="rec-ready",
                    revision_hash="hash-ready",
                    project_code="G32026TJ1000008",
                    project_name="天津市城科智能热力有限公司100%股权",
                    project_type="股权转让",
                    exchange="天交所",
                    listing_date="2026-05-18",
                    state="ready",
                    source_file=evidence,
                    archive_path=evidence,
                    parser_payload={},
                    postprocess_payload={},
                    findings=[],
                    source_identity={
                        "record_family": "listing",
                        "project_code": "G32026TJ1000008",
                        "original_source_file": evidence,
                        "source_id": "tpre",
                    },
                )
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = peap_main(
                    [
                        "repair-failures",
                        "--app-home",
                        temp_dir,
                        "--record-id",
                        failed["record_id"],
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["summary"]["repairable_count"], 0)
            self.assertEqual(payload["items"][0]["action"], "superseded_by_record")
            self.assertEqual(payload["items"][0]["superseded_by_record_id"], "rec-ready")

    def test_apply_repair_failures_runs_supported_action_inside_operation_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)
            evidence = os.path.join(temp_dir, "archive", "tpre.html")
            os.makedirs(os.path.dirname(evidence), exist_ok=True)
            with open(evidence, "w", encoding="utf-8") as handle:
                handle.write("<html><head><title>天津交易集团</title></head><body>ok</body></html>")
            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_failed_record(
                project_code="G32026TJ1000008",
                source_file=evidence,
                state="parse_failed",
                error_type="exchange-detect-failed",
                error_message=f"exchange-detect-failed: {evidence}",
                payload={"project_code": "G32026TJ1000008"},
            )

            runtime = FailureRepairRuntime(
                store=store,
                reprocess_record=lambda record_id: {"record_id": record_id, "state": "ready"},
            )
            exit_code, payload = apply_failure_repair_plan(
                app_home=temp_dir,
                allow_batch=True,
                runtime=runtime,
            )

            journals = StreamingStore(db_path, auto_migrate=False).list_operation_journals(limit=5)
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["applied"])
            self.assertEqual(payload["results"][0]["status"], "succeeded")
            self.assertEqual(journals[0]["operation_type"], "failed_record_repair")
            self.assertEqual(journals[0]["status"], "succeeded")

    def test_apply_repair_failures_noops_without_repairable_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)
            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_failed_record(
                project_code="G32025TJ1000102",
                source_file=os.path.join(temp_dir, "archive", "synthetic.html"),
                state="parse_failed",
                error_type="synthetic_archive_quarantined",
                error_message="历史成交归档为人造 synthetic/list-row fallback snapshot",
                payload={"project_code": "G32025TJ1000102"},
            )

            exit_code, payload = apply_failure_repair_plan(
                app_home=temp_dir,
                allow_batch=True,
                runtime=FailureRepairRuntime(store=store, reprocess_record=lambda record_id: {"record_id": record_id}),
            )

            journals = StreamingStore(db_path, auto_migrate=False).list_operation_journals(limit=5)
            self.assertEqual(exit_code, 2)
            self.assertFalse(payload["applied"])
            self.assertEqual(payload["error"]["code"], "no_repairable_records")
            self.assertEqual(journals, [])

    def test_apply_repair_failures_treats_returned_error_payload_as_failed_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)
            evidence = os.path.join(temp_dir, "archive", "tpre.html")
            os.makedirs(os.path.dirname(evidence), exist_ok=True)
            with open(evidence, "w", encoding="utf-8") as handle:
                handle.write("<html><head><title>天津交易集团</title></head><body>ok</body></html>")
            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_failed_record(
                project_code="G32026TJ1000008",
                source_file=evidence,
                state="parse_failed",
                error_type="exchange-detect-failed",
                error_message=f"exchange-detect-failed: {evidence}",
                payload={"project_code": "G32026TJ1000008"},
            )

            runtime = FailureRepairRuntime(
                store=store,
                reprocess_record=lambda record_id: {
                    "record_id": record_id,
                    "state": "parse_failed",
                    "error_code": "source_missing",
                },
            )
            exit_code, payload = apply_failure_repair_plan(
                app_home=temp_dir,
                allow_batch=True,
                runtime=runtime,
            )

            journals = StreamingStore(db_path, auto_migrate=False).list_operation_journals(limit=5)
            self.assertEqual(exit_code, 4)
            self.assertTrue(payload["applied"])
            self.assertEqual(payload["error"]["code"], "partial_failure")
            self.assertEqual(payload["results"][0]["status"], "failed")
            self.assertEqual(journals[0]["operation_type"], "failed_record_repair")
            self.assertEqual(journals[0]["status"], "failed")

    def test_apply_repair_failures_treats_missing_state_payload_as_failed_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "data", "streaming_ingest.sqlite3")
            MigrationRunner.run(db_path)
            evidence = os.path.join(temp_dir, "archive", "tpre.html")
            os.makedirs(os.path.dirname(evidence), exist_ok=True)
            with open(evidence, "w", encoding="utf-8") as handle:
                handle.write("<html><head><title>天津交易集团</title></head><body>ok</body></html>")
            store = StreamingStore(db_path, auto_migrate=False)
            store.upsert_failed_record(
                project_code="G32026TJ1000008",
                source_file=evidence,
                state="parse_failed",
                error_type="exchange-detect-failed",
                error_message=f"exchange-detect-failed: {evidence}",
                payload={"project_code": "G32026TJ1000008"},
            )

            runtime = FailureRepairRuntime(store=store, reprocess_record=lambda record_id: {"record_id": record_id})
            exit_code, payload = apply_failure_repair_plan(
                app_home=temp_dir,
                allow_batch=True,
                runtime=runtime,
            )

            journals = StreamingStore(db_path, auto_migrate=False).list_operation_journals(limit=5)
            self.assertEqual(exit_code, 4)
            self.assertTrue(payload["applied"])
            self.assertEqual(payload["error"]["code"], "partial_failure")
            self.assertEqual(payload["results"][0]["status"], "failed")
            self.assertEqual(journals[0]["operation_type"], "failed_record_repair")
            self.assertEqual(journals[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
