from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_backend.app_config import AppConfig
from desktop_backend.app_service import AppService


class MappingsUndoSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.app_home = str(root / "app_home")
        self.docs_home = str(root / "docs_home")
        with patch.dict(
            os.environ,
            {
                "PEAP_APP_HOME": self.app_home,
                "PEAP_DOCUMENTS_HOME": self.docs_home,
            },
            clear=False,
        ):
            self.config = AppConfig.from_env(project_root=self.temp_dir.name)
        self.service = AppService(config_obj=self.config)

    def test_undo_last_operation_within_same_startup_session(self) -> None:
        self.service._mapping_undo_stack = [{"kind": "upsert", "entry_id": "entry-123"}]
        with patch.object(self.service, "delete_mapping", return_value={"deleted": True}) as delete_mapping:
            payload = self.service.undo_last_mapping_operation(startup_session_id=self.service.startup_session_id())
        self.assertTrue(payload["undone"])
        self.assertEqual(payload["undo_kind"], "upsert")
        delete_mapping.assert_called_once_with("entry-123", _record_undo=False)

    def test_undo_rejects_cross_startup_session(self) -> None:
        self.service._mapping_undo_stack = [{"kind": "upsert", "entry_id": "entry-123"}]
        with self.assertRaisesRegex(ValueError, "startup_session_id mismatch"):
            self.service.undo_last_mapping_operation(startup_session_id="other-session")

    def test_mapping_undo_state_exposes_only_current_session_capability(self) -> None:
        self.assertEqual(
            self.service.mapping_undo_state(),
            {
                "available": False,
                "startup_session_id": self.service.startup_session_id(),
                "operation_kind": "",
            },
        )
        self.service._mapping_undo_stack = [{"kind": "update", "current_entry_id": "entry-123"}]
        self.assertEqual(
            self.service.mapping_undo_state(),
            {
                "available": True,
                "startup_session_id": self.service.startup_session_id(),
                "operation_kind": "update",
            },
        )

    def test_failed_undo_keeps_operation_available_for_retry(self) -> None:
        operation = {"kind": "upsert", "entry_id": "entry-123"}
        self.service._mapping_undo_stack = [operation]
        with (
            patch.object(self.service, "delete_mapping", side_effect=RuntimeError("mapping refresh busy")),
            self.assertRaisesRegex(RuntimeError, "mapping refresh busy"),
        ):
            self.service.undo_last_mapping_operation(startup_session_id=self.service.startup_session_id())
        self.assertEqual(self.service._mapping_undo_stack, [operation])

    def test_undo_pops_latest_operation_only(self) -> None:
        self.service._mapping_undo_stack = [
            {"kind": "upsert", "entry_id": "entry-old"},
            {"kind": "upsert", "entry_id": "entry-new"},
        ]
        with patch.object(self.service, "delete_mapping", return_value={"deleted": True}) as delete_mapping:
            payload = self.service.undo_last_mapping_operation(startup_session_id=self.service.startup_session_id())
        self.assertEqual(payload["entry_id"], "entry-new")
        self.assertEqual(len(self.service._mapping_undo_stack), 1)
        self.assertEqual(self.service._mapping_undo_stack[0]["entry_id"], "entry-old")
        delete_mapping.assert_called_once_with("entry-new", _record_undo=False)

    def test_undo_update_restores_previous_entry_payload(self) -> None:
        previous_entry = {
            "rule_kind": "source",
            "source_name": "VendorName",
            "target_value": "Acme Realty",
            "notes": "keep canonical",
        }
        self.service._mapping_undo_stack = [
            {
                "kind": "update",
                "current_entry_id": "entry-456",
                "previous_entry": previous_entry,
            }
        ]
        with patch.object(self.service, "update_mapping", return_value={"entry_id": "entry-456"}) as update_mapping:
            payload = self.service.undo_last_mapping_operation(startup_session_id=self.service.startup_session_id())
        self.assertEqual(payload, {"undone": True, "undo_kind": "update", "entry_id": "entry-456"})
        update_mapping.assert_called_once_with(
            "entry-456",
            {
                "rule_kind": "source",
                "source_name": "VendorName",
                "target_value": "Acme Realty",
                "notes": "keep canonical",
                "confirm_overwrite": True,
            },
            _record_undo=False,
        )

    def test_undo_update_rejects_non_mapping_previous_entry(self) -> None:
        for previous_entry in (False, [], "not-a-mapping", None):
            with self.subTest(previous_entry=previous_entry):
                self.service._mapping_undo_stack = [
                    {
                        "kind": "update",
                        "current_entry_id": "entry-456",
                        "previous_entry": previous_entry,
                    }
                ]
                with (
                    patch.object(self.service, "update_mapping") as update_mapping,
                    self.assertRaisesRegex(ValueError, "previous_entry"),
                ):
                    self.service.undo_last_mapping_operation(startup_session_id=self.service.startup_session_id())
                update_mapping.assert_not_called()

    def test_undo_delete_recreates_deleted_entry_payload(self) -> None:
        deleted_entry = {
            "rule_kind": "business",
            "source_name": "PropA",
            "target_value": "Property A",
            "notes": "deleted by mistake",
        }
        self.service._mapping_undo_stack = [{"kind": "delete", "deleted_entry": deleted_entry}]
        with patch.object(self.service, "upsert_mapping", return_value={"entry_id": "entry-789"}) as upsert_mapping:
            payload = self.service.undo_last_mapping_operation(startup_session_id=self.service.startup_session_id())
        self.assertEqual(payload, {"undone": True, "undo_kind": "delete", "entry_id": "entry-789"})
        upsert_mapping.assert_called_once_with(
            {
                "rule_kind": "business",
                "source_name": "PropA",
                "target_value": "Property A",
                "notes": "deleted by mistake",
                "confirm_overwrite": True,
            },
            _record_undo=False,
        )

    def test_undo_delete_rejects_non_mapping_deleted_entry(self) -> None:
        for deleted_entry in (False, [], "not-a-mapping", None):
            with self.subTest(deleted_entry=deleted_entry):
                self.service._mapping_undo_stack = [{"kind": "delete", "deleted_entry": deleted_entry}]
                with (
                    patch.object(self.service, "upsert_mapping") as upsert_mapping,
                    self.assertRaisesRegex(ValueError, "deleted_entry"),
                ):
                    self.service.undo_last_mapping_operation(startup_session_id=self.service.startup_session_id())
                upsert_mapping.assert_not_called()


if __name__ == "__main__":
    unittest.main()
