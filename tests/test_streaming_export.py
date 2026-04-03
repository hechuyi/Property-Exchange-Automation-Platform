from __future__ import annotations

import tempfile
import unittest
from datetime import datetime as _REAL_DATETIME
from unittest.mock import patch

from peap.streaming_export import record_to_export_payload, run_ready_export
from peap.streaming_models import ExportRequest, IngestedRecord
from peap.streaming_store import StreamingStore


class StreamingExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming.sqlite3")
        self.store.upsert_record(
            IngestedRecord(
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
                parser_payload={
                    "项目编号": "G32025SH1000194",
                    "项目名称": "测试项目",
                    "项目类型": "股权转让",
                    "类型": "国资",
                    "转让方": "上海测试公司",
                    "挂牌次数": 3,
                    "挂牌开始日期": "2026-03-21",
                },
                postprocess_payload={"项目编号": "G32025SH1000194", "项目名称": "测试项目", "项目类型": "股权转让"},
                findings=[],
            )
        )

    def test_run_ready_export_uses_unique_export_id_within_same_second(self) -> None:
        request = ExportRequest(
            date_from="2026-03-01",
            date_to="2026-03-31",
            business_types=["股权转让"],
            mode="incremental",
            output_dir=f"{self.temp_dir.name}/exports",
        )

        class _FrozenDatetime:
            @classmethod
            def now(cls):
                return _REAL_DATETIME(2026, 3, 22, 10, 0, 0)

        writer_calls: list[str] = []

        def fake_writer(file_path: str, rows: list[dict[str, object]]) -> None:
            writer_calls.append(file_path)

        with patch("peap.streaming_export.dt.datetime", _FrozenDatetime):
            first = run_ready_export(self.store, request, writer=fake_writer)
            second = run_ready_export(self.store, request, writer=fake_writer)

        self.assertNotEqual(first.export_id, second.export_id)
        self.assertEqual(len(writer_calls), 1)

    def test_run_ready_export_matches_records_saved_with_slash_dates(self) -> None:
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-2",
                revision_hash="hash-2",
                project_code="G32025SH1000999",
                project_name="斜杠日期项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026/03/20",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/b.html",
                archive_path=f"{self.temp_dir.name}/archive/b.html",
                parser_payload={"项目编号": "G32025SH1000999", "项目名称": "斜杠日期项目"},
                postprocess_payload={"项目编号": "G32025SH1000999", "项目名称": "斜杠日期项目", "项目类型": "股权转让"},
                findings=[],
            )
        )
        request = ExportRequest(
            date_from="2026-03-20",
            date_to="2026-03-20",
            business_types=["股权转让"],
            mode="incremental",
            output_dir=f"{self.temp_dir.name}/exports",
        )

        writer_calls: list[str] = []

        def fake_writer(file_path: str, rows: list[dict[str, object]]) -> None:
            writer_calls.append(file_path)

        result = run_ready_export(self.store, request, writer=fake_writer)

        self.assertEqual(result.new_records, 1)
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(len(writer_calls), 1)

    def test_run_ready_export_uses_output_contract_headers_and_values(self) -> None:
        request = ExportRequest(
            date_from="2026-03-21",
            date_to="2026-03-21",
            business_types=["股权转让"],
            mode="rebuild",
            output_dir=f"{self.temp_dir.name}/exports",
        )
        captured: dict[str, object] = {}

        def fake_writer(file_path: str, rows: list[dict[str, object]]) -> None:
            captured["file_path"] = file_path
            captured["rows"] = rows

        result = run_ready_export(self.store, request, writer=fake_writer)

        self.assertEqual(len(result.artifacts), 1)
        self.assertIn("挂牌_股权转让", str(captured["file_path"]))
        self.assertEqual(len(captured["rows"]), 1)
        row = captured["rows"][0]
        self.assertEqual(row["类型"], "国资")
        self.assertEqual(row["转让方"], "上海测试公司")
        self.assertEqual(row["挂牌次数"], 3)

    def test_record_to_export_payload_accepts_explicit_canonical_projection(self) -> None:
        payload = record_to_export_payload(
            {
                "project_code": "G32025SH1000194",
                "project_name": "测试项目",
                "project_type": "股权转让",
                "exchange": "shanghai",
                "canonical_projection": {
                    "项目编号": "G32025SH1000194",
                    "项目名称": "测试项目",
                    "项目类型": "股权转让",
                    "挂牌价格": "108.00",
                    "转让方": "上海测试公司",
                },
                "parser_payload": {
                    "未审计透传字段": "should-not-leak",
                },
                "postprocess_payload": {
                    "另一个透传字段": "still-should-not-leak",
                },
            }
        )

        self.assertEqual(payload["项目编号"], "G32025SH1000194")
        self.assertEqual(payload["挂牌价格"], "108.00")
        self.assertEqual(payload["转让方"], "上海测试公司")
        self.assertNotIn("未审计透传字段", payload)
        self.assertNotIn("另一个透传字段", payload)

    def test_run_ready_export_prefers_persisted_canonical_projection_over_raw_payload_merge(self) -> None:
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-canonical-export",
                revision_hash="hash-canonical-export",
                project_code="G32025SH1000998",
                project_name="原始项目名",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/canonical.html",
                archive_path=f"{self.temp_dir.name}/archive/canonical.html",
                parser_payload={
                    "项目编号": "G32025SH1000998",
                    "项目名称": "解析层项目名",
                    "项目类型": "股权转让",
                    "转让方": "解析层卖方",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1000998",
                    "项目名称": "后处理项目名",
                    "项目类型": "股权转让",
                    "转让方": "后处理卖方",
                },
                source_identity={
                    "record_family": "listing",
                    "original_source_file": f"{self.temp_dir.name}/raw/canonical.html",
                    "source_url": "https://example.test/detail/export-canonical",
                    "project_code": "G32025SH1000998",
                    "project_name": "原始项目名",
                    "exchange": "shanghai",
                    "listing_date": "2026-03-21",
                    "candidate_tokens": [
                        "project_code:G32025SH1000998",
                        "page_url:https://example.test/detail/export-canonical",
                    ],
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {"project_code": "G32025SH1000998"},
                    "canonical_fields": {
                        "project_code": "G32025SH1000998",
                        "project_name": "规范化项目名",
                        "project_type": "股权转让",
                        "seller": "规范化卖方",
                    },
                },
                canonical_projection={
                    "项目编号": "G32025SH1000998",
                    "项目名称": "规范化项目名",
                    "项目类型": "股权转让",
                    "转让方": "规范化卖方",
                },
                findings=[],
            )
        )
        request = ExportRequest(
            date_from="2026-03-21",
            date_to="2026-03-21",
            business_types=["股权转让"],
            mode="rebuild",
            output_dir=f"{self.temp_dir.name}/exports",
        )
        captured: dict[str, object] = {}

        def fake_writer(file_path: str, rows: list[dict[str, object]]) -> None:
            captured["rows"] = rows

        run_ready_export(self.store, request, writer=fake_writer)

        row = next(item for item in captured["rows"] if item["项目编号"] == "G32025SH1000998")
        self.assertEqual(row["项目名称"], "规范化项目名")
        self.assertEqual(row["转让方"], "规范化卖方")

    def test_record_to_export_payload_does_not_pass_through_arbitrary_raw_fields(self) -> None:
        payload = record_to_export_payload(
            {
                "project_code": "G32025SH1000194",
                "project_name": "测试项目",
                "project_type": "股权转让",
                "exchange": "shanghai",
                "parser_payload": {
                    "项目编号": "G32025SH1000194",
                    "项目名称": "测试项目",
                    "项目类型": "股权转让",
                    "挂牌价格": "108.00",
                    "未审计透传字段": "should-not-leak",
                },
                "postprocess_payload": {
                    "转让方": "上海测试公司",
                    "另一个透传字段": "still-should-not-leak",
                },
            }
        )

        self.assertEqual(payload["项目编号"], "G32025SH1000194")
        self.assertEqual(payload["挂牌价格"], "108.00")
        self.assertEqual(payload["转让方"], "上海测试公司")
        self.assertNotIn("未审计透传字段", payload)
        self.assertNotIn("另一个透传字段", payload)

    def test_record_to_export_payload_preserves_public_resource_fields_needed_by_writer(self) -> None:
        payload = record_to_export_payload(
            {
                "project_code": "GR20260001",
                "project_name": "成交样例项目",
                "project_type": "股权转让",
                "exchange": "北交所",
                "parser_payload": {
                    "交易所": "北交所",
                    "项目编号": "GR20260001",
                    "项目名称": "成交样例项目",
                    "交易方式": "网络竞价",
                    "受让方名称": "样例受让方",
                    "转让标的评估值": "88.00",
                    "成交金额": "108.00",
                    "成交日期": "2026/03/01",
                },
                "postprocess_payload": {},
            }
        )

        self.assertEqual(payload["交易方式"], "网络竞价")
        self.assertEqual(payload["受让方名称"], "样例受让方")
        self.assertEqual(payload["转让标的评估值"], "88.00")
        self.assertEqual(payload["成交金额"], "108.00")
        self.assertEqual(payload["成交日期"], "2026/03/01")

    def test_run_ready_export_rebuild_twice_still_exports_full_scoped_range(self) -> None:
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-2",
                revision_hash="hash-2",
                project_code="G32025SH1000195",
                project_name="测试项目二",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/c.html",
                archive_path=f"{self.temp_dir.name}/archive/c.html",
                parser_payload={
                    "项目编号": "G32025SH1000195",
                    "项目名称": "测试项目二",
                    "项目类型": "股权转让",
                    "挂牌开始日期": "2026-03-21",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1000195",
                    "项目名称": "测试项目二",
                    "项目类型": "股权转让",
                },
                findings=[],
            )
        )
        request = ExportRequest(
            date_from="2026-03-21",
            date_to="2026-03-21",
            business_types=["股权转让"],
            mode="rebuild",
            output_dir=f"{self.temp_dir.name}/exports",
        )

        first_capture: dict[str, object] = {}
        second_capture: dict[str, object] = {}

        def fake_writer_first(file_path: str, rows: list[dict[str, object]]) -> None:
            first_capture["file_path"] = file_path
            first_capture["rows"] = rows

        def fake_writer_second(file_path: str, rows: list[dict[str, object]]) -> None:
            second_capture["file_path"] = file_path
            second_capture["rows"] = rows

        first = run_ready_export(self.store, request, writer=fake_writer_first)
        second = run_ready_export(self.store, request, writer=fake_writer_second)

        self.assertEqual(first.new_records, 2)
        self.assertEqual(first.changed_records, 0)
        self.assertEqual(len(first.artifacts), 1)
        self.assertEqual(len(first_capture["rows"]), 2)
        self.assertEqual(second.new_records, 2)
        self.assertEqual(second.changed_records, 0)
        self.assertEqual(len(second.artifacts), 1)
        self.assertEqual(len(second_capture["rows"]), 2)

    def test_run_ready_export_treats_mixed_case_rebuild_as_full_rebuild(self) -> None:
        request = ExportRequest(
            date_from="2026-03-21",
            date_to="2026-03-21",
            business_types=["股权转让"],
            mode="ReBuild",
            output_dir=f"{self.temp_dir.name}/exports",
        )

        first_capture: dict[str, object] = {}
        second_capture: dict[str, object] = {}

        def fake_writer_first(file_path: str, rows: list[dict[str, object]]) -> None:
            first_capture["rows"] = rows

        def fake_writer_second(file_path: str, rows: list[dict[str, object]]) -> None:
            second_capture["rows"] = rows

        first = run_ready_export(self.store, request, writer=fake_writer_first)
        second = run_ready_export(self.store, request, writer=fake_writer_second)

        self.assertEqual(first.new_records, 1)
        self.assertEqual(second.new_records, 1)
        self.assertEqual(len(first_capture["rows"]), 1)
        self.assertEqual(len(second_capture["rows"]), 1)

    def test_default_cursor_key_changes_with_keyword_scope(self) -> None:
        request = ExportRequest(
            date_from="2026-03-21",
            date_to="2026-03-21",
            business_types=["股权转让"],
            mode="incremental",
            output_dir=f"{self.temp_dir.name}/exports",
        )
        object.__setattr__(request, "requested_state", "all")
        object.__setattr__(request, "keyword", "")

        first = run_ready_export(self.store, request, writer=lambda *_args, **_kwargs: None)

        object.__setattr__(request, "keyword", "北交所")
        second = run_ready_export(self.store, request, writer=lambda *_args, **_kwargs: None)

        self.assertNotEqual(first.cursor_key, second.cursor_key)

    def test_run_ready_export_rejects_non_listing_record_family(self) -> None:
        request = ExportRequest(
            date_from="2026-03-21",
            date_to="2026-03-21",
            business_types=["股权转让"],
            mode="incremental",
            output_dir=f"{self.temp_dir.name}/exports",
            record_family="deal",
        )

        with self.assertRaises(ValueError):
            run_ready_export(self.store, request, writer=lambda *_args, **_kwargs: None)

    def test_run_ready_export_filters_records_by_record_family(self) -> None:
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-deal-export",
                revision_hash="hash-deal-export",
                project_code="D32026SH000002",
                project_name="成交导出项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/deal-export.html",
                archive_path=f"{self.temp_dir.name}/archive/deal-export.html",
                parser_payload={"项目编号": "D32026SH000002", "项目名称": "成交导出项目"},
                postprocess_payload={"项目编号": "D32026SH000002", "项目名称": "成交导出项目", "项目类型": "股权转让"},
                findings=[],
                record_family="deal",
            )
        )
        request = ExportRequest(
            date_from="2026-03-21",
            date_to="2026-03-21",
            business_types=["股权转让"],
            mode="incremental",
            output_dir=f"{self.temp_dir.name}/exports",
            record_family="listing",
        )
        captured: dict[str, object] = {}

        def fake_writer(file_path: str, rows: list[dict[str, object]]) -> None:
            captured["file_path"] = file_path
            captured["rows"] = rows

        result = run_ready_export(self.store, request, writer=fake_writer)

        self.assertEqual(result.new_records, 1)
        self.assertEqual(len(captured["rows"]), 1)
        self.assertEqual(captured["rows"][0]["项目编号"], "G32025SH1000194")


class StreamingExportRegressionTest(unittest.TestCase):
    """Regression tests for streaming export contract violations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def test_export_never_falls_back_to_raw_payload_merge_when_canonical_projection_exists(self) -> None:
        """Regression: streaming_export must never fall back to raw payload merge.

        Once a canonical projection exists, export must use it exclusively.
        Currently export may fall back to merging parser_payload and postprocess_payload
        when canonical_projection is incomplete.
        """
        store = StreamingStore(f"{self.temp_dir.name}/streaming_export_regression.sqlite3")

        # Create a record with canonical_projection that is incomplete
        # but parser_payload has the missing fields
        store.upsert_record(
            IngestedRecord(
                record_id="rec-partial-canonical",
                revision_hash="hash-partial",
                project_code="G32025SH1000999",
                project_name="部分规范化项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/partial.html",
                archive_path=f"{self.temp_dir.name}/archive/partial.html",
                parser_payload={
                    "项目编号": "G32025SH1000999",
                    "项目名称": "解析层名称",
                    "项目类型": "股权转让",
                    "挂牌价格": "200.00",
                    "转让方": "解析层卖方",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1000999",
                    "项目名称": "后处理名称",
                    "项目类型": "股权转让",
                },
                # canonical_projection is incomplete - missing price and seller
                canonical_projection={
                    "项目编号": "G32025SH1000999",
                    "项目名称": "规范化名称",
                    "项目类型": "股权转让",
                },
                findings=[],
            )
        )

        request = ExportRequest(
            date_from="2026-03-21",
            date_to="2026-03-21",
            business_types=["股权转让"],
            mode="rebuild",
            output_dir=f"{self.temp_dir.name}/exports",
        )
        captured_rows = []

        def fake_writer(file_path: str, rows: list[dict]) -> None:
            captured_rows.extend(rows)

        run_ready_export(store, request, writer=fake_writer)

        # Export must NOT fall back to raw payload merge
        exported_row = next(r for r in captured_rows if r.get("项目编号") == "G32025SH1000999")

        # The project_name should be from canonical_projection, NOT parser_payload or postprocess_payload
        self.assertEqual(
            exported_row["项目名称"],
            "规范化名称",
            "Export must use canonical_projection, not fall back to raw payload merge"
        )

        # If price is missing from canonical_projection, export should fail or use PipelineFailure
        # It must NOT silently merge from parser_payload
        self.assertNotIn(
            "挂牌价格",
            exported_row,
            "streaming_export fell back to raw payload merge when canonical_projection was incomplete"
        )

    def test_assemble_normalize_export_preserves_required_canonical_fields(self) -> None:
        """Regression: assemble -> normalize -> export must preserve required canonical fields.

        project_type, status, start_date, price, seller must be preserved.
        """
        store = StreamingStore(f"{self.temp_dir.name}/streaming_export_fields.sqlite3")

        # Create a record with all required canonical fields
        store.upsert_record(
            IngestedRecord(
                record_id="rec-full-canonical",
                revision_hash="hash-full",
                project_code="G32025SH1000194",
                project_name="完整规范化项目",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-03-21",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/full.html",
                archive_path=f"{self.temp_dir.name}/archive/full.html",
                parser_payload={
                    "项目编号": "G32025SH1000194",
                    "项目名称": "完整规范化项目",
                    "项目类型": "股权转让",
                    "挂牌开始日期": "2026-03-21",
                    "挂牌价格": "108.00",
                    "转让方": "上海测试公司",
                    "项目状态": "挂牌中",
                },
                postprocess_payload={
                    "项目编号": "G32025SH1000194",
                    "项目名称": "完整规范化项目",
                    "项目类型": "股权转让",
                },
                canonical_record={
                    "record_family": "listing",
                    "canonical_fields": {
                        "project_code": "G32025SH1000194",
                        "project_name": "完整规范化项目",
                        "project_type": "股权转让",
                        "status": "listed",
                        "start_date": "2026-03-21",
                        "price": "108.00",
                        "seller": "上海测试公司",
                    }
                },
                canonical_projection={
                    "项目编号": "G32025SH1000194",
                    "项目名称": "完整规范化项目",
                    "项目类型": "股权转让",
                },
                findings=[],
            )
        )

        request = ExportRequest(
            date_from="2026-03-21",
            date_to="2026-03-21",
            business_types=["股权转让"],
            mode="rebuild",
            output_dir=f"{self.temp_dir.name}/exports",
        )
        captured_rows = []

        def fake_writer(file_path: str, rows: list[dict]) -> None:
            captured_rows.extend(rows)

        run_ready_export(store, request, writer=fake_writer)

        exported_row = next(r for r in captured_rows if r.get("项目编号") == "G32025SH1000194")

        # All required canonical fields must be preserved
        # project_type
        self.assertIn("项目类型", exported_row, "project_type must be preserved in export")

        # status (项目状态)
        self.assertIn("项目状态", exported_row, "status must be preserved in export")

        # start_date (挂牌开始日期)
        self.assertIn("挂牌开始日期", exported_row, "start_date must be preserved in export")

        # price (挂牌价格)
        self.assertIn("挂牌价格", exported_row, "price must be preserved in export")

        # seller (转让方)
        self.assertIn("转让方", exported_row, "seller must be preserved in export")


if __name__ == "__main__":
    unittest.main()
