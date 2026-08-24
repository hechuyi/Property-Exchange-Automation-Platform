from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from desktop_backend.app_backend import build_handler, dispatch_api_request
from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppService, AppUserFacingError
from desktop_backend.error_codes import ERROR_INVALID_REQUEST
from desktop_backend.http_contract import build_not_found_payload
from desktop_backend.request_contract import normalize_one_click_request


class ReadyRuntimeDependencies:
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


class FakeAppService:
    def __init__(self) -> None:
        self.last_basic_settings_payload = None
        self.last_advanced_settings_payload = None
        self.last_one_click_payload = None
        self.last_download_ingest_payload = None
        self.last_manual_import_payload = None
        self.last_archive_reprocess_payload = None
        self.last_record_scope_payload = None
        self.last_deleted_mapping_id = None
        self.last_event_limit = None
        self.last_reprocess_record_id = None
        self.last_reveal_record_folder_id = None
        self.last_retry_job_id = None
        self.last_open_export_id = None
        self.last_download_export_id = None
        self.last_acknowledged_record_id = None
        self.last_runtime_install_payload = None

    def readiness(self):
        return {"ready": True}

    def health(self):
        return {"status": "healthy"}

    def overview(self):
        return {"runtime": {"browser": {"installed": False}}, "record_summary": {"pending_mapping_count": 0}}

    def build_job_progress(self, job):
        return {"job_status": str((job or {}).get("status") or "")}

    def get_job(self, job_id: str):
        if job_id != "job-1":
            raise KeyError(job_id)
        return {"job_id": job_id, "job_type": "export_excel", "status": "running", "summary": {}}

    def list_jobs(self, *, limit: int = 20):
        return [{"job_id": "job-1", "job_type": "export_excel", "status": "running", "summary": {}}]

    def get_job_events(self, job_id: str, *, limit: int = 200):
        if job_id != "job-1":
            raise KeyError(job_id)
        self.last_event_limit = limit
        return [
            {
                "event_id": "event-1",
                "event_ts": "2026-05-01 09:00:00",
                "stage": "download",
                "status": "running",
                "project_code": "G32026SH1000001",
                "archive_path": "",
                "error_type": "",
                "error_message": "",
                "payload": {
                    "label": "running",
                    "summary_payload": {
                        "summary": {},
                        "phase_percent": 20,
                    },
                },
            }
        ]

    def count_job_events(self, job_id: str) -> int:
        if job_id != "job-1":
            raise KeyError(job_id)
        return 1

    def list_records(self, payload):
        self.last_record_scope_payload = payload
        return {"records": [], "scope": payload}

    def get_basic_settings(self):
        return {
            "default_exchange": "all",
            "effective_default_scope": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "sse",
            },
        }

    def get_advanced_settings(self):
        return {"save_json": False}

    def set_basic_settings(self, payload):
        self.last_basic_settings_payload = payload
        return {"default_exchange": payload.get("default_exchange", "all")}

    def set_advanced_settings(self, payload):
        self.last_advanced_settings_payload = payload
        return {"save_json": bool(payload.get("save_json"))}

    def launch_one_click(self, payload):
        self.last_one_click_payload = normalize_one_click_request(
            payload,
            basic_settings=self.get_basic_settings(),
            advanced_settings=self.get_advanced_settings(),
        )
        return {"job_id": "job-1", "job_type": "one_click"}

    def launch_download_ingest(self, payload):
        self.last_download_ingest_payload = normalize_one_click_request(
            payload,
            basic_settings=self.get_basic_settings(),
            advanced_settings=self.get_advanced_settings(),
        )
        return {"job_id": "job-history-1", "job_type": "download_ingest"}

    def launch_manual_import(self, payload):
        self.last_manual_import_payload = payload
        return {"job_id": "job-manual", "job_type": "manual_import"}

    def launch_archive_reprocess(self, payload):
        self.last_archive_reprocess_payload = payload
        return {"job_id": "job-archive-reprocess", "job_type": "archive_reprocess"}

    def retry_job(self, job_id: str):
        self.last_retry_job_id = job_id
        return {
            "job_id": "job-retry-1",
            "job_type": "manual_import",
            "retry_of_job_id": job_id,
            "notification": {"level": "success", "message": "任务重试已受理"},
        }

    def run_export(self, payload):
        self.last_export_payload = payload
        return {
            "job_id": "job-export",
            "job_type": "export_excel",
            "status": "succeeded",
            "message": "",
            "failure_code": "",
            "failure_message": "",
            "empty_reason_code": "",
            "scope_state_counts": {},
            "scope": payload.get("scope", {}),
            "export_id": "exp-1",
            "cursor_id": "cursor-1",
            "requested_export_mode": payload.get("requested_export_mode", ""),
            "new_records": 0,
            "changed_records": 0,
            "artifacts": [],
        }

    def reprocess_record(self, record_id: str):
        self.last_reprocess_record_id = record_id
        return {"record_id": record_id, "status": "queued"}

    def reveal_record_folder(self, record_id: str):
        self.last_reveal_record_folder_id = record_id
        return {"record_id": record_id, "path": "/tmp/folder"}

    def delete_mapping(self, entry_id: str):
        self.last_deleted_mapping_id = entry_id
        return {"entry_id": entry_id, "deleted": True}

    def list_exports_history(self, *, limit: int = 100):
        return {"rows": [{"export_id": "exp-1", "openable": False, "rebuildable": False, "is_tombstone": True}], "limit": limit}

    def get_export_history_detail(self, export_id: str):
        if export_id != "exp-1":
            raise KeyError(export_id)
        return {"export_id": export_id, "openable": False, "rebuildable": False, "is_tombstone": True}

    def open_export_history(self, export_id: str):
        self.last_open_export_id = export_id
        return {"export_id": export_id, "opened": False, "openable": False, "rebuildable": False, "is_tombstone": True}

    def download_export_history(self, export_id: str, *, output_dir: str):
        self.last_download_export_id = export_id
        return {
            "export_id": export_id,
            "downloaded": False,
            "openable": False,
            "rebuildable": False,
            "is_tombstone": True,
            "artifacts": [],
            "output_dir": output_dir,
        }

    def acknowledge_field_missing(self, record_id: str):
        self.last_acknowledged_record_id = record_id
        return {
            "record_id": record_id,
            "state": "field_missing",
            "acknowledgement": {
                "acknowledged": True,
                "missing_fields_hash": "hash-1",
                "revision_id": 7,
            },
            "attention": {
                "requires_attention": False,
                "suppressed": True,
                "reason": "acknowledged",
            },
            "exportable": False,
        }

    def launch_browser_runtime_install(self, payload):
        self.last_runtime_install_payload = payload
        return {
            "status": "running",
            "browser_name": payload.get("browser_name", ""),
            "trigger": payload.get("trigger", ""),
            "attempt_count": 1,
            "started_at": "2026-05-01 09:00:00",
            "updated_at": "2026-05-01 09:00:00",
            "completed_at": "",
            "message": "Installing chromium",
            "running": True,
        }


class AppBackendDispatchTest(unittest.TestCase):
    def _assert_ok(self, payload):
        self.assertTrue(payload["ok"])
        self.assertIn("data", payload)
        return payload["data"]

    def _assert_error(self, payload, *, code: str):
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], code)
        return payload["error"]

    def test_options_preflight_returns_no_content_without_transport_payload(self) -> None:
        status, payload = dispatch_api_request(
            FakeAppService(),
            method="OPTIONS",
            path="/api/overview",
            headers={"Origin": "null"},
            api_token="test-token",
        )

        self.assertEqual(status, 204)
        self.assertIsNone(payload)

    def test_write_handler_rejects_non_object_json_body_instead_of_dispatching_empty_payload(self) -> None:
        service = FakeAppService()
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(service, api_token="test-token"))
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(server_thread.join, 5)
        request = Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/jobs/archive-reprocess",
            data=json.dumps([]).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-PEAP-Desktop-Token": "test-token",
            },
            method="POST",
        )

        with self.assertRaises(HTTPError) as captured:
            urlopen(request, timeout=5)
        payload = json.loads(captured.exception.read().decode("utf-8"))

        self.assertEqual(captured.exception.code, 400)
        self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIsNone(service.last_archive_reprocess_payload)

    def test_dispatch_rejects_non_object_write_body_instead_of_dispatching_empty_payload(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/archive-reprocess",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            body=False,
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIsNone(service.last_archive_reprocess_payload)

    def test_write_handler_rejects_malformed_json_body_instead_of_dispatching_empty_payload(self) -> None:
        service = FakeAppService()
        server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(service, api_token="test-token"))
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(server_thread.join, 5)
        request = Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/jobs/archive-reprocess",
            data=b"{",
            headers={
                "Content-Type": "application/json",
                "X-PEAP-Desktop-Token": "test-token",
            },
            method="POST",
        )

        with self.assertRaises(HTTPError) as captured:
            urlopen(request, timeout=5)
        payload = json.loads(captured.exception.read().decode("utf-8"))

        self.assertEqual(captured.exception.code, 400)
        self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIsNone(service.last_archive_reprocess_payload)

    def test_ready_and_health_routes_use_transport_envelope(self) -> None:
        service = FakeAppService()

        ready_status, ready_payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/ready",
            headers={},
            api_token="test-token",
        )
        health_status, health_payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/health",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )
        self.assertEqual(ready_status, 200)
        self.assertEqual(self._assert_ok(ready_payload), {"ready": True})
        self.assertEqual(health_status, 200)
        self.assertEqual(self._assert_ok(health_payload), {"status": "healthy"})

    def test_export_history_routes_delegate_and_keep_tombstone_non_openable(self) -> None:
        service = FakeAppService()
        headers = {"X-PEAP-Desktop-Token": "test-token"}
        list_status, list_payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/exports/history?limit=20",
            headers=headers,
            api_token="test-token",
        )
        detail_status, detail_payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/exports/history/exp-1",
            headers=headers,
            api_token="test-token",
        )
        open_status, open_payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/exports/history/exp-1/open",
            headers=headers,
            body={},
            api_token="test-token",
        )
        download_status, download_payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/exports/history/exp-1/download",
            headers=headers,
            body={"output_dir": "/tmp/test-export-history"},
            api_token="test-token",
        )
        self.assertEqual(list_status, 200)
        self.assertEqual(detail_status, 200)
        self.assertEqual(open_status, 200)
        self.assertEqual(download_status, 200)
        self.assertTrue(self._assert_ok(list_payload)["rows"][0]["is_tombstone"])
        self.assertFalse(self._assert_ok(detail_payload)["openable"])
        self.assertFalse(self._assert_ok(open_payload)["opened"])
        self.assertFalse(self._assert_ok(download_payload)["downloaded"])

    def test_export_history_download_rejects_non_string_output_dir_instead_of_stringifying_object(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/exports/history/exp-1/download",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            body={"output_dir": {"path": "/tmp/test-export-history"}},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("output_dir", error["message"])
        self.assertIsNone(service.last_download_export_id)

    def test_export_history_download_defaults_missing_output_dir_at_service_boundary(self) -> None:
        service = FakeAppService()

        for body in ({}, {"output_dir": ""}, {"output_dir": "  "}):
            with self.subTest(body=body):
                status, payload = dispatch_api_request(
                    service,
                    method="POST",
                    path="/api/exports/history/exp-1/download",
                    headers={"X-PEAP-Desktop-Token": "test-token"},
                    body=body,
                    api_token="test-token",
                )

                self.assertEqual(status, 200)
                result = self._assert_ok(payload)
                self.assertEqual(result["output_dir"], "")
                self.assertEqual(service.last_download_export_id, "exp-1")

    def test_runtime_install_rejects_non_string_browser_name_instead_of_stringifying_object(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/runtime/install-browser",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            body={"browser_name": {"name": "chromium"}},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("browser_name", error["message"])
        self.assertIsNone(service.last_runtime_install_payload)

    def test_malformed_dynamic_routes_return_not_found_without_dispatching_handlers(self) -> None:
        headers = {"X-PEAP-Desktop-Token": "test-token"}
        probes = [
            ("POST", "/api/records/rec-a/extra/reprocess", {}),
            ("POST", "/api/records//rec-a/reprocess", {}),
            ("POST", "/api/records/rec-a//reprocess", {}),
            ("POST", "/api/records/rec-a/extra/reveal-folder", {}),
            ("POST", "/api/records//rec-a/field-missing/acknowledge", {}),
            ("PUT", "/api/mappings//entry-1", {"source_name": "x"}),
            ("DELETE", "/api/mappings//entry-1", None),
            ("POST", "/api/exports/history/export-1/extra/open", {}),
            ("POST", "/api/exports/history/export-1/extra/download", {}),
        ]
        for method, path, body in probes:
            with self.subTest(method=method, path=path):
                service = FakeAppService()
                status, payload = dispatch_api_request(
                    service,
                    method=method,
                    path=path,
                    headers=headers,
                    body=body,
                    api_token="test-token",
                )

                self.assertEqual(status, 404)
                self.assertEqual(payload, build_not_found_payload(resource="endpoint", resource_id=path))
                self.assertIsNone(service.last_reprocess_record_id)
                self.assertIsNone(service.last_reveal_record_folder_id)
                self.assertIsNone(service.last_acknowledged_record_id)
                self.assertIsNone(service.last_open_export_id)
                self.assertIsNone(service.last_download_export_id)
                self.assertIsNone(service.last_deleted_mapping_id)

    def test_dynamic_action_routes_decode_frontend_encoded_resource_ids(self) -> None:
        headers = {"X-PEAP-Desktop-Token": "test-token"}
        probes = [
            (
                "POST",
                "/api/jobs/job%2Funsafe%20id/retry",
                {},
                "last_retry_job_id",
            ),
            (
                "POST",
                "/api/records/record%2Funsafe%20id/reprocess",
                {},
                "last_reprocess_record_id",
            ),
            (
                "DELETE",
                "/api/mappings/mapping%2Funsafe%20id",
                None,
                "last_deleted_mapping_id",
            ),
            (
                "POST",
                "/api/exports/history/export%2Funsafe%20id/open",
                {},
                "last_open_export_id",
            ),
        ]
        for method, path, body, captured_field in probes:
            with self.subTest(method=method, path=path):
                service = FakeAppService()
                status, payload = dispatch_api_request(
                    service,
                    method=method,
                    path=path,
                    headers=headers,
                    body=body,
                    api_token="test-token",
                )

                self.assertIn(status, {200, 202})
                self.assertTrue(payload["ok"])
                self.assertEqual(getattr(service, captured_field), "job/unsafe id" if captured_field == "last_retry_job_id" else (
                    "record/unsafe id" if captured_field == "last_reprocess_record_id" else (
                        "mapping/unsafe id" if captured_field == "last_deleted_mapping_id" else "export/unsafe id"
                    )
                ))

    def test_missing_export_history_route_reports_export_resource(self) -> None:
        status, payload = dispatch_api_request(
            FakeAppService(),
            method="GET",
            path="/api/exports/history/missing%20export",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 404)
        self.assertEqual(
            payload,
            build_not_found_payload(resource="export", resource_id="missing export"),
        )

    def test_field_missing_acknowledge_route_delegates_and_keeps_noise_only_contract(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/records/rec-field-missing/field-missing/acknowledge",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        data = self._assert_ok(payload)
        self.assertEqual(service.last_acknowledged_record_id, "rec-field-missing")
        self.assertEqual(data["record_id"], "rec-field-missing")
        self.assertEqual(data["state"], "field_missing")
        self.assertFalse(data["exportable"])
        self.assertTrue(data["field_missing_acknowledgement"]["acknowledged"])
        self.assertTrue(data["attention"]["suppressed"])

    def test_settings_routes_delegate_to_service(self) -> None:
        service = FakeAppService()

        basic_status, basic_payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/settings/basic",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )
        advanced_status, advanced_payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/settings/advanced",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )
        save_basic_status, save_basic_payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/settings/basic",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"defaults": {"default_exchange": "sse"}},
            api_token="test-token",
        )
        save_advanced_status, save_advanced_payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/settings/advanced",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"processing": {"save_json": True}},
            api_token="test-token",
        )

        self.assertEqual(basic_status, 200)
        self.assertEqual(advanced_status, 200)
        self.assertIsInstance(self._assert_ok(basic_payload), dict)
        self.assertIsInstance(self._assert_ok(advanced_payload), dict)
        self.assertEqual(save_basic_status, 200)
        self.assertEqual(save_advanced_status, 200)
        self.assertIn("default_exchange", service.last_basic_settings_payload)
        self.assertIn("save_json", service.last_advanced_settings_payload)
        self.assertIsInstance(self._assert_ok(save_basic_payload), dict)
        self.assertIsInstance(self._assert_ok(save_advanced_payload), dict)

    def test_settings_basic_route_preserves_explicit_empty_stored_preference_for_scope_clear(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/settings/basic",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "stored_preference": {},
                "default_exchange": "cbex",
            },
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        self.assertEqual(service.last_basic_settings_payload["stored_preference"], {})
        self.assertEqual(service.last_basic_settings_payload["default_exchange"], "cbex")
        self.assertIsInstance(self._assert_ok(payload), dict)

    def test_launch_routes_delegate_and_return_accepted_without_cross_request_scope_leakage(self) -> None:
        service = FakeAppService()

        one_click_status, one_click_payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/one-click",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "start_date": "2026-03-26",
                "end_date": "2026-03-26",
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "cbex",
            },
            api_token="test-token",
        )
        manual_status, manual_payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/manual-import",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"input_dir": "/tmp/demo"},
            api_token="test-token",
        )

        self.assertEqual(one_click_status, 202)
        self.assertEqual(manual_status, 202)
        self.assertIn("start_date", service.last_one_click_payload)
        self.assertIn("input_dir", service.last_manual_import_payload)
        self.assertNotIn("business_id", service.last_manual_import_payload)
        self.assertTrue(self._assert_ok(one_click_payload)["job_id"])
        self.assertTrue(self._assert_ok(manual_payload)["job_id"])

    def test_launch_route_rejects_non_object_service_result_instead_of_empty_success(self) -> None:
        service = FakeAppService()

        def launch_one_click(_payload):
            return []

        service.launch_one_click = launch_one_click

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/one-click",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "start_date": "2026-03-26",
                "end_date": "2026-03-26",
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "cbex",
            },
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("service result must be an object", error["message"])

    def test_runtime_install_rejects_bad_integer_service_result_instead_of_zero_success(self) -> None:
        service = FakeAppService()

        def launch_browser_runtime_install(payload):
            return {
                "status": "running",
                "browser_name": payload.get("browser_name", ""),
                "trigger": payload.get("trigger", ""),
                "attempt_count": "many",
            }

        service.launch_browser_runtime_install = launch_browser_runtime_install

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/runtime/install-browser",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("attempt_count", error["message"])

    def test_jobs_list_rejects_non_object_service_item_instead_of_filtering_it(self) -> None:
        service = FakeAppService()
        service.list_jobs = lambda *, limit=20: [
            {"job_id": "job-1", "job_type": "export_excel", "status": "running", "summary": {}},
            [],
        ]
        progress_calls: list[str] = []

        def build_job_progress(job):
            if not isinstance(job, dict):
                raise AssertionError("progress builder should not receive invalid job payload")
            progress_calls.append(str(job.get("job_id") or ""))
            return {}

        service.build_job_progress = build_job_progress

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/jobs?limit=20",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("job payload must be an object", error["message"])
        self.assertEqual(progress_calls, ["job-1"])

    def test_one_click_route_rejects_empty_body_when_actionable_default_scope_is_missing(self) -> None:
        service = FakeAppService()

        def get_basic_settings():
            return {
                "default_exchange": "all",
                "default_concurrency": 2,
                "effective_default_scope": {},
                "stored_preference": {},
                "stale_default_metadata": {
                    "is_stale": False,
                    "reason": "",
                    "hint": "",
                },
            }

        service.get_basic_settings = get_basic_settings

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/one-click",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("no actionable default scope", error["message"])
        self.assertIsNone(service.last_one_click_payload)

    def test_download_ingest_route_rejects_empty_body_when_effective_default_scope_is_stale(self) -> None:
        service = FakeAppService()

        def get_basic_settings():
            return {
                "default_exchange": "sse",
                "default_concurrency": 2,
                "effective_default_scope": {},
                "stored_preference": {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "exchange": "not_a_real_exchange",
                },
                "stale_default_metadata": {
                    "is_stale": True,
                    "reason": "invalid_exchange",
                    "hint": "reselect a supported exchange in settings",
                },
            }

        service.get_basic_settings = get_basic_settings

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/download-ingest",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("invalid_exchange", error["message"])
        self.assertIn("reselect a supported exchange", error["message"])
        self.assertIsNone(service.last_download_ingest_payload)

    def test_exports_route_rejects_empty_explicit_scope(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/exports",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"scope": {}},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("explicit canonical scope", error["message"])
        self.assertIsNone(getattr(service, "last_export_payload", None))

    def test_exports_route_rejects_non_string_output_dir_instead_of_stringifying_object(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/exports",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "scope": {
                    "record_family": "listing",
                    "business_id": "all",
                    "state": "all",
                    "exchange": "all",
                },
                "requested_export_mode": "full",
                "output_dir": {"path": "/tmp/export"},
            },
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("output_dir", error["message"])
        self.assertIsNone(getattr(service, "last_export_payload", None))

    def test_one_click_route_accepts_multi_family_scopes_as_single_request(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/one-click",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "start_date": "2026-03-26",
                "end_date": "2026-03-26",
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
            api_token="test-token",
        )

        self.assertEqual(status, 202)
        self.assertEqual(
            service.last_one_click_payload["family_scopes"],
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
        self.assertTrue(self._assert_ok(payload)["job_id"])

    def test_one_click_route_collapses_single_family_scope_to_scalar_request(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/one-click",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "start_date": "2026-03-26",
                "end_date": "2026-03-26",
                "family_scopes": [
                    {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "exchange": "sse",
                    },
                ],
            },
            api_token="test-token",
        )

        self.assertEqual(status, 202)
        self.assertEqual(service.last_one_click_payload["record_family"], "deal")
        self.assertEqual(service.last_one_click_payload["business_id"], "deal_equity_transfer")
        self.assertEqual(service.last_one_click_payload["business_label"], "股权转让成交")
        self.assertEqual(service.last_one_click_payload["exchange"], "sse")
        self.assertNotIn("family_scopes", service.last_one_click_payload)
        self.assertNotIn("record_families", service.last_one_click_payload)
        self.assertTrue(self._assert_ok(payload)["job_id"])

    def test_unauthorized_and_missing_job_routes_return_transport_errors(self) -> None:
        service = FakeAppService()

        unauthorized_status, unauthorized_payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/overview",
            headers={},
            api_token="test-token",
        )
        missing_status, missing_payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/jobs/missing-job",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(unauthorized_status, 401)
        self._assert_error(unauthorized_payload, code="unauthorized")
        self.assertEqual(missing_status, 404)
        self.assertEqual(missing_payload, build_not_found_payload(resource="job", resource_id="missing-job"))

    def test_non_string_api_token_misconfiguration_does_not_disable_auth(self) -> None:
        service = FakeAppService()

        for api_token in (False, 0, None):
            with self.subTest(api_token=api_token):
                status, payload = dispatch_api_request(
                    service,
                    method="GET",
                    path="/api/overview",
                    headers={},
                    api_token=api_token,  # type: ignore[arg-type]
                )

                self.assertEqual(status, 401)
                self._assert_error(payload, code="unauthorized")

    def test_bad_headers_mapping_contract_fails_fast_instead_of_masking_as_unauthorized(self) -> None:
        class RaisingHeaders:
            def get(self, _name):
                raise RuntimeError("broken headers mapping")

        with self.assertRaisesRegex(RuntimeError, "broken headers mapping"):
            dispatch_api_request(
                FakeAppService(),
                method="GET",
                path="/api/overview",
                headers=RaisingHeaders(),
                api_token="test-token",
            )

    def test_records_route_accepts_declared_scope_and_cache_bust_query_keys(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path=(
                "/api/records?"
                "record_family=listing&state=ready&business_id=equity_transfer&exchange=sse"
                "&keyword=%E5%8C%97%E4%BA%A4%E6%89%80&date_from=2026-03-01&date_to=2026-03-31"
                "&page=2&page_size=25&_=1&_t=2&_ts=3"
            ),
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        data = self._assert_ok(payload)
        self.assertEqual(data["scope"]["record_family"], "listing")
        self.assertEqual(data["scope"]["business_id"], "equity_transfer")
        self.assertEqual(data["scope"]["exchange"], "sse")
        self.assertEqual(data["scope"]["page"], 2)
        self.assertEqual(data["scope"]["page_size"], 25)

    def test_records_route_rejects_invalid_page_instead_of_defaulting(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/records?record_family=listing&business_id=equity_transfer&page=abc&page_size=25",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("invalid page", error["message"])
        self.assertIsNone(service.last_record_scope_payload)

    def test_records_route_rejects_invalid_page_size_instead_of_defaulting(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/records?record_family=listing&business_id=equity_transfer&page=1&page_size=abc",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("invalid page_size", error["message"])
        self.assertIsNone(service.last_record_scope_payload)

    def test_records_route_rejects_invalid_limit_alias_instead_of_defaulting(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/records?record_family=listing&business_id=equity_transfer&limit=abc",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("invalid limit", error["message"])
        self.assertIsNone(service.last_record_scope_payload)

    def test_records_route_rejects_invalid_limit_alias_when_page_size_is_present(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/records?record_family=listing&business_id=equity_transfer&page_size=25&limit=abc",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("invalid limit", error["message"])
        self.assertIsNone(service.last_record_scope_payload)

    def test_job_events_route_allows_limit_query_parameter(self) -> None:
        service = FakeAppService()

        ok_status, ok_payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/jobs/job-1/events?limit=3",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )
        self.assertEqual(ok_status, 200)
        events = self._assert_ok(ok_payload)
        self.assertEqual(service.last_event_limit, 4)
        self.assertEqual(events["total_count"], 1)

    def test_jobs_limit_query_rejects_repeated_scalar_values_instead_of_using_first(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/jobs?limit=20&limit=abc",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("multiple values for limit", error["message"])

    def test_get_routes_reject_unknown_query_keys_before_dispatch(self) -> None:
        headers = {"X-PEAP-Desktop-Token": "test-token"}
        probes = {
            "/api/ready?x=1": [],
            "/api/health?x=1": [],
            "/api/catalog?x=1": [],
            "/api/overview?x=1": [],
            "/api/jobs?limit=2&x=1": ["limit"],
            "/api/jobs/job-1?x=1": [],
            "/api/jobs/job-1/events?limit=2&x=1": ["limit"],
            "/api/exports/history?limit=2&x=1": ["limit"],
            "/api/exports/history/exp-1?x=1": [],
        }
        for path, allowed_keys in probes.items():
            with self.subTest(path=path):
                status, payload = dispatch_api_request(
                    FakeAppService(),
                    method="GET",
                    path=path,
                    headers=headers,
                    api_token="test-token",
                )

                self.assertEqual(status, 400)
                error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
                self.assertEqual(error["details"]["unknown_query_keys"], ["x"])
                self.assertEqual(error["details"]["allowed_query_keys"], allowed_keys)

    def test_records_route_only_allows_scope_pagination_and_cache_bust_query_keys(self) -> None:
        status, payload = dispatch_api_request(
            FakeAppService(),
            method="GET",
            path="/api/records?record_family=listing&business_id=equity_transfer&unknown=1&_=1",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertEqual(error["details"]["unknown_query_keys"], ["unknown"])
        self.assertIn("record_family", error["details"]["allowed_query_keys"])
        self.assertIn("_", error["details"]["allowed_query_keys"])

    def test_post_routes_reject_any_query_keys(self) -> None:
        status, payload = dispatch_api_request(
            FakeAppService(),
            method="POST",
            path="/api/exports?x=1",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "scope": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "all",
                },
                "requested_export_mode": "full",
            },
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertEqual(error["details"]["unknown_query_keys"], ["x"])
        self.assertEqual(error["details"]["allowed_query_keys"], [])

    def test_query_strictness_does_not_apply_to_unmatched_routes(self) -> None:
        status, payload = dispatch_api_request(
            FakeAppService(),
            method="GET",
            path="/api/unknown?x=1",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 404)
        self.assertEqual(payload, build_not_found_payload(resource="endpoint", resource_id="/api/unknown"))

    def test_one_click_runtime_error_maps_to_internal_error_payload(self) -> None:
        service = FakeAppService()

        def raise_user_error(_payload):
            raise AppUserFacingError(
                message="browser runtime missing",
                error_code="browser_runtime_missing",
                http_status=409,
            )

        service.launch_one_click = raise_user_error

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/one-click",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "start_date": "2026-03-26",
                "end_date": "2026-03-26",
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "cbex",
            },
            api_token="test-token",
        )

        self.assertEqual(status, 409)
        self._assert_error(payload, code="browser_runtime_missing")

    def test_missing_schema_routes_return_schema_not_ready_without_implicit_database_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_home = os.path.join(tmp, "app_home")
            docs_home = os.path.join(tmp, "docs_home")
            with patch.dict(
                os.environ,
                {
                    "PEAP_APP_HOME": app_home,
                    "PEAP_DOCUMENTS_HOME": docs_home,
                },
                clear=False,
            ):
                config = AppConfig.from_env(project_root=tmp)
            service = AppService(
                config_obj=config,
                runtime_dependencies=ReadyRuntimeDependencies(),
            )
            self.assertFalse(os.path.exists(config.STREAMING_DB_PATH))

            status, payload = dispatch_api_request(
                service,
                method="GET",
                path="/api/overview",
                headers={"X-PEAP-Desktop-Token": "test-token"},
                body=None,
                api_token="test-token",
            )
            self.assertEqual(status, 200)
            overview = self._assert_ok(payload)
            self.assertEqual(overview["record_summary"], {"state_counts": {}, "pending_mapping_count": 0})
            self.assertEqual(overview["recent_jobs"], [])
            self.assertEqual(overview["latest_progress"]["job_status"], "")
            self.assertEqual(overview["latest_progress"]["phase_percent"], 0)
            self.assertEqual(overview["latest_progress"]["metrics"], [])
            self.assertIn("runtime", overview)
            self.assertIn("defaults", overview)
            self.assertIn("visibility", overview)
            self.assertFalse(os.path.exists(config.STREAMING_DB_PATH))

            for method, path, body in (
                ("GET", "/api/settings/basic", None),
                ("GET", "/api/records?record_family=listing&business_id=all&exchange=all&state=all", None),
                ("POST", "/api/jobs/manual-import", {"input_dir": tmp}),
            ):
                with self.subTest(path=path):
                    status, payload = dispatch_api_request(
                        service,
                        method=method,
                        path=path,
                        headers={"X-PEAP-Desktop-Token": "test-token"},
                        body=body,
                        api_token="test-token",
                    )

                    self.assertEqual(status, 503)
                    error = self._assert_error(payload, code="schema_not_ready")
                    self.assertEqual(error["details"]["schema"]["ready"], False)
                    self.assertEqual(error["details"]["db_path"], os.path.abspath(config.STREAMING_DB_PATH))
                    self.assertFalse(os.path.exists(config.STREAMING_DB_PATH))

    def test_one_click_route_rejects_legacy_project_type_input(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/one-click",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "start_date": "2026-03-26",
                "end_date": "2026-03-26",
                "project_type": "股权转让",
            },
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("project_type", error["message"])

    def test_one_click_route_rejects_non_string_scope_field_instead_of_stringifying_object(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/one-click",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "start_date": "2026-03-26",
                "end_date": "2026-03-26",
                "record_family": {"value": "listing"},
                "business_id": "equity_transfer",
                "exchange": "sse",
            },
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("record_family", error["message"])
        self.assertIsNone(service.last_one_click_payload)

    def test_download_ingest_route_uses_distinct_job_type(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/download-ingest",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "start_date": "2026-03-26",
                "end_date": "2026-03-26",
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "sse",
            },
            api_token="test-token",
        )

        self.assertEqual(status, 202)
        data = self._assert_ok(payload)
        self.assertEqual(data["job_type"], "download_ingest")
        self.assertEqual(service.last_download_ingest_payload["exchange"], "sse")

    def test_download_ingest_route_delegates_raw_multi_family_scopes_to_service_normalizer(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/download-ingest",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "start_date": "2026-03-26",
                "end_date": "2026-03-26",
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
            api_token="test-token",
        )

        self.assertEqual(status, 202)
        data = self._assert_ok(payload)
        self.assertEqual(data["job_type"], "download_ingest")
        self.assertEqual(
            service.last_download_ingest_payload["record_families"],
            ["listing", "deal"],
        )
        self.assertEqual(len(service.last_download_ingest_payload["family_scopes"]), 2)

    def test_archive_reprocess_route_uses_dedicated_job_type_without_download_scope(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/archive-reprocess",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={},
            api_token="test-token",
        )

        self.assertEqual(status, 202)
        data = self._assert_ok(payload)
        self.assertEqual(data["job_type"], "archive_reprocess")
        self.assertEqual(service.last_archive_reprocess_payload, {"input_dir": ""})

    def test_archive_reprocess_route_rejects_non_string_input_dir_instead_of_stringifying_object(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/archive-reprocess",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"input_dir": {"path": "/tmp/archive"}},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        error = self._assert_error(payload, code=ERROR_INVALID_REQUEST)
        self.assertIn("input_dir", error["message"])
        self.assertIsNone(service.last_archive_reprocess_payload)

    def test_job_retry_route_delegates_and_returns_retry_notification_contract(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/job-1/retry",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={},
            api_token="test-token",
        )

        self.assertEqual(status, 202)
        data = self._assert_ok(payload)
        self.assertEqual(service.last_retry_job_id, "job-1")
        self.assertEqual(data["job_id"], "job-retry-1")
        self.assertEqual(data["retry_of_job_id"], "job-1")
        self.assertEqual(data["notification"]["level"], "success")



if __name__ == "__main__":
    unittest.main()
