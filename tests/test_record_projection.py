from __future__ import annotations

import os
import tempfile
import unittest

from peap_core import CanonicalRecord


class RecordProjectionTest(unittest.TestCase):
    def test_record_status_label_maps_pending_review_to_operator_facing_text(self) -> None:
        from desktop_backend.domain.record_projection import record_status_label

        label = record_status_label(
            {
                "state": "pending_review",
                "findings": [],
            }
        )

        self.assertEqual(label, "待人工复核")

    def test_record_status_detail_uses_safe_finding_label_for_pending_review(self) -> None:
        from desktop_backend.domain.record_projection import record_status_detail

        detail = record_status_detail(
            {
                "state": "pending_review",
                "findings": [
                    {
                        "severity": "warn",
                        "type": "export_field_missing",
                        "message": "UNTRUSTED_EXTERNAL_TEXT",
                    }
                ],
            }
        )

        self.assertEqual(detail, "导出必填字段缺失，暂不能进入导出")
        self.assertNotIn("UNTRUSTED_EXTERNAL_TEXT", detail)

    def test_record_status_detail_prioritizes_safe_missing_field_label_for_field_missing_state(self) -> None:
        from desktop_backend.domain.record_projection import record_status_detail

        detail = record_status_detail(
            {
                "state": "field_missing",
                "findings": [
                    {
                        "severity": "error",
                        "type": "generic_error",
                        "message": "通用错误提示",
                    },
                    {
                        "severity": "warn",
                        "type": "business_resolution_required",
                        "message": "需要业务归属",
                    },
                    {
                        "severity": "warn",
                        "type": "export_field_missing",
                        "message": "UNTRUSTED_EXTERNAL_TEXT",
                    },
                ],
            }
        )

        self.assertEqual(detail, "导出必填字段缺失，暂不能进入导出")
        self.assertNotIn("UNTRUSTED_EXTERNAL_TEXT", detail)

    def test_record_status_detail_uses_safe_failure_label_instead_of_last_error_message(self) -> None:
        from desktop_backend.domain.record_projection import record_status_detail

        detail = record_status_detail(
            {
                "state": "parse_failed",
                "last_error_message": "UNTRUSTED_EXTERNAL_TEXT",
            }
        )

        self.assertEqual(detail, "解析失败，暂不能进入录入")
        self.assertNotIn("UNTRUSTED_EXTERNAL_TEXT", detail)

    def test_record_status_detail_field_missing_fallback_when_messages_absent(self) -> None:
        from desktop_backend.domain.record_projection import record_status_detail

        detail = record_status_detail(
            {
                "state": "field_missing",
                "findings": [
                    {"severity": "warn", "type": "export_field_missing", "message": ""},
                    {"severity": "warn", "type": "canonical_field_missing"},
                    {"severity": "error", "type": "generic_error", "message": "   "},
                ],
            }
        )

        self.assertEqual(detail, "导出必填字段缺失，暂不能进入导出")

    def test_record_status_detail_rejects_non_mapping_archive_conflict_evidence(self) -> None:
        from desktop_backend.domain.record_projection import record_status_detail

        with self.assertRaisesRegex(TypeError, r"findings\[\*\]\.evidence must be a mapping"):
            record_status_detail(
                {
                    "state": "conflict",
                    "archive_path": "/tmp/archive/current.html",
                    "findings": [
                        {
                            "type": "archive_conflict",
                            "evidence": [],
                        }
                    ],
                }
            )

    def test_record_status_detail_rejects_non_mapping_finding_items(self) -> None:
        from desktop_backend.domain.record_projection import record_status_detail

        with self.assertRaisesRegex(TypeError, r"findings\[\*\] must be a mapping"):
            record_status_detail(
                {
                    "state": "field_missing",
                    "findings": ["export_field_missing"],
                }
            )

    def test_record_status_label_rejects_non_list_findings_on_skipped_records(self) -> None:
        from desktop_backend.domain.record_projection import record_status_label

        with self.assertRaisesRegex(TypeError, "findings must be a list"):
            record_status_label(
                {
                    "state": "skipped",
                    "findings": False,
                }
            )

    def test_record_status_label_rejects_non_mapping_skipped_finding_items(self) -> None:
        from desktop_backend.domain.record_projection import record_status_label

        with self.assertRaisesRegex(TypeError, r"findings\[\*\] must be a mapping"):
            record_status_label(
                {
                    "state": "skipped",
                    "findings": ["rule_filtered"],
                }
            )

    def test_record_status_detail_keeps_archive_path_display_fallback_when_conflict_evidence_is_missing(self) -> None:
        from desktop_backend.domain.record_projection import record_status_detail

        detail = record_status_detail(
            {
                "state": "conflict",
                "archive_path": "/tmp/archive/current.html",
                "findings": [
                    {
                        "type": "archive_conflict",
                    }
                ],
            }
        )

        self.assertEqual(detail, "归档文件同名，已另存为 current.html")

    def test_build_record_top_level_fields_rejects_explicit_non_mapping_canonical_record(self) -> None:
        from desktop_backend.domain.record_projection import build_record_top_level_fields

        with self.assertRaisesRegex(TypeError, "canonical_record must be a mapping"):
            build_record_top_level_fields({"canonical_record": []})

    def test_build_record_display_values_keeps_project_type_as_display_only_when_business_id_is_absent(self) -> None:
        from desktop_backend.domain.record_projection import build_record_display_values

        values = build_record_display_values(
            {
                "project_code": "G32026SH1000002-0",
                "project_name": "回归字段展示项目",
                "project_type": "股权转让",
                "exchange": "shanghai",
            },
            project_kind=None,
        )

        self.assertEqual(
            list(values.keys())[:4],
            ["项目编号", "项目名称", "项目类型", "交易所"],
        )
        self.assertEqual(values["项目类型"], "股权转让")
        self.assertNotIn("类型", values)

    def test_build_record_display_payload_uses_canonical_export_payload_only(self) -> None:
        from desktop_backend.domain.record_projection import build_record_display_payload

        payload = build_record_display_payload(
            {
                "project_code": "G32026SH1000002-0",
                "project_name": "回归字段展示项目",
                "project_type": "股权转让",
                "parser_payload": {
                    "项目编号": "G32026SH1000002-0",
                    "隶属集团": "上海样例集团",
                    "挂牌价格": "1000.5",
                },
                "postprocess_payload": {
                    "项目编号": "G32026SH1000002-0",
                    "转让方": "上海样例转让方",
                    "挂牌次数": "2",
                    "snapshot_body_legacy": "legacy-v2",
                },
                "source_identity_json": {
                    "snapshot_id": "old-snapshot-id",
                },
            }
        )

        self.assertEqual(payload["项目编号"], "G32026SH1000002-0")
        self.assertEqual(payload["项目名称"], "回归字段展示项目")
        self.assertEqual(payload["项目类型"], "股权转让")
        self.assertNotIn("隶属集团", payload)
        self.assertNotIn("挂牌价格", payload)
        self.assertNotIn("转让方", payload)
        self.assertNotIn("挂牌次数", payload)
        self.assertNotIn("snapshot_body_legacy", payload)
        self.assertNotIn("snapshot_id", payload)

    def test_build_record_mapping_payload_rejects_explicit_non_mapping_parser_payload(self) -> None:
        from desktop_backend.domain.record_projection import build_record_mapping_payload

        with self.assertRaisesRegex(TypeError, "parser_payload must be a mapping"):
            build_record_mapping_payload(
                {
                    "project_code": "TOPLEVEL-SHOULD-NOT-BACKFILL",
                    "project_name": "顶层字段不应掩盖坏 parser payload",
                    "parser_payload": [],
                    "postprocess_payload": {"项目名称": "后处理字段"},
                }
            )

    def test_record_matches_mapping_source_rejects_explicit_non_mapping_canonical_projection(self) -> None:
        from desktop_backend.domain.record_projection import record_matches_mapping_source

        with self.assertRaisesRegex(TypeError, "canonical_projection must be a mapping"):
            record_matches_mapping_source(
                {
                    "canonical_projection": [],
                    "canonical_record": {
                        "canonical_fields": {
                            "seller": "顶层规范字段不应掩盖坏 canonical projection",
                        },
                    },
                },
                match_field="transferor",
                source_name="顶层规范字段不应掩盖坏 canonical projection",
            )

    def test_record_matches_mapping_source_uses_primary_ratio_transferor_subject(self) -> None:
        from desktop_backend.domain.record_projection import record_matches_mapping_source

        record = {
            "postprocess_payload": {
                "转让方": "烟台顺达海洋工程服务有限责任公司(99.5%) 上海诺亚船舶修理有限公司(0.5%)",
            }
        }

        self.assertTrue(
            record_matches_mapping_source(
                record,
                match_field="transferor",
                source_name="烟台顺达海洋工程服务有限责任公司",
            )
        )
        self.assertFalse(
            record_matches_mapping_source(
                record,
                match_field="transferor",
                source_name="上海诺亚船舶修理有限公司",
            )
        )

    def test_record_matches_mapping_source_keeps_group_exact_matching(self) -> None:
        from desktop_backend.domain.record_projection import record_matches_mapping_source

        record = {
            "postprocess_payload": {
                "转让方": "烟台顺达海洋工程服务有限责任公司(99.5%) 上海诺亚船舶修理有限公司(0.5%)",
                "隶属集团": "烟台顺达集团",
            }
        }

        self.assertTrue(
            record_matches_mapping_source(
                record,
                match_field="group",
                source_name="烟台顺达集团",
            )
        )
        self.assertFalse(
            record_matches_mapping_source(
                record,
                match_field="group",
                source_name="烟台顺达海洋工程服务有限责任公司",
            )
        )

    def test_build_record_display_values_uses_canonical_family_when_top_level_family_is_blank(self) -> None:
        from desktop_backend.domain.record_projection import build_record_display_values

        values = build_record_display_values(
            {
                "record_family": " ",
                "business_id": "deal_equity_transfer",
                "canonical_record": {
                    "business_identity": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "D32026SH000003",
                        "project_name": "成交展示项目",
                        "project_type": "股权转让",
                        "status": "成交",
                        "exchange": "上交所",
                        "deal_date": "2026-04-20",
                        "deal_date_basis": "deal_date",
                        "deal_price": "1080.5",
                        "deal_price_unit_basis": "raw_unit",
                        "valuation": "1288.8",
                        "reserve_price": "1000",
                    },
                },
            },
            project_kind=None,
        )

        self.assertIn("转让标的评估值", values)
        self.assertIn("成交日期", values)
        self.assertNotIn("项目类型", values)
        self.assertEqual(values["转让标的评估值"], "1288.8")

    def test_build_record_display_values_uses_source_identity_family_and_business_id(self) -> None:
        from desktop_backend.domain.record_projection import build_record_display_values

        values = build_record_display_values(
            {
                "record_family": "",
                "business_id": "",
                "canonical_record": {
                    "business_identity": {},
                    "source_identity": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "D32026SH000004",
                        "project_name": "来源身份成交展示项目",
                        "project_type": "股权转让",
                        "status": "成交",
                        "exchange": "上交所",
                        "deal_date": "2026-04-21",
                        "deal_date_basis": "deal_date",
                        "deal_price": "1180.5",
                        "deal_price_unit_basis": "raw_unit",
                        "valuation": "1388.8",
                        "reserve_price": "1100",
                    },
                },
            },
            project_kind=None,
        )

        self.assertIn("转让标的评估值", values)
        self.assertIn("成交日期", values)
        self.assertNotIn("项目类型", values)
        self.assertEqual(values["转让标的评估值"], "1388.8")

    def test_mixed_record_display_values_use_deal_columns_from_business_identity_family(self) -> None:
        from desktop_backend.domain.record_projection import build_mixed_record_display_values

        values = build_mixed_record_display_values(
            {
                "canonical_record": {
                    "business_identity": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "D32026SH000001",
                        "project_name": "成交项目",
                        "status": "成交",
                        "exchange": "上交所",
                        "deal_date": "2026-04-18",
                        "deal_price": "1080.5",
                        "deal_price_unit_basis": "raw_unit",
                    },
                },
            }
        )

        self.assertIn("业务", values)
        self.assertIn("成交日期", values)
        self.assertIn("金额", values)
        self.assertNotIn("开始日期", values)
        self.assertNotIn("截止日期", values)
        self.assertNotIn("主体", values)

    def test_mixed_record_display_values_use_deal_columns_from_source_identity_family(self) -> None:
        from desktop_backend.domain.record_projection import build_mixed_record_display_values

        values = build_mixed_record_display_values(
            {
                "canonical_record": {
                    "business_identity": {
                        "business_id": "deal_equity_transfer",
                    },
                    "source_identity": {
                        "source_id": "sse",
                        "record_family": "deal",
                    },
                    "canonical_fields": {
                        "project_code": "D32026SH000002",
                        "project_name": "来源身份成交项目",
                        "status": "成交",
                        "exchange": "上交所",
                        "deal_date": "2026-04-19",
                        "deal_price": "1180.5",
                        "deal_price_unit_basis": "raw_unit",
                    },
                },
            }
        )

        self.assertIn("业务", values)
        self.assertIn("成交日期", values)
        self.assertIn("金额", values)
        self.assertNotIn("开始日期", values)
        self.assertNotIn("截止日期", values)
        self.assertNotIn("主体", values)

    def test_mixed_record_display_values_use_source_identity_business_label(self) -> None:
        from desktop_backend.domain.record_projection import build_mixed_record_display_values

        values = build_mixed_record_display_values(
            {
                "business_id": "",
                "project_type": "兼容字段旧类型",
                "canonical_record": {
                    "business_identity": {},
                    "source_identity": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                    },
                    "canonical_fields": {
                        "project_code": "D32026SH000005",
                        "project_name": "来源身份业务成交项目",
                        "status": "成交",
                        "exchange": "上交所",
                        "deal_date": "2026-04-22",
                        "deal_price": "1280.5",
                        "deal_price_unit_basis": "raw_unit",
                    },
                },
            }
        )

        self.assertEqual(values["业务"], "股权转让成交")
        self.assertIn("成交日期", values)
        self.assertIn("金额", values)
        self.assertNotIn("开始日期", values)
        self.assertNotIn("截止日期", values)
        self.assertNotIn("主体", values)

    def test_build_record_display_values_uses_capital_contract_without_compat_field_leakage(self) -> None:
        from desktop_backend.domain.record_projection import build_record_display_values

        values = build_record_display_values(
            {
                "revision_id": "rev-capital-1",
                "project_code": "G62025BJ1000073",
                "project_name": "增资扩股项目",
                "project_type": "增资扩股",
                "exchange": "beijing",
                "canonical_record": {
                    "record_family": "listing",
                    "business_identity": {
                        "business_id": "capital_increase",
                        "raw_business_label": "增资扩股",
                    },
                    "canonical_fields": {
                        "project_code": "G62025BJ1000073",
                        "project_name": "增资扩股项目",
                        "project_type": "增资扩股",
                        "status": "挂牌中",
                        "exchange": "北交所",
                        "seller": "京东方科技集团股份有限公司",
                        "price": "不超过30000万元",
                        "start_date": "2025/11/18",
                        "end_date": "2026/03/03",
                        "source_type": "国资",
                        "group_name": "北京电子控股有限责任公司",
                    },
                    "export_extras": {
                        "融资方": "京东方科技集团股份有限公司",
                        "融资金额": "不超过30000万元",
                        "持股比例": "不超过16.7%",
                        "所属行业": "科技推广和应用服务业",
                        "披露截止日期": "2026/03/03",
                        "挂牌次数": "3",
                    },
                },
            },
            project_kind="capital_increase",
        )

        self.assertEqual(values["融资金额"], "不超过30000万元")
        self.assertEqual(values["持股比例"], "不超过16.7%")
        self.assertNotIn("融资金额（万）", values)
        self.assertNotIn("类型", values)
        self.assertNotIn("挂牌次数", values)
        self.assertNotIn("项目类型", values)
        self.assertNotIn("项目状态", values)

    def test_project_canonical_record_to_export_payload_preserves_required_fields(self) -> None:
        from peap.export_projection import project_canonical_record_to_export_payload

        canonical = CanonicalRecord(
            record_id="rec-002",
            record_family="listing",
            source_identity={"source_id": "beijing"},
            business_identity={"project_code": "P002"},
            canonical_fields={
                "project_code": "P002",
                "project_name": "规范化项目",
                "project_type": "股权转让",
                "status": "挂牌",
                "exchange": "北交所",
                "seller": "测试转让方",
                "price": "108.00",
                "start_date": "2026/03/31",
                "source_type": "国资",
                "group_name": "测试集团",
            },
            field_provenance={},
            normalizer_version="record_normalizer/v1",
        )

        payload, findings = project_canonical_record_to_export_payload(canonical, fail_on_missing=False)

        self.assertEqual(payload["项目编号"], "P002")
        self.assertEqual(payload["项目名称"], "规范化项目")
        self.assertEqual(payload["项目类型"], "股权转让")
        self.assertEqual(payload["项目状态"], "挂牌")
        self.assertEqual(payload["转让方"], "测试转让方")
        self.assertEqual(payload["挂牌价格"], "108.00")
        self.assertEqual(payload["挂牌开始日期"], "2026/03/31")
        self.assertEqual(payload["类型"], "国资")
        self.assertEqual(payload["隶属集团"], "测试集团")
        self.assertEqual(findings, ())

    def test_export_projection_requires_canonical_fields(self) -> None:
        from peap.export_projection import (
            ExportProjectionError,
            project_canonical_record_to_export_payload,
        )

        canonical = CanonicalRecord(
            record_id="rec-003",
            record_family="listing",
            source_identity={"source_id": "beijing"},
            business_identity={"project_code": "P003"},
            canonical_fields={
                "project_code": "P003",
                "project_name": "缺失字段项目",
                # Missing: project_type, status, start_date, price, seller
            },
            field_provenance={},
            normalizer_version="record_normalizer/v1",
        )

        with self.assertRaises(ExportProjectionError) as ctx:
            project_canonical_record_to_export_payload(canonical, fail_on_missing=True)

        # The exception message should indicate missing fields
        self.assertIn("status", str(ctx.exception))
        self.assertNotIn("project_type", str(ctx.exception))

    def test_export_projection_no_longer_requires_project_type_when_business_identity_is_present(self) -> None:
        from peap.export_projection import project_canonical_record_to_export_payload

        canonical = CanonicalRecord(
            record_id="rec-004",
            record_family="listing",
            source_identity={"source_id": "beijing"},
            business_identity={
                "project_code": "P004",
                "business_id": "equity_transfer",
                "raw_business_label": "股权转让",
            },
            canonical_fields={
                "project_code": "P004",
                "project_name": "无项目类型但业务身份完整",
                "status": "挂牌",
                "exchange": "北交所",
                "seller": "测试转让方",
                "price": "118.00",
                "start_date": "2026/04/01",
                "source_type": "国资",
                "group_name": "测试集团",
            },
            field_provenance={},
            normalizer_version="record_normalizer/v1",
        )

        payload, findings = project_canonical_record_to_export_payload(canonical, fail_on_missing=False)

        self.assertEqual(payload["项目编号"], "P004")
        self.assertEqual(payload["项目名称"], "无项目类型但业务身份完整")
        self.assertEqual(findings, ())

    def test_export_projection_allows_missing_group_name(self) -> None:
        from peap.export_projection import project_canonical_record_to_export_payload

        canonical = CanonicalRecord(
            record_id="rec-005",
            record_family="listing",
            source_identity={"source_id": "beijing"},
            business_identity={
                "project_code": "P005",
                "business_id": "equity_transfer",
                "raw_business_label": "股权转让",
            },
            canonical_fields={
                "project_code": "P005",
                "project_name": "缺集团但可导出项目",
                "status": "挂牌",
                "exchange": "北交所",
                "seller": "测试转让方",
                "price": "128.00",
                "start_date": "2026/04/02",
                "source_type": "国资",
            },
            field_provenance={},
            normalizer_version="record_normalizer/v1",
        )

        payload, findings = project_canonical_record_to_export_payload(canonical, fail_on_missing=False)

        self.assertEqual(payload["项目编号"], "P005")
        self.assertEqual(payload["项目名称"], "缺集团但可导出项目")
        self.assertNotIn("隶属集团", payload)
        self.assertEqual(findings, ())

    def test_export_projection_listing_physical_asset_does_not_require_status(self) -> None:
        from peap.export_projection import project_canonical_record_to_export_payload

        canonical = CanonicalRecord(
            record_id="rec-physical-001",
            record_family="listing",
            source_identity={"source_id": "cbex"},
            business_identity={
                "project_code": "W0001",
                "business_id": "physical_asset",
                "raw_business_label": "实物资产",
            },
            canonical_fields={
                "project_code": "W0001",
                "project_name": "北交所实物资产项目",
                "exchange": "北交所",
                "seller": "测试转让方",
                "price": "88.00",
                "start_date": "2026/04/02",
                "source_type": "国资",
            },
            field_provenance={},
            normalizer_version="record_normalizer/v1",
        )

        payload, findings = project_canonical_record_to_export_payload(canonical, fail_on_missing=False)

        self.assertEqual(payload["项目编号"], "W0001")
        self.assertEqual(payload["类型"], "国资")
        self.assertNotIn("项目状态", payload)
        self.assertEqual(findings, ())

    def test_export_projection_listing_physical_asset_allows_missing_transferor_and_type(self) -> None:
        from peap.export_projection import project_canonical_record_to_export_payload

        canonical = CanonicalRecord(
            record_id="rec-physical-002",
            record_family="listing",
            source_identity={"source_id": "cbex"},
            business_identity={
                "project_code": "W0002",
                "business_id": "physical_asset",
                "raw_business_label": "实物资产",
            },
            canonical_fields={
                "project_code": "W0002",
                "project_name": "北交所实物资产缺转让主体项目",
                "exchange": "北交所",
                "price": "89.00",
                "start_date": "2026/04/03",
            },
            field_provenance={},
            normalizer_version="record_normalizer/v1",
        )

        payload, findings = project_canonical_record_to_export_payload(canonical, fail_on_missing=False)

        self.assertEqual(payload["项目编号"], "W0002")
        self.assertEqual(payload["挂牌价格"], "89.00")
        self.assertNotIn("转让方", payload)
        self.assertNotIn("类型", payload)
        self.assertEqual(findings, ())

    def test_export_projection_listing_physical_asset_with_transferor_still_requires_type(self) -> None:
        from peap.export_projection import project_canonical_record_to_export_payload

        canonical = CanonicalRecord(
            record_id="rec-physical-003",
            record_family="listing",
            source_identity={"source_id": "cbex"},
            business_identity={
                "project_code": "W0003",
                "business_id": "physical_asset",
                "raw_business_label": "实物资产",
            },
            canonical_fields={
                "project_code": "W0003",
                "project_name": "北交所实物资产缺类型项目",
                "exchange": "北交所",
                "seller": "测试转让方",
                "price": "90.00",
                "start_date": "2026/04/04",
            },
            field_provenance={},
            normalizer_version="record_normalizer/v1",
        )

        _, findings = project_canonical_record_to_export_payload(canonical, fail_on_missing=False)

        self.assertEqual([finding.type for finding in findings], ["export_field_missing"])
        self.assertIn("类型", findings[0].message)
        self.assertIn("source_type", findings[0].message)

    def test_export_projection_deal_family_does_not_require_listing_only_fields(self) -> None:
        from peap.export_projection import project_canonical_record_to_export_payload

        canonical = CanonicalRecord(
            record_id="rec-deal-001",
            record_family="deal",
            source_identity={"source_id": "sse"},
            business_identity={
                "project_code": "D32026SH000001",
                "business_id": "deal_equity_transfer",
            },
            canonical_fields={
                "project_code": "D32026SH000001",
                "project_name": "成交项目",
                "status": "成交",
                "exchange": "上交所",
                "deal_date": "2026-04-18",
                "deal_date_basis": "deal_date",
                "deal_date_is_imputed": False,
                "collection_date": "2026-04-19",
                "deal_price": "1080.5",
                "deal_price_unit_basis": "raw_unit",
                "valuation": "1200",
                "reserve_price": "1000",
            },
            field_provenance={},
            normalizer_version="record_normalizer/v1",
        )

        payload, findings = project_canonical_record_to_export_payload(canonical, fail_on_missing=False)

        self.assertEqual(payload["项目编号"], "D32026SH000001")
        self.assertEqual(payload["项目名称"], "成交项目")
        self.assertEqual(payload["项目状态"], "成交")
        self.assertEqual(findings, ())

    def test_resolve_record_artifact_path_requires_existing_managed_file(self) -> None:
        from desktop_backend.domain.record_projection import resolve_record_artifact_path

        with tempfile.TemporaryDirectory() as temp_dir:
            managed_dir = os.path.join(temp_dir, "archive")
            external_dir = os.path.join(temp_dir, "external")
            os.makedirs(managed_dir, exist_ok=True)
            os.makedirs(external_dir, exist_ok=True)
            external_file = os.path.join(external_dir, "evidence.html")
            with open(external_file, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")

            resolved = resolve_record_artifact_path(
                {
                    "source_file": os.path.join(managed_dir, "missing-source.html"),
                    "archive_path": os.path.join(managed_dir, "missing-archive.html"),
                    "source_identity_json": {
                        "original_source_file": external_file,
                    },
                }
            )

        self.assertEqual(
            resolved,
            "",
            "records page must not treat parent dirs or external provenance as browsable managed artifacts",
        )

    def test_resolve_record_artifact_path_recovers_managed_original_source_file(self) -> None:
        from desktop_backend.domain.record_projection import resolve_record_artifact_path

        with tempfile.TemporaryDirectory() as temp_dir:
            managed_dir = os.path.join(temp_dir, "archive")
            os.makedirs(managed_dir, exist_ok=True)
            managed_file = os.path.join(managed_dir, "deal.html")
            with open(managed_file, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")

            resolved = resolve_record_artifact_path(
                {
                    "source_file": os.path.join(managed_dir, "missing-source.html"),
                    "archive_path": os.path.join(managed_dir, "missing-archive.html"),
                    "source_identity_json": {
                        "original_source_file": managed_file,
                    },
                },
                managed_roots=(managed_dir,),
            )

        self.assertEqual(resolved, managed_file)

    def test_resolve_record_artifact_path_does_not_mask_missing_archive_with_source_file(self) -> None:
        from desktop_backend.domain.record_projection import resolve_record_artifact_path

        with tempfile.TemporaryDirectory() as temp_dir:
            managed_dir = os.path.join(temp_dir, "archive")
            os.makedirs(managed_dir, exist_ok=True)
            source_file = os.path.join(managed_dir, "source.html")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")

            resolved = resolve_record_artifact_path(
                {
                    "source_file": source_file,
                    "archive_path": os.path.join(managed_dir, "missing-archive.html"),
                },
                managed_roots=(managed_dir,),
            )

        self.assertEqual(resolved, "")

    def test_record_artifact_missing_reason_rejects_explicit_non_mapping_source_identity(self) -> None:
        from desktop_backend.domain.record_projection import record_artifact_missing_reason

        cases = (
            (
                {
                    "source_identity_json": [],
                    "source_identity": {"original_source_file": "/tmp/should-not-mask.html"},
                },
                "source_identity_json must be a mapping",
            ),
            (
                {
                    "source_identity": [],
                },
                "source_identity must be a mapping",
            ),
        )

        for record, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(TypeError, message):
                    record_artifact_missing_reason(record, "")

    def test_record_artifact_missing_reason_allows_absent_or_null_source_identity(self) -> None:
        from desktop_backend.domain.record_projection import record_artifact_missing_reason

        for record in ({}, {"source_identity_json": None, "source_identity": None}):
            with self.subTest(record=record):
                self.assertEqual(record_artifact_missing_reason(record, ""), "artifact_path_missing")

    def test_build_record_evidence_verdict_serializes_structured_artifact_truth(self) -> None:
        from desktop_backend.domain.record_projection import build_record_evidence_verdict

        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "archive.html")
            with open(archive_path, "w", encoding="utf-8") as handle:
                handle.write("safe fixture body")

            verdict = build_record_evidence_verdict(
                {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                    "exchange": "sse",
                    "project_code": "PRJ-001",
                    "archive_path": archive_path,
                }
            )

        self.assertEqual(verdict["status"], "verified")
        self.assertEqual(verdict["reason_code"], "identity_verified_artifact_present")
        self.assertEqual(verdict["authoritative_path"], archive_path)
        self.assertEqual(verdict["inspection_openable_path"], archive_path)
        self.assertEqual(verdict["identity_confidence"], "verified")
        self.assertEqual(verdict["safe_evidence"]["path_authority"], "archive_path")
        self.assertIn("content_sha256", verdict["safe_evidence"])


if __name__ == "__main__":
    unittest.main()
