"""Tests for download CLI payload helpers."""

from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace

from peap.download_cli_payloads import download_result_to_summary_payload
from peap.download_errors import DownloadError
from peap.download_models import DownloadRunResult, TaskTypedErrorList
from peap.download_reporting import new_totals
from peap.downloaders.common import DownloadSummary


class DownloadCliPayloadsTest(unittest.TestCase):
    def test_download_result_payload_preserves_task_new_downloads(self) -> None:
        """Verify new_downloads from task summaries survive CLI payload serialization."""
        result = DownloadRunResult(
            exit_code=0,
            task_count=1,
            aggregate_summary={"saved": 1, "errors": 0},
            task_summaries={
                "cbex:physical_asset": {
                    "display_name": "Beijing (CBEX) - Physical Asset",
                    "summary": {"saved": 1, "errors": 0},
                    "errors": [],
                    "new_downloads": ["2026年4月/GR2026BJ1001952-demo.html"],
                }
            },
            typed_errors=TaskTypedErrorList(),
            any_failure=False,
        )

        payload = download_result_to_summary_payload(result, log_file="x.log", split_plan_only=False)
        self.assertEqual(
            payload["task_summaries"]["cbex:physical_asset"]["new_downloads"],
            ["2026年4月/GR2026BJ1001952-demo.html"],
        )

    def test_download_result_payload_includes_archive_audit(self) -> None:
        result = DownloadRunResult(
            exit_code=0,
            task_count=1,
            aggregate_summary={"saved": 1, "errors": 0},
            task_summaries={},
            typed_errors=TaskTypedErrorList(),
            any_failure=False,
            archive_audit={
                "ok": True,
                "html_count": 1,
                "sidecar_count": 1,
                "issue_count": 0,
                "issues": [],
            },
        )

        payload = download_result_to_summary_payload(result, log_file="x.log", split_plan_only=False)

        self.assertEqual(payload["archive_audit"]["ok"], True)
        self.assertEqual(payload["archive_audit"]["html_count"], 1)

    def test_build_task_result_accepts_new_downloads(self) -> None:
        """Verify build_task_result accepts and serializes new_downloads parameter."""
        from peap.download_reporting import build_task_result

        result = build_task_result(
            display_name="Test Task",
            summary={"saved": 1, "errors": 0},
            new_downloads=["2026年4月/test.html", "2026年4月/test2.html"],
        )
        self.assertEqual(result["new_downloads"], ["2026年4月/test.html", "2026年4月/test2.html"])

    def test_build_task_result_accepts_duplicate_samples(self) -> None:
        from peap.download_reporting import build_task_result

        duplicate_samples = [
            {
                "candidate_id": "G32026BJ100001",
                "source_url": "https://www.cbex.com.cn/xm/foo.shtml",
            }
        ]

        result = build_task_result(
            display_name="Test Task",
            summary={"saved": 1, "duplicate_skipped": 1, "errors": 0},
            duplicate_samples=duplicate_samples,
        )

        self.assertEqual(result["duplicate_samples"], duplicate_samples)

    def test_build_task_result_rejects_explicit_non_list_typed_errors(self) -> None:
        from peap.download_reporting import build_task_result

        with self.assertRaisesRegex(TypeError, "typed_errors must be a list"):
            build_task_result(
                display_name="Test Task",
                summary={"saved": 0, "errors": 0},
                typed_errors="not-a-list",  # type: ignore[arg-type]
            )

    def test_build_task_result_rejects_explicit_non_list_new_downloads(self) -> None:
        from peap.download_reporting import build_task_result

        with self.assertRaisesRegex(TypeError, "new_downloads must be a list or None"):
            build_task_result(
                display_name="Test Task",
                summary={"saved": 0, "errors": 0},
                new_downloads="not-a-list",  # type: ignore[arg-type]
            )

    def test_accumulate_rejects_bad_typed_errors_contract(self) -> None:
        from peap.download_reporting import accumulate

        summary = SimpleNamespace(typed_errors={})

        with self.assertRaisesRegex(TypeError, "summary.typed_errors must be a list"):
            accumulate(summary, new_totals(), [])

    def test_accumulate_rejects_bad_downloaded_this_run_contract(self) -> None:
        from peap.download_reporting import accumulate

        summary = DownloadSummary(saved=1)
        summary.downloaded_this_run = []  # type: ignore[assignment]

        with self.assertRaisesRegex(TypeError, "summary.downloaded_this_run must be a set"):
            accumulate(summary, new_totals(), [], set())

    def test_accumulate_rejects_non_download_error_items(self) -> None:
        from peap.download_reporting import accumulate

        summary = SimpleNamespace(typed_errors=["not-typed"])

        with self.assertRaisesRegex(TypeError, "summary.typed_errors must contain DownloadError items"):
            accumulate(summary, new_totals(), [])

    def test_classify_terminal_download_summary_rejects_non_mapping_summary(self) -> None:
        from peap.download_reporting import classify_terminal_download_summary

        with self.assertRaisesRegex(TypeError, "summary must be a mapping or None"):
            classify_terminal_download_summary([])  # type: ignore[arg-type]

    def test_classify_terminal_download_summary_rejects_pair_sequence_summary(self) -> None:
        from peap.download_reporting import classify_terminal_download_summary

        with self.assertRaisesRegex(TypeError, "summary must be a mapping or None"):
            classify_terminal_download_summary([("listed", 1)])  # type: ignore[arg-type]

    def test_detail_unavailable_skip_is_terminal_warning_not_hidden_success(self) -> None:
        from peap.download_reporting import classify_terminal_download_summary

        classification = classify_terminal_download_summary(
            {
                "listed": 3,
                "detail_candidates": 3,
                "saved": 2,
                "detail_unavailable_skipped": 1,
                "detail_failed": 0,
                "errors": 0,
            }
        )

        self.assertEqual(classification["warning_code"], "detail_pages_unavailable_skipped")
        self.assertIn("1", classification["warning_message"])
        self.assertIn("不可用", classification["warning_message"])

    def test_summary_metadata_to_dict_includes_downloaded_this_run(self) -> None:
        """Verify summary_metadata_to_dict extracts downloaded_this_run as new_downloads."""
        from peap.download_reporting import summary_metadata_to_dict
        from peap.downloaders.common import DownloadSummary

        summary = DownloadSummary()
        summary.downloaded_this_run.add("2026年4月/test.html")
        summary.downloaded_this_run.add("2026年4月/test2.html")

        metadata = summary_metadata_to_dict(summary)
        self.assertEqual(metadata["new_downloads"], ["2026年4月/test.html", "2026年4月/test2.html"])

    def test_summary_metadata_to_dict_includes_list_page_observations(self) -> None:
        from peap.download_reporting import summary_metadata_to_dict

        summary = DownloadSummary()
        summary.list_page_observations.append(
            {
                "source_id": "cquae",
                "source_label": "capital-pre",
                "page_index": 1,
                "status": "empty",
                "declared_total": 0,
                "parsed_items": 0,
            }
        )

        metadata = summary_metadata_to_dict(summary)

        self.assertEqual(
            metadata["list_page_observations"],
            [
                {
                    "source_id": "cquae",
                    "source_label": "capital-pre",
                    "page_index": 1,
                    "status": "empty",
                    "declared_total": 0,
                    "parsed_items": 0,
                }
            ],
        )

    def test_summary_metadata_to_dict_includes_discovery_task_manifest(self) -> None:
        from peap.download_reporting import summary_metadata_to_dict

        summary = DownloadSummary()
        summary.discovery_task_manifest = {
            "source_id": "sse",
            "task_id": "sse:listing:equity_transfer",
            "run_id": "run-1",
            "path": "_evidence/run-1/sse__listing__equity_transfer/discovery/task_manifest.json",
            "sha256": "sha256:" + "a" * 64,
            "bytes": 431,
        }

        metadata = summary_metadata_to_dict(summary)

        self.assertEqual(metadata["discovery_task_manifest"], summary.discovery_task_manifest)

    def test_summary_metadata_to_dict_includes_duplicate_samples(self) -> None:
        from peap.download_reporting import summary_metadata_to_dict

        summary = DownloadSummary()
        summary.duplicate_samples.append(
            {
                "candidate_id": "G32026BJ100001",
                "source_url": "https://www.cbex.com.cn/xm/foo.shtml",
                "project_code": "G32026BJ100001",
                "project_name": "Demo equity",
            }
        )

        metadata = summary_metadata_to_dict(summary)

        self.assertEqual(
            metadata["duplicate_samples"],
            [
                {
                    "candidate_id": "G32026BJ100001",
                    "source_url": "https://www.cbex.com.cn/xm/foo.shtml",
                    "project_code": "G32026BJ100001",
                    "project_name": "Demo equity",
                }
            ],
        )

    def test_summary_metadata_to_dict_rejects_bad_downloaded_this_run_contract(self) -> None:
        from peap.download_reporting import summary_metadata_to_dict

        with self.assertRaisesRegex(TypeError, "summary.downloaded_this_run must be a set"):
            summary_metadata_to_dict(SimpleNamespace(downloaded_this_run=[]))

    def test_summary_metadata_to_dict_rejects_bad_list_page_observations_contract(self) -> None:
        from peap.download_reporting import summary_metadata_to_dict

        with self.assertRaisesRegex(TypeError, "summary.list_page_observations must be a list"):
            summary_metadata_to_dict(SimpleNamespace(downloaded_this_run=set(), list_page_observations={}))
        with self.assertRaisesRegex(TypeError, r"summary.list_page_observations\[\*\] must be a mapping"):
            summary_metadata_to_dict(SimpleNamespace(downloaded_this_run=set(), list_page_observations=["bad"]))

    def test_summary_metadata_to_dict_rejects_bad_discovery_manifest_contract(self) -> None:
        from peap.download_reporting import summary_metadata_to_dict

        with self.assertRaisesRegex(TypeError, "summary.discovery_task_manifest must be a mapping"):
            summary_metadata_to_dict(
                SimpleNamespace(
                    downloaded_this_run=set(),
                    discovery_task_manifest="bad",
                )
            )
        with self.assertRaisesRegex(ValueError, "discovery manifest sha256"):
            summary = DownloadSummary()
            summary.discovery_task_manifest = {
                "source_id": "sse",
                "task_id": "sse:listing:equity_transfer",
                "run_id": "run-1",
                "path": "_evidence/run/task/discovery/task_manifest.json",
                "sha256": "not-a-hash",
                "bytes": 1,
            }
            summary_metadata_to_dict(summary)

        with self.assertRaisesRegex(ValueError, "source_id"):
            summary = DownloadSummary()
            summary.discovery_task_manifest = {
                "path": "_evidence/run/task/discovery/task_manifest.json",
                "sha256": "sha256:" + "a" * 64,
                "bytes": 1,
            }
            summary_metadata_to_dict(summary)

    def test_summary_to_dict_rejects_non_download_error_items(self) -> None:
        from peap.download_reporting import summary_to_dict

        summary = SimpleNamespace(typed_errors=[{"error_message": "bad"}])

        with self.assertRaisesRegex(TypeError, "summary.typed_errors must contain DownloadError items"):
            summary_to_dict(summary)

    def test_summary_to_dict_counts_typed_download_errors(self) -> None:
        from peap.download_reporting import summary_to_dict

        typed_error = DownloadError(
            error_code="tpre_collect_failed",
            error_message="tpre: collect-failed: upstream 500",
            stage="prepare_tasks",
            failure_kind="collect",
            source_id="tpre",
            task_id="tpre:listing:physical_asset",
            raw_reason="upstream 500",
        )
        summary = SimpleNamespace(typed_errors=[typed_error])

        self.assertEqual(summary_to_dict(summary)["errors"], 1)

    def test_print_summary_limits_error_samples_with_remaining_count(self) -> None:
        from peap.download_reporting import print_summary

        errors = [
            DownloadError(
                error_code=f"cquae_execute_failed_{index}",
                error_message=f"cquae: execute-failed: detail error {index}",
                stage="save_pages",
                failure_kind="execute",
                source_id="cquae",
                task_id="cquae:listing:equity_transfer",
                raw_reason=f"detail error {index}",
            )
            for index in range(1, 7)
        ]
        summary = DownloadSummary()
        summary.typed_errors.extend(errors)
        buffer = io.StringIO()

        with contextlib.redirect_stdout(buffer):
            print_summary("Unit", summary, error_limit=3)

        output = buffer.getvalue()
        self.assertIn("Unit errors (first 3 of 6; 3 more not shown):", output)
        self.assertIn("detail error 1", output)
        self.assertIn("detail error 3", output)
        self.assertNotIn("detail error 4", output)


if __name__ == "__main__":
    unittest.main()
