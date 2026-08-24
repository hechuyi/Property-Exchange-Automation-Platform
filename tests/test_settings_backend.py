from __future__ import annotations

import unittest

from desktop_backend.app_backend import dispatch_api_request
from desktop_backend.product_errors import UserInputError
from desktop_backend.settings_contract import build_advanced_settings_view


class FakeSettingsService:
    def __init__(self) -> None:
        self.last_basic_settings_payload = None
        self.last_advanced_settings_payload = None

    def get_basic_settings(self) -> dict[str, object]:
        return {
            "effective_default_scope": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "sse",
            },
            "stored_preference": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "sse",
            },
            "stale_default_metadata": {
                "is_stale": False,
                "reason": "",
            },
            "default_exchange": "sse",
            "default_concurrency": 4,
            "workspace_root": "/tmp/workspace",
            "archive_root": "/tmp/archive",
            "export_root": "/tmp/export",
        }

    def get_advanced_settings(self) -> dict[str, object]:
        return {
            "effective_default_scope": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "business_label": "股权转让",
                "exchange": "sse",
            },
            "stored_preference": {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "sse",
            },
            "stale_default_metadata": {
                "is_stale": False,
                "reason": "",
            },
            "save_json": False,
            "postprocess_config": "/tmp/postprocess.json",
            "raw_manual_root": "/tmp/manual",
            "raw_auto_root": "/tmp/archive",
            "app_home": "/tmp/workspace",
            "streaming_db": "/tmp/streaming.sqlite3",
            "log_dir": "/tmp/logs",
            "cache_dir": "/tmp/cache",
            "browser_cache_dir": "/tmp/browser-cache",
            "archive_root": "/tmp/archive",
            "export_root": "/tmp/export",
        }

    def set_basic_settings(self, payload: dict[str, object]) -> dict[str, object]:
        self.last_basic_settings_payload = payload
        stored_preference = payload.get("stored_preference")
        if isinstance(stored_preference, dict) and stored_preference.get("business_id") == "not_a_real_business":
            raise UserInputError("invalid stored default-preference combination")
        return self.get_basic_settings()

    def set_advanced_settings(self, payload: dict[str, object]) -> dict[str, object]:
        self.last_advanced_settings_payload = payload
        return self.get_advanced_settings()


class SettingsBackendTests(unittest.TestCase):
    def _assert_ok(self, payload: dict[str, object]) -> dict[str, object]:
        self.assertTrue(payload["ok"])
        self.assertIn("data", payload)
        return payload["data"]  # type: ignore[return-value]

    def test_basic_and_advanced_settings_expose_one_effective_default_scope_object(self) -> None:
        service = FakeSettingsService()

        basic_status, basic_payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/settings/basic",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )
        advanced_status, advanced_payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="GET",
            path="/api/settings/advanced",
            headers={"X-PEAP-Desktop-Token": "test-token"},
            api_token="test-token",
        )

        self.assertEqual(basic_status, 200)
        self.assertEqual(advanced_status, 200)

        basic = self._assert_ok(basic_payload)
        advanced = self._assert_ok(advanced_payload)

        self.assertIn("effective_default_scope", basic)
        self.assertIn("effective_default_scope", advanced)
        self.assertEqual(basic["effective_default_scope"], advanced["effective_default_scope"])
        self.assertEqual(basic["effective_default_scope"]["record_family"], "listing")
        self.assertEqual(basic["effective_default_scope"]["business_id"], "equity_transfer")
        self.assertEqual(basic["stored_preference"]["business_id"], "equity_transfer")
        self.assertNotIn("default_project_type", basic)

    def test_advanced_settings_view_parses_string_false_save_json_instead_of_truthy_bool(self) -> None:
        payload = build_advanced_settings_view({"save_json": "false"})

        self.assertFalse(payload["processing"]["save_json"])

    def test_invalid_basic_settings_write_returns_bad_request(self) -> None:
        service = FakeSettingsService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="POST",
            path="/api/settings/basic",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "stored_preference": {
                    "record_family": "listing",
                    "business_id": "not_a_real_business",
                    "exchange": "sse",
                }
            },
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertIsNone(service.last_basic_settings_payload)

    def test_basic_settings_route_rejects_server_owned_default_scope_fields(self) -> None:
        service = FakeSettingsService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="POST",
            path="/api/settings/basic",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={
                "stored_preference": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "sse",
                },
                "effective_default_scope": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "sse",
                },
            },
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertIn("effective_default_scope", payload["error"]["message"])
        self.assertIsNone(service.last_basic_settings_payload)

    def test_basic_settings_route_rejects_non_object_stored_preference_instead_of_clearing_scope(self) -> None:
        for body in (
            {"stored_preference": "clear"},
            {"default_scope": {"stored_preference": "clear"}},
        ):
            with self.subTest(body=body):
                service = FakeSettingsService()

                status, payload = dispatch_api_request(
                    service,  # type: ignore[arg-type]
                    method="POST",
                    path="/api/settings/basic",
                    headers={
                        "X-PEAP-Desktop-Token": "test-token",
                        "Content-Type": "application/json",
                    },
                    body=body,
                    api_token="test-token",
                )

                self.assertEqual(status, 400)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "invalid_request")
                self.assertIn("stored_preference", payload["error"]["message"])
                self.assertIsNone(service.last_basic_settings_payload)

    def test_basic_settings_route_rejects_invalid_default_concurrency_instead_of_forwarding_bad_default(self) -> None:
        service = FakeSettingsService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="POST",
            path="/api/settings/basic",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"defaults": {"default_concurrency": "not-a-number"}},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertIn("default_concurrency", payload["error"]["message"])
        self.assertIsNone(service.last_basic_settings_payload)

    def test_basic_settings_route_rejects_non_string_default_exchange_before_forwarding(self) -> None:
        service = FakeSettingsService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="POST",
            path="/api/settings/basic",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"defaults": {"default_exchange": {"exchange": "sse"}}},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertIn("default_exchange", payload["error"]["message"])
        self.assertIsNone(service.last_basic_settings_payload)

    def test_basic_settings_route_rejects_non_string_directory_fields_before_forwarding(self) -> None:
        for field_name in ("archive_root", "export_root"):
            with self.subTest(field_name=field_name):
                service = FakeSettingsService()

                status, payload = dispatch_api_request(
                    service,  # type: ignore[arg-type]
                    method="POST",
                    path="/api/settings/basic",
                    headers={
                        "X-PEAP-Desktop-Token": "test-token",
                        "Content-Type": "application/json",
                    },
                    body={"paths": {field_name: {"path": "/tmp/example"}}},
                    api_token="test-token",
                )

                self.assertEqual(status, 400)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "invalid_request")
                self.assertIn(field_name, payload["error"]["message"])
                self.assertIsNone(service.last_basic_settings_payload)

    def test_advanced_settings_route_rejects_invalid_save_json_instead_of_defaulting(self) -> None:
        service = FakeSettingsService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="POST",
            path="/api/settings/advanced",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"processing": {"save_json": "not-a-bool"}},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertIn("save_json", payload["error"]["message"])
        self.assertIsNone(service.last_advanced_settings_payload)

    def test_advanced_settings_route_rejects_non_string_postprocess_config_before_forwarding(self) -> None:
        service = FakeSettingsService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="POST",
            path="/api/settings/advanced",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"processing": {"postprocess_config": {"path": "/tmp/postprocess.json"}}},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertIn("postprocess_config", payload["error"]["message"])
        self.assertIsNone(service.last_advanced_settings_payload)

    def test_advanced_settings_route_rejects_non_string_raw_manual_root_before_forwarding(self) -> None:
        service = FakeSettingsService()

        status, payload = dispatch_api_request(
            service,  # type: ignore[arg-type]
            method="POST",
            path="/api/settings/advanced",
            headers={
                "X-PEAP-Desktop-Token": "test-token",
                "Content-Type": "application/json",
            },
            body={"ingest_paths": {"raw_manual_root": {"path": "/tmp/manual"}}},
            api_token="test-token",
        )

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertIn("raw_manual_root", payload["error"]["message"])
        self.assertIsNone(service.last_advanced_settings_payload)


if __name__ == "__main__":
    unittest.main()
