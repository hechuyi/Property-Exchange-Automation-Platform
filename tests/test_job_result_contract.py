from __future__ import annotations

import unittest

from desktop_backend.job_result_contract import build_job_result_view


class JobResultContractTest(unittest.TestCase):
    def test_unknown_job_type_does_not_expose_dynamic_numeric_metrics(self) -> None:
        result = build_job_result_view(
            {
                "job_type": "custom_pipeline",
                "status": "success",
                "summary": {
                    "message": "自定义任务完成",
                    "internal_metric": 7,
                    "pending_mapping_count": 3,
                },
            }
        )

        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["message"], "自定义任务完成")
        self.assertEqual(result["metrics"], [])

    def test_known_job_type_only_exposes_declared_result_metrics(self) -> None:
        result = build_job_result_view(
            {
                "job_type": "export_excel",
                "status": "success",
                "summary": {
                    "new_records": 2,
                    "changed_records": 1,
                    "visible_count": 5,
                    "internal_metric": 9,
                },
            }
        )

        self.assertEqual(
            result["metrics"],
            [
                {"key": "new_records", "label": "新增记录", "value": 2},
                {"key": "changed_records", "label": "变更记录", "value": 1},
                {"key": "visible_count", "label": "可见记录", "value": 5},
            ],
        )

    def test_job_result_view_preserves_business_identity_and_scope_snapshot(self) -> None:
        result = build_job_result_view(
            {
                "job_type": "export_excel",
                "status": "success",
                "metadata": {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "business_label": "实物资产",
                    "scope": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "business_label": "实物资产",
                        "exchange": "cbex",
                    },
                },
                "summary": {
                    "message": "导出完成",
                    "new_records": 2,
                    "changed_records": 1,
                    "visible_count": 5,
                },
            }
        )

        self.assertIn("business_id", result)
        self.assertEqual(result["business_id"], "physical_asset")
        self.assertIn("business_label", result)
        self.assertEqual(result["business_label"], "实物资产")
        self.assertIn("scope", result)
        self.assertEqual(
            result["scope"],
            {
                "record_family": "listing",
                "business_id": "physical_asset",
                "business_label": "实物资产",
                "exchange": "cbex",
            },
        )

    def test_business_re_evaluation_job_result_exposes_distinct_public_metrics(self) -> None:
        result = build_job_result_view(
            {
                "job_type": "business_re_evaluation",
                "status": "success_with_warnings",
                "summary": {
                    "pending_review_count": 3,
                    "accepted_completed_count": 7,
                    "skipped_count": 1,
                    "failed_count": 0,
                    "pending_mapping_count": 99,
                    "mapping_conflict_count": 2,
                },
            }
        )

        self.assertEqual(result["outcome"], "succeeded_with_warnings")
        self.assertEqual(
            result["metrics"],
            [
                {"key": "pending_review_count", "label": "待人工复核", "value": 3},
                {"key": "pending_mapping_count", "label": "待补映射", "value": 99},
                {"key": "mapping_conflict_count", "label": "映射冲突", "value": 2},
                {"key": "accepted_completed_count", "label": "已采纳", "value": 7},
                {"key": "skipped_count", "label": "已跳过", "value": 1},
                {"key": "failed_count", "label": "失败", "value": 0},
            ],
        )

    def test_manual_import_job_result_exposes_pending_review_metric(self) -> None:
        result = build_job_result_view(
            {
                "job_type": "manual_import",
                "status": "success_with_warnings",
                "summary": {
                    "imported_count": 2,
                    "pending_review_count": 1,
                    "pending_mapping_count": 0,
                    "skipped_count": 0,
                    "failed_count": 0,
                },
            }
        )

        self.assertEqual(
            result["metrics"],
            [
                {"key": "imported_count", "label": "导入成功", "value": 2},
                {"key": "pending_review_count", "label": "待人工复核", "value": 1},
                {"key": "pending_mapping_count", "label": "待补映射", "value": 0},
                {"key": "skipped_count", "label": "已跳过", "value": 0},
                {"key": "failed_count", "label": "失败", "value": 0},
            ],
        )

    def test_ingest_job_result_exposes_all_operator_attention_metrics(self) -> None:
        result = build_job_result_view(
            {
                "job_type": "one_click",
                "status": "success_with_warnings",
                "summary": {
                    "downloaded_count": 30,
                    "persisted_count": 28,
                    "exception_count": 1,
                    "pending_review_count": 1,
                    "pending_mapping_count": 2,
                    "mapping_conflict_count": 3,
                    "skipped_count": 4,
                    "failed_count": 5,
                },
            }
        )

        self.assertEqual(result["outcome"], "succeeded_with_warnings")
        self.assertEqual(
            result["metrics"],
            [
                {"key": "downloaded_count", "label": "已下载", "value": 30},
                {"key": "persisted_count", "label": "已归档", "value": 28},
                {"key": "exception_count", "label": "异常", "value": 1},
                {"key": "pending_review_count", "label": "待人工复核", "value": 1},
                {"key": "pending_mapping_count", "label": "待补映射", "value": 2},
                {"key": "mapping_conflict_count", "label": "映射冲突", "value": 3},
                {"key": "skipped_count", "label": "已跳过", "value": 4},
                {"key": "failed_count", "label": "失败", "value": 5},
            ],
        )

    def test_download_job_result_exposes_download_archive_audit(self) -> None:
        archive_audit = {
            "root": "/tmp/archive",
            "ok": True,
            "html_count": 3,
            "sidecar_count": 3,
            "issue_count": 0,
            "issues": [],
        }

        result = build_job_result_view(
            {
                "job_type": "download_ingest",
                "status": "success",
                "summary": {
                    "downloaded_count": 3,
                    "persisted_count": 3,
                    "download_archive_audit": archive_audit,
                },
            }
        )

        self.assertEqual(result["download_archive_audit"], archive_audit)

    def test_one_click_result_exposes_filtered_public_resource_summary(self) -> None:
        result = build_job_result_view(
            {
                "job_type": "one_click",
                "status": "success_with_warnings",
                "summary": {
                    "downloaded_count": 3,
                    "persisted_count": 3,
                    "public_resource": {
                        "status": "success",
                        "record_count": 3,
                        "workbook": "/tmp/public-resource.xlsx",
                        "evidence_root": "/tmp/evidence",
                        "archive_root": "/tmp/archive",
                        "error_type": "",
                        "error_message": "",
                        "internal_only": "must not leak",
                    },
                },
            }
        )

        self.assertEqual(
            result["public_resource"],
            {
                "status": "success",
                "record_count": 3,
                "workbook": "/tmp/public-resource.xlsx",
                "evidence_root": "/tmp/evidence",
                "archive_root": "/tmp/archive",
            },
        )

    def test_public_resource_result_rejects_non_integer_record_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "summary.public_resource.record_count must be an integer"):
            build_job_result_view(
                {
                    "job_type": "one_click",
                    "status": "success",
                    "summary": {"public_resource": {"record_count": "many"}},
                }
            )

    def test_public_resource_result_rejects_non_mapping_summary(self) -> None:
        with self.assertRaisesRegex(ValueError, "summary.public_resource must be a mapping"):
            build_job_result_view(
                {
                    "job_type": "one_click",
                    "status": "success",
                    "summary": {"public_resource": []},
                }
            )

    def test_job_result_view_rejects_non_mapping_download_archive_audit(self) -> None:
        with self.assertRaisesRegex(ValueError, "summary.download_archive_audit must be a mapping"):
            build_job_result_view(
                {
                    "job_type": "download_ingest",
                    "status": "success",
                    "summary": {
                        "downloaded_count": 3,
                        "persisted_count": 3,
                        "download_archive_audit": [],
                    },
                }
            )

    def test_job_result_view_builds_user_facing_terminal_summary(self) -> None:
        result = build_job_result_view(
            {
                "job_type": "one_click",
                "status": "success_with_warnings",
                "summary": {
                    "downloaded_count": 30,
                    "persisted_count": 28,
                    "pending_review_count": 1,
                    "pending_mapping_count": 2,
                    "mapping_conflict_count": 3,
                    "export_artifacts": ["/tmp/out.xlsx"],
                },
            }
        )

        self.assertEqual(
            result["message"],
            "已完成但有待处理：已下载 30，已归档 28，待人工复核 1，待补映射 2，映射冲突 3，生成文件 1 个",
        )

    def test_failed_job_result_prefers_failure_summary_for_user_message(self) -> None:
        result = build_job_result_view(
            {
                "job_type": "download_ingest",
                "status": "failed",
                "summary": {
                    "downloaded_count": 2,
                    "persisted_count": 1,
                    "failure_code": "download_failed",
                    "failure_stage": "save_pages",
                    "failure_message": "上交所 / 实物资产详情下载失败",
                },
            }
        )

        self.assertEqual(result["message"], "未完成：上交所 / 实物资产详情下载失败")
        self.assertEqual(result["failure_code"], "download_failed")
        self.assertEqual(result["failure_message"], "上交所 / 实物资产详情下载失败")

    def test_job_result_view_rejects_invalid_declared_metric_instead_of_dropping_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "summary.new_records must be an integer"):
            build_job_result_view(
                {
                    "job_type": "export_excel",
                    "status": "success",
                    "summary": {
                        "new_records": "two",
                        "changed_records": 1,
                        "visible_count": 5,
                    },
                }
            )

    def test_job_result_view_rejects_bool_declared_metric_instead_of_integer_coercion(self) -> None:
        with self.assertRaisesRegex(ValueError, "summary.new_records must be an integer"):
            build_job_result_view(
                {
                    "job_type": "export_excel",
                    "status": "success",
                    "summary": {
                        "new_records": True,
                        "changed_records": 1,
                        "visible_count": 5,
                    },
                }
            )

    def test_job_result_view_rejects_explicit_non_mapping_job(self) -> None:
        with self.assertRaisesRegex(ValueError, "job must be a mapping"):
            build_job_result_view([])

    def test_job_result_view_rejects_explicit_non_mapping_summary(self) -> None:
        with self.assertRaisesRegex(ValueError, "job.summary must be a mapping"):
            build_job_result_view(
                {
                    "job_type": "export_excel",
                    "status": "success",
                    "summary": [],
                }
            )

    def test_job_result_view_rejects_non_mapping_scope_snapshot(self) -> None:
        invalid_jobs = (
            (
                "job.scope",
                {
                    "job_type": "export_excel",
                    "status": "success",
                    "scope": [],
                    "summary": {},
                },
            ),
            (
                "job.metadata.scope",
                {
                    "job_type": "export_excel",
                    "status": "success",
                    "metadata": {"scope": []},
                    "summary": {},
                },
            ),
        )
        for field_name, job in invalid_jobs:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, f"{field_name} must be a mapping"):
                    build_job_result_view(job)

    def test_job_result_view_tolerates_legacy_resolution_scope_strings(self) -> None:
        for scope in ("mapping_resolution", "business_resolution"):
            with self.subTest(scope=scope):
                result = build_job_result_view(
                    {
                        "job_type": "mapping_refresh",
                        "status": "success",
                        "metadata": {"scope": scope},
                        "summary": {},
                    }
                )
                self.assertEqual(result["scope"], {})

    def test_job_result_view_rejects_non_mapping_job_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "job.metadata must be a mapping"):
            build_job_result_view(
                {
                    "job_type": "export_excel",
                    "status": "success",
                    "metadata": [],
                    "summary": {},
                }
            )


if __name__ == "__main__":
    unittest.main()
