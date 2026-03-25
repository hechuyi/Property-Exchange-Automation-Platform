from __future__ import annotations

import unittest

from desktop_backend.app_backend import dispatch_api_request
from desktop_backend.http_contract import build_not_found_payload


class FakeAppService:
    def __init__(self) -> None:
        self.last_records_payload = None
        self.last_export_payload = None
        self.last_event_limit = None

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

    def run_export(self, payload):
        self.last_export_payload = payload
        return {
            "status": "empty",
            "empty_reason_code": "no_matching_records",
            "scope_state_counts": {"pending_mapping": 0},
            "scope": payload["scope"],
        }


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


if __name__ == "__main__":
    unittest.main()
