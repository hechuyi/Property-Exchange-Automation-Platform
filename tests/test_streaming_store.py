from __future__ import annotations

import os
import tempfile
import unittest

from peap.streaming_models import IngestedRecord, ItemProgressEvent, PostProcessFinding
from peap.streaming_store import StreamingStore


class StreamingStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming.sqlite3")

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
            mode="incremental",
            date_from="2026-03-01",
            date_to="2026-03-31",
            project_type="股权转让",
            output_dir=f"{self.temp_dir.name}/exports",
            summary={"new_records": 1, "changed_records": 0},
            records=rows,
        )
        exported = self.store.get_exported_revision_map("default")
        self.assertEqual(exported[rows[0]["record_id"]]["revision_hash"], "hash-2")

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

    def test_normalize_required_mapping_states_reclassifies_unknown_project_type(self) -> None:
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
        latest = self.store.iter_latest_records(states=["pending_mapping"])
        self.assertEqual(len(latest), 1)
        self.assertTrue(any(str(item.get("type") or "") == "project_type_unknown" for item in latest[0]["findings"]))
        self.assertEqual(self.store.count_pending_mappings(), 1)

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

    def test_store_recreates_schema_after_database_file_is_deleted(self) -> None:
        self.store.set_setting("ui.basic", {"default_exchange": "all"})

        os.remove(self.store.db_path)

        self.assertEqual(
            self.store.get_setting("ui.basic", default={"default_exchange": "fallback"}),
            {"default_exchange": "fallback"},
        )
        self.assertEqual(self.store.count_records_by_state(), {})
        self.assertEqual(self.store.count_pending_mappings(), 0)


if __name__ == "__main__":
    unittest.main()
