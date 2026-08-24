from __future__ import annotations

import unittest

from desktop_backend.domain.normalizers import normalize_job_event_payload
from desktop_backend.job_event_contract import build_job_event_view


class JobEventContractTest(unittest.TestCase):
    def test_event_view_rejects_none_top_level_event_instead_of_emptying_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "raw_event must be an object"):
            build_job_event_view(None)  # type: ignore[arg-type]

    def test_event_view_rejects_non_object_top_level_event_instead_of_emptying_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "raw_event must be an object"):
            build_job_event_view("not-an-object")  # type: ignore[arg-type]

    def test_event_view_prefers_canonical_summary_payload_and_filters_unknown_summary_keys(self) -> None:
        event = build_job_event_view(
            {
                "event_id": "event-1",
                "stage": "save_pages",
                "status": "running",
                "payload": {
                    "label": "保存中",
                    "state": "RecordState.READY",
                    "summary": {"listed": 99, "internal_only_count": 1},
                    "summary_payload": {
                        "kind": "download",
                        "task_label": "北交所 - 增资扩股",
                        "task_index": 2,
                        "task_total": 4,
                        "phase_percent": 50,
                        "summary": {
                            "listed": 12,
                            "detail_fetched": 6,
                            "saved": 5,
                            "internal_only_count": 77,
                        },
                    },
                },
            }
        )

        self.assertEqual(event["stage_code"], "save_pages")
        self.assertEqual(event["stage_label"], "正在保存网页")
        self.assertEqual(event["kind"], "download")
        self.assertEqual(event["task_label"], "北交所 - 增资扩股")
        self.assertEqual(event["record_state"], "ready")
        self.assertEqual(
            event["summary"],
            {
                "listed": 12,
                "detail_fetched": 6,
                "saved": 5,
            },
        )

    def test_event_view_preserves_public_resource_top_level_payload_fields(self) -> None:
        event = build_job_event_view(
            {
                "event_id": "public-resource-1",
                "stage": "save_pages",
                "status": "done",
                "payload": {
                    "label": "公共资源网成交下载及解析完成",
                    "source_id": "public_resource",
                    "task_label": "公共资源网成交",
                    "task_index": 1,
                    "task_total": 1,
                    "phase_percent": 98,
                    "summary": {
                        "status": "success",
                        "record_count": 3,
                        "workbook": "/tmp/public-resource.xlsx",
                        "evidence_root": "/tmp/evidence",
                        "archive_root": "/tmp/archive",
                        "internal_only": "must not leak",
                    },
                    "internal_payload": "must not leak",
                },
            }
        )

        self.assertEqual(event["source_id"], "public_resource")
        self.assertEqual(event["task_label"], "公共资源网成交")
        self.assertEqual(event["task_index"], 1)
        self.assertEqual(event["task_total"], 1)
        self.assertEqual(event["phase_percent"], 98)
        self.assertEqual(
            event["summary"],
            {
                "status": "success",
                "record_count": 3,
                "workbook": "/tmp/public-resource.xlsx",
                "evidence_root": "/tmp/evidence",
                "archive_root": "/tmp/archive",
            },
        )

    def test_event_view_preserves_filtered_public_resource_retry_progress(self) -> None:
        event = build_job_event_view(
            {
                "event_id": "public-resource-retry-1",
                "stage": "save_pages",
                "status": "running",
                "payload": {
                    "source_id": "public_resource",
                    "summary": {
                        "attempt": "2",
                        "attempt_total": 3,
                        "retry_in_seconds": "5",
                        "business_code": "800",
                        "transport": " playwright ",
                        "role": "search_transient_business_error",
                        "internal_retry_token": "must not leak",
                    },
                },
            }
        )

        self.assertEqual(
            event["summary"],
            {
                "attempt": 2,
                "attempt_total": 3,
                "retry_in_seconds": 5,
                "business_code": 800,
                "transport": "playwright",
                "role": "search_transient_business_error",
            },
        )
        self.assertNotIn("internal_retry_token", event["summary"])

    def test_nested_event_progress_fields_override_legacy_top_level_fallback(self) -> None:
        event = build_job_event_view(
            {
                "event_id": "progress-override",
                "stage": "save_pages",
                "status": "running",
                "payload": {
                    "task_label": "legacy",
                    "phase_percent": 10,
                    "summary_payload": {
                        "task_label": "canonical",
                        "phase_percent": 42,
                    },
                },
            }
        )

        self.assertEqual(event["task_label"], "canonical")
        self.assertEqual(event["phase_percent"], 42)

    def test_event_view_canonicalizes_legacy_error_sources_at_normalizer_boundary(self) -> None:
        event = build_job_event_view(
            {
                "event_id": "event-2",
                "stage": "save_pages",
                "status": "failed",
                "payload": {
                    "summary_payload": {
                        "typed_error": {
                            "error_code": "collect_failed",
                            "error_message": "上游 500",
                        },
                        "typed_errors": [
                            {"error_message": "不应覆盖 canonical error"},
                        ],
                    },
                },
            }
        )

        self.assertEqual(event["error_code"], "collect_failed")
        self.assertEqual(event["error_message"], "上游 500")
        self.assertEqual(event["summary"], {})

    def test_event_view_preserves_business_identity_snapshot(self) -> None:
        event = build_job_event_view(
            {
                "event_id": "event-3",
                "stage": "save_pages",
                "status": "running",
                "project_code": "G32025SH1000194",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "payload": {
                    "label": "保存中",
                    "summary_payload": {
                        "kind": "collect",
                        "task_label": "上交所 - 股权转让",
                        "task_index": 1,
                        "task_total": 3,
                        "phase_percent": 33,
                    },
                },
            }
        )

        self.assertIn("business_id", event)
        self.assertEqual(event["business_id"], "equity_transfer")
        self.assertIn("business_label", event)
        self.assertEqual(event["business_label"], "股权转让")

    def test_event_view_falls_back_to_parent_scope_for_unscoped_lifecycle_event(self) -> None:
        event = build_job_event_view(
            {
                "event_id": "startup-1",
                "stage": "startup",
                "status": "running",
                "payload": {"label": "任务已启动"},
            },
            parent_job={
                "job_id": "job-1",
                "job_type": "one_click",
                "status": "running",
                "metadata": {
                    "scope": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "business_label": "股权转让",
                        "exchange": "sse",
                    }
                },
            },
        )

        self.assertEqual(event["record_family"], "listing")
        self.assertEqual(event["business_id"], "equity_transfer")
        self.assertEqual(event["business_label"], "股权转让")
        self.assertEqual(event["scope"]["exchange"], "sse")

    def test_event_view_keeps_specific_event_scope_over_aggregate_parent_scope(self) -> None:
        event = build_job_event_view(
            {
                "event_id": "item-1",
                "stage": "downloaded",
                "status": "ok",
                "payload": {
                    "scope": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "exchange": "cbex",
                    }
                },
            },
            parent_job={
                "job_id": "job-multi",
                "metadata": {
                    "record_families": ["listing", "deal"],
                    "family_scopes": [],
                },
            },
        )

        self.assertEqual(event["record_family"], "deal")
        self.assertEqual(event["business_id"], "deal_equity_transfer")
        self.assertEqual(event["scope"]["exchange"], "cbex")

    def test_event_view_preserves_business_re_evaluation_summary_metrics(self) -> None:
        event = build_job_event_view(
            {
                "event_id": "event-4",
                "stage": "reprocessing",
                "status": "pending_review",
                "payload": {
                    "label": "业务重判（内部兼容）后仍有待处理项",
                    "summary_payload": {
                        "kind": "business_re_evaluation",
                        "task_label": "业务重判（内部兼容）",
                        "summary": {
                            "pending_review_count": 3,
                            "accepted_completed_count": 7,
                            "internal_only_count": 99,
                        },
                    },
                },
            }
        )

        self.assertEqual(event["kind"], "business_re_evaluation")
        self.assertEqual(
            event["summary"],
            {
                "pending_review_count": 3,
                "accepted_completed_count": 7,
            },
        )

    def test_event_view_preserves_extended_download_warning_diagnostics(self) -> None:
        event = build_job_event_view(
            {
                "event_id": "event-download-warning",
                "stage": "save_pages",
                "status": "warning",
                "payload": {
                    "label": "日期范围内没有可下载详情",
                    "summary_payload": {
                        "kind": "download",
                        "warning_code": "all_listed_rows_outside_date_range",
                        "warning_message": "已列出 30 条，30 条因披露日期不在 2026-03-01..2026-03-31 被跳过",
                        "summary": {
                            "listed": 30,
                            "list_date_skipped": 30,
                            "business_filter_skipped": 4,
                            "detail_candidates": 0,
                            "saved": 0,
                            "list_unaccounted": 0,
                            "internal_only_count": 99,
                        },
                    },
                },
            }
        )

        self.assertEqual(event["status"], "warning")
        self.assertEqual(event["warning_code"], "all_listed_rows_outside_date_range")
        self.assertIn("披露日期", event["warning_message"])
        self.assertEqual(
            event["summary"],
            {
                "listed": 30,
                "list_date_skipped": 30,
                "business_filter_skipped": 4,
                "detail_candidates": 0,
                "saved": 0,
                "list_unaccounted": 0,
            },
        )

    def test_skipped_parse_event_without_payload_state_exposes_warning_reason(self) -> None:
        event = build_job_event_view(
            {
                "event_id": "event-skipped-missing-state",
                "stage": "save_pages",
                "status": "skipped",
                "error_type": "skip_parse",
                "error_message": "skip-cbex-otc-page: missing project detail card",
                "payload": {
                    "summary_payload": {
                        "kind": "save_pages",
                    },
                },
            }
        )

        self.assertEqual(event["status"], "skipped")
        self.assertEqual(event["record_state"], "")
        self.assertEqual(event["error_code"], "")
        self.assertEqual(event["error_message"], "")
        self.assertEqual(event["warning_code"], "skip_parse")
        self.assertEqual(event["warning_message"], "skip-cbex-otc-page: missing project detail card")

    def test_skipped_parse_event_with_payload_state_still_exposes_warning_reason(self) -> None:
        event = build_job_event_view(
            {
                "event_id": "event-skipped-with-state",
                "stage": "save_pages",
                "status": "skipped",
                "error_type": "skip_parse",
                "error_message": "skip-cbex-otc-page: duplicate parser guard",
                "payload": {
                    "state": "skipped",
                    "summary_payload": {
                        "kind": "save_pages",
                    },
                },
            }
        )

        self.assertEqual(event["status"], "skipped")
        self.assertEqual(event["record_state"], "skipped")
        self.assertEqual(event["error_code"], "")
        self.assertEqual(event["error_message"], "")
        self.assertEqual(event["warning_code"], "skip_parse")
        self.assertEqual(event["warning_message"], "skip-cbex-otc-page: duplicate parser guard")

    def test_event_view_rejects_non_object_summary_payload_instead_of_emptying_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "summary_payload must be an object"):
            build_job_event_view(
                {
                    "event_id": "event-bad-summary-payload",
                    "stage": "save_pages",
                    "status": "running",
                    "payload": {"summary_payload": "not-an-object"},
                }
            )

    def test_event_view_rejects_non_object_summary_instead_of_emptying_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "summary must be an object"):
            build_job_event_view(
                {
                    "event_id": "event-bad-summary",
                    "stage": "save_pages",
                    "status": "running",
                    "payload": {"summary_payload": {"summary": "not-an-object"}},
                }
            )

    def test_event_view_rejects_bad_scope_state_count_instead_of_zeroing_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "scope_state_counts.pending_review must be an integer"):
            build_job_event_view(
                {
                    "event_id": "event-bad-count",
                    "stage": "save_pages",
                    "status": "running",
                    "payload": {
                        "summary_payload": {
                            "scope_state_counts": {"pending_review": "many"},
                        },
                    },
                }
            )

    def test_event_view_rejects_bool_scope_state_count_instead_of_integer_coercion(self) -> None:
        with self.assertRaisesRegex(ValueError, "scope_state_counts.ready must be an integer"):
            build_job_event_view(
                {
                    "event_id": "event-bool-count",
                    "stage": "save_pages",
                    "status": "running",
                    "payload": {
                        "summary_payload": {
                            "scope_state_counts": {"ready": True},
                        },
                    },
                }
            )

    def test_normalize_job_event_payload_rejects_bad_summary_progress_number_instead_of_zeroing_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "summary_payload.phase_percent must be an integer"):
            normalize_job_event_payload(
                {
                    "event_id": "event-bad-progress",
                    "stage": "save_pages",
                    "status": "running",
                    "payload": {
                        "summary_payload": {
                            "task_index": 1,
                            "task_total": 4,
                            "phase_percent": "bad",
                        },
                    },
                }
            )

    def test_event_view_rejects_bad_summary_progress_number_instead_of_zeroing_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "summary_payload.task_total must be an integer"):
            build_job_event_view(
                {
                    "event_id": "event-bad-progress-view",
                    "stage": "save_pages",
                    "status": "running",
                    "payload": {
                        "summary_payload": {
                            "task_index": 1,
                            "task_total": {},
                            "phase_percent": 25,
                        },
                    },
                }
            )

    def test_event_view_rejects_bad_known_summary_number_instead_of_dropping_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "summary.saved must be an integer"):
            build_job_event_view(
                {
                    "event_id": "event-bad-summary-number",
                    "stage": "save_pages",
                    "status": "running",
                    "payload": {
                        "summary_payload": {
                            "summary": {
                                "saved": "bad",
                                "detail_candidates": 3,
                            },
                        },
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
