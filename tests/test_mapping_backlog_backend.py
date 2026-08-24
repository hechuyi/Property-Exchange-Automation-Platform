from __future__ import annotations

import unittest

from desktop_backend.app_backend import dispatch_api_request


class FakeMappingBacklogService:
    def __init__(self) -> None:
        self.last_business_re_evaluation_payload = None
        self.last_pending_mapping_refresh_payload = None
        self.last_mapping_update = None
        self.last_mapping_delete = None
        self.last_undo_session_id = None
        self.startup_session_id = "startup-session-a"

    def list_mapping_entries(self):
        return [
            {
                "entry_id": "entry-1",
                "rule_kind": "transferor_group",
                "rule_title": "转让方 -> 集团",
                "source_name": "中铁",
                "target_value": "中铁集团",
                "match_field": "transferor",
                "target_field": "group_name",
                "notes": "既有规则",
                "updated_at": "2026-04-12T10:00:00",
            }
        ]

    def list_pending_mappings(self):
        return {
            "sections": [
                {
                    "section_id": "mapping_gap_resolution",
                    "title": "待映射补全",
                    "count": 1,
                    "cta_kind": "reprocess_pending",
                    "items": [
                        {
                            "record_id": "rec-mapping-gap",
                            "blocker_kind": "mapping_resolution",
                            "audit_only": False,
                        }
                    ],
                },
                {
                    "section_id": "mapping_conflict_resolution",
                    "title": "待映射冲突",
                    "count": 1,
                    "cta_kind": "read_only",
                    "items": [
                        {
                            "record_id": "rec-mapping-conflict",
                            "blocker_kind": "mapping_conflict",
                            "audit_only": False,
                        }
                    ],
                },
                {
                    "section_id": "audit",
                    "title": "审计只读",
                    "count": 1,
                    "cta_kind": "read_only",
                    "items": [
                        {
                            "record_id": "rec-hidden-audit",
                            "blocker_kind": "audit",
                            "audit_only": True,
                            "record_family": "agreement",
                        }
                    ],
                },
            ],
            "summary": {
                "actionable_count": 2,
                "mapping_gap_count": 1,
                "mapping_conflict_count": 1,
                "audit_count": 1,
            },
            "returned_count": 3,
            "total_count": 3,
            "truncated": False,
        }

    def mapping_undo_state(self):
        return {
            "available": True,
            "startup_session_id": self.startup_session_id,
            "operation_kind": "delete",
        }

    def launch_business_re_evaluation(self, payload):
        self.last_business_re_evaluation_payload = payload
        return {
            "job_id": "job-business-re-eval",
            "job_type": "business_re_evaluation",
            "affected_count": 1,
        }

    def launch_pending_mapping_refresh(self, payload):
        self.last_pending_mapping_refresh_payload = payload
        return {
            "job_id": "job-pending-mapping-refresh",
            "job_type": "mapping_refresh",
            "affected_count": 1,
        }

    def update_mapping(self, entry_id, payload):
        self.last_mapping_update = (entry_id, payload)
        return {
            "entry_id": "entry-2",
            "job_id": "job-mapping-update",
            "job_type": "mapping_refresh",
            "affected_count": 2,
            "conflict": False,
            "mode": "update",
            "existing_entry": {
                "entry_id": entry_id,
                "rule_title": "转让方 -> 集团",
                "source_name": "中铁",
                "target_value": "中铁集团",
            },
            "affected_pending_count": 1,
            "match_field": "transferor",
            "target_field": "group_name",
            "target_value": "新集团",
            "source_name": "新主体",
            "rule_kind": "transferor_group",
            "rule_title": "转让方 -> 集团",
            "source_label": "转让方名称",
            "target_label": "集团名称",
            "scope_miss": False,
            "scope_miss_message": "",
        }

    def delete_mapping(self, entry_id):
        self.last_mapping_delete = entry_id
        return {
            "entry_id": entry_id,
            "deleted": True,
            "job_id": "job-mapping-delete",
            "job_type": "mapping_refresh",
            "affected_count": 1,
        }

    def undo_last_mapping_operation(self, *, startup_session_id: str):
        self.last_undo_session_id = startup_session_id
        if not startup_session_id:
            raise ValueError("startup_session_id is required")
        if startup_session_id != self.startup_session_id:
            raise ValueError("startup_session_id mismatch; undo is only available in current backend startup session")
        return {"undone": True, "undo_kind": "delete", "entry_id": "entry-1"}


class MappingBacklogBackendTest(unittest.TestCase):
    def _assert_ok(self, payload):
        self.assertTrue(payload["ok"])
        self.assertIn("data", payload)
        return payload["data"]

    def test_get_mappings_exposes_split_backlog_sections_and_audit_only_hidden_family_blockers(self) -> None:
        service = FakeMappingBacklogService()

        status, payload = dispatch_api_request(
            service,
            method="GET",
            path="/api/mappings",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        data = self._assert_ok(payload)
        sections = {section["section_id"]: section for section in data["sections"]}
        self.assertEqual(data["summary"]["actionable_count"], 2)
        self.assertEqual(data["summary"]["audit_count"], 1)
        self.assertEqual(
            data["undo"],
            {
                "available": True,
                "startup_session_id": "startup-session-a",
                "operation_kind": "delete",
            },
        )
        self.assertEqual(sections["mapping_gap_resolution"]["count"], 1)
        self.assertEqual(sections["mapping_gap_resolution"]["cta_kind"], "reprocess_pending")
        self.assertEqual(sections["mapping_conflict_resolution"]["count"], 1)
        self.assertEqual(sections["mapping_conflict_resolution"]["cta_kind"], "read_only")
        self.assertEqual(sections["audit"]["count"], 1)
        self.assertEqual(sections["audit"]["cta_kind"], "read_only")
        self.assertTrue(sections["audit"]["items"][0]["audit_only"])

    def test_get_mappings_rejects_backlog_without_sections_instead_of_empty_queue(self) -> None:
        class BrokenBacklogService(FakeMappingBacklogService):
            def list_pending_mappings(self):
                return {"summary": {"actionable_count": 0}}

        status, payload = dispatch_api_request(
            BrokenBacklogService(),
            method="GET",
            path="/api/mappings",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("sections", payload["error"]["message"])

    def test_get_mappings_rejects_section_items_that_are_not_lists_instead_of_empty_queue(self) -> None:
        class BrokenBacklogService(FakeMappingBacklogService):
            def list_pending_mappings(self):
                return {
                    "sections": [
                        {
                            "section_id": "mapping_gap_resolution",
                            "title": "待映射补全",
                            "count": 1,
                            "cta_kind": "reprocess_pending",
                            "items": {"record_id": "rec-mapping-gap"},
                        }
                    ],
                    "summary": {"actionable_count": 1},
                }

        status, payload = dispatch_api_request(
            BrokenBacklogService(),
            method="GET",
            path="/api/mappings",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("items", payload["error"]["message"])

    def test_get_mappings_rejects_non_object_section_items_instead_of_empty_item_view(self) -> None:
        class BrokenBacklogService(FakeMappingBacklogService):
            def list_pending_mappings(self):
                return {
                    "sections": [
                        {
                            "section_id": "mapping_gap_resolution",
                            "title": "待映射补全",
                            "count": 1,
                            "cta_kind": "reprocess_pending",
                            "items": ["rec-mapping-gap"],
                        }
                    ],
                    "summary": {"actionable_count": 1},
                }

        status, payload = dispatch_api_request(
            BrokenBacklogService(),
            method="GET",
            path="/api/mappings",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("items", payload["error"]["message"])

    def test_get_mappings_rejects_malformed_candidate_resolution_nodes_instead_of_empty_resolution(self) -> None:
        class BrokenBacklogService(FakeMappingBacklogService):
            def list_pending_mappings(self):
                return {
                    "sections": [
                        {
                            "section_id": "mapping_gap_resolution",
                            "title": "待映射补全",
                            "count": 1,
                            "cta_kind": "reprocess_pending",
                            "items": [
                                {
                                    "record_id": "rec-mapping-gap",
                                    "candidate_resolutions": ["transferor_group"],
                                }
                            ],
                        }
                    ],
                    "summary": {"actionable_count": 1},
                }

        status, payload = dispatch_api_request(
            BrokenBacklogService(),
            method="GET",
            path="/api/mappings",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("candidate_resolutions", payload["error"]["message"])

    def test_post_re_evaluate_business_exposes_distinct_job_surface(self) -> None:
        service = FakeMappingBacklogService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/mappings/re-evaluate-business",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"record_ids": ["rec-business-resolution"]},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        data = self._assert_ok(payload)
        self.assertEqual(service.last_business_re_evaluation_payload, {"record_ids": ["rec-business-resolution"]})
        self.assertEqual(
            data,
            {
                "job_id": "job-business-re-eval",
                "job_type": "business_re_evaluation",
                "db_path": "",
                "input_dir": "",
                "discovered_count": 0,
                "affected_count": 1,
            },
        )

    def test_post_re_evaluate_business_rejects_non_list_record_ids_instead_of_defaulting_to_all(self) -> None:
        service = FakeMappingBacklogService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/mappings/re-evaluate-business",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"record_ids": {"record_id": "rec-business-resolution"}},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertIn("record_ids", payload["error"]["message"])
        self.assertIsNone(service.last_business_re_evaluation_payload)

    def test_post_reprocess_pending_rejects_non_list_record_ids_instead_of_defaulting_to_all(self) -> None:
        service = FakeMappingBacklogService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/mappings/reprocess-pending",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"record_ids": {"record_id": "rec-mapping-gap"}},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertIn("record_ids", payload["error"]["message"])
        self.assertIsNone(service.last_pending_mapping_refresh_payload)

    def test_put_mapping_updates_specific_entry(self) -> None:
        service = FakeMappingBacklogService()

        status, payload = dispatch_api_request(
            service,
            method="PUT",
            path="/api/mappings/entry-1",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "rule_kind": "transferor_group",
                "source_name": "新主体",
                "target_value": "新集团",
                "notes": "after",
            },
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            service.last_mapping_update,
            (
                "entry-1",
                {
                    "entry_id": "entry-1",
                    "match_field": "transferor",
                    "rule_kind": "transferor_group",
                    "source_name": "新主体",
                    "target_field": "group_name",
                    "target_value": "新集团",
                    "notes": "after",
                    "confirm_overwrite": False,
                },
            ),
        )
        data = self._assert_ok(payload)
        self.assertEqual(data["entry_id"], "entry-2")
        self.assertEqual(data["mode"], "update")
        self.assertEqual(data["affected_count"], 2)

    def test_put_mapping_rejects_non_string_body_entry_id_instead_of_stringifying_object(self) -> None:
        service = FakeMappingBacklogService()

        status, payload = dispatch_api_request(
            service,
            method="PUT",
            path="/api/mappings/entry-1",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "entry_id": {"id": "entry-1"},
                "rule_kind": "transferor_group",
                "source_name": "新主体",
                "target_value": "新集团",
            },
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")
        error = payload["error"]
        self.assertIn("entry_id", error["message"])
        self.assertIsNone(service.last_mapping_update)

    def test_delete_mapping_removes_specific_entry(self) -> None:
        service = FakeMappingBacklogService()

        status, payload = dispatch_api_request(
            service,
            method="DELETE",
            path="/api/mappings/entry-1",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(status, 200)
        self.assertEqual(service.last_mapping_delete, "entry-1")
        data = self._assert_ok(payload)
        self.assertEqual(
            data,
            {
                "entry_id": "entry-1",
                "deleted": True,
                "job_id": "job-mapping-delete",
                "job_type": "mapping_refresh",
                "affected_count": 1,
            },
        )

    def test_post_mappings_undo_requires_same_startup_session_id(self) -> None:
        service = FakeMappingBacklogService()
        headers = {
            "X-PEAP-Desktop-Token": "test-token",
            "Content-Type": "application/json",
        }

        ok_status, ok_payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/mappings/undo",
            headers=headers,
            body={"startup_session_id": "startup-session-a"},
            api_token="test-token",
        )
        self.assertEqual(ok_status, 200)
        self.assertEqual(service.last_undo_session_id, "startup-session-a")
        self.assertEqual(
            self._assert_ok(ok_payload),
            {"undone": True, "undo_kind": "delete", "entry_id": "entry-1"},
        )

        bad_status, bad_payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/mappings/undo",
            headers=headers,
            body={"startup_session_id": "startup-session-b"},
            api_token="test-token",
        )
        self.assertEqual(bad_status, 400)
        self.assertFalse(bad_payload["ok"])

    def test_post_mappings_undo_rejects_non_string_startup_session_id_before_service_call(self) -> None:
        service = FakeMappingBacklogService()

        status, payload = dispatch_api_request(
            service,
            method="POST",
            path="/api/mappings/undo",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"startup_session_id": {"id": "startup-session-a"}},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertIn("startup_session_id", payload["error"]["message"])
        self.assertIsNone(service.last_undo_session_id)
