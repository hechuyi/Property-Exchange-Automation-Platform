from __future__ import annotations

import unittest

from desktop_backend.app_backend import dispatch_api_request
from desktop_backend.error_codes import ERROR_INVALID_REQUEST


class FakeJobsService:
    def __init__(self) -> None:
        self.last_export_payload = None
        self.last_event_limit = None
        self.last_jobs_limit = None
        self.last_exports_history_limit = None

    def list_jobs(self, *, limit: int = 20):
        self.last_jobs_limit = limit
        return [
            {
                "job_id": "job-1",
                "job_type": "one_click",
                "status": "running",
                "created_at": "2026-04-12T10:00:00",
                "updated_at": "2026-04-12T10:10:00",
                "downloaded_count": 9,
                "persisted_count": 4,
                "exception_count": 1,
                "metadata": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "business_label": "股权转让",
                },
            }
        ]

    def build_job_progress(self, job):
        return {
            "phase_code": "prepare_tasks",
            "phase_label": "正在扫描网页",
            "job_status": str((job or {}).get("status") or ""),
            "phase_percent": 25,
            "current_task_label": "北交所 - 增资扩股",
        }

    def get_job(self, job_id: str):
        if job_id != "job-1":
            raise KeyError(job_id)
        return {
            "job_id": job_id,
            "job_type": "one_click",
            "status": "running",
            "created_at": "2026-04-12T10:00:00",
            "updated_at": "2026-04-12T10:10:00",
            "downloaded_count": 9,
            "persisted_count": 4,
            "exception_count": 1,
            "metadata": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "scope": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "business_label": "股权转让",
                    "exchange": "sse",
                },
            },
            "summary": {"visible_count": 3},
        }

    def get_job_events(self, job_id: str, *, limit: int = 200):
        if job_id != "job-1":
            raise KeyError(job_id)
        self.last_event_limit = limit
        return [
            {
                "event_id": "event-0",
                "stage": "prepare_tasks",
                "status": "running",
                "project_code": "G32025SH1000194",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "payload": {
                    "label": "正在扫描网页",
                    "summary_payload": {
                        "kind": "collect",
                        "task_label": "北交所 - 增资扩股",
                        "task_index": 1,
                        "task_total": 4,
                        "phase_percent": 25,
                    },
                },
            },
            {
                "event_id": "event-1",
                "stage": "save_pages",
                "status": "failed",
                "project_code": "G32025SH1000194",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "error_type": "collect_failed",
                "error_message": "上游 500",
                "payload": {
                    "label": "扫描失败",
                    "summary_payload": {
                        "kind": "collect",
                        "task_label": "北交所 - 增资扩股",
                        "task_index": 2,
                        "task_total": 4,
                        "phase_percent": 50,
                    },
                },
            },
        ]

    def count_job_events(self, job_id: str) -> int:
        if job_id != "job-1":
            raise KeyError(job_id)
        return 2

    def run_export(self, payload):
        self.last_export_payload = payload
        return {
            "status": "empty",
            "empty_reason_code": "no_matching_records",
            "scope_state_counts": {"pending_mapping": 0},
            "scope": payload["scope"],
        }

    def list_exports_history(self, *, limit: int = 100):
        self.last_exports_history_limit = limit
        return {"rows": [], "limit": limit}


class FakeBusinessReEvaluationJobsService(FakeJobsService):
    def get_job(self, job_id: str):
        if job_id != "job-1":
            raise KeyError(job_id)
        return {
            "job_id": job_id,
            "job_type": "business_re_evaluation",
            "status": "success_with_warnings",
            "created_at": "2026-04-12T10:00:00",
            "updated_at": "2026-04-12T10:10:00",
            "summary": {
                "pending_review_count": 3,
                "accepted_completed_count": 7,
                "skipped_count": 1,
            },
            "metadata": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
            },
        }

    def build_job_progress(self, job):
        return {
            "phase_code": "reprocessing",
            "phase_label": "业务重判（内部兼容）",
            "job_status": str((job or {}).get("status") or ""),
            "phase_percent": 50,
            "pending_review_count": 2,
            "accepted_completed_count": 5,
        }

    def get_job_events(self, job_id: str, *, limit: int = 200):
        if job_id != "job-1":
            raise KeyError(job_id)
        self.last_event_limit = limit
        return [
            {
                "event_id": "event-business-re-eval",
                "stage": "reprocessing",
                "status": "pending_review",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "payload": {
                    "label": "业务重判（内部兼容）后仍有待处理项",
                    "summary_payload": {
                        "kind": "business_re_evaluation",
                        "task_label": "业务重判（内部兼容）",
                        "task_index": 1,
                        "task_total": 2,
                        "phase_percent": 50,
                        "summary": {
                            "pending_review_count": 3,
                            "accepted_completed_count": 7,
                        },
                    },
                },
            }
        ]


class FakeInvalidJobEventsEnvelopeService(FakeJobsService):
    def __init__(self, *, raw_events, total_count) -> None:
        super().__init__()
        self.raw_events = raw_events
        self.total_count = total_count

    def get_job_events(self, job_id: str, *, limit: int = 200):
        if job_id != "job-1":
            raise KeyError(job_id)
        self.last_event_limit = limit
        return self.raw_events

    def count_job_events(self, job_id: str) -> int:
        if job_id != "job-1":
            raise KeyError(job_id)
        return self.total_count


class JobsActionsBackendTests(unittest.TestCase):
    def _assert_ok(self, payload: dict[str, object]) -> dict[str, object]:
        self.assertTrue(payload["ok"])
        self.assertIn("data", payload)
        return payload["data"]  # type: ignore[return-value]

    def test_job_summary_and_events_preserve_business_identity(self) -> None:
        service = FakeJobsService()

        summary_status, summary_payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/jobs/job-1",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )
        events_status, events_payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/jobs/job-1/events?limit=2",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(summary_status, 200)
        summary = self._assert_ok(summary_payload)
        self.assertEqual(summary["business_id"], "equity_transfer")
        self.assertEqual(summary["business_label"], "股权转让")
        self.assertEqual(summary["scope"]["record_family"], "listing")
        self.assertEqual(summary["scope"]["business_id"], "equity_transfer")

        self.assertEqual(events_status, 200)
        events = self._assert_ok(events_payload)
        self.assertEqual(service.last_event_limit, 3)
        self.assertEqual(events["events"][0]["business_id"], "equity_transfer")
        self.assertEqual(events["events"][0]["business_label"], "股权转让")
        self.assertEqual(events["events"][0]["task_label"], "北交所 - 增资扩股")
        self.assertEqual(events["events"][1]["error_code"], "collect_failed")
        self.assertEqual(events["events"][1]["business_id"], "equity_transfer")

    def test_jobs_limit_query_rejects_invalid_explicit_value_instead_of_defaulting(self) -> None:
        service = FakeJobsService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/jobs?limit=abc",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], ERROR_INVALID_REQUEST)
        self.assertIn("invalid limit", payload["error"]["message"])
        self.assertIsNone(service.last_jobs_limit)

    def test_jobs_limit_query_rejects_explicit_zero_instead_of_coercing_to_one(self) -> None:
        service = FakeJobsService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/jobs?limit=0",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], ERROR_INVALID_REQUEST)
        self.assertIn("invalid limit", payload["error"]["message"])
        self.assertIsNone(service.last_jobs_limit)

    def test_job_events_limit_query_rejects_invalid_explicit_value_instead_of_defaulting(self) -> None:
        service = FakeJobsService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/jobs/job-1/events?limit=abc",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], ERROR_INVALID_REQUEST)
        self.assertIn("invalid limit", payload["error"]["message"])
        self.assertIsNone(service.last_event_limit)

    def test_job_events_envelope_rejects_invalid_items_shape(self) -> None:
        for raw_events in (None, "not-events", {"event_id": "event-1"}):
            with self.subTest(raw_events=raw_events):
                service = FakeInvalidJobEventsEnvelopeService(raw_events=raw_events, total_count=0)

                status, payload = dispatch_api_request(
                    service,  # type: ignore[arg-type]
                    method="GET",
                    path="/api/jobs/job-1/events?limit=2",
                    headers={"X-PEAP-Desktop-Token": "test-token"},
                    api_token="test-token",
                )

                self.assertEqual(status, 400)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], ERROR_INVALID_REQUEST)
                self.assertIn("items must be a list", payload["error"]["message"])

    def test_job_events_envelope_rejects_invalid_total_count(self) -> None:
        service = FakeInvalidJobEventsEnvelopeService(raw_events=[{"event_id": "event-1"}] * 3, total_count="abc")

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/jobs/job-1/events?limit=2",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], ERROR_INVALID_REQUEST)
        self.assertIn("total_count must be an integer", payload["error"]["message"])

    def test_exports_history_limit_query_rejects_invalid_explicit_value_instead_of_defaulting(self) -> None:
        service = FakeJobsService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/exports/history?limit=abc",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], ERROR_INVALID_REQUEST)
        self.assertIn("invalid limit", payload["error"]["message"])
        self.assertIsNone(service.last_exports_history_limit)

    def test_exports_history_limit_query_rejects_explicit_negative_instead_of_coercing_to_one(self) -> None:
        service = FakeJobsService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/exports/history?limit=-1",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], ERROR_INVALID_REQUEST)
        self.assertIn("invalid limit", payload["error"]["message"])
        self.assertIsNone(service.last_exports_history_limit)

    def test_exports_endpoint_passes_family_aware_business_scope(self) -> None:
        service = FakeJobsService()
        request_payload = {
            "scope": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "cbex",
                "state": "all",
                "keyword": "",
                "date_from": "",
                "date_to": "",
                "page": 1,
                "page_size": 50,
            },
            "requested_export_mode": "full",
            "output_dir": "/tmp/export",
        }

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
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
        data = self._assert_ok(payload)
        self.assertEqual(
            service.last_export_payload,
            {
                "scope": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "business_label": "股权转让",
                    "exchange": "cbex",
                    "state": "all",
                    "keyword": "",
                    "date_from": "",
                    "date_to": "",
                    "page": 1,
                    "page_size": 50,
                },
                "requested_export_mode": "full",
                "output_dir": "/tmp/export",
            },
        )
        self.assertNotIn("mode", service.last_export_payload)
        self.assertNotIn("cursor_key", service.last_export_payload)
        self.assertEqual(data["scope"]["business_id"], "equity_transfer")
        self.assertEqual(data["scope"]["business_label"], "股权转让")
        self.assertEqual(data["scope"]["record_family"], "listing")
        self.assertNotIn("project_type", data["scope"])

    def test_exports_endpoint_rejects_legacy_project_type_scope(self) -> None:
        service = FakeJobsService()
        request_payload = {
            "scope": {
                "record_family": "listing",
                "project_type": "股权转让",
                "exchange": "cbex",
                "state": "all",
                "page": 1,
                "page_size": 50,
            },
            "requested_export_mode": "full",
        }

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="POST",
            path="/api/exports",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body=request_payload,
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], ERROR_INVALID_REQUEST)
        self.assertIn("project_type", payload["error"]["message"])

    def test_job_summary_and_events_keep_business_re_evaluation_metrics_distinct(self) -> None:
        service = FakeBusinessReEvaluationJobsService()

        summary_status, summary_payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/jobs/job-1",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )
        events_status, events_payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/jobs/job-1/events?limit=2",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(summary_status, 200)
        summary = self._assert_ok(summary_payload)
        self.assertEqual(
            summary["result"]["metrics"],
            [
                {"key": "pending_review_count", "label": "待人工复核", "value": 3},
                {"key": "accepted_completed_count", "label": "已采纳", "value": 7},
                {"key": "skipped_count", "label": "已跳过", "value": 1},
            ],
        )
        self.assertEqual(
            summary["progress"]["metrics"],
            [
                {"key": "pending_review_count", "label": "待人工复核", "value": 2},
                {"key": "accepted_completed_count", "label": "已采纳", "value": 5},
            ],
        )

        self.assertEqual(events_status, 200)
        events = self._assert_ok(events_payload)
        self.assertEqual(events["events"][0]["kind"], "business_re_evaluation")
        self.assertEqual(
            events["events"][0]["summary"],
            {
                "pending_review_count": 3,
                "accepted_completed_count": 7,
            },
        )


if __name__ == "__main__":
    unittest.main()
