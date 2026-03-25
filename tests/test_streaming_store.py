from __future__ import annotations

import os
import sqlite3
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
        before = reopened.get_record("legacy-failed-record")
        self.assertTrue(before["identity_anchor"])
        self.assertEqual(before["business_key"], f"failed:{before['identity_anchor']}")
        self.assertEqual(before["source_identity_json"]["original_evidence_path"], legacy_source)

        reopened.update_record_source_file("legacy-failed-record", moved_source)
        after = reopened.get_record("legacy-failed-record")

        self.assertEqual(after["identity_anchor"], before["identity_anchor"])
        self.assertEqual(after["business_key"], before["business_key"])
        self.assertEqual(after["source_identity_json"]["candidate_tokens"], ["project_id:CQLEGACY001"])

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
