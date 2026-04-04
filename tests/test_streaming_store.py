from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from peap.streaming_models import IngestedRecord, ItemProgressEvent, PostProcessFinding
from peap.streaming_store import StreamingStore
from peap.streaming_store_maintenance import run_streaming_store_maintenance


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
        self.assertTrue(any(str(item.get("type") or "") == "mapping_applied" for item in record["findings"]))

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

    def test_store_recreates_schema_after_database_file_is_deleted(self) -> None:
        self.store.set_setting("ui.basic", {"default_exchange": "all"})

        os.remove(self.store.db_path)

        self.assertEqual(
            self.store.get_setting("ui.basic", default={"default_exchange": "fallback"}),
            {"default_exchange": "fallback"},
        )
        self.assertEqual(self.store.count_records_by_state(), {})
        self.assertEqual(self.store.count_pending_mappings(), 0)


class StreamingStoreDeduplicationTest(unittest.TestCase):
    """Tests for intra-run page deduplication."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming_dedup.sqlite3")

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
            mode="incremental",
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
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming_state_machine.sqlite3")

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
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming_export_cursor.sqlite3")

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
            mode="incremental",
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
            mode="incremental",
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
            mode="incremental",
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
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming_job_lifecycle.sqlite3")

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


if __name__ == "__main__":
    unittest.main()
