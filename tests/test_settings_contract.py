from __future__ import annotations

import unittest

from desktop_backend.settings_contract import (
    build_basic_settings_view,
    normalize_advanced_settings_update,
    normalize_basic_settings_update,
)


class SettingsContractTests(unittest.TestCase):
    def test_basic_settings_view_rejects_bad_service_result_instead_of_defaulting(self) -> None:
        with self.assertRaisesRegex(ValueError, "settings payload"):
            build_basic_settings_view(False)  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, "default_concurrency"):
            build_basic_settings_view({"default_concurrency": "not-a-number"})

        with self.assertRaisesRegex(ValueError, "retention_count"):
            build_basic_settings_view({"retention_count": 0})

        with self.assertRaisesRegex(ValueError, "default_scope"):
            build_basic_settings_view({"default_scope": "invalid"})

    def test_basic_settings_view_does_not_legacy_fallback_explicit_empty_scope_fields(self) -> None:
        view = build_basic_settings_view(
            {
                "effective_default_scope": {},
                "stored_preference": {},
                "stale_default_metadata": {},
                "default_scope": {
                    "effective_scope": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "business_label": "产权转让",
                        "exchange": "cbex",
                    },
                    "stored_preference": {
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "business_label": "产权转让",
                        "exchange": "cbex",
                    },
                    "stale_resolution": {
                        "is_stale": True,
                        "reason": "legacy",
                        "hint": "legacy",
                    },
                },
            }
        )

        self.assertEqual(
            view["effective_default_scope"],
            {"record_family": "", "business_id": "", "business_label": "", "exchange": ""},
        )
        self.assertEqual(
            view["stored_preference"],
            {"record_family": "", "business_id": "", "business_label": "", "exchange": ""},
        )
        self.assertEqual(
            view["stale_default_metadata"],
            {"is_stale": False, "reason": "", "hint": ""},
        )

    def test_basic_settings_view_rejects_bad_explicit_scope_fields_before_legacy_fallback(self) -> None:
        payload = {
            "default_scope": {
                "effective_scope": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "business_label": "产权转让",
                    "exchange": "cbex",
                },
                "stored_preference": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "business_label": "产权转让",
                    "exchange": "cbex",
                },
                "stale_resolution": {
                    "is_stale": True,
                    "reason": "legacy",
                    "hint": "legacy",
                },
            },
        }
        for field_name in ("effective_default_scope", "stored_preference", "stale_default_metadata"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    build_basic_settings_view({**payload, field_name: False})

    def test_basic_settings_update_rejects_false_payload_instead_of_empty_patch(self) -> None:
        with self.assertRaisesRegex(ValueError, "settings payload"):
            normalize_basic_settings_update(False)  # type: ignore[arg-type]

    def test_advanced_settings_update_rejects_false_payload_instead_of_empty_patch(self) -> None:
        with self.assertRaisesRegex(ValueError, "settings payload"):
            normalize_advanced_settings_update(False)  # type: ignore[arg-type]

    def test_basic_settings_update_rejects_corrupt_nested_containers_instead_of_empty_patch(self) -> None:
        cases = (
            ({"defaults": False}, "defaults"),
            ({"paths": []}, "paths"),
            ({"default_scope": "bad"}, "default_scope"),
        )
        for payload, field_name in cases:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, f"{field_name} must be an object"):
                    normalize_basic_settings_update(payload)  # type: ignore[arg-type]

    def test_advanced_settings_update_rejects_corrupt_nested_containers_instead_of_empty_patch(self) -> None:
        cases = (
            ({"processing": []}, "processing"),
            ({"ingest_paths": "bad"}, "ingest_paths"),
        )
        for payload, field_name in cases:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, f"{field_name} must be an object"):
                    normalize_advanced_settings_update(payload)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
