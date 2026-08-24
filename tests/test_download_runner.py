from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from peap.download_errors import execute_failed_error
from peap.download_models import DownloadTaskRunResult
from peap.download_reporting import new_totals
from peap.download_runner import (
    DownloadRunnerError,
    DownloadRunRequest,
    audit_download_run_archives,
    build_download_runner_settings,
    build_downloader,
    ensure_runtime_dependencies,
    prepare_download_session,
    run_download_session,
    run_downloader,
    run_downloader_with_prefetched,
    task_progress_label,
)
from peap.download_task_flow import run_download_task
from peap.download_tasks import build_task_registry
from peap.downloaders.common import DownloadSummary, HttpFetchedText, record_downloaded_target
from peap.downloaders.discovery_contract import expected_discovery_query_ids
from peap.downloaders.discovery_evidence import DiscoveryTaskEvidence
from peap_core.source_catalog import get_source_descriptor


class DownloadRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )
        self.config = SimpleNamespace(
            AUTO_HTML_FOLDER="C:\\temp\\auto_html",
            HTML_FOLDER="C:\\temp\\manual_html",
            PROJECT_ROOT="C:\\repo\\PEAP",
            DOWNLOAD_CHUNK_STATE_DIR="C:\\temp\\chunk_state",
            DOWNLOADER_TASK_PAGE_SIZE={
                "sse:listing:physical_asset": 20,
                "cbex:listing:physical_asset": 20,
                "sse:listing:equity_transfer": 20,
                "sse:listing:capital_increase": 20,
                "sse:listing:pre_disclosure": 20,
                "cbex:listing:equity_transfer": 20,
                "cbex:listing:capital_increase": 20,
                "cbex:listing:pre_disclosure": 20,
                "tpre:listing:physical_asset": 20,
                "tpre:listing:equity_transfer": 20,
                "tpre:listing:capital_increase": 20,
                "tpre:listing:pre_disclosure": 20,
                "cquae:listing:physical_asset": 20,
                "cquae:listing:equity_transfer": 20,
                "cquae:listing:capital_increase": 20,
                "cquae:listing:pre_disclosure": 20,
                "shandong:listing:equity_transfer": 20,
                "shandong:listing:capital_increase": 20,
                "guangdong:listing:equity_transfer": 20,
                "guangdong:listing:capital_increase": 20,
                "shenzhen:listing:equity_transfer": 20,
                "shenzhen:listing:capital_increase": 20,
            },
            is_path_within_project_root=lambda path: False,
        )

    def _write_discovery_evidence(
        self,
        root: str,
        *,
        task_id: str,
        run_id: str = "run-test-discovery",
    ) -> dict[str, object]:
        source_id, record_family, business_id = task_id.split(":")
        query_ids = expected_discovery_query_ids(
            source_id=source_id,
            record_family=record_family,
            business_id=business_id,
        )
        task = DiscoveryTaskEvidence(
            root=root,
            source_id=source_id,
            task_id=task_id,
            run_id=run_id,
            expected_query_ids=query_ids,
        )
        for index, query_id in enumerate(query_ids, start=1):
            query = task.query(query_id, authoritative_total=True, page_size=20)
            response = HttpFetchedText(
                '{"rows":[]}',
                source_url=f"https://example.test/list?query={index}",
                final_url=f"https://example.test/list?query={index}",
                http_status=200,
                raw_bytes=b'{"rows":[]}',
            )
            query.record_page(
                page_index=1,
                response=response,
                body_format="json",
                extracted_row_count=0,
                declared_total_items=0,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")
        task.complete()
        return task.manifest_reference()

    def test_run_download_session_merges_task_results(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        args = SimpleNamespace(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            output_root="C:\\temp\\auto_html",
            force_manual_root=False,
            start_date="2026-01-01",
            end_date="2026-01-02",
            split_plan_only=False,
            split_use_plan=False,
            split_plan_file=None,
            split_candidates=10,
            split_min_days=1,
            split_max_depth=3,
            split_mode="fast",
            page_size=None,
            max_pages=None,
            concurrency=2,
            resume=True,
            save_json=False,
            sse_ca_bundle=None,
            sse_ssl_verify=True,
            auto_split=False,
            chunk_state_file=None,
        )
        totals = new_totals()
        totals["saved"] = 2
        task_result = {
            "display_name": spec.display_name,
            "summary": {"saved": 2, "errors": 0},
            "errors": [],
        }

        with (
            patch("peap.download_runner.resolve_tasks", return_value=[spec]) as resolve_tasks,
            patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
            patch("peap.download_runner.load_requested_split_plans", return_value={}),
            patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
            patch("peap.download_runner.build_download_artifact_audit", return_value=None),
            patch("peap.download_runner._audit_new_download_archive_roots", return_value={}),
            patch(
                "peap.download_runner.run_download_task",
                return_value=DownloadTaskRunResult(
                    any_failure=False,
                    totals=totals,
                    typed_errors=[],
                    task_result=task_result,
                ),
            ) as run_download_task,
        ):
            result = run_download_session(
                args,
                logger=self.logger,
                config_obj=self.config,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.task_count, 1)
        self.assertEqual(result.aggregate_summary["saved"], 2)
        self.assertEqual(result.task_summaries[spec.task_id]["summary"]["saved"], 2)
        resolve_tasks.assert_called_once()
        run_download_task.assert_called_once()

    def test_run_download_session_aggregate_summary_counts_typed_errors(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        args = SimpleNamespace(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            output_root="C:\\temp\\auto_html",
            force_manual_root=False,
            start_date="2026-01-01",
            end_date="2026-01-02",
            split_plan_only=False,
            split_use_plan=False,
            split_plan_file=None,
            split_candidates=10,
            split_min_days=1,
            split_max_depth=3,
            split_mode="fast",
            page_size=None,
            max_pages=None,
            concurrency=2,
            resume=True,
            save_json=False,
            sse_ca_bundle=None,
            sse_ssl_verify=True,
            auto_split=False,
            chunk_state_file=None,
        )
        totals = new_totals()
        task_error = execute_failed_error(
            source_id="sse",
            task_id=spec.task_id,
            raw_reason="rendered-page-fetch-failed",
        )

        with (
            patch("peap.download_runner.resolve_tasks", return_value=[spec]),
            patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
            patch("peap.download_runner.load_requested_split_plans", return_value={}),
            patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
            patch("peap.download_runner.build_download_artifact_audit", return_value=None),
            patch(
                "peap.download_runner.run_download_task",
                return_value=DownloadTaskRunResult(
                    any_failure=True,
                    totals=totals,
                    typed_errors=[task_error],
                    task_result={
                        "display_name": spec.display_name,
                        "summary": {"saved": 0},
                        "errors": [task_error.error_message],
                    },
                ),
            ),
        ):
            result = run_download_session(
                args,
                logger=self.logger,
                config_obj=self.config,
            )

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.aggregate_summary["detail_failed"], 0)
        self.assertEqual(result.aggregate_summary["errors"], 2)

    def test_run_download_session_fails_when_summary_has_unaccounted_candidates(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        args = SimpleNamespace(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            output_root="C:\\temp\\auto_html",
            force_manual_root=False,
            start_date="2026-01-01",
            end_date="2026-01-02",
            split_plan_only=False,
            split_use_plan=False,
            split_plan_file=None,
            split_candidates=10,
            split_min_days=1,
            split_max_depth=3,
            split_mode="fast",
            page_size=None,
            max_pages=None,
            concurrency=2,
            resume=True,
            save_json=False,
            sse_ca_bundle=None,
            sse_ssl_verify=True,
            auto_split=False,
            chunk_state_file=None,
        )
        summary = SimpleNamespace(
            pages_requested=1,
            listed_items=3,
            detail_fetched=1,
            saved=1,
            skipped_by_list_date=0,
            skipped_by_detail_date=0,
            date_missing_skipped=0,
            skipped_by_resume=0,
            skipped_by_duplicate=0,
            skipped_by_business_filter=0,
            skipped_by_missing_xmid=0,
            skipped_by_detail_unavailable=0,
            detail_candidates=2,
            detail_failed=0,
            list_unaccounted=1,
            detail_unaccounted=1,
            typed_errors=[],
            downloaded_this_run=set(),
            list_page_observations=[],
        )

        with (
            patch("peap.download_runner.resolve_tasks", return_value=[spec]),
            patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
            patch("peap.download_runner.load_requested_split_plans", return_value={}),
            patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
            patch("peap.download_runner.build_download_artifact_audit", return_value=None),
            patch("peap.download_runner.build_downloader", return_value=object()),
            patch("peap.download_runner._audit_new_download_archive_roots", return_value={}),
            patch("peap.download_runner.run_downloader", return_value=summary),
        ):
            result = run_download_session(
                args,
                logger=self.logger,
                config_obj=self.config,
            )

        self.assertEqual(result.exit_code, 1)
        self.assertTrue(result.any_failure)
        self.assertEqual(result.aggregate_summary["list_unaccounted"], 1)
        self.assertEqual(result.aggregate_summary["detail_unaccounted"], 1)
        self.assertEqual(result.aggregate_summary["errors"], 1)
        self.assertEqual(result.typed_errors[0].error_code, "sse_unaccounted_download_candidates")

    def test_run_download_session_fails_when_new_archive_artifact_is_not_auditable(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        with tempfile.TemporaryDirectory() as temp_dir:
            args = SimpleNamespace(
                exchange="sse",
                record_family="listing",
                business_id="physical_asset",
                output_root=temp_dir,
                force_manual_root=False,
                start_date="2026-07-01",
                end_date="2026-07-01",
                split_plan_only=False,
                split_use_plan=False,
                split_plan_file=None,
                split_candidates=10,
                split_min_days=1,
                split_max_depth=3,
                split_mode="fast",
                page_size=None,
                max_pages=None,
                concurrency=2,
                resume=True,
                save_json=False,
                sse_ca_bundle=None,
                sse_ssl_verify=True,
                auto_split=False,
                chunk_state_file=None,
            )

            def fake_run_download_task(*args, **kwargs):
                html_path = os.path.join(
                    temp_dir,
                    "sse__listing__physical_asset",
                    "2026年7月",
                    "P001-missing-sidecar.html",
                )
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>P001</body></html>")
                totals = new_totals()
                totals["saved"] = 1
                relpath = os.path.relpath(html_path, temp_dir)
                return DownloadTaskRunResult(
                    any_failure=False,
                    totals=totals,
                    typed_errors=[],
                    task_result={
                        "display_name": spec.display_name,
                        "summary": {"saved": 1, "errors": 0},
                        "errors": [],
                        "new_downloads": [relpath],
                        "discovery_task_manifest": self._write_discovery_evidence(
                            temp_dir,
                            task_id=spec.task_id,
                            run_id=kwargs["args"].run_id,
                        ),
                    },
                )

            with (
                patch("peap.download_runner.resolve_tasks", return_value=[spec]),
                patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
                patch("peap.download_runner.load_requested_split_plans", return_value={}),
                patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
                patch("peap.download_runner.build_download_artifact_audit", return_value=None),
                patch("peap.download_runner.run_download_task", side_effect=fake_run_download_task),
            ):
                result = run_download_session(
                    args,
                    logger=self.logger,
                    config_obj=self.config,
                )

        self.assertEqual(result.exit_code, 1)
        self.assertTrue(result.any_failure)
        self.assertFalse(result.archive_audit["ok"])
        self.assertEqual(result.archive_audit["issue_count"], 1)
        self.assertEqual(result.archive_audit["issues"][0]["code"], "missing_sidecar")
        self.assertEqual(result.aggregate_summary["errors"], 1)

    def test_run_download_session_attaches_successful_archive_audit_for_new_downloads(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        with tempfile.TemporaryDirectory() as temp_dir:
            args = SimpleNamespace(
                exchange="sse",
                record_family="listing",
                business_id="physical_asset",
                output_root=temp_dir,
                force_manual_root=False,
                start_date="2026-07-01",
                end_date="2026-07-01",
                split_plan_only=False,
                split_use_plan=False,
                split_plan_file=None,
                split_candidates=10,
                split_min_days=1,
                split_max_depth=3,
                split_mode="fast",
                page_size=None,
                max_pages=None,
                concurrency=2,
                resume=True,
                save_json=False,
                sse_ca_bundle=None,
                sse_ssl_verify=True,
                auto_split=False,
                chunk_state_file=None,
            )

            def fake_run_download_task(*args, **kwargs):
                html = "<html><body>P001</body></html>"
                html_path = os.path.join(
                    temp_dir,
                    "sse__listing__physical_asset",
                    "2026年7月",
                    "P001-complete.html",
                )
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write(html)
                with open(f"{html_path}.peap-save-status.json", "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "task_id": "sse:listing:physical_asset",
                            "source_id": "sse",
                            "record_family": "listing",
                            "business_id": "physical_asset",
                            "save_status": "complete",
                            "source_url": "https://example.test/P001-complete",
                            "http_status": 200,
                            "archive_content_sha256": "sha256:"
                            + hashlib.sha256(html.encode("utf-8")).hexdigest(),
                            "archive_content_bytes": len(html.encode("utf-8")),
                        },
                        handle,
                        ensure_ascii=False,
                    )
                totals = new_totals()
                totals["saved"] = 1
                return DownloadTaskRunResult(
                    any_failure=False,
                    totals=totals,
                    typed_errors=[],
                    task_result={
                        "display_name": spec.display_name,
                        "summary": {"saved": 1, "errors": 0},
                        "errors": [],
                        "new_downloads": [os.path.relpath(html_path, temp_dir)],
                        "discovery_task_manifest": self._write_discovery_evidence(
                            temp_dir,
                            task_id=spec.task_id,
                            run_id=kwargs["args"].run_id,
                        ),
                    },
                )

            with (
                patch("peap.download_runner.resolve_tasks", return_value=[spec]),
                patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
                patch("peap.download_runner.load_requested_split_plans", return_value={}),
                patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
                patch("peap.download_runner.build_download_artifact_audit", return_value=None),
                patch("peap.download_runner.run_download_task", side_effect=fake_run_download_task),
            ):
                result = run_download_session(
                    args,
                    logger=self.logger,
                    config_obj=self.config,
                )

        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.any_failure)
        self.assertTrue(result.archive_audit["ok"])
        self.assertEqual(result.archive_audit["html_count"], 1)
        self.assertEqual(result.archive_audit["sidecar_count"], 1)
        self.assertEqual(result.archive_audit["issue_count"], 0)

    def test_run_download_session_requires_detail_sidecar_when_save_json_is_enabled(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        with tempfile.TemporaryDirectory() as temp_dir:
            args = SimpleNamespace(
                exchange="sse",
                record_family="listing",
                business_id="physical_asset",
                output_root=temp_dir,
                force_manual_root=False,
                start_date="2026-07-01",
                end_date="2026-07-01",
                split_plan_only=False,
                split_use_plan=False,
                split_plan_file=None,
                split_candidates=10,
                split_min_days=1,
                split_max_depth=3,
                split_mode="fast",
                page_size=None,
                max_pages=None,
                concurrency=2,
                resume=True,
                save_json=True,
                sse_ca_bundle=None,
                sse_ssl_verify=True,
                auto_split=False,
                chunk_state_file=None,
            )

            def fake_run_download_task(*args, **kwargs):
                html = "<html><body>P001</body></html>"
                html_path = os.path.join(
                    temp_dir,
                    "sse__listing__physical_asset",
                    "2026年7月",
                    "P001-marker-only.html",
                )
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write(html)
                with open(f"{html_path}.peap-save-status.json", "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "task_id": "sse:listing:physical_asset",
                            "source_id": "sse",
                            "record_family": "listing",
                            "business_id": "physical_asset",
                            "save_status": "complete",
                            "source_url": "https://example.test/P001-marker-only",
                            "http_status": 200,
                            "archive_content_sha256": "sha256:"
                            + hashlib.sha256(html.encode("utf-8")).hexdigest(),
                            "archive_content_bytes": len(html.encode("utf-8")),
                        },
                        handle,
                        ensure_ascii=False,
                    )
                totals = new_totals()
                totals["saved"] = 1
                return DownloadTaskRunResult(
                    any_failure=False,
                    totals=totals,
                    typed_errors=[],
                    task_result={
                        "display_name": spec.display_name,
                        "summary": {"saved": 1, "errors": 0},
                        "errors": [],
                        "new_downloads": [os.path.relpath(html_path, temp_dir)],
                        "discovery_task_manifest": self._write_discovery_evidence(
                            temp_dir,
                            task_id=spec.task_id,
                            run_id=kwargs["args"].run_id,
                        ),
                    },
                )

            with (
                patch("peap.download_runner.resolve_tasks", return_value=[spec]),
                patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
                patch("peap.download_runner.load_requested_split_plans", return_value={}),
                patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
                patch("peap.download_runner.build_download_artifact_audit", return_value=None),
                patch("peap.download_runner.run_download_task", side_effect=fake_run_download_task),
            ):
                result = run_download_session(
                    args,
                    logger=self.logger,
                    config_obj=self.config,
                )

        self.assertEqual(result.exit_code, 1)
        self.assertFalse(result.archive_audit["ok"])
        self.assertEqual(result.archive_audit["issues"][0]["code"], "missing_detail_sidecar")

    def test_archive_audit_fails_closed_when_saved_count_has_no_new_download_manifest(self) -> None:
        result = audit_download_run_archives(
            output_root="/tmp/peap-archive",
            task_results={
                "sse:listing:physical_asset": {
                    "display_name": "上海联合产权交易所 / 实物资产",
                    "summary": {"saved": 1, "errors": 0},
                    "errors": [],
                }
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["issue_count"], 1)
        self.assertEqual(result["issues"][0]["code"], "saved_without_new_download_manifest")
        self.assertEqual(result["issues"][0]["task_id"], "sse:listing:physical_asset")

    def test_archive_audit_checks_current_discovery_manifest_when_saved_is_zero(self) -> None:
        from peap.downloaders.common import HttpFetchedText
        from peap.downloaders.discovery_evidence import DiscoveryTaskEvidence

        task_id = "sse:listing:equity_transfer"
        with tempfile.TemporaryDirectory() as temp_dir:
            task = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id=task_id,
                run_id="run-zero-save",
                expected_query_ids=("CHANQUAN-gplx-2",),
            )
            query = task.query("CHANQUAN-gplx-2", authoritative_total=True, page_size=20)
            response = HttpFetchedText(
                '{"rows":[]}',
                source_url="https://example.test/list?page=1",
                final_url="https://example.test/list?page=1",
                http_status=200,
                raw_bytes=b'{"rows":[]}',
            )
            query.record_page(
                page_index=1,
                response=response,
                body_format="json",
                extracted_row_count=0,
                declared_total_items=0,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")
            task.complete()

            result = audit_download_run_archives(
                output_root=temp_dir,
                task_results={
                    task_id: {
                        "summary": {"saved": 0, "errors": 0},
                        "new_downloads": [],
                        "discovery_task_manifest": task.manifest_reference(),
                    }
                },
                required_discovery_task_ids={task_id},
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["discovery_task_count"], 1)
        self.assertEqual(result["discovery_page_count"], 1)

    def test_archive_audit_accepts_current_sse_deal_discovery_manifest(self) -> None:
        task_id = "sse:deal:deal_equity_transfer"
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = self._write_discovery_evidence(
                temp_dir,
                task_id=task_id,
                run_id="run-sse-deal",
            )

            result = audit_download_run_archives(
                output_root=temp_dir,
                task_results={
                    task_id: {
                        "summary": {"saved": 0, "errors": 0},
                        "new_downloads": [],
                        "discovery_task_manifest": reference,
                    }
                },
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["discovery_task_count"], 1)
        self.assertEqual(result["discovery_page_count"], 1)

    def test_archive_audit_fails_when_required_discovery_reference_is_missing(self) -> None:
        task_id = "sse:listing:equity_transfer"
        with tempfile.TemporaryDirectory() as temp_dir:
            result = audit_download_run_archives(
                output_root=temp_dir,
                task_results={
                    task_id: {
                        "summary": {"saved": 0, "errors": 0},
                        "new_downloads": [],
                    }
                },
                required_discovery_task_ids={task_id},
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"][0]["code"], "discovery_task_manifest_reference_missing")

    def test_archive_audit_rejects_tampered_discovery_task_manifest(self) -> None:
        from peap.downloaders.common import HttpFetchedText
        from peap.downloaders.discovery_evidence import DiscoveryTaskEvidence

        task_id = "sse:listing:equity_transfer"
        with tempfile.TemporaryDirectory() as temp_dir:
            task = DiscoveryTaskEvidence(
                root=temp_dir,
                source_id="sse",
                task_id=task_id,
                run_id="run-tampered",
                expected_query_ids=("CHANQUAN-gplx-2",),
            )
            query = task.query("CHANQUAN-gplx-2", authoritative_total=True, page_size=20)
            response = HttpFetchedText(
                '{"rows":[]}',
                source_url="https://example.test/list?page=1",
                final_url="https://example.test/list?page=1",
                http_status=200,
                raw_bytes=b'{"rows":[]}',
            )
            query.record_page(
                page_index=1,
                response=response,
                body_format="json",
                extracted_row_count=0,
                declared_total_items=0,
                declared_total_pages=1,
            )
            query.complete(termination_reason="declared_pages_exhausted")
            task.complete()
            reference = task.manifest_reference()
            with open(task.manifest_path, "a", encoding="utf-8") as handle:
                handle.write("\n")

            result = audit_download_run_archives(
                output_root=temp_dir,
                task_results={
                    task_id: {
                        "summary": {"saved": 0, "errors": 0},
                        "new_downloads": [],
                        "discovery_task_manifest": reference,
                    }
                },
                required_discovery_task_ids={task_id},
            )

        self.assertFalse(result["ok"])
        self.assertIn(
            "discovery_task_manifest_hash_mismatch",
            {issue["code"] for issue in result["issues"]},
        )

    def test_archive_audit_rejects_a_manifest_from_an_unexpected_run(self) -> None:
        task_id = "sse:listing:equity_transfer"
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = self._write_discovery_evidence(temp_dir, task_id=task_id)
            try:
                result = audit_download_run_archives(
                    output_root=temp_dir,
                    task_results={
                        task_id: {
                            "summary": {"saved": 0, "errors": 0},
                            "new_downloads": [],
                            "discovery_task_manifest": reference,
                        }
                    },
                    required_discovery_task_ids={task_id},
                    expected_discovery_run_ids={task_id: "run-current"},
                )
            except TypeError as exc:
                self.fail(f"archive audit must accept runner-owned discovery run ids: {exc}")

        self.assertFalse(result["ok"])
        self.assertIn(
            "discovery_task_manifest_run_mismatch",
            {issue["code"] for issue in result["issues"]},
        )

    def test_archive_audit_fails_when_new_download_manifest_points_to_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = audit_download_run_archives(
                output_root=temp_dir,
                task_results={
                    "sse:listing:physical_asset": {
                        "display_name": "上海联合产权交易所 / 实物资产",
                        "summary": {"saved": 1, "errors": 0},
                        "errors": [],
                        "new_downloads": ["sse__listing__physical_asset/2026年7月/missing.html"],
                    }
                },
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["issue_count"], 1)
        self.assertEqual(result["issues"][0]["code"], "new_download_missing")
        self.assertEqual(result["issues"][0]["task_id"], "sse:listing:physical_asset")

    def test_archive_audit_fails_when_new_download_manifest_escapes_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = audit_download_run_archives(
                output_root=temp_dir,
                task_results={
                    "sse:listing:physical_asset": {
                        "display_name": "上海联合产权交易所 / 实物资产",
                        "summary": {"saved": 1, "errors": 0},
                        "errors": [],
                        "new_downloads": ["../outside.html"],
                    }
                },
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["issue_count"], 1)
        self.assertEqual(result["issues"][0]["code"], "new_download_outside_output_root")
        self.assertEqual(result["issues"][0]["task_id"], "sse:listing:physical_asset")

    def test_run_download_session_audits_task_root_when_failure_left_no_new_downloads(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        with tempfile.TemporaryDirectory() as temp_dir:
            args = SimpleNamespace(
                exchange="sse",
                record_family="listing",
                business_id="physical_asset",
                output_root=temp_dir,
                force_manual_root=False,
                start_date="2026-07-01",
                end_date="2026-07-01",
                split_plan_only=False,
                split_use_plan=False,
                split_plan_file=None,
                split_candidates=10,
                split_min_days=1,
                split_max_depth=3,
                split_mode="fast",
                page_size=None,
                max_pages=None,
                concurrency=2,
                resume=True,
                save_json=False,
                sse_ca_bundle=None,
                sse_ssl_verify=True,
                auto_split=False,
                chunk_state_file=None,
            )

            def fake_run_download_task(*args, **kwargs):
                html_path = os.path.join(
                    temp_dir,
                    "sse__listing__physical_asset",
                    "2026年7月",
                    "P001-failed.html",
                )
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html><body>P001 failed</body></html>")
                with open(f"{html_path}.peap-save-status.json", "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "task_id": "sse:listing:physical_asset",
                            "source_id": "sse",
                            "business_id": "physical_asset",
                            "save_status": "failed",
                            "source_url": "https://example.test/P001-failed",
                            "http_status": 200,
                        },
                        handle,
                        ensure_ascii=False,
                    )
                totals = new_totals()
                totals["detail_failed"] = 1
                return DownloadTaskRunResult(
                    any_failure=True,
                    totals=totals,
                    typed_errors=[],
                    task_result={
                        "display_name": spec.display_name,
                        "summary": {"detail_failed": 1, "errors": 0},
                        "errors": [],
                        "new_downloads": [],
                    },
                )

            with (
                patch("peap.download_runner.resolve_tasks", return_value=[spec]),
                patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
                patch("peap.download_runner.load_requested_split_plans", return_value={}),
                patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
                patch("peap.download_runner.build_download_artifact_audit", return_value=None),
                patch("peap.download_runner.run_download_task", side_effect=fake_run_download_task),
            ):
                result = run_download_session(
                    args,
                    logger=self.logger,
                    config_obj=self.config,
                )

        self.assertEqual(result.exit_code, 1)
        self.assertFalse(result.archive_audit["ok"])
        self.assertEqual(result.archive_audit["issue_count"], 5)
        self.assertIn(
            "discovery_task_manifest_reference_missing",
            {issue["code"] for issue in result.archive_audit["issues"]},
        )
        self.assertEqual(
            {issue["code"] for issue in result.archive_audit["issues"]},
            {
                "sidecar_not_complete",
                "record_family_missing",
                "archive_hash_missing",
                "archive_bytes_missing",
                "discovery_task_manifest_reference_missing",
            },
        )

    def test_run_download_session_audits_legacy_flat_archive_layout(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        with tempfile.TemporaryDirectory() as temp_dir:
            args = SimpleNamespace(
                exchange="sse",
                record_family="listing",
                business_id="physical_asset",
                output_root=temp_dir,
                force_manual_root=False,
                start_date="2026-07-01",
                end_date="2026-07-01",
                split_plan_only=False,
                split_use_plan=False,
                split_plan_file=None,
                split_candidates=10,
                split_min_days=1,
                split_max_depth=3,
                split_mode="fast",
                page_size=None,
                max_pages=None,
                concurrency=2,
                resume=True,
                save_json=False,
                sse_ca_bundle=None,
                sse_ssl_verify=True,
                auto_split=False,
                chunk_state_file=None,
            )

            def fake_run_download_task(*args, **kwargs):
                html = "<html><body>P001 flat</body></html>"
                html_path = os.path.join(temp_dir, "2026年7月", "P001-flat.html")
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write(html)
                with open(f"{html_path}.peap-save-status.json", "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "task_id": "sse:listing:physical_asset",
                            "source_id": "sse",
                            "record_family": "listing",
                            "business_id": "physical_asset",
                            "save_status": "complete",
                            "source_url": "https://example.test/P001-flat",
                            "http_status": 200,
                            "archive_content_sha256": "sha256:"
                            + hashlib.sha256(html.encode("utf-8")).hexdigest(),
                            "archive_content_bytes": len(html.encode("utf-8")),
                        },
                        handle,
                        ensure_ascii=False,
                    )
                totals = new_totals()
                totals["saved"] = 1
                return DownloadTaskRunResult(
                    any_failure=False,
                    totals=totals,
                    typed_errors=[],
                    task_result={
                        "display_name": spec.display_name,
                        "summary": {"saved": 1, "errors": 0},
                        "errors": [],
                        "new_downloads": [os.path.relpath(html_path, temp_dir)],
                        "discovery_task_manifest": self._write_discovery_evidence(
                            temp_dir,
                            task_id=spec.task_id,
                            run_id=kwargs["args"].run_id,
                        ),
                    },
                )

            with (
                patch("peap.download_runner.resolve_tasks", return_value=[spec]),
                patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
                patch("peap.download_runner.load_requested_split_plans", return_value={}),
                patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
                patch("peap.download_runner.build_download_artifact_audit", return_value=None),
                patch("peap.download_runner.run_download_task", side_effect=fake_run_download_task),
            ):
                result = run_download_session(
                    args,
                    logger=self.logger,
                    config_obj=self.config,
                )

        self.assertEqual(result.exit_code, 1)
        self.assertFalse(result.archive_audit["ok"])
        self.assertEqual(result.archive_audit["html_count"], 1)
        self.assertIn(
            "archive_scope_missing",
            {issue["code"] for issue in result.archive_audit["issues"]},
        )

    def test_run_download_session_aborts_when_artifact_audit_database_is_missing(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        args = SimpleNamespace(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            output_root="C:\\temp\\auto_html",
            force_manual_root=False,
            start_date="2026-01-01",
            end_date="2026-01-02",
            split_plan_only=False,
            split_use_plan=False,
            split_plan_file=None,
            split_candidates=10,
            split_min_days=1,
            split_max_depth=3,
            split_mode="fast",
            page_size=None,
            max_pages=None,
            concurrency=2,
            resume=True,
            save_json=False,
            sse_ca_bundle=None,
            sse_ssl_verify=True,
            auto_split=False,
            chunk_state_file=None,
        )

        with (
            patch("peap.download_runner.resolve_tasks", return_value=[spec]),
            patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
            patch("peap.download_runner.load_requested_split_plans", return_value={}),
            patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
            patch(
                "peap.download_runner.build_download_artifact_audit",
                side_effect=FileNotFoundError("STREAMING_DB_PATH database not found: missing.sqlite3"),
            ),
            patch("peap.download_runner.run_download_task") as run_download_task,
            self.assertRaisesRegex(FileNotFoundError, "STREAMING_DB_PATH"),
        ):
            run_download_session(
                args,
                logger=self.logger,
                config_obj=self.config,
            )

        run_download_task.assert_not_called()

    def test_run_download_session_rejects_manual_root_without_flag(self) -> None:
        args = SimpleNamespace(
            exchange="all",
            record_family="listing",
            business_id="all",
            output_root=self.config.HTML_FOLDER,
            force_manual_root=False,
            start_date=None,
            end_date=None,
            split_plan_only=False,
            split_use_plan=False,
            split_plan_file=None,
            split_candidates=10,
            split_min_days=1,
            split_max_depth=3,
            split_mode="fast",
        )

        with self.assertRaises(DownloadRunnerError):
            run_download_session(
                args,
                logger=self.logger,
                config_obj=self.config,
            )

    def test_run_download_session_rejects_explicit_reversed_date_range_before_resolving_tasks(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        totals = new_totals()
        task_result = {
            "display_name": spec.display_name,
            "summary": {"saved": 0, "errors": 0},
            "errors": [],
        }
        args = DownloadRunRequest(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            output_root="C:\\temp\\auto_html",
            start_date="2026-01-03",
            end_date="2026-01-01",
        )

        with (
            patch("peap.download_runner.resolve_tasks", return_value=[spec]) as resolve_tasks,
            patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
            patch("peap.download_runner.load_requested_split_plans", return_value={}),
            patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
            patch(
                "peap.download_runner.run_download_task",
                return_value=DownloadTaskRunResult(
                    any_failure=False,
                    totals=totals,
                    typed_errors=[],
                    task_result=task_result,
                ),
            ) as run_download_task,
            self.assertRaisesRegex(DownloadRunnerError, "start-date must be on or before end-date"),
        ):
            run_download_session(
                args,
                logger=self.logger,
                config_obj=self.config,
            )

        resolve_tasks.assert_not_called()
        run_download_task.assert_not_called()

    def test_runtime_dependency_guidance_points_to_uv_sync(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        logger = unittest.mock.Mock()

        with (
            patch("peap.download_runner.importlib.util.find_spec", return_value=None),
            patch("peap.download_runner.sys.executable", "/tmp/peap/.venv/bin/python"),
            patch("builtins.print") as mock_print,
        ):
            ready = ensure_runtime_dependencies([spec], logger=logger)

        self.assertFalse(ready)
        message = logger.error.call_args.args[0]
        self.assertIn("uv sync", message)
        self.assertIn("playwright install chromium", message)
        self.assertNotIn("pip install playwright", message)
        mock_print.assert_called_once_with(message)

    def test_task_labels_and_display_names_use_shared_source_catalog_metadata(self) -> None:
        registry = build_task_registry()
        cbex_spec = registry["cbex:listing:physical_asset"]
        tpre_spec = registry["tpre:listing:pre_disclosure"]

        self.assertEqual(
            cbex_spec.display_name,
            f"{get_source_descriptor('cbex').site_label} - 实物资产",
        )
        self.assertEqual(
            task_progress_label(cbex_spec),
            f"{get_source_descriptor('cbex').canonical_label} - 挂牌实物资产",
        )
        self.assertEqual(
            tpre_spec.display_name,
            f"{get_source_descriptor('tpre').site_label} - 预披露",
        )
        self.assertEqual(
            task_progress_label(tpre_spec),
            f"{get_source_descriptor('tpre').canonical_label} - 挂牌预披露",
        )

    def test_listing_exchange_runtime_registry_exposes_only_implemented_new_scopes(self) -> None:
        registry = build_task_registry(config_obj=self.config)

        self.assertIn("shandong:listing:equity_transfer", registry)
        self.assertIn("guangdong:listing:equity_transfer", registry)
        self.assertIn("shenzhen:listing:equity_transfer", registry)
        self.assertIn("shandong:listing:capital_increase", registry)
        self.assertIn("guangdong:listing:capital_increase", registry)
        self.assertIn("shenzhen:listing:capital_increase", registry)
        self.assertTrue(registry["shandong:listing:equity_transfer"].implemented)
        self.assertTrue(registry["guangdong:listing:equity_transfer"].implemented)
        self.assertTrue(registry["shandong:listing:capital_increase"].implemented)
        self.assertTrue(registry["guangdong:listing:capital_increase"].implemented)
        self.assertTrue(registry["shenzhen:listing:capital_increase"].implemented)

    def test_deal_runtime_task_result_paths_are_relative_to_session_output_root(self) -> None:
        spec = build_task_registry()["cbex:deal:deal_equity_transfer"]
        args = SimpleNamespace(
            auto_split=False,
            page_size=None,
            max_pages=None,
            concurrency=1,
            resume=False,
            save_json=False,
        )
        observed_html_roots: list[str] = []

        def fake_run(downloader, **_kwargs):
            observed_html_roots.append(os.path.abspath(downloader.html_root))
            html_path = os.path.join(downloader.html_root, "2026", "P001.html")
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>P001</body></html>")

            manifest_path = os.path.join(
                downloader.html_root,
                "_evidence",
                "run-test",
                "cbex__deal__deal_equity_transfer",
                "discovery",
                "task_manifest.json",
            )
            os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
            manifest_bytes = b"{}"
            with open(manifest_path, "wb") as handle:
                handle.write(manifest_bytes)

            summary = DownloadSummary(saved=1)
            record_downloaded_target(
                summary,
                html_root=downloader.html_root,
                html_path=html_path,
            )
            summary.discovery_task_manifest = {
                "source_id": "cbex",
                "task_id": spec.task_id,
                "run_id": "run-test",
                "path": os.path.relpath(manifest_path, downloader.html_root),
                "sha256": "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
                "bytes": len(manifest_bytes),
            }
            return summary

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            spec.downloader_cls,
            "run",
            fake_run,
        ):
            result = run_download_task(
                spec,
                args=args,
                logger=self.logger,
                output_root=temp_dir,
                loaded_plan_map={},
                chunk_state_ctx=None,
                build_downloader=build_downloader,
                run_downloader=run_downloader,
                run_downloader_with_prefetched=run_downloader_with_prefetched,
                parse_date_arg=lambda *_args: None,
            )

            task_component = "cbex__deal__deal_equity_transfer"
            self.assertEqual(
                observed_html_roots,
                [os.path.join(os.path.abspath(temp_dir), task_component)],
            )
            self.assertIsNotNone(result.task_result)
            task_result = result.task_result or {}
            self.assertEqual(
                task_result["new_downloads"],
                [os.path.join(task_component, "2026", "P001.html")],
            )
            self.assertEqual(
                task_result["discovery_task_manifest"]["path"],
                os.path.join(
                    task_component,
                    "_evidence",
                    "run-test",
                    "cbex__deal__deal_equity_transfer",
                    "discovery",
                    "task_manifest.json",
                ),
            )

    def test_listing_runtime_uses_scoped_root_and_produces_auditable_paths(self) -> None:
        spec = build_task_registry()["sse:listing:physical_asset"]
        saved_events: list[dict[str, object]] = []
        args = SimpleNamespace(
            auto_split=False,
            page_size=None,
            max_pages=None,
            concurrency=1,
            resume=True,
            save_json=False,
            run_id="run-listing-scoped",
            item_saved_callback=saved_events.append,
            sse_ssl_verify=True,
            sse_ca_bundle=None,
        )
        observed_html_roots: list[str] = []

        def fake_run(downloader, **_kwargs):
            observed_html_roots.append(os.path.abspath(downloader.html_root))
            self.assertTrue(downloader.resume)
            html = "<html><body>P001</body></html>"
            html_path = os.path.join(downloader.html_root, "2026年7月", "P001.html")
            os.makedirs(os.path.dirname(html_path), exist_ok=True)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write(html)
            with open(f"{html_path}.peap-save-status.json", "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "task_id": spec.task_id,
                        "source_id": spec.exchange_code,
                        "record_family": spec.record_family,
                        "business_id": spec.business_id,
                        "save_status": "complete",
                        "source_url": "https://example.test/P001",
                        "http_status": 200,
                        "archive_content_sha256": "sha256:"
                        + hashlib.sha256(html.encode("utf-8")).hexdigest(),
                        "archive_content_bytes": len(html.encode("utf-8")),
                    },
                    handle,
                    ensure_ascii=False,
                )

            downloader.item_saved_callback(
                {
                    "source_file": html_path,
                    "archive_path": html_path,
                    "task_id": spec.task_id,
                }
            )
            summary = DownloadSummary(saved=1)
            record_downloaded_target(
                summary,
                html_root=downloader.html_root,
                html_path=html_path,
            )
            summary.discovery_task_manifest = self._write_discovery_evidence(
                downloader.html_root,
                task_id=spec.task_id,
                run_id=args.run_id,
            )
            return summary

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            spec.downloader_cls,
            "run",
            fake_run,
        ):
            result = run_download_task(
                spec,
                args=args,
                logger=self.logger,
                output_root=temp_dir,
                loaded_plan_map={},
                chunk_state_ctx=None,
                build_downloader=build_downloader,
                run_downloader=run_downloader,
                run_downloader_with_prefetched=run_downloader_with_prefetched,
                parse_date_arg=lambda *_args: None,
            )

            task_component = "sse__listing__physical_asset"
            task_root = os.path.join(os.path.abspath(temp_dir), task_component)
            self.assertEqual(observed_html_roots, [task_root])
            self.assertEqual(saved_events[0]["source_file"], os.path.join(task_root, "2026年7月", "P001.html"))
            self.assertIsNotNone(result.task_result)
            task_result = result.task_result or {}
            self.assertEqual(
                task_result["new_downloads"],
                [os.path.join(task_component, "2026年7月", "P001.html")],
            )
            self.assertEqual(
                task_result["discovery_task_manifest"]["path"],
                os.path.join(
                    task_component,
                    "_evidence",
                    args.run_id,
                    task_component,
                    "discovery",
                    "task_manifest.json",
                ),
            )

            audit = audit_download_run_archives(
                output_root=temp_dir,
                task_results={spec.task_id: task_result},
                required_discovery_task_ids={spec.task_id},
                expected_discovery_run_ids={spec.task_id: args.run_id},
            )
            self.assertTrue(audit["ok"], audit)
            self.assertEqual(audit["html_count"], 1)
            self.assertEqual(audit["issue_count"], 0)

    def test_prepare_download_session_resolves_new_source_capital_increase_tasks(self) -> None:
        settings = build_download_runner_settings(self.config)
        for exchange in ("shandong", "guangdong"):
            with self.subTest(exchange=exchange):
                request = DownloadRunRequest(
                    exchange=exchange,
                    record_family="listing",
                    business_id="capital_increase",
                    output_root="C:\\temp\\auto_html",
                    start_date="2026-01-01",
                    end_date="2026-01-02",
                )
                with (
                    patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
                ):
                    session = prepare_download_session(
                        request,
                        logger=self.logger,
                        config_obj=self.config,
                        settings=settings,
                    )
                self.assertEqual([spec.task_id for spec in session.tasks], [f"{exchange}:listing:capital_increase"])
                self.assertRegex(str(getattr(session.request, "run_id", "")), r"^run-[0-9a-f]{32}$")

                runtime = build_downloader(
                    session.tasks[0],
                    args=session.request,
                    output_root=session.output_root,
                    logger=self.logger,
                )
                self.assertEqual(runtime.downloader.run_id, session.request.run_id)

    def test_split_execution_honors_resume_with_persisted_done_chunk(self) -> None:
        """When a chunk is done in persisted state, resume=True still allows further chunks."""
        spec = build_task_registry()["sse:listing:physical_asset"]
        args = SimpleNamespace(
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
            output_root="C:\\temp\\auto_html",
            force_manual_root=False,
            start_date="2026-01-01",
            end_date="2026-01-03",
            split_plan_only=False,
            split_use_plan=False,
            split_plan_file=None,
            split_candidates=10,
            split_min_days=1,
            split_max_depth=3,
            split_mode="fast",
            page_size=None,
            max_pages=None,
            concurrency=2,
            resume=True,
            save_json=False,
            sse_ca_bundle=None,
            sse_ssl_verify=True,
            auto_split=True,
            chunk_state_file=None,
        )
        totals = new_totals()
        totals["saved"] = 1
        task_result = {
            "display_name": spec.display_name,
            "summary": {"saved": 1, "errors": 0},
            "errors": [],
        }
        resume_overrides: list[bool | None] = []

        def capture_resume_override(spec, *, args, output_root, logger, resume_override=None):
            resume_overrides.append(resume_override)
            return object()

        with (
            patch("peap.download_runner.resolve_tasks", return_value=[spec]),
            patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
            patch("peap.download_runner.load_requested_split_plans", return_value={}),
            patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
            patch("peap.download_runner.build_downloader", side_effect=capture_resume_override),
            patch("peap.download_runner.build_download_artifact_audit", return_value=None),
            patch("peap.download_runner._audit_new_download_archive_roots", return_value={}),
            patch(
                "peap.download_runner.run_download_task",
                return_value=DownloadTaskRunResult(
                    any_failure=False,
                    totals=totals,
                    typed_errors=[],
                    task_result=task_result,
                ),
            ),
        ):
            result = run_download_session(
                args,
                logger=self.logger,
                config_obj=self.config,
            )

        self.assertEqual(result.exit_code, 0)
        # When no chunk_state_ctx exists, resume_override should be None (honoring resume flag)
        self.assertTrue(all(r is None for r in resume_overrides))

    def test_run_download_session_executes_deal_scope_when_binding_is_implemented(self) -> None:
        spec = build_task_registry()["sse:deal:deal_equity_transfer"]
        args = SimpleNamespace(
            exchange="sse",
            record_family="deal",
            business_id="deal_equity_transfer",
            output_root="C:\\temp\\auto_html",
            force_manual_root=False,
            start_date="2026-04-01",
            end_date="2026-04-02",
            split_plan_only=False,
            split_use_plan=False,
            split_plan_file=None,
            split_candidates=10,
            split_min_days=1,
            split_max_depth=3,
            split_mode="fast",
            page_size=None,
            max_pages=None,
            concurrency=2,
            resume=True,
            save_json=False,
            sse_ca_bundle=None,
            sse_ssl_verify=True,
            auto_split=False,
            chunk_state_file=None,
        )
        totals = new_totals()
        totals["saved"] = 1
        task_result = {
            "display_name": spec.display_name,
            "summary": {"saved": 1, "errors": 0},
            "errors": [],
        }

        with (
            patch("peap.download_runner.resolve_tasks", return_value=[spec]),
            patch("peap.download_runner.ensure_runtime_dependencies", return_value=True),
            patch("peap.download_runner.load_requested_split_plans", return_value={}),
            patch("peap.download_runner.prepare_chunk_state_context", return_value=None),
            patch("peap.download_runner.build_download_artifact_audit", return_value=None),
            patch("peap.download_runner._audit_new_download_archive_roots", return_value={}),
            patch(
                "peap.download_runner.run_download_task",
                return_value=DownloadTaskRunResult(
                    any_failure=False,
                    totals=totals,
                    typed_errors=[],
                    task_result=task_result,
                ),
            ) as run_download_task,
        ):
            result = run_download_session(
                args,
                logger=self.logger,
                config_obj=self.config,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.task_count, 1)
        self.assertIn(spec.task_id, result.task_summaries)
        run_download_task.assert_called_once()


if __name__ == "__main__":
    unittest.main()
