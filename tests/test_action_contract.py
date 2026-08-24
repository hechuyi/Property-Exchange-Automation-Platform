from __future__ import annotations

import unittest

from desktop_backend.action_contract import (
    build_export_action_view,
    build_mapping_delete_view,
    build_mapping_preview_view,
    build_mapping_undo_view,
    build_path_open_view,
    build_path_selection_view,
    build_record_field_missing_ack_view,
    build_streaming_job_launch_view,
)


class ActionContractTest(unittest.TestCase):
    def test_export_action_view_rejects_explicit_non_list_artifacts(self) -> None:
        with self.assertRaisesRegex(ValueError, "artifacts"):
            build_export_action_view({"artifacts": "export.xlsx"})

    def test_export_action_view_rejects_scalar_missing_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing_fields"):
            build_export_action_view(
                {
                    "field_missing_diagnostics": [
                        {
                            "record_id": "r-1",
                            "missing_fields": "field_name",
                        }
                    ]
                }
            )

    def test_export_action_view_rejects_non_list_field_missing_diagnostics(self) -> None:
        for raw_value in ({"record_id": "r-1"}, "not-a-list"):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(ValueError, "field_missing_diagnostics"):
                    build_export_action_view({"field_missing_diagnostics": raw_value})

    def test_streaming_job_launch_view_does_not_leak_legacy_project_type_from_scope(self) -> None:
        payload = build_streaming_job_launch_view(
            {
                "job_id": "job-1",
                "job_type": "one_click",
                "scope": {
                    "record_family": "listing",
                    "project_type": "股权转让",
                    "business_id": "equity_transfer",
                    "exchange": "sse",
                },
            }
        )

        self.assertEqual(
            payload["scope"],
            {
                "record_family": "listing",
                "state": "",
                "exchange": "sse",
                "keyword": "",
                "date_from": "",
                "date_to": "",
                "page": 0,
                "page_size": 0,
                "business_id": "equity_transfer",
            },
        )
        self.assertNotIn("project_type", payload["scope"])

    def test_streaming_job_launch_view_rejects_missing_or_non_text_required_job_fields(self) -> None:
        cases = (
            ({"job_type": "one_click"}, "job_id"),
            ({"job_id": "job-1"}, "job_type"),
            ({"job_id": 123, "job_type": "one_click"}, "job_id"),
        )
        for payload, field_name in cases:
            with self.subTest(field_name=field_name, payload=payload):
                with self.assertRaisesRegex(ValueError, field_name):
                    build_streaming_job_launch_view(payload)

    def test_streaming_job_launch_view_includes_retry_log_and_notification_fields(self) -> None:
        payload = build_streaming_job_launch_view(
            {
                "job_id": "job-2",
                "job_type": "manual_import",
                "retry_of_job_id": "job-1",
                "log_path": "/tmp/peap/logs/job-2.log",
                "notification": {
                    "level": "success",
                    "message": "任务重试已启动",
                },
            }
        )

        self.assertEqual(payload["retry_of_job_id"], "job-1")
        self.assertEqual(payload["log_path"], "/tmp/peap/logs/job-2.log")
        self.assertEqual(
            payload["notification"],
            {
                "level": "success",
                "message": "任务重试已启动",
            },
        )

    def test_record_field_missing_ack_view_rejects_string_boolean_fields(self) -> None:
        payload = {
            "record_id": "rec-1",
            "field_missing_acknowledgement": {
                "acknowledged": "false",
            },
            "attention": {
                "requires_attention": "false",
                "suppressed": "false",
            },
            "exportable": "false",
        }

        for path in (
            "field_missing_acknowledgement.acknowledged",
            "attention.requires_attention",
            "attention.suppressed",
            "exportable",
        ):
            with self.subTest(path=path):
                broken = {
                    **payload,
                    "field_missing_acknowledgement": {
                        **payload["field_missing_acknowledgement"],
                        "acknowledged": False,
                    },
                    "attention": {
                        **payload["attention"],
                        "requires_attention": False,
                        "suppressed": False,
                    },
                    "exportable": False,
                }
                if path == "field_missing_acknowledgement.acknowledged":
                    broken["field_missing_acknowledgement"]["acknowledged"] = "false"
                elif path == "attention.requires_attention":
                    broken["attention"]["requires_attention"] = "false"
                elif path == "attention.suppressed":
                    broken["attention"]["suppressed"] = "false"
                else:
                    broken["exportable"] = "false"

                with self.assertRaisesRegex(ValueError, path):
                    build_record_field_missing_ack_view(broken)

    def test_action_result_views_reject_string_boolean_fields(self) -> None:
        cases = (
            (
                build_mapping_delete_view,
                {"entry_id": "entry-1", "deleted": "false"},
                "deleted",
            ),
            (
                build_path_open_view,
                {"opened": "false", "reveal": False},
                "opened",
            ),
            (
                build_path_open_view,
                {"opened": False, "reveal": "false"},
                "reveal",
            ),
            (
                build_path_selection_view,
                {"selected": "false"},
                "selected",
            ),
            (
                build_mapping_undo_view,
                {"undone": "false"},
                "undone",
            ),
            (
                build_mapping_preview_view,
                {"conflict": "false", "scope_miss": False},
                "conflict",
            ),
            (
                build_mapping_preview_view,
                {"conflict": False, "scope_miss": "false"},
                "scope_miss",
            ),
        )
        for builder, payload, field_name in cases:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    builder(payload)


if __name__ == "__main__":
    unittest.main()
