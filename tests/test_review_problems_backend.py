from __future__ import annotations

import unittest

from desktop_backend.app_backend import dispatch_api_request
from desktop_backend.error_codes import ERROR_INVALID_REQUEST


class FakeReviewProblemService:
    def __init__(self) -> None:
        self.query = None

    def list_review_problems(self, query):
        self.query = dict(query)
        return {
            "summary": {
                "total_count": 0,
                "project_type_unresolved_count": 0,
                "business_family_unresolved_count": 0,
                "deal_data_incomplete_count": 0,
                "export_fields_missing_count": 0,
                "manual_review_unclassified_count": 0,
            },
            "rows": [],
            "returned_count": 0,
            "total_count": 0,
            "truncated": False,
        }


class ReviewProblemsBackendTest(unittest.TestCase):
    def test_get_review_problems_exposes_readonly_resource_and_filters(self) -> None:
        service = FakeReviewProblemService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/review-problems?problem_kind=export_fields_missing&page=0&page_size=500",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(service.query["problem_kind"], "export_fields_missing")
        self.assertEqual(service.query["page"], 1)
        self.assertEqual(service.query["page_size"], 200)

    def test_get_review_problems_rejects_invalid_enum(self) -> None:
        status, payload = dispatch_api_request(
            FakeReviewProblemService(),
            method="GET",
            path="/api/review-problems?state=pending_mapping",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_get_review_problems_rejects_invalid_page_instead_of_defaulting(self) -> None:
        service = FakeReviewProblemService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/review-problems?page=abc&page_size=50",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], ERROR_INVALID_REQUEST)
        self.assertIn("invalid page", payload["error"]["message"])
        self.assertIsNone(service.query)

    def test_get_review_problems_rejects_invalid_page_size_instead_of_defaulting(self) -> None:
        service = FakeReviewProblemService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/review-problems?page=1&page_size=abc",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], ERROR_INVALID_REQUEST)
        self.assertIn("invalid page_size", payload["error"]["message"])
        self.assertIsNone(service.query)

    def test_get_review_problems_rejects_date_from_after_date_to_instead_of_empty_result(self) -> None:
        service = FakeReviewProblemService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/review-problems?date_from=2026-04-30&date_to=2026-04-01",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], ERROR_INVALID_REQUEST)
        self.assertIn("date_from", payload["error"]["message"])
        self.assertIsNone(service.query)
