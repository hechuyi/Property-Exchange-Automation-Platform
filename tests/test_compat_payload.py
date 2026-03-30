from __future__ import annotations

import unittest

from peap.compat_payload import COMPAT_PAYLOAD_KEYS, build_compat_payload
from peap.standard_model import build_standard_project


class CompatPayloadTest(unittest.TestCase):
    def test_build_compat_payload_keeps_bounded_standard_projection(self) -> None:
        standard = build_standard_project(
            {
                "项目编号": "P001",
                "项目名称": "标准化项目",
                "项目类型": "股权转让",
                "状态": "挂牌",
                "交易所": "北交所",
                "转让方": "测试转让方",
                "挂牌价格": "108.00",
                "挂牌开始日期": "2026-03-21",
                "挂牌截止日期": "2026-03-31",
                "神秘字段": "should-not-leak",
            }
        )

        payload = build_compat_payload(standard, raw_payload=standard.raw)

        self.assertEqual(payload["项目编号"], "P001")
        self.assertEqual(payload["挂牌价格"], "108.00")
        self.assertNotIn("神秘字段", payload)
        self.assertEqual(set(payload), set(COMPAT_PAYLOAD_KEYS))

    def test_build_compat_payload_preserves_public_resource_contract_fields(self) -> None:
        standard = build_standard_project(
            {
                "交易所": "北交所",
                "项目编号": "GR20260001",
                "项目名称": "成交样例项目",
                "交易方式": "网络竞价",
                "受让方名称": "样例受让方",
                "转让标的评估值": "88.00",
                "成交金额": "108.00",
                "成交日期": "2026/03/01",
            }
        )

        payload = build_compat_payload(standard, raw_payload=standard.raw)

        self.assertEqual(payload["交易方式"], "网络竞价")
        self.assertEqual(payload["受让方名称"], "样例受让方")
        self.assertEqual(payload["转让标的评估值"], "88.00")
        self.assertEqual(payload["成交金额"], "108.00")
        self.assertEqual(payload["成交日期"], "2026/03/01")


if __name__ == "__main__":
    unittest.main()
