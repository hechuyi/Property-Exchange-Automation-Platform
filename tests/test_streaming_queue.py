from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from peap.streaming_models import ItemSavedPayload
from peap.streaming_queue import StreamingIngestService


class StreamingQueueTest(unittest.TestCase):
    def test_repeated_stop_after_delayed_worker_exit_does_not_leave_stale_sentinel(self) -> None:
        class FakeStore:
            def update_job_counts(self, _job_id: str, **_counts: int) -> None:
                return

            def append_event(self, _event) -> None:
                return

        class BlockingRunner:
            def __init__(self) -> None:
                self.started = threading.Event()
                self.release = threading.Event()

            def ingest(self, item: ItemSavedPayload):
                self.started.set()
                self.release.wait(timeout=1.0)
                return {
                    "state": "ready",
                    "project_code": item.project_code,
                    "archive_path": item.source_file,
                }

        runner = BlockingRunner()
        service = StreamingIngestService(store=FakeStore(), runner=runner)
        service.start()
        service.enqueue(
            job_id="job-delayed-stop",
            item=ItemSavedPayload(source_file="", project_code="P001", exchange="sse"),
        )
        self.assertTrue(runner.started.wait(timeout=1.0))

        with patch("peap.streaming_queue._ARTIFACT_READY_TIMEOUT_SECONDS", -1.0):
            service.stop()
        worker = service._thread
        self.assertIsNotNone(worker)
        runner.release.set()
        worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())

        service.stop()
        service.start()
        try:
            self.assertIsNotNone(service._thread)
            self.assertTrue(service._thread.is_alive())
        finally:
            service.stop()

    def test_service_can_restart_and_process_items_after_stop(self) -> None:
        class FakeStore:
            def __init__(self) -> None:
                self.events: list[object] = []

            def update_job_counts(self, _job_id: str, **_counts: int) -> None:
                return

            def append_event(self, event) -> None:
                self.events.append(event)

        class RestartRunner:
            def __init__(self) -> None:
                self.processed = threading.Event()

            def ingest(self, item: ItemSavedPayload):
                self.processed.set()
                return {
                    "state": "ready",
                    "project_code": item.project_code,
                    "archive_path": item.source_file,
                }

        store = FakeStore()
        runner = RestartRunner()
        service = StreamingIngestService(store=store, runner=runner)

        service.stop()
        service.start()
        service.stop()
        service.start()
        try:
            service.enqueue(
                job_id="job-restarted-worker",
                item=ItemSavedPayload(
                    source_file="",
                    project_code="G32026SH1000999",
                    exchange="sse",
                    extra={
                        "record_family": "listing",
                        "business_id": "equity_transfer",
                        "source_id": "sse",
                    },
                ),
            )
            self.assertTrue(runner.processed.wait(timeout=1.0))
            service.wait_for_idle()
        finally:
            service.stop()

        self.assertTrue(any(event.stage == "persisted" for event in store.events))

    def test_callback_translates_business_id_into_ingest_business_hint(self) -> None:
        service = StreamingIngestService(store=object(), runner=object())
        captured: dict[str, object] = {}

        def fake_enqueue(*, job_id: str, item) -> None:
            captured["job_id"] = job_id
            captured["item"] = item

        service.enqueue = fake_enqueue  # type: ignore[method-assign]
        callback = service.build_callback(job_id="job-queue-1")
        callback(
            {
                "source_file": "/tmp/sse-equity-transfer.html",
                "page_url": "https://example.test/detail/1",
                "project_code": "G32026SH1000134",
                "project_name": "股权转让项目",
                "exchange": "sse",
                "business_id": "equity_transfer",
            }
        )

        item = captured["item"]
        self.assertEqual(captured["job_id"], "job-queue-1")
        self.assertEqual(item.exchange, "sse")
        self.assertEqual(item.extra["business_id"], "equity_transfer")
        self.assertEqual(item.extra["business_label"], "股权转让")
        self.assertEqual(item.extra["project_type_fallback"], "股权转让")

    def test_worker_waits_for_complete_json_sidecar_before_ingest(self) -> None:
        class FakeStore:
            def __init__(self) -> None:
                self.events: list[object] = []
                self.counts = {"downloaded": 0, "persisted": 0, "exception": 0}

            def update_job_counts(
                self,
                _job_id: str,
                *,
                downloaded_inc: int = 0,
                persisted_inc: int = 0,
                exception_inc: int = 0,
            ) -> None:
                self.counts["downloaded"] += downloaded_inc
                self.counts["persisted"] += persisted_inc
                self.counts["exception"] += exception_inc

            def append_event(self, event) -> None:
                self.events.append(event)

        class SidecarReadingRunner:
            def ingest(self, item: ItemSavedPayload):
                sidecar_path = os.path.splitext(item.source_file)[0] + ".json"
                try:
                    with open(sidecar_path, "r", encoding="utf-8") as handle:
                        json.load(handle)
                except json.JSONDecodeError as exc:
                    return {
                        "state": "parse_failed",
                        "project_code": item.project_code,
                        "error_type": "deal_sidecar_invalid_json",
                        "error_message": str(exc),
                    }
                return {
                    "state": "pending_mapping",
                    "project_code": item.project_code,
                    "archive_path": item.source_file,
                }

        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = os.path.join(tmp_dir, "GR2026CQ1003976-demo.html")
            sidecar_path = os.path.splitext(html_path)[0] + ".json"
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>demo</body></html>")
            with open(sidecar_path, "w", encoding="utf-8") as handle:
                handle.write("")

            def complete_sidecar() -> None:
                time.sleep(0.05)
                with open(sidecar_path, "w", encoding="utf-8") as handle:
                    json.dump({"save_status": "complete"}, handle)

            writer = threading.Thread(target=complete_sidecar)
            store = FakeStore()
            service = StreamingIngestService(store=store, runner=SidecarReadingRunner())
            service.start()
            writer.start()
            try:
                service.enqueue(
                    job_id="job-sidecar",
                    item=ItemSavedPayload(
                        source_file=html_path,
                        project_code="GR2026CQ1003976",
                        exchange="sse",
                        extra={
                            "record_family": "deal",
                            "business_id": "deal_equity_transfer",
                            "business_label": "股权转让成交",
                            "source_id": "sse",
                        },
                    ),
                )
                service.wait_for_idle()
            finally:
                service.stop()
                writer.join(timeout=1.0)

        self.assertEqual(store.counts["exception"], 0)
        self.assertEqual(store.counts["persisted"], 1)
        self.assertFalse(any(event.status == "parse_failed" for event in store.events))
        expected_scope = {
            "record_family": "deal",
            "business_id": "deal_equity_transfer",
            "business_label": "股权转让成交",
            "exchange": "sse",
        }
        self.assertTrue(store.events)
        self.assertTrue(all(event.payload.get("scope") == expected_scope for event in store.events))

    def test_worker_records_artifact_not_ready_timeout_without_ingest(self) -> None:
        class FakeStore:
            def __init__(self) -> None:
                self.events: list[object] = []
                self.counts = {"downloaded": 0, "persisted": 0, "exception": 0}

            def update_job_counts(
                self,
                _job_id: str,
                *,
                downloaded_inc: int = 0,
                persisted_inc: int = 0,
                exception_inc: int = 0,
            ) -> None:
                self.counts["downloaded"] += downloaded_inc
                self.counts["persisted"] += persisted_inc
                self.counts["exception"] += exception_inc

            def append_event(self, event) -> None:
                self.events.append(event)

        class RunnerThatMustNotRun:
            calls = 0

            def ingest(self, item: ItemSavedPayload):
                self.calls += 1
                return {"state": "pending_mapping", "project_code": item.project_code}

        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = os.path.join(tmp_dir, "GR2026CQ1003976-demo.html")
            sidecar_path = os.path.splitext(html_path)[0] + ".json"
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>demo</body></html>")
            with open(sidecar_path, "w", encoding="utf-8") as handle:
                json.dump({"save_status": "pending"}, handle)

            store = FakeStore()
            runner = RunnerThatMustNotRun()
            service = StreamingIngestService(store=store, runner=runner)
            with (
                patch("peap.streaming_queue._ARTIFACT_READY_TIMEOUT_SECONDS", 0.01),
                patch("peap.streaming_queue._ARTIFACT_READY_POLL_SECONDS", 0.001),
            ):
                service.start()
                try:
                    service.enqueue(
                        job_id="job-sidecar-timeout",
                        item=ItemSavedPayload(
                            source_file=html_path,
                            project_code="GR2026CQ1003976",
                            exchange="cquae",
                        ),
                    )
                    service.wait_for_idle()
                finally:
                    service.stop()

        self.assertEqual(runner.calls, 0)
        self.assertEqual(store.counts["exception"], 1)
        self.assertEqual(store.counts["persisted"], 0)
        failed_events = [event for event in store.events if event.stage == "failed"]
        self.assertEqual(len(failed_events), 1)
        self.assertEqual(failed_events[0].error_type, "artifact_not_ready")
        self.assertEqual(
            failed_events[0].payload["scope"],
            {
                "record_family": "listing",
                "business_id": "",
                "business_label": "",
                "exchange": "cquae",
            },
        )

    def test_worker_treats_field_missing_as_persisted_non_exception_state(self) -> None:
        class FakeStore:
            def __init__(self) -> None:
                self.events: list[object] = []
                self.counts = {"downloaded": 0, "persisted": 0, "exception": 0}

            def update_job_counts(
                self,
                _job_id: str,
                *,
                downloaded_inc: int = 0,
                persisted_inc: int = 0,
                exception_inc: int = 0,
            ) -> None:
                self.counts["downloaded"] += downloaded_inc
                self.counts["persisted"] += persisted_inc
                self.counts["exception"] += exception_inc

            def append_event(self, event) -> None:
                self.events.append(event)

        class FieldMissingRunner:
            def ingest(self, item: ItemSavedPayload):
                return {
                    "state": "field_missing",
                    "project_code": item.project_code,
                    "archive_path": item.source_file,
                    "findings": [
                        {
                            "type": "canonical_field_missing",
                            "evidence": {"missing_fields": ["deal_price"]},
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as tmp_dir:
            html_path = os.path.join(tmp_dir, "GR2025SH1000337-demo.html")
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>demo</body></html>")

            store = FakeStore()
            service = StreamingIngestService(store=store, runner=FieldMissingRunner())
            service.start()
            try:
                service.enqueue(
                    job_id="job-field-missing",
                    item=ItemSavedPayload(
                        source_file=html_path,
                        project_code="GR2025SH1000337",
                        exchange="sse",
                    ),
                )
                service.wait_for_idle()
            finally:
                service.stop()

        self.assertEqual(store.counts["downloaded"], 1)
        self.assertEqual(store.counts["persisted"], 1)
        self.assertEqual(store.counts["exception"], 0)
        field_missing_events = [event for event in store.events if event.stage == "field_missing"]
        self.assertEqual(len(field_missing_events), 1)
        self.assertEqual(field_missing_events[0].status, "field_missing")
        self.assertFalse(any(event.stage == "failed" for event in store.events))
        self.assertTrue(all("scope" in event.payload for event in store.events))

    def test_worker_scope_is_preserved_for_skipped_and_worker_failed_events(self) -> None:
        class FakeStore:
            def __init__(self) -> None:
                self.events: list[object] = []

            def update_job_counts(self, _job_id: str, **_counts: int) -> None:
                return

            def append_event(self, event) -> None:
                self.events.append(event)

        class SkippedRunner:
            def ingest(self, item: ItemSavedPayload):
                return {"state": "skipped", "project_code": item.project_code}

        class FailingRunner:
            def ingest(self, _item: ItemSavedPayload):
                raise RuntimeError("worker exploded")

        expected_scope = {
            "record_family": "listing",
            "business_id": "physical_asset",
            "business_label": "实物资产",
            "exchange": "cbex",
        }
        for runner in (SkippedRunner(), FailingRunner()):
            with self.subTest(runner=runner.__class__.__name__):
                store = FakeStore()
                service = StreamingIngestService(store=store, runner=runner)
                service._wait_for_artifact_ready = lambda _source_file: True  # type: ignore[method-assign]
                service.start()
                try:
                    service.enqueue(
                        job_id="job-worker-scope",
                        item=ItemSavedPayload(
                            source_file="",
                            project_code="G32026SH1000134",
                            exchange="cbex",
                            extra={
                                "record_family": "listing",
                                "business_id": "physical_asset",
                                "business_label": "实物资产",
                                "source_id": "cbex",
                            },
                        ),
                    )
                    service.wait_for_idle()
                finally:
                    service.stop()
                self.assertTrue(store.events)
                self.assertTrue(all(event.payload.get("scope") == expected_scope for event in store.events))


if __name__ == "__main__":
    unittest.main()
