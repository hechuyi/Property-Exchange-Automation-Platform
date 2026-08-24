from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppUserFacingError
from desktop_backend.job_event_contract import build_job_event_view
from desktop_backend.services.execution_service import ExecutionService
from desktop_backend.services.runtime_service import RuntimeService
from peap.streaming_store import StreamingStore


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


class ExportServiceScopeTests(unittest.TestCase):
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
        self.basic_settings = {
            "archive_root": self.config.ARCHIVE_ROOT,
            "export_root": self.config.OUTPUT_EXCEL_DIR,
            "default_exchange": "all",
            "default_project_type": "equity_transfer",
            "default_concurrency": 2,
        }
        self.service = ExecutionService(
            config_obj=self.config,
            store=self.store,
            db_path=self.db_path,
            runtime_service=self.runtime_service,
            get_basic_settings=lambda: dict(self.basic_settings),
            get_advanced_settings=lambda: {"save_json": False, "postprocess_config": "", "raw_manual_root": ""},
            run_store_maintenance=lambda: None,
            repair_missing_archives_once=lambda: None,
            build_ingest_runner=lambda archive_root=None: None,
            user_error_cls=AppUserFacingError,
        )

    def test_run_export_with_contract_preserves_family_business_scope(self) -> None:
        captured: dict[str, object] = {}

        def fake_run_ready_export(_store, request):
            captured["run_request"] = request

            class _Result:
                export_id = "exp-2"
                cursor_id = "cursor-2"
                artifacts = []
                new_records = 0
                changed_records = 0

            return _Result()

        payload = self.service.run_export_with_contract(
            {
                "record_family": "listing",
                "business_id": "physical_asset",
                "exchange": "beijing",
                "date_from": "2026-03-21",
                "date_to": "2026-03-21",
                "requested_export_mode": "full",
            },
            run_ready_export_fn=fake_run_ready_export,
            count_scope_fn=lambda *_args, **_kwargs: {},
        )

        self.assertEqual(captured["run_request"].record_family, "listing")
        self.assertEqual(captured["run_request"].business_types, ["physical_asset"])
        self.assertEqual(payload["scope"]["record_family"], "listing")
        self.assertIn("business_id", payload["scope"])
        self.assertEqual(payload["scope"]["business_id"], "physical_asset")
        self.assertNotIn("project_type", payload["scope"])

    def test_run_export_with_contract_keeps_explicit_deal_business_scope_without_expanding_to_all(self) -> None:
        captured: dict[str, object] = {}

        def fake_run_ready_export(_store, request):
            captured["run_request"] = request

            class _Result:
                export_id = "exp-deal-1"
                cursor_id = "cursor-deal-1"
                artifacts = []
                new_records = 0
                changed_records = 0

            return _Result()

        payload = self.service.run_export_with_contract(
            {
                "record_family": "deal",
                "business_id": "deal_equity_transfer",
                "exchange": "cbex",
                "date_from": "2026-04-20",
                "date_to": "2026-04-20",
                "requested_export_mode": "full",
            },
            run_ready_export_fn=fake_run_ready_export,
            count_scope_fn=lambda *_args, **_kwargs: {},
        )

        self.assertEqual(captured["run_request"].record_family, "deal")
        self.assertEqual(captured["run_request"].business_types, ["deal_equity_transfer"])
        self.assertEqual(payload["scope"]["record_family"], "deal")
        self.assertEqual(payload["scope"]["business_id"], "deal_equity_transfer")
        self.assertEqual(payload["scope"]["exchange"], "cbex")

    def test_run_export_with_contract_uses_configured_retention_count(self) -> None:
        self.basic_settings["retention_count"] = 6
        captured: dict[str, object] = {}

        def fake_run_ready_export(_store, request):
            captured["run_request"] = request

            class _Result:
                export_id = "exp-retention"
                cursor_id = request.cursor_id
                artifacts = []
                new_records = 0
                changed_records = 0
                revision_watermark = 0
                field_missing_blocked_records = 0
                field_missing_diagnostics = []

            return _Result()

        payload = self.service.run_export_with_contract(
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "all",
                "date_from": "2026-03-21",
                "date_to": "2026-03-21",
                "requested_export_mode": "full",
            },
            run_ready_export_fn=fake_run_ready_export,
            count_scope_fn=lambda *_args, **_kwargs: {},
        )

        self.assertEqual(captured["run_request"].retention_count, 6)
        self.assertEqual(payload["retention_count"], 6)

    def test_run_export_with_contract_rejects_invalid_exchange_via_shared_scope_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "exchange"):
            self.service.run_export_with_contract(
                {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "exchange": "not_a_real_exchange",
                    "requested_export_mode": "full",
                },
                run_ready_export_fn=lambda *_args, **_kwargs: None,
                count_scope_fn=lambda *_args, **_kwargs: {},
            )

    def test_run_export_with_contract_rejects_deal_physical_exchange_outside_current_surface_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "deal_physical_asset"):
            self.service.run_export_with_contract(
                {
                    "record_family": "deal",
                    "business_id": "deal_physical_asset",
                    "exchange": "tpre",
                    "date_from": "2026-04-18",
                    "date_to": "2026-04-18",
                    "requested_export_mode": "full",
                },
                run_ready_export_fn=lambda *_args, **_kwargs: None,
                count_scope_fn=lambda *_args, **_kwargs: {},
            )

    def test_run_export_with_contract_accepts_listing_capital_increase_sources(self) -> None:
        captured: dict[str, object] = {}

        def fake_run_ready_export(_store, request):
            captured["run_request"] = request

            class _Result:
                export_id = "exp-shenzhen-ci"
                cursor_id = request.cursor_id
                artifacts = []
                new_records = 0
                changed_records = 0
                field_missing_blocked_records = 0
                field_missing_diagnostics = []

            return _Result()

        for exchange in ("shandong", "guangdong", "shenzhen"):
            with self.subTest(exchange=exchange):
                payload = self.service.run_export_with_contract(
                    {
                        "record_family": "listing",
                        "business_id": "capital_increase",
                        "exchange": exchange,
                        "date_from": "2026-04-18",
                        "date_to": "2026-04-18",
                        "requested_export_mode": "full",
                    },
                    run_ready_export_fn=fake_run_ready_export,
                    count_scope_fn=lambda *_args, **_kwargs: {},
                )

                self.assertEqual(captured["run_request"].exchange, exchange)
                self.assertEqual(captured["run_request"].business_types, ["capital_increase"])
                self.assertEqual(payload["scope"]["exchange"], exchange)

    def test_run_export_with_contract_exposes_scope_and_empty_export_diagnostics_in_event_view(self) -> None:
        payload = self.service.run_export_with_contract(
            {
                "record_family": "listing",
                "business_id": "physical_asset",
                "exchange": "cbex",
                "date_from": "2026-03-21",
                "date_to": "2026-03-21",
                "requested_export_mode": "full",
            },
            run_ready_export_fn=lambda _store, request: type(
                "_Result",
                (),
                {
                    "export_id": "exp-empty-1",
                    "cursor_id": request.cursor_id,
                    "artifacts": [],
                    "new_records": 0,
                    "changed_records": 0,
                    "field_missing_blocked_records": 0,
                    "field_missing_diagnostics": [],
                },
            )(),
            count_scope_fn=lambda _store, _request: {"pending_mapping": 2, "conflict": 0},
        )

        raw_events = self.service.get_job_events(payload["job_id"])
        export_event = next(event for event in raw_events if event["stage"] == "exporting")
        event_view = build_job_event_view(export_event)

        self.assertEqual(event_view["scope"]["record_family"], "listing")
        self.assertEqual(event_view["scope"]["business_id"], "physical_asset")
        self.assertEqual(event_view["scope"]["exchange"], "cbex")
        self.assertEqual(event_view["scope"]["state"], "all")
        self.assertEqual(event_view["warning_code"], "pending_mapping_blocked")
        self.assertIn("待补映射", event_view["warning_message"])
        self.assertEqual(event_view["empty_reason_code"], "pending_mapping_blocked")
        self.assertEqual(event_view["scope_state_counts"], {"pending_mapping": 2, "conflict": 0})


if __name__ == "__main__":
    unittest.main()
