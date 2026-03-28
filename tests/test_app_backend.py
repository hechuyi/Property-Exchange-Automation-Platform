from __future__ import annotations

import unittest

from desktop_backend.app_backend import dispatch_api_request
from desktop_backend.app_service import AppUserFacingError
from desktop_backend.http_contract import build_not_found_payload


class FakeAppService:
    def __init__(self) -> None:
        self.last_records_payload = None
        self.last_export_payload = None
        self.last_event_limit = None
        self.last_jobs_limit = None
        self.last_resolve_conflict_payload = None
        self.raise_on_one_click = None
        self.raise_on_manual_import = None

    def get_job(self, job_id: str):
        if job_id != "job-1":
            raise KeyError(job_id)
        return {
            "job_id": job_id,
            "job_type": "export_excel",
            "status": "running",
            "summary": {"visible_count": 3},
            "events": [{"event_id": "inline-event"}],
        }

    def get_job_events(self, job_id: str, *, limit: int = 200):
        if job_id != "job-1":
            raise KeyError(job_id)
        self.last_event_limit = limit
        return [{"event_id": f"event-{index}"} for index in range(limit)]

    def list_records(self, payload):
        self.last_records_payload = payload
        return {"rows": [], "scope": payload, "record_family": payload["record_family"]}

    def list_mapping_entries(self):
        return [{"entry_id": "entry-1"}]

    def list_pending_mappings(self):
        return [
            {"record_id": "pending-0"},
            {"record_id": "pending-1"},
        ]

    def run_export(self, payload):
        self.last_export_payload = payload
        return {
            "status": "empty",
            "empty_reason_code": "no_matching_records",
            "scope_state_counts": {"pending_mapping": 0},
            "scope": payload["scope"],
        }

    def list_jobs(self, *, limit: int = 20):
        self.last_jobs_limit = limit
        return [{"job_id": "job-1"}]

    def resolve_mapping_conflict(self, payload):
        self.last_resolve_conflict_payload = payload
        return {"job_id": "job-resolve", "record_id": payload["record_id"], "affected_count": 3}

    def launch_one_click(self, payload):
        if self.raise_on_one_click:
            raise self.raise_on_one_click
        return {"job_id": "job-1", "job_type": "one_click"}

    def launch_manual_import(self, payload):
        if self.raise_on_manual_import:
            raise self.raise_on_manual_import
        return {"job_id": "job-manual", "job_type": "manual_import"}


class AppBackendDispatchTest(unittest.TestCase):
    def test_get_job_returns_summary_without_inline_events(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/jobs/job-1",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        self.assertNotIn("events", payload)
        self.assertEqual(payload["job_id"], "job-1")
        self.assertEqual(payload["summary"], {"visible_count": 3})

    def test_get_job_events_returns_events_envelope_with_counts_and_truncated_flag(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/jobs/job-1/events?limit=2",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        self.assertEqual(service.last_event_limit, 3)
        self.assertEqual(payload["events"], [{"event_id": "event-0"}, {"event_id": "event-1"}])
        self.assertEqual(payload["returned_count"], 2)
        self.assertEqual(payload["total_count"], 3)
        self.assertTrue(payload["truncated"])

    def test_jobs_endpoint_clamps_invalid_limit_to_default_capacity(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/jobs?limit=not-a-number",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        self.assertEqual(service.last_jobs_limit, 20)
        self.assertEqual(payload["jobs"], [{"job_id": "job-1"}])

    def test_mappings_endpoint_wraps_pending_items_with_capacity_fields(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/mappings",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload["entries"], [{"entry_id": "entry-1"}])
        self.assertEqual(payload["pending"], [{"record_id": "pending-0"}, {"record_id": "pending-1"}])
        self.assertEqual(payload["returned_count"], 2)
        self.assertEqual(payload["total_count"], 2)
        self.assertFalse(payload["truncated"])

    def test_missing_job_and_missing_job_events_both_return_404(self) -> None:
        service = FakeAppService()

        summary_status, summary_payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/jobs/missing-job",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )
        events_status, events_payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/jobs/missing-job/events",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        expected = build_not_found_payload(resource="job", resource_id="missing-job")
        self.assertEqual(summary_status, 404)
        self.assertEqual(summary_payload, expected)
        self.assertEqual(events_status, 404)
        self.assertEqual(events_payload, expected)

    def test_records_endpoint_parses_record_family_scope_fields(self) -> None:
        service = FakeAppService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/records?record_family=deal&state=all&project_type=all&date_from=2026-03-01&date_to=2026-03-25&keyword=%E5%8D%8E%E6%B6%A6&limit=25&page=2",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        self.assertEqual(service.last_records_payload["record_family"], "deal")
        self.assertEqual(service.last_records_payload["page_size"], "25")
        self.assertEqual(service.last_records_payload["page"], "2")
        self.assertEqual(payload["record_family"], "deal")

    def test_exports_endpoint_requires_scope_payload(self) -> None:
        service = FakeAppService()
        request_payload = {
            "scope": {
                "record_family": "listing",
                "state": "all",
                "project_type": "all",
                "keyword": "",
                "date_from": "",
                "date_to": "",
                "page": 1,
                "page_size": 50,
            },
            "mode": "rebuild",
            "cursor_key": "",
            "output_dir": "/tmp/export",
        }

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/exports",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body=request_payload,
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        self.assertEqual(service.last_export_payload, request_payload)
        self.assertEqual(
            payload,
            {
                "status": "empty",
                "empty_reason_code": "no_matching_records",
                "scope_state_counts": {"pending_mapping": 0},
                "scope": request_payload["scope"],
            },
        )

    def test_resolve_conflict_endpoint_delegates_to_service(self) -> None:
        service = FakeAppService()
        request_payload = {
            "record_id": "rec-1",
            "selected_resolution": {
                "match_field": "group",
                "target_field": "source_type",
                "source_name": "中铁",
                "target_value": "央企",
            },
        }

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/mappings/resolve-conflict",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body=request_payload,
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        self.assertEqual(service.last_resolve_conflict_payload, request_payload)
        self.assertEqual(payload["job_id"], "job-resolve")

    def test_one_click_runtime_blocker_maps_to_conflict_payload(self) -> None:
        service = FakeAppService()
        service.raise_on_one_click = AppUserFacingError(
            message="browser runtime missing",
            error_code="browser_runtime_missing",
            http_status=409,
        )

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/one-click",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"start_date": "2026-03-26", "end_date": "2026-03-26"},
            api_token="test-token",
        )

        self.assertEqual(status, 409)
        self.assertEqual(payload["error_code"], "browser_runtime_missing")

    def test_manual_import_invalid_directory_maps_to_bad_request_payload(self) -> None:
        service = FakeAppService()
        service.raise_on_manual_import = AppUserFacingError(
            message="/tmp/missing",
            error_code="manual_import_input_dir_not_found",
            http_status=400,
            details={"input_dir": "/tmp/missing"},
        )

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/jobs/manual-import",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"input_dir": "/tmp/missing"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertEqual(payload["error_code"], "manual_import_input_dir_not_found")
        self.assertEqual(payload["details"]["input_dir"], "/tmp/missing")

    def test_manual_import_mutating_job_conflict_maps_to_conflict_payload(self) -> None:
        service = FakeAppService()
        service.raise_on_manual_import = AppUserFacingError(
            message="已有执行中的任务：一键执行",
            error_code="mutating_job_in_progress",
            http_status=409,
            details={"active_job_type": "one_click"},
        )

        status, payload = dispatch_api_request(
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

        self.assertEqual(status, 409)
        self.assertEqual(payload["error_code"], "mutating_job_in_progress")
        self.assertEqual(payload["details"]["active_job_type"], "one_click")


if __name__ == "__main__":
    unittest.main()
