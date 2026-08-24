import unittest
from unittest.mock import patch

from desktop_backend.progress_resource_contract import build_progress_resource


class ProgressResourceContractTests(unittest.TestCase):
    def test_build_progress_resource_only_reads_public_task_fields(self) -> None:
        resource = build_progress_resource(
            {
                "phase_code": "process_detail_pages",
                "phase_label": "处理详情页",
                "phase_percent": 45,
                "current_item_label": "legacy item",
                "current_index": 3,
                "current_total": 9,
                "current_task_label": "公开任务",
                "task_index": 4,
                "task_total": 10,
            },
            job={"status": "running", "job_type": "one_click"},
        )

        self.assertEqual(resource["current_task_label"], "公开任务")
        self.assertEqual(resource["task_index"], 4)
        self.assertEqual(resource["task_total"], 10)

    def test_build_progress_resource_preserves_collected_candidates_stage_summary(self) -> None:
        resource = build_progress_resource(
            {
                "phase_code": "prepare_tasks",
                "phase_label": "扫描网页",
                "phase_percent": 30,
                "current_task_label": "北交所 - 成交",
                "task_index": 1,
                "task_total": 2,
                "latest_stage_summary": {
                    "listed": 12,
                    "collected_candidates": 7,
                    "internal_only_count": 99,
                },
            },
            job={"status": "running", "job_type": "one_click"},
        )

        self.assertEqual(
            resource["latest_stage_summary"],
            {
                "listed": 12,
                "collected_candidates": 7,
            },
        )

    def test_build_progress_resource_does_not_bridge_legacy_internal_task_fields(self) -> None:
        resource = build_progress_resource(
            {
                "phase_code": "process_detail_pages",
                "phase_label": "处理详情页",
                "phase_percent": 45,
                "current_item_label": "legacy item",
                "current_index": 3,
                "current_total": 9,
            },
            job={"status": "running", "job_type": "one_click"},
        )

        self.assertEqual(resource["current_task_label"], "")
        self.assertEqual(resource["task_index"], 0)
        self.assertEqual(resource["task_total"], 0)

    def test_build_progress_resource_preserves_business_identity_and_scope_from_job_metadata(self) -> None:
        resource = build_progress_resource(
            {
                "phase_code": "process_detail_pages",
                "phase_label": "处理详情页",
                "phase_percent": 45,
                "current_task_label": "北交所 - 股权转让",
                "task_index": 3,
                "task_total": 9,
            },
            job={
                "status": "running",
                "job_type": "one_click",
                "metadata": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "business_label": "股权转让",
                    "scope": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "business_label": "股权转让",
                        "exchange": "cbex",
                    },
                },
            },
        )

        self.assertIn("business_id", resource)
        self.assertEqual(resource["business_id"], "equity_transfer")
        self.assertIn("business_label", resource)
        self.assertEqual(resource["business_label"], "股权转让")
        self.assertIn("scope", resource)
        self.assertEqual(
            resource["scope"],
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "cbex",
            },
        )

    def test_build_progress_resource_preserves_business_re_evaluation_metrics(self) -> None:
        resource = build_progress_resource(
            {
                "job_status": "running",
                "phase_code": "reprocessing",
                "phase_label": "业务重判（内部兼容）",
                "phase_percent": 50,
                "pending_review_count": 3,
                "accepted_completed_count": 7,
                "skipped_count": 1,
                "failed_count": 0,
            },
            job={"status": "running", "job_type": "business_re_evaluation"},
        )

        self.assertEqual(
            resource["metrics"],
            [
                {"key": "pending_review_count", "label": "待人工复核", "value": 3},
                {"key": "accepted_completed_count", "label": "已采纳", "value": 7},
                {"key": "skipped_count", "label": "已跳过", "value": 1},
                {"key": "failed_count", "label": "失败", "value": 0},
            ],
        )

    def test_build_progress_resource_rejects_invalid_public_progress_numbers(self) -> None:
        invalid_cases = (
            ("phase_percent", {"phase_percent": "half", "task_index": 1, "task_total": 2}),
            ("task_index", {"phase_percent": 50, "task_index": "first", "task_total": 2}),
            ("task_total", {"phase_percent": 50, "task_index": 1, "task_total": "many"}),
            ("phase_percent", {"phase_percent": True, "task_index": 1, "task_total": 2}),
            ("task_index", {"phase_percent": 50, "task_index": False, "task_total": 2}),
            ("task_total", {"phase_percent": 50, "task_index": 1, "task_total": True}),
        )
        for field_name, overrides in invalid_cases:
            with self.subTest(field_name=field_name):
                payload = {
                    "phase_code": "process_detail_pages",
                    "phase_label": "处理详情页",
                    "current_task_label": "公开任务",
                    **overrides,
                }
                with self.assertRaisesRegex(ValueError, f"{field_name} must be an integer"):
                    build_progress_resource(payload, job={"status": "running", "job_type": "one_click"})

    def test_build_progress_resource_rejects_explicit_non_mapping_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload must be a mapping"):
            build_progress_resource([], job={"status": "running", "job_type": "one_click"})

    def test_build_progress_resource_rejects_explicit_non_mapping_job(self) -> None:
        with self.assertRaisesRegex(ValueError, "job must be a mapping"):
            build_progress_resource({"phase_percent": 50}, job=[])

    def test_build_progress_resource_rejects_non_mapping_scope_snapshot(self) -> None:
        invalid_jobs = (
            (
                "job.scope",
                {
                    "status": "running",
                    "job_type": "one_click",
                    "scope": [],
                },
            ),
            (
                "job.metadata.scope",
                {
                    "status": "running",
                    "job_type": "one_click",
                    "metadata": {"scope": []},
                },
            ),
        )
        for field_name, job in invalid_jobs:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, f"{field_name} must be a mapping"):
                    build_progress_resource({"phase_percent": 50}, job=job)

    def test_build_progress_resource_tolerates_legacy_resolution_scope_strings(self) -> None:
        for scope in ("mapping_resolution", "business_resolution"):
            with self.subTest(scope=scope):
                resource = build_progress_resource(
                    {"phase_percent": 50},
                    job={"status": "running", "job_type": "mapping_refresh", "metadata": {"scope": scope}},
                )
                self.assertEqual(resource["scope"], {})

    def test_build_progress_resource_rejects_non_mapping_job_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "job.metadata must be a mapping"):
            build_progress_resource(
                {"phase_percent": 50},
                job={"status": "running", "job_type": "one_click", "metadata": []},
            )

    def test_build_progress_resource_rejects_non_mapping_latest_stage_summary(self) -> None:
        with self.assertRaisesRegex(ValueError, "latest_stage_summary must be a mapping or text"):
            build_progress_resource(
                {
                    "phase_percent": 50,
                    "latest_stage_summary": [],
                },
                job={"status": "running", "job_type": "one_click"},
            )

    def test_build_progress_resource_rejects_bad_progress_view_nested_shapes(self) -> None:
        base_progress_view = {
            "record_family": "",
            "business_id": "",
            "business_label": "",
            "scope": {},
            "phase_code": "",
            "phase_label": "",
            "job_status": "running",
            "is_terminal": False,
            "current_item_label": "",
            "current_index": 0,
            "current_total": 0,
            "metrics": [],
            "latest_stage_code": "",
            "latest_stage_label": "",
            "latest_stage_summary": {},
        }
        invalid_cases = (
            ("progress.scope", {"scope": []}, "progress.scope must be a mapping"),
            ("progress.metrics", {"metrics": {}}, "progress.metrics must be a list"),
            (
                "progress.latest_stage_summary",
                {"latest_stage_summary": []},
                "progress.latest_stage_summary must be a mapping",
            ),
        )
        for field_name, override, message in invalid_cases:
            with self.subTest(field_name=field_name):
                with patch(
                    "desktop_backend.progress_resource_contract.build_progress_view",
                    return_value={**base_progress_view, **override},
                ):
                    with self.assertRaisesRegex(ValueError, message):
                        build_progress_resource({"phase_percent": 50}, job={"status": "running", "job_type": "one_click"})


if __name__ == "__main__":
    unittest.main()
