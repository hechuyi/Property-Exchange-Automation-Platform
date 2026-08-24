from __future__ import annotations

import argparse
import os
import tempfile
import types
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

from peap.streaming_daily_pipeline import run_streaming_daily_pipeline
from peap.streaming_models import IngestedRecord, PostProcessFinding
from peap.streaming_store import StreamingStore


@dataclass(frozen=True)
class _FakeDownloadRunRequest:
    exchange: str = "all"
    record_family: str = "listing"
    business_id: str = "all"
    list_tasks: bool = False
    output_root: str = ""
    force_manual_root: bool = False
    start_date: str | None = None
    end_date: str | None = None
    page_size: int | None = None
    max_pages: int | None = None
    concurrency: int = 1
    resume: bool = True
    save_json: bool = False
    sse_ssl_verify: bool = True
    sse_ca_bundle: str | None = None
    log_dir: str = ""
    log_file: str | None = None
    verbose: bool = False
    auto_split: bool = False
    split_candidates: int = 0
    split_min_days: int = 0
    split_max_depth: int = 0
    split_plan_only: bool = False
    split_plan_file: str | None = None
    split_use_plan: bool = False
    split_mode: str = "fast"
    chunk_state_file: str | None = None
    item_saved_callback: object = None
    task_progress_callback: object = None


@dataclass(frozen=True)
class _FakeDownloadOneClickRequest:
    download_request: _FakeDownloadRunRequest
    plan_file: str
    keep_plan: bool = False
    with_refresh: bool = False
    stage_callback: object = None
    existing_project_codes: frozenset[str] | None = None
    existing_candidate_tokens: frozenset[str] | None = None


@dataclass
class _FakeDownloadOneClickRunResult:
    exit_code: int
    log_file: str
    plan_file: str
    plan_file_exists: bool
    plan_file_removed: bool
    start: str
    end: str
    duration_sec: float
    aggregate_summary: dict[str, int]
    task_summaries: dict[str, dict]
    errors: list[str]
    stages: list[object] = field(default_factory=list)


class _FakeRunner:
    def __init__(self, *args, **kwargs):
        pass

    def ingest(self, item):
        code = os.path.splitext(os.path.basename(item.source_file))[0].upper()
        return {
            "state": "ready",
            "record_id": code,
            "revision_id": 1,
            "project_code": code,
            "archive_path": item.source_file,
        }


class _FakeRunnerWithPendingReview:
    def __init__(self, *args, **kwargs):
        pass

    def ingest(self, item):
        code = os.path.splitext(os.path.basename(item.source_file))[0].upper()
        return {
            "state": "pending_review",
            "record_id": code,
            "revision_id": 1,
            "project_code": code,
            "archive_path": item.source_file,
        }


class _FakeRunnerWithPendingMapping:
    def __init__(self, *args, **kwargs):
        pass

    def ingest(self, item):
        code = os.path.splitext(os.path.basename(item.source_file))[0].upper()
        return {
            "state": "pending_mapping",
            "record_id": code,
            "revision_id": 1,
            "project_code": code,
            "archive_path": item.source_file,
        }


class _FakeRunnerWithMappingConflict:
    def __init__(self, *args, **kwargs):
        pass

    def ingest(self, item):
        code = os.path.splitext(os.path.basename(item.source_file))[0].upper()
        return {
            "state": "mapping_conflict",
            "record_id": code,
            "revision_id": 1,
            "project_code": code,
            "archive_path": item.source_file,
        }


class StreamingPipelineReviewStatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = SimpleNamespace(
            LOG_DIR=self.temp_dir.name,
            DATA_ROOT=self.temp_dir.name,
            OUTPUT_EXCEL_DIR=os.path.join(self.temp_dir.name, "exports"),
            LOG_LEVEL="INFO",
            LOG_TO_FILE=False,
            DOWNLOADER_DEFAULTS={
                "concurrency": 2,
                "resume": True,
                "save_json": False,
                "auto_split": True,
                "split_candidates": 10,
                "split_min_days": 1,
                "split_max_depth": 3,
                "split_mode": "fast",
                "sse_ssl_verify": True,
                "sse_ca_bundle": None,
            },
        )

    def _args(self, *, db_path: str | None = None) -> argparse.Namespace:
        return argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
            record_family="listing",
            business_id="all",
            concurrency=2,
            page_size=None,
            max_pages=None,
            with_refresh=False,
            no_resume=False,
            save_json=False,
            postprocess_config=None,
            verbose=False,
            streaming_db=db_path,
            no_auto_export=True,
        )

    def test_streaming_pipeline_normalizes_legacy_project_type_unknown_to_pending_review(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming-review-normalize.sqlite3")
        store = StreamingStore(db_path, auto_migrate=True)
        source_file = os.path.join(self.temp_dir.name, "legacy-review.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")
        store.upsert_record(
            IngestedRecord(
                record_id="legacy-review-record",
                revision_hash="hash-legacy-review",
                project_code="G32026BJ1999001",
                project_name="历史未知业务项目",
                project_type="",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026BJ1999001", "项目名称": "历史未知业务项目"},
                postprocess_payload={"项目编号": "G32026BJ1999001", "项目名称": "历史未知业务项目"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="project_type_unknown",
                        message="项目类型无法识别",
                    )
                ],
            )
        )

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            return _FakeDownloadOneClickRunResult(
                exit_code=0,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=False,
                start=str(request.download_request.start_date),
                end=str(request.download_request.end_date),
                duration_sec=0.1,
                aggregate_summary={"saved": 0, "errors": 0},
                task_summaries={},
                errors=[],
            )

        fake_download_oneclick.run_download_oneclick = _fake_run_download_oneclick

        with patch.dict(
            "sys.modules",
            {
                "peap.download_runner": fake_download_runner,
                "peap.download_oneclick": fake_download_oneclick,
            },
        ):
            run_streaming_daily_pipeline(self._args(db_path=db_path), config_obj=self.config, emit_console=False)

        live_store = StreamingStore(db_path, auto_migrate=True)
        pending_review = live_store.iter_latest_records(states=["pending_review"])
        self.assertEqual(len(pending_review), 1)
        self.assertEqual(pending_review[0]["record_id"], "legacy-review-record")
        self.assertEqual(live_store.iter_latest_records(states=["pending_mapping"]), [])
        self.assertEqual(live_store.iter_latest_records(states=["ready"]), [])

    def test_streaming_pipeline_counts_pending_review_as_persisted_work(self) -> None:
        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            html_path = os.path.join(self.temp_dir.name, "item_pending_review.html")
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            request.download_request.item_saved_callback(
                {
                    "source_file": html_path,
                    "project_code": "ITEM_PENDING_REVIEW",
                    "project_name": "item_pending_review",
                }
            )
            return _FakeDownloadOneClickRunResult(
                exit_code=0,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=False,
                start="2026-03-20",
                end="2026-03-21",
                duration_sec=0.1,
                aggregate_summary={"saved": 1, "errors": 0},
                task_summaries={},
                errors=[],
            )

        fake_download_oneclick.run_download_oneclick = _fake_run_download_oneclick

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunnerWithPendingReview),
        ):
            result = run_streaming_daily_pipeline(self._args(), config_obj=self.config, emit_console=False)

        self.assertEqual(result.persisted_count, 1)
        self.assertEqual(result.exception_count, 0)

        store = StreamingStore(result.db_path, auto_migrate=True)
        job = store.get_job(result.job_id)
        self.assertEqual(job["persisted_count"], 1)
        self.assertEqual(job["exception_count"], 0)

    def test_streaming_pipeline_counts_mapping_conflict_as_persisted_work(self) -> None:
        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            html_path = os.path.join(self.temp_dir.name, "item_conflict.html")
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            request.download_request.item_saved_callback(
                {
                    "source_file": html_path,
                    "project_code": "ITEM_CONFLICT",
                    "project_name": "item_conflict",
                }
            )
            return _FakeDownloadOneClickRunResult(
                exit_code=0,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=False,
                start="2026-03-20",
                end="2026-03-21",
                duration_sec=0.1,
                aggregate_summary={"saved": 1, "errors": 0},
                task_summaries={},
                errors=[],
            )

        fake_download_oneclick.run_download_oneclick = _fake_run_download_oneclick

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunnerWithMappingConflict),
        ):
            result = run_streaming_daily_pipeline(self._args(), config_obj=self.config, emit_console=False)

        self.assertEqual(result.persisted_count, 1)
        self.assertEqual(result.exception_count, 0)

        store = StreamingStore(result.db_path, auto_migrate=True)
        job = store.get_job(result.job_id)
        self.assertEqual(job["persisted_count"], 1)
        self.assertEqual(job["exception_count"], 0)

    def test_streaming_pipeline_counts_pending_mapping_as_persisted_work(self) -> None:
        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            html_path = os.path.join(self.temp_dir.name, "item_pending.html")
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            request.download_request.item_saved_callback(
                {
                    "source_file": html_path,
                    "project_code": "ITEM_PENDING",
                    "project_name": "item_pending",
                }
            )
            return _FakeDownloadOneClickRunResult(
                exit_code=0,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=False,
                start="2026-03-20",
                end="2026-03-21",
                duration_sec=0.1,
                aggregate_summary={"saved": 1, "errors": 0},
                task_summaries={},
                errors=[],
            )

        fake_download_oneclick.run_download_oneclick = _fake_run_download_oneclick

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunnerWithPendingMapping),
        ):
            result = run_streaming_daily_pipeline(self._args(), config_obj=self.config, emit_console=False)

        self.assertEqual(result.persisted_count, 1)
        self.assertEqual(result.exception_count, 0)

        store = StreamingStore(result.db_path, auto_migrate=True)
        job = store.get_job(result.job_id)
        self.assertEqual(job["persisted_count"], 1)
        self.assertEqual(job["exception_count"], 0)

    def test_streaming_pipeline_includes_pending_review_in_dedupe_lookup(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming-review-dedupe.sqlite3")
        store = StreamingStore(db_path, auto_migrate=True)
        review_html = os.path.join(self.temp_dir.name, "review-record.html")
        with open(review_html, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")
        store.upsert_record(
            IngestedRecord(
                record_id="review-record",
                revision_hash="hash-review",
                project_code="G32026BJ1000888",
                project_name="待复核项目",
                project_type="",
                exchange="beijing",
                listing_date="2026-03-21",
                state="pending_review",
                source_file=review_html,
                archive_path=review_html,
                parser_payload={"项目编号": "G32026BJ1000888", "项目名称": "待复核项目"},
                postprocess_payload={"项目编号": "G32026BJ1000888", "项目名称": "待复核项目"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="business_resolution_required",
                        message="业务类型未识别",
                        evidence={"raw_business_label": "未知业务"},
                    )
                ],
                source_identity={
                    "record_family": "listing",
                    "candidate_tokens": ["project_code:G32026BJ1000888"],
                },
            )
        )

        captured: dict[str, object] = {}
        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            captured["existing_project_codes"] = request.existing_project_codes
            captured["existing_candidate_tokens"] = request.existing_candidate_tokens
            return _FakeDownloadOneClickRunResult(
                exit_code=0,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=False,
                start=str(request.download_request.start_date),
                end=str(request.download_request.end_date),
                duration_sec=0.1,
                aggregate_summary={"saved": 0, "errors": 0},
                task_summaries={},
                errors=[],
            )

        fake_download_oneclick.run_download_oneclick = _fake_run_download_oneclick

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunner),
        ):
            run_streaming_daily_pipeline(self._args(db_path=db_path), config_obj=self.config, emit_console=False)

        self.assertIn("G32026BJ1000888", captured["existing_project_codes"])
        self.assertIn("project_code:G32026BJ1000888", captured["existing_candidate_tokens"])

    def test_streaming_pipeline_includes_mapping_conflict_in_dedupe_lookup(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming-mapping-conflict.sqlite3")
        store = StreamingStore(db_path, auto_migrate=True)
        conflict_html = os.path.join(self.temp_dir.name, "conflict-record.html")
        with open(conflict_html, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")
        store.upsert_record(
            IngestedRecord(
                record_id="conflict-record",
                revision_hash="hash-conflict",
                project_code="G32026BJ1000999",
                project_name="映射冲突项目",
                project_type="股权转让",
                exchange="beijing",
                listing_date="2026-03-21",
                state="mapping_conflict",
                source_file=conflict_html,
                archive_path=conflict_html,
                parser_payload={"项目编号": "G32026BJ1000999", "项目名称": "映射冲突项目"},
                postprocess_payload={"项目编号": "G32026BJ1000999", "项目名称": "映射冲突项目", "项目类型": "股权转让"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_conflict",
                        message="conflicting mapping candidates",
                    )
                ],
            )
        )

        captured: dict[str, object] = {}
        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            captured["existing_project_codes"] = request.existing_project_codes
            captured["existing_candidate_tokens"] = request.existing_candidate_tokens
            return _FakeDownloadOneClickRunResult(
                exit_code=0,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=False,
                start=str(request.download_request.start_date),
                end=str(request.download_request.end_date),
                duration_sec=0.1,
                aggregate_summary={"saved": 0, "errors": 0},
                task_summaries={},
                errors=[],
            )

        fake_download_oneclick.run_download_oneclick = _fake_run_download_oneclick

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunner),
        ):
            run_streaming_daily_pipeline(self._args(db_path=db_path), config_obj=self.config, emit_console=False)

        self.assertIn("G32026BJ1000999", captured["existing_project_codes"])
        self.assertIn("project_code:G32026BJ1000999", captured["existing_candidate_tokens"])

    def test_streaming_pipeline_includes_pending_mapping_in_dedupe_lookup(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming-pending-mapping.sqlite3")
        store = StreamingStore(db_path, auto_migrate=True)
        pending_html = os.path.join(self.temp_dir.name, "pending-record.html")
        with open(pending_html, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")
        store.upsert_record(
            IngestedRecord(
                record_id="pending-record",
                revision_hash="hash-pending",
                project_code="G32026BJ1000888",
                project_name="待补映射项目",
                project_type="股权转让",
                exchange="beijing",
                listing_date="2026-03-21",
                state="pending_mapping",
                source_file=pending_html,
                archive_path=pending_html,
                parser_payload={"项目编号": "G32026BJ1000888", "项目名称": "待补映射项目"},
                postprocess_payload={"项目编号": "G32026BJ1000888", "项目名称": "待补映射项目", "项目类型": "股权转让"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="mapping_missing",
                        message="missing mapping",
                    )
                ],
            )
        )

        captured: dict[str, object] = {}
        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            captured["existing_project_codes"] = request.existing_project_codes
            captured["existing_candidate_tokens"] = request.existing_candidate_tokens
            return _FakeDownloadOneClickRunResult(
                exit_code=0,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=False,
                start=str(request.download_request.start_date),
                end=str(request.download_request.end_date),
                duration_sec=0.1,
                aggregate_summary={"saved": 0, "errors": 0},
                task_summaries={},
                errors=[],
            )

        fake_download_oneclick.run_download_oneclick = _fake_run_download_oneclick

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunner),
        ):
            run_streaming_daily_pipeline(self._args(db_path=db_path), config_obj=self.config, emit_console=False)

        self.assertIn("G32026BJ1000888", captured["existing_project_codes"])
        self.assertIn("project_code:G32026BJ1000888", captured["existing_candidate_tokens"])
