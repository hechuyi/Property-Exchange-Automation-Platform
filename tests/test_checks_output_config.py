from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace

import peap.checks as checks_module
from peap.checks import _check_output_config


class ChecksOutputConfigTest(unittest.TestCase):
    def test_output_files_expected_keys_come_from_file_name_contract(self) -> None:
        config_obj = SimpleNamespace(
            OUTPUT_FILE_NAMES={
                "equity_transfer": "listing_equity_transfer.xlsx",
                "pre_disclosure": "listing_pre_disclosure.xlsx",
                "physical_asset": "listing_physical_asset.xlsx",
                "capital_increase": "listing_capital_increase.xlsx",
                "new_listing_business": "listing_new_business.xlsx",
            },
            DEAL_FILE_NAMES={
                "equity_transfer": "deal_equity_transfer.xlsx",
                "physical_asset": "deal_physical_asset.xlsx",
                "capital_increase": "deal_capital_increase.xlsx",
            },
            OUTPUT_FILES={
                "equity_transfer": "/tmp/listing_equity_transfer.xlsx",
                "pre_disclosure": "/tmp/listing_pre_disclosure.xlsx",
                "physical_asset": "/tmp/listing_physical_asset.xlsx",
                "capital_increase": "/tmp/listing_capital_increase.xlsx",
            },
            DEAL_FILES={
                "equity_transfer": "/tmp/deal_equity_transfer.xlsx",
                "physical_asset": "/tmp/deal_physical_asset.xlsx",
                "capital_increase": "/tmp/deal_capital_increase.xlsx",
            },
        )

        output_result = next(result for result in _check_output_config(config_obj) if result.name == "output-files")

        self.assertFalse(output_result.passed)
        self.assertIn("new_listing_business", output_result.message)

    def test_deal_files_expected_keys_come_from_file_name_contract(self) -> None:
        config_obj = SimpleNamespace(
            OUTPUT_FILE_NAMES={
                "equity_transfer": "listing_equity_transfer.xlsx",
                "pre_disclosure": "listing_pre_disclosure.xlsx",
                "physical_asset": "listing_physical_asset.xlsx",
                "capital_increase": "listing_capital_increase.xlsx",
            },
            DEAL_FILE_NAMES={
                "equity_transfer": "deal_equity_transfer.xlsx",
                "physical_asset": "deal_physical_asset.xlsx",
                "capital_increase": "deal_capital_increase.xlsx",
                "new_deal_business": "deal_new_business.xlsx",
            },
            OUTPUT_FILES={
                "equity_transfer": "/tmp/listing_equity_transfer.xlsx",
                "pre_disclosure": "/tmp/listing_pre_disclosure.xlsx",
                "physical_asset": "/tmp/listing_physical_asset.xlsx",
                "capital_increase": "/tmp/listing_capital_increase.xlsx",
            },
            DEAL_FILES={
                "equity_transfer": "/tmp/deal_equity_transfer.xlsx",
                "physical_asset": "/tmp/deal_physical_asset.xlsx",
                "capital_increase": "/tmp/deal_capital_increase.xlsx",
            },
        )

        deal_result = next(result for result in _check_output_config(config_obj) if result.name == "deal-files")

        self.assertFalse(deal_result.passed)
        self.assertIn("new_deal_business", deal_result.message)

    def test_checks_module_does_not_keep_independent_output_key_truth(self) -> None:
        checks_source = inspect.getsource(checks_module)

        self.assertNotIn("expected_output_keys", checks_source)
        self.assertNotIn("expected_deal_keys", checks_source)
        self.assertNotIn(
            '{"equity_transfer", "pre_disclosure", "physical_asset", "capital_increase"}',
            checks_source,
        )
        self.assertNotIn(
            '{"equity_transfer", "physical_asset", "capital_increase"}',
            checks_source,
        )

    def test_standard_mapping_self_check_excludes_legacy_public_resource(self) -> None:
        checks_source = inspect.getsource(checks_module._check_standard_mapping_layer)

        self.assertNotIn("public_resource", checks_source)


if __name__ == "__main__":
    unittest.main()
