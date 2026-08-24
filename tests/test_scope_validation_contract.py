from __future__ import annotations

import unittest

from desktop_backend.record_scope import normalize_record_scope
from desktop_backend.request_contract import (
    build_record_scope_payload_from_query,
    normalize_export_request_payload,
    normalize_one_click_request,
)
from desktop_backend.settings_contract import normalize_basic_settings_update


class ScopeValidationContractTest(unittest.TestCase):
    def test_shared_scope_boundary_rejects_unknown_business_invalid_exchange_and_mismatched_label(self) -> None:
        probes = (
            (
                {"record_family": "listing", "business_id": "not_a_real_business"},
                "business_id",
            ),
            (
                {"record_family": "listing", "business_id": "physical_asset", "exchange": "not_a_real_exchange"},
                "exchange",
            ),
            (
                {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "business_label": "股权转让",
                },
                "business_label",
            ),
        )

        for payload, expected_fragment in probes:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, expected_fragment):
                    normalize_record_scope(payload)

    def test_request_scope_entrypoints_reject_unknown_business_and_invalid_exchange(self) -> None:
        with self.assertRaisesRegex(ValueError, "business_id"):
            build_record_scope_payload_from_query(
                {
                    "record_family": ["listing"],
                    "business_id": ["not_a_real_business"],
                    "exchange": ["all"],
                }
            )

        with self.assertRaisesRegex(ValueError, "exchange"):
            normalize_export_request_payload(
                {
                    "scope": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "not_a_real_exchange",
                    },
                    "requested_export_mode": "full",
                }
            )

    def test_settings_update_rejects_server_owned_default_scope_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "effective_default_scope"):
            normalize_basic_settings_update(
                {
                    "stored_preference": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "cbex",
                    },
                    "effective_default_scope": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "cbex",
                    },
                }
            )

        with self.assertRaisesRegex(ValueError, "stale_default_metadata"):
            normalize_basic_settings_update(
                {
                    "stored_preference": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "cbex",
                    },
                    "stale_default_metadata": {
                        "is_stale": True,
                        "reason": "invalid_exchange",
                    },
                }
            )

    def test_one_click_request_rejects_client_supplied_server_owned_default_truth_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "effective_default_scope"):
            normalize_one_click_request(
                {
                    "effective_default_scope": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "cbex",
                    }
                },
                basic_settings={
                    "effective_default_scope": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "cbex",
                    },
                    "stored_preference": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "cbex",
                    },
                    "default_exchange": "cbex",
                    "default_concurrency": 2,
                },
                advanced_settings={"save_json": False, "postprocess_config": ""},
            )

        with self.assertRaisesRegex(ValueError, "stored_preference"):
            normalize_one_click_request(
                {
                    "stored_preference": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "cbex",
                    }
                },
                basic_settings={
                    "effective_default_scope": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "cbex",
                    },
                    "stored_preference": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "cbex",
                    },
                    "default_exchange": "cbex",
                    "default_concurrency": 2,
                },
                advanced_settings={"save_json": False, "postprocess_config": ""},
            )

    def test_one_click_request_treats_single_record_families_entry_as_scalar_scope(self) -> None:
        payload = normalize_one_click_request(
            {
                "record_families": ["deal"],
                "business_id": "deal_equity_transfer",
                "exchange": "sse",
            },
            basic_settings={
                "default_exchange": "all",
                "default_concurrency": 2,
                "effective_default_scope": {},
                "stored_preference": {},
            },
            advanced_settings={"save_json": False, "postprocess_config": ""},
        )

        self.assertEqual(payload["record_family"], "deal")
        self.assertEqual(payload["business_id"], "deal_equity_transfer")
        self.assertEqual(payload["exchange"], "sse")
        self.assertNotIn("record_families", payload)

    def test_one_click_request_treats_single_family_scopes_entry_as_scalar_scope(self) -> None:
        payload = normalize_one_click_request(
            {
                "start_date": "2026-03-22",
                "end_date": "2026-03-22",
                "family_scopes": [
                    {
                        "record_family": "deal",
                        "business_id": "deal_equity_transfer",
                        "exchange": "sse",
                    },
                ],
            },
            basic_settings={
                "default_exchange": "all",
                "default_concurrency": 2,
                "effective_default_scope": {},
                "stored_preference": {},
            },
            advanced_settings={"save_json": False, "postprocess_config": ""},
        )

        self.assertEqual(payload["record_family"], "deal")
        self.assertEqual(payload["business_id"], "deal_equity_transfer")
        self.assertEqual(payload["business_label"], "股权转让成交")
        self.assertEqual(payload["exchange"], "sse")
        self.assertNotIn("record_families", payload)
        self.assertNotIn("family_scopes", payload)

    def test_one_click_request_requires_explicit_family_scopes_for_multi_family_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "family_scopes"):
            normalize_one_click_request(
                {
                    "record_families": ["listing", "deal"],
                    "business_id": "all",
                    "exchange": "sse",
                },
                basic_settings={
                    "default_exchange": "all",
                    "default_concurrency": 2,
                    "effective_default_scope": {},
                    "stored_preference": {},
                },
                advanced_settings={"save_json": False, "postprocess_config": ""},
            )

    def test_one_click_request_rejects_invalid_family_scope_entries_instead_of_filtering(self) -> None:
        basic_settings = {
            "default_exchange": "all",
            "default_concurrency": 2,
            "effective_default_scope": {},
            "stored_preference": {},
        }

        with self.assertRaisesRegex(ValueError, "family_scopes"):
            normalize_one_click_request(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "sse",
                    "family_scopes": [
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "exchange": "sse",
                        },
                        "not-a-mapping",
                    ],
                },
                basic_settings=basic_settings,
                advanced_settings={"save_json": False, "postprocess_config": ""},
            )

        with self.assertRaisesRegex(ValueError, "family_scopes"):
            normalize_one_click_request(
                {
                    "start_date": "2026-03-22",
                    "end_date": "2026-03-22",
                    "exchange": "sse",
                    "family_scopes": [
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "exchange": "sse",
                        },
                        {
                            "record_family": "deal",
                            "business_id": "",
                            "exchange": "sse",
                        },
                    ],
                },
                basic_settings=basic_settings,
                advanced_settings={"save_json": False, "postprocess_config": ""},
            )

    def test_one_click_request_rejects_source_business_scopes_outside_one_click_contract(self) -> None:
        basic_settings = {
            "default_exchange": "all",
            "default_concurrency": 2,
            "effective_default_scope": {},
            "stored_preference": {},
        }
        advanced_settings = {"save_json": False, "postprocess_config": ""}

        with self.assertRaisesRegex(ValueError, "one-click scope"):
            normalize_one_click_request(
                {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "exchange": "shandong",
                },
                basic_settings=basic_settings,
                advanced_settings=advanced_settings,
            )

        with self.assertRaisesRegex(ValueError, "family_scopes"):
            normalize_one_click_request(
                {
                    "exchange": "sse",
                    "family_scopes": [
                        {
                            "record_family": "listing",
                            "business_id": "equity_transfer",
                            "exchange": "sse",
                        },
                        {
                            "record_family": "deal",
                            "business_id": "deal_physical_asset",
                            "exchange": "tpre",
                        },
                    ],
                },
                basic_settings=basic_settings,
                advanced_settings=advanced_settings,
            )

    def test_one_click_request_accepts_declared_one_click_source_business_scopes(self) -> None:
        basic_settings = {
            "default_exchange": "all",
            "default_concurrency": 2,
            "effective_default_scope": {},
            "stored_preference": {},
        }
        advanced_settings = {"save_json": False, "postprocess_config": ""}

        probes = (
            ("listing", "equity_transfer", "shandong"),
            ("listing", "capital_increase", "guangdong"),
            ("deal", "deal_equity_transfer", "tpre"),
        )
        for record_family, business_id, exchange in probes:
            with self.subTest(record_family=record_family, business_id=business_id, exchange=exchange):
                payload = normalize_one_click_request(
                    {
                        "record_family": record_family,
                        "business_id": business_id,
                        "exchange": exchange,
                    },
                    basic_settings=basic_settings,
                    advanced_settings=advanced_settings,
                )

                self.assertEqual(payload["record_family"], record_family)
                self.assertEqual(payload["business_id"], business_id)
                self.assertEqual(payload["exchange"], exchange)

    def test_one_click_request_expands_all_business_scope_before_surface_validation(self) -> None:
        basic_settings = {
            "default_exchange": "all",
            "default_concurrency": 2,
            "effective_default_scope": {},
            "stored_preference": {},
        }
        advanced_settings = {"save_json": False, "postprocess_config": ""}

        payload = normalize_one_click_request(
            {
                "record_family": "listing",
                "business_id": "all",
                "exchange": "all",
            },
            basic_settings=basic_settings,
            advanced_settings=advanced_settings,
        )

        self.assertEqual(payload["record_family"], "listing")
        self.assertEqual(payload["business_id"], "all")
        self.assertEqual(payload["exchange"], "all")

        with self.assertRaisesRegex(ValueError, "one-click scope"):
            normalize_one_click_request(
                {
                    "record_family": "listing",
                    "business_id": "all",
                    "exchange": "shandong",
                },
                basic_settings=basic_settings,
                advanced_settings=advanced_settings,
            )


if __name__ == "__main__":
    unittest.main()
