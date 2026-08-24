from __future__ import annotations

import argparse
import json
import os
import tempfile
import types
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import Mock, patch

from peap.download_errors import DownloadError
from peap.streaming_daily_pipeline import (
    _download_archive_audit_summary,
    _download_failure_summary_fields,
    _failure_summary_fields,
    _load_rules_config,
    _warning_summary_fields,
    run_streaming_daily_pipeline,
)
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


class StreamingDailyPipelineSmokeTest(unittest.TestCase):
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

    def test_load_rules_config_surfaces_invalid_explicit_config_instead_of_returning_empty_rules(self) -> None:
        config_path = os.path.join(self.temp_dir.name, "bad-postprocess.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write("{")

        with self.assertRaises(json.JSONDecodeError):
            _load_rules_config(config_path)

    def test_load_rules_config_surfaces_missing_explicit_config_instead_of_returning_empty_rules(self) -> None:
        config_path = os.path.join(self.temp_dir.name, "missing-postprocess.json")

        with self.assertRaises(FileNotFoundError):
            _load_rules_config(config_path)

    def test_load_rules_config_accepts_rules_only_config_without_requiring_input_dir(self) -> None:
        config_path = os.path.join(self.temp_dir.name, "rules-only-postprocess.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "rules": {
                        "R006_derive_listing_times": {
                            "enabled": True,
                            "priority": 60,
                        }
                    }
                },
                handle,
            )

        rules_config = _load_rules_config(config_path)

        self.assertIn("R006_derive_listing_times", rules_config)
        self.assertEqual(
            rules_config["R006_derive_listing_times"],
            {"enabled": True, "priority": 60, "params": {}},
        )
        self.assertIn("R001_group_mapping_fill", rules_config)

    def test_load_rules_config_surfaces_invalid_rules_shape_instead_of_rules_fallback(self) -> None:
        config_path = os.path.join(self.temp_dir.name, "bad-postprocess-shape.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "input_dir": self.temp_dir.name,
                    "rules": "not-a-rules-object",
                },
                handle,
            )

        with self.assertRaisesRegex(ValueError, "rules must be an object or array"):
            _load_rules_config(config_path)

    def test_load_rules_config_rejects_non_mapping_loader_contract_result(self) -> None:
        config_path = os.path.join(self.temp_dir.name, "postprocess.json")

        with (
            patch(
                "peap_postprocess.postprocess_engine.config.load_rules_config",
                return_value=[],
            ),
            self.assertRaisesRegex(TypeError, "rules_config must be a mapping"),
        ):
            _load_rules_config(config_path)

    def _patched_pipeline_modules(self):
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
        return fake_download_runner, fake_download_oneclick

    def test_streaming_pipeline_smoke_uses_callback_and_updates_counts(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
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
            streaming_db=None,
            no_auto_export=False,
        )
        fake_download_runner, fake_download_oneclick = self._patched_pipeline_modules()

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

    def test_auto_export_uses_requested_export_mode_full_contract(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
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
            streaming_db=None,
            no_auto_export=False,
        )
        fake_download_runner, fake_download_oneclick = self._patched_pipeline_modules()
        captured: dict[str, object] = {}

        @dataclass(frozen=True)
        class _FakeExportResult:
            export_id: str
            artifacts: list[object]
            new_records: int = 0
            changed_records: int = 0

        def _capture_export(_store, request):
            captured["request"] = request
            return _FakeExportResult(export_id="exp-full", artifacts=[])

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunner),
            patch("peap.streaming_daily_pipeline.run_ready_export", side_effect=_capture_export),
        ):
            result = run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        request = captured["request"]
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(request.requested_export_mode, "full")
        self.assertFalse(hasattr(request, "mode"))

    def test_auto_export_empty_artifacts_preserves_field_missing_blocking_context(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming.sqlite3")
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
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
            no_auto_export=False,
        )
        fake_download_runner, fake_download_oneclick = self._patched_pipeline_modules()

        @dataclass(frozen=True)
        class _FakeExportResult:
            export_id: str
            artifacts: list[object]
            field_missing_blocked_records: int
            field_missing_diagnostics: list[dict[str, object]]

        field_missing_diagnostics = [
            {
                "record_id": "rec-missing-export-field",
                "failure_code": "export_field_missing",
                "missing_fields": ["listing_price"],
            }
        ]

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
                    export_id="exp-field-missing",
                    artifacts=[],
                    field_missing_blocked_records=1,
                    field_missing_diagnostics=field_missing_diagnostics,
                ),
            ),
        ):
            result = run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        store = StreamingStore(db_path)
        job = store.get_job(result.job_id)
        events = store.list_job_events(result.job_id, limit=100)
        export_event = next(event for event in events if event["stage"] == "exporting" and event["status"] != "running")

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(job["status"], "success_with_warnings")
        self.assertEqual(job["summary"]["warning_code"], "field_missing_blocked_records")
        self.assertEqual(job["summary"]["field_missing_blocked_records"], 1)
        self.assertEqual(job["summary"]["field_missing_diagnostics"], field_missing_diagnostics)
        self.assertEqual(export_event["status"], "warning")
        self.assertEqual(export_event["payload"]["field_missing_blocked_records"], 1)
        self.assertEqual(export_event["payload"]["field_missing_diagnostics"], field_missing_diagnostics)

    def test_streaming_pipeline_smoke_creates_job_when_no_job_id(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
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
            streaming_db=None,
            no_auto_export=True,
        )
        fake_download_runner, fake_download_oneclick = self._patched_pipeline_modules()
        captured: dict[str, str] = {}

        @dataclass(frozen=True)
        class _FakeArtifact:
            file_path: str

        @dataclass(frozen=True)
        class _FakeExportResult:
            export_id: str
            artifacts: list[_FakeArtifact]
            new_records: int = 2
            changed_records: int = 0

        def job_created_callback(job_id: str, db_path: str) -> None:
            captured["job_id"] = job_id
            captured["db_path"] = db_path

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
                    export_id="exp-2",
                    artifacts=[_FakeArtifact(os.path.join(self.temp_dir.name, "b.xlsx"))],
                ),
            ),
        ):
            result = run_streaming_daily_pipeline(
                args,
                config_obj=self.config,
                emit_console=False,
                job_created_callback=job_created_callback,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.job_id)
        self.assertEqual(captured["job_id"], result.job_id)
        self.assertTrue(captured["db_path"])

    def test_streaming_pipeline_can_append_to_externally_managed_job(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "external.sqlite3")
        store = StreamingStore(db_path, auto_migrate=True)
        job_id = store.create_job(
            "one_click",
            metadata={"record_family": "all", "record_families": ["listing", "deal"]},
        )
        store.start_job(job_id)
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
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
        fake_download_runner, fake_download_oneclick = self._patched_pipeline_modules()

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
            result = run_streaming_daily_pipeline(
                args,
                config_obj=self.config,
                emit_console=False,
                job_id=job_id,
                manage_job_lifecycle=False,
            )

        job = store.get_job(job_id)
        self.assertEqual(result.job_id, job_id)
        self.assertEqual(result.downloaded_count, 2)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["downloaded_count"], 2)

    def test_streaming_pipeline_preserves_resume_when_database_is_empty(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="all",
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
            streaming_db=None,
            no_auto_export=True,
        )
        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest

        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest
        captured: dict[str, bool] = {}

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            captured["resume"] = bool(request.download_request.resume)
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
        ):
            result = run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertEqual(result.exit_code, 0)
        self.assertIs(captured["resume"], True)

    def test_streaming_pipeline_canonicalizes_existing_skip_source_scope(self) -> None:
        args = argparse.Namespace(
            start_date="2026-03-20",
            end_date="2026-03-21",
            exchange="shanghai",
            record_family="deal",
            business_id="deal_equity_transfer",
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
        captured: dict[str, list[str]] = {"source_ids": []}

        def _fake_list_existing_project_codes(self, **kwargs):
            captured["source_ids"].append(str(kwargs.get("source_id") or ""))
            return set()

        def _fake_list_existing_candidate_tokens(self, **kwargs):
            captured["source_ids"].append(str(kwargs.get("source_id") or ""))
            return set()

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
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
            patch(
                "peap.streaming_daily_pipeline.StreamingStore.list_existing_project_codes",
                autospec=True,
                side_effect=_fake_list_existing_project_codes,
            ),
            patch(
                "peap.streaming_daily_pipeline.StreamingStore.list_existing_candidate_tokens",
                autospec=True,
                side_effect=_fake_list_existing_candidate_tokens,
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunner),
        ):
            result = run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(captured["source_ids"], ["sse", "sse"])


class StreamingDailyPipelineBoundaryTest(unittest.TestCase):
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

    def _args(self, **overrides):
        values = {
            "start_date": "2026-03-20",
            "end_date": "2026-03-21",
            "exchange": "all",
            "record_family": "listing",
            "business_id": "all",
            "concurrency": 2,
            "page_size": None,
            "max_pages": None,
            "with_refresh": False,
            "no_resume": False,
            "save_json": False,
            "postprocess_config": None,
            "verbose": False,
            "streaming_db": None,
            "no_auto_export": True,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def _patched_pipeline_modules(self, *, run_download_oneclick=None):
        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest
        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _default_run_download_oneclick(request, *, config_obj, emit_console):
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

        fake_download_oneclick.run_download_oneclick = run_download_oneclick or _default_run_download_oneclick
        return fake_download_runner, fake_download_oneclick

    def test_non_text_job_id_is_rejected_instead_of_stringified(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "external.sqlite3")
        store = StreamingStore(db_path, auto_migrate=True)
        store.create_job("one_click", job_id="123")
        args = self._args(streaming_db=db_path)
        fake_download_runner, fake_download_oneclick = self._patched_pipeline_modules()

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunner),
            self.assertRaisesRegex(TypeError, "job_id must be str"),
        ):
            run_streaming_daily_pipeline(
                args,
                config_obj=self.config,
                emit_console=False,
                job_id=123,
                manage_job_lifecycle=False,
            )

    def test_non_path_streaming_db_is_rejected_instead_of_stringified_relative_filename(self) -> None:
        fake_download_runner, fake_download_oneclick = self._patched_pipeline_modules()

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunner),
            self.assertRaisesRegex(TypeError, "streaming_db must be str or os.PathLike"),
        ):
            run_streaming_daily_pipeline(
                self._args(streaming_db=["streaming.sqlite3"]),
                config_obj=self.config,
                emit_console=False,
            )

    def test_explicit_bad_archive_root_values_are_rejected_instead_of_fallback_or_stringify(self) -> None:
        fake_download_runner, fake_download_oneclick = self._patched_pipeline_modules()

        for bad_archive_root in (False, True, ["archive"], {"root": "archive"}):
            with (
                self.subTest(archive_root=bad_archive_root),
                patch.dict(
                    "sys.modules",
                    {
                        "peap.download_runner": fake_download_runner,
                        "peap.download_oneclick": fake_download_oneclick,
                    },
                ),
                patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FakeRunner),
                self.assertRaisesRegex(TypeError, "archive_root must be str or os.PathLike"),
            ):
                run_streaming_daily_pipeline(
                    self._args(),
                    config_obj=self.config,
                    emit_console=False,
                    archive_root=bad_archive_root,
                )

    def test_auto_export_rejects_empty_export_root_in_pipeline_before_export_layer(self) -> None:
        self.config.OUTPUT_EXCEL_DIR = ""
        fake_download_runner, fake_download_oneclick = self._patched_pipeline_modules()

        @dataclass(frozen=True)
        class _FakeExportResult:
            export_id: str
            artifacts: list[object]

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
                return_value=_FakeExportResult(export_id="exp", artifacts=[]),
            ),
            self.assertRaisesRegex(ValueError, "export_root is required when auto export is enabled"),
        ):
            run_streaming_daily_pipeline(
                self._args(no_auto_export=False),
                config_obj=self.config,
                emit_console=False,
            )

    def test_auto_export_rejects_bad_export_root_values_in_pipeline_before_export_layer(self) -> None:
        fake_download_runner, fake_download_oneclick = self._patched_pipeline_modules()

        for bad_export_root in (False, True, ["exports"], {"root": "exports"}):
            with (
                self.subTest(export_root=bad_export_root),
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
                    side_effect=AssertionError("export layer should not receive invalid export_root"),
                ),
                self.assertRaisesRegex(TypeError, "export_root must be str or os.PathLike"),
            ):
                run_streaming_daily_pipeline(
                    self._args(no_auto_export=False),
                    config_obj=self.config,
                    emit_console=False,
                    export_root=bad_export_root,
                )

    def test_auto_export_is_blocked_when_ingest_records_failures_after_successful_download(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming.sqlite3")
        fake_download_runner, fake_download_oneclick = StreamingDailyPipelineSmokeTest._patched_pipeline_modules(self)

        @dataclass(frozen=True)
        class _FakeExportResult:
            export_id: str
            artifacts: list[object]

        class _FailingRunner:
            def __init__(self, *args, **kwargs):
                pass

            def ingest(self, item):
                code = os.path.splitext(os.path.basename(item.source_file))[0].upper()
                return {
                    "state": "parse_failed",
                    "record_id": code,
                    "project_code": code,
                    "archive_path": item.source_file,
                    "error_type": "parse_failed",
                    "error_message": "parse failed during streaming ingest",
                }

        export_mock = Mock(return_value=_FakeExportResult(export_id="exp", artifacts=[]))

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FailingRunner),
            patch("peap.streaming_daily_pipeline.run_ready_export", export_mock),
        ):
            result = run_streaming_daily_pipeline(
                self._args(streaming_db=db_path, no_auto_export=False),
                config_obj=self.config,
                emit_console=False,
            )

        store = StreamingStore(db_path)
        job = store.get_job(result.job_id)
        events = store.list_job_events(result.job_id, limit=100)

        export_mock.assert_not_called()
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(job["status"], "failed")
        self.assertGreater(job["exception_count"], 0)
        self.assertTrue(any(event["stage"] == "failed" for event in events))

    def test_field_missing_ingest_state_reaches_auto_export_warning_path(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming.sqlite3")
        fake_download_runner, fake_download_oneclick = StreamingDailyPipelineSmokeTest._patched_pipeline_modules(self)

        @dataclass(frozen=True)
        class _FakeExportResult:
            export_id: str
            artifacts: list[object]
            field_missing_blocked_records: int = 1
            field_missing_diagnostics: list[dict[str, object]] = field(
                default_factory=lambda: [
                    {
                        "record_id": "rec-field-missing",
                        "failure_code": "canonical_field_missing",
                        "missing_fields": ["deal_price"],
                    }
                ]
            )

        class _FieldMissingRunner:
            def __init__(self, *args, **kwargs):
                pass

            def ingest(self, item):
                code = os.path.splitext(os.path.basename(item.source_file))[0].upper()
                return {
                    "state": "field_missing",
                    "record_id": code,
                    "project_code": code,
                    "archive_path": item.source_file,
                    "findings": [
                        {
                            "type": "canonical_field_missing",
                            "evidence": {"missing_fields": ["deal_price"]},
                        }
                    ],
                }

        export_mock = Mock(return_value=_FakeExportResult(export_id="exp", artifacts=[]))

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FieldMissingRunner),
            patch("peap.streaming_daily_pipeline.run_ready_export", export_mock),
        ):
            result = run_streaming_daily_pipeline(
                self._args(streaming_db=db_path, no_auto_export=False),
                config_obj=self.config,
                emit_console=False,
            )

        store = StreamingStore(db_path)
        job = store.get_job(result.job_id)
        events = store.list_job_events(result.job_id, limit=100)

        export_mock.assert_called_once()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(job["status"], "success_with_warnings")
        self.assertEqual(job["exception_count"], 0)
        self.assertEqual(job["persisted_count"], 2)
        self.assertFalse(any(event["stage"] == "ingest_guard" for event in events))
        self.assertFalse(any(event["stage"] == "failed" for event in events))
        self.assertTrue(any(event["stage"] == "field_missing" for event in events))
        self.assertEqual(job["summary"]["warning_code"], "field_missing_blocked_records")

    def test_field_missing_ingest_state_does_not_trigger_guard_without_auto_export(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming.sqlite3")
        fake_download_runner, fake_download_oneclick = StreamingDailyPipelineSmokeTest._patched_pipeline_modules(self)

        class _FieldMissingRunner:
            def __init__(self, *args, **kwargs):
                pass

            def ingest(self, item):
                code = os.path.splitext(os.path.basename(item.source_file))[0].upper()
                return {
                    "state": "field_missing",
                    "record_id": code,
                    "project_code": code,
                    "archive_path": item.source_file,
                    "findings": [
                        {
                            "type": "canonical_field_missing",
                            "evidence": {"missing_fields": ["deal_price"]},
                        }
                    ],
                }

        export_mock = Mock(side_effect=AssertionError("auto export should stay disabled"))

        with (
            patch.dict(
                "sys.modules",
                {
                    "peap.download_runner": fake_download_runner,
                    "peap.download_oneclick": fake_download_oneclick,
                },
            ),
            patch("peap.streaming_daily_pipeline.StreamingIngestRunner", _FieldMissingRunner),
            patch("peap.streaming_daily_pipeline.run_ready_export", export_mock),
        ):
            result = run_streaming_daily_pipeline(
                self._args(streaming_db=db_path, no_auto_export=True),
                config_obj=self.config,
                emit_console=False,
            )

        store = StreamingStore(db_path)
        job = store.get_job(result.job_id)
        events = store.list_job_events(result.job_id, limit=100)

        export_mock.assert_not_called()
        self.assertEqual(result.exit_code, 0)
        self.assertNotEqual(job["status"], "failed")
        self.assertEqual(job["exception_count"], 0)
        self.assertFalse(any(event["stage"] == "ingest_guard" for event in events))
        self.assertFalse(any(event["stage"] == "failed" for event in events))
        self.assertTrue(any(event["stage"] == "field_missing" for event in events))

    def test_stage_callback_missing_phase_code_records_contract_violation_event(self) -> None:
        db_path = os.path.join(self.temp_dir.name, "streaming.sqlite3")

        def _run_download_with_bad_stage(request, *, config_obj, emit_console):
            request.stage_callback({"status": "running", "label": "missing phase"})
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

        fake_download_runner, fake_download_oneclick = self._patched_pipeline_modules(
            run_download_oneclick=_run_download_with_bad_stage,
        )

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
            result = run_streaming_daily_pipeline(
                self._args(streaming_db=db_path),
                config_obj=self.config,
                emit_console=False,
            )

        store = StreamingStore(db_path)
        events = store.list_job_events(result.job_id, limit=100)
        violations = [
            event
            for event in events
            if event["stage"] == "contract_violation"
            and event["error_type"] == "missing_phase_code"
        ]
        self.assertEqual(len(violations), 1)


class DownloadExistingSkipScopeTest(unittest.TestCase):
    def test_filter_existing_candidates_does_not_cross_skip_between_listing_and_deal(self) -> None:
        from peap.download_oneclick import _filter_existing_candidates

        candidate_entries = [
            {
                "project_code": "G32026SHSAME001",
                "project_id": "SAME001",
                "page_url": "https://example.test/deal/same001",
            }
        ]
        existing_project_codes = frozenset(
            {"scope:listing|equity_transfer|sse|project_code:G32026SHSAME001"}
        )
        existing_candidate_tokens = frozenset(
            {
                "scope:listing|equity_transfer|sse|project_code:G32026SHSAME001",
                "scope:listing|equity_transfer|sse|project_id:SAME001",
                "scope:listing|equity_transfer|sse|page_url:https://example.test/deal/same001",
            }
        )

        filtered, skipped = _filter_existing_candidates(
            candidate_entries,
            existing_project_codes=existing_project_codes,
            existing_candidate_tokens=existing_candidate_tokens,
            record_family="deal",
            business_id="deal_equity_transfer",
            source_id="sse",
        )

        self.assertEqual(skipped, 0)
        self.assertEqual(len(filtered), 1)

    def test_filter_existing_candidates_skips_when_scope_matches(self) -> None:
        from peap.download_oneclick import _filter_existing_candidates

        candidate_entries = [
            {
                "project_code": "G32026SHSAME002",
                "project_id": "SAME002",
                "page_url": "https://example.test/deal/same002",
            }
        ]
        existing_project_codes = frozenset(
            {"scope:deal|deal_equity_transfer|sse|project_code:G32026SHSAME002"}
        )
        existing_candidate_tokens = frozenset(
            {
                "scope:deal|deal_equity_transfer|sse|project_code:G32026SHSAME002",
                "scope:deal|deal_equity_transfer|sse|project_id:SAME002",
                "scope:deal|deal_equity_transfer|sse|page_url:https://example.test/deal/same002",
            }
        )

        filtered, skipped = _filter_existing_candidates(
            candidate_entries,
            existing_project_codes=existing_project_codes,
            existing_candidate_tokens=existing_candidate_tokens,
            record_family="deal",
            business_id="deal_equity_transfer",
            source_id="sse",
        )

        self.assertEqual(skipped, 1)
        self.assertEqual(len(filtered), 0)

    def test_filter_existing_candidates_falls_back_to_legacy_tokens_when_scope_absent(self) -> None:
        from peap.download_oneclick import _filter_existing_candidates

        candidate_entries = [
            {
                "project_code": "G32026SHLEGACY003",
                "project_id": "LEGACY003",
                "page_url": "https://example.test/deal/legacy003",
            }
        ]
        existing_project_codes = frozenset({"G32026SHLEGACY003"})
        existing_candidate_tokens = frozenset(
            {
                "project_code:G32026SHLEGACY003",
                "project_id:LEGACY003",
            }
        )

        filtered, skipped = _filter_existing_candidates(
            candidate_entries,
            existing_project_codes=existing_project_codes,
            existing_candidate_tokens=existing_candidate_tokens,
            record_family="deal",
            business_id="deal_equity_transfer",
            source_id="sse",
        )

        self.assertEqual(skipped, 1)
        self.assertEqual(filtered, [])


class StreamingDailyPipelineFailureSummaryTest(unittest.TestCase):
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

    def test_streaming_pipeline_summary_keeps_upstream_site_failure_visible(self) -> None:
        args = argparse.Namespace(
            start_date="2026-04-01",
            end_date="2026-04-02",
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
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
        typed_error = DownloadError(
            error_code="sse_list_failed",
            error_message="sse: list-failed: read operation timed out",
            stage="prepare_tasks",
            failure_kind="list",
            source_id="sse",
            task_id="sse:listing:physical_asset",
            raw_reason="read operation timed out",
        )

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            request.stage_callback(
                {
                    "phase_code": "prepare_tasks",
                    "status": "failed",
                    "label": "扫描失败",
                    "error_code": typed_error.error_code,
                    "error_message": typed_error.error_message,
                    "summary_payload": {
                        "aggregate_summary": {"detail_candidates": 0, "saved": 0},
                        "task_summaries": {},
                    },
                }
            )
            request.stage_callback(
                {
                    "phase_code": "save_pages",
                    "status": "done",
                    "label": "当前没有需要下载的网页，无需下载",
                    "summary_payload": {
                        "aggregate_summary": {"detail_candidates": 0, "saved": 0},
                        "task_summaries": {},
                    },
                }
            )
            return _FakeDownloadOneClickRunResult(
                exit_code=0,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=True,
                start="2026-04-01 00:00:00",
                end="2026-04-01 00:01:00",
                duration_sec=60.0,
                aggregate_summary={"saved": 0, "detail_candidates": 0},
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
            result = run_streaming_daily_pipeline(args, config_obj=self.config, emit_console=False)

        job_store = StreamingStore(result.db_path)
        job_store.migrate()
        job = job_store.get_job(result.job_id)
        self.assertEqual(job["summary"]["failure_code"], "sse_list_failed")
        self.assertIn("timed out", job["summary"]["failure_message"])

    def test_streaming_pipeline_summary_includes_download_archive_audit(self) -> None:
        args = argparse.Namespace(
            start_date="2026-04-01",
            end_date="2026-04-02",
            exchange="sse",
            record_family="listing",
            business_id="physical_asset",
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
        archive_audit = {
            "ok": False,
            "issue_count": 1,
            "issues": [{"code": "missing_sidecar", "path": "/tmp/P001.html"}],
        }
        fake_download_runner = types.ModuleType("peap.download_runner")
        fake_download_runner.DownloadRunRequest = _FakeDownloadRunRequest
        fake_download_oneclick = types.ModuleType("peap.download_oneclick")
        fake_download_oneclick.DownloadOneClickRequest = _FakeDownloadOneClickRequest

        def _fake_run_download_oneclick(request, *, config_obj, emit_console):
            return types.SimpleNamespace(
                exit_code=1,
                log_file="download.log",
                plan_file=request.plan_file,
                plan_file_exists=False,
                plan_file_removed=True,
                start="2026-04-01 00:00:00",
                end="2026-04-01 00:01:00",
                duration_sec=60.0,
                aggregate_summary={"saved": 1, "errors": 1},
                task_summaries={},
                typed_errors=[
                    DownloadError(
                        error_code="sse_archive_audit_failed",
                        error_message="sse: archive-audit-failed: download-archive-audit-failed: issues=1",
                        stage="save_pages",
                        failure_kind="validation",
                        source_id="sse",
                        task_id="archive_audit",
                        raw_reason="download-archive-audit-failed: issues=1",
                    )
                ],
                stages=[],
                archive_audit=archive_audit,
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

        job_store = StreamingStore(result.db_path)
        job_store.migrate()
        job = job_store.get_job(result.job_id)
        self.assertEqual(job["summary"]["download_archive_audit"], archive_audit)

    def test_download_archive_audit_summary_rejects_bad_contract(self) -> None:
        with self.assertRaisesRegex(TypeError, "download_result.archive_audit must be a mapping"):
            _download_archive_audit_summary(SimpleNamespace(archive_audit=[]))

    def test_failure_summary_rejects_non_mapping_event_payload_instead_of_treating_empty(self) -> None:
        with self.assertRaisesRegex(ValueError, "progress_event.payload must be a mapping"):
            _failure_summary_fields(
                [
                    {
                        "stage": "prepare_tasks",
                        "status": "failed",
                        "error_type": "",
                        "error_message": "",
                        "payload": [],
                    }
                ]
            )

    def test_warning_summary_rejects_non_mapping_summary_payload_instead_of_treating_empty(self) -> None:
        with self.assertRaisesRegex(ValueError, "progress_event.payload.summary_payload must be a mapping"):
            _warning_summary_fields(
                [
                    {
                        "stage": "save_pages",
                        "status": "warning",
                        "payload": {
                            "summary_payload": [],
                        },
                    }
                ]
            )

    def test_warning_summary_rejects_non_mapping_nested_summary_instead_of_treating_empty(self) -> None:
        with self.assertRaisesRegex(ValueError, "progress_event.payload.summary_payload.summary must be a mapping"):
            _warning_summary_fields(
                [
                    {
                        "stage": "save_pages",
                        "status": "warning",
                        "payload": {
                            "summary_payload": {
                                "summary": [],
                            },
                        },
                    }
                ]
            )

    def test_download_failure_summary_rejects_bad_typed_errors_contract(self) -> None:
        with self.assertRaisesRegex(TypeError, "download_result.typed_errors must be a list"):
            _download_failure_summary_fields(SimpleNamespace(typed_errors={}))


if __name__ == "__main__":
    unittest.main()
