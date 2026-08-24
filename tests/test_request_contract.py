from __future__ import annotations

import unittest

from desktop_backend.domain.normalizers import (
    normalize_job_event_payload,
    normalize_mapping_payload,
)
from desktop_backend.legacy_contract import (
    legacy_mapping_source_name,
    legacy_record_scope_page_size,
)
from desktop_backend.request_contract import (
    build_record_scope_payload_from_query,
    normalize_archive_reprocess_request,
    normalize_export_history_download_request,
    normalize_export_request_payload,
    normalize_manual_import_request,
    normalize_mapping_business_re_evaluation_request,
    normalize_mapping_conflict_request,
    normalize_mapping_request,
    normalize_mapping_undo_request,
    normalize_mapping_update_request,
    normalize_one_click_request,
    normalize_path_open_request,
    normalize_path_selection_request,
    normalize_runtime_install_request,
)


class RequestContractTest(unittest.TestCase):
    def test_legacy_contract_helpers_reject_explicit_non_mapping_payloads(self) -> None:
        self.assertEqual(legacy_mapping_source_name(None), "")
        self.assertIsNone(legacy_record_scope_page_size(None))

        for payload in (False, 0, [], "source"):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "payload must be an object"):
                    legacy_mapping_source_name(payload)  # type: ignore[arg-type]
                with self.assertRaisesRegex(ValueError, "payload must be an object"):
                    legacy_record_scope_page_size(payload)  # type: ignore[arg-type]

    def test_build_record_scope_payload_from_query_normalizes_shared_fields(self) -> None:
        payload = build_record_scope_payload_from_query(
            {
                "record_family": ["listing"],
                "state": ["ready"],
                "exchange": ["beijing"],
                "keyword": ["华润"],
                "date_from": ["2026-03-01"],
                "date_to": ["2026-03-31"],
                "page": ["2"],
                "limit": ["25"],
            }
        )

        self.assertEqual(
            payload,
            {
                "record_family": "listing",
                "state": "ready",
                "business_id": "all",
                "exchange": "cbex",
                "keyword": "华润",
                "date_from": "2026-03-01",
                "date_to": "2026-03-31",
                "page": 2,
                "page_size": 25,
            },
        )

    def test_build_record_scope_payload_from_query_rejects_legacy_project_type_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "project_type"):
            build_record_scope_payload_from_query(
                {
                    "record_family": ["listing"],
                    "state": ["all"],
                    "project_type": ["股权转让"],
                    "exchange": ["all"],
                }
            )

    def test_build_record_scope_payload_from_query_rejects_unknown_record_family(self) -> None:
        with self.assertRaises(ValueError):
            build_record_scope_payload_from_query(
                {
                    "record_family": ["unknown_family"],
                    "state": ["all"],
                    "exchange": ["all"],
                }
            )

    def test_build_record_scope_payload_from_query_rejects_unknown_business_and_invalid_exchange(self) -> None:
        with self.assertRaisesRegex(ValueError, "business_id"):
            build_record_scope_payload_from_query(
                {
                    "record_family": ["listing"],
                    "business_id": ["not_a_real_business"],
                    "exchange": ["all"],
                }
            )

        with self.assertRaisesRegex(ValueError, "exchange"):
            build_record_scope_payload_from_query(
                {
                    "record_family": ["listing"],
                    "business_id": ["physical_asset"],
                    "exchange": ["not_a_real_exchange"],
                }
            )

    def test_build_record_scope_payload_from_query_rejects_invalid_limit_when_page_size_is_present(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid limit"):
            build_record_scope_payload_from_query(
                {
                    "record_family": ["listing"],
                    "business_id": ["equity_transfer"],
                    "page_size": ["25"],
                    "limit": ["abc"],
                }
            )

    def test_build_record_scope_payload_from_query_rejects_invalid_query_value_shape(self) -> None:
        for query in (
            {"record_family": "listing"},
            {"record_family": None},
            {"record_family": ["listing", {"value": "deal"}]},
        ):
            with self.subTest(query=query):
                with self.assertRaisesRegex(ValueError, "record_family"):
                    build_record_scope_payload_from_query(query)  # type: ignore[arg-type]

    def test_normalize_export_request_payload_accepts_flat_record_scope_fields(self) -> None:
        payload = normalize_export_request_payload(
            {
                "record_family": "listing",
                "state": "ready",
                "business_id": "all",
                "exchange": "cbex",
                "keyword": "北交所",
                "date_from": "2026-03-01",
                "date_to": "2026-03-31",
                "requested_export_mode": "full",
                "output_dir": "/tmp/export",
            }
        )

        self.assertEqual(
            payload,
            {
                "scope": {
                    "record_family": "listing",
                    "state": "ready",
                    "business_id": "all",
                    "exchange": "cbex",
                    "keyword": "北交所",
                    "date_from": "2026-03-01",
                    "date_to": "2026-03-31",
                    "page": 1,
                    "page_size": 50,
                },
                "requested_export_mode": "full",
                "output_dir": "/tmp/export",
            },
        )
        self.assertNotIn("export_mode", payload)
        self.assertNotIn("mode", payload)
        self.assertNotIn("cursor_key", payload)

    def test_normalize_export_request_payload_preserves_family_business_scope(self) -> None:
        payload = normalize_export_request_payload(
            {
                "record_family": "listing",
                "business_id": "physical_asset",
                "state": "ready",
                "exchange": "cbex",
                "date_from": "2026-03-01",
                "date_to": "2026-03-31",
                "requested_export_mode": "full",
            }
        )

        self.assertEqual(payload["scope"]["record_family"], "listing")
        self.assertIn("business_id", payload["scope"])
        self.assertEqual(payload["scope"]["business_id"], "physical_asset")
        self.assertNotIn("project_type", payload["scope"])

    def test_normalize_export_request_payload_rejects_legacy_export_mode_mode_and_cursor_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "export_mode"):
            normalize_export_request_payload(
                {
                    "scope": {
                        "record_family": "listing",
                        "business_id": "all",
                        "state": "all",
                        "exchange": "all",
                    },
                    "export_mode": "full",
                }
            )
        with self.assertRaisesRegex(ValueError, "mode"):
            normalize_export_request_payload(
                {
                    "scope": {
                        "record_family": "listing",
                        "business_id": "all",
                        "state": "all",
                        "exchange": "all",
                    },
                    "mode": "rebuild",
                }
            )
        with self.assertRaisesRegex(ValueError, "cursor_key"):
            normalize_export_request_payload(
                {
                    "scope": {
                        "record_family": "listing",
                        "business_id": "all",
                        "state": "all",
                        "exchange": "all",
                    },
                    "requested_export_mode": "incremental",
                    "cursor_key": "legacy-cursor",
                }
            )

    def test_normalize_export_request_payload_rejects_invalid_requested_export_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "requested_export_mode"):
            normalize_export_request_payload(
                {
                    "scope": {
                        "record_family": "listing",
                        "business_id": "all",
                        "state": "all",
                        "exchange": "all",
                    },
                    "requested_export_mode": "rebuild",
                }
            )

    def test_normalize_export_request_payload_rejects_non_string_requested_export_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "requested_export_mode"):
            normalize_export_request_payload(
                {
                    "scope": {
                        "record_family": "listing",
                        "business_id": "all",
                        "state": "all",
                        "exchange": "all",
                    },
                    "requested_export_mode": False,
                }
            )

    def test_normalize_export_request_payload_rejects_legacy_project_type_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "project_type"):
            normalize_export_request_payload(
                {
                    "scope": {
                        "record_family": "listing",
                        "state": "all",
                        "project_type": "股权转让",
                        "exchange": "sse",
                        "keyword": "",
                        "date_from": "",
                        "date_to": "",
                        "page": 3,
                        "page_size": 20,
                    },
                    "requested_export_mode": "full",
                }
            )

    def test_normalize_export_request_payload_rejects_empty_explicit_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit canonical scope"):
            normalize_export_request_payload({"scope": {}})

    def test_normalize_export_request_payload_rejects_non_mapping_payloads(self) -> None:
        for payload in (False, 0, [], "scope"):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "payload must be an object"):
                    normalize_export_request_payload(payload)  # type: ignore[arg-type]

    def test_normalize_export_request_payload_rejects_non_mapping_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "scope must be an object"):
            normalize_export_request_payload(
                {
                    "scope": [],
                    "record_family": "listing",
                    "business_id": "all",
                    "state": "all",
                    "exchange": "all",
                    "requested_export_mode": "full",
                }
            )

    def test_normalize_export_request_payload_rejects_non_string_scope_text_fields(self) -> None:
        base_scope = {
            "record_family": "listing",
            "business_id": "all",
            "state": "all",
            "exchange": "all",
            "keyword": "",
            "date_from": "",
            "date_to": "",
        }
        for field_name in ("state", "keyword", "date_from", "date_to"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    normalize_export_request_payload(
                        {
                            "scope": {**base_scope, field_name: {"value": "x"}},
                            "requested_export_mode": "full",
                        }
                    )

    def test_normalize_export_request_payload_rejects_non_string_output_dir(self) -> None:
        with self.assertRaisesRegex(ValueError, "output_dir"):
            normalize_export_request_payload(
                {
                    "scope": {
                        "record_family": "listing",
                        "business_id": "all",
                        "state": "all",
                        "exchange": "all",
                    },
                    "requested_export_mode": "full",
                    "output_dir": {"path": "/tmp/export"},
                }
            )

    def test_normalize_export_history_download_request_rejects_non_string_output_dir(self) -> None:
        with self.assertRaisesRegex(ValueError, "output_dir"):
            normalize_export_history_download_request(
                {"output_dir": {"path": "/tmp/export"}},
                default_output_dir="/tmp/default-export",
            )

    def test_normalize_export_history_download_request_defaults_missing_or_blank_output_dir(self) -> None:
        for payload in ({}, {"output_dir": ""}, {"output_dir": "  "}):
            with self.subTest(payload=payload):
                self.assertEqual(
                    normalize_export_history_download_request(
                        payload,
                        default_output_dir="/tmp/default-export",
                    ),
                    {"output_dir": ""},
                )

    def test_normalize_runtime_install_request_rejects_non_string_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "browser_name"):
            normalize_runtime_install_request({"browser_name": {"name": "chromium"}})

        with self.assertRaisesRegex(ValueError, "trigger"):
            normalize_runtime_install_request({"trigger": {"source": "manual"}})

    def test_normalize_runtime_install_request_uses_defaults_when_omitted(self) -> None:
        payload = normalize_runtime_install_request({})

        self.assertEqual(payload, {"browser_name": "chromium", "trigger": "manual"})

    def test_normalize_one_click_request_rejects_legacy_project_type_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "project_type"):
            normalize_one_click_request(
                {
                    "record_family": "listing",
                    "project_type": "股权转让",
                    "exchange": "sse",
                },
                basic_settings={
                    "default_exchange": "all",
                    "default_concurrency": 2,
                    "effective_default_scope": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "exchange": "cbex",
                    },
                    "stored_preference": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "exchange": "cbex",
                    },
                },
                advanced_settings={"save_json": False, "postprocess_config": ""},
            )

    def test_normalize_one_click_request_rejects_client_supplied_server_owned_default_scope_fields(self) -> None:
        basic_settings = {
            "default_exchange": "all",
            "default_concurrency": 2,
            "effective_default_scope": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "cbex",
            },
            "stored_preference": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "cbex",
            },
        }
        advanced_settings = {"save_json": False, "postprocess_config": ""}

        with self.assertRaisesRegex(ValueError, "effective_default_scope"):
            normalize_one_click_request(
                {
                    "effective_default_scope": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "cbex",
                    }
                },
                basic_settings=basic_settings,
                advanced_settings=advanced_settings,
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
                basic_settings=basic_settings,
                advanced_settings=advanced_settings,
            )

    def test_normalize_one_click_request_rejects_explicit_non_mapping_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload must be an object"):
            normalize_one_click_request(
                [],  # type: ignore[arg-type]
                basic_settings={"default_exchange": "all", "default_concurrency": 2},
                advanced_settings={"save_json": False, "postprocess_config": ""},
            )

    def test_normalize_one_click_request_rejects_explicit_non_mapping_scope(self) -> None:
        for raw_scope in ([], "listing", 0):
            with self.subTest(raw_scope=raw_scope):
                with self.assertRaisesRegex(ValueError, "scope must be an object"):
                    normalize_one_click_request(
                        {"scope": raw_scope},
                        basic_settings={"default_exchange": "all", "default_concurrency": 2},
                        advanced_settings={"save_json": False, "postprocess_config": ""},
                    )

    def test_normalize_one_click_request_reports_missing_actionable_default_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "no actionable default scope"):
            normalize_one_click_request(
                {},
                basic_settings={
                    "default_exchange": "all",
                    "default_concurrency": 2,
                    "effective_default_scope": {},
                    "stored_preference": {},
                    "stale_default_metadata": {
                        "is_stale": False,
                        "reason": "",
                        "hint": "",
                    },
                },
                advanced_settings={"save_json": False, "postprocess_config": ""},
            )

    def test_normalize_one_click_request_reports_stale_default_scope_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_exchange"):
            normalize_one_click_request(
                {},
                basic_settings={
                    "default_exchange": "sse",
                    "default_concurrency": 2,
                    "effective_default_scope": {},
                    "stored_preference": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "not_a_real_exchange",
                    },
                    "stale_default_metadata": {
                        "is_stale": True,
                        "reason": "invalid_exchange",
                        "hint": "reselect a supported exchange in settings",
                    },
                },
                advanced_settings={"save_json": False, "postprocess_config": ""},
            )

    def test_normalize_one_click_request_rejects_invalid_stale_default_metadata_bool(self) -> None:
        with self.assertRaisesRegex(ValueError, "stale_default_metadata.is_stale"):
            normalize_one_click_request(
                {},
                basic_settings={
                    "default_exchange": "sse",
                    "default_concurrency": 2,
                    "effective_default_scope": {},
                    "stored_preference": {},
                    "stale_default_metadata": {
                        "is_stale": "not-a-bool",
                        "reason": "invalid_exchange",
                        "hint": "reselect a supported exchange in settings",
                    },
                },
                advanced_settings={"save_json": False, "postprocess_config": ""},
            )

    def test_normalize_one_click_request_rejects_invalid_boolean_input_instead_of_defaulting_false(self) -> None:
        base_request = {
            "record_family": "listing",
            "business_id": "equity_transfer",
            "exchange": "sse",
        }
        basic_settings = {
            "default_exchange": "all",
            "default_concurrency": 2,
            "effective_default_scope": {},
            "stored_preference": {},
        }

        for field_name in ("no_resume", "save_json", "verbose"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    normalize_one_click_request(
                        {**base_request, field_name: "not-a-bool"},
                        basic_settings=basic_settings,
                        advanced_settings={"save_json": False, "postprocess_config": ""},
                    )

    def test_normalize_one_click_request_rejects_invalid_advanced_save_json_default(self) -> None:
        base_request = {
            "record_family": "listing",
            "business_id": "equity_transfer",
            "exchange": "sse",
        }
        basic_settings = {
            "default_exchange": "all",
            "default_concurrency": 2,
            "effective_default_scope": {},
            "stored_preference": {},
        }

        for raw_value in ("not-a-bool", {}):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(ValueError, "save_json"):
                    normalize_one_click_request(
                        base_request,
                        basic_settings=basic_settings,
                        advanced_settings={"save_json": raw_value, "postprocess_config": ""},
                    )

    def test_normalize_one_click_request_rejects_non_string_scope_and_config_fields(self) -> None:
        base_request = {
            "record_family": "listing",
            "business_id": "equity_transfer",
            "business_label": "股权转让",
            "exchange": "sse",
            "start_date": "2026-03-26",
            "end_date": "2026-03-26",
            "postprocess_config": "/tmp/rules.yaml",
        }
        basic_settings = {
            "default_exchange": "all",
            "default_concurrency": 2,
            "effective_default_scope": {},
            "stored_preference": {},
        }
        advanced_settings = {"save_json": False, "postprocess_config": ""}

        for field_name in (
            "record_family",
            "business_id",
            "business_label",
            "exchange",
            "start_date",
            "end_date",
            "postprocess_config",
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    normalize_one_click_request(
                        {**base_request, field_name: {"value": "x"}},
                        basic_settings=basic_settings,
                        advanced_settings=advanced_settings,
                    )

    def test_normalize_one_click_request_rejects_non_string_record_families_entries(self) -> None:
        with self.assertRaisesRegex(ValueError, "record_families"):
            normalize_one_click_request(
                {
                    "record_families": ["listing", {"record_family": "deal"}],
                    "business_id": "equity_transfer",
                    "exchange": "sse",
                },
                basic_settings={"default_exchange": "all", "default_concurrency": 2},
                advanced_settings={"save_json": False, "postprocess_config": ""},
            )

    def test_normalize_job_event_payload_rejects_explicit_non_mapping_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload must be an object"):
            normalize_job_event_payload(
                {
                    "event_id": "event-1",
                    "stage": "download",
                    "status": "running",
                    "payload": [],
                }
            )

    def test_normalize_one_click_request_rejects_non_string_family_scope_fields(self) -> None:
        for field_name in ("record_family", "business_id", "business_label", "exchange"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    normalize_one_click_request(
                        {
                            "family_scopes": [
                                {
                                    "record_family": "listing",
                                    "business_id": "equity_transfer",
                                    "business_label": "股权转让",
                                    "exchange": "sse",
                                    field_name: {"value": "x"},
                                }
                            ],
                        },
                        basic_settings={"default_exchange": "all", "default_concurrency": 2},
                        advanced_settings={"save_json": False, "postprocess_config": ""},
                    )

    def test_normalize_manual_import_request_does_not_inherit_effective_default_scope_when_scope_is_omitted(self) -> None:
        payload = normalize_manual_import_request(
            {"input_dir": "/tmp/manual-html"},
            basic_settings={
                "effective_default_scope": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "business_label": "股权转让",
                    "exchange": "sse",
                },
            },
            advanced_settings={"raw_manual_root": ""},
        )

        self.assertEqual(
            payload,
            {
                "input_dir": "/tmp/manual-html",
            },
        )

    def test_normalize_manual_import_request_preserves_explicit_scope(self) -> None:
        payload = normalize_manual_import_request(
            {
                "input_dir": "/tmp/manual-html",
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "sse",
            },
            basic_settings={
                "effective_default_scope": {
                    "record_family": "listing",
                    "business_id": "physical_asset",
                    "business_label": "实物资产",
                    "exchange": "cbex",
                },
            },
            advanced_settings={"raw_manual_root": ""},
        )

        self.assertEqual(
            payload,
            {
                "input_dir": "/tmp/manual-html",
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "sse",
            },
        )

    def test_normalize_manual_import_request_rejects_incomplete_explicit_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "record_family and business_id"):
            normalize_manual_import_request(
                {
                    "input_dir": "/tmp/manual-html",
                    "business_id": "equity_transfer",
                    "exchange": "sse",
                },
                basic_settings={},
                advanced_settings={"raw_manual_root": ""},
            )

    def test_normalize_manual_import_request_rejects_unknown_explicit_business_id_instead_of_omitting_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "business_id"):
            normalize_manual_import_request(
                {
                    "input_dir": "/tmp/manual-html",
                    "record_family": "listing",
                    "business_id": "not_a_real_business",
                    "exchange": "sse",
                },
                basic_settings={},
                advanced_settings={"raw_manual_root": ""},
            )

    def test_normalize_manual_import_request_rejects_non_string_explicit_scope_fields(self) -> None:
        base_payload = {
            "input_dir": "/tmp/manual-html",
            "record_family": "listing",
            "business_id": "equity_transfer",
            "business_label": "股权转让",
            "exchange": "sse",
        }
        for field_name in ("record_family", "business_id", "business_label", "exchange"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    normalize_manual_import_request(
                        {**base_payload, field_name: {"value": "x"}},
                        basic_settings={},
                        advanced_settings={"raw_manual_root": ""},
                    )

    def test_normalize_manual_import_request_rejects_false_input_dir_instead_of_using_default_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "input_dir"):
            normalize_manual_import_request(
                {"input_dir": False},
                basic_settings={},
                advanced_settings={"raw_manual_root": "/tmp/manual-html"},
            )

    def test_normalize_archive_reprocess_request_rejects_false_input_dir_instead_of_using_default_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "input_dir"):
            normalize_archive_reprocess_request(
                {"input_dir": False},
                default_input_dir="/tmp/archive-html",
            )

    def test_normalize_archive_reprocess_request_rejects_false_payload_instead_of_using_default_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload"):
            normalize_archive_reprocess_request(
                False,
                default_input_dir="/tmp/archive-html",
            )

    def test_normalize_manual_import_request_rejects_exchange_outside_records_source_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "manual-import scope"):
            normalize_manual_import_request(
                {
                    "input_dir": "/tmp/manual-html",
                    "record_family": "deal",
                    "business_id": "deal_physical_asset",
                    "business_label": "实物资产成交",
                    "exchange": "tpre",
                },
                basic_settings={},
                advanced_settings={"raw_manual_root": ""},
            )

    def test_normalize_manual_import_request_accepts_listing_capital_increase_sources(self) -> None:
        for exchange in ("shandong", "guangdong", "shenzhen"):
            with self.subTest(exchange=exchange):
                payload = normalize_manual_import_request(
                    {
                        "input_dir": "/tmp/manual-html",
                        "record_family": "listing",
                        "business_id": "capital_increase",
                        "business_label": "增资扩股",
                        "exchange": exchange,
                    },
                    basic_settings={},
                    advanced_settings={"raw_manual_root": ""},
                )

                self.assertEqual(payload["exchange"], exchange)
                self.assertEqual(payload["business_id"], "capital_increase")

    def test_normalize_mapping_request_rejects_invalid_confirm_overwrite_instead_of_defaulting_false(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirm_overwrite"):
            normalize_mapping_request(
                {
                    "source_name": "测试来源",
                    "target_value": "央企",
                    "confirm_overwrite": "not-a-bool",
                }
            )

    def test_normalize_mapping_request_rejects_non_string_rule_fields_instead_of_stringifying_objects(self) -> None:
        base_payload = {
            "source_name": "测试来源",
            "target_value": "央企",
            "rule_kind": "transferor_type",
            "match_field": "transferor",
            "target_field": "source_type",
        }
        for field_name in (
            "source_name",
            "target_value",
            "rule_kind",
            "match_field",
            "target_field",
            "group_name",
            "source_type",
            "notes",
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    normalize_mapping_request({**base_payload, field_name: {"value": "x"}})

        with self.assertRaisesRegex(ValueError, "company_name"):
            normalize_mapping_request({**base_payload, "source_name": "", "company_name": {"value": "x"}})

        with self.assertRaisesRegex(ValueError, "company_name"):
            normalize_mapping_request({**base_payload, "company_name": {"value": "x"}})

        with self.assertRaisesRegex(ValueError, "company_name"):
            normalize_mapping_request({**base_payload, "company_name": "另一个来源"})

    def test_normalize_mapping_request_rejects_non_string_entry_id_instead_of_stringifying_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "entry_id"):
            normalize_mapping_request(
                {
                    "entry_id": {"id": "entry-1"},
                    "source_name": "测试来源",
                    "target_value": "央企",
                }
            )

        with self.assertRaisesRegex(ValueError, "entry_id"):
            normalize_mapping_update_request(
                "entry-1",
                {
                    "entry_id": {"id": "entry-1"},
                    "source_name": "测试来源",
                    "target_value": "央企",
                },
            )

    def test_normalize_mapping_request_keeps_target_value_for_source_type_rules(self) -> None:
        normalized = normalize_mapping_payload(
            {
                "rule_kind": "group_type",
                "source_name": "上海电气集团",
                "target_value": "市属",
            }
        )
        payload = normalize_mapping_request(
            {
                "rule_kind": "group_type",
                "source_name": "上海电气集团",
                "target_value": "市属",
            }
        )

        self.assertEqual(normalized["source_type"], "市属")
        self.assertEqual(payload["target_field"], "source_type")
        self.assertEqual(payload["target_value"], "市属")

    def test_normalize_mapping_request_rejects_source_type_outside_four_type_taxonomy(self) -> None:
        with self.assertRaisesRegex(ValueError, "source_type"):
            normalize_mapping_request(
                {
                    "rule_kind": "group_type",
                    "source_name": "某研究院",
                    "target_value": "科研院所",
                }
            )

    def test_normalize_mapping_conflict_request_rejects_invalid_confirm_overwrite_instead_of_defaulting_false(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirm_overwrite"):
            normalize_mapping_conflict_request(
                {
                    "record_id": "rec-1",
                    "selected_resolution": {
                        "source_name": "测试来源",
                        "target_value": "央企",
                    },
                    "confirm_overwrite": "not-a-bool",
                }
            )

    def test_normalize_mapping_conflict_request_rejects_non_string_record_and_resolution_fields(self) -> None:
        base_payload = {
            "record_id": "rec-1",
            "selected_resolution": {
                "source_name": "测试来源",
                "target_value": "央企",
                "rule_kind": "transferor_type",
                "match_field": "transferor",
                "target_field": "source_type",
            },
        }
        with self.assertRaisesRegex(ValueError, "record_id"):
            normalize_mapping_conflict_request({**base_payload, "record_id": {"id": "rec-1"}})

        for field_name in (
            "source_name",
            "target_value",
            "rule_kind",
            "match_field",
            "target_field",
            "group_name",
            "source_type",
            "notes",
        ):
            with self.subTest(field_name=field_name):
                payload = {
                    **base_payload,
                    "selected_resolution": {
                        **base_payload["selected_resolution"],
                        field_name: {"value": "x"},
                    },
                }
                with self.assertRaisesRegex(ValueError, field_name):
                    normalize_mapping_conflict_request(payload)

    def test_normalize_path_open_request_rejects_invalid_reveal_instead_of_defaulting_false(self) -> None:
        with self.assertRaisesRegex(ValueError, "reveal"):
            normalize_path_open_request({"path": "/tmp/example", "reveal": "not-a-bool"})

    def test_normalize_path_open_request_rejects_non_string_path_instead_of_stringifying_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "path"):
            normalize_path_open_request({"path": {"path": "/tmp/example"}})

    def test_normalize_path_selection_request_rejects_non_string_current_path_instead_of_stringifying_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "current_path"):
            normalize_path_selection_request({"current_path": {"path": "/tmp/example"}})

    def test_normalize_path_selection_request_rejects_non_string_kind_and_prompt(self) -> None:
        with self.assertRaisesRegex(ValueError, "selection_kind"):
            normalize_path_selection_request({"selection_kind": {"kind": "file"}})

        with self.assertRaisesRegex(ValueError, "prompt"):
            normalize_path_selection_request({"prompt": {"text": "选择文件"}})

    def test_normalize_mapping_business_re_evaluation_rejects_invalid_record_ids_shape(self) -> None:
        with self.assertRaisesRegex(ValueError, "record_ids"):
            normalize_mapping_business_re_evaluation_request({"record_ids": {"record_id": "rec-1"}})

        with self.assertRaisesRegex(ValueError, "record_ids"):
            normalize_mapping_business_re_evaluation_request({"record_ids": ["rec-1", {"record_id": "rec-2"}]})

    def test_normalize_mapping_undo_rejects_non_string_startup_session_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "startup_session_id"):
            normalize_mapping_undo_request({"startup_session_id": {"id": "startup-session-a"}})


if __name__ == "__main__":
    unittest.main()
