from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import load_workbook

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import (
    AppService,
    AppUserFacingError,
    _database_schema_ready,
    _record_reprocess_runtime_supported,
)
from peap.migrations import MigrationRunner
from peap.streaming_ingest import _canonical_archive_target
from peap.streaming_models import IngestedRecord, PostProcessFinding
from peap.streaming_store import SCHEMA_VERSION


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


class _FalsyDict(dict):
    def __bool__(self) -> bool:
        return False


class _FalsyList(list):
    def __bool__(self) -> bool:
        return False


SSE_DEAL_NOTICE_SHELL_FIXTURE = bytes(
    (
        60, 104, 49, 62, 83, 83, 69, 32, 68, 101, 97, 108,
        32, 78, 111, 116, 105, 99, 101, 60, 47, 104, 49, 62,
    )
)
SAFE_HTML_FIXTURE = bytes(
    (
        60, 104, 116, 109, 108, 62, 60, 47, 104, 116, 109, 108, 62,
    )
)


class AppUserFacingErrorTest(unittest.TestCase):
    def test_details_none_defaults_to_empty_dict(self) -> None:
        error = AppUserFacingError(message="bad", error_code="bad_request", http_status=400)

        self.assertEqual(error.details, {})

    def test_details_mapping_is_copied(self) -> None:
        details = {"field": "name"}

        error = AppUserFacingError(
            message="bad",
            error_code="bad_request",
            http_status=400,
            details=details,
        )
        details["field"] = "changed"

        self.assertEqual(error.details, {"field": "name"})

    def test_details_rejects_explicit_non_mapping(self) -> None:
        for details in ([], "field=name", True):
            with self.subTest(details=details):
                with self.assertRaisesRegex(TypeError, "details must be a dict"):
                    AppUserFacingError(
                        message="bad",
                        error_code="bad_request",
                        http_status=400,
                        details=details,
                    )


class AppServiceSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app_home = os.path.join(self.temp_dir.name, "app_home")
        self.docs_home = os.path.join(self.temp_dir.name, "docs_home")
        self.data_root = os.path.join(self.temp_dir.name, "data")
        self.archive_root = os.path.join(self.temp_dir.name, "archive")
        self.export_root = os.path.join(self.temp_dir.name, "exports")
        self.cache_dir = os.path.join(self.temp_dir.name, "cache")
        self.streaming_db_path = os.path.join(self.data_root, "streaming_ingest.sqlite3")
        with patch.dict(
            os.environ,
            {
                "PEAP_WORKSPACE_ROOT": self.app_home,
                "PEAP_APP_HOME": self.app_home,
                "PEAP_DOCUMENTS_HOME": self.docs_home,
                "PEAP_DATA_ROOT": self.data_root,
                "PEAP_ARCHIVE_ROOT": self.archive_root,
                "PEAP_EXPORT_ROOT": self.export_root,
                "PEAP_CACHE_DIR": self.cache_dir,
                "PEAP_STREAMING_DB_PATH": self.streaming_db_path,
            },
            clear=False,
        ):
            self.config = AppConfig.from_env(project_root=self.temp_dir.name)
        self.service = AppService(
            config_obj=self.config,
            runtime_dependencies=FakeRuntimeDependencies(),
        )

    def _migrated_service(self) -> AppService:
        MigrationRunner.run(self.config.STREAMING_DB_PATH)
        return AppService(
            config_obj=self.config,
            runtime_dependencies=FakeRuntimeDependencies(),
        )

    def test_schema_readiness_rejects_tables_with_incomplete_columns(self) -> None:
        os.makedirs(os.path.dirname(self.config.STREAMING_DB_PATH), exist_ok=True)
        with sqlite3.connect(self.config.STREAMING_DB_PATH) as conn:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            for table_name in (
                "jobs",
                "records",
                "record_revisions",
                "exports",
                "settings",
                "operation_journal",
            ):
                conn.execute(f"CREATE TABLE {table_name} (id TEXT)")

        self.assertFalse(_database_schema_ready(self.config.STREAMING_DB_PATH))

    def test_missing_schema_startup_does_not_create_database_and_basic_routes_raise_not_ready(self) -> None:
        self.assertFalse(os.path.exists(self.config.STREAMING_DB_PATH))
        self.assertFalse(self.service.readiness()["schema"]["ready"])
        self.assertFalse(os.path.exists(self.config.STREAMING_DB_PATH))

        guarded_calls = [
            self.service.get_basic_settings,
            lambda: self.service.list_records(
                {
                    "record_family": "listing",
                    "business_id": "all",
                    "exchange": "all",
                    "state": "all",
                }
            ),
            lambda: self.service.launch_manual_import({"input_dir": self.temp_dir.name}),
        ]
        for call in guarded_calls:
            with self.subTest(call=call), self.assertRaises(Exception) as captured:
                call()
            self.assertEqual(getattr(captured.exception, "error_code", ""), "schema_not_ready")
            self.assertEqual(getattr(captured.exception, "http_status", 0), 503)
            self.assertFalse(os.path.exists(self.config.STREAMING_DB_PATH))

    def test_missing_schema_overview_returns_runtime_defaults_and_empty_snapshot(self) -> None:
        overview = self.service.overview()

        self.assertFalse(overview["schema"]["ready"])
        self.assertEqual(overview["record_summary"], {"state_counts": {}, "pending_mapping_count": 0})
        self.assertIsNone(overview["latest_job"])
        self.assertEqual(overview["latest_progress"], {})
        self.assertEqual(overview["recent_jobs"], [])
        self.assertIn("runtime", overview)
        self.assertIn("visibility", overview)
        self.assertIn("defaults", overview)
        self.assertFalse(os.path.exists(self.config.STREAMING_DB_PATH))

    def test_overview_smoke_returns_core_sections(self) -> None:
        self.service = self._migrated_service()
        overview = self.service.overview()

        self.assertIsInstance(overview, dict)
        self.assertIn("runtime", overview)
        self.assertIn("record_summary", overview)
        self.assertIn("defaults", overview)

    def test_settings_smoke_round_trip(self) -> None:
        self.service = self._migrated_service()
        basic_before = self.service.get_basic_settings()
        advanced_before = self.service.get_advanced_settings()
        basic_after = self.service.set_basic_settings({"default_exchange": "sse"})
        advanced_after = self.service.set_advanced_settings({"save_json": True})

        self.assertIsInstance(basic_before, dict)
        self.assertIsInstance(advanced_before, dict)
        self.assertEqual(basic_after["default_exchange"], "sse")
        self.assertTrue(advanced_after["save_json"])

    def test_surface_basic_settings_preserves_falsy_mapping_contract(self) -> None:
        self.service = self._migrated_service()
        settings = _FalsyDict({"archive_root": "/tmp/archive"})

        with patch.object(self.service, "get_basic_settings", return_value=settings):
            result = self.service._read_surface_basic_settings()

        self.assertEqual(result, {"archive_root": "/tmp/archive"})

    def test_surface_advanced_settings_rejects_none_contract_result(self) -> None:
        self.service = self._migrated_service()

        with (
            patch.object(self.service, "get_advanced_settings", return_value=None),
            self.assertRaisesRegex(ValueError, "advanced settings must be an object"),
        ):
            self.service._read_surface_advanced_settings()

    def test_open_local_path_rejects_invalid_reveal_instead_of_defaulting_false(self) -> None:
        target_path = os.path.join(self.temp_dir.name, "open-target.txt")
        with open(target_path, "w", encoding="utf-8") as handle:
            handle.write("open target")

        with (
            patch("desktop_backend.app_service.reveal_in_file_manager", return_value=target_path) as mocked_reveal,
            self.assertRaisesRegex(ValueError, "reveal"),
        ):
            self.service.open_local_path({"path": target_path, "reveal": "not-a-bool"})

        mocked_reveal.assert_not_called()

    def test_open_local_path_rejects_non_string_path_instead_of_stringifying_object(self) -> None:
        with (
            patch("desktop_backend.app_service.reveal_in_file_manager") as mocked_reveal,
            self.assertRaisesRegex(ValueError, "path"),
        ):
            self.service.open_local_path({"path": {"path": "/tmp/example"}})

        mocked_reveal.assert_not_called()

    def test_open_local_path_rejects_false_payload_before_reveal(self) -> None:
        with (
            patch("desktop_backend.app_service.reveal_in_file_manager") as mocked_reveal,
            self.assertRaisesRegex(ValueError, "payload"),
        ):
            self.service.open_local_path(False)

        mocked_reveal.assert_not_called()

    def test_choose_local_path_rejects_non_string_current_path_instead_of_stringifying_object(self) -> None:
        with (
            patch("desktop_backend.app_service.pick_local_path") as mocked_picker,
            self.assertRaisesRegex(ValueError, "current_path"),
        ):
            self.service.choose_local_path({"current_path": {"path": "/tmp/example"}})

        mocked_picker.assert_not_called()

    def test_choose_local_path_rejects_non_string_kind_and_prompt_before_picker(self) -> None:
        for field_name, payload in (
            ("selection_kind", {"selection_kind": {"kind": "file"}}),
            ("prompt", {"prompt": {"text": "选择文件"}}),
        ):
            with self.subTest(field_name=field_name):
                with (
                    patch("desktop_backend.app_service.pick_local_path") as mocked_picker,
                    self.assertRaisesRegex(ValueError, field_name),
                ):
                    self.service.choose_local_path(payload)

                mocked_picker.assert_not_called()

    def test_choose_local_path_rejects_false_payload_before_picker(self) -> None:
        with (
            patch("desktop_backend.app_service.pick_local_path") as mocked_picker,
            self.assertRaisesRegex(ValueError, "payload"),
        ):
            self.service.choose_local_path(False)

        mocked_picker.assert_not_called()

    def test_download_export_history_rejects_non_string_output_dir_before_creating_directory(self) -> None:
        self.service = self._migrated_service()

        with (
            patch.object(
                self.service,
                "get_export_history_detail",
                return_value={
                    "export_id": "exp-1",
                    "openable": True,
                    "rebuildable": True,
                    "is_tombstone": False,
                    "retention_status": "available",
                    "existing_artifacts": [],
                },
            ),
            patch("desktop_backend.app_service.os.makedirs") as mocked_makedirs,
            self.assertRaisesRegex(ValueError, "output_dir"),
        ):
            self.service.download_export_history("exp-1", output_dir={"path": "/tmp/export"})

        mocked_makedirs.assert_not_called()

    def test_download_export_history_uses_default_export_root_for_empty_output_dir(self) -> None:
        self.service = self._migrated_service()
        artifact_path = os.path.join(self.service.default_export_root, "default-target.xlsx")
        os.makedirs(self.service.default_export_root, exist_ok=True)
        with open(artifact_path, "wb") as handle:
            handle.write(b"xlsx")
        digest = hashlib.sha256(b"xlsx").hexdigest()

        with (
            patch.object(
                self.service,
                "get_export_history_detail",
                return_value={
                    "export_id": "exp-1",
                    "openable": True,
                    "rebuildable": True,
                    "is_tombstone": False,
                    "retention_status": "available",
                    "existing_artifacts": [artifact_path],
                    "manifest": {"artifact_checksums": {artifact_path: digest}},
                },
            ) as mocked_get_detail,
        ):
            payload = self.service.download_export_history("exp-1", output_dir="  ")

        mocked_get_detail.assert_called_once_with("exp-1")
        self.assertTrue(payload["downloaded"])
        self.assertEqual(payload["artifacts"], [artifact_path])

    def test_download_export_history_reads_current_export_root_after_settings_change(self) -> None:
        self.service = self._migrated_service()
        target_root = os.path.join(self.temp_dir.name, "settings-export-root")
        self.service.set_basic_settings({"export_root": target_root})
        artifact_path = os.path.join(target_root, "settings-target.xlsx")
        os.makedirs(target_root, exist_ok=True)
        with open(artifact_path, "wb") as handle:
            handle.write(b"xlsx")

        with patch.object(
            self.service,
            "get_export_history_detail",
            return_value={
                "export_id": "exp-settings-root",
                "openable": True,
                "rebuildable": True,
                "is_tombstone": False,
                "retention_status": "available",
                "existing_artifacts": [artifact_path],
                "manifest": {"artifact_checksums": {artifact_path: hashlib.sha256(b"xlsx").hexdigest()}},
            },
        ):
            payload = self.service.download_export_history("exp-settings-root", output_dir="")

        self.assertTrue(payload["downloaded"])
        self.assertEqual(payload["artifacts"], [artifact_path])

    def test_download_export_history_rejects_and_removes_tampered_destination(self) -> None:
        self.service = self._migrated_service()
        source_dir = os.path.join(self.temp_dir.name, "source-export")
        target_dir = os.path.join(self.temp_dir.name, "downloaded-export")
        os.makedirs(source_dir, exist_ok=True)
        source_path = os.path.join(source_dir, "artifact.xlsx")
        with open(source_path, "wb") as handle:
            handle.write(b"source")
        manifest = {"artifact_checksums": {source_path: hashlib.sha256(b"source").hexdigest()}}

        from desktop_backend import app_service as app_service_module

        real_copy = app_service_module._copy_export_artifact_exclusive

        def tampering_copy(source: str, destination: str) -> None:
            real_copy(source, destination)
            with open(destination, "wb") as handle:
                handle.write(b"tampered")

        with (
            patch.object(
                self.service,
                "get_export_history_detail",
                return_value={
                    "export_id": "exp-tampered-destination",
                    "openable": True,
                    "rebuildable": True,
                    "is_tombstone": False,
                    "retention_status": "available",
                    "existing_artifacts": [source_path],
                    "manifest": manifest,
                },
            ),
            patch("desktop_backend.app_service._copy_export_artifact_exclusive", side_effect=tampering_copy),
            self.assertRaisesRegex(RuntimeError, "destination checksum mismatch"),
        ):
            self.service.download_export_history("exp-tampered-destination", output_dir=target_dir)

        self.assertFalse(os.path.exists(os.path.join(target_dir, "artifact.xlsx")))

    def test_download_export_history_fails_closed_on_manifest_checksum_mismatch(self) -> None:
        self.service = self._migrated_service()
        source_dir = os.path.join(self.temp_dir.name, "source-export")
        target_dir = os.path.join(self.temp_dir.name, "downloaded-export")
        os.makedirs(source_dir, exist_ok=True)
        artifact_path = os.path.join(source_dir, "artifact.xlsx")
        with open(artifact_path, "wb") as handle:
            handle.write(b"actual")

        with (
            patch.object(
                self.service,
                "get_export_history_detail",
                return_value={
                    "export_id": "exp-checksum-mismatch",
                    "openable": True,
                    "rebuildable": True,
                    "is_tombstone": False,
                    "retention_status": "available",
                    "existing_artifacts": [artifact_path],
                    "manifest": {"artifact_checksums": {artifact_path: hashlib.sha256(b"expected").hexdigest()}},
                },
            ),
            self.assertRaisesRegex(RuntimeError, "checksum mismatch"),
        ):
            self.service.download_export_history("exp-checksum-mismatch", output_dir=target_dir)

        self.assertFalse(os.path.exists(target_dir))

    def test_download_export_history_rejects_existing_destination_without_overwriting(self) -> None:
        self.service = self._migrated_service()
        source_dir = os.path.join(self.temp_dir.name, "source-export")
        target_dir = os.path.join(self.temp_dir.name, "downloaded-export")
        os.makedirs(source_dir, exist_ok=True)
        os.makedirs(target_dir, exist_ok=True)
        artifact_path = os.path.join(source_dir, "artifact.xlsx")
        destination_path = os.path.join(target_dir, "artifact.xlsx")
        with open(artifact_path, "wb") as handle:
            handle.write(b"source")
        with open(destination_path, "wb") as handle:
            handle.write(b"user-file")
        manifest = {"artifact_checksums": {artifact_path: hashlib.sha256(b"source").hexdigest()}}

        with (
            patch.object(
                self.service,
                "get_export_history_detail",
                return_value={
                    "export_id": "exp-destination-conflict",
                    "openable": True,
                    "rebuildable": True,
                    "is_tombstone": False,
                    "retention_status": "available",
                    "existing_artifacts": [artifact_path],
                    "manifest": manifest,
                },
            ),
            self.assertRaisesRegex(FileExistsError, "destination already exists"),
        ):
            self.service.download_export_history("exp-destination-conflict", output_dir=target_dir)

        with open(destination_path, "rb") as handle:
            self.assertEqual(handle.read(), b"user-file")

    def test_download_export_history_rolls_back_files_created_before_copy_failure(self) -> None:
        self.service = self._migrated_service()
        source_dir = os.path.join(self.temp_dir.name, "source-export")
        target_dir = os.path.join(self.temp_dir.name, "downloaded-export")
        os.makedirs(source_dir, exist_ok=True)
        first = os.path.join(source_dir, "first.xlsx")
        second = os.path.join(source_dir, "second.xlsx")
        for path, value in ((first, b"first"), (second, b"second")):
            with open(path, "wb") as handle:
                handle.write(value)
        manifest = {
            "artifact_checksums": {
                first: hashlib.sha256(b"first").hexdigest(),
                second: hashlib.sha256(b"second").hexdigest(),
            }
        }
        calls = 0
        from desktop_backend import app_service as app_service_module

        real_copy = app_service_module._copy_export_artifact_exclusive

        def flaky_copy(source_path: str, destination_path: str) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated copy failure")
            real_copy(source_path, destination_path)

        with (
            patch.object(
                self.service,
                "get_export_history_detail",
                return_value={
                    "export_id": "exp-copy-rollback",
                    "openable": True,
                    "rebuildable": True,
                    "is_tombstone": False,
                    "retention_status": "available",
                    "existing_artifacts": [first, second],
                    "manifest": manifest,
                },
            ),
            patch("desktop_backend.app_service._copy_export_artifact_exclusive", side_effect=flaky_copy),
            self.assertRaisesRegex(RuntimeError, "simulated copy failure"),
        ):
            self.service.download_export_history("exp-copy-rollback", output_dir=target_dir)

        self.assertFalse(os.path.exists(os.path.join(target_dir, "first.xlsx")))
        self.assertFalse(os.path.exists(os.path.join(target_dir, "second.xlsx")))

    def test_download_export_history_rejects_bad_output_dir_even_when_export_is_not_openable(self) -> None:
        self.service = self._migrated_service()

        with (
            patch.object(
                self.service,
                "get_export_history_detail",
                return_value={
                    "export_id": "exp-1",
                    "openable": False,
                    "rebuildable": False,
                    "is_tombstone": True,
                    "retention_status": "pruned",
                    "existing_artifacts": [],
                },
            ) as mocked_get_detail,
            patch("desktop_backend.app_service.os.makedirs") as mocked_makedirs,
            self.assertRaisesRegex(ValueError, "output_dir"),
        ):
            self.service.download_export_history("exp-1", output_dir={"path": "/tmp/export"})

        mocked_get_detail.assert_not_called()
        mocked_makedirs.assert_not_called()

    def test_open_export_history_rejects_openable_detail_without_existing_artifacts(self) -> None:
        self.service = self._migrated_service()

        for detail in (
            {
                "export_id": "exp-1",
                "openable": True,
                "rebuildable": True,
                "is_tombstone": False,
                "retention_status": "available",
            },
            {
                "export_id": "exp-1",
                "openable": True,
                "rebuildable": True,
                "is_tombstone": False,
                "retention_status": "available",
                "existing_artifacts": [""],
            },
        ):
            with self.subTest(detail=detail):
                with (
                    patch.object(self.service, "get_export_history_detail", return_value=detail),
                    patch("desktop_backend.app_service.reveal_in_file_manager") as mocked_reveal,
                    self.assertRaisesRegex(ValueError, "existing_artifacts"),
                ):
                    self.service.open_export_history("exp-1")

                mocked_reveal.assert_not_called()

    def test_download_export_history_rejects_openable_detail_without_existing_artifacts(self) -> None:
        self.service = self._migrated_service()
        output_dir = os.path.join(self.temp_dir.name, "downloaded-export")

        for detail in (
            {
                "export_id": "exp-1",
                "openable": True,
                "rebuildable": True,
                "is_tombstone": False,
                "retention_status": "available",
            },
            {
                "export_id": "exp-1",
                "openable": True,
                "rebuildable": True,
                "is_tombstone": False,
                "retention_status": "available",
                "existing_artifacts": [],
            },
            {
                "export_id": "exp-1",
                "openable": True,
                "rebuildable": True,
                "is_tombstone": False,
                "retention_status": "available",
                "existing_artifacts": False,
            },
        ):
            with self.subTest(detail=detail):
                with (
                    patch.object(self.service, "get_export_history_detail", return_value=detail),
                    patch("desktop_backend.app_service.shutil.copy2") as mocked_copy,
                    self.assertRaisesRegex(ValueError, "existing_artifacts"),
                ):
                    self.service.download_export_history("exp-1", output_dir=output_dir)

                mocked_copy.assert_not_called()

    def test_get_export_history_detail_rejects_non_string_export_id_before_repository_access(self) -> None:
        self.service = self._migrated_service()

        with (
            patch.object(self.service.pipeline_repository, "get_export") as mocked_get_export,
            self.assertRaisesRegex(ValueError, "export_id"),
        ):
            self.service.get_export_history_detail({"export_id": "exp-1"})

        mocked_get_export.assert_not_called()

    def test_get_export_history_detail_rejects_empty_export_id_before_repository_access(self) -> None:
        self.service = self._migrated_service()

        with (
            patch.object(self.service.pipeline_repository, "get_export") as mocked_get_export,
            self.assertRaisesRegex(ValueError, "export_id"),
        ):
            self.service.get_export_history_detail("  ")

        mocked_get_export.assert_not_called()

    def test_export_history_preserves_falsy_mapping_summary_and_manifest(self) -> None:
        self.service = self._migrated_service()
        with patch.object(
            self.service.pipeline_repository,
            "list_exports",
            return_value=[
                {
                    "export_id": "exp-falsy",
                    "cursor_id": "cursor-falsy",
                    "summary": _FalsyDict(
                        {
                            "requested_export_mode": "incremental",
                            "manifest": _FalsyDict({"revision_watermark": 41}),
                            "artifacts": [],
                        }
                    ),
                    "created_at": "2026-05-01T00:00:00",
                    "is_tombstone": False,
                    "pruned_by_retention": False,
                    "retention_count": 20,
                }
            ],
        ):
            rows = self.service.list_exports_history(limit=1)["rows"]

        self.assertEqual(rows[0]["requested_export_mode"], "incremental")
        self.assertEqual(rows[0]["revision_watermark"], 41)

    def test_export_history_rejects_explicit_non_mapping_summary_fields(self) -> None:
        self.service = self._migrated_service()
        cases = [
            ("summary", {"summary": False}, "export history summary"),
            (
                "summary.manifest",
                {"summary": {"manifest": False, "artifacts": []}},
                "export history summary.manifest",
            ),
            (
                "summary.cursor_value",
                {"summary": {"manifest": {}, "cursor_value": False, "artifacts": []}},
                "export history summary.cursor_value",
            ),
        ]
        for field_name, item_patch, message in cases:
            with self.subTest(field_name=field_name):
                item = {
                    "export_id": "exp-bad",
                    "cursor_id": "cursor-bad",
                    "summary": {"manifest": {}, "cursor_value": {}, "artifacts": []},
                    "created_at": "2026-05-01T00:00:00",
                    "is_tombstone": False,
                    "pruned_by_retention": False,
                    "retention_count": 20,
                } | item_patch
                with (
                    patch.object(self.service.pipeline_repository, "get_export", return_value=item),
                    self.assertRaisesRegex(ValueError, message),
                ):
                    self.service.get_export_history_detail("exp-bad")

    def test_export_history_rejects_explicit_non_list_artifacts(self) -> None:
        self.service = self._migrated_service()
        item = {
            "export_id": "exp-bad-artifacts",
            "cursor_id": "cursor-bad-artifacts",
            "summary": {
                "manifest": {},
                "cursor_value": {},
                "artifacts": False,
            },
            "created_at": "2026-05-01T00:00:00",
            "is_tombstone": False,
            "pruned_by_retention": False,
            "retention_count": 20,
        }

        with (
            patch.object(self.service.pipeline_repository, "get_export", return_value=item),
            self.assertRaisesRegex(ValueError, "export history summary.artifacts"),
        ):
            self.service.get_export_history_detail("exp-bad-artifacts")

    def test_mapping_refresh_selection_preserves_falsy_items_list(self) -> None:
        self.service = self._migrated_service()
        backlog = {
            "sections": [
                {
                    "section_id": "mapping_gap_resolution",
                    "items": _FalsyList([{"record_id": "rec-falsy"}]),
                }
            ]
        }
        with patch.object(self.service, "list_pending_mappings", return_value=backlog):
            selected = self.service._select_mapping_refresh_items({})

        self.assertEqual([item["record_id"] for item in selected], ["rec-falsy"])

    def test_mapping_refresh_selection_requires_normalized_record_ids_contract(self) -> None:
        self.service = self._migrated_service()

        for normalized in ({}, {"record_ids": None}, {"record_ids": False}):
            with self.subTest(normalized=normalized):
                with (
                    patch(
                        "desktop_backend.app_service.normalize_mapping_record_selection_request",
                        return_value=normalized,
                    ),
                    patch.object(self.service, "list_pending_mappings") as mocked_backlog,
                    self.assertRaisesRegex(ValueError, "record_ids"),
                ):
                    self.service._select_mapping_refresh_items({"record_ids": ["rec-1"]})

                mocked_backlog.assert_not_called()

    def test_mapping_refresh_selection_rejects_malformed_backlog_shape(self) -> None:
        self.service = self._migrated_service()
        cases = [
            ({"sections": False}, "mapping backlog sections"),
            ({"sections": [False]}, "mapping backlog sections\\[\\*\\]"),
            (
                {"sections": [{"section_id": "mapping_gap_resolution", "items": False}]},
                "mapping_gap_resolution.items",
            ),
        ]
        for backlog, message in cases:
            with self.subTest(backlog=backlog):
                with (
                    patch.object(self.service, "list_pending_mappings", return_value=backlog),
                    self.assertRaisesRegex(ValueError, message),
                ):
                    self.service._select_mapping_refresh_items({})

    def test_acknowledge_field_missing_rejects_non_string_record_id_before_repository_access(self) -> None:
        self.service = self._migrated_service()

        with (
            patch.object(self.service.pipeline_repository, "load_record") as mocked_load_record,
            patch.object(self.service.pipeline_repository, "acknowledge_field_missing") as mocked_acknowledge,
            self.assertRaisesRegex(ValueError, "record_id"),
        ):
            self.service.acknowledge_field_missing({"record_id": "rec-field-missing"})

        mocked_load_record.assert_not_called()
        mocked_acknowledge.assert_not_called()

    def test_acknowledge_field_missing_rejects_invalid_findings_shape(self) -> None:
        self.service = self._migrated_service()
        record = {
            "record_id": "rec-field-missing",
            "state": "field_missing",
            "findings": {"type": "export_field_missing"},
        }

        with (
            patch.object(self.service.pipeline_repository, "load_record", return_value=record),
            patch.object(self.service.pipeline_repository, "acknowledge_field_missing") as mocked_acknowledge,
            self.assertRaisesRegex(ValueError, "findings must be a list"),
        ):
            self.service.acknowledge_field_missing("rec-field-missing")

        mocked_acknowledge.assert_not_called()

    def test_acknowledge_field_missing_rejects_non_object_finding_entries(self) -> None:
        self.service = self._migrated_service()
        record = {
            "record_id": "rec-field-missing",
            "state": "field_missing",
            "findings": [False],
        }

        with (
            patch.object(self.service.pipeline_repository, "load_record", return_value=record),
            patch.object(self.service.pipeline_repository, "acknowledge_field_missing") as mocked_acknowledge,
            self.assertRaisesRegex(ValueError, "findings entries must be objects"),
        ):
            self.service.acknowledge_field_missing("rec-field-missing")

        mocked_acknowledge.assert_not_called()

    def test_acknowledge_field_missing_rejects_empty_target_missing_fields_before_repository(self) -> None:
        self.service = self._migrated_service()
        record = {
            "record_id": "rec-field-missing",
            "state": "field_missing",
            "findings": [
                {
                    "type": "canonical_field_missing",
                    "evidence": {"missing_fields": []},
                }
            ],
        }

        with (
            patch.object(self.service.pipeline_repository, "load_record", return_value=record),
            patch.object(self.service.pipeline_repository, "acknowledge_field_missing") as mocked_acknowledge,
            self.assertRaisesRegex(ValueError, "missing_fields is empty"),
        ):
            self.service.acknowledge_field_missing("rec-field-missing")

        mocked_acknowledge.assert_not_called()

    def test_acknowledge_field_missing_rejects_invalid_missing_fields_shape(self) -> None:
        self.service = self._migrated_service()
        record = {
            "record_id": "rec-field-missing",
            "state": "field_missing",
            "findings": [
                {
                    "type": "export_field_missing",
                    "evidence": {"missing_fields": "project_name"},
                }
            ],
        }

        with (
            patch.object(self.service.pipeline_repository, "load_record", return_value=record),
            patch.object(self.service.pipeline_repository, "acknowledge_field_missing") as mocked_acknowledge,
            self.assertRaisesRegex(ValueError, "missing_fields must be a list"),
        ):
            self.service.acknowledge_field_missing("rec-field-missing")

        mocked_acknowledge.assert_not_called()

    def test_reveal_record_folder_rejects_non_string_record_id_before_repository_access(self) -> None:
        self.service = self._migrated_service()

        with (
            patch.object(self.service.pipeline_repository, "load_record") as mocked_load_record,
            patch("desktop_backend.app_service.reveal_in_file_manager") as mocked_reveal,
            self.assertRaisesRegex(ValueError, "record_id"),
        ):
            self.service.reveal_record_folder({"record_id": "rec-ready"})

        mocked_load_record.assert_not_called()
        mocked_reveal.assert_not_called()

    def test_reprocess_record_rejects_non_string_record_id_before_reserving_mutating_job(self) -> None:
        self.service = self._migrated_service()

        with (
            patch.object(self.service.pipeline_repository, "load_record") as mocked_load_record,
            patch.object(self.service, "_reserve_mutating_job") as mocked_reserve,
            self.assertRaisesRegex(ValueError, "record_id"),
        ):
            self.service.reprocess_record({"record_id": "rec-ready"})

        mocked_load_record.assert_not_called()
        mocked_reserve.assert_not_called()

    def test_refresh_record_postprocess_rejects_non_string_record_id_before_reserving_mutating_job(self) -> None:
        self.service = self._migrated_service()

        with (
            patch.object(self.service, "_build_ingest_runner") as mocked_build_runner,
            patch.object(self.service, "_reserve_mutating_job") as mocked_reserve,
            self.assertRaisesRegex(ValueError, "record_id"),
        ):
            self.service.refresh_record_postprocess({"record_id": "rec-ready"})

        mocked_build_runner.assert_not_called()
        mocked_reserve.assert_not_called()

    def test_store_maintenance_surfaces_rules_config_errors_instead_of_running_with_empty_rules(self) -> None:
        self.service = self._migrated_service()

        with (
            patch.object(
                self.service,
                "_load_effective_rules_config",
                side_effect=ValueError("bad postprocess config"),
            ),
            patch.object(self.service.pipeline_repository, "run_store_maintenance") as mocked_maintenance,
            self.assertRaisesRegex(ValueError, "bad postprocess config"),
        ):
            self.service._run_store_maintenance()

        mocked_maintenance.assert_not_called()

    def test_store_maintenance_runs_mutating_maintenance(self) -> None:
        self.service = self._migrated_service()

        with (
            patch.object(self.service, "_load_effective_rules_config", return_value={"rules": []}) as mocked_rules,
            patch.object(self.service.pipeline_repository, "run_store_maintenance") as mocked_maintenance,
        ):
            self.service._run_store_maintenance()

        mocked_rules.assert_called_once()
        mocked_maintenance.assert_called_once_with(rules_config={"rules": []}, mutate=True)

    def test_overview_uses_current_advanced_manual_import_root(self) -> None:
        self.service = self._migrated_service()
        self.service.set_advanced_settings({"raw_manual_root": "/tmp/manual-pending"})

        overview = self.service.overview()

        self.assertEqual(
            overview["defaults"]["manual_import_input_dir"],
            "/tmp/manual-pending",
        )

    def test_overview_exposes_archive_root_for_archive_reprocess(self) -> None:
        self.service = self._migrated_service()
        overview = self.service.overview()

        self.assertEqual(
            overview["defaults"]["archive_root"],
            os.path.abspath(self.config.ARCHIVE_ROOT),
        )

    def test_constructor_does_not_mutate_process_browser_cache_env(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            AppService(
                config_obj=self.config,
                runtime_dependencies=FakeRuntimeDependencies(),
            )

            self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", os.environ)
            self.assertNotIn("PEAP_PLAYWRIGHT_BROWSERS_PATH", os.environ)

    def test_launch_one_click_smoke_returns_job_id(self) -> None:
        self.service = self._migrated_service()
        with patch.object(self.service.execution_service, "launch_one_click", return_value={"job_id": "job-1", "job_type": "one_click"}) as mocked:
            payload = self.service.launch_one_click({"start_date": "2026-03-26", "end_date": "2026-03-26"})

        mocked.assert_called_once()
        self.assertEqual(payload["job_id"], "job-1")
        self.assertEqual(payload["job_type"], "one_click")

    def test_launch_manual_import_smoke_returns_job_id(self) -> None:
        self.service = self._migrated_service()
        payload = self.service.launch_manual_import({"input_dir": self.temp_dir.name})

        self.assertTrue(payload["job_id"])
        self.assertEqual(payload["job_type"], "manual_import")

    def test_launch_archive_reprocess_smoke_uses_configured_archive_root(self) -> None:
        self.service = self._migrated_service()
        os.makedirs(self.config.ARCHIVE_ROOT, exist_ok=True)

        payload = self.service.launch_archive_reprocess({})

        self.assertTrue(payload["job_id"])
        self.assertEqual(payload["job_type"], "archive_reprocess")
        self.assertEqual(payload["input_dir"], os.path.abspath(self.config.ARCHIVE_ROOT))

    def test_launch_archive_reprocess_rejects_missing_archive_root(self) -> None:
        self.service = self._migrated_service()
        with (
            patch.object(self.service.execution_service, "get_basic_settings", return_value={"archive_root": ""}),
            self.assertRaises(Exception) as captured,
        ):
            self.service.launch_archive_reprocess({})

        self.assertIn("归档目录未配置", str(captured.exception))

    def test_launch_archive_reprocess_rejects_false_payload_before_execution_service(self) -> None:
        self.service = self._migrated_service()

        with (
            patch.object(self.service.execution_service, "launch_archive_reprocess") as mocked_launch,
            self.assertRaisesRegex(ValueError, "payload"),
        ):
            self.service.launch_archive_reprocess(False)

        mocked_launch.assert_not_called()

    def test_reprocess_record_missing_source_keeps_canonical_state_and_returns_error(self) -> None:
        self.service = self._migrated_service()
        missing_source = os.path.join(self.temp_dir.name, "missing-source.html")
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-missing-source",
                revision_hash="hash-missing-source",
                project_code="G32026SH1000123",
                project_name="缺文件记录",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=missing_source,
                archive_path=missing_source,
                parser_payload={"项目编号": "G32026SH1000123", "项目名称": "缺文件记录"},
                postprocess_payload={"项目编号": "G32026SH1000123", "项目名称": "缺文件记录", "项目类型": "股权转让"},
                findings=[],
            )
        )

        result = self.service.reprocess_record("rec-missing-source")
        record = self.service.store.get_record("rec-missing-source")

        self.assertEqual(result["record_id"], "rec-missing-source")
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["error_code"], "source_missing")
        self.assertIn("source file missing for record: rec-missing-source", result["error_message"])
        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["last_operation_kind"], "reprocess")
        self.assertEqual(record["last_operation_code"], "source_missing")

    def test_reprocess_record_does_not_mask_missing_archive_with_existing_source(self) -> None:
        self.service = self._migrated_service()
        source_file = os.path.join(self.temp_dir.name, "source-exists.html")
        missing_archive = os.path.join(self.temp_dir.name, "missing-archive.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>source still exists</body></html>")
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-missing-archive-source-exists",
                revision_hash="hash-missing-archive-source-exists",
                project_code="G32026SH1000999",
                project_name="归档缺失但源文件存在",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=source_file,
                archive_path=missing_archive,
                parser_payload={"项目编号": "G32026SH1000999", "项目名称": "归档缺失但源文件存在"},
                postprocess_payload={"项目编号": "G32026SH1000999", "项目名称": "归档缺失但源文件存在", "项目类型": "股权转让"},
                findings=[],
            )
        )

        result = self.service.reprocess_record("rec-missing-archive-source-exists")
        record = self.service.store.get_record("rec-missing-archive-source-exists")

        self.assertEqual(result["record_id"], "rec-missing-archive-source-exists")
        self.assertEqual(result["error_code"], "source_missing")
        self.assertEqual(record["last_operation_code"], "source_missing")

    def test_reprocess_record_reports_record_reprocess_lock_namespace(self) -> None:
        self.service = self._migrated_service()
        self.service._reserve_mutating_job("mapping_refresh")
        try:
            with self.assertRaises(Exception) as captured:
                self.service.reprocess_record("rec-any")
        finally:
            self.service._release_mutating_job("mapping_refresh")

        self.assertEqual(getattr(captured.exception, "http_status", 0), 409)
        self.assertEqual(captured.exception.details.get("active_job_type"), "mapping_refresh")
        self.assertEqual(captured.exception.details.get("requested_job_type"), "record_reprocess")

    def test_reprocess_failed_record_uses_source_identity_json_string_original_evidence_path(self) -> None:
        self.service = self._migrated_service()
        missing_source = os.path.join(self.temp_dir.name, "missing-source-failed.html")
        original_evidence = os.path.join(self.temp_dir.name, "original-evidence.html")
        with open(original_evidence, "w", encoding="utf-8") as handle:
            handle.write("<html><body>evidence</body></html>")
        source_identity_json = json.dumps(
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "original_evidence_path": original_evidence,
                "original_source_file": missing_source,
                "source_url": "https://example.test/item/failed",
                "project_code": "G32026SH1000456",
                "project_name": "失败记录",
                "exchange": "shanghai",
                "listing_date": "2026-04-21",
            },
            ensure_ascii=False,
        )

        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-failed-source-identity-string",
                revision_hash="hash-failed-source-identity-string",
                project_code="G32026SH1000456",
                project_name="失败记录",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="parse_failed",
                source_file=missing_source,
                archive_path=missing_source,
                parser_payload={"page_url": "https://example.test/item/failed"},
                postprocess_payload={"项目编号": "G32026SH1000456", "项目名称": "失败记录"},
                findings=[],
            )
        )

        original_load_record = self.service.pipeline_repository.load_record

        def _patched_load_record(record_id: str):
            row = dict(original_load_record(record_id))
            if record_id == "rec-failed-source-identity-string":
                row["source_identity_json"] = source_identity_json
            return row

        with (
            patch.object(self.service.pipeline_repository, "load_record", side_effect=_patched_load_record),
            patch.object(self.service, "_build_ingest_runner") as mocked_runner_builder,
        ):
            fake_runner = mocked_runner_builder.return_value
            fake_runner.ingest.return_value = {"record_id": "rec-failed-source-identity-string", "state": "ready"}
            result = self.service.reprocess_record("rec-failed-source-identity-string")

        self.assertEqual(result["record_id"], "rec-failed-source-identity-string")
        call = fake_runner.ingest.call_args
        payload = call.args[0]
        self.assertEqual(payload.source_file, original_evidence)

    def test_reprocess_failed_record_rejects_invalid_original_evidence_before_ingest(self) -> None:
        self.service = self._migrated_service()
        missing_source = os.path.join(self.temp_dir.name, "missing-source-invalid-failed.html")
        original_evidence = os.path.join(self.temp_dir.name, "invalid-original-evidence.html")
        with open(original_evidence, "wb") as handle:
            handle.write(SSE_DEAL_NOTICE_SHELL_FIXTURE)
        source_identity_json = json.dumps(
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "original_evidence_path": original_evidence,
                "original_source_file": missing_source,
                "source_url": "https://example.test/item/failed-invalid",
                "project_code": "G32026SH1000457",
                "project_name": "失败无效证据",
                "exchange": "shanghai",
                "listing_date": "2026-04-21",
            },
            ensure_ascii=False,
        )

        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-failed-invalid-original-evidence",
                revision_hash="hash-failed-invalid-original-evidence",
                project_code="G32026SH1000457",
                project_name="失败无效证据",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="parse_failed",
                source_file=missing_source,
                archive_path=missing_source,
                parser_payload={"page_url": "https://example.test/item/failed-invalid"},
                postprocess_payload={"项目编号": "G32026SH1000457", "项目名称": "失败无效证据"},
                findings=[],
            )
        )

        original_load_record = self.service.pipeline_repository.load_record

        def _patched_load_record(record_id: str):
            row = dict(original_load_record(record_id))
            if record_id == "rec-failed-invalid-original-evidence":
                row["source_identity_json"] = source_identity_json
            return row

        with (
            patch.object(self.service.pipeline_repository, "load_record", side_effect=_patched_load_record),
            patch.object(self.service, "_build_ingest_runner") as mocked_runner_builder,
        ):
            mocked_runner_builder.return_value.ingest.return_value = {
                "record_id": "rec-failed-invalid-original-evidence",
                "state": "ready",
            }
            result = self.service.reprocess_record("rec-failed-invalid-original-evidence")

        self.assertEqual(result["record_id"], "rec-failed-invalid-original-evidence")
        self.assertEqual(result["error_code"], "source_evidence_invalid")
        self.assertEqual(result["evidence_status"], "invalid_shell")
        mocked_runner_builder.assert_not_called()

    def test_reprocess_record_rejects_invalid_evidence_before_ingest(self) -> None:
        self.service = self._migrated_service()
        invalid_artifact = os.path.join(self.temp_dir.name, "invalid-shell.html")
        with open(invalid_artifact, "wb") as handle:
            handle.write(SSE_DEAL_NOTICE_SHELL_FIXTURE)
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-invalid-evidence-reprocess",
                revision_hash="hash-invalid-evidence-reprocess",
                project_code="G32026SH1000555",
                project_name="无效证据重处理",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-21",
                state="ready",
                source_file=invalid_artifact,
                archive_path=invalid_artifact,
                parser_payload={"项目编号": "G32026SH1000555", "项目名称": "无效证据重处理"},
                postprocess_payload={"项目编号": "G32026SH1000555", "项目名称": "无效证据重处理", "项目类型": "股权转让"},
                findings=[],
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {"business_id": "equity_transfer"},
                    "canonical_fields": {"project_code": "G32026SH1000555", "project_type": "股权转让"},
                },
            )
        )

        with patch.object(self.service, "_build_ingest_runner") as mocked_runner_builder:
            result = self.service.reprocess_record("rec-invalid-evidence-reprocess")

        self.assertEqual(result["record_id"], "rec-invalid-evidence-reprocess")
        self.assertEqual(result["error_code"], "source_evidence_invalid")
        self.assertEqual(result["evidence_status"], "invalid_shell")
        mocked_runner_builder.assert_not_called()

    def test_reprocess_record_combines_verified_evidence_with_runtime_support(self) -> None:
        self.service = self._migrated_service()
        artifact = os.path.join(self.temp_dir.name, "verified-unsupported.html")
        with open(artifact, "wb") as handle:
            handle.write(SAFE_HTML_FIXTURE)
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-unsupported-reprocess",
                revision_hash="hash-unsupported-reprocess",
                project_code="G32026SH1000666",
                project_name="运行时不支持重处理",
                project_type="股权转让",
                exchange="unsupported_source",
                listing_date="2026-04-21",
                state="ready",
                source_file=artifact,
                archive_path=artifact,
                parser_payload={"项目编号": "G32026SH1000666", "项目名称": "运行时不支持重处理"},
                postprocess_payload={"项目编号": "G32026SH1000666", "项目名称": "运行时不支持重处理", "项目类型": "股权转让"},
                findings=[],
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {"business_id": "equity_transfer"},
                    "canonical_fields": {"project_code": "G32026SH1000666", "project_type": "股权转让"},
                },
            )
        )

        with patch.object(self.service, "_build_ingest_runner") as mocked_runner_builder:
            result = self.service.reprocess_record("rec-unsupported-reprocess")

        self.assertEqual(result["record_id"], "rec-unsupported-reprocess")
        self.assertEqual(result["error_code"], "reprocess_unsupported")
        self.assertEqual(result["evidence_status"], "verified")
        mocked_runner_builder.assert_not_called()

    def test_reprocess_runtime_allows_verified_same_scope_business_correction(self) -> None:
        artifact = os.path.join(self.temp_dir.name, "verified-shenzhen-business-correction.html")
        with open(artifact, "wb") as handle:
            handle.write(SAFE_HTML_FIXTURE)
        project_code = "G32026SZ1000999"
        source_identity = {
            "record_family": "listing",
            "business_id": "physical_asset",
            "source_id": "shenzhen",
            "exchange": "深交所",
            "project_code": project_code,
            "project_name": "深圳业务纠正",
        }

        self.assertTrue(
            _record_reprocess_runtime_supported(
                {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "exchange": "深交所",
                    "project_code": project_code,
                    "project_name": "深圳业务纠正",
                    "source_file": artifact,
                    "archive_path": artifact,
                },
                source_identity,
            )
        )

    def test_reprocess_runtime_rejects_unverified_business_correction_fallback(self) -> None:
        source_identity = {
            "record_family": "listing",
            "business_id": "physical_asset",
            "source_id": "shenzhen",
            "exchange": "深交所",
            "project_code": "G32026SZ1010000",
        }

        self.assertFalse(
            _record_reprocess_runtime_supported(
                {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "exchange": "深交所",
                    "project_code": "G32026SZ1010000",
                },
                source_identity,
            )
        )

    def test_reprocess_runtime_rejects_malformed_canonical_record_instead_of_defaulting(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            _record_reprocess_runtime_supported(
                {"record_family": "", "business_id": "", "exchange": "", "canonical_record": "{malformed-json"},
                {},
            )

    def test_reprocess_runtime_rejects_non_object_canonical_record_instead_of_defaulting(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected JSON object"):
            _record_reprocess_runtime_supported(
                {"record_family": "", "business_id": "", "exchange": "", "canonical_record": "[]"},
                {},
            )

    def test_reprocess_record_uses_canonical_business_identity_for_runtime_support(self) -> None:
        self.service = self._migrated_service()
        artifact = os.path.join(self.config.ARCHIVE_ROOT, "canonical-business-deal-runtime.html")
        os.makedirs(os.path.dirname(artifact), exist_ok=True)
        with open(artifact, "wb") as handle:
            handle.write(SAFE_HTML_FIXTURE)
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-canonical-business-deal-runtime",
                revision_hash="hash-canonical-business-deal-runtime",
                project_code="G32026SH1000777",
                project_name="成交运行时业务身份",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-21",
                state="ready",
                source_file=artifact,
                archive_path=artifact,
                parser_payload={"项目编号": "G32026SH1000777", "项目名称": "成交运行时业务身份"},
                postprocess_payload={"项目编号": "G32026SH1000777", "项目名称": "成交运行时业务身份", "项目类型": "股权转让"},
                findings=[],
                canonical_record={
                    "business_identity": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                    },
                    "source_identity": {"source_id": "sse"},
                    "canonical_fields": {
                        "project_code": "G32026SH1000777",
                        "project_name": "成交运行时业务身份",
                        "project_type": "股权转让",
                    },
                },
                source_identity={
                    "record_family": "listing",
                    "source_id": "sse",
                    "business_id": "deal_equity_transfer",
                    "project_code": "G32026SH1000777",
                    "project_name": "成交运行时业务身份",
                    "listing_date": "2026-04-21",
                    "original_source_file": artifact,
                    "original_evidence_path": artifact,
                },
            )
        )
        with self.service.store._connect() as conn:
            conn.execute(
                "UPDATE records SET record_family = '', business_id = '' WHERE record_id = ?",
                ("rec-canonical-business-deal-runtime",),
            )

        with patch.object(self.service, "_build_ingest_runner") as mocked_runner_builder:
            mocked_runner_builder.return_value.ingest.return_value = {
                "record_id": "rec-canonical-business-deal-runtime",
                "state": "ready",
            }
            result = self.service.reprocess_record("rec-canonical-business-deal-runtime")

        self.assertEqual(result["record_id"], "rec-canonical-business-deal-runtime")
        self.assertNotEqual(result.get("error_code"), "reprocess_unsupported")
        mocked_runner_builder.assert_called_once()
        mocked_runner_builder.return_value.ingest.assert_called_once()

    def test_reprocess_record_uses_canonical_source_identity_family_for_runtime_support(self) -> None:
        self.service = self._migrated_service()
        artifact = os.path.join(self.config.ARCHIVE_ROOT, "canonical-source-deal-runtime.html")
        os.makedirs(os.path.dirname(artifact), exist_ok=True)
        with open(artifact, "wb") as handle:
            handle.write(SAFE_HTML_FIXTURE)
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-canonical-source-deal-runtime",
                revision_hash="hash-canonical-source-deal-runtime",
                project_code="G32026SH1000778",
                project_name="成交运行时来源身份",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-21",
                state="ready",
                source_file=artifact,
                archive_path=artifact,
                parser_payload={"项目编号": "G32026SH1000778", "项目名称": "成交运行时来源身份"},
                postprocess_payload={"项目编号": "G32026SH1000778", "项目名称": "成交运行时来源身份", "项目类型": "股权转让"},
                findings=[],
                canonical_record={
                    "business_identity": {"business_id": "deal_equity_transfer"},
                    "source_identity": {
                        "record_family": "deal",
                        "source_id": "sse",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SH1000778",
                        "project_name": "成交运行时来源身份",
                        "project_type": "股权转让",
                    },
                },
                source_identity={
                    "record_family": "listing",
                    "source_id": "sse",
                    "business_id": "deal_equity_transfer",
                    "project_code": "G32026SH1000778",
                    "project_name": "成交运行时来源身份",
                    "listing_date": "2026-04-21",
                    "original_source_file": artifact,
                    "original_evidence_path": artifact,
                },
            )
        )
        with self.service.store._connect() as conn:
            conn.execute(
                "UPDATE records SET record_family = '', business_id = '' WHERE record_id = ?",
                ("rec-canonical-source-deal-runtime",),
            )

        with patch.object(self.service, "_build_ingest_runner") as mocked_runner_builder:
            mocked_runner_builder.return_value.ingest.return_value = {
                "record_id": "rec-canonical-source-deal-runtime",
                "state": "ready",
            }
            result = self.service.reprocess_record("rec-canonical-source-deal-runtime")

        self.assertEqual(result["record_id"], "rec-canonical-source-deal-runtime")
        self.assertNotEqual(result.get("error_code"), "reprocess_unsupported")
        mocked_runner_builder.assert_called_once()
        mocked_runner_builder.return_value.ingest.assert_called_once()

    def test_reprocess_record_retires_original_when_ingest_returns_new_family_identity(self) -> None:
        self.service = self._migrated_service()
        artifact = os.path.join(self.config.ARCHIVE_ROOT, "cquae-deal-family-reprocess.html")
        os.makedirs(os.path.dirname(artifact), exist_ok=True)
        with open(artifact, "wb") as handle:
            handle.write(SAFE_HTML_FIXTURE)
        with open(os.path.splitext(artifact)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "save_status": "complete",
                    "archive_content_sha256": hashlib.sha256(SAFE_HTML_FIXTURE).hexdigest(),
                    "archive_content_bytes": len(SAFE_HTML_FIXTURE),
                    "metadata": {
                        "record_family": "deal",
                        "source_id": "cquae",
                        "project_code": "G32026CQ1000062",
                    },
                },
                handle,
            )
        original = self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-cquae-listing-backlog",
                revision_hash="hash-cquae-listing-backlog",
                record_family="listing",
                project_code="G32026CQ1000062",
                project_name="重庆成交身份修复",
                project_type="股权转让",
                exchange="cquae",
                listing_date="2026-07-02",
                state="pending_mapping",
                source_file=artifact,
                archive_path=artifact,
                parser_payload={
                    "项目编号": "G32026CQ1000062",
                    "项目名称": "重庆成交身份修复",
                },
                postprocess_payload={
                    "项目编号": "G32026CQ1000062",
                    "项目名称": "重庆成交身份修复",
                    "项目类型": "股权转让",
                },
                findings=[],
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                    },
                    "source_identity": {"source_id": "cquae"},
                    "canonical_fields": {
                        "project_code": "G32026CQ1000062",
                        "project_type": "股权转让",
                    },
                },
                source_identity={
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "cquae",
                    "project_code": "G32026CQ1000062",
                    "original_source_file": artifact,
                    "original_evidence_path": artifact,
                },
            )
        )
        self.service.store.mark_mapping_pending(
            record_id="rec-cquae-listing-backlog",
            revision_id=int(original["revision_id"]),
            project_code="G32026CQ1000062",
            payload={"missing": "source_type"},
        )

        def _ingest_as_deal(_item, *, _connection=None):
            self.assertIsNotNone(_connection)
            self.service.store.upsert_record(
                IngestedRecord(
                    record_id="rec-cquae-deal-field-missing",
                    revision_hash="hash-cquae-deal-field-missing",
                    record_family="deal",
                    project_code="G32026CQ1000062",
                    project_name="重庆成交身份修复",
                    project_type="股权转让",
                    exchange="cquae",
                    listing_date="2026-07-02",
                    state="field_missing",
                    source_file=artifact,
                    archive_path=artifact,
                    parser_payload={"项目编号": "G32026CQ1000062"},
                    postprocess_payload={"项目编号": "G32026CQ1000062"},
                    findings=[],
                    canonical_record={
                        "record_family": "deal",
                        "business_identity": {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                        },
                        "source_identity": {"source_id": "cquae"},
                        "canonical_fields": {
                            "project_code": "G32026CQ1000062",
                            "project_type": "股权转让",
                        },
                    },
                    source_identity={
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "source_id": "cquae",
                        "project_code": "G32026CQ1000062",
                        "original_source_file": artifact,
                        "original_evidence_path": artifact,
                    },
                ),
                _connection=_connection,
            )
            return {"record_id": "rec-cquae-deal-field-missing", "state": "field_missing"}

        with patch.object(self.service, "_build_ingest_runner") as mocked_runner_builder:
            mocked_runner_builder.return_value.ingest.side_effect = _ingest_as_deal
            result = self.service.reprocess_record("rec-cquae-listing-backlog")

        original_record = self.service.store.get_record("rec-cquae-listing-backlog")
        replacement = self.service.store.get_record("rec-cquae-deal-field-missing")
        self.assertEqual(result["record_id"], "rec-cquae-deal-field-missing")
        self.assertEqual(original_record["state"], "skipped")
        self.assertEqual(original_record["last_error_type"], "superseded_by_record")
        self.assertEqual(replacement["record_family"], "deal")
        self.assertEqual(replacement["state"], "field_missing")
        self.assertEqual(self.service.store.count_pending_mappings(), 0)
        with sqlite3.connect(self.service.store.db_path) as conn:
            audit_actions = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT action
                    FROM audit_log
                    WHERE action IN ('record_superseded_by_reprocess', 'record_reprocessed')
                    ORDER BY audit_id
                    """
                ).fetchall()
            ]
        self.assertEqual(
            audit_actions,
            ["record_superseded_by_reprocess", "record_reprocessed"],
        )

    def test_reprocess_record_rolls_back_ingest_when_supersede_validation_fails(self) -> None:
        self.service = self._migrated_service()
        project_code = "G32026CQ1000063"
        artifact = os.path.join(self.config.ARCHIVE_ROOT, "cquae-reprocess-rollback.html")
        os.makedirs(os.path.dirname(artifact), exist_ok=True)
        with open(artifact, "wb") as handle:
            handle.write(SAFE_HTML_FIXTURE)
        with open(os.path.splitext(artifact)[0] + ".json", "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "save_status": "complete",
                    "archive_content_sha256": hashlib.sha256(SAFE_HTML_FIXTURE).hexdigest(),
                    "archive_content_bytes": len(SAFE_HTML_FIXTURE),
                    "metadata": {
                        "record_family": "listing",
                        "source_id": "cquae",
                        "project_code": project_code,
                    },
                },
                handle,
            )
        original = self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-cquae-rollback-original",
                revision_hash="hash-cquae-rollback-original",
                record_family="listing",
                project_code=project_code,
                project_name="重庆重处理回滚",
                project_type="股权转让",
                exchange="cquae",
                listing_date="2026-07-03",
                state="pending_mapping",
                source_file=artifact,
                archive_path=artifact,
                parser_payload={"项目编号": project_code, "项目名称": "重庆重处理回滚"},
                postprocess_payload={
                    "项目编号": project_code,
                    "项目名称": "重庆重处理回滚",
                    "项目类型": "股权转让",
                },
                findings=[],
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                    },
                    "source_identity": {"source_id": "cquae"},
                    "canonical_fields": {
                        "project_code": project_code,
                        "project_type": "股权转让",
                    },
                },
                source_identity={
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "cquae",
                    "project_code": project_code,
                    "original_source_file": artifact,
                    "original_evidence_path": artifact,
                },
            )
        )
        self.service.store.mark_mapping_pending(
            record_id="rec-cquae-rollback-original",
            revision_id=int(original["revision_id"]),
            project_code=project_code,
            payload={"missing": "source_type"},
        )

        def _ingest_unrelated(_item, *, _connection=None):
            self.assertIsNotNone(_connection)
            stored = self.service.store.upsert_record_with_mapping_pending(
                IngestedRecord(
                    record_id="rec-unrelated-replacement",
                    revision_hash="hash-unrelated-replacement",
                    record_family="deal",
                    project_code="G32026CQ9999999",
                    project_name="不相关成交记录",
                    project_type="股权转让",
                    exchange="cquae",
                    listing_date="2026-07-03",
                    state="ready",
                    source_file=artifact,
                    archive_path=artifact,
                    parser_payload={"项目编号": "G32026CQ9999999"},
                    postprocess_payload={
                        "项目编号": "G32026CQ9999999",
                        "项目类型": "股权转让",
                    },
                    findings=[],
                    canonical_record={
                        "record_family": "deal",
                        "business_identity": {
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                        },
                        "source_identity": {"source_id": "cquae"},
                        "canonical_fields": {
                            "project_code": "G32026CQ9999999",
                            "project_type": "股权转让",
                        },
                    },
                    source_identity={
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "source_id": "cquae",
                        "project_code": "G32026CQ9999999",
                        "original_source_file": artifact,
                        "original_evidence_path": artifact,
                    },
                ),
                _connection=_connection,
            )
            return {"record_id": stored["record_id"], "state": "ready"}

        with patch.object(self.service, "_build_ingest_runner") as mocked_runner_builder:
            mocked_runner_builder.return_value.ingest.side_effect = _ingest_unrelated
            with self.assertRaisesRegex(ValueError, "project_code mismatch"):
                self.service.reprocess_record("rec-cquae-rollback-original")

        original_after = self.service.store.get_record("rec-cquae-rollback-original")
        self.assertEqual(original_after["state"], "pending_mapping")
        self.assertEqual(original_after["revision_id"], original["revision_id"])
        self.assertEqual(original_after["last_operation_kind"], "reprocess")
        self.assertEqual(original_after["last_operation_code"], "failed")
        self.assertEqual(self.service.store.count_pending_mappings(), 1)
        with self.assertRaises(KeyError):
            self.service.store.get_record("rec-unrelated-replacement")
        with sqlite3.connect(self.service.store.db_path) as conn:
            replacement_revision_count = conn.execute(
                "SELECT COUNT(*) FROM record_revisions WHERE record_id = ?",
                ("rec-unrelated-replacement",),
            ).fetchone()[0]
            reprocess_audit_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM audit_log
                WHERE action IN ('record_superseded_by_reprocess', 'record_reprocessed')
                """
            ).fetchone()[0]
        self.assertEqual(replacement_revision_count, 0)
        self.assertEqual(reprocess_audit_count, 0)

    def test_reprocess_cquae_legacy_deal_twice_preserves_archive_and_sidecar_identity(self) -> None:
        self.service = self._migrated_service()
        deal_archive_root = os.path.join(self.archive_root, "deal")
        self.service.set_basic_settings(
            {
                "archive_root": self.archive_root,
                "deal_archive_root": deal_archive_root,
            }
        )
        project_code = "G32026CQ1000062"
        project_name = "长安福特新能源汽车科技有限公司40%股权"
        source_url = "https://www.cquae.com/CquaeNews/cjgs/Show.cshtml?id=54750"
        artifact_path, _ = _canonical_archive_target(
            archive_root=deal_archive_root,
            project_code=project_code,
            project_name=project_name,
            listing_date="2026-07-02",
            source_file=os.path.join(self.temp_dir.name, "incoming-cquae.html"),
            record_family="deal",
            business_id="deal_equity_transfer",
            source_id="cquae",
        )
        os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
        html = (
            "<html><head><meta charset='utf-8'>"
            f"<title>{project_name} - 重庆产权交易网</title>"
            "</head><body><div>交易结果公示</div><table>"
            f"<tr><th>标的名称</th><td>{project_name}</td></tr>"
            f"<tr><th>项目编号</th><td>{project_code}</td></tr>"
            "<tr><th>成交日期</th><td>2026/7/2</td></tr>"
            "<tr><th>交易价格（万元）</th><td>15384.92</td></tr>"
            "</table></body></html>"
        )
        original_html_bytes = html.encode("utf-8")
        with open(artifact_path, "wb") as handle:
            handle.write(original_html_bytes)
        sidecar_path = os.path.splitext(artifact_path)[0] + ".json"
        with open(sidecar_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "save_status": "complete",
                    "metadata": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "business_label": "股权转让",
                        "source_id": "cquae",
                        "source_url": source_url,
                        "project_code": project_code,
                        "project_name": project_name,
                        "deal_date": "2026-07-02",
                        "collection_date": "2026-07-06",
                    },
                },
                handle,
                ensure_ascii=False,
            )
        original = self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-cquae-legacy-listing",
                revision_hash="hash-cquae-legacy-listing",
                record_family="listing",
                project_code=project_code,
                project_name=project_name,
                project_type="股权转让",
                exchange="重交所",
                listing_date="2026-07-02",
                state="pending_mapping",
                source_file=artifact_path,
                archive_path=artifact_path,
                parser_payload={
                    "项目编号": project_code,
                    "项目名称": project_name,
                    "项目类型": "股权转让",
                    "page_url": source_url,
                },
                postprocess_payload={
                    "项目编号": project_code,
                    "项目名称": project_name,
                    "项目类型": "股权转让",
                },
                findings=[],
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                    },
                    "source_identity": {"source_id": "cquae"},
                    "canonical_fields": {
                        "project_code": project_code,
                        "project_type": "股权转让",
                    },
                },
                source_identity={
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "source_id": "cquae",
                    "exchange": "重交所",
                    "source_url": source_url,
                    "project_code": project_code,
                    "candidate_tokens": [
                        f"project_code:{project_code}",
                        f"page_url:{source_url}",
                    ],
                    "original_source_file": artifact_path,
                    "original_evidence_path": artifact_path,
                },
            )
        )
        self.service.store.mark_mapping_pending(
            record_id="rec-cquae-legacy-listing",
            revision_id=int(original["revision_id"]),
            project_code=project_code,
            payload={"missing": "source_type"},
        )

        first = self.service.reprocess_record("rec-cquae-legacy-listing")

        first_record = self.service.store.get_record(str(first["record_id"]))
        original_record = self.service.store.get_record("rec-cquae-legacy-listing")
        self.assertEqual(first_record["record_family"], "deal")
        self.assertEqual(first_record["business_id"], "deal_equity_transfer")
        self.assertEqual(original_record["state"], "skipped")
        self.assertEqual(original_record["last_error_type"], "superseded_by_record")
        self.assertEqual(self.service.store.count_pending_mappings(), 0)
        self.assertEqual(os.path.commonpath((deal_archive_root, first["archive_path"])), deal_archive_root)
        self.assertTrue(os.path.isfile(artifact_path))
        self.assertTrue(os.path.isfile(sidecar_path))
        with open(artifact_path, "rb") as handle:
            self.assertEqual(handle.read(), original_html_bytes)
        with open(sidecar_path, "r", encoding="utf-8") as handle:
            bound_sidecar = json.load(handle)
        self.assertEqual(
            bound_sidecar["archive_content_sha256"],
            hashlib.sha256(original_html_bytes).hexdigest(),
        )
        self.assertEqual(bound_sidecar["archive_content_bytes"], len(original_html_bytes))

        second = self.service.reprocess_record(str(first["record_id"]))

        second_record = self.service.store.get_record(str(second["record_id"]))
        self.assertEqual(second_record["record_family"], "deal")
        self.assertEqual(second_record["business_id"], "deal_equity_transfer")
        self.assertTrue(os.path.isfile(artifact_path))
        self.assertTrue(os.path.isfile(sidecar_path))
        with open(artifact_path, "rb") as handle:
            self.assertEqual(handle.read(), original_html_bytes)
        with open(sidecar_path, "r", encoding="utf-8") as handle:
            second_sidecar = json.load(handle)
        self.assertEqual(
            second_sidecar["archive_content_sha256"],
            hashlib.sha256(original_html_bytes).hexdigest(),
        )
        self.assertEqual(second_sidecar["archive_content_bytes"], len(original_html_bytes))

    def test_repair_missing_archives_once_is_report_only_and_keeps_managed_raw_file(self) -> None:
        self.service = self._migrated_service()
        raw_dir = Path(self.config.DATA_ROOT) / "raw"
        archive_dir = Path(self.config.ARCHIVE_ROOT) / "2026年4月"
        raw_dir.mkdir(parents=True, exist_ok=True)
        archive_dir.mkdir(parents=True, exist_ok=True)
        raw_file = raw_dir / "G32026SH1000999-raw.html"
        archive_file = archive_dir / "G32026SH1000999-archive.html"
        raw_file.write_text("<html>raw</html>", encoding="utf-8")
        archive_file.write_text("<html>archive</html>", encoding="utf-8")
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-direct-repair-probe",
                revision_hash="hash-direct-repair-probe",
                project_code="G32026SH1000999",
                project_name="直接修复探针",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-20",
                state="ready",
                source_file=str(raw_file),
                archive_path=str(archive_file),
                parser_payload={"项目编号": "G32026SH1000999", "项目名称": "直接修复探针", "项目类型": "股权转让"},
                postprocess_payload={"项目编号": "G32026SH1000999", "项目名称": "直接修复探针", "项目类型": "股权转让"},
                findings=[],
            )
        )

        before_journal = self.service.store.list_operation_journals(limit=20)
        self.service._repair_missing_archives_once()
        after_journal = self.service.store.list_operation_journals(limit=20)
        record = self.service.store.get_record("rec-direct-repair-probe")

        self.assertTrue(raw_file.exists())
        self.assertEqual(record["source_file"], str(raw_file))
        self.assertEqual(record["archive_path"], str(archive_file))
        self.assertEqual(len(before_journal), len(after_journal))

    def test_repair_missing_archives_once_keeps_legacy_submission_path_unchanged(self) -> None:
        self.service = self._migrated_service()
        archive_month_dir = os.path.join(self.config.ARCHIVE_ROOT, "2026年4月")
        os.makedirs(archive_month_dir, exist_ok=True)
        archive_file = os.path.join(archive_month_dir, "G32026SH1000121-0-上海新世界大酒店有限公司100%股权.html")
        with open(archive_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>archive</body></html>")

        legacy_submission_file = os.path.join(self.config.APP_HOME, "submission", "2026年4月", os.path.basename(archive_file))
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-legacy-submission",
                revision_hash="hash-legacy-submission",
                project_code="G32026SH1000121-0",
                project_name="上海新世界大酒店有限公司100%股权",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-20",
                state="ready",
                source_file=legacy_submission_file,
                archive_path=legacy_submission_file,
                parser_payload={"项目编号": "G32026SH1000121-0", "项目名称": "上海新世界大酒店有限公司100%股权", "项目类型": "股权转让"},
                postprocess_payload={"项目编号": "G32026SH1000121-0", "项目名称": "上海新世界大酒店有限公司100%股权", "项目类型": "股权转让"},
                findings=[],
            )
        )

        self.service._repair_missing_archives_once()
        record = self.service.store.get_record("rec-legacy-submission")

        self.assertEqual(record["archive_path"], legacy_submission_file)
        self.assertEqual(record["source_file"], legacy_submission_file)

    def test_startup_archive_report_uses_evidence_verdict_for_missing_archive_with_existing_source(self) -> None:
        self.service = self._migrated_service()
        raw_file = os.path.join(self.config.DATA_ROOT, "raw", "legacy-source.html")
        missing_archive = os.path.join(self.config.ARCHIVE_ROOT, "missing-authoritative.html")
        os.makedirs(os.path.dirname(raw_file), exist_ok=True)
        with open(raw_file, "wb") as handle:
            handle.write(SAFE_HTML_FIXTURE)
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-startup-stale-reference",
                revision_hash="hash-startup-stale-reference",
                project_code="G32026SH1000777",
                project_name="启动证据诊断挂牌",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-18",
                state="ready",
                source_file=raw_file,
                archive_path=missing_archive,
                parser_payload={"项目编号": "G32026SH1000777", "项目名称": "启动证据诊断挂牌", "项目类型": "股权转让"},
                postprocess_payload={"项目编号": "G32026SH1000777", "项目名称": "启动证据诊断挂牌", "项目类型": "股权转让"},
                source_identity={
                    "record_family": "listing",
                    "source_id": "sse",
                    "business_id": "equity_transfer",
                    "project_code": "G32026SH1000777",
                },
                findings=[],
            )
        )

        with patch.object(self.service.pipeline_repository, "add_audit_entry") as mocked_audit:
            self.service._repair_missing_archives_once()

        mocked_audit.assert_called_once()
        action, payload = mocked_audit.call_args.args
        self.assertEqual(action, "missing_archive_repair_deferred")
        self.assertEqual(payload["evidence_verdict_counts"], {"stale_reference": 1})
        self.assertEqual(payload["evidence_reason_counts"], {"authoritative_artifact_missing": 1})
        self.assertNotIn("source_exists", payload)
        self.assertNotIn("source_file", payload)
        self.assertTrue(payload["report_only"])
        self.assertEqual(self.service.store.get_record("rec-startup-stale-reference")["archive_path"], missing_archive)
        self.assertTrue(os.path.exists(raw_file))

    def test_repair_missing_archives_once_retries_after_report_failure(self) -> None:
        self.service = self._migrated_service()
        raw_file = os.path.join(self.config.DATA_ROOT, "raw", "retry-source.html")
        missing_archive = os.path.join(self.config.ARCHIVE_ROOT, "retry-missing.html")
        os.makedirs(os.path.dirname(raw_file), exist_ok=True)
        with open(raw_file, "wb") as handle:
            handle.write(SAFE_HTML_FIXTURE)
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-startup-retry-stale-reference",
                revision_hash="hash-startup-retry-stale-reference",
                project_code="G32026SH1000888",
                project_name="启动证据重试诊断",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-18",
                state="ready",
                source_file=raw_file,
                archive_path=missing_archive,
                parser_payload={"项目编号": "G32026SH1000888", "项目名称": "启动证据重试诊断", "项目类型": "股权转让"},
                postprocess_payload={"项目编号": "G32026SH1000888", "项目名称": "启动证据重试诊断", "项目类型": "股权转让"},
                source_identity={
                    "record_family": "listing",
                    "source_id": "sse",
                    "business_id": "equity_transfer",
                    "project_code": "G32026SH1000888",
                },
                findings=[],
            )
        )

        with patch.object(
            self.service.pipeline_repository,
            "add_audit_entry",
            side_effect=[RuntimeError("audit unavailable"), None],
        ) as mocked_audit:
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                self.service._repair_missing_archives_once()
            self.service._repair_missing_archives_once()

        self.assertEqual(mocked_audit.call_count, 2)

    def test_repair_missing_archives_once_ignores_concurrent_second_runner(self) -> None:
        self.service = self._migrated_service()
        entered = threading.Event()
        release = threading.Event()

        def slow_iter_latest_records(*, sort: str = "recent"):
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return []

        with (
            patch.object(self.service.pipeline_repository, "iter_latest_records", side_effect=slow_iter_latest_records) as mocked_iter,
            patch.object(self.service.pipeline_repository, "add_audit_entry") as mocked_audit,
        ):
            first = threading.Thread(target=self.service._repair_missing_archives_once)
            first.start()
            self.assertTrue(entered.wait(timeout=5))
            self.service._repair_missing_archives_once()
            release.set()
            first.join(timeout=5)

        self.assertFalse(first.is_alive())
        mocked_iter.assert_called_once_with(sort="recent")
        mocked_audit.assert_not_called()

    def test_list_records_does_not_repair_unique_base_code_title_archive_match_before_render(self) -> None:
        self.service = self._migrated_service()
        archive_month_dir = os.path.join(self.config.ARCHIVE_ROOT, "2026年4月")
        os.makedirs(archive_month_dir, exist_ok=True)
        archive_file = os.path.join(
            archive_month_dir,
            "GR2026SH1000324-6-淮安市淮阴医院有限公司部分资产（一台双源CT机）.html",
        )
        with open(archive_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>archive</body></html>")

        legacy_submission_file = os.path.join(
            self.config.APP_HOME,
            "submission",
            "2026年4月",
            "GR2026SH1000324-7-淮安市淮阴医院有限公司部分资产（一台双源CT机）.html",
        )
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-drifted-archive-name",
                revision_hash="hash-drifted-archive-name",
                project_code="GR2026SH1000324-7",
                project_name="淮安市淮阴医院有限公司部分资产（一台双源CT机）",
                project_type="实物资产",
                exchange="shanghai",
                listing_date="2026-04-09",
                state="pending_mapping",
                source_file=legacy_submission_file,
                archive_path=legacy_submission_file,
                parser_payload={"项目编号": "GR2026SH1000324-7", "项目名称": "淮安市淮阴医院有限公司部分资产（一台双源CT机）", "项目类型": "实物资产"},
                postprocess_payload={"项目编号": "GR2026SH1000324-7", "项目名称": "淮安市淮阴医院有限公司部分资产（一台双源CT机）", "项目类型": "实物资产"},
                findings=[],
            )
        )

        payload = self.service.list_records(
            {
                "record_family": "listing",
                "business_id": "all",
                "exchange": "all",
                "state": "all",
            }
        )
        record = self.service.store.get_record("rec-drifted-archive-name")

        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["rows"][0]["record_id"], "rec-drifted-archive-name")
        self.assertFalse(payload["rows"][0]["has_local_artifact"])
        self.assertEqual(payload["rows"][0]["local_artifact_name"], "")
        self.assertEqual(record["archive_path"], legacy_submission_file)
        self.assertEqual(record["source_file"], legacy_submission_file)
        self.assertTrue(os.path.exists(archive_file))

    def test_reveal_record_folder_does_not_repair_or_delete_raw_snapshot(self) -> None:
        self.service = self._migrated_service()
        archive_file = os.path.join(self.config.ARCHIVE_ROOT, "archive.html")
        raw_file = os.path.join(self.config.DATA_ROOT, "raw", "source.html")
        os.makedirs(os.path.dirname(archive_file), exist_ok=True)
        os.makedirs(os.path.dirname(raw_file), exist_ok=True)
        with open(archive_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>archive</body></html>")
        with open(raw_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>raw</body></html>")
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-readonly-reveal",
                revision_hash="hash-readonly-reveal",
                project_code="G32026SH1000999",
                project_name="读路径不修复",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-09",
                state="ready",
                source_file=raw_file,
                archive_path=archive_file,
                parser_payload={"项目编号": "G32026SH1000999", "项目名称": "读路径不修复", "项目类型": "股权转让"},
                postprocess_payload={"项目编号": "G32026SH1000999", "项目名称": "读路径不修复", "项目类型": "股权转让"},
                findings=[],
            )
        )

        with patch("desktop_backend.app_service.reveal_in_file_manager", return_value=archive_file):
            result = self.service.reveal_record_folder("rec-readonly-reveal")
        record = self.service.store.get_record("rec-readonly-reveal")

        self.assertEqual(result["record_id"], "rec-readonly-reveal")
        self.assertEqual(record["source_file"], raw_file)
        self.assertEqual(record["archive_path"], archive_file)
        self.assertTrue(os.path.exists(raw_file))
        self.assertTrue(os.path.exists(archive_file))

    def test_reveal_record_folder_uses_verified_evidence_openable_path(self) -> None:
        self.service = self._migrated_service()
        authoritative_file = os.path.join(self.config.ARCHIVE_ROOT, "authoritative.html")
        openable_file = os.path.join(self.config.ARCHIVE_ROOT, "openable.html")
        os.makedirs(os.path.dirname(authoritative_file), exist_ok=True)
        with open(authoritative_file, "wb") as handle:
            handle.write(SAFE_HTML_FIXTURE)
        with open(openable_file, "wb") as handle:
            handle.write(SAFE_HTML_FIXTURE)
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-verified-openable",
                revision_hash="hash-verified-openable",
                project_code="G32026SH1000777",
                project_name="证据打开路径",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-09",
                state="ready",
                source_file=authoritative_file,
                archive_path=authoritative_file,
                parser_payload={"项目编号": "G32026SH1000777", "项目名称": "证据打开路径"},
                postprocess_payload={"项目编号": "G32026SH1000777", "项目名称": "证据打开路径", "项目类型": "股权转让"},
                findings=[],
            )
        )

        with (
            patch("desktop_backend.app_service.resolve_artifact_evidence_verdict") as mocked_verdict,
            patch("desktop_backend.app_service.reveal_in_file_manager", return_value=openable_file) as mocked_reveal,
        ):
            mocked_verdict.return_value.status = "verified"
            mocked_verdict.return_value.reason_code = "identity_verified_artifact_present"
            mocked_verdict.return_value.authoritative_path = authoritative_file
            mocked_verdict.return_value.inspection_openable_path = openable_file
            result = self.service.reveal_record_folder("rec-verified-openable")

        mocked_reveal.assert_called_once_with(openable_file, reveal=True)
        self.assertEqual(result["path"], openable_file)
        self.assertEqual(result["artifact_name"], "openable.html")

    def test_verified_listing_ready_journey_opens_file_and_exports_from_temp_workspace(self) -> None:
        self.service = self._migrated_service()
        artifact = os.path.join(self.config.ARCHIVE_ROOT, "verified-listing.html")
        os.makedirs(os.path.dirname(artifact), exist_ok=True)
        with open(artifact, "wb") as handle:
            handle.write(SAFE_HTML_FIXTURE)
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-verified-listing-ready",
                revision_hash="hash-verified-listing-ready",
                project_code="G32026SH1000666",
                project_name="证据已验证挂牌",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-09",
                state="ready",
                source_file=artifact,
                archive_path=artifact,
                parser_payload={
                    "项目编号": "G32026SH1000666",
                    "项目名称": "证据已验证挂牌",
                    "项目类型": "股权转让",
                },
                postprocess_payload={
                    "项目编号": "G32026SH1000666",
                    "项目名称": "证据已验证挂牌",
                    "项目类型": "股权转让",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "business_id": "equity_transfer",
                        "raw_business_label": "股权转让",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SH1000666",
                        "project_name": "证据已验证挂牌",
                        "project_type": "股权转让",
                        "status": "挂牌中",
                        "exchange": "上交所",
                        "start_date": "2026-04-09",
                        "price": "108.00",
                        "seller": "上海测试公司",
                        "source_type": "国资",
                    },
                },
                canonical_projection={
                    "项目编号": "G32026SH1000666",
                    "项目名称": "证据已验证挂牌",
                    "项目类型": "股权转让",
                    "项目状态": "挂牌中",
                    "交易所": "上交所",
                    "挂牌开始日期": "2026-04-09",
                    "挂牌价格": "108.00",
                    "转让方": "上海测试公司",
                    "类型": "国资",
                },
                source_identity={
                    "record_family": "listing",
                    "source_id": "sse",
                    "business_id": "equity_transfer",
                    "project_code": "G32026SH1000666",
                    "project_name": "证据已验证挂牌",
                    "listing_date": "2026-04-09",
                    "original_source_file": artifact,
                    "original_evidence_path": artifact,
                },
                findings=[],
            )
        )

        row = self.service.list_records(
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "all",
                "state": "ready",
            }
        )["rows"][0]
        with patch("desktop_backend.app_service.reveal_in_file_manager", return_value=artifact) as mocked_reveal:
            reveal_result = self.service.reveal_record_folder("rec-verified-listing-ready")
        export_result = self.service.run_export(
            {
                "scope": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "all",
                    "state": "ready",
                },
                "requested_export_mode": "full",
            }
        )

        self.assertEqual(row["record_id"], "rec-verified-listing-ready")
        self.assertTrue(row["canonical_ready"])
        self.assertEqual(row["evidence_status"], "verified")
        self.assertTrue(row["has_local_artifact"])
        self.assertTrue(row["exportable"])
        self.assertTrue(row["export_eligible"])
        mocked_reveal.assert_called_once_with(artifact, reveal=True)
        self.assertEqual(reveal_result["path"], artifact)
        self.assertEqual(export_result["status"], "completed")
        self.assertEqual(export_result["new_records"], 1)
        self.assertEqual(len(export_result["artifacts"]), 1)
        self.assertTrue(Path(export_result["artifacts"][0]).is_file())
        self.assertTrue(
            os.path.abspath(export_result["artifacts"][0]).startswith(
                os.path.abspath(self.config.OUTPUT_EXCEL_DIR)
            )
        )

    def test_stale_reference_ready_journey_blocks_open_export_and_download_skip(self) -> None:
        from peap.download_artifact_audit import build_download_artifact_audit
        from peap.download_tasks import build_task_registry

        self.service = self._migrated_service()
        old_source = os.path.join(self.config.ARCHIVE_ROOT, "legacy-source.html")
        missing_archive = os.path.join(self.config.ARCHIVE_ROOT, "missing-authoritative.html")
        os.makedirs(os.path.dirname(old_source), exist_ok=True)
        with open(old_source, "wb") as handle:
            handle.write(SAFE_HTML_FIXTURE)
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-stale-reference-ready",
                revision_hash="hash-stale-reference-ready",
                project_code="G32026SH1000667",
                project_name="权威归档缺失挂牌",
                project_type="股权转让",
                exchange="sse",
                listing_date="2026-04-10",
                state="ready",
                source_file=old_source,
                archive_path=missing_archive,
                parser_payload={
                    "项目编号": "G32026SH1000667",
                    "项目名称": "权威归档缺失挂牌",
                    "项目类型": "股权转让",
                },
                postprocess_payload={
                    "项目编号": "G32026SH1000667",
                    "项目名称": "权威归档缺失挂牌",
                    "项目类型": "股权转让",
                },
                canonical_record={
                    "record_family": "listing",
                    "business_identity": {
                        "business_id": "equity_transfer",
                        "raw_business_label": "股权转让",
                    },
                    "canonical_fields": {
                        "project_code": "G32026SH1000667",
                        "project_name": "权威归档缺失挂牌",
                        "project_type": "股权转让",
                        "status": "挂牌中",
                        "exchange": "上交所",
                        "start_date": "2026-04-10",
                        "price": "109.00",
                        "seller": "上海测试公司",
                        "source_type": "国资",
                    },
                },
                canonical_projection={
                    "项目编号": "G32026SH1000667",
                    "项目名称": "权威归档缺失挂牌",
                    "项目类型": "股权转让",
                    "项目状态": "挂牌中",
                    "交易所": "上交所",
                    "挂牌开始日期": "2026-04-10",
                    "挂牌价格": "109.00",
                    "转让方": "上海测试公司",
                    "类型": "国资",
                },
                source_identity={
                    "record_family": "listing",
                    "source_id": "sse",
                    "business_id": "equity_transfer",
                    "project_code": "G32026SH1000667",
                    "project_name": "权威归档缺失挂牌",
                    "listing_date": "2026-04-10",
                    "original_source_file": old_source,
                    "original_evidence_path": missing_archive,
                },
                findings=[],
            )
        )

        row = self.service.list_records(
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "all",
                "state": "ready",
            }
        )["rows"][0]
        with (
            patch("desktop_backend.app_service.reveal_in_file_manager") as mocked_reveal,
            self.assertRaises(Exception) as captured,
        ):
            self.service.reveal_record_folder("rec-stale-reference-ready")
        export_result = self.service.run_export(
            {
                "scope": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "all",
                    "state": "ready",
                },
                "requested_export_mode": "full",
            }
        )
        audit = build_download_artifact_audit(
            self.config,
            args=SimpleNamespace(
                exchange="sse",
                record_family="listing",
                business_id="equity_transfer",
                start_date="2026-04-01",
                end_date="2026-04-30",
                dry_run=True,
            ),
            tasks=[build_task_registry()["sse:listing:equity_transfer"]],
        )
        task_audit = audit.for_task("sse:listing:equity_transfer")

        self.assertEqual(row["record_id"], "rec-stale-reference-ready")
        self.assertTrue(row["canonical_ready"])
        self.assertEqual(row["evidence_status"], "stale_reference")
        self.assertEqual(row["evidence_verdict"]["reason_code"], "authoritative_artifact_missing")
        self.assertEqual(row["evidence_verdict"]["authoritative_path"], missing_archive)
        self.assertEqual(row["evidence_verdict"]["inspection_openable_path"], "")
        self.assertFalse(row["has_local_artifact"])
        self.assertFalse(row["exportable"])
        self.assertFalse(row["export_eligible"])
        self.assertEqual(getattr(captured.exception, "error_code", ""), "record_artifact_not_found")
        self.assertEqual(captured.exception.details.get("evidence_status"), "stale_reference")
        mocked_reveal.assert_not_called()
        self.assertEqual(export_result["status"], "empty")
        self.assertEqual(export_result["new_records"], 0)
        self.assertEqual(export_result["artifacts"], [])
        self.assertEqual(audit.stale_count, 1)
        self.assertIsNotNone(task_audit)
        self.assertTrue(task_audit.intersects(dt.date(2026, 4, 10), dt.date(2026, 4, 10)))

    def test_invalid_shell_ready_journey_allows_inspection_only_without_export_or_download_skip(self) -> None:
        from peap.download_artifact_audit import build_download_artifact_audit
        from peap.download_tasks import build_task_registry

        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": self.app_home,
                "PEAP_DATA_ROOT": self.data_root,
                "PEAP_ARCHIVE_ROOT": self.archive_root,
                "PEAP_EXPORT_ROOT": self.export_root,
                "PEAP_CACHE_DIR": self.cache_dir,
                "PEAP_STREAMING_DB_PATH": self.streaming_db_path,
            },
            clear=False,
        ):
            self.service = self._migrated_service()
            artifact = os.path.join(self.config.ARCHIVE_ROOT, "invalid-shell-listing.html")
            os.makedirs(os.path.dirname(artifact), exist_ok=True)
            with open(artifact, "wb") as handle:
                handle.write(SSE_DEAL_NOTICE_SHELL_FIXTURE)
            self.service.store.upsert_record(
                IngestedRecord(
                    record_id="rec-invalid-shell-ready",
                    revision_hash="hash-invalid-shell-ready",
                    project_code="G32026SH1000668",
                    project_name="壳页面挂牌",
                    project_type="股权转让",
                    exchange="sse",
                    listing_date="2026-04-11",
                    state="ready",
                    source_file=artifact,
                    archive_path=artifact,
                    parser_payload={
                        "项目编号": "G32026SH1000668",
                        "项目名称": "壳页面挂牌",
                        "项目类型": "股权转让",
                    },
                    postprocess_payload={
                        "项目编号": "G32026SH1000668",
                        "项目名称": "壳页面挂牌",
                        "项目类型": "股权转让",
                    },
                    canonical_record={
                        "record_family": "listing",
                        "business_identity": {
                            "business_id": "equity_transfer",
                            "raw_business_label": "股权转让",
                        },
                        "canonical_fields": {
                            "project_code": "G32026SH1000668",
                            "project_name": "壳页面挂牌",
                            "project_type": "股权转让",
                            "status": "挂牌中",
                            "exchange": "上交所",
                            "start_date": "2026-04-11",
                            "price": "110.00",
                            "seller": "上海测试公司",
                            "source_type": "国资",
                        },
                    },
                    canonical_projection={
                        "项目编号": "G32026SH1000668",
                        "项目名称": "壳页面挂牌",
                        "项目类型": "股权转让",
                        "项目状态": "挂牌中",
                        "交易所": "上交所",
                        "挂牌开始日期": "2026-04-11",
                        "挂牌价格": "110.00",
                        "转让方": "上海测试公司",
                        "类型": "国资",
                    },
                    source_identity={
                        "record_family": "listing",
                        "source_id": "sse",
                        "business_id": "equity_transfer",
                        "project_code": "G32026SH1000668",
                        "project_name": "壳页面挂牌",
                        "listing_date": "2026-04-11",
                        "original_source_file": artifact,
                        "original_evidence_path": artifact,
                    },
                    findings=[],
                )
            )

            row = self.service.list_records(
                {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "all",
                    "state": "ready",
                }
            )["rows"][0]
            with (
                patch("desktop_backend.app_service.reveal_in_file_manager") as mocked_reveal,
                self.assertRaises(Exception) as captured,
            ):
                self.service.reveal_record_folder("rec-invalid-shell-ready")
            export_result = self.service.run_export(
                {
                    "scope": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "exchange": "all",
                        "state": "ready",
                    },
                    "requested_export_mode": "full",
                }
            )
            audit = build_download_artifact_audit(
                self.config,
                args=SimpleNamespace(
                    exchange="sse",
                    record_family="listing",
                    business_id="equity_transfer",
                    start_date="2026-04-01",
                    end_date="2026-04-30",
                    dry_run=True,
                ),
                tasks=[build_task_registry()["sse:listing:equity_transfer"]],
            )
            task_audit = audit.for_task("sse:listing:equity_transfer")

        self.assertEqual(row["record_id"], "rec-invalid-shell-ready")
        self.assertTrue(row["canonical_ready"])
        self.assertEqual(row["evidence_status"], "invalid_shell")
        self.assertEqual(row["evidence_verdict"]["reason_code"], "sse_deal_notice_shell")
        self.assertEqual(row["evidence_verdict"]["authoritative_path"], artifact)
        self.assertEqual(row["evidence_verdict"]["inspection_openable_path"], artifact)
        self.assertTrue(row["has_local_artifact"])
        self.assertFalse(row["exportable"])
        self.assertFalse(row["export_eligible"])
        self.assertEqual(getattr(captured.exception, "error_code", ""), "record_artifact_not_found")
        self.assertEqual(captured.exception.details.get("evidence_status"), "invalid_shell")
        mocked_reveal.assert_not_called()
        self.assertEqual(export_result["status"], "empty")
        self.assertEqual(export_result["new_records"], 0)
        self.assertEqual(export_result["artifacts"], [])
        self.assertEqual(audit.stale_count, 1)
        self.assertIsNotNone(task_audit)
        self.assertEqual(task_audit.stale_records[0].evidence_verdict["status"], "invalid_shell")
        self.assertTrue(task_audit.intersects(dt.date(2026, 4, 11), dt.date(2026, 4, 11)))

    def test_present_unverified_no_project_code_journey_allows_inspection_without_export_or_dedupe(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": self.app_home,
                "PEAP_DATA_ROOT": self.data_root,
                "PEAP_ARCHIVE_ROOT": self.archive_root,
                "PEAP_EXPORT_ROOT": self.export_root,
                "PEAP_CACHE_DIR": self.cache_dir,
                "PEAP_STREAMING_DB_PATH": self.streaming_db_path,
            },
            clear=False,
        ):
            self.service = self._migrated_service()
            artifact = os.path.join(self.config.ARCHIVE_ROOT, "legacy-path-hash-listing.html")
            os.makedirs(os.path.dirname(artifact), exist_ok=True)
            with open(artifact, "wb") as handle:
                handle.write(SAFE_HTML_FIXTURE)
            legacy_identity = "source:" + hashlib.sha1(artifact.encode("utf-8")).hexdigest()
            self.service.store.upsert_record(
                IngestedRecord(
                    record_id="rec-present-unverified-no-project-code",
                    revision_hash="hash-present-unverified-no-project-code",
                    project_code="",
                    project_name="路径哈希遗留挂牌",
                    project_type="股权转让",
                    exchange="sse",
                    listing_date="2026-04-12",
                    state="ready",
                    source_file=artifact,
                    archive_path=artifact,
                    parser_payload={
                        "项目名称": "路径哈希遗留挂牌",
                        "项目类型": "股权转让",
                    },
                    postprocess_payload={
                        "项目名称": "路径哈希遗留挂牌",
                        "项目类型": "股权转让",
                    },
                    canonical_record={
                        "record_family": "listing",
                        "business_identity": {
                            "business_id": "equity_transfer",
                            "raw_business_label": "股权转让",
                        },
                        "canonical_fields": {
                            "project_name": "路径哈希遗留挂牌",
                            "project_type": "股权转让",
                            "status": "挂牌中",
                            "exchange": "上交所",
                            "start_date": "2026-04-12",
                            "price": "111.00",
                            "seller": "上海测试公司",
                            "source_type": "国资",
                        },
                    },
                    canonical_projection={
                        "项目名称": "路径哈希遗留挂牌",
                        "项目类型": "股权转让",
                        "项目状态": "挂牌中",
                        "交易所": "上交所",
                        "挂牌开始日期": "2026-04-12",
                        "挂牌价格": "111.00",
                        "转让方": "上海测试公司",
                        "类型": "国资",
                    },
                    source_identity={
                        "record_family": "listing",
                        "source_id": "sse",
                        "business_id": "equity_transfer",
                        "project_code": legacy_identity,
                        "project_name": "路径哈希遗留挂牌",
                        "listing_date": "2026-04-12",
                        "original_source_file": artifact,
                        "original_evidence_path": artifact,
                        "candidate_tokens": [f"project_code:{legacy_identity}"],
                    },
                    findings=[],
                )
            )

            row = self.service.list_records(
                {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "all",
                    "state": "ready",
                }
            )["rows"][0]
            with patch("desktop_backend.app_service.reveal_in_file_manager", return_value=artifact) as mocked_reveal:
                reveal_result = self.service.reveal_record_folder("rec-present-unverified-no-project-code")
            export_result = self.service.run_export(
                {
                    "scope": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "exchange": "all",
                        "state": "ready",
                    },
                    "requested_export_mode": "full",
                }
            )
            default_tokens = self.service.store.list_existing_candidate_tokens(states=["ready"])
            dedupe_tokens = self.service.store.list_existing_candidate_tokens(
                states=["ready"],
                require_existing_artifact=True,
            )
            dedupe_codes = self.service.store.list_existing_project_codes(
                states=["ready"],
                require_existing_artifact=True,
            )
            updated_record = self.service.store.get_record(
                "rec-present-unverified-no-project-code"
            )
            field_missing_tokens = self.service.store.list_existing_candidate_tokens(
                states=["field_missing"]
            )

        self.assertEqual(row["record_id"], "rec-present-unverified-no-project-code")
        self.assertTrue(row["canonical_ready"])
        self.assertEqual(row["project_code"], "")
        self.assertEqual(row["evidence_status"], "present_unverified")
        self.assertEqual(row["evidence_verdict"]["identity_confidence"], "unresolved")
        self.assertEqual(row["evidence_verdict"]["reason_code"], "identity_unresolved_artifact_present")
        self.assertEqual(row["evidence_verdict"]["authoritative_path"], artifact)
        self.assertEqual(row["evidence_verdict"]["inspection_openable_path"], artifact)
        self.assertTrue(row["has_local_artifact"])
        self.assertFalse(row["exportable"])
        self.assertFalse(row["export_eligible"])
        mocked_reveal.assert_called_once_with(artifact, reveal=True)
        self.assertEqual(reveal_result["path"], artifact)
        self.assertEqual(export_result["status"], "empty")
        self.assertEqual(export_result["new_records"], 0)
        self.assertEqual(export_result["artifacts"], [])
        self.assertEqual(updated_record["state"], "field_missing")
        self.assertNotIn(f"project_code:{legacy_identity}", default_tokens)
        self.assertIn(f"project_code:{legacy_identity}", field_missing_tokens)
        self.assertNotIn(f"project_code:{legacy_identity}", dedupe_tokens)
        self.assertEqual(dedupe_codes, set())

    def test_pending_review_business_unresolved_journey_is_read_only_without_mapping_cta(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": self.app_home,
                "PEAP_DATA_ROOT": self.data_root,
                "PEAP_ARCHIVE_ROOT": self.archive_root,
                "PEAP_EXPORT_ROOT": self.export_root,
                "PEAP_CACHE_DIR": self.cache_dir,
                "PEAP_STREAMING_DB_PATH": self.streaming_db_path,
            },
            clear=False,
        ):
            self.service = self._migrated_service()
            artifact = os.path.join(self.config.ARCHIVE_ROOT, "business-unresolved-review.html")
            os.makedirs(os.path.dirname(artifact), exist_ok=True)
            with open(artifact, "wb") as handle:
                handle.write(SAFE_HTML_FIXTURE)
            self.service.store.upsert_record(
                IngestedRecord(
                    record_id="rec-pending-review-business-unresolved",
                    revision_hash="hash-pending-review-business-unresolved",
                    project_code="G32026SH1000671",
                    project_name="业务归属待复核挂牌",
                    project_type="未知",
                    exchange="sse",
                    listing_date="2026-04-15",
                    state="pending_review",
                    source_file=artifact,
                    archive_path=artifact,
                    parser_payload={
                        "项目编号": "G32026SH1000671",
                        "项目名称": "业务归属待复核挂牌",
                        "项目类型": "未知",
                    },
                    postprocess_payload={
                        "项目编号": "G32026SH1000671",
                        "项目名称": "业务归属待复核挂牌",
                        "项目类型": "未知",
                    },
                    findings=[
                        PostProcessFinding(
                            severity="warn",
                            type="business_resolution_required",
                            message="项目类型未识别，暂不能进入导出",
                            evidence={"reason_code": "unrecognized_business", "raw_business_label": "未知"},
                        )
                    ],
                    record_family="listing",
                )
            )

            review = self.service.list_review_problems(
                {
                    "problem_kind": "all",
                    "record_family": "listing",
                    "business_id": "all",
                    "exchange": "all",
                    "state": "pending_review",
                    "keyword": "",
                    "date_from": "",
                    "date_to": "",
                    "page": 1,
                    "page_size": 50,
                }
            )
            mappings = self.service.list_pending_mappings()

        self.assertEqual(review["total_count"], 1)
        row = review["rows"][0]
        self.assertEqual(row["record_id"], "rec-pending-review-business-unresolved")
        self.assertEqual(row["state"], "pending_review")
        self.assertEqual(row["problem_kind"], "project_type_unresolved")
        self.assertEqual(row["reason_code"], "unrecognized_business")
        self.assertEqual(row["actions"]["primary_action_kind"], "none")
        self.assertFalse(row["actions"]["primary_action_enabled"])
        self.assertEqual(row["actions"]["available_actions"], [])

        sections = {section["section_id"]: section for section in mappings["sections"]}
        self.assertEqual(mappings["summary"]["actionable_count"], 0)
        self.assertEqual(mappings["summary"]["mapping_gap_count"], 0)
        self.assertEqual(sections["mapping_gap_resolution"]["count"], 0)
        self.assertEqual(sections["mapping_gap_resolution"]["cta_kind"], "reprocess_pending")
        self.assertEqual(sections["mapping_gap_resolution"]["items"], [])

    def test_reveal_record_folder_rejects_invalid_evidence_with_verdict_details(self) -> None:
        self.service = self._migrated_service()
        invalid_artifact = os.path.join(self.config.ARCHIVE_ROOT, "invalid-shell.html")
        os.makedirs(os.path.dirname(invalid_artifact), exist_ok=True)
        with open(invalid_artifact, "wb") as handle:
            handle.write(SSE_DEAL_NOTICE_SHELL_FIXTURE)
        self.service.store.upsert_record(
            IngestedRecord(
                record_id="rec-invalid-reveal",
                revision_hash="hash-invalid-reveal",
                project_code="G32026SH1000888",
                project_name="无效证据打开",
                project_type="股权转让",
                exchange="shanghai",
                listing_date="2026-04-09",
                state="ready",
                source_file=invalid_artifact,
                archive_path=invalid_artifact,
                parser_payload={"项目编号": "G32026SH1000888", "项目名称": "无效证据打开"},
                postprocess_payload={"项目编号": "G32026SH1000888", "项目名称": "无效证据打开", "项目类型": "股权转让"},
                findings=[],
            )
        )

        with (
            patch("desktop_backend.app_service.reveal_in_file_manager") as mocked_reveal,
            self.assertRaises(Exception) as captured,
        ):
            self.service.reveal_record_folder("rec-invalid-reveal")

        self.assertEqual(getattr(captured.exception, "error_code", ""), "record_artifact_not_found")
        self.assertEqual(captured.exception.details.get("evidence_status"), "invalid_shell")
        self.assertEqual(captured.exception.details.get("evidence_reason_code"), "sse_deal_notice_shell")
        mocked_reveal.assert_not_called()

    def test_field_missing_acknowledged_journey_reduces_attention_without_export_eligibility(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": self.app_home,
                "PEAP_DATA_ROOT": self.data_root,
                "PEAP_ARCHIVE_ROOT": self.archive_root,
                "PEAP_EXPORT_ROOT": self.export_root,
                "PEAP_CACHE_DIR": self.cache_dir,
                "PEAP_STREAMING_DB_PATH": self.streaming_db_path,
            },
            clear=False,
        ):
            self.service = self._migrated_service()
            artifact = os.path.join(self.config.ARCHIVE_ROOT, "field-missing-ack.html")
            os.makedirs(os.path.dirname(artifact), exist_ok=True)
            with open(artifact, "wb") as handle:
                handle.write(SAFE_HTML_FIXTURE)
            self.service.store.upsert_record(
                IngestedRecord(
                    record_id="rec-field-missing-ack",
                    revision_hash="hash-field-missing-ack",
                    project_code="G32026SH1000888",
                    project_name="缺字段确认",
                    project_type="股权转让",
                    exchange="shanghai",
                    listing_date="2026-04-09",
                    state="field_missing",
                    source_file=os.path.join(self.config.DATA_ROOT, "raw", "field-missing.html"),
                    archive_path=artifact,
                    parser_payload={"项目编号": "G32026SH1000888"},
                    postprocess_payload={"项目编号": "G32026SH1000888"},
                    canonical_record={
                        "record_family": "listing",
                        "business_identity": {
                            "business_id": "equity_transfer",
                            "raw_business_label": "股权转让",
                        },
                        "canonical_fields": {
                            "project_code": "G32026SH1000888",
                            "project_name": "缺字段确认",
                            "project_type": "股权转让",
                        },
                    },
                    findings=[
                        PostProcessFinding(
                            severity="warn",
                            type="export_field_missing",
                            message="导出字段缺失：类型",
                            evidence={"missing_fields": ["类型"]},
                        )
                    ],
                    source_identity={
                        "record_family": "listing",
                        "source_id": "sse",
                        "business_id": "equity_transfer",
                        "project_code": "G32026SH1000888",
                    },
                )
            )

            before = self.service.list_records(
                {
                    "record_family": "listing",
                    "business_id": "all",
                    "exchange": "all",
                    "state": "all",
                }
            )["rows"][0]
            ack_result = self.service.acknowledge_field_missing("rec-field-missing-ack")
            reopened = AppService(
                config_obj=self.config,
                runtime_dependencies=FakeRuntimeDependencies(),
            )
            after = reopened.list_records(
                {
                    "record_family": "listing",
                    "business_id": "all",
                    "exchange": "all",
                    "state": "all",
                }
            )["rows"][0]
            record = reopened.store.get_record("rec-field-missing-ack")
            export_result = reopened.run_export(
                {
                    "scope": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "exchange": "all",
                        "state": "all",
                    },
                    "requested_export_mode": "full",
                }
            )

        self.assertEqual(before["state"], "field_missing")
        self.assertFalse(before["field_missing_acknowledgement"]["acknowledged"])
        self.assertTrue(before["attention"]["requires_attention"])
        self.assertFalse(before["export_eligible"])
        self.assertEqual(ack_result["state"], "field_missing")
        self.assertFalse(ack_result["exportable"])
        self.assertFalse(ack_result["export_eligible"])
        self.assertTrue(ack_result["field_missing_acknowledgement"]["acknowledged"])
        self.assertFalse(ack_result["attention"]["requires_attention"])
        self.assertEqual(record["state"], "field_missing")
        self.assertTrue(after["field_missing_acknowledgement"]["acknowledged"])
        self.assertFalse(after["attention"]["requires_attention"])
        self.assertFalse(after["export_eligible"])
        self.assertEqual(export_result["field_missing_blocked_records"], 1)
        self.assertEqual(export_result["new_records"] + export_result["changed_records"], 0)
        self.assertEqual(export_result["artifacts"], [])

    def test_deal_date_amount_consumer_journey_normalizes_in_export_not_artifact_truth(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": self.app_home,
                "PEAP_DATA_ROOT": self.data_root,
                "PEAP_ARCHIVE_ROOT": self.archive_root,
                "PEAP_EXPORT_ROOT": self.export_root,
                "PEAP_CACHE_DIR": self.cache_dir,
                "PEAP_STREAMING_DB_PATH": self.streaming_db_path,
            },
            clear=False,
        ):
            self.service = self._migrated_service()
            artifact = os.path.join(self.config.ARCHIVE_ROOT, "deal-date-amount-consumer.html")
            os.makedirs(os.path.dirname(artifact), exist_ok=True)
            with open(artifact, "wb") as handle:
                handle.write(SAFE_HTML_FIXTURE)
            self.service.store.upsert_record(
                IngestedRecord(
                    record_id="rec-deal-date-amount-consumer",
                    revision_hash="hash-deal-date-amount-consumer",
                    project_code="G62026SH000321",
                    project_name="成交日期金额消费者",
                    project_type="增资扩股",
                    exchange="sse",
                    listing_date="2026-05-02",
                    state="ready",
                    source_file=artifact,
                    archive_path=artifact,
                    parser_payload={
                        "project_code": "G62026SH000321",
                        "project_name": "成交日期金额消费者",
                        "project_type": "增资扩股",
                    },
                    postprocess_payload={
                        "project_code": "G62026SH000321",
                        "project_name": "成交日期金额消费者",
                        "project_type": "增资扩股",
                    },
                    canonical_record={
                        "record_family": "deal",
                        "source_identity": {
                            "source_id": "sse",
                            "business_id": "deal_capital_increase",
                        },
                        "business_identity": {
                            "business_id": "deal_capital_increase",
                            "raw_business_label": "增资扩股",
                        },
                        "canonical_fields": {
                            "project_code": "G62026SH000321",
                            "project_name": "成交日期金额消费者",
                            "project_type": "增资扩股",
                            "status": "已成交",
                            "collection_date": "2026-05-02",
                            "deal_date_basis": "collection_date",
                            "deal_date_is_imputed": True,
                        },
                        "export_extras": {
                            "增资企业名称": "上海测试增资企业",
                            "投资总金额（万元）": "5000",
                            "investors": [
                                {
                                    "name": "北京国泰新华实业有限公司等",
                                    "investment_amount": "2,800.000000",
                                    "ratio": "11.32",
                                }
                            ],
                        },
                    },
                    source_identity={
                        "record_family": "deal",
                        "source_id": "sse",
                        "business_id": "deal_capital_increase",
                        "project_code": "G62026SH000321",
                        "project_name": "成交日期金额消费者",
                        "original_source_file": artifact,
                        "original_evidence_path": artifact,
                    },
                    record_family="deal",
                    findings=[],
                )
            )

            row = self.service.list_records(
                {
                    "record_family": "deal",
                    "business_id": "deal_capital_increase",
                    "exchange": "all",
                    "state": "ready",
                }
            )["rows"][0]
            record = self.service.store.get_record("rec-deal-date-amount-consumer")
            export_result = self.service.run_export(
                {
                    "scope": {
                        "record_family": "deal",
                        "business_id": "deal_capital_increase",
                        "exchange": "all",
                        "state": "ready",
                    },
                    "requested_export_mode": "full",
                }
            )

        from peap.artifact_truth import resolve_artifact_evidence_verdict

        verdict = resolve_artifact_evidence_verdict(record)
        self.assertEqual(verdict.status, "verified")
        self.assertEqual(verdict.reason_code, "identity_verified_artifact_present")
        self.assertNotIn("deal_date", verdict.safe_evidence)
        self.assertNotIn("investment_amount", verdict.safe_evidence)
        self.assertTrue(row["canonical_ready"])
        self.assertEqual(row["evidence_status"], "verified")
        self.assertTrue(row["exportable"])
        self.assertEqual(export_result["status"], "completed")
        self.assertEqual(export_result["new_records"], 1)
        self.assertEqual(export_result["field_missing_blocked_records"], 0)
        workbook = load_workbook(export_result["artifacts"][0])
        sheet = workbook["上海联交所增资项目"]
        headers = [cell.value for cell in sheet[1]]
        values = [cell.value for cell in sheet[2]]
        exported_row = dict(zip(headers, values, strict=False))
        self.assertEqual(exported_row["成交日期"], "2026-05-02")
        self.assertEqual(exported_row["投资方名称"], "北京国泰新华实业有限公司等")
        self.assertEqual(exported_row["投资金额（万元）"], "2,800.000000")
        self.assertEqual(exported_row["备注"], "成交日期缺失，按采集日填列")


if __name__ == "__main__":
    unittest.main()
