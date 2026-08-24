from __future__ import annotations

import tempfile
import threading
import unittest

from peap.streaming_store import StreamingStore
from peap.write_coordinator import WriteCoordinator


class WriteCoordinatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = StreamingStore(f"{self.temp_dir.name}/streaming.sqlite3", auto_migrate=True)
        self.coordinator = WriteCoordinator(store=self.store)

    def test_success_writes_succeeded_operation_journal_with_manifest(self) -> None:
        result = self.coordinator.write_operation(
            "manual_import",
            {"input_dir": "/tmp/manual-import"},
            lambda operation: self._complete_operation(operation, {"imported_count": 2}, "done"),
        )

        rows = self.store.list_operation_journals(limit=10)

        self.assertEqual(result, "done")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation_type"], "manual_import")
        self.assertEqual(rows[0]["status"], "succeeded")
        self.assertEqual(rows[0]["metadata"]["input_dir"], "/tmp/manual-import")
        self.assertEqual(rows[0]["manifest"]["summary"]["imported_count"], 2)
        self.assertTrue(rows[0]["finished_at"])

    def test_exception_writes_failed_operation_journal_and_reraises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "boom"):
            self.coordinator.write_operation(
                "export_excel",
                {"output_dir": "/tmp/export"},
                lambda operation: self._raise_boom(operation),
            )

        rows = self.store.list_operation_journals(limit=10)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation_type"], "export_excel")
        self.assertEqual(rows[0]["status"], "failed")
        self.assertEqual(rows[0]["error"]["message"], "boom")
        self.assertTrue(rows[0]["finished_at"])

    def test_reentrant_write_operation_is_rejected_and_outer_operation_marked_failed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "reentrant write operation"):
            self.coordinator.write_operation(
                "outer",
                {},
                lambda operation: self.coordinator.write_operation("inner", {}, lambda inner: "never"),
            )

        rows = self.store.list_operation_journals(limit=10)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation_type"], "outer")
        self.assertEqual(rows[0]["status"], "failed")
        self.assertIn("reentrant", rows[0]["error"]["message"])

    def test_start_operation_failure_does_not_poison_thread_for_next_write(self) -> None:
        with self.assertRaisesRegex(TypeError, "not JSON serializable"):
            self.coordinator.write_operation(
                "manual_import",
                {"bad": {1, 2, 3}},
                lambda operation: "never",
            )

        result = self.coordinator.write_operation(
            "manual_import",
            {"input_dir": "/tmp/manual-import"},
            lambda operation: self._complete_operation(operation, {"imported_count": 1}, "recovered"),
        )

        rows = self.store.list_operation_journals(limit=10)

        self.assertEqual(result, "recovered")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "succeeded")
        self.assertEqual(rows[0]["metadata"]["input_dir"], "/tmp/manual-import")

    def test_start_operation_rejects_non_text_operation_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "operation_type must be text"):
            self.coordinator.start_operation(123)  # type: ignore[arg-type]

        self.assertEqual(self.store.list_operation_journals(limit=10), [])

    def test_start_operation_rejects_empty_operation_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "operation_type is empty"):
            self.coordinator.start_operation("  ")

        self.assertEqual(self.store.list_operation_journals(limit=10), [])

    def test_start_operation_rejects_explicit_non_object_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "metadata must be an object"):
            self.coordinator.start_operation("manual_import", False)  # type: ignore[arg-type]

        self.assertEqual(self.store.list_operation_journals(limit=10), [])

    def test_start_operation_accepts_none_metadata_as_empty_object(self) -> None:
        operation = self.coordinator.start_operation("manual_import", None)

        row = self.store.get_operation_journal(operation.operation_id)

        self.assertEqual(row["metadata"], {})
        self.assertEqual(operation.metadata, {})

    def test_manifest_updates_reject_explicit_non_objects(self) -> None:
        operation = self.coordinator.start_operation("manual_import", {})

        with self.assertRaisesRegex(ValueError, "manifest must be an object"):
            operation.set_manifest(False)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "manifest must be an object"):
            operation.update_manifest(False)  # type: ignore[arg-type]

        self.assertEqual(operation.manifest, {})

    def test_fail_rejects_explicit_non_object_error(self) -> None:
        operation = self.coordinator.start_operation("manual_import", {})

        with self.assertRaisesRegex(ValueError, "error must be an object"):
            operation.fail(False)  # type: ignore[arg-type]

        self.assertFalse(operation.is_finished)
        self.assertEqual(self.store.get_operation_journal(operation.operation_id)["status"], "pending")

    def test_fail_accepts_exception_and_none_error_payloads(self) -> None:
        exception_operation = self.coordinator.start_operation("manual_import", {})
        exception_operation.fail(RuntimeError("boom"))
        none_operation = self.coordinator.start_operation("export_excel", {})
        none_operation.fail(None)

        exception_row = self.store.get_operation_journal(exception_operation.operation_id)
        none_row = self.store.get_operation_journal(none_operation.operation_id)

        self.assertEqual(exception_row["status"], "failed")
        self.assertEqual(exception_row["error"]["message"], "boom")
        self.assertEqual(none_row["status"], "failed")
        self.assertEqual(none_row["error"], {})

    def test_concurrent_write_operations_are_serialized_deterministically(self) -> None:
        first_entered = threading.Event()
        release_first = threading.Event()
        timeline: list[str] = []

        def run_first() -> None:
            self.coordinator.write_operation(
                "manual_import",
                {"slot": 1},
                lambda operation: self._block_first_operation(operation, timeline, first_entered, release_first),
            )

        def run_second() -> None:
            first_entered.wait(timeout=2)
            self.coordinator.write_operation(
                "export_excel",
                {"slot": 2},
                lambda operation: self._complete_second_operation(operation, timeline),
            )

        thread_one = threading.Thread(target=run_first)
        thread_two = threading.Thread(target=run_second)
        thread_one.start()
        thread_two.start()
        first_entered.wait(timeout=2)
        release_first.set()
        thread_one.join(timeout=5)
        thread_two.join(timeout=5)

        rows = self.store.list_operation_journals(limit=10)

        self.assertEqual(timeline, ["first-start", "first-end", "second"])
        self.assertEqual({row["operation_type"] for row in rows}, {"export_excel", "manual_import"})
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["status"] == "succeeded" for row in rows))

    @staticmethod
    def _complete_operation(operation, summary: dict[str, int], result: str) -> str:
        operation.set_manifest({"summary": dict(summary)})
        return result

    @staticmethod
    def _raise_boom(operation):
        operation.set_manifest({"stage": "before-crash"})
        raise RuntimeError("boom")

    @staticmethod
    def _block_first_operation(operation, timeline, first_entered, release_first) -> str:
        timeline.append("first-start")
        operation.set_manifest({"summary": {"imported_count": 1}})
        first_entered.set()
        release_first.wait(timeout=2)
        timeline.append("first-end")
        return "first-done"

    @staticmethod
    def _complete_second_operation(operation, timeline) -> str:
        timeline.append("second")
        operation.set_manifest({"summary": {"artifacts": 1}})
        return "second-done"


if __name__ == "__main__":
    unittest.main()
