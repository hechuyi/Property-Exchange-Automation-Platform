from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppService
from desktop_backend.job_contract import build_job_view
from peap.export_projection import ExportProjectionError
from peap.migrations import MigrationRunner
from peap.streaming_models import IngestedRecord, PostProcessFinding
from peap_core.family_catalog import FamilyDescriptor


class _FalsyDict(dict):
    def __bool__(self) -> bool:
        return False


class MappingServiceHelperTest(unittest.TestCase):
    def test_business_finding_helpers_read_explicit_falsy_evidence_mapping(self) -> None:
        from desktop_backend.services import mapping_service

        evidence = _FalsyDict(
            {
                "reason_code": "project_type_mapping_template_missing",
                "raw_business_label": "股权转让",
                "business_label": "产权转让",
            }
        )
        record = {
            "findings": [
                {
                    "type": "business_resolution_required",
                    "message": "业务归属待定",
                }
            ],
        }

        with patch.object(mapping_service, "_finding_evidence", return_value=evidence):
            self.assertEqual(mapping_service._finding_reason_codes(record), {"business_resolution_required"})
            self.assertEqual(mapping_service._raw_business_label(record, {}), "股权转让")
            self.assertEqual(
                mapping_service._raw_business_label_candidates(record, {}),
                {"股权转让", "产权转让"},
            )


class FakeRuntimeDependencies:
    def get_browser_runtime_status(self, *, browser_name: str = "chromium") -> dict[str, object]:
        return {
            "browser_name": browser_name,
            "browser_cache_dir": "/tmp/browser-cache",
            "driver_executable": "/tmp/driver",
            "driver_cli": "/tmp/cli.js",
            "executable_path": "/tmp/chrome",
            "installed": True,
            "error": "",
        }

    def install_browser_runtime(self, *, browser_name: str = "chromium") -> dict[str, object]:
        return self.get_browser_runtime_status(browser_name=browser_name) | {"returncode": 0}


class MappingBacklogServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        app_home = os.path.join(self.temp_dir.name, "app_home")
        docs_home = os.path.join(self.temp_dir.name, "docs_home")
        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": app_home,
                "PEAP_DOCUMENTS_HOME": docs_home,
            },
            clear=False,
        ):
            config = AppConfig.from_env(project_root=self.temp_dir.name)
        MigrationRunner.run(config.STREAMING_DB_PATH)
        self.service = AppService(config_obj=config, runtime_dependencies=FakeRuntimeDependencies())

    def _insert_blocked_record(
        self,
        *,
        record_id: str,
        record_family: str,
        state: str = "pending_mapping",
        project_type: str = "实物资产",
        postprocess_payload: dict[str, object],
        findings: list[PostProcessFinding],
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, f"{record_id}.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>blocked</body></html>")
        self.service.store.upsert_record(
            IngestedRecord(
                record_id=record_id,
                revision_hash=f"hash-{record_id}",
                project_code=f"CODE-{record_id}",
                project_name=f"项目-{record_id}",
                project_type=project_type,
                exchange="beijing",
                listing_date="2026-03-26",
                state=state,
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": f"CODE-{record_id}", "项目名称": f"项目-{record_id}"},
                postprocess_payload=postprocess_payload,
                findings=findings,
                record_family=record_family,
            )
        )

    def _insert_mapping_refresh_record(
        self,
        *,
        record_id: str,
        transferor_name: str,
        source_type: str = "",
        group_name: str = "",
    ) -> None:
        source_file = os.path.join(self.temp_dir.name, f"{record_id}.html")
        with open(source_file, "w", encoding="utf-8") as handle:
            handle.write("<html><body>refreshable</body></html>")
        self.service.store.upsert_record(
            IngestedRecord(
                record_id=record_id,
                revision_hash=f"hash-{record_id}",
                project_code=f"CODE-{record_id}",
                project_name=f"项目-{record_id}",
                project_type="股权转让",
                exchange="beijing",
                listing_date="2026-03-26",
                state="pending_mapping",
                source_file=source_file,
                archive_path=source_file,
                parser_payload={"项目编号": f"CODE-{record_id}", "项目名称": f"项目-{record_id}", "转让方": transferor_name},
                postprocess_payload={
                    "项目编号": f"CODE-{record_id}",
                    "项目名称": f"项目-{record_id}",
                    "项目类型": "股权转让",
                    "转让方": transferor_name,
                    "隶属集团": group_name,
                    "类型": source_type,
                },
                findings=[],
                record_family="listing",
            )
        )

    def test_list_pending_mappings_splits_mapping_gap_conflict_and_hidden_family_audit_items(self) -> None:
        self._insert_blocked_record(
            record_id="rec-mapping-resolution",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-mapping-resolution",
                "项目名称": "映射缺口项目",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        self._insert_blocked_record(
            record_id="rec-mapping-conflict",
            record_family="listing",
            state="mapping_conflict",
            postprocess_payload={
                "项目编号": "CODE-rec-mapping-conflict",
                "项目名称": "映射冲突项目",
                "转让方": "测试主体",
                "隶属集团": "候选集团A",
                "类型": "央企",
            },
            findings=[],
        )
        self._insert_blocked_record(
            record_id="rec-hidden-audit",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-hidden-audit",
                "项目名称": "隐藏家族阻塞项",
                "转让方": "隐藏主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="hidden_family",
                    message="当前记录属于隐藏家族，仅保留审计视图",
                    evidence={"hidden_family": True, "family_id": "agreement"},
                )
            ],
        )

        payload = self.service.list_pending_mappings()

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["summary"]["actionable_count"], 2)
        self.assertEqual(payload["summary"]["audit_count"], 1)
        sections = {section["section_id"]: section for section in payload["sections"]}
        self.assertEqual(sections["mapping_gap_resolution"]["count"], 1)
        self.assertEqual(sections["mapping_gap_resolution"]["cta_kind"], "reprocess_pending")
        self.assertEqual(sections["mapping_gap_resolution"]["items"][0]["record_id"], "rec-mapping-resolution")
        self.assertEqual(sections["mapping_gap_resolution"]["items"][0]["blocker_kind"], "mapping_gap")
        self.assertEqual(sections["mapping_conflict_resolution"]["count"], 1)
        self.assertEqual(sections["mapping_conflict_resolution"]["cta_kind"], "read_only")
        self.assertEqual(sections["mapping_conflict_resolution"]["items"][0]["record_id"], "rec-mapping-conflict")
        self.assertEqual(sections["mapping_conflict_resolution"]["items"][0]["blocker_kind"], "mapping_conflict")
        self.assertEqual(sections["audit"]["count"], 1)
        self.assertTrue(sections["audit"]["items"][0]["audit_only"])
        self.assertEqual(sections["audit"]["items"][0]["record_id"], "rec-hidden-audit")

    def test_mapping_backlog_actionable_family_identity_comes_from_catalog(self) -> None:
        catalog_listing_id = "catalog_listing_family"
        self._insert_blocked_record(
            record_id="rec-catalog-listing-family",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-catalog-listing-family",
                "项目名称": "目录定义上市家族项目",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        with self.service.store._connect() as conn:
            conn.execute(
                "UPDATE records SET record_family = ?, business_id = '' WHERE record_id = ?",
                (catalog_listing_id, "rec-catalog-listing-family"),
            )
        calls: list[str] = []

        def fake_get_family_descriptor(family_id: str) -> FamilyDescriptor:
            calls.append(family_id)
            if family_id in {"listing", catalog_listing_id}:
                return FamilyDescriptor(
                    family_id=catalog_listing_id,
                    canonical_label="Synthetic Listing",
                    aliases=(catalog_listing_id,),
                    source_ids=(),
                    business_ids=(),
                    default_product_profile_id="desktop_listing",
                )
            raise KeyError(family_id)

        with patch(
            "desktop_backend.services.mapping_service.get_family_descriptor",
            side_effect=fake_get_family_descriptor,
            create=True,
        ):
            payload = self.service.list_pending_mappings()

        sections = {section["section_id"]: section for section in payload["sections"]}
        self.assertIn("listing", calls)
        self.assertIn(catalog_listing_id, calls)
        self.assertEqual(payload["summary"]["actionable_count"], 1)
        self.assertEqual(payload["summary"]["audit_count"], 0)
        self.assertEqual(sections["mapping_gap_resolution"]["items"][0]["record_id"], "rec-catalog-listing-family")
        self.assertFalse(sections["mapping_gap_resolution"]["items"][0]["audit_only"])

    def test_mapping_backlog_uses_canonical_business_identity_family_before_listing_fallback(self) -> None:
        record_id = "rec-canonical-business-family"
        canonical_record = {
            "business_identity": {
                "record_family": "deal",
                "business_id": "deal_equity_transfer",
            },
            "canonical_fields": {
                "project_code": f"CODE-{record_id}",
                "project_name": "成交业务身份项目",
                "project_type": "股权转让",
            },
        }
        self._insert_blocked_record(
            record_id=record_id,
            record_family="listing",
            postprocess_payload={
                "项目编号": f"CODE-{record_id}",
                "项目名称": "成交业务身份项目",
                "项目类型": "股权转让",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        with self.service.store._connect() as conn:
            conn.execute(
                "UPDATE records SET record_family = '', business_id = ? WHERE record_id = ?",
                ("deal_equity_transfer", record_id),
            )
            conn.execute(
                """
                UPDATE record_revisions
                SET canonical_record_json = ?
                WHERE revision_id = (SELECT latest_revision_id FROM records WHERE record_id = ?)
                """,
                (json.dumps(canonical_record, ensure_ascii=False), record_id),
            )

        payload = self.service.list_pending_mappings()

        sections = {section["section_id"]: section for section in payload["sections"]}
        self.assertEqual(payload["summary"]["actionable_count"], 0)
        self.assertEqual(payload["summary"]["audit_count"], 1)
        item = sections["audit"]["items"][0]
        self.assertEqual(item["record_id"], record_id)
        self.assertTrue(item["audit_only"])
        self.assertEqual(item["record_family"], "deal")
        self.assertEqual(item["business_label"], "股权转让成交")

    def test_mapping_backlog_uses_canonical_source_identity_family_when_business_identity_lacks_family(self) -> None:
        record_id = "rec-canonical-source-family"
        canonical_record = {
            "business_identity": {
                "business_id": "deal_equity_transfer",
            },
            "source_identity": {
                "record_family": "deal",
            },
            "canonical_fields": {
                "project_code": f"CODE-{record_id}",
                "project_name": "成交来源身份项目",
                "project_type": "股权转让",
            },
        }
        self._insert_blocked_record(
            record_id=record_id,
            record_family="listing",
            postprocess_payload={
                "项目编号": f"CODE-{record_id}",
                "项目名称": "成交来源身份项目",
                "项目类型": "股权转让",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        with self.service.store._connect() as conn:
            conn.execute(
                "UPDATE records SET record_family = '', business_id = ? WHERE record_id = ?",
                ("deal_equity_transfer", record_id),
            )
            conn.execute(
                """
                UPDATE record_revisions
                SET canonical_record_json = ?
                WHERE revision_id = (SELECT latest_revision_id FROM records WHERE record_id = ?)
                """,
                (json.dumps(canonical_record, ensure_ascii=False), record_id),
            )

        payload = self.service.list_pending_mappings()

        sections = {section["section_id"]: section for section in payload["sections"]}
        self.assertEqual(payload["summary"]["actionable_count"], 0)
        self.assertEqual(payload["summary"]["audit_count"], 1)
        item = sections["audit"]["items"][0]
        self.assertEqual(item["record_id"], record_id)
        self.assertTrue(item["audit_only"])
        self.assertEqual(item["record_family"], "deal")
        self.assertEqual(item["business_label"], "股权转让成交")

    def test_mapping_backlog_audits_missing_family_when_canonical_identity_has_no_family(self) -> None:
        record_id = "rec-missing-family"
        canonical_record = {
            "business_identity": "not-an-object",
            "source_identity": {},
            "canonical_fields": {
                "project_code": f"CODE-{record_id}",
                "project_name": "缺失家族项目",
                "project_type": "股权转让",
            },
        }
        self._insert_blocked_record(
            record_id=record_id,
            record_family="listing",
            postprocess_payload={
                "项目编号": f"CODE-{record_id}",
                "项目名称": "缺失家族项目",
                "项目类型": "股权转让",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        with self.service.store._connect() as conn:
            conn.execute(
                "UPDATE records SET record_family = '', business_id = '' WHERE record_id = ?",
                (record_id,),
            )
            conn.execute(
                """
                UPDATE record_revisions
                SET canonical_record_json = ?
                WHERE revision_id = (SELECT latest_revision_id FROM records WHERE record_id = ?)
                """,
                (json.dumps(canonical_record, ensure_ascii=False), record_id),
            )

        with self.assertRaisesRegex(ExportProjectionError, "business_identity must be an object"):
            self.service.list_pending_mappings()

    def test_mapping_backlog_uses_canonical_source_identity_business_id_when_top_level_blank(self) -> None:
        record_id = "rec-canonical-source-business"
        canonical_record = {
            "business_identity": {},
            "source_identity": {
                "record_family": "deal",
                "business_id": "deal_equity_transfer",
            },
            "canonical_fields": {
                "project_code": f"CODE-{record_id}",
                "project_name": "成交来源业务项目",
                "project_type": "股权转让",
            },
        }
        self._insert_blocked_record(
            record_id=record_id,
            record_family="listing",
            postprocess_payload={
                "项目编号": f"CODE-{record_id}",
                "项目名称": "成交来源业务项目",
                "项目类型": "股权转让",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        with self.service.store._connect() as conn:
            conn.execute(
                "UPDATE records SET record_family = '', business_id = '' WHERE record_id = ?",
                (record_id,),
            )
            conn.execute(
                """
                UPDATE record_revisions
                SET canonical_record_json = ?
                WHERE revision_id = (SELECT latest_revision_id FROM records WHERE record_id = ?)
                """,
                (json.dumps(canonical_record, ensure_ascii=False), record_id),
            )

        payload = self.service.list_pending_mappings()

        sections = {section["section_id"]: section for section in payload["sections"]}
        self.assertEqual(payload["summary"]["actionable_count"], 0)
        self.assertEqual(payload["summary"]["audit_count"], 1)
        item = sections["audit"]["items"][0]
        self.assertEqual(item["record_id"], record_id)
        self.assertTrue(item["audit_only"])
        self.assertEqual(item["record_family"], "deal")
        self.assertEqual(item["business_id"], "deal_equity_transfer")
        self.assertEqual(item["business_label"], "股权转让成交")

    def test_mapping_backlog_sanitizes_raw_business_label_from_json_dto(self) -> None:
        self._insert_blocked_record(
            record_id="rec-raw-business-label",
            record_family="listing",
            project_type="UNTRUSTED_EXTERNAL_TEXT",
            postprocess_payload={
                "项目编号": "CODE-rec-raw-business-label",
                "项目名称": "映射缺口污染项目",
                "项目类型": "UNTRUSTED_EXTERNAL_TEXT",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={
                        "missing_fields": ["集团"],
                        "raw_business_label": "UNTRUSTED_EXTERNAL_TEXT",
                    },
                )
            ],
        )

        payload = self.service.list_pending_mappings()

        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("UNTRUSTED_EXTERNAL_TEXT", encoded)
        sections = {section["section_id"]: section for section in payload["sections"]}
        item = sections["mapping_gap_resolution"]["items"][0]
        self.assertNotIn("raw_business_label", item)
        self.assertEqual(item["business_label"], "未识别项目类型")
        self.assertEqual(item["status_label"], "待补映射")

    def test_build_mapping_work_item_rejects_non_object_recommended_rule(self) -> None:
        self._insert_blocked_record(
            record_id="rec-bad-recommended-rule",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-bad-recommended-rule",
                "项目名称": "坏推荐规则项目",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        record = self.service.mapping_service.find_pending_mapping_records()[0]
        analysis = {
            "recommended_rule": [],
            "candidate_resolutions": [],
            "gap_codes": [],
            "available_rule_kinds": [],
        }

        with (
            patch("desktop_backend.services.mapping_service.analyze_mapping_candidates", return_value=analysis),
            self.assertRaisesRegex(ValueError, "recommended_rule"),
        ):
            self.service.mapping_service.build_mapping_work_item(record)

    def test_build_mapping_work_item_rejects_non_list_analysis_resolution_fields(self) -> None:
        self._insert_blocked_record(
            record_id="rec-bad-analysis-list",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-bad-analysis-list",
                "项目名称": "坏分析列表项目",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        record = self.service.mapping_service.find_pending_mapping_records()[0]
        base_analysis = {
            "recommended_rule": {},
            "candidate_resolutions": [],
            "gap_codes": [],
            "available_rule_kinds": [],
        }

        for field_name in ("candidate_resolutions", "gap_codes", "available_rule_kinds"):
            with self.subTest(field_name=field_name):
                analysis = dict(base_analysis)
                analysis[field_name] = {"value": "missing_type"}
                with (
                    patch("desktop_backend.services.mapping_service.analyze_mapping_candidates", return_value=analysis),
                    self.assertRaisesRegex(ValueError, field_name),
                ):
                    self.service.mapping_service.build_mapping_work_item(record)

    def test_build_mapping_work_item_rejects_missing_analysis_resolution_fields(self) -> None:
        self._insert_blocked_record(
            record_id="rec-missing-analysis-list",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-missing-analysis-list",
                "项目名称": "缺失分析列表项目",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        record = self.service.mapping_service.find_pending_mapping_records()[0]
        base_analysis = {
            "recommended_rule": {},
            "candidate_resolutions": [],
            "gap_codes": [],
            "available_rule_kinds": [],
        }

        for field_name in ("candidate_resolutions", "gap_codes", "available_rule_kinds"):
            with self.subTest(field_name=field_name):
                analysis = dict(base_analysis)
                analysis.pop(field_name)
                with (
                    patch("desktop_backend.services.mapping_service.analyze_mapping_candidates", return_value=analysis),
                    self.assertRaisesRegex(ValueError, field_name),
                ):
                    self.service.mapping_service.build_mapping_work_item(record)

    def test_build_mapping_work_item_rejects_non_string_analysis_code_items(self) -> None:
        self._insert_blocked_record(
            record_id="rec-bad-analysis-code-item",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-bad-analysis-code-item",
                "项目名称": "坏分析代码项项目",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        record = self.service.mapping_service.find_pending_mapping_records()[0]
        base_analysis = {
            "recommended_rule": {},
            "candidate_resolutions": [],
            "gap_codes": [],
            "available_rule_kinds": [],
        }

        for field_name in ("gap_codes", "available_rule_kinds"):
            with self.subTest(field_name=field_name):
                analysis = dict(base_analysis)
                analysis[field_name] = [{"value": "missing_type"}]
                with (
                    patch("desktop_backend.services.mapping_service.analyze_mapping_candidates", return_value=analysis),
                    self.assertRaisesRegex(ValueError, field_name),
                ):
                    self.service.mapping_service.build_mapping_work_item(record)

    def test_build_mapping_work_item_rejects_malformed_analysis_candidate_resolutions(self) -> None:
        self._insert_blocked_record(
            record_id="rec-bad-candidate-resolution",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-bad-candidate-resolution",
                "项目名称": "坏候选裁决项目",
                "转让方": "测试主体",
                "隶属集团": "候选集团A",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        record = self.service.mapping_service.find_pending_mapping_records()[0]
        malformed_cases = [
            (["transferor_group"], r"candidate_resolutions\[\*\]"),
            (
                [
                    {
                        "source_name": "测试主体",
                        "target_value": "候选集团",
                        "rule_kind": "transferor_group",
                    }
                ],
                r"evidence_chain",
            ),
            (
                [
                    {
                        "source_name": "测试主体",
                        "target_value": "候选集团",
                        "rule_kind": "transferor_group",
                        "evidence_chain": {"source": "catalog"},
                    }
                ],
                r"evidence_chain",
            ),
        ]

        for candidate_resolutions, error_pattern in malformed_cases:
            with self.subTest(error_pattern=error_pattern):
                analysis = {
                    "recommended_rule": {},
                    "candidate_resolutions": candidate_resolutions,
                    "gap_codes": ["has_conflict"],
                    "available_rule_kinds": ["transferor_group"],
                    "has_conflict": True,
                }
                with (
                    patch("desktop_backend.services.mapping_service.analyze_mapping_candidates", return_value=analysis),
                    self.assertRaisesRegex(ValueError, error_pattern),
                ):
                    self.service.mapping_service.build_mapping_work_item(record)

    def test_list_pending_mappings_rejects_non_list_findings_before_classification(self) -> None:
        record = {
            "record_id": "rec-bad-findings-list",
            "record_family": "listing",
            "state": "pending_mapping",
            "parser_payload": {},
            "postprocess_payload": {
                "项目编号": "CODE-rec-bad-findings-list",
                "项目名称": "坏发现列表项目",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            "findings": False,
        }

        with self.assertRaisesRegex(ValueError, "record\\.findings"):
            self.service.mapping_service.build_mapping_work_item(record)

    def test_list_pending_mappings_rejects_non_object_findings_before_classification(self) -> None:
        record = {
            "record_id": "rec-bad-finding-item",
            "record_family": "listing",
            "state": "pending_mapping",
            "parser_payload": {},
            "postprocess_payload": {
                "项目编号": "CODE-rec-bad-finding-item",
                "项目名称": "坏发现项项目",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            "findings": ["bad-finding"],
        }

        with self.assertRaisesRegex(ValueError, r"record\.findings\[\*\]"):
            self.service.mapping_service.build_mapping_work_item(record)

    def test_list_pending_mappings_rejects_non_mapping_finding_evidence_before_classification(self) -> None:
        record_id = "rec-bad-evidence-classification"
        self._insert_blocked_record(
            record_id=record_id,
            record_family="listing",
            postprocess_payload={
                "项目编号": f"CODE-{record_id}",
                "项目名称": "坏证据分类项目",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        with self.service.store._connect() as conn:
            conn.execute(
                """
                UPDATE record_revisions
                SET findings_json = ?
                WHERE revision_id = (SELECT latest_revision_id FROM records WHERE record_id = ?)
                """,
                (
                    json.dumps(
                        [
                            {
                                "severity": "warn",
                                "type": "mapping_missing",
                                "message": "bad evidence shape",
                                "evidence": False,
                            }
                        ],
                        ensure_ascii=False,
                    ),
                    record_id,
                ),
            )

        with self.assertRaisesRegex(ValueError, r"findings\[\*\]\.evidence"):
            self.service.list_pending_mappings()

    def test_build_mapping_work_item_rejects_non_mapping_business_resolution_evidence(self) -> None:
        record_id = "rec-bad-business-resolution-evidence"
        self._insert_blocked_record(
            record_id=record_id,
            record_family="deal",
            postprocess_payload={
                "项目编号": f"CODE-{record_id}",
                "项目名称": "坏业务证据项目",
                "项目类型": "未知",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="项目类型未识别，暂不能进入导出",
                    evidence={"reason_code": "unrecognized_business"},
                )
            ],
        )
        with self.service.store._connect() as conn:
            conn.execute(
                """
                UPDATE record_revisions
                SET findings_json = ?
                WHERE revision_id = (SELECT latest_revision_id FROM records WHERE record_id = ?)
                """,
                (
                    json.dumps(
                        [
                            {
                                "severity": "warn",
                                "type": "business_resolution_required",
                                "message": "bad evidence shape",
                                "evidence": [],
                            }
                        ],
                        ensure_ascii=False,
                    ),
                    record_id,
                ),
            )
        record = self.service.mapping_service.find_pending_mapping_records()[0]
        analysis = {
            "recommended_rule": {},
            "candidate_resolutions": [],
            "gap_codes": [],
            "available_rule_kinds": [],
        }

        with (
            patch("desktop_backend.services.mapping_service.analyze_mapping_candidates", return_value=analysis),
            self.assertRaisesRegex(ValueError, r"findings\[\*\]\.evidence"),
        ):
            self.service.mapping_service.build_mapping_work_item(record)

    def test_launch_pending_mapping_refresh_limits_itself_to_mapping_resolution_items(self) -> None:
        self._insert_blocked_record(
            record_id="rec-business-refresh",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-business-refresh",
                "项目名称": "业务归属待定刷新项",
                "项目类型": "未知",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="项目类型未识别，暂不能进入导出",
                    evidence={"raw_business_label": "未知", "reason_code": "unrecognized_business"},
                )
            ],
        )
        self._insert_blocked_record(
            record_id="rec-mapping-refresh",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-mapping-refresh",
                "项目名称": "映射刷新项",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        refreshed: list[str] = []

        def fake_reprocess(record_id: str, **_kwargs) -> dict[str, object]:
            refreshed.append(record_id)
            return {"record_id": record_id, "state": "ready"}

        with patch.object(self.service, "_refresh_record_postprocess", side_effect=fake_reprocess):
            payload = self.service.launch_pending_mapping_refresh({})

            deadline = time.time() + 1.0
            while time.time() < deadline and not refreshed:
                time.sleep(0.02)

        self.assertEqual(payload["job_type"], "mapping_refresh")
        self.assertEqual(payload["affected_count"], 1)
        self.assertEqual(refreshed, ["rec-mapping-refresh"])

    def test_launch_pending_mapping_refresh_writes_object_scope_for_tasks_view(self) -> None:
        self._insert_blocked_record(
            record_id="rec-mapping-refresh",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-mapping-refresh",
                "项目名称": "映射刷新项",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )

        with patch.object(self.service, "_start_background_thread", side_effect=lambda *, name, target: target()):
            payload = self.service.launch_pending_mapping_refresh({})

        job = self.service.store.get_job(payload["job_id"])
        self.assertIsInstance(job["metadata"].get("scope"), dict)
        build_job_view(job, progress=self.service.build_job_progress(job))

    def test_failed_background_start_cannot_release_newer_unbound_mapping_lease(self) -> None:
        self._insert_blocked_record(
            record_id="rec-mapping-refresh-start-failure",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-mapping-refresh-start-failure",
                "项目名称": "映射启动失败项",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )
        replacement_reserved = threading.Event()
        release_replacement = threading.Event()
        replacement_thread: threading.Thread | None = None

        def reserve_replacement_lease() -> None:
            self.service._reserve_mutating_job("mapping_refresh")
            replacement_reserved.set()
            release_replacement.wait(timeout=5)
            self.service._release_mutating_job("mapping_refresh")

        def start_then_fail(*, name: str, target) -> None:
            nonlocal replacement_thread
            try:
                target()
            except RuntimeError:
                replacement_thread = threading.Thread(target=reserve_replacement_lease, daemon=True)
                replacement_thread.start()
                self.assertTrue(replacement_reserved.wait(timeout=5))
                raise

        try:
            with (
                patch.object(self.service, "_run_mapping_refresh_job", side_effect=RuntimeError("worker failed")),
                patch.object(self.service, "_start_background_thread", side_effect=start_then_fail),
                self.assertRaisesRegex(RuntimeError, "worker failed"),
            ):
                self.service.launch_pending_mapping_refresh(
                    {"record_ids": ["rec-mapping-refresh-start-failure"]}
                )

            self.assertIn("mapping_refresh", self.service.execution_service._active_mutating_jobs)
            self.assertEqual(
                self.service.execution_service._mutating_job_leases["mapping_refresh"]["job_id"],
                "",
            )
        finally:
            release_replacement.set()
            if replacement_thread is not None:
                replacement_thread.join(timeout=5)

    def test_launch_pending_mapping_refresh_rejects_non_list_record_ids_before_launch(self) -> None:
        self._insert_blocked_record(
            record_id="rec-mapping-refresh",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-mapping-refresh",
                "项目名称": "映射刷新项",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )

        with (
            patch.object(self.service, "_start_background_thread", side_effect=AssertionError("unexpected job launch")),
            self.assertRaisesRegex(ValueError, "record_ids"),
        ):
            self.service.launch_pending_mapping_refresh({"record_ids": {"rec-mapping-refresh": True}})

    def test_launch_pending_mapping_refresh_rejects_bare_string_record_ids_before_launch(self) -> None:
        self._insert_blocked_record(
            record_id="rec-mapping-refresh",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-mapping-refresh",
                "项目名称": "映射刷新项",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )

        with (
            patch.object(self.service, "_start_background_thread", side_effect=AssertionError("unexpected job launch")),
            self.assertRaisesRegex(ValueError, "record_ids"),
        ):
            self.service.launch_pending_mapping_refresh({"record_ids": "rec-mapping-refresh"})

    def test_launch_pending_mapping_refresh_rejects_explicit_selection_with_no_actionable_records(self) -> None:
        self._insert_blocked_record(
            record_id="rec-business-refresh",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-business-refresh",
                "项目名称": "业务归属待定刷新项",
                "项目类型": "未知",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="项目类型未识别，暂不能进入导出",
                    evidence={"raw_business_label": "未知", "reason_code": "unrecognized_business"},
                )
            ],
        )

        with (
            patch.object(self.service, "_start_background_thread", side_effect=AssertionError("unexpected job launch")),
            self.assertRaisesRegex(ValueError, "no actionable mapping refresh records"),
        ):
            self.service.launch_pending_mapping_refresh({"record_ids": ["rec-business-refresh"]})

    def test_launch_business_re_evaluation_exists_as_distinct_job_surface(self) -> None:
        self._insert_blocked_record(
            record_id="rec-business-re-eval",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-business-re-eval",
                "项目名称": "业务重评估项",
                "项目类型": "未知",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="项目类型未识别，暂不能进入导出",
                    evidence={"raw_business_label": "未知", "reason_code": "unrecognized_business"},
                )
            ],
        )

        self.assertTrue(hasattr(self.service, "launch_business_re_evaluation"))
        with (
            patch.object(
                self.service,
                "_refresh_record_postprocess",
                side_effect=lambda record_id, **_kwargs: {"record_id": record_id, "state": "ready"},
            ),
            patch.object(
                self.service,
                "_start_background_thread",
                side_effect=lambda *, name, target: target(),
            ),
        ):
            payload = self.service.launch_business_re_evaluation({"record_ids": ["rec-business-re-eval"]})
        self.assertEqual(payload["job_type"], "business_re_evaluation")
        self.assertEqual(payload["affected_count"], 1)

    def test_launch_business_re_evaluation_writes_object_scope_for_tasks_view(self) -> None:
        self._insert_blocked_record(
            record_id="rec-business-re-eval",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-business-re-eval",
                "项目名称": "业务重评估项",
                "项目类型": "未知",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="项目类型未识别，暂不能进入导出",
                    evidence={"raw_business_label": "未知", "reason_code": "unrecognized_business"},
                )
            ],
        )

        with patch.object(self.service, "_start_background_thread", side_effect=lambda *, name, target: target()):
            payload = self.service.launch_business_re_evaluation({})

        job = self.service.store.get_job(payload["job_id"])
        self.assertIsInstance(job["metadata"].get("scope"), dict)
        build_job_view(job, progress=self.service.build_job_progress(job))

    def test_launch_business_re_evaluation_rejects_non_list_record_ids_before_launch(self) -> None:
        self._insert_blocked_record(
            record_id="rec-business-re-eval",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-business-re-eval",
                "项目名称": "业务重评估项",
                "项目类型": "未知",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="项目类型未识别，暂不能进入导出",
                    evidence={"raw_business_label": "未知", "reason_code": "unrecognized_business"},
                )
            ],
        )

        with (
            patch.object(self.service, "_start_background_thread", side_effect=AssertionError("unexpected job launch")),
            self.assertRaisesRegex(ValueError, "record_ids"),
        ):
            self.service.launch_business_re_evaluation({"record_ids": {"rec-business-re-eval": True}})

    def test_launch_business_re_evaluation_rejects_bare_string_record_ids_before_launch(self) -> None:
        self._insert_blocked_record(
            record_id="rec-business-re-eval",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-business-re-eval",
                "项目名称": "业务重评估项",
                "项目类型": "未知",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="项目类型未识别，暂不能进入导出",
                    evidence={"raw_business_label": "未知", "reason_code": "unrecognized_business"},
                )
            ],
        )

        with (
            patch.object(self.service, "_start_background_thread", side_effect=AssertionError("unexpected job launch")),
            self.assertRaisesRegex(ValueError, "record_ids"),
        ):
            self.service.launch_business_re_evaluation({"record_ids": "rec-business-re-eval"})

    def test_launch_business_re_evaluation_rejects_explicit_selection_with_no_eligible_records(self) -> None:
        self._insert_blocked_record(
            record_id="rec-mapping-refresh",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-mapping-refresh",
                "项目名称": "映射刷新项",
                "转让方": "测试主体",
                "隶属集团": "",
                "类型": "央企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="mapping_missing",
                    message="缺少集团，暂不能进入导出",
                    evidence={"missing_fields": ["集团"]},
                )
            ],
        )

        with (
            patch.object(self.service, "_start_background_thread", side_effect=AssertionError("unexpected job launch")),
            self.assertRaisesRegex(ValueError, "no eligible business re-evaluation records"),
        ):
            self.service.launch_business_re_evaluation({"record_ids": ["rec-mapping-refresh"]})

    def test_mapping_service_rejects_non_list_business_re_evaluation_record_ids(self) -> None:
        self._insert_blocked_record(
            record_id="rec-business-re-eval",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-business-re-eval",
                "项目名称": "业务重评估项",
                "项目类型": "未知",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="项目类型未识别，暂不能进入导出",
                    evidence={"raw_business_label": "未知", "reason_code": "unrecognized_business"},
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "record_ids"):
            self.service.mapping_service.select_business_re_evaluation_items(
                {"record_ids": {"rec-business-re-eval": True}}
            )

    def test_launch_mapping_refresh_job_for_records_rejects_invalid_record_ids_instead_of_dropping_them(self) -> None:
        with (
            patch.object(self.service.pipeline_repository, "create_mapping_refresh_job", side_effect=AssertionError("unexpected job create")),
            self.assertRaisesRegex(ValueError, "record_ids"),
        ):
            self.service._launch_mapping_refresh_job_for_records(
                record_ids=["rec-valid", "", None],
                metadata={"entry_id": "entry-1"},
            )

        with (
            patch.object(self.service.pipeline_repository, "create_mapping_refresh_job", side_effect=AssertionError("unexpected job create")),
            self.assertRaisesRegex(ValueError, "record_ids"),
        ):
            self.service._launch_mapping_refresh_job_for_records(
                record_ids={"rec-valid": True},
                metadata={"entry_id": "entry-1"},
            )

    def test_launch_mapping_refresh_job_for_records_rejects_non_mapping_metadata_before_creating_job(self) -> None:
        for bad_metadata in (False, []):
            with (
                self.subTest(metadata=bad_metadata),
                patch.object(
                    self.service.pipeline_repository,
                    "create_mapping_refresh_job",
                    side_effect=AssertionError("unexpected job create"),
                ),
                self.assertRaisesRegex(TypeError, "metadata"),
            ):
                self.service._launch_mapping_refresh_job_for_records(
                    record_ids=["rec-valid"],
                    metadata=bad_metadata,
                )

    def test_find_records_for_mapping_refresh_specs_rejects_blank_match_field_or_source_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "match_field"):
            self.service.mapping_service.find_records_for_mapping_refresh_specs(
                [{"match_field": "", "source_name": "测试主体"}]
            )

        with self.assertRaisesRegex(ValueError, "source_name"):
            self.service.mapping_service.find_records_for_mapping_refresh_specs(
                [{"match_field": "transferor", "source_name": "  "}]
            )

    def test_find_records_for_mapping_refresh_specs_rejects_records_without_record_id(self) -> None:
        with (
            patch.object(
                self.service.mapping_service,
                "find_records_for_mapping_refresh",
                return_value=[{"record_id": ""}],
            ),
            self.assertRaisesRegex(ValueError, "record_id"),
        ):
            self.service.mapping_service.find_records_for_mapping_refresh_specs(
                [{"match_field": "transferor", "source_name": "测试主体"}]
            )

    def test_enrich_mapping_entry_rejects_false_metadata_instead_of_treating_it_empty(self) -> None:
        with self.assertRaisesRegex(ValueError, "entry\\.metadata"):
            self.service.mapping_service.enrich_mapping_entry(
                {
                    "entry_id": "entry-bad-metadata",
                    "source_name": "测试主体",
                    "group_name": "测试集团",
                    "metadata": False,
                }
            )

    def test_find_existing_mapping_entry_rejects_list_metadata_instead_of_treating_it_empty(self) -> None:
        with (
            patch.object(
                self.service.pipeline_repository,
                "list_mapping_entries",
                return_value=[
                    {
                        "entry_id": "entry-bad-metadata",
                        "company_name": "测试主体",
                        "group_name": "测试集团",
                        "metadata": [],
                    }
                ],
            ),
            self.assertRaisesRegex(ValueError, "entry\\.metadata"),
        ):
            self.service.mapping_service.find_existing_mapping_entry(
                source_name="测试主体",
                match_field="transferor",
                target_field="group_name",
            )

    def test_business_re_evaluation_selects_pending_review_even_when_mapping_projection_conflicts(self) -> None:
        self._insert_blocked_record(
            record_id="rec-business-review-conflict-projection",
            record_family="listing",
            state="pending_review",
            postprocess_payload={
                "项目编号": "CODE-rec-business-review-conflict-projection",
                "项目名称": "业务重评估映射投影冲突项",
                "项目类型": "未知",
                "转让方": "冲突主体",
                "隶属集团": "现有集团",
                "类型": "地方国企",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="项目类型未识别，暂不能进入导出",
                    evidence={"raw_business_label": "未知", "reason_code": "unrecognized_business"},
                )
            ],
        )
        self.service.pipeline_repository.save_mapping_rule(
            source_name="冲突主体",
            group_name="映射集团",
            source_type="",
            rule_kind="transferor_group",
            match_field="transferor",
            target_field="group_name",
            metadata={"match_field": "transferor", "target_field": "group_name"},
        )
        refreshed: list[str] = []

        with (
            patch.object(
                self.service,
                "_refresh_record_postprocess",
                side_effect=lambda record_id, **_kwargs: refreshed.append(record_id) or {"record_id": record_id, "state": "ready"},
            ),
            patch.object(
                self.service,
                "_start_background_thread",
                side_effect=lambda *, name, target: target(),
            ),
        ):
            payload = self.service.launch_business_re_evaluation({"record_ids": ["rec-business-review-conflict-projection"]})

        self.assertEqual(payload["job_type"], "business_re_evaluation")
        self.assertEqual(payload["affected_count"], 1)
        self.assertEqual(refreshed, ["rec-business-review-conflict-projection"])

    def test_business_re_evaluation_summary_keeps_mapping_states_separate_from_review(self) -> None:
        self._insert_blocked_record(
            record_id="rec-business-review-to-mapping-conflict",
            record_family="listing",
            state="pending_review",
            postprocess_payload={
                "项目编号": "CODE-rec-business-review-to-mapping-conflict",
                "项目名称": "业务重评估后映射冲突项",
                "项目类型": "未知",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="项目类型未识别，暂不能进入导出",
                    evidence={"raw_business_label": "未知", "reason_code": "unrecognized_business"},
                )
            ],
        )

        with (
            patch.object(
                self.service,
                "_refresh_record_postprocess",
                side_effect=lambda record_id, **_kwargs: {"record_id": record_id, "state": "mapping_conflict"},
            ),
            patch.object(
                self.service,
                "_start_background_thread",
                side_effect=lambda *, name, target: target(),
            ),
        ):
            payload = self.service.launch_business_re_evaluation({"record_ids": ["rec-business-review-to-mapping-conflict"]})

        job = self.service.store.get_job(payload["job_id"])
        self.assertEqual(job["status"], "success_with_warnings")
        self.assertEqual(job["summary"]["pending_review_count"], 0)
        self.assertEqual(job["summary"]["pending_mapping_count"], 0)
        self.assertEqual(job["summary"]["mapping_conflict_count"], 1)
        self.assertEqual(job["summary"]["failed_count"], 0)

    def test_postprocess_refresh_audit_records_caller_and_job_identity(self) -> None:
        self._insert_blocked_record(
            record_id="rec-audit-caller",
            record_family="listing",
            state="pending_review",
            postprocess_payload={
                "项目编号": "CODE-rec-audit-caller",
                "项目名称": "审计来源项目",
                "项目类型": "未知",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="项目类型未识别，暂不能进入导出",
                    evidence={"raw_business_label": "未知", "reason_code": "unrecognized_business"},
                )
            ],
        )

        class FakeRunner:
            def refresh_postprocess(self, record_id: str) -> dict[str, object]:
                return {"record_id": record_id, "state": "ready"}

        with patch.object(self.service, "_build_ingest_runner", return_value=FakeRunner()):
            self.service._refresh_record_postprocess(
                "rec-audit-caller",
                caller="business_re_evaluation",
                job_id="job-business-audit",
            )

        with self.service.store._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM audit_log
                WHERE action = 'record_postprocess_refreshed'
                ORDER BY audit_id DESC
                LIMIT 1
                """
            ).fetchone()
        self.assertIsNotNone(row)
        payload = json.loads(row["payload_json"])
        self.assertEqual(payload["record_id"], "rec-audit-caller")
        self.assertEqual(payload["caller"], "business_re_evaluation")
        self.assertEqual(payload["job_id"], "job-business-audit")

    def test_update_mapping_rekeys_saved_rule_and_refreshes_old_and_new_matches(self) -> None:
        self._insert_mapping_refresh_record(record_id="rec-old-source", transferor_name="旧主体")
        self._insert_mapping_refresh_record(record_id="rec-new-source", transferor_name="新主体")
        original_entry_id = self.service.pipeline_repository.save_mapping_rule(
            source_name="旧主体",
            group_name="旧集团",
            source_type="",
            rule_kind="transferor_group",
            match_field="transferor",
            target_field="group_name",
            metadata={"match_field": "transferor", "target_field": "group_name", "notes": "before"},
        )
        refreshed: list[str] = []

        with (
            patch.object(
                self.service,
                "_start_background_thread",
                side_effect=lambda *, name, target: target(),
            ),
            patch.object(
                self.service,
                "_refresh_record_postprocess",
                side_effect=lambda record_id, **_kwargs: refreshed.append(record_id) or {"record_id": record_id, "state": "ready"},
            ),
        ):
            payload = self.service.update_mapping(
                original_entry_id,
                {
                    "rule_kind": "transferor_group",
                    "source_name": "新主体",
                    "target_value": "新集团",
                    "notes": "after",
                },
            )

        self.assertEqual(payload["job_type"], "mapping_refresh")
        self.assertEqual(payload["affected_count"], 2)
        self.assertCountEqual(refreshed, ["rec-old-source", "rec-new-source"])
        entries = self.service.list_mapping_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source_name"], "新主体")
        self.assertEqual(entries[0]["target_value"], "新集团")
        self.assertEqual(entries[0]["notes"], "after")
        self.assertNotEqual(entries[0]["entry_id"], original_entry_id)

    def test_update_mapping_rejects_non_string_entry_id_before_reserving_refresh_job(self) -> None:
        with (
            patch.object(self.service, "_reserve_mutating_job") as reserve_mutating_job,
            self.assertRaisesRegex(ValueError, "entry_id"),
        ):
            self.service.update_mapping(False, {"source_name": "主体", "target_value": "集团"})  # type: ignore[arg-type]

        reserve_mutating_job.assert_not_called()

    def test_update_mapping_rejects_false_payload_before_reserving_refresh_job(self) -> None:
        with (
            patch.object(self.service, "_reserve_mutating_job") as reserve_mutating_job,
            self.assertRaisesRegex(ValueError, "payload"),
        ):
            self.service.update_mapping("entry-1", False)  # type: ignore[arg-type]

        reserve_mutating_job.assert_not_called()

    def test_update_mapping_rejects_string_false_confirm_overwrite_instead_of_treating_it_truthy(self) -> None:
        original_entry_id = self.service.pipeline_repository.save_mapping_rule(
            source_name="原主体",
            group_name="原集团",
            source_type="",
            rule_kind="transferor_group",
            match_field="transferor",
            target_field="group_name",
            metadata={"match_field": "transferor", "target_field": "group_name"},
        )
        self.service.pipeline_repository.save_mapping_rule(
            source_name="冲突主体",
            group_name="已有集团",
            source_type="",
            rule_kind="transferor_group",
            match_field="transferor",
            target_field="group_name",
            metadata={"match_field": "transferor", "target_field": "group_name"},
        )

        with self.assertRaisesRegex(ValueError, "overwrite requires confirmation"):
            self.service.update_mapping(
                original_entry_id,
                {
                    "rule_kind": "transferor_group",
                    "source_name": "冲突主体",
                    "target_value": "新集团",
                    "confirm_overwrite": "false",
                },
            )

    def test_resolve_mapping_conflict_treats_decision_as_overwrite_confirmation(self) -> None:
        captured_payload: dict[str, object] = {}

        def fake_upsert(payload: dict[str, object]) -> dict[str, object]:
            captured_payload.update(payload)
            return {"entry_id": "entry-1"}

        self.service.mapping_service.resolve_mapping_conflict(
            {
                "record_id": "rec-conflict",
                "selected_resolution": {
                    "source_name": "冲突主体",
                    "target_value": "冲突集团",
                    "rule_kind": "transferor_group",
                },
                "confirm_overwrite": "false",
            },
            upsert_mapping=fake_upsert,
        )

        self.assertIs(captured_payload["confirm_overwrite"], True)

    def test_resolve_mapping_conflict_rejects_non_string_fields_before_upsert(self) -> None:
        captured_payloads: list[dict[str, object]] = []

        def fake_upsert(payload: dict[str, object]) -> dict[str, object]:
            captured_payloads.append(dict(payload))
            return {"entry_id": "entry-1"}

        base_payload = {
            "record_id": "rec-conflict",
            "selected_resolution": {
                "source_name": "冲突主体",
                "target_value": "冲突集团",
                "rule_kind": "transferor_group",
                "match_field": "transferor",
                "target_field": "group_name",
            },
        }

        with self.assertRaisesRegex(ValueError, "record_id"):
            self.service.mapping_service.resolve_mapping_conflict(
                {**base_payload, "record_id": {"id": "rec-conflict"}},
                upsert_mapping=fake_upsert,
            )

        for field_name in ("source_name", "target_value", "rule_kind", "match_field", "target_field", "notes"):
            with self.subTest(field_name=field_name):
                payload = {
                    **base_payload,
                    "selected_resolution": {
                        **base_payload["selected_resolution"],
                        field_name: {"value": "x"},
                    },
                }
                with self.assertRaisesRegex(ValueError, field_name):
                    self.service.mapping_service.resolve_mapping_conflict(payload, upsert_mapping=fake_upsert)

        self.assertEqual(captured_payloads, [])

    def test_resolve_mapping_conflict_rejects_non_object_selected_resolution_before_upsert(self) -> None:
        captured_payloads: list[dict[str, object]] = []

        def fake_upsert(payload: dict[str, object]) -> dict[str, object]:
            captured_payloads.append(dict(payload))
            return {"entry_id": "entry-1"}

        with self.assertRaisesRegex(ValueError, "selected_resolution must be an object"):
            self.service.mapping_service.resolve_mapping_conflict(
                {
                    "record_id": "rec-conflict",
                    "selected_resolution": [],
                },
                upsert_mapping=fake_upsert,
            )

        self.assertEqual(captured_payloads, [])

    def test_resolve_mapping_conflict_preserves_explicit_falsy_selected_resolution_mapping(self) -> None:
        captured_payloads: list[dict[str, object]] = []

        def fake_upsert(payload: dict[str, object]) -> dict[str, object]:
            captured_payloads.append(dict(payload))
            return {"entry_id": "entry-falsy-resolution"}

        response = self.service.mapping_service.resolve_mapping_conflict(
            {
                "record_id": "rec-conflict",
                "selected_resolution": _FalsyDict(
                    {
                        "source_name": "冲突主体",
                        "target_value": "冲突集团",
                        "rule_kind": "transferor_group",
                        "match_field": "transferor",
                        "target_field": "group_name",
                        "field": "transferor",
                    }
                ),
            },
            upsert_mapping=fake_upsert,
        )

        self.assertEqual(captured_payloads[0]["source_name"], "冲突主体")
        self.assertEqual(captured_payloads[0]["target_value"], "冲突集团")
        self.assertEqual(response["resolution"]["field"], "transferor")

    def test_delete_mapping_removes_saved_rule_and_refreshes_affected_records(self) -> None:
        self._insert_mapping_refresh_record(record_id="rec-delete-source", transferor_name="待删除主体")
        entry_id = self.service.pipeline_repository.save_mapping_rule(
            source_name="待删除主体",
            group_name="待删除集团",
            source_type="",
            rule_kind="transferor_group",
            match_field="transferor",
            target_field="group_name",
            metadata={"match_field": "transferor", "target_field": "group_name", "notes": "cleanup"},
        )
        refreshed: list[str] = []

        with (
            patch.object(
                self.service,
                "_start_background_thread",
                side_effect=lambda *, name, target: target(),
            ),
            patch.object(
                self.service,
                "_refresh_record_postprocess",
                side_effect=lambda record_id, **_kwargs: refreshed.append(record_id) or {"record_id": record_id, "state": "ready"},
            ),
        ):
            payload = self.service.delete_mapping(entry_id)

        self.assertTrue(payload["deleted"])
        self.assertEqual(payload["entry_id"], entry_id)
        self.assertEqual(payload["job_type"], "mapping_refresh")
        self.assertEqual(payload["affected_count"], 1)
        self.assertEqual(refreshed, ["rec-delete-source"])
        self.assertEqual(self.service.list_mapping_entries(), [])

    def test_delete_mapping_rejects_non_string_entry_id_before_reserving_refresh_job(self) -> None:
        with (
            patch.object(self.service, "_reserve_mutating_job") as reserve_mutating_job,
            self.assertRaisesRegex(ValueError, "entry_id"),
        ):
            self.service.delete_mapping(False)  # type: ignore[arg-type]

        reserve_mutating_job.assert_not_called()

    def test_business_resolution_template_gap_stays_on_business_resolution_vocabulary(self) -> None:
        self._insert_blocked_record(
            record_id="rec-template-gap",
            record_family="listing",
            postprocess_payload={
                "项目编号": "CODE-rec-template-gap",
                "项目名称": "模板缺失业务项",
                "项目类型": "股权转让",
            },
            findings=[
                PostProcessFinding(
                    severity="warn",
                    type="business_resolution_required",
                    message="entity_type_mapping_file not found: /tmp/missing.xlsx",
                    evidence={"raw_business_label": "股权转让"},
                )
            ],
        )

        with patch.object(
            self.service,
            "_start_background_thread",
            side_effect=lambda *, name, target: target(),
        ):
            job_payload = self.service.launch_business_re_evaluation({"record_ids": ["rec-template-gap"]})
        payload = self.service.list_pending_mappings()
        sections = {section["section_id"]: section for section in payload["sections"]}
        self.assertEqual(job_payload["job_type"], "business_re_evaluation")
        self.assertNotIn("business_resolution", sections)
