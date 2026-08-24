from __future__ import annotations

import unittest

from desktop_backend.app_backend import dispatch_api_request


class FakeOverviewService:
    def overview(self) -> dict[str, object]:
        return {
            "record_summary": {
                "state_counts": {"ready": 3, "pending_mapping": 2},
                "pending_mapping_count": 2,
            },
            "latest_job": {
                "job_id": "job-1",
                "job_type": "one_click",
                "status": "running",
                "created_at": "2026-04-12T10:00:00",
                "updated_at": "2026-04-12T10:10:00",
                "downloaded_count": 9,
                "persisted_count": 4,
                "exception_count": 1,
                "metadata": {"record_family": "listing", "business_id": "equity_transfer"},
            },
            "latest_progress": {
                "phase_code": "prepare_tasks",
                "phase_label": "正在扫描网页",
                "job_status": "running",
                "phase_percent": 25,
                "current_task_label": "北交所 - 增资扩股",
            },
            "recent_jobs": [],
            "runtime": {
                "browser": {
                    "installed": False,
                    "browser_name": "chromium",
                    "installation_source": "system",
                    "error": "runtime missing",
                },
                "install": {
                    "status": "idle",
                    "browser_name": "chromium",
                    "trigger": "",
                    "attempt_count": 0,
                    "started_at": "",
                    "updated_at": "",
                    "completed_at": "",
                    "message": "",
                    "running": False,
                },
                "readiness": {
                    "ready": False,
                    "download_ready": False,
                    "browser_runtime_ready": False,
                    "issues": [
                        {
                            "code": "browser_runtime_missing",
                            "severity": "error",
                            "message": "runtime missing",
                        }
                    ],
                },
            },
            "defaults": {"manual_import_input_dir": "/tmp/manual"},
            "product_profile": {"profile_id": "desktop_listing"},
        }

    def build_job_progress(self, job):
        return {
            "phase_code": "prepare_tasks",
            "phase_label": "正在扫描网页",
            "job_status": str((job or {}).get("status") or ""),
            "phase_percent": 25,
            "current_task_label": "北交所 - 增资扩股",
        }


class OverviewRuntimeBackendTests(unittest.TestCase):
    def _assert_ok(self, payload: dict[str, object]) -> dict[str, object]:
        self.assertTrue(payload["ok"])
        self.assertIn("data", payload)
        return payload["data"]  # type: ignore[return-value]

    def test_overview_exposes_explicit_phase_one_visibility_instead_of_static_profile_truth(self) -> None:
        service = FakeOverviewService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/overview",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        data = self._assert_ok(payload)
        self.assertEqual(data["record_summary"]["pending_mapping_count"], 2)
        self.assertEqual(data["runtime"]["readiness"]["browser_runtime_ready"], False)
        self.assertEqual(data["visibility"]["mode"], "listing_only")
        self.assertEqual(data["visibility"]["visible_families"], ["listing"])
        self.assertNotIn("product_profile", data)

    def test_overview_resource_keeps_business_re_evaluation_metrics_on_public_latest_job_and_progress(self) -> None:
        class _BusinessReEvaluationOverviewService(FakeOverviewService):
            def overview(self) -> dict[str, object]:
                payload = super().overview()
                payload["latest_job"] = {
                    "job_id": "job-business-re-eval",
                    "job_type": "business_re_evaluation",
                    "status": "success_with_warnings",
                    "created_at": "2026-04-12T10:00:00",
                    "updated_at": "2026-04-12T10:10:00",
                    "summary": {
                        "pending_review_count": 3,
                        "accepted_completed_count": 7,
                        "skipped_count": 1,
                    },
                }
                payload["latest_progress"] = {
                    "phase_code": "reprocessing",
                    "phase_label": "业务重判（内部兼容）",
                    "job_status": "running",
                    "phase_percent": 50,
                    "pending_review_count": 2,
                    "accepted_completed_count": 5,
                }
                return payload

            def build_job_progress(self, job):
                return {
                    "phase_code": "reprocessing",
                    "phase_label": "业务重判（内部兼容）",
                    "job_status": str((job or {}).get("status") or ""),
                    "phase_percent": 50,
                    "pending_review_count": 2,
                    "accepted_completed_count": 5,
                }

        service = _BusinessReEvaluationOverviewService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/overview",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        data = self._assert_ok(payload)
        self.assertEqual(
            data["latest_job"]["result"]["metrics"],
            [
                {"key": "pending_review_count", "label": "待人工复核", "value": 3},
                {"key": "accepted_completed_count", "label": "已采纳", "value": 7},
                {"key": "skipped_count", "label": "已跳过", "value": 1},
            ],
        )
        self.assertEqual(
            data["latest_progress"]["metrics"],
            [
                {"key": "pending_review_count", "label": "待人工复核", "value": 2},
                {"key": "accepted_completed_count", "label": "已采纳", "value": 5},
            ],
        )


if __name__ == "__main__":
    unittest.main()
