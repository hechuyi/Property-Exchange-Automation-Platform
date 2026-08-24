from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from peap.business_reclassification import (
    BusinessReclassificationRuntime,
    _build_plan_entries,
    apply_business_reclassification_plan,
    build_business_reclassification_plan,
)
from peap.streaming_models import IngestedRecord
from peap.streaming_store import (
    StreamingStore,
    _business_reclassification_proof_fingerprint,
)
from scripts import repair_business_classifications as repair_script


class BusinessReclassificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "streaming.sqlite3")
        self.store = StreamingStore(self.db_path, auto_migrate=True)
        self.source_url = "https://example.test/equityDetail/1001"
        self.project_code = "G32026GD0001001-1"
        self.source_path = os.path.join(self.temp_dir.name, "archive", "source.html")
        os.makedirs(os.path.dirname(self.source_path), exist_ok=True)
        with open(self.source_path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>official equity detail</body></html>")

    def _payload(self, *, project_type: str) -> dict[str, str]:
        return {
            "项目编号": self.project_code,
            "项目名称": "测试企业 51% 股权转让",
            "项目类型": project_type,
            "项目状态": "挂牌中",
            "挂牌开始日期": "2026-08-01",
            "挂牌价格": "100.00",
            "转让方": "测试转让方",
            "类型": "央企",
            "交易所": "guangdong",
            "source_id": "guangdong",
            "source_url": self.source_url,
        }

    def _record(
        self,
        *,
        record_id: str,
        business_id: str,
        project_type: str,
        state: str = "ready",
        source_path: str | None = None,
    ) -> IngestedRecord:
        resolved_source_path = self.source_path if source_path is None else source_path
        payload = self._payload(project_type=project_type)
        source_identity = {
            "record_family": "listing",
            "business_id": business_id,
            "business_id_hint": business_id,
            "business_label_hint": project_type,
            "project_type_fallback": project_type,
            "project_code": self.project_code,
            "project_name": payload["项目名称"],
            "source_id": "guangdong",
            "exchange": "guangdong",
            "source_url": self.source_url,
            "original_evidence_path": resolved_source_path,
            "candidate_tokens": [
                f"project_code:{self.project_code}",
                f"page_url:{self.source_url}",
            ],
        }
        canonical_fields = {
            "project_code": self.project_code,
            "project_name": payload["项目名称"],
            "project_type": project_type,
            "status": "挂牌中",
            "exchange": "guangdong",
            "start_date": "2026-08-01",
            "price": "100.00",
            "seller": "测试转让方",
            "source_type": "央企",
        }
        return IngestedRecord(
            record_id=record_id,
            revision_hash=hashlib.sha256(f"{record_id}:{state}".encode()).hexdigest(),
            project_code=self.project_code,
            project_name=payload["项目名称"],
            project_type=project_type,
            exchange="guangdong",
            listing_date="2026-08-01",
            state=state,
            source_file=resolved_source_path,
            archive_path=resolved_source_path,
            parser_payload=payload,
            postprocess_payload=payload,
            source_identity=source_identity,
            canonical_record={
                "record_family": "listing",
                "business_identity": {
                    "record_family": "listing",
                    "business_id": business_id,
                    "project_type_label": project_type,
                },
                "source_identity": source_identity,
                "canonical_fields": canonical_fields,
            },
            canonical_projection={
                "项目编号": self.project_code,
                "项目名称": payload["项目名称"],
                "项目类型": project_type,
            },
        )

    def _insert_original(
        self,
        *,
        source_path: str | None = None,
        state: str = "ready",
    ) -> dict[str, object]:
        return self.store.upsert_record_with_mapping_pending(
            self._record(
                record_id="record-wrong-capital",
                business_id="capital_increase",
                project_type="增资扩股",
                source_path=source_path,
                state=state,
            )
        )

    def _insert_target(
        self,
        *,
        state: str = "ready",
        source_path: str | None = None,
    ) -> dict[str, object]:
        return self.store.upsert_record_with_mapping_pending(
            self._record(
                record_id="record-correct-equity",
                business_id="equity_transfer",
                project_type="股权转让",
                state=state,
                source_path=source_path,
            )
        )

    def _runtime(self, *, parser=None) -> BusinessReclassificationRuntime:
        return BusinessReclassificationRuntime.for_store(
            self.store,
            parser=parser or (lambda _path: self._payload(project_type="股权转让")),
        )

    def test_report_only_fresh_parse_does_not_mutate_store_or_artifact(self) -> None:
        self._insert_original()
        self._insert_target()
        before_records = self.store.iter_latest_records(sort="recent")
        before_source = open(self.source_path, "rb").read()
        with sqlite3.connect(self.db_path) as conn:
            before_counts = conn.execute(
                "SELECT (SELECT COUNT(*) FROM audit_log), "
                "(SELECT COUNT(*) FROM operation_journal), "
                "(SELECT COUNT(*) FROM record_revisions)"
            ).fetchone()

        plan = build_business_reclassification_plan(runtime=self._runtime())

        with sqlite3.connect(self.db_path) as conn:
            after_counts = conn.execute(
                "SELECT (SELECT COUNT(*) FROM audit_log), "
                "(SELECT COUNT(*) FROM operation_journal), "
                "(SELECT COUNT(*) FROM record_revisions)"
            ).fetchone()
        self.assertEqual(plan["mode"], "report_only")
        self.assertFalse(plan["applied"])
        self.assertEqual(plan["summary"]["actionable_count"], 1)
        self.assertEqual(plan["items"][0]["action"], "supersede_existing")
        self.assertEqual(
            plan["items"][0]["proof"]["evidence_kind"],
            "fresh_source_parse",
        )
        self.assertNotIn("parser_payload", plan["items"][0]["proof"])
        self.assertEqual(before_records, self.store.iter_latest_records(sort="recent"))
        self.assertEqual(before_counts, after_counts)
        self.assertEqual(before_source, open(self.source_path, "rb").read())

    def test_empty_stored_source_url_is_still_selected_and_fresh_parsed(self) -> None:
        record = self._record(
            record_id="record-wrong-capital",
            business_id="capital_increase",
            project_type="增资扩股",
            state="field_missing",
        )
        parser_payload = dict(record.parser_payload)
        postprocess_payload = dict(record.postprocess_payload)
        source_identity = dict(record.source_identity)
        parser_payload.pop("source_url", None)
        postprocess_payload.pop("source_url", None)
        source_identity.pop("source_url", None)
        source_identity["candidate_tokens"] = [f"project_code:{self.project_code}"]
        self.store.upsert_record_with_mapping_pending(
            replace(
                record,
                parser_payload=parser_payload,
                postprocess_payload=postprocess_payload,
                source_identity=source_identity,
            )
        )
        parser_calls: list[str] = []

        plan = build_business_reclassification_plan(
            runtime=self._runtime(
                parser=lambda path: parser_calls.append(path)
                or self._payload(project_type="股权转让")
            )
        )

        self.assertEqual(parser_calls, [self.source_path])
        self.assertEqual(plan["summary"]["actionable_count"], 1)
        self.assertEqual(plan["items"][0]["action"], "create_target_needed")
        self.assertEqual(plan["items"][0]["proof"]["source_url"], "")

    def test_missing_original_artifact_uses_locked_target_without_parser(self) -> None:
        missing_path = os.path.join(self.temp_dir.name, "archive", "missing.html")
        self._insert_original(source_path=missing_path, state="field_missing")
        self._insert_target(source_path=missing_path, state="field_missing")
        parser_calls: list[str] = []

        plan = build_business_reclassification_plan(
            runtime=self._runtime(parser=lambda path: parser_calls.append(path) or {})
        )

        self.assertEqual(parser_calls, [])
        self.assertEqual(plan["summary"]["actionable_count"], 1)
        self.assertEqual(plan["items"][0]["action"], "supersede_existing")
        self.assertEqual(
            plan["items"][0]["proof"]["evidence_kind"],
            "locked_target_revision",
        )

        exit_code, applied = apply_business_reclassification_plan(
            runtime=self._runtime(parser=lambda path: parser_calls.append(path) or {})
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(parser_calls, [])
        self.assertTrue(applied["applied"])
        self.assertEqual(self.store.get_record("record-wrong-capital")["state"], "skipped")
        self.assertEqual(
            self.store.get_record("record-correct-equity")["state"],
            "field_missing",
        )

    def test_apply_preserves_ready_target_and_sticky_export_cursor(self) -> None:
        original_stored = self._insert_original()
        self._insert_target()
        target_before = self.store.get_record("record-correct-equity")
        with self.store._connect() as conn:
            conn.execute(
                """
                INSERT INTO mapping_pending (
                    record_id, revision_id, project_code, payload_json, created_at
                ) VALUES (?, ?, ?, '{}', '2026-08-21 00:00:00')
                """,
                (
                    "record-wrong-capital",
                    int(original_stored["revision_id"]),
                    self.project_code,
                ),
            )
        os.makedirs(os.path.join(self.temp_dir.name, "exports"), exist_ok=True)
        self.store.mark_exported(
            export_id="export-before-reclassification",
            cursor_id="sticky-cursor",
            requested_export_mode="incremental",
            date_from="2026-08-01",
            date_to="2026-08-31",
            project_type="增资扩股",
            output_dir=os.path.join(self.temp_dir.name, "exports"),
            summary={"new_records": 1},
            records=[self.store.get_record("record-wrong-capital")],
        )

        exit_code, payload = apply_business_reclassification_plan(runtime=self._runtime())

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["results"][0]["status"], "succeeded")
        original_after = self.store.get_record("record-wrong-capital")
        target_after = self.store.get_record("record-correct-equity")
        self.assertEqual(original_after["state"], "skipped")
        self.assertEqual(original_after["last_error_type"], "superseded_by_record")
        self.assertEqual(target_after, target_before)
        self.assertIn(
            "record-wrong-capital",
            self.store.get_exported_revision_map("sticky-cursor"),
        )
        self.assertTrue(self.store.get_export_cursor_value("sticky-cursor"))
        ready_ids = {
            row["record_id"] for row in self.store.iter_latest_records(states=["ready"])
        }
        removal_ids = set(self.store.get_exported_revision_map("sticky-cursor")) - ready_ids
        self.assertIn("record-wrong-capital", removal_ids)
        self.assertEqual(self.store.list_pending_mappings(), [])
        journals = self.store.list_operation_journals(limit=5)
        self.assertEqual(journals[0]["operation_type"], "business_reclassification_repair")
        self.assertEqual(journals[0]["status"], "succeeded")
        self.assertEqual(payload["operation_id"], journals[0]["operation_id"])
        with sqlite3.connect(self.db_path) as conn:
            audit_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM audit_log
                WHERE action = 'record_business_reclassification_consolidated'
                """
            ).fetchone()[0]
        self.assertEqual(audit_count, 1)

    def test_source_sha_drift_rejects_apply_without_partial_changes(self) -> None:
        self._insert_original()
        self._insert_target()
        entries, _scanned = _build_plan_entries(runtime=self._runtime())
        entry = entries[0]
        original_before = self.store.get_record("record-wrong-capital")
        target_before = self.store.get_record("record-correct-equity")
        with open(self.source_path, "w", encoding="utf-8") as handle:
            handle.write("changed after preview")

        with self.assertRaisesRegex(RuntimeError, "SHA-256 drift"):
            self.store.consolidate_business_reclassification(
                original_snapshot=entry.original_snapshot,
                target_snapshot=entry.target_snapshot,
                target_record=entry.target_record,
                proof=entry.proof or {},
            )

        self.assertEqual(self.store.get_record("record-wrong-capital"), original_before)
        self.assertEqual(self.store.get_record("record-correct-equity"), target_before)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 0)

    def test_target_snapshot_drift_rejects_apply_before_original_retirement(self) -> None:
        self._insert_original()
        self._insert_target()
        entries, _scanned = _build_plan_entries(runtime=self._runtime())
        entry = entries[0]
        original_before = self.store.get_record("record-wrong-capital")
        self.store.update_record_state("record-correct-equity", state="field_missing")

        with self.assertRaisesRegex(RuntimeError, "target drift"):
            self.store.consolidate_business_reclassification(
                original_snapshot=entry.original_snapshot,
                target_snapshot=entry.target_snapshot,
                target_record=entry.target_record,
                proof=entry.proof or {},
            )

        self.assertEqual(self.store.get_record("record-wrong-capital"), original_before)
        self.assertEqual(self.store.get_record("record-correct-equity")["state"], "field_missing")
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 0)

    def test_late_audit_failure_rolls_back_retirement_and_cursor_state(self) -> None:
        self._insert_original()
        self._insert_target()
        entries, _scanned = _build_plan_entries(runtime=self._runtime())
        entry = entries[0]
        original_before = self.store.get_record("record-wrong-capital")
        target_before = self.store.get_record("record-correct-equity")

        with patch.object(
            self.store,
            "_add_audit_entry_conn",
            side_effect=RuntimeError("forced late failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced late failure"):
                self.store.consolidate_business_reclassification(
                    original_snapshot=entry.original_snapshot,
                    target_snapshot=entry.target_snapshot,
                    target_record=entry.target_record,
                    proof=entry.proof or {},
                )

        self.assertEqual(self.store.get_record("record-wrong-capital"), original_before)
        self.assertEqual(self.store.get_record("record-correct-equity"), target_before)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 0)

    def test_create_target_and_retire_original_share_one_transaction(self) -> None:
        self._insert_original()
        entries, _scanned = _build_plan_entries(runtime=self._runtime())
        self.assertEqual(entries[0].item["action"], "create_target_needed")
        target_record_id = str(entries[0].item["target_record_id"])

        exit_code, payload = apply_business_reclassification_plan(runtime=self._runtime())

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["results"][0]["status"], "succeeded")
        self.assertEqual(self.store.get_record("record-wrong-capital")["state"], "skipped")
        target = self.store.get_record(target_record_id)
        self.assertEqual(target["state"], "ready")
        self.assertEqual(target["business_id"], "equity_transfer")

    def test_create_target_preserves_parser_derived_field_missing_state(self) -> None:
        self._insert_original(state="field_missing")
        field_missing_payload = self._payload(project_type="股权转让")
        field_missing_payload.pop("挂牌价格")
        runtime = self._runtime(parser=lambda _path: field_missing_payload)
        entries, _scanned = _build_plan_entries(runtime=runtime)
        self.assertEqual(entries[0].item["action"], "create_target_needed")
        self.assertEqual(
            str(getattr(entries[0].target_record.state, "value", entries[0].target_record.state)),
            "field_missing",
        )

        exit_code, payload = apply_business_reclassification_plan(runtime=runtime)

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["results"][0]["status"], "succeeded")
        self.assertEqual(self.store.get_record("record-wrong-capital")["state"], "skipped")
        target = self.store.get_record(str(entries[0].item["target_record_id"]))
        self.assertEqual(target["state"], "field_missing")
        self.assertEqual(target["business_id"], "equity_transfer")

    def test_create_target_is_rolled_back_when_late_transaction_step_fails(self) -> None:
        self._insert_original()
        entries, _scanned = _build_plan_entries(runtime=self._runtime())
        entry = entries[0]
        self.assertIsNotNone(entry.target_record)
        target_record_id = str(entry.item["target_record_id"])
        original_before = self.store.get_record("record-wrong-capital")

        with patch.object(
            self.store,
            "_add_audit_entry_conn",
            side_effect=RuntimeError("forced late failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced late failure"):
                self.store.consolidate_business_reclassification(
                    original_snapshot=entry.original_snapshot,
                    target_snapshot=entry.target_snapshot,
                    target_record=entry.target_record,
                    proof=entry.proof or {},
                )

        self.assertEqual(self.store.get_record("record-wrong-capital"), original_before)
        with self.assertRaises(KeyError):
            self.store.get_record(target_record_id)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 0)

    def test_non_ready_target_shell_is_revision_locked_then_upgraded(self) -> None:
        self._insert_original()
        self._insert_target(state="skipped")
        entries, _scanned = _build_plan_entries(runtime=self._runtime())
        entry = entries[0]
        self.assertEqual(entry.item["action"], "create_target_needed")
        self.assertEqual(entry.item["target_shell_record_id"], "record-correct-equity")

        exit_code, payload = apply_business_reclassification_plan(runtime=self._runtime())

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["results"][0]["status"], "succeeded")
        self.assertEqual(self.store.get_record("record-wrong-capital")["state"], "skipped")
        target = self.store.get_record("record-correct-equity")
        self.assertEqual(target["state"], "ready")
        self.assertEqual(target["business_id"], "equity_transfer")

    def test_repeated_apply_is_a_noop_after_original_is_retired(self) -> None:
        self._insert_original()
        self._insert_target()
        first_exit, first_payload = apply_business_reclassification_plan(
            runtime=self._runtime()
        )
        target_after_first = self.store.get_record("record-correct-equity")

        second_exit, second_payload = apply_business_reclassification_plan(
            runtime=self._runtime()
        )

        self.assertEqual(first_exit, 0)
        self.assertEqual(first_payload["results"][0]["status"], "succeeded")
        self.assertEqual(second_exit, 0)
        self.assertEqual(second_payload["results"], [])
        self.assertEqual(second_payload["summary"]["actionable_count"], 0)
        self.assertEqual(self.store.get_record("record-correct-equity"), target_after_first)
        journals = self.store.list_operation_journals(limit=10)
        self.assertEqual(len(journals), 1)

    def test_planner_rejects_unreviewed_scope(self) -> None:
        self._insert_original()
        with self.assertRaisesRegex(ValueError, "scope is fixed"):
            build_business_reclassification_plan(
                runtime=self._runtime(),
                source_business_id="pre_disclosure",
                target_business_id="equity_transfer",
            )

    def test_store_rejects_non_listing_proof_scope(self) -> None:
        self._insert_original()
        self._insert_target()
        entries, _scanned = _build_plan_entries(runtime=self._runtime())
        entry = entries[0]
        proof = dict(entry.proof or {})
        proof["record_family"] = "deal"
        proof["evidence_fingerprint"] = _business_reclassification_proof_fingerprint(proof)
        with self.assertRaisesRegex(ValueError, "limited to listing"):
            self.store.consolidate_business_reclassification(
                original_snapshot=entry.original_snapshot,
                target_snapshot=entry.target_snapshot,
                proof=proof,
            )

    def test_cli_report_uses_readonly_store_without_process_lock(self) -> None:
        fake_store = object()
        fake_runtime = object()
        paths = SimpleNamespace(streaming_db_path=self.db_path)
        with (
            patch.object(repair_script, "resolve_runtime_workspace_paths", return_value=paths),
            patch.object(repair_script, "StreamingStore", return_value=fake_store) as store_class,
            patch.object(
                repair_script.BusinessReclassificationRuntime,
                "for_store",
                return_value=fake_runtime,
            ),
            patch.object(
                repair_script,
                "build_business_reclassification_plan",
                return_value={"mode": "report_only", "items": []},
            ),
            patch.object(repair_script, "ProcessLock") as process_lock,
            redirect_stdout(io.StringIO()),
        ):
            exit_code = repair_script.main(["--app-home", self.temp_dir.name])

        self.assertEqual(exit_code, 0)
        db_argument = str(store_class.call_args.args[0])
        self.assertTrue(db_argument.startswith("file:"))
        self.assertTrue(db_argument.endswith("?mode=ro"))
        self.assertEqual(store_class.call_args.kwargs, {"auto_migrate": False})
        process_lock.assert_not_called()

    def test_cli_apply_holds_database_process_lock(self) -> None:
        fake_store = object()
        fake_runtime = object()
        paths = SimpleNamespace(streaming_db_path=self.db_path)
        lock = MagicMock()
        lock.__enter__.return_value = lock
        with (
            patch.object(repair_script, "resolve_runtime_workspace_paths", return_value=paths),
            patch.object(repair_script, "database_lock_path", return_value="db.lock"),
            patch.object(repair_script, "ProcessLock", return_value=lock) as process_lock,
            patch.object(repair_script, "StreamingStore", return_value=fake_store) as store_class,
            patch.object(
                repair_script.BusinessReclassificationRuntime,
                "for_store",
                return_value=fake_runtime,
            ),
            patch.object(
                repair_script,
                "apply_business_reclassification_plan",
                return_value=(0, {"mode": "apply", "items": []}),
            ),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = repair_script.main(
                ["--app-home", self.temp_dir.name, "--apply"]
            )

        self.assertEqual(exit_code, 0)
        process_lock.assert_called_once_with(
            "db.lock",
            label="business reclassification repair",
        )
        store_class.assert_called_once_with(self.db_path, auto_migrate=False)
        lock.__enter__.assert_called_once_with()
        lock.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
