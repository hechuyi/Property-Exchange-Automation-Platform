"""Regression tests for pipeline state machine contracts.

These tests assert known regressions in:
- assemble -> normalize -> export projection preserving project_type, status, start_date, price, seller
- streaming_export never falling back to raw payload merge once a canonical projection exists
- mapping_conflict classified as persisted review work, not exception work
- pending_review state machine semantics

IMPORTANT: mapping_conflict/pending_review lifecycle assertions belong to Task 6 only.
These tests in Task 1 cover the canonical/export regressions and basic state machine contracts.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from peap.export_projection import ExportProjectionError
from peap.streaming_export import record_to_export_payload, run_ready_export
from peap.streaming_models import (
    ExportRequest,
    IngestedRecord,
    RecordState,
)
from peap.streaming_store import StreamingStore


class PipelineStateMachineContractsTest(unittest.TestCase):
    """Regression tests for pipeline state machine contract violations."""

    def test_assemble_normalize_export_preserves_project_type(self) -> None:
        """Regression: assemble -> normalize -> export projection must preserve project_type.

        Currently project_type may be lost during the canonical chain processing.
        """
        # Create a record with all required canonical fields
        record = {
            "project_code": "G32025SH1000194",
            "project_name": "测试项目",
            "project_type": "股权转让",
            "exchange": "shanghai",
            "canonical_record": {
                "canonical_fields": {
                    "project_code": "G32025SH1000194",
                    "project_name": "测试项目",
                    "project_type": "股权转让",
                    "status": "listed",
                    "start_date": "2026-03-21",
                    "price": "108.00",
                    "seller": "上海测试公司",
                    "source_type": "国资",
                    "group_name": "上海电气集团",
                }
            },
            "canonical_projection": {
                "项目编号": "G32025SH1000194",
                "项目名称": "测试项目",
                "项目类型": "股权转让",
            },
            "parser_payload": {
                "项目编号": "G32025SH1000194",
                "项目名称": "测试项目",
                "项目类型": "股权转让",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "108.00",
                "转让方": "上海测试公司",
            },
            "postprocess_payload": {
                "项目编号": "G32025SH1000194",
                "项目名称": "测试项目",
                "项目类型": "股权转让",
            },
        }

        payload = record_to_export_payload(record)

        # project_type must be preserved through the canonical chain
        self.assertIn("项目类型", payload)
        self.assertEqual(payload["项目类型"], "股权转让")

    def test_assemble_normalize_export_preserves_status(self) -> None:
        """Regression: assemble -> normalize -> export projection must preserve status.

        Currently status may be lost during canonical chain processing.
        """
        record = {
            "project_code": "G32025SH1000194",
            "project_name": "测试项目",
            "project_type": "股权转让",
            "exchange": "shanghai",
                "canonical_record": {
                    "canonical_fields": {
                        "project_code": "G32025SH1000194",
                        "project_name": "测试项目",
                        "project_type": "股权转让",
                        "status": "listed",
                        "start_date": "2026-03-21",
                        "price": "108.00",
                        "seller": "上海测试公司",
                        "source_type": "国资",
                        "group_name": "上海电气集团",
                    }
                },
            "canonical_projection": {
                "项目编号": "G32025SH1000194",
                "项目名称": "测试项目",
                "项目类型": "股权转让",
            },
            "parser_payload": {
                "项目编号": "G32025SH1000194",
                "项目名称": "测试项目",
                "项目类型": "股权转让",
                "挂牌开始日期": "2026-03-21",
                "挂牌价格": "108.00",
                "转让方": "上海测试公司",
            },
            "postprocess_payload": {},
        }

        payload = record_to_export_payload(record)

        # status must be preserved in the export projection payload
        self.assertIn("项目状态", payload)
        self.assertEqual(payload["项目状态"], "listed")

    def test_assemble_normalize_export_preserves_start_date(self) -> None:
        """Regression: assemble -> normalize -> export projection must preserve start_date.

        Currently start_date may be lost or renamed during canonical chain processing.
        """
        record = {
            "project_code": "G32025SH1000194",
            "project_name": "测试项目",
            "project_type": "股权转让",
            "exchange": "shanghai",
            "canonical_record": {
                "canonical_fields": {
                    "project_code": "G32025SH1000194",
                    "project_name": "测试项目",
                    "project_type": "股权转让",
                    "status": "listed",
                    "start_date": "2026-03-21",
                    "price": "108.00",
                    "seller": "上海测试公司",
                }
            },
            "canonical_projection": {
                "项目编号": "G32025SH1000194",
                "项目名称": "测试项目",
                "项目类型": "股权转让",
            },
            "parser_payload": {
                "项目编号": "G32025SH1000194",
                "挂牌开始日期": "2026-03-21",
            },
            "postprocess_payload": {},
        }

        payload = record_to_export_payload(record)

        # start_date (挂牌开始日期) must be preserved
        # The regression is that canonical_projection may not include it
        self.assertTrue(
            "挂牌开始日期" in payload or "start_date" in payload,
            "start_date must be preserved in export projection"
        )

    def test_assemble_normalize_export_preserves_price(self) -> None:
        """Regression: assemble -> normalize -> export projection must preserve price.

        Currently price may be lost during canonical chain processing.
        """
        record = {
            "project_code": "G32025SH1000194",
            "project_name": "测试项目",
            "project_type": "股权转让",
            "exchange": "shanghai",
            "canonical_record": {
                "canonical_fields": {
                    "project_code": "G32025SH1000194",
                    "project_name": "测试项目",
                    "project_type": "股权转让",
                    "status": "listed",
                    "start_date": "2026-03-21",
                    "price": "108.00",
                    "seller": "上海测试公司",
                }
            },
            "canonical_projection": {
                "项目编号": "G32025SH1000194",
                "项目名称": "测试项目",
                "项目类型": "股权转让",
            },
            "parser_payload": {
                "项目编号": "G32025SH1000194",
                "挂牌价格": "108.00",
            },
            "postprocess_payload": {},
        }

        payload = record_to_export_payload(record)

        # price (挂牌价格) must be preserved
        # The regression is that it may not be in canonical_projection
        self.assertIn("挂牌价格", payload)
        self.assertEqual(payload["挂牌价格"], "108.00")

    def test_assemble_normalize_export_preserves_seller(self) -> None:
        """Regression: assemble -> normalize -> export projection must preserve seller.

        Currently seller (转让方) may be lost during canonical chain processing.
        """
        record = {
            "project_code": "G32025SH1000194",
            "project_name": "测试项目",
            "project_type": "股权转让",
            "exchange": "shanghai",
            "canonical_record": {
                "canonical_fields": {
                    "project_code": "G32025SH1000194",
                    "project_name": "测试项目",
                    "project_type": "股权转让",
                    "status": "listed",
                    "start_date": "2026-03-21",
                    "price": "108.00",
                    "seller": "上海测试公司",
                }
            },
            "canonical_projection": {
                "项目编号": "G32025SH1000194",
                "项目名称": "测试项目",
                "项目类型": "股权转让",
            },
            "parser_payload": {
                "项目编号": "G32025SH1000194",
                "转让方": "上海测试公司",
            },
            "postprocess_payload": {},
        }

        payload = record_to_export_payload(record)

        # seller (转让方) must be preserved
        # The regression is that it may not be in canonical_projection
        self.assertIn("转让方", payload)
        self.assertEqual(payload["转让方"], "上海测试公司")

    def test_streaming_export_never_falls_back_to_raw_payload_merge(self) -> None:
        """Regression: streaming_export must never fall back to raw payload merge.

        Once a canonical projection exists, export must use it exclusively.
        Currently export may fall back to merging parser_payload and postprocess_payload
        when canonical_projection is incomplete.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StreamingStore(f"{tmp_dir}/streaming.sqlite3")

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
                    source_file=f"{tmp_dir}/raw/partial.html",
                    archive_path=f"{tmp_dir}/archive/partial.html",
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
                output_dir=f"{tmp_dir}/exports",
            )
            with self.assertRaises(ExportProjectionError):
                run_ready_export(store, request, writer=lambda *_args, **_kwargs: None)


class RecordStateContractsTest(unittest.TestCase):
    """Tests for record state contracts."""

    def test_record_states_are_typed_enums_not_strings(self) -> None:
        """Record states must be proper enum types, not arbitrary strings."""
        # Verify RecordState exists and has expected values
        self.assertTrue(hasattr(RecordState, "READY"))
        self.assertTrue(hasattr(RecordState, "PENDING_MAPPING"))
        self.assertTrue(hasattr(RecordState, "MAPPING_CONFLICT"))
        self.assertTrue(hasattr(RecordState, "PARSED_FAILED"))

    def test_job_status_values_are_explicit(self) -> None:
        """Job status values must be explicit and typed."""
        # JobStatus must be defined in streaming_models
        from peap import streaming_models
        self.assertTrue(
            hasattr(streaming_models, "JobStatus"),
            "JobStatus must be defined in peap.streaming_models module. "
            "Currently it is not defined - this is the regression."
        )

        JobStatus = streaming_models.JobStatus
        self.assertTrue(hasattr(JobStatus, "SUCCESS"))
        self.assertTrue(hasattr(JobStatus, "FAILURE"))
        self.assertTrue(hasattr(JobStatus, "PARTIAL"))


if __name__ == "__main__":
    unittest.main()
