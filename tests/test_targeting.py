from __future__ import annotations

import unittest
from types import SimpleNamespace

from peap.constants import (
    KEY_IS_PRE_DISCLOSURE,
    KEY_PROJECT_CODE,
    KEY_PROJECT_TYPE,
    KEY_STATUS,
    TYPE_PHYSICAL_ASSET,
)
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

    def test_build_output_target_settings_rejects_explicit_invalid_file_mappings(self) -> None:
        invalid_values = (None, ["physical_asset"], "physical_asset")

        for attr_name in ("OUTPUT_FILES", "DEAL_FILES"):
            for invalid_value in invalid_values:
                config = SimpleNamespace(
                    OUTPUT_EXCEL_DIR="C:\\temp\\excel",
                    OUTPUT_FILES=self.config.OUTPUT_FILES,
                    DEAL_FILES=self.config.DEAL_FILES,
                )
                setattr(config, attr_name, invalid_value)

                with self.subTest(attr_name=attr_name, invalid_value=invalid_value):
                    with self.assertRaises(TypeError):
                        build_output_target_settings(config)

    def test_decide_output_file_uses_injected_settings(self) -> None:
        settings = build_output_target_settings(self.config)

        listed = decide_output_file(
            {
                "record_family": "listing",
                "business_id": "physical_asset",
                KEY_STATUS: "挂牌",
            },
            settings=settings,
        )
        dealt = decide_output_file(
            {
                "record_family": "listing",
                "business_id": "physical_asset",
                KEY_STATUS: "成交",
            },
            settings=settings,
        )
        pre_disclosure = decide_output_file(
            {
                "record_family": "listing",
                "business_id": "pre_disclosure",
                KEY_STATUS: "成交",
                KEY_IS_PRE_DISCLOSURE: True,
            },
            settings=settings,
        )

        self.assertEqual(listed, "C:\\temp\\excel\\挂牌_实物资产.xlsx")
        self.assertEqual(dealt, "C:\\temp\\excel\\成交_实物资产.xlsx")
        self.assertEqual(pre_disclosure, "C:\\temp\\excel\\挂牌_预披露.xlsx")

    def test_decide_output_file_does_not_use_project_type_as_canonical_selector(self) -> None:
        settings = build_output_target_settings(self.config)

        inferred = decide_output_file(
            {
                KEY_PROJECT_TYPE: TYPE_PHYSICAL_ASSET,
                KEY_STATUS: "挂牌",
            },
            settings=settings,
        )

        self.assertIsNone(inferred)

    def test_decide_output_file_does_not_route_legacy_public_resource(self) -> None:
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

        self.assertIsNone(public_resource)
        self.assertIsNone(inferred)

    def test_decide_output_file_does_not_handle_additional_code_prefixes(self) -> None:
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

        self.assertIsNone(equity)
        self.assertIsNone(physical)

    def test_decide_output_file_does_not_infer_shape_from_project_code_prefix(self) -> None:
        settings = build_output_target_settings(self.config)

        inferred = decide_output_file(
            {
                KEY_PROJECT_CODE: "GR20260001",
                KEY_STATUS: "成交",
            },
            settings=settings,
        )

        self.assertIsNone(inferred)

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

        self.assertIsNone(target_file)

    def test_decide_output_file_uses_business_identity_family_for_nested_business_id(self) -> None:
        settings = build_output_target_settings(self.config)

        target_file = decide_output_file(
            {
                KEY_STATUS: "成交",
                "business_identity": {
                    "record_family": "deal",
                    "business_id": "deal_equity_transfer",
                },
            },
            settings=settings,
        )

        self.assertEqual(target_file, "C:\\temp\\excel\\成交_股权转让.xlsx")

    def test_decide_output_file_uses_canonical_identity_family_for_nested_business_id(self) -> None:
        settings = build_output_target_settings(self.config)

        target_file = decide_output_file(
            {
                KEY_STATUS: "成交",
                "canonical_record": {
                    "business_identity": {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                    },
                },
            },
            settings=settings,
        )

        self.assertEqual(target_file, "C:\\temp\\excel\\成交_股权转让.xlsx")

    def test_decide_output_file_does_not_fallback_to_listing_for_empty_or_unknown_identity_family(self) -> None:
        settings = build_output_target_settings(self.config)

        for record_family in ("", "unknown_family"):
            with self.subTest(record_family=record_family):
                target_file = decide_output_file(
                    {
                        KEY_STATUS: "挂牌",
                        "business_identity": {
                            "record_family": record_family,
                            "business_id": "equity_transfer",
                        },
                    },
                    settings=settings,
                )

                self.assertIsNone(target_file)


if __name__ == "__main__":
    unittest.main()
