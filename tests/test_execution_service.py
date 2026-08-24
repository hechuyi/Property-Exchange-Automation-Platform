from __future__ import annotations

import inspect
import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppUserFacingError
from desktop_backend.error_codes import ERROR_MANUAL_IMPORT_INPUT_DIR_NOT_FOUND
from desktop_backend.services.execution_service import ExecutionService
from desktop_backend.services.runtime_service import RuntimeService
from peap.streaming_export import count_records_in_export_scope_by_state, run_ready_export
from peap.streaming_models import ExportRequest, IngestedRecord
from peap.streaming_store import StreamingStore


class _BrokenProductReadinessRuntimeService:
    def __init__(self, readiness: dict[str, object]) -> None:
        self.readiness = readiness

    def get_browser_runtime_status(self) -> dict[str, object]:
        return {
            "browser_name": "chromium",
            "installed": False,
        }

    def build_product_readiness(self, *, browser_runtime: dict[str, object] | None = None) -> dict[str, object]:
        return dict(self.readiness)


class FakeRuntimeDependencies:
    def get_browser_runtime_status(self, *, browser_name: str = "chromium") -> dict[str, object]:
        return {
            "browser_name": browser_name,
            "browser_cache_dir": "/tmp/browser-cache",
            "driver_executable": "/tmp/driver",
            "driver_cli": "/tmp/cli.js",
            "executable_path": "/tmp/chrome",
            "installed": True,
            "error": "",
        }

    def install_browser_runtime(self, *, browser_name: str = "chromium") -> dict[str, object]:
        return {
            "browser_name": browser_name,
            "browser_cache_dir": "/tmp/browser-cache",
            "driver_executable": "/tmp/driver",
            "driver_cli": "/tmp/cli.js",
            "executable_path": "/tmp/chrome",
            "installed": True,
            "error": "",
            "returncode": 0,
        }


@dataclass(frozen=True)
class _FakeExportArtifact:
    file_path: str


@dataclass(frozen=True)
class _FakeExportResult:
    export_id: str
    cursor_id: str
    artifacts: list[_FakeExportArtifact]
    new_records: int = 0
    changed_records: int = 0
    revision_watermark: int = 0
    field_missing_blocked_records: int = 0
    field_missing_diagnostics: list[dict[str, object]] | None = None


class ExecutionServiceSmokeTest(unittest.TestCase):
    def test_execution_service_uses_shared_job_event_summary_contract(self) -> None:
        import desktop_backend.services.execution_service as execution_service_module

        source = inspect.getsource(execution_service_module)
        self.assertIn("peap.job_event_summary", source)
        self.assertNotIn("streaming_daily_pipeline import _failure_summary_fields", source)
        self.assertNotIn("streaming_daily_pipeline import _has_failed_job_event", source)
        self.assertNotIn("repository._store", source)

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app_home = os.path.join(self.temp_dir.name, "app_home")
        self.docs_home = os.path.join(self.temp_dir.name, "docs_home")
        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": self.app_home,
                "PEAP_DOCUMENTS_HOME": self.docs_home,
            },
            clear=False,
        ):
            self.config = AppConfig.from_env(project_root=self.temp_dir.name)

        self.db_path = os.path.join(self.temp_dir.name, "streaming.sqlite3")
        self.store = StreamingStore(self.db_path, auto_migrate=True)
        self.runtime_service = RuntimeService(
            config_obj=self.config,
            store=self.store,
            runtime_dependencies=FakeRuntimeDependencies(),
        )
        self.archive_root = os.path.join(self.temp_dir.name, "archive")
        self.export_root = os.path.join(self.temp_dir.name, "exports")
        os.makedirs(self.archive_root, exist_ok=True)
        os.makedirs(self.export_root, exist_ok=True)
        self.basic_settings = {
            "archive_root": self.archive_root,
            "export_root": self.export_root,
            "default_exchange": "all",
            "effective_default_scope": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "all",
            },
            "stored_preference": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "all",
            },
            "stale_default_metadata": {
                "is_stale": False,
                "reason": "",
            },
            "default_concurrency": 2,
        }
        self.advanced_settings = {
            "save_json": False,
            "postprocess_config": "",
            "raw_manual_root": "",
        }

        class StubRunner:
            def __init__(runner_self, owner: ExecutionServiceSmokeTest) -> None:
                runner_self.owner = owner

            def ingest(runner_self, payload):
                return {
                    "state": "ready",
                    "record_id": "rec-1",
                    "project_code": "CODE-1",
                    "archive_path": str(getattr(payload, "source_file", "")),
                }

        self.service = ExecutionService(
            config_obj=self.config,
            store=self.store,
            db_path=self.db_path,
            runtime_service=self.runtime_service,
            get_basic_settings=lambda: dict(self.basic_settings),
            get_advanced_settings=lambda: dict(self.advanced_settings),
            run_store_maintenance=lambda: None,
            repair_missing_archives_once=lambda: None,
            build_ingest_runner=lambda archive_root=None: StubRunner(self),
            user_error_cls=AppUserFacingError,
        )

    def test_launch_one_click_smoke_returns_started_job_id(self) -> None:
        captured: dict[str, object] = {}

        def fake_pipeline(
            args,
            *,
            config_obj,
            emit_console,
            job_created_callback,
            job_type,
            archive_root,
            export_root,
            auto_export,
            job_id=None,
        ):
            captured["job_id"] = job_id
            captured["job_type"] = job_type
            job_created_callback(job_id, self.db_path)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_pipeline):
            payload = self.service.launch_one_click(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "all",
                },
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertTrue(payload["job_id"])
        self.assertEqual(payload["job_id"], captured["job_id"])
        self.assertEqual(captured["job_type"], "one_click")

    def test_launch_one_click_rejects_invalid_boolean_string_instead_of_truthy_coercion(self) -> None:
        with self.assertRaisesRegex(AppUserFacingError, "no_resume") as context:
            self.service.launch_one_click(
                {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "all",
                    "no_resume": "not-a-bool",
                },
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(self.service._active_mutating_jobs, set())

    def test_launch_one_click_rejects_non_string_scope_field_before_reserving_job(self) -> None:
        with self.assertRaisesRegex(AppUserFacingError, "record_family") as context:
            self.service.launch_one_click(
                {
                    "record_family": {"value": "listing"},
                    "business_id": "equity_transfer",
                    "exchange": "all",
                },
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(self.service._active_mutating_jobs, set())

    def test_launch_download_ingest_rejects_non_string_scope_field_before_reserving_job(self) -> None:
        with self.assertRaisesRegex(AppUserFacingError, "business_id") as context:
            self.service.launch_download_ingest(
                {
                    "record_family": "listing",
                    "business_id": {"value": "equity_transfer"},
                    "exchange": "all",
                },
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(self.service._active_mutating_jobs, set())

    def test_launch_one_click_rejects_non_list_family_scopes_in_error_details(self) -> None:
        with self.assertRaisesRegex(AppUserFacingError, "family_scopes") as context:
            self.service.launch_one_click(
                {
                    "record_families": ["listing", "deal"],
                    "family_scopes": "listing,deal",
                },
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(context.exception.details["family_scopes"], "listing,deal")
        self.assertEqual(self.service._active_mutating_jobs, set())

    def test_launch_one_click_rejects_non_list_product_readiness_issues(self) -> None:
        self.service.runtime_service = _BrokenProductReadinessRuntimeService(
            {
                "download_ready": False,
                "issues": {"code": "browser_runtime_missing", "message": "not installed"},
            }
        )

        with self.assertRaisesRegex(ValueError, "product_readiness.issues must be a list"):
            self.service.launch_one_click(
                {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "all",
                },
                start_background_thread=lambda *, name, target: target(),
            )

    def test_launch_one_click_rejects_non_mapping_product_readiness_issue(self) -> None:
        self.service.runtime_service = _BrokenProductReadinessRuntimeService(
            {
                "download_ready": False,
                "issues": ["browser_runtime_missing"],
            }
        )

        with self.assertRaisesRegex(ValueError, "product_readiness.issues\\[0\\] must be a mapping"):
            self.service.launch_one_click(
                {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "all",
                },
                start_background_thread=lambda *, name, target: target(),
            )

    def test_launch_manual_import_smoke_returns_job_id(self) -> None:
        payload = self.service.launch_manual_import({"input_dir": self.temp_dir.name})

        self.assertTrue(payload["job_id"])
        self.assertEqual(payload["job_type"], "manual_import")
        self.assertEqual(payload["discovered_count"], 0)
        self.assertNotIn("business_id", payload)
        self.assertNotIn("scope", payload)
        operation_rows = self.store.list_operation_journals(limit=10)
        self.assertEqual(len(operation_rows), 1)
        self.assertEqual(operation_rows[0]["operation_type"], "manual_import")
        self.assertEqual(operation_rows[0]["status"], "succeeded")
        self.assertEqual(operation_rows[0]["manifest"]["job"]["job_type"], "manual_import")
        self.assertEqual(operation_rows[0]["manifest"]["summary"]["discovered_count"], 0)

    def test_launch_manual_import_preserves_explicit_scope_in_job_metadata(self) -> None:
        payload = self.service.launch_manual_import(
            {
                "input_dir": self.temp_dir.name,
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "sse",
            }
        )

        self.assertEqual(payload["business_id"], "equity_transfer")
        self.assertEqual(
            payload["scope"],
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "sse",
            },
        )

    def test_launch_manual_import_rejects_incomplete_explicit_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "record_family and business_id"):
            self.service.launch_manual_import(
                {
                    "input_dir": self.temp_dir.name,
                    "business_id": "equity_transfer",
                    "exchange": "sse",
                }
            )

    def test_launch_manual_import_rejects_unknown_explicit_business_id_instead_of_omitting_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "business_id"):
            self.service.launch_manual_import(
                {
                    "input_dir": self.temp_dir.name,
                    "record_family": "listing",
                    "business_id": "not_a_real_business",
                    "exchange": "sse",
                }
            )

        operation_rows = self.store.list_operation_journals(limit=10)
        self.assertEqual(len(operation_rows), 1)
        self.assertEqual(operation_rows[0]["operation_type"], "manual_import")
        self.assertEqual(operation_rows[0]["status"], "failed")

    def test_launch_manual_import_rejects_exchange_outside_records_source_contract(self) -> None:
        with self.assertRaises(AppUserFacingError) as context:
            self.service.launch_manual_import(
                {
                    "input_dir": self.temp_dir.name,
                    "record_family": "deal",
                    "business_id": "deal_physical_asset",
                    "business_label": "实物资产成交",
                    "exchange": "tpre",
                }
            )

        self.assertEqual(context.exception.error_code, "invalid_request")
        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(
            context.exception.details.get("scope"),
            {
                "exchange": "tpre",
                "record_family": "deal",
                "business_id": "deal_physical_asset",
            },
        )
        self.assertEqual(context.exception.details.get("surface"), "records")
        operation_rows = self.store.list_operation_journals(limit=10)
        self.assertEqual(len(operation_rows), 1)
        self.assertEqual(operation_rows[0]["operation_type"], "manual_import")
        self.assertEqual(operation_rows[0]["status"], "failed")
        self.assertEqual(operation_rows[0]["error"]["code"], "invalid_request")

    def test_launch_manual_import_rejects_unreadable_input_dir(self) -> None:
        input_dir = Path(self.temp_dir.name) / "manual-import-no-access"
        input_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(input_dir, 0)
        self.addCleanup(lambda: os.chmod(input_dir, 0o700))

        try:
            os.listdir(input_dir)
        except PermissionError:
            pass
        else:
            self.skipTest("chmod 000 does not block directory reads on this platform/user")

        with self.assertRaises(AppUserFacingError) as context:
            self.service.launch_manual_import({"input_dir": str(input_dir)})

        self.assertEqual(context.exception.error_code, "invalid_request")
        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(context.exception.details.get("input_dir"), str(input_dir))

    def test_launch_manual_import_rejects_non_string_input_dir_before_start_operation(self) -> None:
        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            self.assertRaisesRegex(ValueError, "input_dir"),
        ):
            self.service.launch_manual_import({"input_dir": {"path": self.temp_dir.name}})

        mocked_start_operation.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_launch_manual_import_rejects_false_input_dir_before_using_default_root(self) -> None:
        self.advanced_settings["raw_manual_root"] = self.temp_dir.name
        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            self.assertRaisesRegex(ValueError, "input_dir"),
        ):
            self.service.launch_manual_import({"input_dir": False})

        mocked_start_operation.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_ingest_manual_import_file_uses_business_scope_as_project_type_fallback(self) -> None:
        captured: dict[str, object] = {}

        class CaptureRunner:
            def ingest(self, payload):
                captured["payload"] = payload
                return {
                    "state": "ready",
                    "record_id": "rec-fallback",
                    "project_code": "CODE-FALLBACK",
                    "archive_path": str(getattr(payload, "source_file", "")),
                }

        self.service.build_ingest_runner = lambda archive_root=None: CaptureRunner()

        self.service.ingest_manual_import_file(
            "/tmp/manual-import-fallback.html",
            import_scope={
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "sse",
            },
        )

        payload = captured["payload"]
        self.assertEqual(payload.extra["project_type_fallback"], "股权转让")
        self.assertEqual(payload.extra["business_id"], "equity_transfer")
        self.assertEqual(payload.extra["exchange"], "sse")

    def test_ingest_manual_import_file_keeps_project_type_fallback_stable_under_english_business_label(self) -> None:
        captured: dict[str, object] = {}

        class CaptureRunner:
            def ingest(self, payload):
                captured["payload"] = payload
                return {
                    "state": "ready",
                    "record_id": "rec-fallback-english",
                    "project_code": "CODE-FALLBACK-ENGLISH",
                    "archive_path": str(getattr(payload, "source_file", "")),
                }

        self.service.build_ingest_runner = lambda archive_root=None: CaptureRunner()

        self.service.ingest_manual_import_file(
            "/tmp/manual-import-fallback-english.html",
            import_scope={
                "record_family": "listing",
                "business_id": "physical_asset",
                "business_label": "Physical Asset",
                "exchange": "sse",
            },
        )

        payload = captured["payload"]
        self.assertEqual(payload.extra["business_label"], "Physical Asset")
        self.assertEqual(payload.extra["project_type_fallback"], "实物资产")
        self.assertEqual(payload.extra["business_id"], "physical_asset")
        self.assertEqual(payload.extra["exchange"], "sse")

    def test_ingest_manual_import_file_uses_family_specific_deal_business_with_listing_project_type_fallback(self) -> None:
        captured: dict[str, object] = {}

        class CaptureRunner:
            def ingest(self, payload):
                captured["payload"] = payload
                return {
                    "state": "ready",
                    "record_id": "rec-fallback-deal",
                    "project_code": "CODE-FALLBACK-DEAL",
                    "archive_path": str(getattr(payload, "source_file", "")),
                }

        self.service.build_ingest_runner = lambda archive_root=None: CaptureRunner()

        self.service.ingest_manual_import_file(
            "/tmp/manual-import-fallback-deal.html",
            import_scope={
                "record_family": "deal",
                "business_id": "deal_equity_transfer",
                "business_label": "Deal Equity Transfer",
                "exchange": "all",
            },
        )

        payload = captured["payload"]
        self.assertEqual(payload.extra["business_label"], "Deal Equity Transfer")
        self.assertEqual(payload.extra["project_type_fallback"], "股权转让")
        self.assertEqual(payload.extra["business_id"], "deal_equity_transfer")

    def test_run_manual_import_job_treats_pending_review_as_imported_warning(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "manual-pending-review.html")
        Path(source_file).write_text("<html></html>", encoding="utf-8")
        job_id = self.store.create_job("manual_import", metadata={"input_dir": self.temp_dir.name})
        self.store.start_job(job_id)

        self.service.run_manual_import_job(
            job_id=job_id,
            files=[source_file],
            ingest_file=lambda _file_path: {
                "state": "pending_review",
                "record_id": "rec-pending-review",
                "project_code": "CODE-PENDING-REVIEW",
                "archive_path": source_file,
            },
            sleep_fn=lambda _seconds: None,
        )

        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "success_with_warnings")
        self.assertEqual(job["summary"]["imported_count"], 1)
        self.assertEqual(job["summary"]["pending_review_count"], 1)
        self.assertEqual(job["summary"]["failed_count"], 0)

    def test_run_manual_import_job_treats_field_missing_as_persisted_warning(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "manual-field-missing.html")
        Path(source_file).write_text("<html></html>", encoding="utf-8")
        job_id = self.store.create_job("manual_import", metadata={"input_dir": self.temp_dir.name})
        self.store.start_job(job_id)

        self.service.run_manual_import_job(
            job_id=job_id,
            files=[source_file],
            ingest_file=lambda _file_path: {
                "state": "field_missing",
                "record_id": "rec-field-missing",
                "project_code": "CODE-FIELD-MISSING",
                "archive_path": source_file,
            },
            sleep_fn=lambda _seconds: None,
        )

        job = self.store.get_job(job_id)
        self.assertEqual(job["status"], "success_with_warnings")
        self.assertEqual(job["summary"]["imported_count"], 1)
        self.assertEqual(job["summary"]["field_missing_count"], 1)
        self.assertEqual(job["summary"]["failed_count"], 0)
        self.assertEqual(job["downloaded_count"], 1)
        self.assertEqual(job["persisted_count"], 1)
        self.assertEqual(job["exception_count"], 0)

    def test_run_manual_import_job_stops_after_job_is_interrupted(self) -> None:
        first_file = os.path.join(self.temp_dir.name, "manual-interrupt-first.html")
        second_file = os.path.join(self.temp_dir.name, "manual-interrupt-second.html")
        Path(first_file).write_text("<html></html>", encoding="utf-8")
        Path(second_file).write_text("<html></html>", encoding="utf-8")
        job_id = self.store.create_job("manual_import", metadata={"input_dir": self.temp_dir.name})
        self.store.start_job(job_id)
        ingested: list[str] = []

        def ingest(file_path: str) -> dict[str, object]:
            ingested.append(file_path)
            self.store.interrupt_job(job_id, reason="operator stop")
            return {
                "state": "ready",
                "record_id": "rec-interrupted",
                "project_code": "CODE-INTERRUPTED",
                "archive_path": file_path,
            }

        self.service.run_manual_import_job(
            job_id=job_id,
            files=[first_file, second_file],
            ingest_file=ingest,
            sleep_fn=lambda _seconds: None,
        )

        self.assertEqual(ingested, [first_file])
        self.assertEqual(self.store.get_job(job_id)["status"], "interrupted")

    def test_ingest_manual_import_file_uses_deal_archive_root_for_deal_scope(self) -> None:
        deal_root = os.path.join(self.temp_dir.name, "deal-archive")
        self.basic_settings["deal_archive_root"] = deal_root
        captured: dict[str, object] = {}

        class CaptureRunner:
            def ingest(self, payload):
                captured["payload"] = payload
                return {"state": "ready"}

        self.service.build_ingest_runner = lambda archive_root=None: (
            captured.__setitem__("archive_root", archive_root) or CaptureRunner()
        )
        self.service.ingest_manual_import_file(
            os.path.join(self.temp_dir.name, "deal-manual.html"),
            import_scope={
                "record_family": "deal",
                "business_id": "deal_equity_transfer",
                "exchange": "sse",
            },
        )

        self.assertEqual(captured["archive_root"], deal_root)

    def test_run_export_with_contract_smoke_returns_job_summary(self) -> None:
        payload = self.service.run_export_with_contract(
            {
                "scope": {
                    "record_family": "listing",
                    "business_id": "all",
                    "state": "all",
                    "exchange": "all",
                    "keyword": "",
                    "date_from": "",
                    "date_to": "",
                },
                "requested_export_mode": "full",
                "output_dir": self.export_root,
            },
            run_ready_export_fn=lambda store, request: _FakeExportResult(
                export_id="export-1",
                cursor_id=request.cursor_id,
                artifacts=[_FakeExportArtifact(file_path=os.path.join(self.export_root, "export.xlsx"))],
                new_records=1,
                changed_records=0,
                revision_watermark=17,
            ),
            count_scope_fn=lambda _store, request: {},
        )

        self.assertTrue(payload["job_id"])
        self.assertEqual(payload["status"], "completed")
        self.assertTrue(payload["artifacts"])
        self.assertEqual(payload["requested_export_mode"], "full")
        self.assertTrue(payload["cursor_id"])
        self.assertNotIn("cursor_key", payload)
        self.assertEqual(payload["revision_watermark"], 17)
        operation_rows = self.store.list_operation_journals(limit=10)
        self.assertEqual(operation_rows[0]["operation_type"], "export_excel")
        self.assertEqual(operation_rows[0]["status"], "succeeded")
        self.assertEqual(operation_rows[0]["manifest"]["job"]["job_id"], payload["job_id"])
        self.assertEqual(operation_rows[0]["manifest"]["result"]["status"], "completed")

    def test_run_export_starts_job_before_export_runner(self) -> None:
        observed: dict[str, str] = {}

        def run_export(store, request):
            jobs = store.list_jobs(limit=10)
            observed["status"] = str(next(job for job in jobs if job["job_type"] == "export_excel")["status"])
            return _FakeExportResult(
                export_id="export-started",
                cursor_id=request.cursor_id,
                artifacts=[],
            )

        self.service.run_export_with_contract(
            {
                "scope": {
                    "record_family": "listing",
                    "business_id": "all",
                    "state": "all",
                    "exchange": "all",
                    "keyword": "",
                    "date_from": "",
                    "date_to": "",
                },
                "requested_export_mode": "full",
                "output_dir": self.export_root,
            },
            run_ready_export_fn=run_export,
            count_scope_fn=lambda _store, request: {},
        )

        self.assertEqual(observed["status"], "running")

    def test_launch_archive_reprocess_empty_directory_writes_operation_journal(self) -> None:
        payload = self.service.launch_archive_reprocess({})

        self.assertTrue(payload["job_id"])
        self.assertEqual(payload["job_type"], "archive_reprocess")
        operation_rows = self.store.list_operation_journals(limit=10)
        self.assertEqual(operation_rows[0]["operation_type"], "archive_reprocess")
        self.assertEqual(operation_rows[0]["status"], "succeeded")
        self.assertEqual(operation_rows[0]["manifest"]["job"]["job_type"], "archive_reprocess")
        self.assertEqual(operation_rows[0]["manifest"]["summary"]["discovered_count"], 0)

    def test_latest_progress_rejects_malformed_progress_event_numbers(self) -> None:
        job_id = self.store.create_job("one_click", metadata={"record_family": "listing"})
        self.store.start_job(job_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO job_events (
                    job_id, event_ts, stage, status, payload_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    "2026-05-31T00:00:00Z",
                    "prepare_tasks",
                    "running",
                    json.dumps(
                        {
                            "task_index": "first",
                            "task_total": "many",
                            "phase_percent": "half",
                        }
                    ),
                ),
            )

        job = self.store.get_job(job_id)
        with self.assertRaisesRegex(ValueError, "progress_event.payload.task_index"):
            self.service.build_latest_progress(job)

    def test_launch_archive_reprocess_rejects_explicit_missing_input_dir_without_creating_it(self) -> None:
        missing_input_dir = os.path.join(self.temp_dir.name, "missing-archive-reprocess-input")

        with self.assertRaisesRegex(AppUserFacingError, "归档目录不存在") as context:
            self.service.launch_archive_reprocess({"input_dir": missing_input_dir})

        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(context.exception.error_code, ERROR_MANUAL_IMPORT_INPUT_DIR_NOT_FOUND)
        self.assertEqual(context.exception.details.get("input_dir"), missing_input_dir)
        self.assertFalse(os.path.exists(missing_input_dir))
        operation_rows = self.store.list_operation_journals(limit=10)
        self.assertEqual(operation_rows[0]["operation_type"], "archive_reprocess")
        self.assertEqual(operation_rows[0]["status"], "failed")
        self.assertEqual(operation_rows[0]["metadata"]["input_dir"], missing_input_dir)
        self.assertIn("归档目录不存在", operation_rows[0]["error"]["message"])
        jobs = self.store.list_jobs(limit=10)
        self.assertFalse(any(job["job_type"] == "archive_reprocess" for job in jobs))
        self.assertFalse(self.service._active_mutating_jobs)

    def test_launch_archive_reprocess_rejects_non_string_input_dir_before_start_operation(self) -> None:
        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            patch("desktop_backend.services.execution_service.os.makedirs") as mocked_makedirs,
            self.assertRaisesRegex(ValueError, "input_dir"),
        ):
            self.service.launch_archive_reprocess({"input_dir": {"path": self.archive_root}})

        mocked_start_operation.assert_not_called()
        mocked_makedirs.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_launch_archive_reprocess_rejects_false_input_dir_before_using_default_root(self) -> None:
        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            patch("desktop_backend.services.execution_service.os.makedirs") as mocked_makedirs,
            self.assertRaisesRegex(ValueError, "input_dir"),
        ):
            self.service.launch_archive_reprocess({"input_dir": False})

        mocked_start_operation.assert_not_called()
        mocked_makedirs.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_retry_job_relaunches_one_click_and_returns_new_job(self) -> None:
        original_job_id = self.store.create_job(
            "one_click",
            metadata={
                "start_date": "2026-03-22",
                "end_date": "2026-03-22",
                "exchange": "all",
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
            },
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        def _fake_pipeline(
            _args,
            *,
            config_obj,
            emit_console,
            job_created_callback,
            job_type,
            archive_root,
            export_root,
            auto_export,
            job_id=None,
        ):
            job_created_callback(job_id, self.db_path)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=_fake_pipeline):
            with patch.object(self.service, "start_background_thread", side_effect=lambda *, name, target: target()):
                payload = self.service.retry_job(original_job_id)

        self.assertEqual(payload["job_type"], "one_click")
        self.assertEqual(payload["retry_of_job_id"], original_job_id)
        self.assertEqual(payload["notification"]["level"], "success")
        self.assertNotEqual(payload["job_id"], original_job_id)
        self.assertIsNotNone(self.store.get_job(payload["job_id"]))

    def test_retry_job_preserves_normalized_streaming_options(self) -> None:
        original_job_id = self.store.create_job(
            "one_click",
            metadata={
                "start_date": "2026-03-22",
                "end_date": "2026-03-23",
                "exchange": "sse",
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "concurrency": 3,
                "page_size": 25,
                "max_pages": 10,
                "save_json": True,
                "no_resume": True,
                "verbose": True,
                "postprocess_config": "/tmp/rules.json",
            },
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        with patch.object(
            self.service,
            "launch_one_click",
            return_value={"job_id": "retry-job", "job_type": "one_click"},
        ) as launch:
            result = self.service.retry_job(original_job_id)

        self.assertEqual(result["job_id"], "retry-job")
        self.assertEqual(
            launch.call_args.args[0],
            {
                "start_date": "2026-03-22",
                "end_date": "2026-03-23",
                "exchange": "sse",
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "concurrency": 3,
                "page_size": 25,
                "max_pages": 10,
                "save_json": True,
                "no_resume": True,
                "verbose": True,
                "postprocess_config": "/tmp/rules.json",
            },
        )

    def test_retry_job_preserves_multi_family_scope(self) -> None:
        original_job_id = self.store.create_job(
            "download_ingest",
            metadata={
                "start_date": "2026-03-22",
                "end_date": "2026-03-23",
                "exchange": "sse",
                "record_family": "",
                "record_families": ["listing", "deal"],
                "family_scopes": [
                    {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "business_label": "股权转让",
                        "exchange": "sse",
                    },
                    {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "business_label": "成交股权转让",
                        "exchange": "sse",
                    },
                ],
                "concurrency": 2,
                "page_size": 20,
                "max_pages": None,
                "save_json": False,
                "no_resume": False,
                "verbose": False,
                "postprocess_config": "",
            },
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        with patch.object(
            self.service,
            "launch_download_ingest",
            return_value={"job_id": "retry-multi", "job_type": "download_ingest"},
        ) as launch:
            self.service.retry_job(original_job_id)

        retry_payload = launch.call_args.args[0]
        self.assertEqual(retry_payload["family_scopes"][1]["record_family"], "deal")
        self.assertEqual(retry_payload["concurrency"], 2)
        self.assertEqual(retry_payload["page_size"], 20)
        self.assertIsNone(retry_payload["max_pages"])

    def test_retry_job_rejects_non_string_job_id_before_repository_access(self) -> None:
        with (
            patch.object(self.service.repository, "get_job") as mocked_get_job,
            self.assertRaisesRegex(ValueError, "job_id"),
        ):
            self.service.retry_job({"job_id": "job-1"})

        mocked_get_job.assert_not_called()

    def test_retry_job_rejects_running_job_before_reading_or_relaunching_payload(self) -> None:
        with (
            patch.object(
                self.service.repository,
                "get_job",
                return_value={
                    "job_id": "job-1",
                    "job_type": "one_click",
                    "status": "running",
                    "metadata": {"start_date": "2026-03-22"},
                },
            ),
            patch.object(self.service, "launch_one_click", side_effect=AssertionError("unexpected relaunch")),
            self.assertRaisesRegex(ValueError, "job status is not retryable: running"),
        ):
            self.service.retry_job("job-1")

    def test_retry_job_rejects_non_mapping_job_metadata_before_relaunch(self) -> None:
        with (
            patch.object(
                self.service.repository,
                "get_job",
                return_value={
                    "job_id": "job-1",
                    "job_type": "one_click",
                    "status": "failed",
                    "metadata": False,
                },
            ),
            patch.object(self.service, "launch_one_click", side_effect=AssertionError("unexpected relaunch")),
            self.assertRaisesRegex(ValueError, "metadata"),
        ):
            self.service.retry_job("job-1")

    def test_retry_job_rejects_false_one_click_start_date_before_launch(self) -> None:
        original_job_id = self.store.create_job(
            "one_click",
            metadata={
                "start_date": False,
                "end_date": "2026-03-22",
                "exchange": "all",
                "record_family": "listing",
                "business_id": "equity_transfer",
            },
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        with (
            patch.object(self.service, "start_background_thread", side_effect=lambda *, name, target: target()),
            patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=AssertionError("unexpected pipeline launch")),
            self.assertRaisesRegex(AppUserFacingError, "start_date"),
        ):
            self.service.retry_job(original_job_id)

    def test_retry_job_relaunches_manual_import_using_original_input_dir(self) -> None:
        original_job_id = self.store.create_job(
            "manual_import",
            metadata={"input_dir": self.temp_dir.name},
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        payload = self.service.retry_job(original_job_id)

        self.assertEqual(payload["job_type"], "manual_import")
        self.assertEqual(payload["retry_of_job_id"], original_job_id)
        self.assertEqual(payload["input_dir"], self.temp_dir.name)
        self.assertNotEqual(payload["job_id"], original_job_id)
        self.assertIsNotNone(self.store.get_job(payload["job_id"]))

    def test_retry_job_rejects_non_string_manual_import_input_dir_before_start_operation(self) -> None:
        original_job_id = self.store.create_job(
            "manual_import",
            metadata={"input_dir": {"path": self.temp_dir.name}},
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            self.assertRaisesRegex(ValueError, "input_dir"),
        ):
            self.service.retry_job(original_job_id)

        mocked_start_operation.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_retry_job_rejects_false_manual_import_input_dir_before_using_default_root(self) -> None:
        self.advanced_settings["raw_manual_root"] = self.temp_dir.name
        original_job_id = self.store.create_job(
            "manual_import",
            metadata={"input_dir": False},
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            self.assertRaisesRegex(ValueError, "input_dir"),
        ):
            self.service.retry_job(original_job_id)

        mocked_start_operation.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_retry_job_rejects_missing_manual_import_input_dir_before_using_default_root(self) -> None:
        self.advanced_settings["raw_manual_root"] = self.temp_dir.name
        original_job_id = self.store.create_job(
            "manual_import",
            metadata={},
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            self.assertRaisesRegex(ValueError, "input_dir"),
        ):
            self.service.retry_job(original_job_id)

        mocked_start_operation.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_retry_job_rejects_empty_manual_import_input_dir_before_using_default_root(self) -> None:
        self.advanced_settings["raw_manual_root"] = self.temp_dir.name
        original_job_id = self.store.create_job(
            "manual_import",
            metadata={"input_dir": ""},
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            self.assertRaisesRegex(ValueError, "input_dir"),
        ):
            self.service.retry_job(original_job_id)

        mocked_start_operation.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_retry_job_rejects_non_object_manual_import_scope_instead_of_dropping_scope(self) -> None:
        original_job_id = self.store.create_job(
            "manual_import",
            metadata={"input_dir": self.temp_dir.name, "scope": "equity_transfer"},
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            self.assertRaisesRegex(ValueError, "scope"),
        ):
            self.service.retry_job(original_job_id)

        mocked_start_operation.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_retry_job_rejects_null_manual_import_scope_instead_of_dropping_scope(self) -> None:
        original_job_id = self.store.create_job(
            "manual_import",
            metadata={"input_dir": self.temp_dir.name, "scope": None},
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            self.assertRaisesRegex(ValueError, "scope"),
        ):
            self.service.retry_job(original_job_id)

        mocked_start_operation.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_retry_job_rejects_non_string_archive_reprocess_input_dir_before_start_operation(self) -> None:
        original_job_id = self.store.create_job(
            "archive_reprocess",
            metadata={"input_dir": {"path": self.archive_root}},
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            patch("desktop_backend.services.execution_service.os.makedirs") as mocked_makedirs,
            self.assertRaisesRegex(ValueError, "input_dir"),
        ):
            self.service.retry_job(original_job_id)

        mocked_start_operation.assert_not_called()
        mocked_makedirs.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_retry_job_rejects_empty_archive_reprocess_input_dir_before_using_default_root(self) -> None:
        original_job_id = self.store.create_job(
            "archive_reprocess",
            metadata={"input_dir": ""},
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            patch("desktop_backend.services.execution_service.os.makedirs") as mocked_makedirs,
            self.assertRaisesRegex(ValueError, "input_dir"),
        ):
            self.service.retry_job(original_job_id)

        mocked_start_operation.assert_not_called()
        mocked_makedirs.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_retry_job_rejects_missing_archive_reprocess_input_dir_before_using_default_root(self) -> None:
        original_job_id = self.store.create_job(
            "archive_reprocess",
            metadata={},
        )
        self.store.finish_job(original_job_id, status="failed", summary={"message": "boom"})

        with (
            patch.object(self.service.write_coordinator, "start_operation") as mocked_start_operation,
            patch("desktop_backend.services.execution_service.os.makedirs") as mocked_makedirs,
            self.assertRaisesRegex(ValueError, "input_dir"),
        ):
            self.service.retry_job(original_job_id)

        mocked_start_operation.assert_not_called()
        mocked_makedirs.assert_not_called()
        self.assertFalse(self.service._active_mutating_jobs)

    def test_launch_manual_import_failed_job_marks_operation_journal_failed(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "manual-import-failure.html")
        Path(source_file).write_text("<html></html>", encoding="utf-8")

        payload = self.service.launch_manual_import(
            {"input_dir": self.temp_dir.name},
            ingest_file=lambda _path: (_ for _ in ()).throw(RuntimeError("ingest boom")),
            start_background_thread=lambda *, name, target: target(),
        )

        self.assertEqual(payload["job_type"], "manual_import")
        operation_rows = self.store.list_operation_journals(limit=10)
        self.assertEqual(operation_rows[0]["operation_type"], "manual_import")
        self.assertEqual(operation_rows[0]["status"], "failed")
        self.assertIn("failed", operation_rows[0]["error"]["code"])

    def test_launch_manual_import_unknown_ingest_state_fails_job_and_operation(self) -> None:
        ready_file = os.path.join(self.temp_dir.name, "manual-import-ready.html")
        unknown_file = os.path.join(self.temp_dir.name, "manual-import-unknown.html")
        Path(ready_file).write_text("<html></html>", encoding="utf-8")
        Path(unknown_file).write_text("<html></html>", encoding="utf-8")

        def ingest(file_path: str) -> dict[str, object]:
            if file_path == ready_file:
                return {
                    "state": "ready",
                    "record_id": "rec-ready",
                    "project_code": "CODE-READY",
                    "archive_path": ready_file,
                }
            return {
                "state": "corrupt_contract_state",
                "record_id": "rec-corrupt",
                "project_code": "CODE-CORRUPT",
                "archive_path": unknown_file,
            }

        payload = self.service.launch_manual_import(
            {"input_dir": self.temp_dir.name},
            ingest_file=ingest,
            start_background_thread=lambda *, name, target: target(),
        )

        job = self.store.get_job(payload["job_id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["summary"]["failure_code"], "invalid_ingest_state")
        self.assertEqual(job["summary"]["failed_count"], 1)
        operation_rows = self.store.list_operation_journals(limit=10)
        self.assertEqual(operation_rows[0]["operation_type"], "manual_import")
        self.assertEqual(operation_rows[0]["status"], "failed")
        self.assertEqual(operation_rows[0]["error"]["code"], "invalid_ingest_state")

    def test_launch_manual_import_wrapper_exception_marks_operation_failed_and_releases_lock(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "manual-import-wrapper-failure.html")
        Path(source_file).write_text("<html></html>", encoding="utf-8")
        captured_target: dict[str, object] = {}

        def raise_in_get_job(_job_id: str) -> dict[str, object]:
            raise RuntimeError("wrapper get_job boom")

        with patch.object(self.service.repository, "get_job", side_effect=raise_in_get_job):
            payload = self.service.launch_manual_import(
                {"input_dir": self.temp_dir.name},
                ingest_file=lambda _path: {
                    "state": "ready",
                    "record_id": "rec-wrapper-failure",
                    "project_code": "CODE-WRAPPER-FAILURE",
                    "archive_path": source_file,
                },
                start_background_thread=lambda *, name, target: captured_target.setdefault("target", target),
            )
            with self.assertRaisesRegex(RuntimeError, "wrapper get_job boom"):
                captured_target["target"]()

        self.assertEqual(payload["job_type"], "manual_import")
        operation_rows = self.store.list_operation_journals(limit=10)
        self.assertEqual(len(operation_rows), 1)
        self.assertEqual(operation_rows[0]["operation_type"], "manual_import")
        self.assertEqual(operation_rows[0]["status"], "failed")
        self.assertEqual(operation_rows[0]["error"]["message"], "wrapper get_job boom")
        self.assertFalse(self.service._active_mutating_jobs)

    def test_launch_manual_import_rejects_non_mapping_final_job_summary(self) -> None:
        source_file = os.path.join(self.temp_dir.name, "manual-import-bad-summary.html")
        Path(source_file).write_text("<html></html>", encoding="utf-8")
        captured_target: dict[str, object] = {}
        final_job = {
            "job_id": "job-manual-bad-summary",
            "job_type": "manual_import",
            "status": "success",
            "summary": [],
        }

        with patch.object(self.service.repository, "get_job", return_value=final_job):
            payload = self.service.launch_manual_import(
                {"input_dir": self.temp_dir.name},
                ingest_file=lambda _path: {
                    "state": "ready",
                    "record_id": "rec-manual-bad-summary",
                    "project_code": "CODE-MANUAL-BAD-SUMMARY",
                    "archive_path": source_file,
                },
                start_background_thread=lambda *, name, target: captured_target.setdefault("target", target),
            )
            with self.assertRaisesRegex(ValueError, "final_job.summary must be a mapping"):
                captured_target["target"]()

        self.assertEqual(payload["job_type"], "manual_import")
        operation_rows = self.store.list_operation_journals(limit=10)
        self.assertEqual(operation_rows[0]["status"], "failed")
        self.assertFalse(self.service._active_mutating_jobs)

    def test_launch_archive_reprocess_wrapper_exception_marks_operation_failed_and_releases_lock(self) -> None:
        source_file = os.path.join(self.archive_root, "archive-wrapper-failure.html")
        Path(source_file).write_text("<html></html>", encoding="utf-8")
        captured_target: dict[str, object] = {}

        def raise_in_get_job(_job_id: str) -> dict[str, object]:
            raise RuntimeError("archive wrapper get_job boom")

        with patch.object(self.service.repository, "get_job", side_effect=raise_in_get_job):
            payload = self.service.launch_archive_reprocess(
                {},
                ingest_file=lambda _path: {
                    "state": "ready",
                    "record_id": "rec-archive-wrapper-failure",
                    "project_code": "CODE-ARCHIVE-WRAPPER-FAILURE",
                    "archive_path": source_file,
                },
                start_background_thread=lambda *, name, target: captured_target.setdefault("target", target),
            )
            with self.assertRaisesRegex(RuntimeError, "archive wrapper get_job boom"):
                captured_target["target"]()

        self.assertEqual(payload["job_type"], "archive_reprocess")
        operation_rows = self.store.list_operation_journals(limit=10)
        self.assertEqual(len(operation_rows), 1)
        self.assertEqual(operation_rows[0]["operation_type"], "archive_reprocess")
        self.assertEqual(operation_rows[0]["status"], "failed")
        self.assertEqual(operation_rows[0]["error"]["message"], "archive wrapper get_job boom")
        self.assertFalse(self.service._active_mutating_jobs)

    def test_launch_manual_import_start_operation_failure_releases_mutating_lock(self) -> None:
        with patch.object(
            self.service.write_coordinator,
            "start_operation",
            side_effect=TypeError("Object of type _Unserializable is not JSON serializable"),
        ):
            with self.assertRaisesRegex(TypeError, "not JSON serializable"):
                self.service.launch_manual_import({"input_dir": self.temp_dir.name})

        self.assertFalse(self.service._active_mutating_jobs)
        self.service.reserve_mutating_job("manual_import")
        self.assertEqual(self.service._active_mutating_jobs, {"manual_import"})
        self.service.release_mutating_job("manual_import")

    def test_launch_archive_reprocess_start_operation_failure_releases_mutating_lock(self) -> None:
        class _Unserializable:
            pass

        with patch.object(
            self.service.write_coordinator,
            "start_operation",
            side_effect=TypeError("Object of type _Unserializable is not JSON serializable"),
        ):
            with self.assertRaisesRegex(TypeError, "not JSON serializable"):
                self.service.launch_archive_reprocess({"payload": _Unserializable()})

        self.assertFalse(self.service._active_mutating_jobs)
        self.service.reserve_mutating_job("archive_reprocess")
        self.assertEqual(self.service._active_mutating_jobs, {"archive_reprocess"})
        self.service.release_mutating_job("archive_reprocess")

    def test_recover_stale_interrupted_range_lock_and_allow_follow_up_task(self) -> None:
        job_id = self.store.create_job(
            "one_click",
            metadata={
                "start_date": "2026-03-22",
                "end_date": "2026-03-22",
                "exchange": "all",
                "record_family": "listing",
                "business_id": "equity_transfer",
            },
        )
        self.store.start_job(job_id)
        self.store.interrupt_running_jobs(reason="range execution terminal interrupted")

        self.service.reserve_mutating_job("one_click")
        self.service.bind_mutating_job("one_click", job_id=job_id, worker_thread_name="dead-range-thread")

        payload = self.service.launch_manual_import({"input_dir": self.temp_dir.name})

        self.assertEqual(payload["job_type"], "manual_import")
        self.assertNotIn("one_click", self.service._active_mutating_jobs)
        self.assertNotIn("one_click", self.service._mutating_job_leases)

    def test_reserve_mutating_job_keeps_live_current_process_work_blocking(self) -> None:
        self.service.reserve_mutating_job("manual_import")

        with self.assertRaises(AppUserFacingError) as context:
            self.service.reserve_mutating_job("one_click")

        self.assertEqual(context.exception.error_code, "mutating_job_in_progress")
        self.assertEqual(context.exception.http_status, 409)
        self.assertEqual(context.exception.details.get("active_job_type"), "manual_import")
        self.assertIn("manual_import", self.service._active_mutating_jobs)
        self.service.release_mutating_job("manual_import")

    def test_reserve_mutating_job_rejects_false_job_type_instead_of_reserving_task(self) -> None:
        with self.assertRaisesRegex(ValueError, "job_type"):
            self.service.reserve_mutating_job(False)  # type: ignore[arg-type]

        self.assertNotIn("task", self.service._active_mutating_jobs)
        self.assertNotIn("task", self.service._mutating_job_leases)

    def test_release_mutating_job_rejects_false_job_type_instead_of_releasing_task(self) -> None:
        self.service.reserve_mutating_job("task")

        with self.assertRaisesRegex(ValueError, "job_type"):
            self.service.release_mutating_job(False)  # type: ignore[arg-type]

        self.assertIn("task", self.service._active_mutating_jobs)
        self.assertIn("task", self.service._mutating_job_leases)
        self.service.release_mutating_job("task")

    def test_stale_worker_cannot_release_newer_mutating_job_lease(self) -> None:
        self.service.reserve_mutating_job("manual_import")
        self.service.bind_mutating_job("manual_import", job_id="job-old")
        self.assertTrue(self.service.release_mutating_job("manual_import", job_id="job-old"))

        self.service.reserve_mutating_job("manual_import")
        self.service.bind_mutating_job("manual_import", job_id="job-new")

        self.assertFalse(self.service.release_mutating_job("manual_import", job_id="job-old"))
        self.assertIn("manual_import", self.service._active_mutating_jobs)
        self.assertEqual(
            self.service._mutating_job_leases["manual_import"]["job_id"],
            "job-new",
        )
        self.assertTrue(self.service.release_mutating_job("manual_import", job_id="job-new"))

    def test_mutating_job_lease_rejects_rebinding_to_different_job(self) -> None:
        self.service.reserve_mutating_job("manual_import")
        self.service.bind_mutating_job("manual_import", job_id="job-1")

        with self.assertRaisesRegex(RuntimeError, "already bound"):
            self.service.bind_mutating_job("manual_import", job_id="job-2")

        self.assertEqual(
            self.service._mutating_job_leases["manual_import"]["job_id"],
            "job-1",
        )
        self.service.release_mutating_job("manual_import", job_id="job-1")

    def test_bind_mutating_job_rejects_unreserved_job_type_instead_of_silent_noop(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "mutating job lease"):
            self.service.bind_mutating_job("one_click", job_id="job-1")

        self.assertNotIn("one_click", self.service._active_mutating_jobs)
        self.assertNotIn("one_click", self.service._mutating_job_leases)

    def test_thread_job_scope_rejects_false_job_type_instead_of_using_task(self) -> None:
        with self.assertRaisesRegex(ValueError, "job_type"):
            with self.service.thread_job_scope(False):  # type: ignore[arg-type]
                pass

        self.assertNotIn("task", self.service.thread_job_stack())

    def test_current_thread_holds_mutating_job_rejects_false_job_type_instead_of_checking_task(self) -> None:
        with self.service.thread_job_scope("task"):
            with self.assertRaisesRegex(ValueError, "job_type"):
                self.service.current_thread_holds_mutating_job(False)  # type: ignore[arg-type]

    def test_run_export_with_contract_does_not_invoke_archive_repair_hook(self) -> None:
        repair_calls: list[str] = []
        self.service.repair_missing_archives_once = lambda: repair_calls.append("called")

        payload = self.service.run_export_with_contract(
            {
                "scope": {
                    "record_family": "listing",
                    "business_id": "all",
                    "state": "all",
                    "exchange": "all",
                    "keyword": "",
                    "date_from": "",
                    "date_to": "",
                },
                "requested_export_mode": "full",
                "output_dir": self.export_root,
            },
            run_ready_export_fn=lambda store, request: _FakeExportResult(
                export_id="export-no-repair",
                cursor_id=request.cursor_id,
                artifacts=[_FakeExportArtifact(file_path=os.path.join(self.export_root, "export.xlsx"))],
            ),
            count_scope_fn=lambda store, request: {},
        )

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(repair_calls, [])

    def test_launch_one_click_does_not_invoke_archive_repair_hook(self) -> None:
        repair_calls: list[str] = []
        self.service.repair_missing_archives_once = lambda: repair_calls.append("called")

        def fake_pipeline(
            _args,
            *,
            config_obj,
            emit_console,
            job_created_callback,
            job_type,
            archive_root,
            export_root,
            auto_export,
            job_id=None,
        ):
            job_created_callback(job_id, self.db_path)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_pipeline):
            payload = self.service.launch_one_click(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "all",
                },
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(payload["job_type"], "one_click")
        self.assertEqual(repair_calls, [])

    def test_run_export_with_contract_exposes_field_missing_diagnostics(self) -> None:
        payload = self.service.run_export_with_contract(
            {
                "scope": {
                    "record_family": "listing",
                    "business_id": "all",
                    "state": "all",
                    "exchange": "all",
                    "keyword": "",
                    "date_from": "",
                    "date_to": "",
                },
                "requested_export_mode": "full",
                "output_dir": self.export_root,
            },
            run_ready_export_fn=lambda store, request: _FakeExportResult(
                export_id="export-empty-skipped",
                cursor_id=request.cursor_id,
                artifacts=[],
                new_records=0,
                changed_records=0,
                field_missing_blocked_records=2,
                field_missing_diagnostics=[
                    {
                        "record_id": "rec-missing",
                        "revision_id": 7,
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "failure_code": "export_field_missing",
                        "missing_fields": [
                            {
                                "kind": "export",
                                "field": "类型",
                                "canonical_field": "source_type",
                                "export_field": "类型",
                                "message": "export field 类型 is required",
                            }
                        ],
                    }
                ],
            ),
            count_scope_fn=lambda store, request: {"field_missing": 2},
        )

        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["empty_reason_code"], "field_missing_blocked_records")
        self.assertEqual(payload["field_missing_blocked_records"], 2)
        self.assertEqual(payload["field_missing_diagnostics"][0]["record_id"], "rec-missing")
        self.assertNotIn("incomplete_diagnostics", payload)

    def test_run_export_with_contract_rejects_non_list_field_missing_diagnostics(self) -> None:
        with self.assertRaisesRegex(ValueError, "export_result.field_missing_diagnostics must be a list"):
            self.service.run_export_with_contract(
                {
                    "scope": {
                        "record_family": "listing",
                        "business_id": "all",
                        "state": "all",
                        "exchange": "all",
                        "keyword": "",
                        "date_from": "",
                        "date_to": "",
                    },
                    "requested_export_mode": "full",
                    "output_dir": self.export_root,
                },
                run_ready_export_fn=lambda store, request: _FakeExportResult(
                    export_id="export-bad-diagnostics",
                    cursor_id=request.cursor_id,
                    artifacts=[_FakeExportArtifact(file_path=os.path.join(self.export_root, "export.xlsx"))],
                    field_missing_diagnostics="not-a-list",  # type: ignore[arg-type]
                ),
                count_scope_fn=lambda store, request: {},
            )

    def test_run_export_with_contract_real_run_ready_export_reports_field_missing_blocked_records(self) -> None:
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-incomplete-export",
                revision_hash="hash-incomplete-export",
                project_code="G32026SH1999001",
                project_name="导出字段不完整记录",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-20",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/incomplete-export.html",
                archive_path=f"{self.temp_dir.name}/archive/incomplete-export.html",
                parser_payload={
                    "项目编号": "G32026SH1999001",
                    "项目名称": "导出字段不完整记录",
                    "项目类型": "股权转让",
                },
                postprocess_payload={
                    "项目编号": "G32026SH1999001",
                    "项目名称": "导出字段不完整记录",
                    "项目类型": "股权转让",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "business_id": "equity_transfer",
                        "raw_business_label": "股权转让",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SH1999001",
                        "project_name": "导出字段不完整记录",
                        "project_type": "股权转让",
                        "status": "挂牌中",
                        "exchange": "sse",
                        "start_date": "2026-04-20",
                    },
                },
                canonical_projection={
                    "项目编号": "G32026SH1999001",
                    "项目名称": "导出字段不完整记录",
                    "项目类型": "股权转让",
                    "项目状态": "挂牌中",
                    "挂牌开始日期": "2026-04-20",
                },
                findings=[],
                record_family="listing",
                source_identity={
                    "record_family": "listing",
                    "source_id": "sse",
                    "business_id": "equity_transfer",
                    "project_code": "G32026SH1999001",
                    "project_name": "导出字段不完整记录",
                    "listing_date": "2026-04-20",
                },
            )
        )
        request_payload = {
            "scope": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "state": "all",
                "exchange": "all",
                "keyword": "",
                "date_from": "2026-04-20",
                "date_to": "2026-04-20",
            },
            "requested_export_mode": "full",
            "output_dir": self.export_root,
        }

        direct_result = run_ready_export(
            self.store,
            ExportRequest(
                date_from="2026-04-20",
                date_to="2026-04-20",
                business_types=["equity_transfer"],
                exchange="all",
                requested_state="all",
                keyword="",
                requested_export_mode="full",
                output_dir=self.export_root,
                record_family="listing",
            ),
        )
        self.assertEqual(direct_result.new_records, 0)
        self.assertGreater(direct_result.field_missing_blocked_records, 0)
        self.assertGreater(len(direct_result.field_missing_diagnostics), 0)

        payload = self.service.run_export_with_contract(
            request_payload,
            run_ready_export_fn=run_ready_export,
            count_scope_fn=count_records_in_export_scope_by_state,
        )

        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["empty_reason_code"], "field_missing_blocked_records")
        self.assertGreater(payload["field_missing_blocked_records"], 0)
        self.assertEqual(payload["field_missing_diagnostics"][0]["record_id"], "rec-incomplete-export")
        self.assertEqual(payload["field_missing_diagnostics"][0]["failure_code"], "canonical_field_missing")

    def test_run_export_with_contract_ready_corrupt_business_identity_is_not_no_matching_records(self) -> None:
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-ready-broken-business-identity",
                revision_hash="hash-ready-broken-business-identity",
                project_code="G32026SH1999003",
                project_name="业务身份损坏记录",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-22",
                state="ready",
                source_file=f"{self.temp_dir.name}/raw/ready-broken-business-identity.html",
                archive_path=f"{self.temp_dir.name}/archive/ready-broken-business-identity.html",
                parser_payload={"项目编号": "G32026SH1999003", "项目名称": "业务身份损坏记录"},
                postprocess_payload={"项目编号": "G32026SH1999003", "项目名称": "业务身份损坏记录"},
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "business_id": "equity_transfer",
                        "raw_business_label": "股权转让",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SH1999003",
                        "project_name": "业务身份损坏记录",
                        "project_type": "股权转让",
                        "status": "挂牌中",
                        "exchange": "sse",
                        "start_date": "2026-04-22",
                        "price": "108.00",
                        "seller": "上海测试公司",
                    },
                },
                canonical_projection={
                    "项目编号": "G32026SH1999003",
                    "项目名称": "业务身份损坏记录",
                    "项目类型": "股权转让",
                    "项目状态": "挂牌中",
                    "挂牌开始日期": "2026-04-22",
                    "挂牌价格": "108.00",
                    "转让方": "上海测试公司",
                },
                findings=[],
                record_family="listing",
                source_identity={
                    "record_family": "listing",
                    "source_id": "sse",
                    "business_id": "equity_transfer",
                    "project_code": "G32026SH1999003",
                    "project_name": "业务身份损坏记录",
                    "listing_date": "2026-04-22",
                },
            )
        )
        corrupted_canonical_record = {
            "record_family": "listing",
            "business_identity": "oops",
            "canonical_fields": {
                "project_code": "G32026SH1999003",
                "project_name": "业务身份损坏记录",
                "project_type": "股权转让",
                "status": "挂牌中",
                "exchange": "sse",
                "start_date": "2026-04-22",
                "price": "108.00",
                "seller": "上海测试公司",
            },
        }
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT latest_revision_id FROM records WHERE record_id = ?",
                ("rec-ready-broken-business-identity",),
            ).fetchone()
            conn.execute(
                "UPDATE records SET business_id = '' WHERE record_id = ?",
                ("rec-ready-broken-business-identity",),
            )
            conn.execute(
                "UPDATE record_revisions SET canonical_record_json = ? WHERE revision_id = ?",
                (
                    json.dumps(corrupted_canonical_record, ensure_ascii=False, sort_keys=True),
                    int(row["latest_revision_id"]),
                ),
            )

        payload = self.service.run_export_with_contract(
            {
                "scope": {
                    "record_family": "listing",
                    "business_id": "all",
                    "state": "all",
                    "exchange": "all",
                    "keyword": "",
                    "date_from": "2026-04-22",
                    "date_to": "2026-04-22",
                },
                "requested_export_mode": "full",
                "output_dir": self.export_root,
            },
            run_ready_export_fn=run_ready_export,
            count_scope_fn=count_records_in_export_scope_by_state,
        )

        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["empty_reason_code"], "field_missing_blocked_records")
        self.assertEqual(payload["scope_state_counts"], {"ready": 1})
        self.assertEqual(payload["field_missing_blocked_records"], 1)
        self.assertEqual(payload["field_missing_diagnostics"][0]["record_id"], "rec-ready-broken-business-identity")
        self.assertEqual(payload["field_missing_diagnostics"][0]["failure_code"], "invalid_identity_shape")
        self.assertEqual(self.store.list_exports(limit=10), [])

    def test_run_export_with_contract_field_missing_only_scope_is_blocked_not_no_matching_records(self) -> None:
        self.store.upsert_record(
            IngestedRecord(
                record_id="rec-field-missing-contract",
                revision_hash="hash-field-missing-contract",
                project_code="G32026SH1999002",
                project_name="缺字段合同记录",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-21",
                state="field_missing",
                source_file=f"{self.temp_dir.name}/raw/field-missing-contract.html",
                archive_path=f"{self.temp_dir.name}/archive/field-missing-contract.html",
                parser_payload={"项目编号": "G32026SH1999002"},
                postprocess_payload={"项目编号": "G32026SH1999002"},
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "business_id": "equity_transfer",
                        "raw_business_label": "股权转让",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SH1999002",
                        "project_name": "缺字段合同记录",
                        "project_type": "股权转让",
                    },
                },
                findings=[],
                record_family="listing",
                source_identity={
                    "record_family": "listing",
                    "source_id": "sse",
                    "business_id": "equity_transfer",
                },
            )
        )

        payload = self.service.run_export_with_contract(
            {
                "scope": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "state": "all",
                    "exchange": "all",
                    "keyword": "",
                    "date_from": "2026-04-21",
                    "date_to": "2026-04-21",
                },
                "requested_export_mode": "full",
                "output_dir": self.export_root,
            },
            run_ready_export_fn=run_ready_export,
            count_scope_fn=count_records_in_export_scope_by_state,
        )

        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["empty_reason_code"], "field_missing_blocked_records")
        self.assertEqual(payload["scope_state_counts"], {"field_missing": 1})
        self.assertEqual(payload["field_missing_blocked_records"], 1)
        self.assertEqual(self.store.list_exports(limit=10), [])


if __name__ == "__main__":
    unittest.main()
