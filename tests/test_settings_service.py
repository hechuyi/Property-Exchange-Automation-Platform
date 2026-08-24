from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from desktop_backend.app_config import AppConfig
from desktop_backend.product_errors import UserInputError
from desktop_backend.repositories.pipeline_repository import PipelineRepository
from desktop_backend.services.settings_service import (
    SettingsService,
    _stored_preference_from_payload,
)
from peap.streaming_store import StreamingStore


class FakeSettingsRepository:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}
        self.audit_log: list[tuple[str, dict[str, object]]] = []

    def get_setting(self, key: str, *, default: dict[str, object]) -> dict[str, object]:
        return dict(self.values.get(key, default))

    def set_setting(self, key: str, value: dict[str, object]) -> None:
        self.values[key] = dict(value)

    def add_audit_entry(self, action: str, payload: dict[str, object]) -> None:
        self.audit_log.append((action, dict(payload)))


class SettingsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app_home = os.path.join(self.temp_dir.name, "app_home")
        self.docs_home = os.path.join(self.temp_dir.name, "docs_home")
        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": self.app_home,
                "PEAP_DOCUMENTS_HOME": self.docs_home,
            },
            clear=False,
        ):
            self.config = AppConfig.from_env(project_root=self.temp_dir.name)
        self.repository = FakeSettingsRepository()
        self.service = SettingsService(
            config_obj=self.config,
            repository=self.repository,
            app_home=self.app_home,
            default_archive_root=self.config.ARCHIVE_ROOT,
            default_export_root=self.config.OUTPUT_EXCEL_DIR,
        )

    def test_stored_preference_payload_rejects_non_mapping_instead_of_defaulting(self) -> None:
        self.assertEqual(_stored_preference_from_payload(None), {})

        for payload in ([], [("business_id", "equity_transfer")]):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "payload must be an object"):
                    _stored_preference_from_payload(payload)  # type: ignore[arg-type]

    def test_get_basic_settings_exposes_effective_default_scope_object(self) -> None:
        payload = self.service.get_basic_settings()

        self.assertIn("effective_default_scope", payload)
        self.assertIn("stored_preference", payload)
        self.assertIn("stale_default_metadata", payload)
        self.assertEqual(payload["retention_count"], 20)
        self.assertNotIn("default_project_type", payload)

    def test_set_basic_settings_validates_positive_export_retention_count(self) -> None:
        updated = self.service.set_basic_settings({"retention_count": 7})

        self.assertEqual(updated["retention_count"], 7)
        self.assertEqual(self.repository.values[self.service._basic_settings_key()]["retention_count"], 7)

        with self.assertRaises(UserInputError):
            self.service.set_basic_settings({"retention_count": 0})

        self.assertEqual(self.repository.values[self.service._basic_settings_key()]["retention_count"], 7)

    def test_set_basic_settings_rejects_invalid_default_concurrency_instead_of_persisting_bad_default(self) -> None:
        with self.assertRaisesRegex(ValueError, "default_concurrency"):
            self.service.set_basic_settings({"default_concurrency": "not-a-number"})

        self.assertNotIn(self.service._basic_settings_key(), self.repository.values)

    def test_set_basic_settings_rejects_non_positive_default_concurrency(self) -> None:
        with self.assertRaisesRegex(ValueError, "default_concurrency"):
            self.service.set_basic_settings({"default_concurrency": 0})

        self.assertNotIn(self.service._basic_settings_key(), self.repository.values)

    def test_set_basic_settings_rejects_false_payload_instead_of_persisting_empty_patch(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload"):
            self.service.set_basic_settings(False)

        self.assertNotIn(self.service._basic_settings_key(), self.repository.values)

    def test_get_basic_settings_seeds_actionable_all_scope_defaults(self) -> None:
        payload = self.service.get_basic_settings()

        self.assertEqual(
            payload["stored_preference"],
            {
                "record_family": "listing",
                "business_id": "all",
                "exchange": "all",
            },
        )
        self.assertEqual(
            payload["effective_default_scope"],
            {
                "record_family": "listing",
                "business_id": "all",
                "business_label": "",
                "exchange": "all",
            },
        )

    def test_set_basic_settings_persists_stored_preference_instead_of_legacy_project_type(self) -> None:
        updated = self.service.set_basic_settings(
            {
                "stored_preference": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "sse",
                }
            }
        )

        stored = self.repository.values[self.service._basic_settings_key()]
        self.assertIn("stored_preference", stored)
        self.assertEqual(stored["stored_preference"]["business_id"], "equity_transfer")
        self.assertIn("effective_default_scope", updated)
        self.assertEqual(updated["effective_default_scope"]["business_id"], "equity_transfer")
        self.assertNotIn("default_project_type", updated)

    def test_set_basic_settings_rejects_invalid_stored_preference_combination(self) -> None:
        with self.assertRaises(UserInputError):
            self.service.set_basic_settings(
                {
                    "stored_preference": {
                        "record_family": "listing",
                        "business_id": "not_a_real_business",
                        "exchange": "sse",
                    }
                }
            )

        self.assertNotIn(self.service._basic_settings_key(), self.repository.values)

    def test_get_basic_settings_preserves_stale_metadata_for_invalid_stored_preference(self) -> None:
        self.repository.values[self.service._basic_settings_key()] = {
            "stored_preference": {
                "record_family": "listing",
                "business_id": "not_a_real_business",
                "exchange": "sse",
            },
            "default_exchange": "sse",
            "default_concurrency": 4,
            "archive_root": self.config.ARCHIVE_ROOT,
            "export_root": self.config.OUTPUT_EXCEL_DIR,
            "workspace_root": self.app_home,
        }

        payload = self.service.get_basic_settings()

        self.assertTrue(payload["stale_default_metadata"]["is_stale"])
        self.assertEqual(payload["stale_default_metadata"]["reason"], "unknown_business_id")
        self.assertEqual(payload["effective_default_scope"], {})
        self.assertEqual(payload["stored_preference"]["business_id"], "not_a_real_business")

    def test_get_basic_settings_marks_invalid_exchange_as_stale_without_publishing_effective_default_scope(self) -> None:
        self.repository.values[self.service._basic_settings_key()] = {
            "stored_preference": {
                "record_family": "listing",
                "business_id": "physical_asset",
                "exchange": "not_a_real_exchange",
            },
            "default_exchange": "all",
            "default_concurrency": 4,
            "archive_root": self.config.ARCHIVE_ROOT,
            "export_root": self.config.OUTPUT_EXCEL_DIR,
            "workspace_root": self.app_home,
        }

        payload = self.service.get_basic_settings()

        self.assertTrue(payload["stale_default_metadata"]["is_stale"])
        self.assertEqual(payload["stale_default_metadata"]["reason"], "invalid_exchange")
        self.assertEqual(payload["effective_default_scope"], {})

    def test_set_basic_settings_rejects_invalid_exchange_in_stored_preference(self) -> None:
        with self.assertRaises(UserInputError):
            self.service.set_basic_settings(
                {
                    "stored_preference": {
                        "record_family": "listing",
                        "business_id": "physical_asset",
                        "exchange": "not_a_real_exchange",
                    }
                }
            )

        self.assertNotIn(self.service._basic_settings_key(), self.repository.values)

    def test_set_basic_settings_rejects_surface_unsupported_stored_preference(self) -> None:
        with self.assertRaises(UserInputError):
            self.service.set_basic_settings(
                {
                    "stored_preference": {
                        "record_family": "deal",
                        "business_id": "deal_physical_asset",
                        "exchange": "tpre",
                    }
                }
            )

        self.assertNotIn(self.service._basic_settings_key(), self.repository.values)

    def test_set_basic_settings_rejects_corrupt_stored_json_without_repairing_setting(self) -> None:
        store = StreamingStore(f"{self.temp_dir.name}/settings-corrupt.sqlite3", auto_migrate=True)
        service = SettingsService(
            config_obj=self.config,
            repository=PipelineRepository(store=store),
            app_home=self.app_home,
            default_archive_root=self.config.ARCHIVE_ROOT,
            default_export_root=self.config.OUTPUT_EXCEL_DIR,
        )
        service.set_basic_settings({"retention_count": 5})
        with sqlite3.connect(store.db_path) as conn:
            before_revision_count = conn.execute("SELECT COUNT(*) FROM settings_revisions").fetchone()[0]
            before_audit_count = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'settings_basic_updated'"
            ).fetchone()[0]
            conn.execute(
                "UPDATE settings SET value_json = ? WHERE key = ?",
                ("{not valid json", service._basic_settings_key()),
            )

        with self.assertRaises(UserInputError):
            service.set_basic_settings({"default_exchange": "sse"})

        with sqlite3.connect(store.db_path) as conn:
            stored_json = conn.execute(
                "SELECT value_json FROM settings WHERE key = ?",
                (service._basic_settings_key(),),
            ).fetchone()[0]
            after_revision_count = conn.execute("SELECT COUNT(*) FROM settings_revisions").fetchone()[0]
            after_audit_count = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'settings_basic_updated'"
            ).fetchone()[0]
        self.assertEqual(stored_json, "{not valid json")
        self.assertEqual(after_revision_count, before_revision_count)
        self.assertEqual(after_audit_count, before_audit_count)

    def test_set_basic_settings_rejects_corrupt_stale_marker_without_clearing_marker(self) -> None:
        marker = {
            "is_stale": True,
            "reason": "settings_payload_corrupt",
            "hint": "repair or reset settings from a valid runtime configuration",
        }
        self.repository.values[self.service._basic_settings_key()] = {
            "stale_default_metadata": dict(marker),
            "default_exchange": "all",
            "default_concurrency": 4,
            "archive_root": self.config.ARCHIVE_ROOT,
            "export_root": self.config.OUTPUT_EXCEL_DIR,
            "workspace_root": self.app_home,
        }

        with self.assertRaises(UserInputError):
            self.service.set_basic_settings({"retention_count": 7})

        stored = self.repository.values[self.service._basic_settings_key()]
        self.assertEqual(stored["stale_default_metadata"], marker)
        self.assertEqual(self.repository.audit_log, [])

    def test_set_basic_settings_default_exchange_patch_does_not_mutate_existing_stored_preference(self) -> None:
        self.service.set_basic_settings(
            {
                "stored_preference": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "sse",
                }
            }
        )

        updated = self.service.set_basic_settings({"default_exchange": "cbex"})

        self.assertEqual(updated["default_exchange"], "cbex")
        self.assertEqual(
            updated["stored_preference"],
            {
                "record_family": "listing",
                "business_id": "equity_transfer",
                "exchange": "sse",
            },
        )
        self.assertEqual(updated["effective_default_scope"]["exchange"], "sse")

    def test_set_basic_settings_rejects_non_string_default_exchange_instead_of_persisting_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "default_exchange"):
            self.service.set_basic_settings({"default_exchange": {"exchange": "sse"}})

        self.assertNotIn(self.service._basic_settings_key(), self.repository.values)

    def test_set_basic_settings_rejects_non_string_directory_fields_before_mkdir(self) -> None:
        for field_name in ("archive_root", "export_root"):
            with self.subTest(field_name=field_name):
                with (
                    patch("desktop_backend.domain.normalizers.os.makedirs") as mocked_makedirs,
                    patch("desktop_backend.domain.normalizers.os.path.isdir", return_value=True),
                    self.assertRaisesRegex((ValueError, UserInputError), field_name),
                ):
                    self.service.set_basic_settings({field_name: {"path": self.temp_dir.name}})

                mocked_makedirs.assert_not_called()
                self.assertNotIn(self.service._basic_settings_key(), self.repository.values)

    def test_archive_root_change_derives_deal_archive_root_when_not_explicit(self) -> None:
        archive_root = os.path.join(self.temp_dir.name, "archive-v2")

        updated = self.service.set_basic_settings({"archive_root": archive_root})

        self.assertEqual(updated["archive_root"], archive_root)
        self.assertEqual(updated["deal_archive_root"], os.path.join(archive_root, "deal"))
        self.assertEqual(
            self.repository.values[self.service._basic_settings_key()]["deal_archive_root"],
            os.path.join(archive_root, "deal"),
        )

    def test_archive_root_change_preserves_explicit_deal_archive_root(self) -> None:
        explicit_deal_root = os.path.join(self.temp_dir.name, "independent-deals")
        self.service.set_basic_settings({"deal_archive_root": explicit_deal_root})

        updated = self.service.set_basic_settings(
            {"archive_root": os.path.join(self.temp_dir.name, "archive-v3")}
        )

        self.assertEqual(updated["deal_archive_root"], explicit_deal_root)

    def test_get_basic_settings_rejects_corrupt_present_path_fields_instead_of_using_defaults(self) -> None:
        for field_name in ("archive_root", "deal_archive_root", "export_root"):
            for bad_value in (False, "", {"path": self.temp_dir.name}):
                with self.subTest(field_name=field_name, bad_value=bad_value):
                    self.repository.values[self.service._basic_settings_key()] = {
                        "default_exchange": "all",
                        "default_concurrency": 4,
                        "archive_root": self.config.ARCHIVE_ROOT,
                        "deal_archive_root": os.path.join(self.config.ARCHIVE_ROOT, "deal"),
                        "export_root": self.config.OUTPUT_EXCEL_DIR,
                        "workspace_root": self.app_home,
                    }
                    self.repository.values[self.service._basic_settings_key()][field_name] = bad_value

                    with self.assertRaisesRegex(ValueError, field_name):
                        self.service.get_basic_settings()

    def test_set_basic_settings_allows_explicit_empty_stored_preference_to_clear_existing_scope(self) -> None:
        self.service.set_basic_settings(
            {
                "stored_preference": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "sse",
                }
            }
        )

        updated = self.service.set_basic_settings(
            {
                "stored_preference": {},
                "default_exchange": "cbex",
            }
        )

        self.assertEqual(updated["default_exchange"], "cbex")
        self.assertEqual(updated["stored_preference"], {})
        self.assertEqual(updated["effective_default_scope"], {})

    def test_set_basic_settings_rejects_non_object_stored_preference_instead_of_clearing_scope(self) -> None:
        self.service.set_basic_settings(
            {
                "stored_preference": {
                    "record_family": "listing",
                    "business_id": "equity_transfer",
                    "exchange": "sse",
                }
            }
        )

        for payload in (
            {"stored_preference": "clear"},
            {"default_scope": {"stored_preference": "clear"}},
        ):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "stored_preference"):
                    self.service.set_basic_settings(payload)

                stored = self.repository.values[self.service._basic_settings_key()]
                self.assertEqual(stored["stored_preference"]["business_id"], "equity_transfer")

    def test_set_basic_settings_accepts_all_business_default_scope(self) -> None:
        updated = self.service.set_basic_settings(
            {
                "stored_preference": {
                    "record_family": "listing",
                    "business_id": "all",
                    "exchange": "all",
                }
            }
        )

        self.assertEqual(
            updated["stored_preference"],
            {
                "record_family": "listing",
                "business_id": "all",
                "exchange": "all",
            },
        )
        self.assertEqual(updated["effective_default_scope"]["business_id"], "all")
        self.assertEqual(updated["effective_default_scope"]["exchange"], "all")

    def test_get_basic_settings_marks_invalid_record_family_as_stale(self) -> None:
        self.repository.values[self.service._basic_settings_key()] = {
            "stored_preference": {
                "record_family": "legacy_family",
                "business_id": "equity_transfer",
                "exchange": "sse",
            },
            "default_exchange": "all",
            "default_concurrency": 4,
            "archive_root": self.config.ARCHIVE_ROOT,
            "export_root": self.config.OUTPUT_EXCEL_DIR,
            "workspace_root": self.app_home,
        }

        payload = self.service.get_basic_settings()

        self.assertTrue(payload["stale_default_metadata"]["is_stale"])
        self.assertEqual(payload["stale_default_metadata"]["reason"], "unknown_record_family")
        self.assertEqual(payload["effective_default_scope"], {})
        self.assertEqual(payload["stored_preference"]["record_family"], "legacy_family")

    def test_get_basic_settings_marks_surface_unsupported_scope_as_stale(self) -> None:
        self.repository.values[self.service._basic_settings_key()] = {
            "stored_preference": {
                "record_family": "deal",
                "business_id": "deal_physical_asset",
                "exchange": "tpre",
            },
            "default_exchange": "all",
            "default_concurrency": 4,
            "archive_root": self.config.ARCHIVE_ROOT,
            "export_root": self.config.OUTPUT_EXCEL_DIR,
            "workspace_root": self.app_home,
        }

        payload = self.service.get_basic_settings()

        self.assertTrue(payload["stale_default_metadata"]["is_stale"])
        self.assertEqual(payload["stale_default_metadata"]["reason"], "unsupported_default_scope")
        self.assertEqual(payload["effective_default_scope"], {})
        self.assertEqual(payload["stored_preference"]["business_id"], "deal_physical_asset")

    def test_get_basic_settings_marks_corrupt_stored_json_as_stale(self) -> None:
        self.repository.values[self.service._basic_settings_key()] = {
            "__peap_settings_decode_error__": "invalid_json",
            "default_exchange": "all",
            "default_concurrency": 4,
            "archive_root": self.config.ARCHIVE_ROOT,
            "export_root": self.config.OUTPUT_EXCEL_DIR,
            "workspace_root": self.app_home,
        }

        payload = self.service.get_basic_settings()

        self.assertTrue(payload["stale_default_metadata"]["is_stale"])
        self.assertEqual(payload["stale_default_metadata"]["reason"], "settings_payload_corrupt")
        self.assertIn("repair or reset settings from a valid runtime configuration", payload["stale_default_metadata"]["hint"])
        self.assertEqual(payload["stored_preference"], {})
        self.assertEqual(payload["effective_default_scope"], {})

    def test_get_basic_settings_keeps_corrupt_stale_marker_from_publishing_default_scope(self) -> None:
        self.repository.values[self.service._basic_settings_key()] = {
            "stale_default_metadata": {
                "is_stale": True,
                "reason": "settings_payload_corrupt",
                "hint": "repair or reset settings from a valid runtime configuration",
            },
            "default_exchange": "all",
            "default_concurrency": 4,
            "archive_root": self.config.ARCHIVE_ROOT,
            "export_root": self.config.OUTPUT_EXCEL_DIR,
            "workspace_root": self.app_home,
        }

        payload = self.service.get_basic_settings()

        self.assertTrue(payload["stale_default_metadata"]["is_stale"])
        self.assertEqual(payload["stale_default_metadata"]["reason"], "settings_payload_corrupt")
        self.assertEqual(payload["stored_preference"], {})
        self.assertEqual(payload["effective_default_scope"], {})

    def test_get_basic_settings_marks_non_object_persisted_stored_preference_as_stale(self) -> None:
        cases = (
            {"stored_preference": "clear"},
            {"default_scope": {"stored_preference": "clear"}},
        )
        for corrupt_scope_payload in cases:
            with self.subTest(corrupt_scope_payload=corrupt_scope_payload):
                self.repository.values[self.service._basic_settings_key()] = {
                    **corrupt_scope_payload,
                    "default_exchange": "all",
                    "default_concurrency": 4,
                    "archive_root": self.config.ARCHIVE_ROOT,
                    "export_root": self.config.OUTPUT_EXCEL_DIR,
                    "workspace_root": self.app_home,
                }

                payload = self.service.get_basic_settings()

                self.assertTrue(payload["stale_default_metadata"]["is_stale"])
                self.assertEqual(payload["stale_default_metadata"]["reason"], "settings_payload_corrupt")
                self.assertEqual(payload["stored_preference"], {})
                self.assertEqual(payload["effective_default_scope"], {})

    def test_set_advanced_settings_rejects_invalid_basic_default_state(self) -> None:
        self.repository.values[self.service._basic_settings_key()] = {
            "stored_preference": {
                "record_family": "listing",
                "business_id": "not_a_real_business",
                "exchange": "sse",
            },
            "default_exchange": "sse",
            "default_concurrency": 4,
            "archive_root": self.config.ARCHIVE_ROOT,
            "export_root": self.config.OUTPUT_EXCEL_DIR,
            "workspace_root": self.app_home,
        }

        with self.assertRaises(UserInputError):
            self.service.set_advanced_settings({"save_json": True})

        self.assertNotIn(self.service._advanced_settings_key(), self.repository.values)

    def test_set_advanced_settings_rejects_false_payload_instead_of_persisting_empty_patch(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload"):
            self.service.set_advanced_settings(False)

        self.assertNotIn(self.service._advanced_settings_key(), self.repository.values)

    def test_set_advanced_settings_parses_string_false_save_json_instead_of_storing_truthy_string(self) -> None:
        updated = self.service.set_advanced_settings({"save_json": "false"})

        self.assertFalse(updated["save_json"])
        self.assertFalse(self.repository.values[self.service._advanced_settings_key()]["save_json"])

    def test_set_advanced_settings_rejects_invalid_save_json_instead_of_defaulting(self) -> None:
        with self.assertRaisesRegex(ValueError, "save_json"):
            self.service.set_advanced_settings({"save_json": "not-a-bool"})

        self.assertNotIn(self.service._advanced_settings_key(), self.repository.values)

    def test_set_advanced_settings_rejects_non_string_postprocess_config_before_stringifying_object(self) -> None:
        with self.assertRaisesRegex(UserInputError, "postprocess_config must be a string"):
            self.service.set_advanced_settings({"postprocess_config": {"path": "/tmp/postprocess.json"}})

        self.assertNotIn(self.service._advanced_settings_key(), self.repository.values)

    def test_set_advanced_settings_rejects_non_string_raw_manual_root_before_mkdir(self) -> None:
        with (
            patch("desktop_backend.domain.normalizers.os.makedirs") as mocked_makedirs,
            patch("desktop_backend.domain.normalizers.os.path.isdir", return_value=True),
            self.assertRaisesRegex((ValueError, UserInputError), "raw_manual_root"),
        ):
            self.service.set_advanced_settings({"raw_manual_root": {"path": self.temp_dir.name}})

        mocked_makedirs.assert_not_called()
        self.assertNotIn(self.service._advanced_settings_key(), self.repository.values)

    def test_get_advanced_settings_forces_raw_auto_root_to_follow_archive_root(self) -> None:
        self.service.set_basic_settings(
            {
                "archive_root": os.path.join(self.temp_dir.name, "custom-archive"),
                "export_root": self.config.OUTPUT_EXCEL_DIR,
            }
        )
        self.repository.values[self.service._advanced_settings_key()] = {
            "raw_manual_root": os.path.join(self.temp_dir.name, "manual-root"),
            "raw_auto_root": os.path.join(self.temp_dir.name, "stale-auto-root"),
            "save_json": True,
        }

        payload = self.service.get_advanced_settings()

        self.assertEqual(payload["archive_root"], os.path.join(self.temp_dir.name, "custom-archive"))
        self.assertEqual(payload["raw_auto_root"], os.path.join(self.temp_dir.name, "custom-archive"))
        self.assertEqual(payload["raw_manual_root"], os.path.join(self.temp_dir.name, "manual-root"))

    def test_set_advanced_settings_does_not_allow_raw_auto_root_to_diverge_from_archive_root(self) -> None:
        archive_root = os.path.join(self.temp_dir.name, "canonical-archive")
        self.service.set_basic_settings(
            {
                "archive_root": archive_root,
                "export_root": self.config.OUTPUT_EXCEL_DIR,
            }
        )

        updated = self.service.set_advanced_settings(
            {
                "raw_manual_root": os.path.join(self.temp_dir.name, "manual-root"),
                "raw_auto_root": os.path.join(self.temp_dir.name, "ignored-auto-root"),
                "save_json": True,
            }
        )

        stored = self.repository.values[self.service._advanced_settings_key()]
        self.assertEqual(updated["raw_auto_root"], archive_root)
        self.assertEqual(stored["raw_auto_root"], archive_root)
        self.assertEqual(updated["raw_manual_root"], os.path.join(self.temp_dir.name, "manual-root"))


if __name__ == "__main__":
    unittest.main()
