from __future__ import annotations

import unittest

from desktop_backend.http_contract import (
    DEFAULT_JOB_EVENT_LIMIT,
    build_capacity_envelope,
    build_error_payload,
    build_job_events_envelope,
    build_not_found_payload,
    build_success_payload,
    normalize_job_event_limit,
)


class HttpContractTest(unittest.TestCase):
    def test_normalize_job_event_limit_uses_one_upper_bound(self) -> None:
        self.assertEqual(DEFAULT_JOB_EVENT_LIMIT, 200)
        self.assertEqual(normalize_job_event_limit(None), 200)
        self.assertEqual(normalize_job_event_limit(5), 5)
        self.assertEqual(normalize_job_event_limit(1000), 200)

    def test_build_job_events_envelope_reports_returned_total_and_truncation(self) -> None:
        envelope = build_job_events_envelope(
            [{"event_id": 1}, {"event_id": 2}],
            total_count=4,
        )

        self.assertEqual(envelope["events"], [{"event_id": 1}, {"event_id": 2}])
        self.assertEqual(envelope["returned_count"], 2)
        self.assertEqual(envelope["total_count"], 4)
        self.assertTrue(envelope["truncated"])

    def test_build_capacity_envelope_reports_returned_total_and_truncation(self) -> None:
        envelope = build_capacity_envelope(
            ["pending-1", "pending-2"],
            total_count=5,
        )

        self.assertEqual(envelope["items"], ["pending-1", "pending-2"])
        self.assertEqual(envelope["returned_count"], 2)
        self.assertEqual(envelope["total_count"], 5)
        self.assertTrue(envelope["truncated"])

    def test_build_success_payload_wraps_data_and_optional_meta(self) -> None:
        payload = build_success_payload(
            data={"jobs": [{"job_id": "job-1"}]},
            meta={"request_id": "req-1"},
        )

        self.assertEqual(
            payload,
            {
                "ok": True,
                "data": {"jobs": [{"job_id": "job-1"}]},
                "meta": {"request_id": "req-1"},
            },
        )

    def test_build_error_payload_uses_nested_error_shape(self) -> None:
        payload = build_error_payload(
            error_code="mutating_job_in_progress",
            message="已有执行中的任务：一键执行",
            details={"active_job_type": "one_click"},
        )

        self.assertEqual(
            payload,
            {
                "ok": False,
                "error": {
                    "code": "mutating_job_in_progress",
                    "message": "已有执行中的任务：一键执行",
                    "details": {"active_job_type": "one_click"},
                },
            },
        )

    def test_build_not_found_payload_uses_fixed_shape(self) -> None:
        self.assertEqual(
            build_not_found_payload(resource="job", resource_id="job-1"),
            {
                "ok": False,
                "error": {
                    "code": "not_found",
                    "message": "not_found",
                    "details": {
                        "resource": "job",
                        "resource_id": "job-1",
                    },
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
