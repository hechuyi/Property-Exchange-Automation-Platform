from __future__ import annotations

import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppUserFacingError
from desktop_backend.error_codes import ERROR_INVALID_REQUEST
from desktop_backend.job_contract import build_job_view
from desktop_backend.request_contract import normalize_one_click_request
from desktop_backend.services.execution_service import ExecutionService
from desktop_backend.services.runtime_service import RuntimeService
from desktop_backend.services.settings_service import SettingsService
from peap.streaming_models import ItemProgressEvent
from peap.streaming_store import StreamingStore


class _FakeRuntimeDependencies:
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


class ExecutionDownloadServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config = AppConfig.from_env(project_root=self.temp_dir.name)
        self.db_path = os.path.join(self.temp_dir.name, "streaming.sqlite3")
        self.store = StreamingStore(self.db_path, auto_migrate=True)
        self.runtime_service = RuntimeService(
            config_obj=self.config,
            store=self.store,
            runtime_dependencies=_FakeRuntimeDependencies(),
        )
        self.service = ExecutionService(
            config_obj=self.config,
            store=self.store,
            db_path=self.db_path,
            runtime_service=self.runtime_service,
            get_basic_settings=lambda: {
                "archive_root": os.path.join(self.temp_dir.name, "archive"),
                "export_root": os.path.join(self.temp_dir.name, "exports"),
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
            },
            get_advanced_settings=lambda: {
                "save_json": False,
                "postprocess_config": "",
                "raw_manual_root": "",
            },
            run_store_maintenance=lambda: None,
            repair_missing_archives_once=lambda: None,
            build_ingest_runner=lambda archive_root=None: None,
            user_error_cls=AppUserFacingError,
        )

    def test_launch_streaming_job_passes_family_business_scope_to_pipeline(self) -> None:
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
            captured["record_family"] = args.record_family
            captured["business_id"] = args.business_id
            job_created_callback(job_id, self.db_path)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_pipeline):
            self.service.launch_streaming_job(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "beijing",
                    "record_family": "listing",
                    "business_id": "physical_asset",
                },
                job_type="one_click",
                auto_export=False,
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(captured["record_family"], "listing")
        self.assertEqual(captured["business_id"], "physical_asset")

    def test_launch_streaming_job_acknowledges_persisted_job_before_pipeline_bootstrap(self) -> None:
        pipeline_entered = threading.Event()
        release_pipeline = threading.Event()
        threads: list[threading.Thread] = []

        def fake_pipeline(*args, **kwargs):
            pipeline_entered.set()
            release_pipeline.wait(timeout=2.0)
            return None

        def launch_thread(*, name, target) -> None:
            thread = threading.Thread(name=name, target=target, daemon=True)
            threads.append(thread)
            thread.start()

        self.service._startup_handshake_timeout_sec = 0.05
        try:
            with (
                patch(
                    "peap.streaming_daily_pipeline.run_streaming_daily_pipeline",
                    side_effect=fake_pipeline,
                ),
                patch.object(
                    self.service,
                    "run_store_maintenance",
                    side_effect=AssertionError("launch request must not run full store maintenance"),
                ),
            ):
                response = self.service.launch_streaming_job(
                    {
                        "start_date": "2026-03-22",
                        "end_date": "2026-03-22",
                        "exchange": "beijing",
                        "record_family": "listing",
                        "business_id": "physical_asset",
                    },
                    job_type="one_click",
                    auto_export=False,
                    start_background_thread=launch_thread,
                )
        finally:
            release_pipeline.set()
            for thread in threads:
                thread.join(timeout=2.0)

        self.assertTrue(pipeline_entered.is_set())
        self.assertEqual(response["job_type"], "one_click")
        self.assertEqual(self.store.get_job(response["job_id"])["status"], "starting")

    def test_launch_streaming_job_rejects_unknown_business_scope(self) -> None:
        with self.assertRaises(AppUserFacingError) as context:
            self.service.launch_streaming_job(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "beijing",
                    "record_family": "listing",
                    "business_id": "unknown_business",
                },
                job_type="one_click",
                auto_export=False,
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(context.exception.error_code, ERROR_INVALID_REQUEST)
        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(
            context.exception.details["scope"],
            {
                "exchange": "cbex",
                "record_family": "listing",
                "business_id": "unknown_business",
            },
        )

    def test_launch_streaming_job_defaults_missing_record_family_to_listing(self) -> None:
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
            captured["record_family"] = args.record_family
            captured["business_id"] = args.business_id
            job_created_callback(job_id, self.db_path)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_pipeline):
            self.service.launch_streaming_job(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "beijing",
                    "business_id": "physical_asset",
                },
                job_type="one_click",
                auto_export=False,
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(captured["record_family"], "listing")
        self.assertEqual(captured["business_id"], "physical_asset")

    def test_launch_streaming_job_rejects_false_scope_fields_before_pipeline_launch(self) -> None:
        base_payload = {
            "start_date": "2026-03-22",
            "end_date": "2026-03-22",
            "exchange": "beijing",
            "record_family": "listing",
            "business_id": "physical_asset",
        }
        for field_name in ("exchange", "record_family", "business_id"):
            with self.subTest(field_name=field_name):
                payload = {**base_payload, field_name: False}
                with (
                    patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=AssertionError("unexpected pipeline launch")),
                    self.assertRaisesRegex((AppUserFacingError, ValueError), field_name),
                ):
                    self.service.launch_streaming_job(
                        payload,
                        job_type="one_click",
                        auto_export=False,
                        start_background_thread=lambda *, name, target: target(),
                    )

    def test_launch_streaming_job_allows_deal_scope_and_preserves_family_business_args(self) -> None:
        captured_scopes: list[tuple[str, str, str]] = []

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
            captured_scopes.append((str(args.exchange), str(args.record_family), str(args.business_id)))
            job_created_callback(job_id, self.db_path)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_pipeline) as pipeline:
            for exchange in ("sse", "cbex"):
                response = self.service.launch_streaming_job(
                    {
                        "start_date": "2026-03-22",
                        "end_date": "2026-03-22",
                        "exchange": exchange,
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                    },
                    job_type="one_click",
                    auto_export=False,
                    start_background_thread=lambda *, name, target: target(),
                )
                self.assertEqual(response["job_type"], "one_click")

        self.assertEqual(
            captured_scopes,
            [
                ("sse", "deal", "deal_equity_transfer"),
                ("cbex", "deal", "deal_equity_transfer"),
            ],
        )
        self.assertEqual(pipeline.call_count, 2)

    def test_launch_streaming_job_exposes_startup_fail_job_persistence_error(self) -> None:
        with (
            patch(
                "peap.streaming_daily_pipeline.run_streaming_daily_pipeline",
                side_effect=RuntimeError("pipeline-startup-broken"),
            ),
            patch.object(
                self.service.repository,
                "fail_job",
                side_effect=RuntimeError("fail-job-broken"),
            ),
            self.assertRaisesRegex(RuntimeError, "fail-job-broken"),
        ):
            self.service.launch_streaming_job(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "beijing",
                    "record_family": "listing",
                    "business_id": "physical_asset",
                },
                job_type="one_click",
                auto_export=False,
                start_background_thread=lambda *, name, target: target(),
            )

    def test_launch_streaming_job_exposes_startup_create_job_persistence_error(self) -> None:
        original_create_job = self.service.repository.create_job
        create_job_calls = 0

        def create_job_then_break(*args, **kwargs):
            nonlocal create_job_calls
            create_job_calls += 1
            if create_job_calls == 1:
                return original_create_job(*args, **kwargs)
            raise RuntimeError("create-job-broken")

        with (
            patch(
                "peap.streaming_daily_pipeline.run_streaming_daily_pipeline",
                side_effect=RuntimeError("pipeline-startup-broken"),
            ),
            patch.object(self.service.repository, "get_job", side_effect=KeyError("missing-job")),
            patch.object(self.service.repository, "create_job", side_effect=create_job_then_break),
            self.assertRaisesRegex(RuntimeError, "create-job-broken"),
        ):
            self.service.launch_streaming_job(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "beijing",
                    "record_family": "listing",
                    "business_id": "physical_asset",
                },
                job_type="one_click",
                auto_export=False,
                start_background_thread=lambda *, name, target: target(),
            )

    def test_run_export_runtime_error_raises_user_facing_failure_instead_of_returning_success_payload(self) -> None:
        def fail_export(_store, _request):
            raise RuntimeError("xlsx writer crashed")

        with self.assertRaises(AppUserFacingError) as context:
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
                    "output_dir": os.path.join(self.temp_dir.name, "exports"),
                },
                run_ready_export_fn=fail_export,
                count_scope_fn=lambda _store, _request: {},
            )

        self.assertEqual(context.exception.error_code, "export_failed")
        self.assertEqual(context.exception.http_status, 500)
        self.assertIn("xlsx writer crashed", context.exception.message)

        jobs = self.store.list_jobs(limit=10)
        self.assertEqual(jobs[0]["status"], "failed")
        self.assertEqual(jobs[0]["summary"]["failure_code"], "export_failed")

        operations = self.store.list_operation_journals(limit=10)
        self.assertEqual(operations[0]["operation_type"], "export_excel")
        self.assertEqual(operations[0]["status"], "failed")
        self.assertEqual(operations[0]["error"]["code"], "export_failed")

    def test_run_export_without_output_dir_rejects_corrupt_basic_settings_before_export(self) -> None:
        settings_service = SettingsService(
            config_obj=self.config,
            store=self.store,
            app_home=self.temp_dir.name,
            default_archive_root=os.path.join(self.temp_dir.name, "archive-default"),
            default_export_root=os.path.join(self.temp_dir.name, "exports-default"),
        )
        self.store.set_setting(
            "app.settings.basic",
            {
                "archive_root": os.path.join(self.temp_dir.name, "archive-configured"),
                "export_root": os.path.join(self.temp_dir.name, "exports-configured"),
                "retention_count": 3,
            },
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE settings SET value_json = ? WHERE key = ?",
                ("{not valid json", "app.settings.basic"),
            )
        self.service.get_basic_settings = settings_service.get_basic_settings
        called = False

        def fail_if_called(_store, _request):
            nonlocal called
            called = True
            self.fail("run_ready_export_fn must not be called when basic settings are corrupt")

        with self.assertRaises(AppUserFacingError) as context:
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
                },
                run_ready_export_fn=fail_if_called,
                count_scope_fn=lambda _store, _request: {},
            )

        self.assertFalse(called)
        self.assertEqual(context.exception.error_code, ERROR_INVALID_REQUEST)
        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(context.exception.details["reason"], "settings_payload_corrupt")

    def test_run_export_with_output_dir_rejects_corrupt_basic_settings_before_export(self) -> None:
        settings_service = SettingsService(
            config_obj=self.config,
            store=self.store,
            app_home=self.temp_dir.name,
            default_archive_root=os.path.join(self.temp_dir.name, "archive-default"),
            default_export_root=os.path.join(self.temp_dir.name, "exports-default"),
        )
        self.store.set_setting(
            "app.settings.basic",
            {
                "archive_root": os.path.join(self.temp_dir.name, "archive-configured"),
                "export_root": os.path.join(self.temp_dir.name, "exports-configured"),
                "retention_count": 3,
            },
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE settings SET value_json = ? WHERE key = ?",
                ("{not valid json", "app.settings.basic"),
            )
        self.service.get_basic_settings = settings_service.get_basic_settings
        called = False

        def fail_if_called(_store, _request):
            nonlocal called
            called = True
            self.fail("run_ready_export_fn must not be called when basic settings are corrupt")

        with self.assertRaises(AppUserFacingError) as context:
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
                    "output_dir": os.path.join(self.temp_dir.name, "explicit-exports"),
                },
                run_ready_export_fn=fail_if_called,
                count_scope_fn=lambda _store, _request: {},
            )

        self.assertFalse(called)
        self.assertEqual(context.exception.error_code, ERROR_INVALID_REQUEST)
        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(context.exception.details["reason"], "settings_payload_corrupt")

    def test_launch_multi_family_one_click_runs_families_inside_single_job(self) -> None:
        captured_calls: list[dict[str, object]] = []

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
            manage_job_lifecycle=True,
        ):
            captured_calls.append(
                {
                    "job_id": job_id,
                    "record_family": args.record_family,
                    "business_id": args.business_id,
                    "manage_job_lifecycle": manage_job_lifecycle,
                }
            )
            self.store.update_job_counts(str(job_id), downloaded_inc=1)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_pipeline):
            response = self.service.launch_one_click(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "sse",
                    "family_scopes": [
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "exchange": "sse",
                        },
                        {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "exchange": "sse",
                        },
                    ],
                },
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(response["job_type"], "one_click")
        self.assertTrue(response["job_id"])
        self.assertEqual(
            [(call["record_family"], call["business_id"]) for call in captured_calls],
            [("listing", "equity_transfer"), ("deal", "deal_equity_transfer")],
        )
        self.assertEqual({call["job_id"] for call in captured_calls}, {response["job_id"]})
        self.assertEqual({call["manage_job_lifecycle"] for call in captured_calls}, {False})
        job = self.store.get_job(response["job_id"])
        self.assertEqual(job["status"], "success")
        self.assertNotEqual(job["metadata"].get("record_family"), "all")
        self.assertEqual(job["metadata"].get("record_families"), ["listing", "deal"])
        self.assertEqual(
            job["metadata"].get("family_scopes"),
            [
                {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "business_label": "股权转让",
                    "exchange": "sse",
                },
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "business_label": "股权转让成交",
                    "exchange": "sse",
                },
            ],
        )
        job_view = build_job_view(job, progress=self.service.build_latest_progress(job))
        self.assertEqual(job_view["record_family"], "")
        self.assertEqual(job_view["progress"]["record_family"], "")
        self.assertEqual(job["downloaded_count"], 2)
        self.assertEqual(len(self.store.list_jobs(limit=10)), 1)

    def test_launch_multi_family_streaming_job_rejects_false_scope_fields_before_pipeline_launch(self) -> None:
        base_payload = {
            "start_date": "2026-03-22",
            "end_date": "2026-03-22",
            "exchange": "sse",
        }
        base_scope = {
            "record_family": "listing",
            "business_id": "equity_transfer",
            "exchange": "sse",
        }
        cases = (
            ("exchange", {**base_payload, "exchange": False}, [base_scope]),
            ("record_family", base_payload, [{**base_scope, "record_family": False}]),
            ("business_id", base_payload, [{**base_scope, "business_id": False}]),
            ("exchange", base_payload, [{**base_scope, "exchange": False}]),
        )
        for field_name, payload, family_scopes in cases:
            with self.subTest(field_name=field_name, family_scopes=family_scopes):
                with (
                    patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=AssertionError("unexpected pipeline launch")),
                    self.assertRaisesRegex((AppUserFacingError, ValueError), field_name),
                ):
                    self.service.launch_multi_family_streaming_job(
                        payload,
                        family_scopes=family_scopes,
                        job_type="one_click",
                        auto_export=False,
                        start_background_thread=lambda *, name, target: target(),
                    )

    def test_launch_one_click_treats_single_record_families_entry_as_scalar_family_scope(self) -> None:
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
            captured["record_family"] = args.record_family
            captured["business_id"] = args.business_id
            job_created_callback(job_id, self.db_path)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_pipeline):
            response = self.service.launch_one_click(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "sse",
                    "business_id": "deal_equity_transfer",
                    "record_families": ["deal"],
                },
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(response["job_type"], "one_click")
        self.assertEqual(captured["record_family"], "deal")
        self.assertEqual(captured["business_id"], "deal_equity_transfer")

    def test_normalize_one_click_treats_single_family_scope_with_matching_top_level_exchange_as_scalar_scope(self) -> None:
        normalized = normalize_one_click_request(
            {
                "start_date": "2026-03-22",
                "end_date": "2026-03-22",
                "exchange": "sse",
                "family_scopes": [
                    {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "exchange": "sse",
                    },
                ],
            },
            basic_settings=self.service.get_basic_settings(),
            advanced_settings=self.service.get_advanced_settings(),
        )

        self.assertNotIn("family_scopes", normalized)
        self.assertEqual(normalized["exchange"], "sse")
        self.assertEqual(normalized["record_family"], "deal")
        self.assertEqual(normalized["business_id"], "deal_equity_transfer")

    def test_normalize_one_click_rejects_single_family_scope_conflicting_top_level_exchange(self) -> None:
        with self.assertRaisesRegex(ValueError, "family_scopes.*exchange"):
            normalize_one_click_request(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "cbex",
                    "family_scopes": [
                        {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "exchange": "sse",
                        },
                    ],
                },
                basic_settings=self.service.get_basic_settings(),
                advanced_settings=self.service.get_advanced_settings(),
            )

    def test_normalize_one_click_accepts_single_family_scope_with_equivalent_top_level_aliases(self) -> None:
        normalized = normalize_one_click_request(
            {
                "start_date": "2026-03-22",
                "end_date": "2026-03-22",
                "record_family": "LISTING",
                "business_id": "股权转让",
                "exchange": "shanghai",
                "family_scopes": [
                    {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "exchange": "sse",
                    },
                ],
            },
            basic_settings=self.service.get_basic_settings(),
            advanced_settings=self.service.get_advanced_settings(),
        )

        self.assertEqual(normalized["record_family"], "listing")
        self.assertEqual(normalized["business_id"], "equity_transfer")
        self.assertEqual(normalized["exchange"], "sse")

    def test_normalize_one_click_rejects_multi_family_scopes_conflicting_top_level_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "family_scopes.*business_id"):
            normalize_one_click_request(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "cbex",
                    "business_id": "physical_asset",
                    "family_scopes": [
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "exchange": "sse",
                        },
                        {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "exchange": "sse",
                        },
                    ],
                },
                basic_settings=self.service.get_basic_settings(),
                advanced_settings=self.service.get_advanced_settings(),
            )

    def test_launch_one_click_runs_multi_family_scopes_in_one_job(self) -> None:
        captured_calls: list[dict[str, object]] = []

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
            manage_job_lifecycle=True,
        ):
            captured_calls.append(
                {
                    "job_id": job_id,
                    "record_family": args.record_family,
                    "business_id": args.business_id,
                    "manage_job_lifecycle": manage_job_lifecycle,
                }
            )
            self.store.update_job_counts(str(job_id), downloaded_inc=1)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_pipeline):
            response = self.service.launch_one_click(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "sse",
                    "family_scopes": [
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "exchange": "sse",
                        },
                        {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "exchange": "sse",
                        },
                    ],
                },
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(response["job_type"], "one_click")
        self.assertEqual(
            [(call["record_family"], call["business_id"]) for call in captured_calls],
            [("listing", "equity_transfer"), ("deal", "deal_equity_transfer")],
        )
        self.assertEqual({call["job_id"] for call in captured_calls}, {response["job_id"]})
        self.assertEqual({call["manage_job_lifecycle"] for call in captured_calls}, {False})
        job = self.store.get_job(response["job_id"])
        self.assertEqual(
            job["metadata"].get("family_scopes"),
            [
                {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "business_label": "股权转让",
                    "exchange": "sse",
                },
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "business_label": "股权转让成交",
                    "exchange": "sse",
                },
            ],
        )

    def test_launch_download_ingest_runs_multi_family_scopes_in_one_job(self) -> None:
        captured_calls: list[dict[str, object]] = []

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
            manage_job_lifecycle=True,
        ):
            captured_calls.append(
                {
                    "job_id": job_id,
                    "job_type": job_type,
                    "record_family": args.record_family,
                    "business_id": args.business_id,
                    "manage_job_lifecycle": manage_job_lifecycle,
                }
            )
            self.store.update_job_counts(str(job_id), downloaded_inc=1)
            return None

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_pipeline):
            response = self.service.launch_download_ingest(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "sse",
                    "family_scopes": [
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "exchange": "sse",
                        },
                        {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "exchange": "sse",
                        },
                    ],
                },
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(response["job_type"], "download_ingest")
        self.assertEqual(
            [(call["record_family"], call["business_id"]) for call in captured_calls],
            [("listing", "equity_transfer"), ("deal", "deal_equity_transfer")],
        )
        self.assertEqual({call["job_id"] for call in captured_calls}, {response["job_id"]})
        self.assertEqual({call["job_type"] for call in captured_calls}, {"download_ingest"})
        self.assertEqual({call["manage_job_lifecycle"] for call in captured_calls}, {False})
        job = self.store.get_job(response["job_id"])
        self.assertEqual(job["metadata"].get("record_families"), ["listing", "deal"])

    def test_one_click_and_history_include_public_resource_for_same_date_range(self) -> None:
        def fake_pipeline(*_args, **_kwargs):
            return type("Result", (), {"exit_code": 0, "download_result": None})()

        public_result = {
            "unique_selected": 3,
            "workbook": os.path.join(self.temp_dir.name, "exports", "public-resource.xlsx"),
            "records": [],
        }
        payload = {
            "start_date": "2025-12-20",
            "end_date": "2026-01-05",
            "include_public_resource": True,
            "family_scopes": [
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "sse",
                }
            ],
        }

        with patch(
            "peap.streaming_daily_pipeline.run_streaming_daily_pipeline",
            side_effect=fake_pipeline,
        ), patch(
            "scripts.collect_public_resource_deals.collect_date_range",
            return_value=public_result,
        ) as collect:
            for launch, expected_job_type in (
                (self.service.launch_one_click, "one_click"),
                (self.service.launch_download_ingest, "download_ingest"),
            ):
                with self.subTest(job_type=expected_job_type):
                    response = launch(
                        dict(payload),
                        start_background_thread=lambda *, name, target: target(),
                    )
                    call = collect.call_args
                    self.assertEqual(call.args[0].isoformat(), "2025-12-20")
                    self.assertEqual(call.args[1].isoformat(), "2026-01-05")
                    self.assertTrue(call.kwargs["resume"])
                    job = self.store.get_job(response["job_id"])
                    self.assertEqual(response["job_type"], expected_job_type)
                    self.assertEqual(job["metadata"]["include_public_resource"], True)
                    self.assertEqual(job["summary"]["public_resource"]["status"], "success")
                    self.assertEqual(job["summary"]["public_resource"]["record_count"], 3)
                    self.assertEqual(job["downloaded_count"], 3)
                    self.assertEqual(job["persisted_count"], 3)
                    collect.reset_mock()

    def test_multi_family_job_summary_includes_child_download_archive_audits(self) -> None:
        child_audits = {
            "listing": {
                "ok": True,
                "root_count": 1,
                "html_count": 2,
                "sidecar_count": 2,
                "issue_count": 0,
                "issues": [],
                "roots": [],
            },
            "deal": {
                "ok": True,
                "root_count": 1,
                "html_count": 1,
                "sidecar_count": 1,
                "issue_count": 0,
                "issues": [],
                "roots": [],
            },
        }

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
            manage_job_lifecycle=True,
        ):
            self.store.update_job_counts(str(job_id), downloaded_inc=1, persisted_inc=1)
            return type(
                "Result",
                (),
                {
                    "exit_code": 0,
                    "download_result": type(
                        "DownloadResult",
                        (),
                        {"archive_audit": child_audits[args.record_family]},
                    )(),
                },
            )()

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_pipeline):
            response = self.service.launch_download_ingest(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "sse",
                    "family_scopes": [
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "exchange": "sse",
                        },
                        {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "exchange": "sse",
                        },
                    ],
                },
                start_background_thread=lambda *, name, target: target(),
            )

        job = self.store.get_job(response["job_id"])
        archive_audit = job["summary"]["download_archive_audit"]
        self.assertTrue(archive_audit["ok"])
        self.assertEqual(archive_audit["scope_count"], 2)
        self.assertEqual(archive_audit["html_count"], 3)
        self.assertEqual(archive_audit["sidecar_count"], 3)
        self.assertEqual(archive_audit["issue_count"], 0)
        self.assertEqual(
            [
                (item["record_family"], item["business_id"], item["audit"])
                for item in archive_audit["scopes"]
            ],
            [
                ("listing", "equity_transfer", child_audits["listing"]),
                ("deal", "deal_equity_transfer", child_audits["deal"]),
            ],
        )

    def test_launch_multi_family_streaming_job_exposes_startup_fail_job_persistence_error(self) -> None:
        with (
            patch(
                "peap.streaming_daily_pipeline.run_streaming_daily_pipeline",
                side_effect=RuntimeError("pipeline-startup-broken"),
            ),
            patch.object(
                self.service.repository,
                "fail_job",
                side_effect=RuntimeError("fail-job-broken"),
            ),
            self.assertRaisesRegex(RuntimeError, "fail-job-broken"),
        ):
            self.service.launch_multi_family_streaming_job(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "sse",
                },
                family_scopes=[
                    {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "exchange": "sse",
                    },
                    {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "exchange": "sse",
                    },
                ],
                job_type="one_click",
                auto_export=False,
                start_background_thread=lambda *, name, target: target(),
            )

    def test_launch_multi_family_streaming_job_rejects_empty_family_scopes_before_creating_job(self) -> None:
        with (
            patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=AssertionError("unexpected pipeline launch")),
            self.assertRaises(AppUserFacingError) as context,
        ):
            self.service.launch_multi_family_streaming_job(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "sse",
                },
                family_scopes=[],
                job_type="one_click",
                auto_export=False,
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(context.exception.error_code, ERROR_INVALID_REQUEST)
        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(self.store.list_jobs(limit=10), [])

    def test_launch_multi_family_streaming_job_maps_unknown_scope_to_invalid_request(self) -> None:
        with (
            patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=AssertionError("unexpected pipeline launch")),
            self.assertRaises(AppUserFacingError) as context,
        ):
            self.service.launch_multi_family_streaming_job(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "sse",
                },
                family_scopes=[
                    {
                        "record_family": "listing",
                        "business_id": "unknown_business",
                        "exchange": "sse",
                    },
                ],
                job_type="one_click",
                auto_export=False,
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(context.exception.error_code, ERROR_INVALID_REQUEST)
        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(
            context.exception.details["scope"],
            {
                "exchange": "sse",
                "record_family": "listing",
                "business_id": "unknown_business",
            },
        )

    def test_launch_multi_family_one_click_fails_when_pipeline_records_failed_event_with_zero_exit(self) -> None:
        class _PipelineResult:
            def __init__(self, *, exit_code: int) -> None:
                self.exit_code = exit_code

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
            manage_job_lifecycle=True,
        ):
            if args.record_family == "listing":
                self.store.append_event(
                    ItemProgressEvent(
                        job_id=str(job_id),
                        stage="prepare_tasks",
                        status="failed",
                        error_type="sse_list_failed",
                        error_message="sse: list-failed: read operation timed out",
                        payload={
                            "label": "扫描失败",
                            "error_code": "sse_list_failed",
                            "error_message": "sse: list-failed: read operation timed out",
                        },
                    )
                )
                self.store.append_event(
                    ItemProgressEvent(
                        job_id=str(job_id),
                        stage="save_pages",
                        status="done",
                        payload={
                            "label": "当前没有需要下载的网页，无需下载",
                            "summary": {"detail_candidates": 0, "detail_fetched": 0},
                        },
                    )
                )
            return _PipelineResult(exit_code=0)

        with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_pipeline):
            response = self.service.launch_one_click(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "sse",
                    "family_scopes": [
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "exchange": "sse",
                        },
                        {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "exchange": "sse",
                        },
                    ],
                },
                start_background_thread=lambda *, name, target: target(),
            )

        job = self.store.get_job(response["job_id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["summary"].get("download_exit_code"), 1)
        self.assertEqual(job["summary"].get("failure_code"), "sse_list_failed")
        self.assertEqual(
            job["summary"].get("failure_message"),
            "sse: list-failed: read operation timed out",
        )
        self.assertEqual(job["summary"].get("failure_stage"), "prepare_tasks")

        progress = self.service.build_latest_progress(job)

        self.assertEqual(progress["job_status"], "failed")
        self.assertEqual(progress["phase_code"], "failed")
        self.assertIn("失败", progress["phase_label"])

    def test_launch_one_click_rejects_aggregate_multi_family_business_scope(self) -> None:
        with self.assertRaisesRegex(AppUserFacingError, "family_scopes"):
            self.service.launch_one_click(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "all",
                    "business_id": "all",
                    "record_families": ["listing", "deal"],
                },
                start_background_thread=lambda *, name, target: target(),
            )

    def test_launch_one_click_rejects_invalid_family_scope_entries_instead_of_filtering(self) -> None:
        with self.assertRaisesRegex(AppUserFacingError, "family_scopes") as context:
            self.service.launch_one_click(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "sse",
                    "family_scopes": [
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "exchange": "sse",
                        },
                        {
                            "record_family": "deal",
                            "business_id": "",
                            "exchange": "sse",
                        },
                    ],
                },
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(context.exception.error_code, ERROR_INVALID_REQUEST)
        self.assertEqual(context.exception.http_status, 400)
        self.assertEqual(context.exception.details["family_scopes"][1]["record_family"], "deal")

    def test_launch_one_click_accepts_new_listing_capital_increase_sources(self) -> None:
        for exchange in ("shandong", "guangdong"):
            with self.subTest(exchange=exchange):
                captured: dict[str, object] = {}

                def fake_pipeline(args, *, job_created_callback, job_id=None, _captured=captured, **_kwargs):
                    _captured["exchange"] = args.exchange
                    _captured["record_family"] = args.record_family
                    _captured["business_id"] = args.business_id
                    job_created_callback(job_id, self.db_path)
                    return None

                with patch("peap.streaming_daily_pipeline.run_streaming_daily_pipeline", side_effect=fake_pipeline):
                    response = self.service.launch_one_click(
                        {
                            "start_date": "2026-03-22",
                            "end_date": "2026-03-22",
                            "exchange": exchange,
                            "record_family": "listing",
                            "business_id": "capital_increase",
                        },
                        start_background_thread=lambda *, name, target: target(),
                    )
                self.assertEqual(response["job_type"], "one_click")
                self.assertEqual(captured["exchange"], exchange)
                self.assertEqual(captured["record_family"], "listing")
                self.assertEqual(captured["business_id"], "capital_increase")

    def test_launch_streaming_job_rejects_deal_physical_exchange_outside_current_surface_contract(self) -> None:
        with self.assertRaises(AppUserFacingError) as context:
            self.service.launch_streaming_job(
                {
                    "start_date": "2026-04-18",
                    "end_date": "2026-04-18",
                    "exchange": "tpre",
                    "record_family": "deal",
                    "business_id": "deal_physical_asset",
                },
                job_type="one_click",
                auto_export=False,
                start_background_thread=lambda *, name, target: target(),
            )

        self.assertEqual(context.exception.error_code, ERROR_INVALID_REQUEST)
        self.assertEqual(
            context.exception.details["scope"],
            {
                "exchange": "tpre",
                "record_family": "deal",
                "business_id": "deal_physical_asset",
            },
        )

    def test_build_latest_progress_keeps_upstream_failure_visible_instead_of_empty_result_label(self) -> None:
        job_id = self.store.create_job(
            "one_click",
            metadata={
                "record_family": "listing",
                "business_id": "physical_asset",
                "exchange": "sse",
            },
        )
        self.store.start_job(job_id)
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="prepare_tasks",
                status="failed",
                error_type="sse_list_failed",
                error_message="sse: list-failed: read operation timed out",
                payload={
                    "label": "扫描失败",
                    "error_code": "sse_list_failed",
                    "error_message": "sse: list-failed: read operation timed out",
                    "summary_payload": {
                        "aggregate_summary": {"detail_candidates": 0, "saved": 0},
                        "task_summaries": {},
                    },
                },
            )
        )
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="save_pages",
                status="done",
                payload={
                    "label": "当前没有需要下载的网页，无需下载",
                    "summary": {"detail_candidates": 0, "detail_fetched": 0},
                },
            )
        )
        self.store.finish_job(
            job_id,
            status="success_with_warnings",
            summary={
                "download_exit_code": 0,
                "downloaded_count": 0,
                "persisted_count": 0,
                "exception_count": 0,
                "failure_code": "sse_list_failed",
                "failure_message": "sse: list-failed: read operation timed out",
                "failure_stage": "prepare_tasks",
            },
        )

        progress = self.service.build_latest_progress(self.store.get_job(job_id))

        self.assertEqual(progress["job_status"], "success_with_warnings")
        self.assertEqual(progress["phase_code"], "completed_with_warnings")
        self.assertNotEqual(progress["phase_label"], "本次未发现新网页")
        self.assertIn("失败", progress["phase_label"])

    def test_build_latest_progress_keeps_date_filtered_zero_candidate_warning_visible(self) -> None:
        job_id = self.store.create_job(
            "one_click",
            metadata={
                "record_family": "listing",
                "business_id": "physical_asset",
                "exchange": "cbex",
            },
        )
        self.store.start_job(job_id)
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="save_pages",
                status="warning",
                payload={
                    "label": "已列出 30 条，30 条因披露日期不在 2026-03-01..2026-03-31 被跳过",
                    "summary": {
                        "listed": 30,
                        "list_date_skipped": 30,
                        "detail_candidates": 0,
                        "saved": 0,
                        "warning_code": "all_listed_rows_outside_date_range",
                        "warning_message": "已列出 30 条，30 条因披露日期不在 2026-03-01..2026-03-31 被跳过",
                    },
                },
            )
        )
        self.store.finish_job(
            job_id,
            status="success_with_warnings",
            summary={
                "download_exit_code": 0,
                "downloaded_count": 0,
                "persisted_count": 0,
                "exception_count": 0,
                "warning_code": "all_listed_rows_outside_date_range",
                "warning_message": "已列出 30 条，30 条因披露日期不在 2026-03-01..2026-03-31 被跳过",
            },
        )

        progress = self.service.build_latest_progress(self.store.get_job(job_id))

        self.assertEqual(progress["job_status"], "success_with_warnings")
        self.assertEqual(progress["phase_code"], "completed_with_warnings")
        self.assertIn("披露日期", progress["phase_label"])
        self.assertNotEqual(progress["phase_label"], "本次未发现新网页")
        self.assertEqual(
            progress["latest_stage_summary"]["warning_code"],
            "all_listed_rows_outside_date_range",
        )

    def test_build_latest_progress_accepts_historical_success_without_warning_fields(self) -> None:
        job_id = self.store.create_job(
            "download_ingest",
            metadata={
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "all",
            },
        )
        self.store.start_job(job_id)
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="save_pages",
                status="done",
                payload={
                    "label": "当前没有需要下载的网页，无需下载",
                    "summary": {"detail_candidates": 0, "detail_fetched": 0},
                },
            )
        )
        self.store.finish_job(
            job_id,
            status="success",
            summary={
                "download_exit_code": 0,
                "downloaded_count": 0,
                "persisted_count": 0,
                "exception_count": 0,
            },
        )

        progress = self.service.build_latest_progress(self.store.get_job(job_id))
        view = build_job_view(self.store.get_job(job_id), progress=progress)

        self.assertEqual(progress["job_status"], "success")
        self.assertEqual(progress["phase_code"], "completed")
        self.assertEqual(view["job_id"], job_id)

    def test_build_latest_progress_rejects_non_mapping_job_metadata(self) -> None:
        for metadata in (False, []):
            with (
                self.subTest(metadata=metadata),
                self.assertRaisesRegex(ValueError, "job.metadata"),
            ):
                self.service.build_latest_progress(
                    {
                        "job_id": "",
                        "job_type": "download_ingest",
                        "status": "success",
                        "metadata": metadata,
                    }
                )

    def test_build_latest_progress_rejects_non_list_record_families_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "job.metadata.record_families must be a list"):
            self.service.build_latest_progress(
                {
                    "job_id": "",
                    "job_type": "download_ingest",
                    "status": "success",
                    "metadata": {"record_families": "listing,deal"},
                }
            )

    def test_build_latest_progress_rejects_non_mapping_stage_summary_with_field_diagnostic(self) -> None:
        job_id = self.store.create_job(
            "download_ingest",
            metadata={
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "all",
            },
        )
        self.store.start_job(job_id)
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="save_pages",
                status="done",
                payload={
                    "label": "当前没有需要下载的网页，无需下载",
                    "summary": [],
                },
            )
        )

        with self.assertRaisesRegex(ValueError, "progress_event.payload.summary must be a mapping"):
            self.service.build_latest_progress(self.store.get_job(job_id))

    def test_build_latest_progress_rejects_non_mapping_event_payload_with_field_diagnostic(self) -> None:
        job_id = self.store.create_job(
            "download_ingest",
            metadata={
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "all",
            },
        )
        with self.assertRaisesRegex(ValueError, "progress_event.payload must be a mapping"):
            with patch.object(
                self.service.repository,
                "list_job_events",
                return_value=[
                    {
                        "stage": "save_pages",
                        "status": "done",
                        "payload": [],
                    }
                ],
            ):
                self.service.build_latest_progress(self.store.get_job(job_id))

    def test_build_latest_progress_rejects_non_mapping_progress_stage_summary(self) -> None:
        job_id = self.store.create_job(
            "download_ingest",
            metadata={
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "all",
            },
        )
        with self.assertRaisesRegex(ValueError, "progress.latest_stage_summary must be a mapping"):
            with patch(
                "desktop_backend.services.execution_service.build_progress_view",
                return_value={
                    "phase_code": "save_pages",
                    "phase_label": "正在保存网页",
                    "current_item_label": "",
                    "current_index": 0,
                    "current_total": 0,
                    "latest_stage_code": "save_pages",
                    "latest_stage_label": "正在保存网页",
                    "latest_stage_summary": [],
                },
            ):
                self.service.build_latest_progress(self.store.get_job(job_id))


if __name__ == "__main__":
    unittest.main()
