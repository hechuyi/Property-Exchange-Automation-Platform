from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
import unittest

from peap.download_errors import DownloadError
from peap.downloaders import DownloadSummary as ExportedDownloadSummary
from peap.downloaders.common import (
    DownloadSummary,
    HttpFetchedText,
    ProgressLogThrottle,
    complete_resume_sidecar_exists,
    in_date_range,
    mark_artifact_save_failed,
    parse_bound,
    parse_loose_date,
    record_downloaded_target,
    record_duplicate_candidate,
    reserve_download_target,
    safe_filename,
    successful_http_evidence,
)


class DownloadersCommonTest(unittest.TestCase):
    def test_failed_sidecar_created_before_pending_write_keeps_task_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = os.path.join(temp_dir, "P001.html")
            json_path = os.path.splitext(html_path)[0] + ".json"
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>partial snapshot</body></html>")

            def write_json(path: str, payload: dict[str, object]) -> None:
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)

            marked = mark_artifact_save_failed(
                html_path=html_path,
                save_json=True,
                write_json=write_json,
                write_resume_status=lambda _path, _status: None,
                failure_identity={
                    "task_id": "cbex:listing:physical_asset",
                    "source_id": "cbex",
                    "record_family": "listing",
                    "business_id": "physical_asset",
                },
            )

            with open(json_path, encoding="utf-8") as handle:
                payload = json.load(handle)

        self.assertTrue(marked)
        self.assertEqual(
            payload,
            {
                "task_id": "cbex:listing:physical_asset",
                "source_id": "cbex",
                "record_family": "listing",
                "business_id": "physical_asset",
                "save_status": "failed",
            },
        )

    def test_http_fetched_text_keeps_content_and_transport_evidence_together(self) -> None:
        fetched = HttpFetchedText(
            "<html><body>official detail</body></html>",
            source_url="https://exchange.example/project/P001",
            final_url="https://exchange.example/project/P001?resolved=1",
            http_status=206,
        )

        self.assertIsInstance(fetched, str)
        self.assertEqual(str(fetched), "<html><body>official detail</body></html>")
        self.assertEqual(fetched.source_url, "https://exchange.example/project/P001")
        self.assertEqual(fetched.final_url, "https://exchange.example/project/P001?resolved=1")
        self.assertEqual(fetched.http_status, 206)

    def test_http_fetched_text_fails_closed_on_missing_transport_evidence(self) -> None:
        with self.assertRaises(ValueError):
            HttpFetchedText(
                "<html></html>",
                source_url="https://exchange.example/project/P001",
                final_url="",
                http_status=0,
            )

    def test_successful_http_evidence_preserves_actual_response_status(self) -> None:
        self.assertEqual(
            successful_http_evidence(
                source_url="https://exchange.example/project/P001",
                http_status=206,
            ),
            {
                "source_url": "https://exchange.example/project/P001",
                "http_status": 206,
            },
        )

    def test_successful_http_evidence_fails_closed_without_a_success_response(self) -> None:
        invalid_cases = (
            ("", 200),
            ("https://exchange.example/project/P001", True),
            ("https://exchange.example/project/P001", None),
            ("https://exchange.example/project/P001", 302),
            ("https://exchange.example/project/P001", 404),
            ("https://exchange.example/project/P001", 503),
        )
        for source_url, http_status in invalid_cases:
            with self.subTest(source_url=source_url, http_status=http_status):
                with self.assertRaises(ValueError):
                    successful_http_evidence(
                        source_url=source_url,
                        http_status=http_status,
                    )

    def test_parse_loose_date_accepts_epoch_and_localized_text(self) -> None:
        self.assertEqual(parse_loose_date(1704067200000), dt.date(2024, 1, 1))
        self.assertEqual(parse_loose_date("2026年3月10日"), dt.date(2026, 3, 10))
        self.assertEqual(parse_loose_date("2026/03/11"), dt.date(2026, 3, 11))

    def test_parse_bound_accepts_empty_and_rejects_invalid(self) -> None:
        self.assertIsNone(parse_bound("", "start-date"))
        self.assertIsNone(parse_bound(None, "end-date"))
        with self.assertRaisesRegex(ValueError, "invalid start-date"):
            parse_bound("not-a-date", "start-date")

    def test_in_date_range_and_safe_filename_share_common_rules(self) -> None:
        value = dt.date(2026, 3, 10)
        self.assertTrue(in_date_range(value, dt.date(2026, 3, 1), dt.date(2026, 3, 31)))
        self.assertFalse(in_date_range(value, dt.date(2026, 3, 11), dt.date(2026, 3, 31)))
        self.assertEqual(safe_filename('a/b:c*?"<>|'), "a_b_c_")

    def test_download_summary_exposes_shared_skip_counters(self) -> None:
        summary = DownloadSummary()
        exported_summary = ExportedDownloadSummary()
        self.assertEqual(summary.skipped_by_duplicate, 0)
        self.assertEqual(summary.skipped_by_missing_xmid, 0)
        self.assertEqual(summary.detail_candidates, 0)
        self.assertIsInstance(exported_summary, DownloadSummary)
        self.assertEqual(exported_summary.detail_candidates, 0)
        self.assertEqual(exported_summary.skipped_by_duplicate, 0)

    def test_download_summary_uses_typed_errors_without_legacy_string_channel(self) -> None:
        typed_error = DownloadError(
            error_code="sse_collect_failed",
            error_message="sse: collect-failed: upstream 500",
            stage="prepare_tasks",
            failure_kind="collect",
            source_id="sse",
            task_id="sse:listing:physical_asset",
            raw_reason="upstream 500",
        )

        summary = DownloadSummary(
            typed_errors=[typed_error],
        )

        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors, [typed_error])
        self.assertEqual(summary.typed_errors[0].source_id, "sse")
        self.assertNotIn("exchange", summary.typed_errors[0].to_presenter_payload()["error_details"])
        self.assertEqual(summary.typed_errors[0].to_presenter_payload()["error_details"]["source_id"], "sse")
        self.assertEqual(str(summary.typed_errors[0]), "sse: collect-failed: upstream 500")

    def test_download_summary_reserves_targets_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = DownloadSummary()
            html_path = os.path.join(temp_dir, "sse__listing__physical_asset", "2026年7月", "P001.html")

            first = reserve_download_target(
                summary,
                html_root=temp_dir,
                html_path=html_path,
                source_id="sse",
                task_id="sse:listing:physical_asset",
            )
            second = reserve_download_target(
                summary,
                html_root=temp_dir,
                html_path=html_path,
                source_id="sse",
                task_id="sse:listing:physical_asset",
            )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].error_code, "sse_duplicate_download_target")
        self.assertIn("sse__listing__physical_asset", summary.typed_errors[0].raw_reason)

    def test_download_summary_rejects_target_already_recorded_downloaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = DownloadSummary()
            html_path = os.path.join(temp_dir, "sse__listing__physical_asset", "2026年7月", "P001.html")
            record_downloaded_target(summary, html_root=temp_dir, html_path=html_path)

            reserved = reserve_download_target(
                summary,
                html_root=temp_dir,
                html_path=html_path,
                source_id="sse",
                task_id="sse:listing:physical_asset",
            )

        self.assertFalse(reserved)
        self.assertEqual(len(summary.typed_errors), 1)
        self.assertEqual(summary.typed_errors[0].error_code, "sse_duplicate_download_target")

    def test_download_summary_records_bounded_duplicate_candidate_samples(self) -> None:
        summary = DownloadSummary()

        for index in range(12):
            record_duplicate_candidate(
                summary,
                candidate_id=f"P{index:03d}",
                source_url=f"https://example.test/{index}",
                project_code=f"P{index:03d}",
                project_name=f"Project {index}",
                max_samples=3,
            )

        self.assertEqual(
            summary.duplicate_samples,
            [
                {
                    "candidate_id": "P000",
                    "source_url": "https://example.test/0",
                    "project_code": "P000",
                    "project_name": "Project 0",
                },
                {
                    "candidate_id": "P001",
                    "source_url": "https://example.test/1",
                    "project_code": "P001",
                    "project_name": "Project 1",
                },
                {
                    "candidate_id": "P002",
                    "source_url": "https://example.test/2",
                    "project_code": "P002",
                    "project_name": "Project 2",
                },
            ],
        )

    def test_business_id_key_prefers_runtime_catalog_aliases_over_legacy_mapping_table(self) -> None:
        from peap.downloaders.common import business_id_key

        self.assertEqual(business_id_key("挂牌实物资产"), "physical_asset")

    def test_progress_log_throttle_keeps_long_detail_runs_readable(self) -> None:
        throttle = ProgressLogThrottle(total=100, min_step=20, min_interval_seconds=30)

        self.assertTrue(throttle.should_log(1, now=0))
        self.assertFalse(throttle.should_log(2, now=1))
        self.assertTrue(throttle.should_log(21, now=2))
        self.assertFalse(throttle.should_log(22, now=3))
        self.assertTrue(throttle.should_log(23, now=33))
        self.assertFalse(throttle.should_log(24, now=34))
        self.assertTrue(throttle.should_log(100, now=35))

    def test_resume_rejects_complete_sidecar_when_archive_hash_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = os.path.join(temp_dir, "archive.html")
            json_path = os.path.splitext(html_path)[0] + ".json"
            original = "<html><body>original official page</body></html>"
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write(original)
            original_hash = "sha256:" + hashlib.sha256(original.encode("utf-8")).hexdigest()
            with open(json_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "save_status": "complete",
                        "archive_content_sha256": original_hash,
                        "archive_content_bytes": len(original.encode("utf-8")),
                    },
                    handle,
                )

            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>modified page</body></html>")

            self.assertFalse(complete_resume_sidecar_exists(html_path))


if __name__ == "__main__":
    unittest.main()
