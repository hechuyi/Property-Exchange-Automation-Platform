from __future__ import annotations

import unittest

from peap_core import CanonicalRecord


class RecordProjectionTest(unittest.TestCase):
    def test_project_canonical_record_to_compat_payload_is_explicit_and_bounded(self) -> None:
        from peap.record_projection import project_canonical_record_to_compat_payload

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
                "mystery": "should-not-leak",
            },
            field_provenance={"project_name": {"snapshot_id": "snap-1"}},
            normalizer_version="record_normalizer/v1",
        )

        payload = project_canonical_record_to_compat_payload(canonical)

        self.assertEqual(payload["项目编号"], "P001")
        self.assertEqual(payload["项目名称"], "投影项目")
        self.assertEqual(payload["转让方"], "测试转让方")
        self.assertEqual(payload["挂牌价格"], "108.00")
        self.assertNotIn("mystery", payload)


if __name__ == "__main__":
    unittest.main()
