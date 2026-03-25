from __future__ import annotations

import unittest
from types import SimpleNamespace

from peap.constants import (
    KEY_IS_PRE_DISCLOSURE,
    KEY_PROJECT_CODE,
    KEY_PROJECT_TYPE,
    KEY_STATUS,
    TYPE_PHYSICAL_ASSET,
    TYPE_PRE_DISCLOSURE,
)
from peap.output_contract import PUBLIC_RESOURCE_OUTPUT_FILENAME
from peap.parsing import build_parsed_project
from peap.targeting import (
    OutputTargetSettings,
    build_output_target_settings,
    decide_output_file,
)


class TargetingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SimpleNamespace(
            OUTPUT_EXCEL_DIR="C:\\temp\\excel",
            OUTPUT_FILES={
                "equity_transfer": "C:\\temp\\excel\\挂牌_股权转让.xlsx",
                "pre_disclosure": "C:\\temp\\excel\\挂牌_预披露.xlsx",
                "physical_asset": "C:\\temp\\excel\\挂牌_实物资产.xlsx",
                "capital_increase": "C:\\temp\\excel\\挂牌_增资扩股.xlsx",
            },
            DEAL_FILES={
                "equity_transfer": "C:\\temp\\excel\\成交_股权转让.xlsx",
                "physical_asset": "C:\\temp\\excel\\成交_实物资产.xlsx",
                "capital_increase": "C:\\temp\\excel\\成交_增资扩股.xlsx",
            },
        )

    def test_build_output_target_settings_from_config(self) -> None:
        settings = build_output_target_settings(self.config)

        self.assertIsInstance(settings, OutputTargetSettings)
        self.assertEqual(settings.output_excel_dir, "C:\\temp\\excel")
        self.assertEqual(
            settings.output_files["pre_disclosure"],
            "C:\\temp\\excel\\挂牌_预披露.xlsx",
        )
        self.assertEqual(
            settings.deal_files["physical_asset"],
            "C:\\temp\\excel\\成交_实物资产.xlsx",
        )

    def test_decide_output_file_uses_injected_settings(self) -> None:
        settings = build_output_target_settings(self.config)

        listed = decide_output_file(
            {
                KEY_PROJECT_TYPE: TYPE_PHYSICAL_ASSET,
                KEY_STATUS: "挂牌",
            },
            settings=settings,
        )
        dealt = decide_output_file(
            {
                KEY_PROJECT_TYPE: TYPE_PHYSICAL_ASSET,
                KEY_STATUS: "成交",
            },
            settings=settings,
        )
        pre_disclosure = decide_output_file(
            {
                KEY_PROJECT_TYPE: TYPE_PRE_DISCLOSURE,
                KEY_STATUS: "成交",
                KEY_IS_PRE_DISCLOSURE: True,
            },
            settings=settings,
        )

        self.assertEqual(listed, "C:\\temp\\excel\\挂牌_实物资产.xlsx")
        self.assertEqual(dealt, "C:\\temp\\excel\\成交_实物资产.xlsx")
        self.assertEqual(pre_disclosure, "C:\\temp\\excel\\挂牌_预披露.xlsx")

    def test_decide_output_file_handles_public_resource_and_code_inference(self) -> None:
        settings = build_output_target_settings(self.config)

        public_resource = decide_output_file(
            {
                "__source_exchange": "public_resource",
            },
            settings=settings,
        )
        inferred = decide_output_file(
            {
                KEY_PROJECT_CODE: "GR20260001",
                KEY_STATUS: "成交",
            },
            settings=settings,
        )

        self.assertEqual(
            public_resource,
            "C:\\temp\\excel\\公共资源网四大交易所股权转让成交信息统计.xlsx",
        )
        self.assertEqual(inferred, "C:\\temp\\excel\\成交_实物资产.xlsx")

    def test_decide_output_file_handles_additional_code_prefixes(self) -> None:
        settings = build_output_target_settings(self.config)

        equity = decide_output_file(
            {
                KEY_PROJECT_CODE: "T32026TJ1000007",
                KEY_STATUS: "挂牌",
            },
            settings=settings,
        )
        physical = decide_output_file(
            {
                KEY_PROJECT_CODE: "TA2026BJ1000943",
                KEY_STATUS: "挂牌",
            },
            settings=settings,
        )

        self.assertEqual(equity, "C:\\temp\\excel\\挂牌_股权转让.xlsx")
        self.assertEqual(physical, "C:\\temp\\excel\\挂牌_实物资产.xlsx")

    def test_decide_output_file_accepts_parsed_project(self) -> None:
        settings = build_output_target_settings(self.config)
        parsed = build_parsed_project(
            file_path="C:\\temp\\sample.html",
            exchange="public_resource",
            encoding="utf-8",
            data={
                KEY_PROJECT_CODE: "GR20260001",
                KEY_STATUS: "鎴愪氦",
                KEY_PROJECT_TYPE: TYPE_PHYSICAL_ASSET,
            },
        )

        target_file = decide_output_file(parsed, settings=settings)

        self.assertEqual(
            target_file,
            f"C:\\temp\\excel\\{PUBLIC_RESOURCE_OUTPUT_FILENAME}",
        )


if __name__ == "__main__":
    unittest.main()
