from __future__ import annotations

import argparse
import datetime as dt
import os
import tempfile
import types
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

from peap.streaming_daily_pipeline import run_streaming_daily_pipeline
from peap.streaming_models import IngestedRecord, ItemProgressEvent, PostProcessFinding
from peap.streaming_store import StreamingStore


@dataclass(frozen=True)
class _FakeDownloadRunRequest:
    exchange: str = "all"
    project_type: str = "all"
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


class _FakeRunnerWithSkip:
    def __init__(self, *args, **kwargs):
        pass

    def ingest(self, item):
        code = os.path.splitext(os.path.basename(item.source_file))[0].upper()
        if code == "ITEM_A":
            return {
                "state": "skipped",
                "record_id": code,
                "revision_id": 1,
                "project_code": code,
                "error_type": "skip_parse",
                "error_message": "skip-cbex-otc-page",
                "archive_path": "",
            }
        return {
            "state": "ready",
            "record_id": code,
            "revision_id": 1,
            "project_code": code,
            "archive_path": item.source_file,
        }


class _FakeRunnerWithConflict:
    def __init__(self, *args, **kwargs):
        pass

    def ingest(self, item):
        code = os.path.splitext(os.path.basename(item.source_file))[0].upper()
        return {
            "state": "conflict",
            "record_id": code,
            "revision_id": 1,
            "project_code": code,
            "archive_path": item.source_file,
        }


class StreamingDailyPipelineTest(unittest.TestCase):
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
                "sse_ssl_fallback_insecure": True,
                "sse_ca_bundle": None,
            },
        )

    def test_streaming_pipeline_uses_callback_and_updates_counts(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
            concurrency=2,
            page_size=None,
            max_pages=None,
            with_refresh=False,
            no_resume=False,
            save_json=False,
            postprocess_config=None,
            verbose=False,
            streaming_db=None,
            no_auto_export=False,
        )

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            for stem in ("item_a", "item_b"):
                html_path = os.path.join(self.temp_dir.name, f"{stem}.html")
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html></html>")
                request.download_request.item_saved_callback(
                    {
                        "source_file": html_path,
                        "project_code": stem.upper(),
                        "project_name": stem,
                    }
                )
            return _FakeDownloadOneClickRunResult(
                exit_code=0,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=True,
                start="2026-03-20 00:00:00",
                end="2026-03-20 00:01:00",
                duration_sec=60.0,
                aggregate_summary={"saved": 2, "errors": 0},
                task_summaries={},
                errors=[],
            )

        fake_download_oneclick.run_download_oneclick = _fake_run_download_oneclick

        @dataclass(frozen=True)
        class _FakeArtifact:
            file_path: str

        @dataclass(frozen=True)
        class _FakeExportResult:
            export_id: str
            artifacts: list[_FakeArtifact]
            new_records: int = 2
            changed_records: int = 0

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunner),
            patch(
                "peap.streaming_daily_pipeline.run_ready_export",
                return_value=_FakeExportResult(
                    export_id="exp-1",
                    artifacts=[_FakeArtifact(os.path.join(self.temp_dir.name, "a.xlsx"))],
                ),
            ),
        ):
            result = run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.downloaded_count, 2)

    def test_streaming_pipeline_runs_store_maintenance_before_download_bootstrap(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
            concurrency=2,
            page_size=None,
            max_pages=None,
            with_refresh=False,
            no_resume=False,
            save_json=False,
            postprocess_config=None,
            verbose=False,
            streaming_db=None,
            no_auto_export=True,
        )
        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest
        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest
        call_order: list[str] = []

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            call_order.append("download")
            return _FakeDownloadOneClickRunResult(
                exit_code=0,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=True,
                start="2026-03-20 00:00:00",
                end="2026-03-20 00:01:00",
                duration_sec=60.0,
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
            patch(
                "peap.streaming_daily_pipeline.run_streaming_store_maintenance",
                side_effect=lambda store: call_order.append("maintenance"),
            ),
        ):
            result = run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(call_order[:2], ["maintenance", "download"])

    def test_streaming_pipeline_persists_stage_failure_reason_from_payload_errors(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
            concurrency=2,
            page_size=None,
            max_pages=None,
            with_refresh=False,
            no_resume=False,
            save_json=False,
            postprocess_config=None,
            verbose=False,
            streaming_db=None,
            no_auto_export=True,
        )

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            request.stage_callback(
                {
                    "phase_code": "prepare_tasks",
                    "status": "failed",
                    "label": "正在扫描网页",
                    "error_code": "sse_list_api_not_found",
                    "error_details": {"exchange": "sse", "stage": "prepare_tasks"},
                    "error_message": "上交所列表接口 queryAllNew 返回 404，当前扫描已中止",
                    "errors": ["tpre: collect-failed: upstream 500"],
                    "summary_payload": {"errors": ["tpre: collect-failed: upstream 500"]},
                }
            )
            return _FakeDownloadOneClickRunResult(
                exit_code=1,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=True,
                start="2026-03-20 00:00:00",
                end="2026-03-20 00:00:01",
                duration_sec=1.0,
                aggregate_summary={"saved": 0, "errors": 1},
                task_summaries={},
                errors=["tpre: collect-failed: upstream 500"],
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
            result = run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertEqual(result.exit_code, 1)
        store = StreamingStore(result.db_path)
        events = store.list_job_events(result.job_id, limit=20)
        prepare_failed = next(
            event for event in events if event["stage"] == "prepare_tasks" and event["status"] == "failed"
        )
        self.assertEqual(prepare_failed["error_type"], "sse_list_api_not_found")
        self.assertEqual(prepare_failed["error_message"], "上交所列表接口 queryAllNew 返回 404，当前扫描已中止")
        job = store.get_job(result.job_id)
        self.assertEqual(job["summary"]["failure_code"], "sse_list_api_not_found")
        self.assertEqual(job["summary"]["failure_stage"], "prepare_tasks")
        self.assertEqual(job["summary"]["failure_message"], "上交所列表接口 queryAllNew 返回 404，当前扫描已中止")

    def test_streaming_pipeline_persists_generic_collect_failure_code_from_payload(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
            concurrency=2,
            page_size=None,
            max_pages=None,
            with_refresh=False,
            no_resume=False,
            save_json=False,
            postprocess_config=None,
            verbose=False,
            streaming_db=None,
            no_auto_export=True,
        )

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest
        raw_error = "cbex-list-failed: list-api-failed 股权转让 p=1: api-http-521"

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            request.stage_callback(
                {
                    "phase_code": "prepare_tasks",
                    "status": "failed",
                    "label": "扫描失败",
                    "error_code": "cbex_list_failed",
                    "error_details": {
                        "exchange": "cbex",
                        "stage": "prepare_tasks",
                        "failure_kind": "list",
                        "raw_reason": "list-api-failed 股权转让 p=1: api-http-521",
                    },
                    "error_message": raw_error,
                    "errors": [raw_error],
                    "summary_payload": {"errors": [raw_error]},
                }
            )
            return _FakeDownloadOneClickRunResult(
                exit_code=1,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=True,
                start="2026-03-20 00:00:00",
                end="2026-03-20 00:00:01",
                duration_sec=1.0,
                aggregate_summary={"saved": 0, "errors": 1},
                task_summaries={},
                errors=[raw_error],
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
            result = run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertEqual(result.exit_code, 1)
        store = StreamingStore(result.db_path)
        events = store.list_job_events(result.job_id, limit=20)
        prepare_failed = next(
            event for event in events if event["stage"] == "prepare_tasks" and event["status"] == "failed"
        )
        self.assertEqual(prepare_failed["error_type"], "cbex_list_failed")
        self.assertEqual(prepare_failed["error_message"], raw_error)
        job = store.get_job(result.job_id)
        self.assertEqual(job["summary"]["failure_code"], "cbex_list_failed")
        self.assertEqual(job["summary"]["failure_stage"], "prepare_tasks")
        self.assertEqual(job["summary"]["failure_message"], raw_error)

    def test_streaming_pipeline_defaults_to_today_only(self) -> None:
        args = argparse.Namespace(
            start_date="",
            end_date="",
            exchange="all",
            project_type="all",
            concurrency=2,
            page_size=None,
            max_pages=None,
            with_refresh=False,
            no_resume=False,
            save_json=False,
            postprocess_config=None,
            verbose=False,
            streaming_db=None,
            no_auto_export=True,
        )

        captured: dict[str, object] = {}

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            captured["start_date"] = request.download_request.start_date
            captured["end_date"] = request.download_request.end_date
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
            patch("peap.streaming_daily_pipeline.today_local", return_value=dt.date(2026, 3, 22)),
        ):
            result = run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertEqual(result.start_date, "2026-03-22")
        self.assertEqual(result.end_date, "2026-03-22")
        self.assertEqual(captured["start_date"], "2026-03-22")
        self.assertEqual(captured["end_date"], "2026-03-22")

    def test_streaming_pipeline_does_not_count_skipped_items_as_exceptions(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
            concurrency=2,
            page_size=None,
            max_pages=None,
            with_refresh=False,
            no_resume=False,
            save_json=False,
            postprocess_config=None,
            verbose=False,
            streaming_db=None,
            no_auto_export=True,
        )

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            for stem in ("item_a", "item_b"):
                html_path = os.path.join(self.temp_dir.name, f"{stem}.html")
                with open(html_path, "w", encoding="utf-8") as handle:
                    handle.write("<html></html>")
                request.download_request.item_saved_callback(
                    {
                        "source_file": html_path,
                        "project_code": stem.upper(),
                        "project_name": stem,
                    }
                )
            return _FakeDownloadOneClickRunResult(
                exit_code=0,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=True,
                start="2026-03-20 00:00:00",
                end="2026-03-20 00:01:00",
                duration_sec=60.0,
                aggregate_summary={"saved": 2, "errors": 0},
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
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunnerWithSkip),
        ):
            result = run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.downloaded_count, 2)
        self.assertEqual(result.persisted_count, 1)
        self.assertEqual(result.exception_count, 0)
        store = StreamingStore(result.db_path)
        job = store.get_job(result.job_id)
        self.assertEqual(job["status"], "success")
        events = store.list_job_events(result.job_id, limit=20)
        self.assertTrue(any(event["status"] == "skipped" for event in events))

    def test_streaming_pipeline_uses_archive_root_and_small_window_default_max_pages(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
            concurrency=2,
            page_size=None,
            max_pages=None,
            with_refresh=False,
            no_resume=False,
            save_json=False,
            postprocess_config=None,
            verbose=False,
            streaming_db=None,
            no_auto_export=True,
        )
        captured: dict[str, object] = {}

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            captured["output_root"] = request.download_request.output_root
            captured["max_pages"] = request.download_request.max_pages
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
        self.config.ARCHIVE_ROOT = os.path.join(self.temp_dir.name, "submission")

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
            run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertEqual(captured["output_root"], self.config.ARCHIVE_ROOT)
        self.assertEqual(captured["max_pages"], 10)

    def test_streaming_pipeline_passes_existing_project_codes_into_oneclick_request(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming.sqlite3")
        store = StreamingStore(db_path)
        existing_html = os.path.join(self.temp_dir.name, "existing.html")
        with open(existing_html, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")
        store.upsert_record(
            IngestedRecord(
                record_id="existing-rec",
                revision_hash="hash-existing",
                project_code="G32026BJ1000003",
                project_name="已有项目",
                project_type="股权转让",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=existing_html,
                archive_path=existing_html,
                parser_payload={"项目编号": "G32026BJ1000003", "项目名称": "已有项目", "项目类型": "股权转让"},
                postprocess_payload={"项目编号": "G32026BJ1000003", "项目名称": "已有项目", "项目类型": "股权转让", "类型": "国资"},
                findings=[],
            )
        )
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
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
        captured: dict[str, object] = {}

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            captured["existing_project_codes"] = request.existing_project_codes
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
            run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertEqual(captured["existing_project_codes"], frozenset({"G32026BJ1000003"}))

    def test_streaming_pipeline_passes_existing_candidate_tokens_into_oneclick_request(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming-identities.sqlite3")
        store = StreamingStore(db_path)
        existing_html = os.path.join(self.temp_dir.name, "existing-token.html")
        with open(existing_html, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")
        store.upsert_record(
            IngestedRecord(
                record_id="existing-token-rec",
                revision_hash="hash-existing-token",
                project_code="G32026BJ1000008",
                project_name="已有候选身份项目",
                project_type="股权转让",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=existing_html,
                archive_path=existing_html,
                parser_payload={"项目编号": "G32026BJ1000008", "项目名称": "已有候选身份项目"},
                postprocess_payload={"项目编号": "G32026BJ1000008", "项目名称": "已有候选身份项目", "项目类型": "股权转让"},
                findings=[],
            )
        )
        job_id = store.create_job("one_click")
        store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={
                    "source_file": existing_html,
                    "project_code": "G32026BJ1000008",
                    "page_url": "https://example.test/detail/8",
                    "project_id": "CQ008",
                },
            )
        )

        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
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
        captured: dict[str, object] = {}

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
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
            run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertIn("project_code:G32026BJ1000008", captured["existing_candidate_tokens"])
        self.assertIn("page_url:https://example.test/detail/8", captured["existing_candidate_tokens"])
        self.assertIn("project_id:CQ008", captured["existing_candidate_tokens"])

    def test_streaming_pipeline_normalizes_legacy_pending_mapping_before_collecting_request_context(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming-normalize-legacy.sqlite3")
        store = StreamingStore(db_path)
        source_file = os.path.join(self.temp_dir.name, "legacy-normalize.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")
        store.upsert_record(
            IngestedRecord(
                record_id="legacy-normalize-record",
                revision_hash="hash-legacy-normalize",
                project_code="G32026BJ1999001",
                project_name="历史未知类型项目",
                project_type="",
                exchange="beijing",
                listing_date="2026-03-21",
                state="ready",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": "G32026BJ1999001", "项目名称": "历史未知类型项目"},
                postprocess_payload={"项目编号": "G32026BJ1999001", "项目名称": "历史未知类型项目"},
                findings=[
                    PostProcessFinding(
                        severity="warn",
                        type="project_type_unknown",
                        message="项目类型无法识别",
                    )
                ],
            )
        )

        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
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
        captured: dict[str, object] = {}

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            live_store = StreamingStore(db_path)
            captured["pending_mapping_count"] = live_store.count_pending_mappings()
            captured["pending_records"] = live_store.iter_latest_records(states=["pending_mapping"])
            captured["ready_records"] = live_store.iter_latest_records(states=["ready"])
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
            run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertEqual(captured["pending_mapping_count"], 1)
        self.assertEqual(len(captured["pending_records"]), 1)
        self.assertEqual(captured["pending_records"][0]["record_id"], "legacy-normalize-record")
        self.assertEqual(captured["ready_records"], [])

    def test_streaming_pipeline_does_not_pass_failed_only_candidate_tokens_when_filtering_ready_states(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming-failed-identities.sqlite3")
        store = StreamingStore(db_path)
        failed_html = os.path.join(self.temp_dir.name, "failed-token.html")
        with open(failed_html, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")
        store.upsert_failed_record(
            project_code="FAILED-PIPELINE-001",
            source_file=failed_html,
            state="parse_failed",
            error_type="parse_failed",
            error_message="boom",
            payload={"项目编号": "FAILED-PIPELINE-001"},
        )
        job_id = store.create_job("one_click")
        store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={
                    "source_file": failed_html,
                    "project_code": "FAILED-PIPELINE-001",
                    "page_url": "https://example.test/detail/failed-pipeline",
                    "project_id": "CQFAILEDPIPELINE001",
                },
            )
        )

        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
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
        captured: dict[str, object] = {}

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
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
            run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertNotIn("project_code:FAILED-PIPELINE-001", captured["existing_candidate_tokens"])
        self.assertNotIn("page_url:https://example.test/detail/failed-pipeline", captured["existing_candidate_tokens"])
        self.assertNotIn("project_id:CQFAILEDPIPELINE001", captured["existing_candidate_tokens"])

    def test_streaming_pipeline_does_not_pass_blank_code_failed_candidate_tokens_when_filtering_ready_states(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming-failed-blank-code.sqlite3")
        store = StreamingStore(db_path)
        failed_html = os.path.join(self.temp_dir.name, "failed-blank-code-token.html")
        with open(failed_html, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")
        store.upsert_failed_record(
            project_code="",
            source_file=failed_html,
            state="parse_failed",
            error_type="parse_failed",
            error_message="boom",
            payload={},
        )
        job_id = store.create_job("one_click")
        store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                payload={
                    "source_file": failed_html,
                    "project_code": "",
                    "page_url": "https://example.test/detail/failed-blank-code",
                    "project_id": "CQFAILEDBLANKCODE001",
                },
            )
        )

        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
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
        captured: dict[str, object] = {}

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
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
            run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertNotIn("page_url:https://example.test/detail/failed-blank-code", captured["existing_candidate_tokens"])
        self.assertNotIn("project_id:CQFAILEDBLANKCODE001", captured["existing_candidate_tokens"])

    def test_streaming_pipeline_does_not_count_conflict_as_exception(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
            concurrency=2,
            page_size=None,
            max_pages=None,
            with_refresh=False,
            no_resume=False,
            save_json=False,
            postprocess_config=None,
            verbose=False,
            streaming_db=None,
            no_auto_export=True,
        )

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            html_path = os.path.join(self.temp_dir.name, "item_a.html")
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            request.download_request.item_saved_callback(
                {
                    "source_file": html_path,
                    "project_code": "ITEM_A",
                    "project_name": "item_a",
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
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunnerWithConflict),
        ):
            result = run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertEqual(result.exception_count, 0)
        self.assertEqual(result.persisted_count, 1)

    def test_streaming_pipeline_rejects_empty_job_id_before_starting_download(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
            project_type="all",
            concurrency=2,
            page_size=None,
            max_pages=None,
            with_refresh=False,
            no_resume=False,
            save_json=False,
            postprocess_config=None,
            verbose=False,
            streaming_db=None,
            no_auto_export=True,
        )
        callback_job_ids: list[str] = []

        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _unexpected_run_download_oneclick(request, *, config_obj, emit_console):
            raise AssertionError("download should not start when job creation fails")

        fake_download_oneclick.run_download_oneclick = _unexpected_run_download_oneclick

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunner),
            patch("peap.streaming_daily_pipeline.StreamingStore.create_job", return_value=""),
        ):
            with self.assertRaisesRegex(RuntimeError, "job_id"):
                run_streaming_daily_pipeline(
                    args,
                    config_obj=self.config,
                    emit_console=False,
                    job_created_callback=lambda job_id, db_path: callback_job_ids.append(job_id),
                )

        self.assertEqual(callback_job_ids, [])


if __name__ == "__main__":
    unittest.main()
