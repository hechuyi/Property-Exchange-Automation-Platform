from __future__ import annotations

import json
import unittest

from peap.download_errors import DownloadError
from peap.download_oneclick_presenters import (
    build_phase_summary,
    emit_stage,
    format_download_error,
    present_collect_error,
    stage_error_message,
)


class DownloadOneClickPresentersTest(unittest.TestCase):
    def test_stage_error_message_uses_explicit_or_typed_error_only(self) -> None:
        self.assertEqual(stage_error_message({"error_message": "boom"}), "boom")
        self.assertEqual(
            stage_error_message(
                {
                    "typed_error": {
                        "error_code": "tpre_collect_failed",
                        "error_message": "typed boom",
                        "error_details": {
                            "source_id": "tpre",
                            "stage": "prepare_tasks",
                            "failure_kind": "collect",
                            "raw_reason": "upstream 500",
                            "task_id": "tpre:physical_asset",
                        },
                    }
                }
            ),
            "typed boom",
        )
        self.assertEqual(stage_error_message({"errors": ["nested boom"]}), "")
        self.assertEqual(
            stage_error_message(
                {
                    "task_summaries": {
                        "sse:physical_asset": {
                            "errors": ["task boom"],
                        }
                    }
                }
            ),
            "",
        )

    def test_present_collect_error_rejects_raw_string_fallbacks(self) -> None:
        with self.assertRaises(TypeError):
            present_collect_error(
                [
                    "https://www.suaee.com/manageprojectweb/foreign/project/queryAllNew HTTP Error 404: Not Found"
                ]
            )

        with self.assertRaises(TypeError):
            present_collect_error(["tpre: collect-failed: upstream 500"])

    def test_format_download_error_serializes_source_id_payload(self) -> None:
        typed_error = DownloadError(
            error_code="tpre_collect_failed",
            error_message="tpre: collect-failed: upstream 500",
            stage="prepare_tasks",
            failure_kind="collect",
            source_id="tpre",
            task_id="tpre:physical_asset",
            raw_reason="upstream 500",
        )

        payload = format_download_error(typed_error)

        self.assertEqual(
            payload,
            {
                "error_code": "tpre_collect_failed",
                "error_message": "tpre: collect-failed: upstream 500",
                "error_details": {
                    "source_id": "tpre",
                    "stage": "prepare_tasks",
                    "failure_kind": "collect",
                    "raw_reason": "upstream 500",
                    "task_id": "tpre:physical_asset",
                },
            },
        )
        json.dumps(payload)

    def test_present_collect_error_uses_formatter_for_typed_error_payload(self) -> None:
        typed_error = DownloadError(
            error_code="tpre_collect_failed",
            error_message="tpre: collect-failed: upstream 500",
            stage="prepare_tasks",
            failure_kind="collect",
            source_id="tpre",
            task_id="tpre:physical_asset",
            raw_reason="upstream 500",
        )

        payload = present_collect_error([typed_error])

        self.assertEqual(payload["error_code"], "tpre_collect_failed")
        self.assertEqual(payload["error_message"], "tpre: collect-failed: upstream 500")
        self.assertEqual(payload["typed_error"], format_download_error(typed_error))
        self.assertNotIsInstance(payload["typed_error"], DownloadError)
        json.dumps(payload)

    def test_present_collect_error_does_not_parse_raw_string_when_typed_error_present(self) -> None:
        typed_error = DownloadError(
            error_code="custom_collect_failed",
            error_message="Custom formatter message",
            stage="prepare_tasks",
            failure_kind="collect",
            source_id="custom",
            task_id="custom:task",
            raw_reason="upstream broken",
        )

        payload = present_collect_error(
            [
                typed_error,
                "cbex-list-failed: list-api-failed 股权转让 p=1: api-http-521",
            ]
        )

        self.assertEqual(payload["error_code"], "custom_collect_failed")
        self.assertEqual(payload["error_message"], "Custom formatter message")
        self.assertEqual(payload["typed_error"], format_download_error(typed_error))
        self.assertEqual(payload["error_details"]["source_id"], "custom")
        self.assertEqual(payload["error_details"]["task_id"], "custom:task")
        self.assertNotEqual(
            payload["error_message"],
            "cbex-list-failed: list-api-failed 股权转让 p=1: api-http-521",
        )
        json.dumps(payload)

    def test_emit_stage_keeps_summary_payload_nested_without_flattening(self) -> None:
        emitted: list[dict[str, object]] = []
        typed_error_payload = {
            "error_code": "tpre_collect_failed",
            "error_message": "tpre: collect-failed: upstream 500",
            "error_details": {
                "source_id": "tpre",
                "stage": "prepare_tasks",
                "failure_kind": "collect",
                "raw_reason": "upstream 500",
                "task_id": "tpre:physical_asset",
            },
        }

        emit_stage(
            emitted.append,
            phase_code="prepare_tasks",
            status="failed",
            label="扫描失败",
            summary_payload={
                "typed_error": typed_error_payload,
                "typed_errors": [typed_error_payload],
            },
        )

        self.assertEqual(emitted[0]["summary_payload"]["typed_error"], typed_error_payload)
        self.assertEqual(emitted[0]["summary_payload"]["typed_errors"], [typed_error_payload])
        self.assertEqual(emitted[0]["error_message"], "tpre: collect-failed: upstream 500")
        self.assertNotIn("typed_error", {k: v for k, v in emitted[0].items() if k != "summary_payload"})
        self.assertNotIn("typed_errors", {k: v for k, v in emitted[0].items() if k != "summary_payload"})
        json.dumps(emitted[0])

    def test_build_phase_summary_and_emit_stage_keep_phase_data_nested(self) -> None:
        emitted: list[dict[str, object]] = []
        summary = build_phase_summary(
            totals={
                "pages": 0,
                "listed": 0,
                "detail_fetched": 0,
                "saved": 2,
                "list_date_skipped": 0,
                "detail_date_skipped": 0,
                "date_missing_skipped": 0,
                "resume_skipped": 0,
                "duplicate_skipped": 0,
                "missing_xmid_skipped": 0,
                "detail_candidates": 0,
                "detail_failed": 0,
                "list_unaccounted": 0,
                "detail_unaccounted": 0,
            },
            task_index=1,
            task_total=2,
            task_label="上交所 - 挂牌实物资产",
            phase_percent=49,
            collected_candidates=3,
        )

        emit_stage(
            emitted.append,
            phase_code="prepare_tasks",
            status="done",
            label="扫描完成",
            summary_payload={
                **summary,
                "error_message": "collect boom",
            },
        )

        self.assertEqual(emitted[0]["phase_code"], "prepare_tasks")
        self.assertEqual(emitted[0]["summary_payload"]["task_label"], "上交所 - 挂牌实物资产")
        self.assertEqual(emitted[0]["summary_payload"]["summary"]["collected_candidates"], 3)
        self.assertEqual(emitted[0]["error_message"], "collect boom")
        self.assertNotIn("task_label", {k: v for k, v in emitted[0].items() if k != "summary_payload"})
        self.assertNotIn("summary", {k: v for k, v in emitted[0].items() if k != "summary_payload"})
        self.assertEqual(emitted[0]["summary_payload"]["error_message"], "collect boom")


if __name__ == "__main__":
    unittest.main()
