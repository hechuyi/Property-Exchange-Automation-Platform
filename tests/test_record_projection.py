from __future__ import annotations

import unittest

from peap_core import CanonicalRecord


class RecordProjectionTest(unittest.TestCase):
    def test_project_canonical_record_to_compat_payload_is_explicit_and_bounded(self) -> None:
        from peap.export_projection import project_canonical_record_to_compat_payload

        canonical = CanonicalRecord(
            record_id="rec-001",
            record_family="listing",
            source_identity={"source_id": "beijing"},
            business_identity={"project_code": "P001"},
            canonical_fields={
                "project_code": "P001",
                "project_name": "投影项目",
                "project_type": "股权转让",
                "status": "挂牌",
                "exchange": "北交所",
                "seller": "测试转让方",
                "price": "108.00",
                "start_date": "2026/03/31",
                "source_type": "",
                "group_name": "",
            },
            field_provenance={"project_name": {"snapshot_id": "snap-1"}},
            normalizer_version="record_normalizer/v1",
        )

        payload = project_canonical_record_to_compat_payload(canonical)

        self.assertEqual(payload["项目编号"], "P001")
        self.assertEqual(payload["项目名称"], "投影项目")
        self.assertEqual(payload["项目类型"], "股权转让")
        self.assertEqual(payload["项目状态"], "挂牌")
        self.assertEqual(payload["交易所"], "北交所")
        self.assertEqual(payload["转让方"], "测试转让方")
        self.assertEqual(payload["挂牌价格"], "108.00")
        self.assertEqual(payload["挂牌开始日期"], "2026/03/31")

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
        from peap.export_projection import ExportProjectionError, project_canonical_record_to_export_payload

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
        self.assertIn("project_type", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
