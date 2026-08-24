import json
import os
import tempfile
import unittest
from unittest.mock import patch

from desktop_backend.services.records_service import RecordsService, normalize_request_scope


class _FalsyDict(dict):
    def __bool__(self) -> bool:
        return False


class _FakeRecordsRepository:
    def __init__(self, records):
        self.records = list(records)

    def iter_latest_records(self, **_kwargs):
        return list(self.records)


def _canonical_listing_record(
    *,
    project_code: str,
    project_name: str,
    business_id: str = "physical_asset",
    project_type: str = "实物资产",
    canonical_fields: dict[str, object] | None = None,
) -> dict[str, object]:
    fields: dict[str, object] = {
        "project_code": project_code,
        "project_name": project_name,
        "project_type": project_type,
        "status": "挂牌中",
        "exchange": "上交所",
        "seller": "测试转让方",
        "price": "100.00",
        "start_date": "2026/04/21",
        "source_type": "国资",
    }
    fields.update(canonical_fields or {})
    return {
        "record_family": "listing",
        "business_identity": {
            "business_id": business_id,
            "raw_business_label": project_type,
        },
        "source_identity": {"source_id": "sse"},
        "canonical_fields": fields,
        "export_extras": {},
    }


class RecordsServiceScopeContractTests(unittest.TestCase):
    def test_normalize_request_scope_rejects_non_mapping_payload(self) -> None:
        for payload in ([], "bad", [("state", "ready")]):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "payload must be an object"):
                    normalize_request_scope(payload, require_explicit_scope=False)  # type: ignore[arg-type]

    def test_normalize_request_scope_requires_scope_for_none_when_explicit(self) -> None:
        with self.assertRaisesRegex(ValueError, "scope is required"):
            normalize_request_scope(None, require_explicit_scope=True)

    def test_normalize_request_scope_does_not_accept_top_level_limit_alias(self) -> None:
        query_scope, normalized_scope, scope_payload = normalize_request_scope(
            {
                "state": "pending_mapping",
                "limit": 2,
            },
            require_explicit_scope=False,
        )

        self.assertEqual(query_scope["limit"], 2)
        self.assertEqual(scope_payload["state"], "pending_mapping")
        self.assertEqual(scope_payload["page_size"], 50)
        self.assertEqual(normalized_scope.page_size, 50)

    def test_normalize_request_scope_preserves_family_business_identity(self) -> None:
        query_scope, normalized_scope, scope_payload = normalize_request_scope(
            {
                "scope": {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "exchange": "sse",
                    "state": "ready",
                }
            },
            require_explicit_scope=True,
        )

        self.assertEqual(query_scope["scope"]["business_id"], "physical_asset")
        self.assertEqual(scope_payload["record_family"], "listing")
        self.assertEqual(scope_payload["business_id"], "physical_asset")
        self.assertEqual(scope_payload["exchange"], "sse")
        self.assertEqual(normalized_scope.business_id, "physical_asset")

    def test_normalize_request_scope_rejects_legacy_project_type_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "project_type"):
            normalize_request_scope(
                {
                    "scope": {
                        "record_family": "listing",
                        "project_type": "股权转让",
                        "exchange": "sse",
                        "state": "ready",
                    }
                },
                require_explicit_scope=True,
            )

    def test_list_records_excludes_unresolved_rows_under_explicit_business_scope(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-unresolved",
                        "project_code": "CODE-UNRESOLVED",
                        "project_name": "未归属项目",
                        "project_type": "未知业务",
                        "business_id": "",
                        "exchange": "beijing",
                        "listing_date": "2026-03-21",
                        "state": "pending_review",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-03-21T10:00:00",
                    },
                    {
                        "record_id": "r-physical",
                        "project_code": "CODE-PHYSICAL",
                        "project_name": "实物资产项目",
                        "project_type": "实物资产",
                        "business_id": "physical_asset",
                        "exchange": "beijing",
                        "listing_date": "2026-03-21",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-03-21T10:01:00",
                    },
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_display_values",
                side_effect=lambda record, project_kind=None: {
                    "项目类型": "实物资产" if record.get("business_id") == "physical_asset" else "未知业务",
                    "交易所": "北京产权交易所",
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
            patch(
                "desktop_backend.services.records_service._resolve_record_artifact_path",
                side_effect=lambda record, **_kwargs: "/archive/physical.html" if record.get("record_id") == "r-physical" else "",
            ),
        ):
            payload = service.list_records(
                {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "exchange": "cbex",
                    "state": "all",
                }
            )

        self.assertEqual(payload["scope"]["business_id"], "physical_asset")
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual([row["record_id"] for row in payload["rows"]], ["r-physical"])

    def test_list_records_default_all_scope_includes_browsable_failed_and_field_missing_states(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-ready",
                        "project_code": "CODE-READY",
                        "project_name": "正常记录",
                        "project_type": "实物资产",
                        "business_id": "physical_asset",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:00:00",
                    },
                    {
                        "record_id": "r-failed",
                        "project_code": "CODE-FAILED",
                        "project_name": "失败对象",
                        "project_type": "实物资产",
                        "business_id": "",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "parse_failed",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:01:00",
                    },
                    {
                        "record_id": "r-postprocess-failed",
                        "project_code": "CODE-POSTPROCESS-FAILED",
                        "project_name": "处理失败对象",
                        "project_type": "实物资产",
                        "business_id": "",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "postprocess_failed",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:02:00",
                    },
                    {
                        "record_id": "r-field-missing",
                        "project_code": "CODE-FIELD-MISSING",
                        "project_name": "缺字段对象",
                        "project_type": "实物资产",
                        "business_id": "physical_asset",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "field_missing",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:03:00",
                    },
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_display_values",
                side_effect=lambda record, project_kind=None: {
                    "项目类型": record.get("project_type") or "",
                    "交易所": "上交所",
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
            patch(
                "desktop_backend.services.records_service._resolve_record_artifact_path",
                return_value="",
            ),
        ):
            payload = service.list_records(
                {
                    "record_family": "listing",
                    "business_id": "all",
                    "exchange": "all",
                    "state": "all",
                }
            )

        self.assertEqual(payload["total_count"], 4)
        self.assertEqual(
            payload["summary"]["filtered_state_counts"],
            {"ready": 1, "parse_failed": 1, "postprocess_failed": 1, "field_missing": 1},
        )
        self.assertEqual(
            [row["record_id"] for row in payload["rows"]],
            ["r-ready", "r-failed", "r-postprocess-failed", "r-field-missing"],
        )

    def test_list_records_rejects_explicit_unknown_state_instead_of_empty_result(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-ready",
                        "project_code": "CODE-READY",
                        "project_name": "正常记录",
                        "project_type": "实物资产",
                        "business_id": "physical_asset",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:00:00",
                    },
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with self.assertRaisesRegex(ValueError, "unknown state"):
            service.list_records(
                {
                    "record_family": "listing",
                    "business_id": "all",
                    "exchange": "all",
                    "state": "not_a_state",
                }
            )

    def test_list_records_exposes_field_missing_ack_as_noise_only_attention_state(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-field-missing",
                        "project_code": "CODE-FIELD-MISSING",
                        "project_name": "缺字段对象",
                        "project_type": "实物资产",
                        "business_id": "physical_asset",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "field_missing",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:03:00",
                        "revision_id": 12,
                        "acknowledged_payload_json": {
                            "field_missing": {
                                "acknowledged": True,
                                "missing_fields_hash": "hash-1",
                                "missing_fields": [
                                    {
                                        "kind": "export",
                                        "field": "类型",
                                        "canonical_field": "",
                                        "export_field": "类型",
                                        "message": "export field 类型 is required",
                                    }
                                ],
                            }
                        },
                    },
                    {
                        "record_id": "r-field-missing-attention",
                        "project_code": "CODE-FIELD-MISSING-2",
                        "project_name": "缺字段对象 2",
                        "project_type": "实物资产",
                        "business_id": "physical_asset",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "field_missing",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:04:00",
                        "revision_id": 13,
                        "acknowledged_payload_json": {},
                    },
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_display_values",
                side_effect=lambda record, project_kind=None: {
                    "项目类型": record.get("project_type") or "",
                    "交易所": "上交所",
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
            patch(
                "desktop_backend.services.records_service._resolve_record_artifact_path",
                return_value="",
            ),
        ):
            payload = service.list_records(
                {
                    "record_family": "listing",
                    "business_id": "all",
                    "exchange": "all",
                    "state": "all",
                }
            )

        acked, unacked = payload["rows"]
        self.assertEqual(acked["state"], "field_missing")
        self.assertTrue(acked["field_missing_acknowledgement"]["acknowledged"])
        self.assertFalse(acked["attention"]["requires_attention"])
        self.assertTrue(acked["attention"]["suppressed"])
        self.assertFalse(acked["exportable"])
        self.assertEqual(unacked["state"], "field_missing")
        self.assertFalse(unacked["field_missing_acknowledgement"]["acknowledged"])
        self.assertTrue(unacked["attention"]["requires_attention"])
        self.assertFalse(unacked["exportable"])

    def test_row_from_record_hides_stale_field_missing_ack_after_record_leaves_field_missing(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "verified",
                    "logical_record_identity": "id-ready-with-stale-ack",
                    "identity_confidence": "verified",
                    "authoritative_path": "/managed/ready.html",
                    "inspection_openable_path": "/managed/ready.html",
                    "reason_code": "identity_verified_artifact_present",
                    "safe_evidence": {},
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            row = service.row_from_record(
                {
                    "record_id": "r-ready-with-stale-ack",
                    "project_code": "CODE-READY-ACK",
                    "project_name": "已恢复对象",
                    "project_type": "实物资产",
                    "business_id": "physical_asset",
                    "record_family": "listing",
                    "exchange": "shanghai",
                    "listing_date": "2026-04-21",
                    "state": "ready",
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-21T10:05:00",
                    "revision_id": 14,
                    "canonical_record": _canonical_listing_record(
                        project_code="CODE-READY-ACK",
                        project_name="已恢复对象",
                    ),
                    "acknowledged_payload_json": {
                        "field_missing": {
                            "acknowledged": True,
                            "missing_fields_hash": "hash-stale",
                            "missing_fields": [{"field": "deal_price_unit_basis"}],
                        }
                    },
                },
                values={"项目类型": "实物资产", "交易所": "上交所"},
                local_artifact_path="",
            )

        self.assertEqual(row["state"], "ready")
        self.assertFalse(row["field_missing_acknowledgement"]["acknowledged"])
        self.assertEqual(row["field_missing_acknowledgement"]["missing_fields"], [])
        self.assertFalse(row["attention"]["suppressed"])

    def test_row_from_record_exposes_evidence_verdict_and_maps_legacy_fields_from_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            managed_dir = os.path.join(tmp_dir, "archive")
            os.makedirs(managed_dir, exist_ok=True)
            source_file = os.path.join(managed_dir, "source.html")
            missing_archive = os.path.join(managed_dir, "missing-archive.html")
            managed_original = os.path.join(managed_dir, "original.html")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write("source fixture")
            with open(managed_original, "w", encoding="utf-8") as handle:
                handle.write("managed fixture")

            service = RecordsService(
                repository=_FakeRecordsRepository([]),
                db_path=os.path.join(tmp_dir, "data", "streaming.sqlite3"),
                managed_artifact_roots=(managed_dir,),
            )
            row = service.row_from_record(
                {
                    "record_id": "r-stale",
                    "project_code": "CODE-STALE",
                    "project_name": "权威路径缺失对象",
                    "project_type": "实物资产",
                    "business_id": "physical_asset",
                    "record_family": "listing",
                    "exchange": "shanghai",
                    "listing_date": "2026-04-21",
                    "state": "ready",
                    "archive_path": missing_archive,
                    "source_file": source_file,
                    "source_identity_json": {"original_source_file": managed_original},
                    "updated_at": "2026-04-21T10:00:00",
                },
                values={"项目类型": "实物资产", "交易所": "上交所"},
            )

        self.assertEqual(row["evidence_verdict"]["status"], "stale_reference")
        self.assertEqual(row["evidence_verdict"]["reason_code"], "authoritative_artifact_missing")
        self.assertEqual(row["evidence_verdict"]["inspection_openable_path"], managed_original)
        self.assertEqual(row["artifact_status"], "available")
        self.assertEqual(row["artifact_missing_reason"], "authoritative_artifact_missing")
        self.assertTrue(row["has_local_artifact"])
        self.assertEqual(row["local_artifact_name"], "original.html")

    def test_row_from_record_exposes_export_eligibility_separate_from_legacy_paths(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                side_effect=[
                    {
                        "status": "verified",
                        "logical_record_identity": "id-1",
                        "identity_confidence": "verified",
                        "authoritative_path": "/managed/verified.html",
                        "inspection_openable_path": "/managed/verified.html",
                        "reason_code": "identity_verified_artifact_present",
                        "safe_evidence": {},
                    },
                    {
                        "status": "present_unverified",
                        "logical_record_identity": "",
                        "identity_confidence": "unresolved",
                        "authoritative_path": "/legacy/source.html",
                        "inspection_openable_path": "/legacy/source.html",
                        "reason_code": "identity_unresolved",
                        "safe_evidence": {},
                    },
                    {
                        "status": "verified",
                        "logical_record_identity": "id-3",
                        "identity_confidence": "verified",
                        "authoritative_path": "/managed/field.html",
                        "inspection_openable_path": "/managed/field.html",
                        "reason_code": "identity_verified_artifact_present",
                        "safe_evidence": {},
                    },
                ],
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            ready_verified = service.row_from_record(
                {
                    "record_id": "r-ready-verified",
                    "project_code": "CODE-READY-VERIFIED",
                    "project_name": "证据已验证对象",
                    "project_type": "实物资产",
                    "business_id": "physical_asset",
                    "record_family": "listing",
                    "exchange": "shanghai",
                    "listing_date": "2026-04-21",
                    "state": "ready",
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-21T10:00:00",
                    "canonical_record": _canonical_listing_record(
                        project_code="CODE-READY-VERIFIED",
                        project_name="证据已验证对象",
                    ),
                },
                values={"项目类型": "实物资产", "交易所": "上交所"},
                local_artifact_path="",
            )
            ready_unverified_with_legacy_path = service.row_from_record(
                {
                    "record_id": "r-ready-unverified",
                    "project_code": "CODE-READY-UNVERIFIED",
                    "project_name": "证据未验证对象",
                    "project_type": "实物资产",
                    "business_id": "physical_asset",
                    "record_family": "listing",
                    "exchange": "shanghai",
                    "listing_date": "2026-04-21",
                    "state": "ready",
                    "archive_path": "/legacy/archive.html",
                    "source_file": "/legacy/source.html",
                    "updated_at": "2026-04-21T10:01:00",
                    "canonical_record": _canonical_listing_record(
                        project_code="CODE-READY-UNVERIFIED",
                        project_name="证据未验证对象",
                    ),
                },
                values={"项目类型": "实物资产", "交易所": "上交所"},
                local_artifact_path="/legacy/source.html",
            )
            field_missing_verified = service.row_from_record(
                {
                    "record_id": "r-field-missing-verified",
                    "project_code": "CODE-FIELD-MISSING-VERIFIED",
                    "project_name": "字段缺失对象",
                    "project_type": "实物资产",
                    "business_id": "physical_asset",
                    "record_family": "listing",
                    "exchange": "shanghai",
                    "listing_date": "2026-04-21",
                    "state": "field_missing",
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-21T10:02:00",
                },
                values={"项目类型": "实物资产", "交易所": "上交所"},
                local_artifact_path="",
            )

        self.assertTrue(ready_verified["canonical_ready"])
        self.assertEqual(ready_verified["evidence_status"], "verified")
        self.assertTrue(ready_verified["export_eligible"])
        self.assertTrue(ready_verified["exportable"])

        self.assertTrue(ready_unverified_with_legacy_path["canonical_ready"])
        self.assertEqual(ready_unverified_with_legacy_path["evidence_status"], "present_unverified")
        self.assertTrue(ready_unverified_with_legacy_path["has_local_artifact"])
        self.assertFalse(ready_unverified_with_legacy_path["export_eligible"])
        self.assertFalse(ready_unverified_with_legacy_path["exportable"])

        self.assertFalse(field_missing_verified["canonical_ready"])
        self.assertEqual(field_missing_verified["evidence_status"], "verified")
        self.assertFalse(field_missing_verified["export_eligible"])
        self.assertFalse(field_missing_verified["exportable"])

    def test_row_from_record_keeps_display_ready_when_export_projection_missing_required_fields(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "verified",
                    "logical_record_identity": "id-canonical-incomplete",
                    "identity_confidence": "verified",
                    "authoritative_path": "/managed/canonical-incomplete.html",
                    "inspection_openable_path": "/managed/canonical-incomplete.html",
                    "reason_code": "identity_verified_artifact_present",
                    "safe_evidence": {},
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            row = service.row_from_record(
                {
                    "record_id": "r-ready-canonical-incomplete",
                    "project_code": "CODE-CANONICAL-INCOMPLETE",
                    "project_name": "投影缺字段对象",
                    "project_type": "实物资产",
                    "business_id": "physical_asset",
                    "record_family": "listing",
                    "exchange": "shanghai",
                    "listing_date": "2026-04-21",
                    "state": "ready",
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-21T10:03:00",
                    "canonical_record": _canonical_listing_record(
                        project_code="CODE-CANONICAL-INCOMPLETE",
                        project_name="投影缺字段对象",
                        canonical_fields={
                            "price": "",
                            "seller": "",
                        },
                    ),
                },
                values={"项目类型": "实物资产", "交易所": "上交所"},
                local_artifact_path="",
            )

        self.assertTrue(row["canonical_ready"])
        self.assertEqual(row["evidence_status"], "verified")
        self.assertFalse(row["export_eligible"])
        self.assertFalse(row["exportable"])
        self.assertEqual(row["status_detail"], "导出必填字段缺失，暂不能进入导出")

    def test_row_from_record_sanitizes_status_detail_from_last_error_message(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "undeclared",
                    "logical_record_identity": "",
                    "identity_confidence": "unresolved",
                    "authoritative_path": "",
                    "inspection_openable_path": "",
                    "reason_code": "artifact_path_not_declared",
                    "safe_evidence": {},
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            row = service.row_from_record(
                {
                    "record_id": "r-parse-failed",
                    "project_code": "CODE-PARSE-FAILED",
                    "project_name": "解析失败对象",
                    "project_type": "实物资产",
                    "business_id": "physical_asset",
                    "record_family": "listing",
                    "exchange": "shanghai",
                    "listing_date": "2026-04-21",
                    "state": "parse_failed",
                    "last_error_message": "UNTRUSTED_EXTERNAL_TEXT",
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-21T10:05:00",
                },
                values={"项目类型": "实物资产", "交易所": "上交所"},
                local_artifact_path="",
            )

        self.assertEqual(row["status_detail"], "解析失败，暂不能进入录入")
        self.assertNotIn("UNTRUSTED_EXTERNAL_TEXT", row["status_detail"])

    def test_row_from_record_sanitizes_status_detail_from_finding_message(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "undeclared",
                    "logical_record_identity": "",
                    "identity_confidence": "unresolved",
                    "authoritative_path": "",
                    "inspection_openable_path": "",
                    "reason_code": "artifact_path_not_declared",
                    "safe_evidence": {},
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            row = service.row_from_record(
                {
                    "record_id": "r-field-missing",
                    "project_code": "CODE-FIELD-MISSING",
                    "project_name": "字段缺失对象",
                    "project_type": "实物资产",
                    "business_id": "physical_asset",
                    "record_family": "listing",
                    "exchange": "shanghai",
                    "listing_date": "2026-04-21",
                    "state": "field_missing",
                    "findings": [
                        {
                            "severity": "warn",
                            "type": "export_field_missing",
                            "message": "UNTRUSTED_EXTERNAL_TEXT",
                        }
                    ],
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-21T10:06:00",
                },
                values={"项目类型": "实物资产", "交易所": "上交所"},
                local_artifact_path="",
            )

        self.assertEqual(row["status_detail"], "导出必填字段缺失，暂不能进入导出")
        self.assertNotIn("UNTRUSTED_EXTERNAL_TEXT", row["status_detail"])

    def test_row_from_record_does_not_build_missing_field_ack_from_raw_finding_message(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "undeclared",
                    "logical_record_identity": "",
                    "identity_confidence": "unresolved",
                    "authoritative_path": "",
                    "inspection_openable_path": "",
                    "reason_code": "artifact_path_not_declared",
                    "safe_evidence": {},
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            row = service.row_from_record(
                {
                    "record_id": "r-field-missing-raw-message",
                    "project_code": "CODE-FIELD-MISSING-RAW",
                    "project_name": "字段缺失对象",
                    "project_type": "实物资产",
                    "business_id": "physical_asset",
                    "record_family": "listing",
                    "exchange": "shanghai",
                    "listing_date": "2026-04-21",
                    "state": "field_missing",
                    "findings": [
                        {
                            "severity": "warn",
                            "type": "export_field_missing",
                            "message": "UNTRUSTED_EXTERNAL_TEXT",
                            "evidence": {},
                        }
                    ],
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-21T10:06:00",
                },
                values={"项目类型": "实物资产", "交易所": "上交所"},
                local_artifact_path="",
            )

        row_json = json.dumps(row, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("UNTRUSTED_EXTERNAL_TEXT", row_json)
        self.assertEqual(row["field_missing_acknowledgement"]["missing_fields"], [])

    def test_row_from_record_rejects_non_list_findings_instead_of_empty_ack(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "undeclared",
                    "logical_record_identity": "",
                    "identity_confidence": "unresolved",
                    "authoritative_path": "",
                    "inspection_openable_path": "",
                    "reason_code": "artifact_path_not_declared",
                    "safe_evidence": {},
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            with self.assertRaisesRegex(ValueError, "findings must be a list"):
                service.row_from_record(
                    {
                        "record_id": "r-field-missing-bad-findings",
                        "project_code": "CODE-FIELD-MISSING-BAD-FINDINGS",
                        "project_name": "坏 findings 对象",
                        "project_type": "实物资产",
                        "business_id": "physical_asset",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "field_missing",
                        "findings": {"type": "export_field_missing"},
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:06:00",
                    },
                    values={"项目类型": "实物资产", "交易所": "上交所"},
                    local_artifact_path="",
                )

    def test_row_from_record_rejects_non_mapping_finding_evidence_instead_of_empty_ack(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "undeclared",
                    "logical_record_identity": "",
                    "identity_confidence": "unresolved",
                    "authoritative_path": "",
                    "inspection_openable_path": "",
                    "reason_code": "artifact_path_not_declared",
                    "safe_evidence": {},
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            with self.assertRaisesRegex(ValueError, r"findings\[\*\]\.evidence must be an object"):
                service.row_from_record(
                    {
                        "record_id": "r-field-missing-bad-evidence",
                        "project_code": "CODE-FIELD-MISSING-BAD-EVIDENCE",
                        "project_name": "坏 evidence 对象",
                        "project_type": "实物资产",
                        "business_id": "physical_asset",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "field_missing",
                        "findings": [
                            {
                                "severity": "warn",
                                "type": "export_field_missing",
                                "evidence": [],
                            }
                        ],
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:06:00",
                    },
                    values={"项目类型": "实物资产", "交易所": "上交所"},
                    local_artifact_path="",
                )

    def test_row_from_record_preserves_explicit_empty_ack_missing_fields(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "undeclared",
                    "logical_record_identity": "",
                    "identity_confidence": "unresolved",
                    "authoritative_path": "",
                    "inspection_openable_path": "",
                    "reason_code": "artifact_path_not_declared",
                    "safe_evidence": {},
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            row = service.row_from_record(
                {
                    "record_id": "r-field-missing-empty-ack-fields",
                    "project_code": "CODE-FIELD-MISSING-EMPTY-ACK",
                    "project_name": "空 ack missing_fields 对象",
                    "project_type": "实物资产",
                    "business_id": "physical_asset",
                    "record_family": "listing",
                    "exchange": "shanghai",
                    "listing_date": "2026-04-21",
                    "state": "field_missing",
                    "findings": [
                        {
                            "severity": "warn",
                            "type": "export_field_missing",
                            "evidence": {"missing_fields": ["project_name"]},
                        }
                    ],
                    "acknowledged_payload_json": {
                        "field_missing": {
                            "acknowledged": True,
                            "missing_fields": [],
                        }
                    },
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-21T10:06:00",
                },
                values={"项目类型": "实物资产", "交易所": "上交所"},
                local_artifact_path="",
            )

        self.assertEqual(row["field_missing_acknowledgement"]["missing_fields"], [])

    def test_row_from_record_rejects_scalar_ack_missing_fields_instead_of_empty_ack(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "undeclared",
                    "logical_record_identity": "",
                    "identity_confidence": "unresolved",
                    "authoritative_path": "",
                    "inspection_openable_path": "",
                    "reason_code": "artifact_path_not_declared",
                    "safe_evidence": {},
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            with self.assertRaisesRegex(ValueError, r"field_missing\.missing_fields must be a list"):
                service.row_from_record(
                    {
                        "record_id": "r-field-missing-scalar-ack-fields",
                        "project_code": "CODE-FIELD-MISSING-SCALAR-ACK",
                        "project_name": "坏 ack missing_fields 对象",
                        "project_type": "实物资产",
                        "business_id": "physical_asset",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "field_missing",
                        "findings": [],
                        "acknowledged_payload_json": {
                            "field_missing": {
                                "acknowledged": True,
                                "missing_fields": "project_name",
                            }
                        },
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:06:00",
                    },
                    values={"项目类型": "实物资产", "交易所": "上交所"},
                    local_artifact_path="",
                )

    def test_row_from_record_rejects_non_list_missing_fields_evidence_instead_of_empty_ack(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "undeclared",
                    "logical_record_identity": "",
                    "identity_confidence": "unresolved",
                    "authoritative_path": "",
                    "inspection_openable_path": "",
                    "reason_code": "artifact_path_not_declared",
                    "safe_evidence": {},
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            with self.assertRaisesRegex(ValueError, "evidence.missing_fields must be a list"):
                service.row_from_record(
                    {
                        "record_id": "r-field-missing-bad-missing-fields",
                        "project_code": "CODE-FIELD-MISSING-BAD-MISSING-FIELDS",
                        "project_name": "坏 missing_fields 对象",
                        "project_type": "实物资产",
                        "business_id": "physical_asset",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "field_missing",
                        "findings": [
                            {
                                "severity": "warn",
                                "type": "export_field_missing",
                                "evidence": {"missing_fields": "project_name"},
                            }
                        ],
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:06:00",
                    },
                    values={"项目类型": "实物资产", "交易所": "上交所"},
                    local_artifact_path="",
                )

    def test_row_from_record_rejects_non_mapping_canonical_fields_for_export_readiness(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "verified",
                    "logical_record_identity": "id-bad-canonical-fields",
                    "identity_confidence": "verified",
                    "authoritative_path": "/managed/bad-canonical-fields.html",
                    "inspection_openable_path": "/managed/bad-canonical-fields.html",
                    "reason_code": "identity_verified_artifact_present",
                    "safe_evidence": {},
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            with self.assertRaisesRegex(ValueError, r"canonical_record\.canonical_fields must be an object"):
                service.row_from_record(
                    {
                        "record_id": "r-ready-bad-canonical-fields",
                        "project_code": "CODE-BAD-CANONICAL-FIELDS",
                        "project_name": "坏 canonical_fields 对象",
                        "project_type": "实物资产",
                        "business_id": "physical_asset",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:06:00",
                        "canonical_record": {"canonical_fields": []},
                    },
                    values={"项目类型": "实物资产", "交易所": "上交所"},
                    local_artifact_path="",
                )

    def test_list_records_rejects_non_mapping_canonical_record_under_business_filter(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-bad-canonical-business-filter",
                        "project_code": "CODE-BAD-CANONICAL",
                        "project_name": "坏 canonical_record 过滤对象",
                        "project_type": "实物资产",
                        "business_id": "",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-04-21",
                        "state": "ready",
                        "canonical_record": [],
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:06:00",
                    }
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with self.assertRaisesRegex(ValueError, "canonical_record must be an object"):
            service.list_records(
                {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "state": "all",
                }
            )

    def test_row_from_record_allows_explicit_shared_official_page_export_evidence(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "shared_official_page",
                    "logical_record_identity": "id-shared",
                    "identity_confidence": "unresolved",
                    "authoritative_path": "",
                    "inspection_openable_path": "",
                    "reason_code": "shared_official_page_explicit",
                    "safe_evidence": {"page_kind": "shared_official_page"},
                },
            ),
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            row = service.row_from_record(
                {
                    "record_id": "r-shared-official-page",
                    "project_code": "CODE-SHARED",
                    "project_name": "共享官网页对象",
                    "project_type": "实物资产",
                    "business_id": "physical_asset",
                    "record_family": "listing",
                    "exchange": "shanghai",
                    "listing_date": "2026-04-21",
                    "state": "ready",
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-21T10:03:00",
                    "canonical_record": _canonical_listing_record(
                        project_code="CODE-SHARED",
                        project_name="共享官网页对象",
                    ),
                },
                values={"项目类型": "实物资产", "交易所": "上交所"},
                local_artifact_path="",
            )

        self.assertTrue(row["canonical_ready"])
        self.assertEqual(row["evidence_status"], "shared_official_page")
        self.assertTrue(row["export_eligible"])

    def test_row_from_record_uses_shared_export_evidence_policy(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository([]),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._build_record_evidence_verdict",
                return_value={
                    "status": "verified",
                    "logical_record_identity": "id-verified",
                    "identity_confidence": "verified",
                    "authoritative_path": "",
                    "inspection_openable_path": "",
                    "reason_code": "identity_verified_artifact_present",
                    "safe_evidence": {},
                },
            ),
            patch(
                "desktop_backend.services.records_service.export_evidence_verdict_accepted",
                return_value=False,
            ) as policy,
            patch(
                "desktop_backend.services.records_service._build_record_top_level_fields",
                return_value={"seller": "", "price": ""},
            ),
        ):
            row = service.row_from_record(
                {
                    "record_id": "r-shared-policy",
                    "project_code": "CODE-SHARED-POLICY",
                    "project_name": "共享策略对象",
                    "project_type": "实物资产",
                    "business_id": "physical_asset",
                    "record_family": "listing",
                    "exchange": "shanghai",
                    "listing_date": "2026-04-21",
                    "state": "ready",
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-21T10:04:00",
                    "canonical_record": _canonical_listing_record(
                        project_code="CODE-SHARED-POLICY",
                        project_name="共享策略对象",
                    ),
                },
                values={"项目类型": "实物资产", "交易所": "上交所"},
                local_artifact_path="",
            )

        policy.assert_called_once()
        self.assertTrue(row["canonical_ready"])
        self.assertEqual(row["evidence_status"], "verified")
        self.assertFalse(row["export_eligible"])
        self.assertFalse(row["exportable"])

    def test_list_records_uses_full_pre_disclosure_contract_columns_when_business_scope_is_known(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-pre",
                        "project_code": "G32026CQ1000019-0",
                        "project_name": "预披露项目",
                        "project_type": "预披露",
                        "business_id": "pre_disclosure",
                        "record_family": "listing",
                        "exchange": "chongqing",
                        "listing_date": "2026-02-28",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-02-28T10:00:00",
                        "canonical_record": {
                            "record_family": "listing",
                            "business_identity": {
                                "business_id": "pre_disclosure",
                                "raw_business_label": "预披露",
                            },
                            "canonical_fields": {
                                "project_code": "G32026CQ1000019-0",
                                "project_name": "预披露项目",
                                "project_type": "预披露",
                                "status": "挂牌中",
                                "exchange": "重交所",
                                "seller": "重药控股（四川）有限公司",
                                "start_date": "2026/02/28",
                                "source_type": "央企",
                            },
                            "export_extras": {
                                "预披露开始日期": "2026/02/28",
                                "预披露截止日期": "2026/03/26",
                                "所属行业": "科技推广和应用服务业",
                                "近一年净利润（万）": "39.25",
                                "总资产（万）": "4904.85",
                            },
                        },
                    }
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with patch(
            "desktop_backend.services.records_service._resolve_record_artifact_path",
            return_value="/archive/pre.html",
        ):
            payload = service.list_records(
                {
                    "record_family": "listing",
                    "business_id": "pre_disclosure",
                    "exchange": "all",
                    "state": "all",
                }
            )

        self.assertEqual(
            payload["display_columns"],
            [
                "ID",
                "类型",
                "项目编号",
                "隶属集团",
                "转让方",
                "项目名称",
                "所属行业",
                "披露开始日期",
                "披露截止日期",
                "受托机构",
                "交易所",
                "经办人",
                "近一年净利润（万）",
                "总资产（万）",
                "挂牌次数",
                "备注",
            ],
        )
        self.assertNotIn("项目类型", payload["display_columns"])
        self.assertNotIn("项目状态", payload["display_columns"])
        self.assertNotIn("挂牌价格", payload["display_columns"])
        self.assertNotIn("预披露开始日期", payload["display_columns"])
        self.assertNotIn("预披露截止日期", payload["display_columns"])
        self.assertEqual(payload["rows"][0]["display_values"]["披露开始日期"], "2026/02/28")
        self.assertEqual(payload["rows"][0]["display_values"]["披露截止日期"], "2026/03/26")
        self.assertNotIn("项目类型", payload["rows"][0]["display_values"])
        self.assertNotIn("项目状态", payload["rows"][0]["display_values"])

    def test_list_records_uses_compact_generic_columns_when_business_scope_is_all(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-equity",
                        "project_code": "G32026SH1000001",
                        "project_name": "股权转让项目",
                        "project_type": "股权转让",
                        "business_id": "equity_transfer",
                        "record_family": "listing",
                        "exchange": "shanghai",
                        "listing_date": "2026-03-01",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-03-01T10:00:00",
                        "canonical_record": {
                            "record_family": "listing",
                            "business_identity": {
                                "business_id": "equity_transfer",
                                "raw_business_label": "股权转让",
                            },
                            "canonical_fields": {
                                "project_code": "G32026SH1000001",
                                "project_name": "股权转让项目",
                                "project_type": "股权转让",
                                "status": "挂牌中",
                                "exchange": "上交所",
                                "seller": "上海样例转让方",
                                "price": "100.00",
                                "start_date": "2026/03/01",
                                "source_type": "市属",
                                "group_name": "上海样例集团",
                            },
                            "export_extras": {
                                "挂牌截止日期": "2026/03/31",
                                "挂牌次数": "2",
                                "所属行业": "制造业",
                            },
                        },
                    },
                    {
                        "record_id": "r-capital",
                        "project_code": "G62026BJ1000002",
                        "project_name": "增资扩股项目",
                        "project_type": "增资扩股",
                        "business_id": "capital_increase",
                        "record_family": "listing",
                        "exchange": "beijing",
                        "listing_date": "2026-04-02",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-02T10:00:00",
                        "canonical_record": {
                            "record_family": "listing",
                            "business_identity": {
                                "business_id": "capital_increase",
                                "raw_business_label": "增资扩股",
                            },
                            "canonical_fields": {
                                "project_code": "G62026BJ1000002",
                                "project_name": "增资扩股项目",
                                "project_type": "增资扩股",
                                "status": "披露中",
                                "exchange": "北交所",
                                "seller": "北京样例融资方",
                                "price": "3000万元",
                                "start_date": "2026/04/02",
                                "source_type": "国资",
                                "group_name": "北京样例集团",
                            },
                            "export_extras": {
                                "融资方": "北京样例融资方",
                                "融资金额": "3000万元",
                                "披露开始日期": "2026/04/02",
                                "披露截止日期": "2026/05/06",
                                "持股比例": "15%",
                            },
                        },
                    },
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with patch(
            "desktop_backend.services.records_service._resolve_record_artifact_path",
            side_effect=lambda record, **_kwargs: f"/archive/{record['record_id']}.html",
        ):
            payload = service.list_records(
                {
                    "record_family": "listing",
                    "business_id": "all",
                    "exchange": "all",
                    "state": "all",
                }
            )

        self.assertEqual(
            payload["display_columns"],
            [
                "项目编号",
                "项目名称",
                "项目类型",
                "交易所",
                "主体",
                "隶属集团",
                "开始日期",
                "截止日期",
                "金额",
                "类型",
            ],
        )
        rows_by_id = {row["record_id"]: row for row in payload["rows"]}
        self.assertEqual(rows_by_id["r-equity"]["display_values"]["主体"], "上海样例转让方")
        self.assertEqual(rows_by_id["r-equity"]["display_values"]["金额"], "100.00")
        self.assertEqual(rows_by_id["r-equity"]["display_values"]["开始日期"], "2026/03/01")
        self.assertEqual(rows_by_id["r-equity"]["display_values"]["截止日期"], "2026/03/31")
        self.assertEqual(rows_by_id["r-capital"]["display_values"]["主体"], "北京样例融资方")
        self.assertEqual(rows_by_id["r-capital"]["display_values"]["金额"], "3000万元")
        self.assertEqual(rows_by_id["r-capital"]["display_values"]["开始日期"], "2026/04/02")
        self.assertEqual(rows_by_id["r-capital"]["display_values"]["截止日期"], "2026/05/06")
        self.assertNotIn("融资方", payload["display_columns"])
        self.assertNotIn("融资金额", payload["display_columns"])
        self.assertNotIn("挂牌截止日期", payload["display_columns"])
        self.assertNotIn("披露截止日期", payload["display_columns"])

    def test_list_records_deal_explicit_scope_filters_by_family_exchange_date_and_keyword(self) -> None:
        class _ScopeAwareRepository:
            def __init__(self, records):
                self.records = list(records)
                self.last_kwargs = {}

            def iter_latest_records(self, **kwargs):
                self.last_kwargs = dict(kwargs)
                rows = list(self.records)
                record_family = str(kwargs.get("record_family") or "").strip()
                if record_family:
                    rows = [row for row in rows if str(row.get("record_family") or "").strip() == record_family]
                date_from = str(kwargs.get("date_from") or "").strip()
                if date_from:
                    rows = [row for row in rows if str(row.get("listing_date") or "") >= date_from]
                date_to = str(kwargs.get("date_to") or "").strip()
                if date_to:
                    rows = [row for row in rows if str(row.get("listing_date") or "") <= date_to]
                return rows

        repository = _ScopeAwareRepository(
            [
                {
                    "record_id": "r-deal-match",
                    "project_code": "D32026BJ000001",
                    "project_name": "北交所成交项目",
                    "project_type": "",
                    "business_id": "deal_equity_transfer",
                    "record_family": "deal",
                    "exchange": "cbex",
                    "listing_date": "2026-04-20",
                    "state": "ready",
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-20T10:00:00",
                    "canonical_record": {
                        "record_family": "deal",
                        "business_identity": {
                            "business_id": "deal_equity_transfer",
                            "raw_business_label": "股权转让成交",
                        },
                        "canonical_fields": {
                            "project_code": "D32026BJ000001",
                            "project_name": "北交所成交项目",
                            "status": "成交",
                            "exchange": "北交所",
                            "deal_date": "2026-04-20",
                            "deal_price": "8800",
                            "valuation": "9000",
                            "reserve_price": "8600",
                        },
                        "export_extras": {
                            "标的名称": "北交所成交项目",
                            "交易方式": "协议转让",
                            "成交日期": "2026-04-20",
                        },
                    },
                },
                {
                    "record_id": "r-deal-other-exchange",
                    "project_code": "D32026SH000002",
                    "project_name": "上交所成交项目",
                    "project_type": "",
                    "business_id": "deal_equity_transfer",
                    "record_family": "deal",
                    "exchange": "sse",
                    "listing_date": "2026-04-20",
                    "state": "ready",
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-20T10:01:00",
                },
                {
                    "record_id": "r-deal-other-date",
                    "project_code": "D32026BJ000003",
                    "project_name": "北交所旧成交项目",
                    "project_type": "",
                    "business_id": "deal_equity_transfer",
                    "record_family": "deal",
                    "exchange": "cbex",
                    "listing_date": "2026-04-18",
                    "state": "ready",
                    "archive_path": "",
                    "source_file": "",
                    "updated_at": "2026-04-18T10:00:00",
                },
            ]
        )
        service = RecordsService(
            repository=repository,
            db_path="/tmp/test-streaming.sqlite3",
        )

        with patch(
            "desktop_backend.services.records_service._resolve_record_artifact_path",
            return_value="",
        ):
            payload = service.list_records(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "cbex",
                    "keyword": "协议转让",
                    "date_from": "2026-04-20",
                    "date_to": "2026-04-20",
                    "state": "all",
                }
            )

        self.assertEqual(repository.last_kwargs["record_family"], "deal")
        self.assertEqual(repository.last_kwargs["date_from"], "2026-04-20")
        self.assertEqual(repository.last_kwargs["date_to"], "2026-04-20")
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual([row["record_id"] for row in payload["rows"]], ["r-deal-match"])
        self.assertEqual(payload["rows"][0]["exchange_code"], "cbex")
        self.assertEqual(payload["rows"][0]["listing_date"], "2026-04-20")

    def test_list_records_deal_all_scope_uses_deal_mixed_columns(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-deal-equity",
                        "project_code": "D32026BJ000010",
                        "project_name": "股权转让成交项目",
                        "project_type": "",
                        "business_id": "deal_equity_transfer",
                        "raw_business_label": "股权转让成交",
                        "record_family": "deal",
                        "exchange": "cbex",
                        "listing_date": "2026-04-20",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-20T10:00:00",
                        "canonical_record": {
                            "record_family": "deal",
                            "business_identity": {
                                "business_id": "deal_equity_transfer",
                                "raw_business_label": "股权转让成交",
                            },
                            "canonical_fields": {
                                "project_code": "D32026BJ000010",
                                "project_name": "股权转让成交项目",
                                "status": "成交",
                                "exchange": "北交所",
                                "deal_date": "2026-04-20",
                                "deal_price": "6800",
                                "valuation": "7000",
                                "reserve_price": "6600",
                            },
                        },
                    },
                    {
                        "record_id": "r-deal-capital",
                        "project_code": "D62026SH000011",
                        "project_name": "增资扩股成交项目",
                        "project_type": "",
                        "business_id": "deal_capital_increase",
                        "raw_business_label": "增资扩股成交",
                        "record_family": "deal",
                        "exchange": "sse",
                        "listing_date": "2026-04-21",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-21T10:00:00",
                        "canonical_record": {
                            "record_family": "deal",
                            "business_identity": {
                                "business_id": "deal_capital_increase",
                                "raw_business_label": "增资扩股成交",
                            },
                            "canonical_fields": {
                                "project_code": "D62026SH000011",
                                "project_name": "增资扩股成交项目",
                                "status": "成交",
                                "exchange": "上交所",
                                "deal_date": "2026-04-21",
                                "deal_price": "9200",
                            },
                        },
                    },
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with patch(
            "desktop_backend.services.records_service._resolve_record_artifact_path",
            side_effect=lambda record, **_kwargs: f"/archive/{record['record_id']}.html",
        ):
            payload = service.list_records(
                {
                    "record_family": "deal",
                    "business_id": "all",
                    "exchange": "all",
                    "state": "all",
                }
            )

        self.assertEqual(
            payload["display_columns"],
            [
                "项目编号",
                "项目名称",
                "业务",
                "交易所",
                "成交日期",
                "金额",
                "状态",
            ],
        )
        rows_by_id = {row["record_id"]: row for row in payload["rows"]}
        self.assertEqual(rows_by_id["r-deal-equity"]["display_values"]["业务"], "股权转让成交")
        self.assertEqual(rows_by_id["r-deal-equity"]["display_values"]["成交日期"], "2026-04-20")
        self.assertEqual(rows_by_id["r-deal-equity"]["display_values"]["金额"], "6800")
        self.assertEqual(rows_by_id["r-deal-equity"]["display_values"]["状态"], "成交")
        self.assertEqual(rows_by_id["r-deal-capital"]["display_values"]["业务"], "增资扩股成交")
        self.assertEqual(rows_by_id["r-deal-capital"]["display_values"]["成交日期"], "2026-04-21")
        self.assertEqual(rows_by_id["r-deal-capital"]["display_values"]["金额"], "9200")
        self.assertEqual(rows_by_id["r-deal-capital"]["display_values"]["状态"], "成交")
        self.assertNotIn("项目类型", payload["display_columns"])
        self.assertNotIn("主体", payload["display_columns"])
        self.assertNotIn("开始日期", payload["display_columns"])
        self.assertNotIn("截止日期", payload["display_columns"])
        self.assertNotIn("类型", payload["display_columns"])

    def test_list_records_deal_all_scope_keyword_matches_visible_business_exchange_and_status(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-deal-visible",
                        "project_code": "D32026BJ000020",
                        "project_name": "项目A",
                        "project_type": "",
                        "business_id": "deal_equity_transfer",
                        "record_family": "deal",
                        "exchange": "cbex",
                        "listing_date": "2026-04-22",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-22T10:00:00",
                        "canonical_record": {
                            "record_family": "deal",
                            "business_identity": {
                                "business_id": "deal_equity_transfer",
                                "raw_business_label": "股权转让成交",
                            },
                            "canonical_fields": {
                                "project_code": "D32026BJ000020",
                                "project_name": "项目A",
                                "status": "已成交",
                                "exchange": "北交所",
                                "deal_date": "2026-04-22",
                                "deal_price": "6800",
                            },
                        },
                    },
                    {
                        "record_id": "r-deal-other",
                        "project_code": "D32026SH000021",
                        "project_name": "项目B",
                        "project_type": "",
                        "business_id": "deal_capital_increase",
                        "record_family": "deal",
                        "exchange": "sse",
                        "listing_date": "2026-04-22",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-22T10:01:00",
                        "canonical_record": {
                            "record_family": "deal",
                            "business_identity": {
                                "business_id": "deal_capital_increase",
                                "raw_business_label": "增资扩股成交",
                            },
                            "canonical_fields": {
                                "project_code": "D32026SH000021",
                                "project_name": "项目B",
                                "status": "挂牌中",
                                "exchange": "上交所",
                                "deal_date": "2026-04-22",
                                "deal_price": "9200",
                            },
                        },
                    },
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with patch(
            "desktop_backend.services.records_service._resolve_record_artifact_path",
            return_value="",
        ):
            for keyword in ("股权转让成交", "北交所", "已成交"):
                payload = service.list_records(
                    {
                        "record_family": "deal",
                        "business_id": "all",
                        "exchange": "all",
                        "keyword": keyword,
                        "state": "all",
                    }
                )
                with self.subTest(keyword=keyword):
                    self.assertEqual(payload["total_count"], 1)
                    self.assertEqual([row["record_id"] for row in payload["rows"]], ["r-deal-visible"])

    def test_list_records_explicit_business_scope_uses_canonical_business_id_fallback(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-deal-canonical-business",
                        "project_code": "D32026BJ000030",
                        "project_name": "缺失顶层业务ID但已归一化记录",
                        "project_type": "",
                        "business_id": "",
                        "record_family": "deal",
                        "exchange": "cbex",
                        "listing_date": "2026-04-23",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-23T10:00:00",
                        "canonical_record": {
                            "record_family": "deal",
                            "business_identity": {
                                "business_id": "deal_equity_transfer",
                                "raw_business_label": "股权转让成交",
                            },
                            "canonical_fields": {
                                "project_code": "D32026BJ000030",
                                "project_name": "缺失顶层业务ID但已归一化记录",
                                "status": "成交",
                                "exchange": "北交所",
                                "deal_date": "2026-04-23",
                            },
                        },
                    },
                    {
                        "record_id": "r-deal-capital",
                        "project_code": "D32026SH000031",
                        "project_name": "增资扩股成交项目",
                        "project_type": "",
                        "business_id": "deal_capital_increase",
                        "record_family": "deal",
                        "exchange": "sse",
                        "listing_date": "2026-04-23",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-23T10:01:00",
                    },
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with patch(
            "desktop_backend.services.records_service._resolve_record_artifact_path",
            return_value="",
        ):
            payload = service.list_records(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "all",
                    "state": "all",
                }
            )

        self.assertEqual(payload["total_count"], 1)
        self.assertEqual([row["record_id"] for row in payload["rows"]], ["r-deal-canonical-business"])
        self.assertEqual(payload["rows"][0]["business_id"], "deal_equity_transfer")

    def test_list_records_explicit_business_scope_uses_canonical_source_identity_business_id_fallback(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-deal-source-identity-business",
                        "project_code": "D32026BJ000033",
                        "project_name": "来源身份已归一化记录",
                        "project_type": "",
                        "business_id": "",
                        "record_family": "deal",
                        "exchange": "cbex",
                        "listing_date": "2026-04-24",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-24T10:00:00",
                        "canonical_record": {
                            "record_family": "deal",
                            "business_identity": {},
                            "source_identity": {
                                "business_id": "deal_equity_transfer",
                                "record_family": "deal",
                            },
                            "canonical_fields": {
                                "project_code": "D32026BJ000033",
                                "project_name": "来源身份已归一化记录",
                                "status": "成交",
                                "exchange": "北交所",
                                "deal_date": "2026-04-24",
                            },
                        },
                    },
                    {
                        "record_id": "r-deal-capital",
                        "project_code": "D32026SH000034",
                        "project_name": "增资扩股成交项目",
                        "project_type": "",
                        "business_id": "deal_capital_increase",
                        "record_family": "deal",
                        "exchange": "sse",
                        "listing_date": "2026-04-24",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-24T10:01:00",
                    },
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with patch(
            "desktop_backend.services.records_service._resolve_record_artifact_path",
            return_value="",
        ):
            payload = service.list_records(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "all",
                    "state": "all",
                }
            )

        self.assertEqual(payload["total_count"], 1)
        self.assertEqual([row["record_id"] for row in payload["rows"]], ["r-deal-source-identity-business"])
        self.assertEqual(payload["rows"][0]["business_id"], "deal_equity_transfer")

    def test_list_records_explicit_business_scope_preserves_falsy_canonical_business_identity(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-deal-falsy-business-identity",
                        "project_code": "D32026BJ000035",
                        "project_name": "falsy identity 成交项目",
                        "project_type": "",
                        "business_id": "",
                        "record_family": "deal",
                        "exchange": "cbex",
                        "listing_date": "2026-04-24",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-24T10:02:00",
                        "canonical_record": {
                            "record_family": "deal",
                            "business_identity": _FalsyDict(
                                {
                                    "business_id": "deal_equity_transfer",
                                }
                            ),
                            "canonical_fields": {
                                "project_code": "D32026BJ000035",
                                "project_name": "falsy identity 成交项目",
                                "status": "成交",
                                "exchange": "北交所",
                                "deal_date": "2026-04-24",
                            },
                        },
                    }
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with patch(
            "desktop_backend.services.records_service._resolve_record_artifact_path",
            return_value="",
        ):
            payload = service.list_records(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "all",
                    "state": "all",
                }
            )

        self.assertEqual(payload["total_count"], 1)
        self.assertEqual([row["record_id"] for row in payload["rows"]], ["r-deal-falsy-business-identity"])
        self.assertEqual(payload["rows"][0]["business_id"], "deal_equity_transfer")

    def test_list_records_explicit_business_scope_rejects_malformed_canonical_business_identity(self) -> None:
        service = RecordsService(
            repository=_FakeRecordsRepository(
                [
                    {
                        "record_id": "r-deal-canonical-business",
                        "project_code": "D32026BJ000030",
                        "project_name": "缺失顶层业务ID但已归一化记录",
                        "project_type": "",
                        "business_id": "",
                        "record_family": "deal",
                        "exchange": "cbex",
                        "listing_date": "2026-04-23",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-23T10:00:00",
                        "canonical_record": {
                            "record_family": "deal",
                            "business_identity": {
                                "business_id": "deal_equity_transfer",
                                "raw_business_label": "股权转让成交",
                            },
                            "canonical_fields": {
                                "project_code": "D32026BJ000030",
                                "project_name": "缺失顶层业务ID但已归一化记录",
                                "status": "成交",
                                "exchange": "北交所",
                                "deal_date": "2026-04-23",
                            },
                        },
                    },
                    {
                        "record_id": "r-deal-malformed-canonical-business",
                        "project_code": "D32026BJ000032",
                        "project_name": "畸形业务身份记录",
                        "project_type": "",
                        "business_id": "",
                        "record_family": "deal",
                        "exchange": "cbex",
                        "listing_date": "2026-04-23",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-23T10:02:00",
                        "canonical_record": {
                            "record_family": "deal",
                            "business_identity": "oops",
                            "canonical_fields": {
                                "project_code": "D32026BJ000032",
                                "project_name": "畸形业务身份记录",
                                "status": "成交",
                                "exchange": "北交所",
                                "deal_date": "2026-04-23",
                            },
                        },
                    },
                    {
                        "record_id": "r-deal-capital",
                        "project_code": "D32026SH000031",
                        "project_name": "增资扩股成交项目",
                        "project_type": "",
                        "business_id": "deal_capital_increase",
                        "record_family": "deal",
                        "exchange": "sse",
                        "listing_date": "2026-04-23",
                        "state": "ready",
                        "archive_path": "",
                        "source_file": "",
                        "updated_at": "2026-04-23T10:01:00",
                    },
                ]
            ),
            db_path="/tmp/test-streaming.sqlite3",
        )

        with (
            patch(
                "desktop_backend.services.records_service._resolve_record_artifact_path",
                return_value="",
            ),
            self.assertRaisesRegex(ValueError, r"canonical_record\.business_identity must be an object"),
        ):
            service.list_records(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "all",
                    "state": "all",
                }
            )

    def test_list_records_keeps_rows_without_resolvable_local_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            archive_dir = os.path.join(tmp_dir, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            existing_artifact = os.path.join(archive_dir, "demo.html")
            with open(existing_artifact, "w", encoding="utf-8") as handle:
                handle.write("artifact fixture")
            service = RecordsService(
                repository=_FakeRecordsRepository(
                    [
                        {
                            "record_id": "r-missing-artifact",
                            "project_code": "CODE-MISSING",
                            "project_name": "缺失网页工件",
                            "project_type": "股权转让",
                            "business_id": "equity_transfer",
                            "record_family": "listing",
                            "exchange": "shanghai",
                            "listing_date": "2026-03-21",
                            "state": "ready",
                            "archive_path": os.path.join(archive_dir, "missing.html"),
                            "source_file": os.path.join(archive_dir, "missing.html"),
                            "updated_at": "2026-03-21T10:00:00",
                        },
                        {
                            "record_id": "r-has-artifact",
                            "project_code": "CODE-HAS",
                            "project_name": "存在网页工件",
                            "project_type": "股权转让",
                            "business_id": "equity_transfer",
                            "record_family": "listing",
                            "exchange": "shanghai",
                            "listing_date": "2026-03-21",
                            "state": "ready",
                            "archive_path": existing_artifact,
                            "source_file": existing_artifact,
                            "updated_at": "2026-03-21T10:01:00",
                        },
                    ]
                ),
                db_path=os.path.join(tmp_dir, "data", "test-streaming.sqlite3"),
            )

            with (
                patch(
                    "desktop_backend.services.records_service._build_record_display_values",
                    side_effect=lambda record, project_kind=None: {
                        "项目类型": "股权转让",
                        "交易所": "上交所",
                    },
                ),
                patch(
                    "desktop_backend.services.records_service._build_record_top_level_fields",
                    return_value={"seller": "", "price": ""},
                ),
            ):
                payload = service.list_records(
                    {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "exchange": "sse",
                        "state": "all",
                    }
                )

        self.assertEqual(payload["total_count"], 2)
        self.assertCountEqual([row["record_id"] for row in payload["rows"]], ["r-has-artifact", "r-missing-artifact"])
        rows_by_id = {row["record_id"]: row for row in payload["rows"]}
        self.assertTrue(rows_by_id["r-has-artifact"]["has_local_artifact"])
        self.assertEqual(rows_by_id["r-has-artifact"]["local_artifact_name"], "demo.html")
        self.assertFalse(rows_by_id["r-missing-artifact"]["has_local_artifact"])
        self.assertEqual(rows_by_id["r-missing-artifact"]["local_artifact_name"], "")

    def test_list_records_recovers_managed_original_source_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_home = os.path.join(temp_dir, "PEAP")
            archive_root = os.path.join(app_home, "archive")
            data_root = os.path.join(app_home, "data")
            os.makedirs(archive_root, exist_ok=True)
            os.makedirs(data_root, exist_ok=True)
            managed_file = os.path.join(archive_root, "deal.html")
            with open(managed_file, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            service = RecordsService(
                repository=_FakeRecordsRepository(
                    [
                        {
                            "record_id": "r-managed-original",
                            "project_code": "CODE-MANAGED",
                            "project_name": "存在历史归档文件",
                            "project_type": "股权转让",
                            "business_id": "equity_transfer",
                            "record_family": "listing",
                            "exchange": "shanghai",
                            "listing_date": "2026-03-21",
                            "state": "ready",
                            "archive_path": os.path.join(archive_root, "missing.html"),
                            "source_file": os.path.join(archive_root, "missing.html"),
                            "source_identity_json": {"original_source_file": managed_file},
                            "updated_at": "2026-03-21T10:00:00",
                        },
                    ]
                ),
                db_path=os.path.join(data_root, "streaming.sqlite3"),
            )

            payload = service.list_records(
                {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "all",
                    "state": "all",
                }
            )

            self.assertEqual(payload["total_count"], 1)
            row = payload["rows"][0]
            self.assertTrue(row["has_local_artifact"])
            self.assertEqual(row["local_artifact_name"], "deal.html")


if __name__ == "__main__":
    unittest.main()
