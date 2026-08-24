from __future__ import annotations

import inspect
import unittest

from desktop_backend.app_service import AppService
from desktop_backend.services.execution_service import ExecutionService
from desktop_backend.services.mapping_service import MappingService
from desktop_backend.services.records_service import RecordsService
from desktop_backend.services.runtime_service import RuntimeService
from desktop_backend.services.settings_service import SettingsService


class AppServiceArchitectureTest(unittest.TestCase):
    def test_hot_path_workflow_methods_no_longer_touch_streaming_store_directly(self) -> None:
        guarded_methods = [
            "_run_store_maintenance",
            "_repair_missing_archives_once",
            "overview",
            "reveal_record_folder",
            "_run_mapping_refresh_job",
            "_launch_mapping_refresh_job",
            "launch_pending_mapping_refresh",
            "upsert_mapping",
            "_refresh_record_postprocess",
            "_reprocess_record",
        ]

        for method_name in guarded_methods:
            with self.subTest(method_name=method_name):
                source = inspect.getsource(getattr(AppService, method_name))
                self.assertNotIn("self.store", source)

    def test_sub_services_use_repository_boundary_instead_of_direct_store_access(self) -> None:
        guarded_methods = [
            (RecordsService, ["list_records"]),
            (
                MappingService,
                [
                    "build_mapping_work_item",
                    "list_mapping_entries",
                    "find_records_for_mapping_refresh",
                    "find_pending_mapping_records",
                    "find_existing_mapping_entry",
                ],
            ),
            (RuntimeService, ["launch_browser_runtime_install", "install_browser_runtime"]),
            (
                ExecutionService,
                [
                    "build_latest_progress",
                    "list_jobs",
                    "get_job",
                    "get_job_events",
                    "run_manual_import_job",
                    "launch_manual_import",
                    "run_export_with_contract",
                    "launch_streaming_job",
                ],
            ),
            (SettingsService, ["get_basic_settings", "get_advanced_settings", "set_basic_settings", "set_advanced_settings"]),
        ]

        for cls, method_names in guarded_methods:
            for method_name in method_names:
                with self.subTest(class_name=cls.__name__, method_name=method_name):
                    source = inspect.getsource(getattr(cls, method_name))
                    self.assertNotIn("self.store", source)

    def test_settings_service_no_longer_uses_default_project_type_shim(self) -> None:
        guarded_methods = [
            "get_basic_settings",
            "get_advanced_settings",
            "set_basic_settings",
            "set_advanced_settings",
        ]

        for method_name in guarded_methods:
            with self.subTest(method_name=method_name):
                source = inspect.getsource(getattr(SettingsService, method_name))
                self.assertNotIn("default_project_type", source)
