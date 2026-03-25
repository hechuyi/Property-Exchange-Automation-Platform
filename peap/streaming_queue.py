"""Background queue for download-to-ingest processing."""

from __future__ import annotations

import queue
import threading
from typing import Dict

from .streaming_ingest import StreamingIngestRunner
from .streaming_models import ItemProgressEvent, ItemSavedPayload
from .streaming_store import StreamingStore


class StreamingIngestService:
    """Owns the background worker that turns downloaded items into records."""

    def __init__(
        self,
        *,
        store: StreamingStore,
        runner: StreamingIngestRunner,
    ) -> None:
        self.store = store
        self.runner = runner
        self._queue: "queue.Queue[tuple[str, ItemSavedPayload] | None]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="streaming-ingest", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def wait_for_idle(self) -> None:
        self._queue.join()

    def build_callback(self, *, job_id: str):
        def _callback(payload: Dict[str, object]) -> None:
            item = ItemSavedPayload(
                source_file=str(payload.get("source_file") or payload.get("html_path") or ""),
                page_url=str(payload.get("page_url") or ""),
                project_code=str(payload.get("project_code") or ""),
                project_name=str(payload.get("project_name") or ""),
                exchange=str(payload.get("exchange") or ""),
                listing_date=str(payload.get("listing_date") or ""),
                extra={key: value for key, value in payload.items() if key not in {
                    "source_file", "html_path", "page_url", "project_code", "project_name", "exchange", "listing_date"
                }},
            )
            self.enqueue(job_id=job_id, item=item)

        return _callback

    def enqueue(self, *, job_id: str, item: ItemSavedPayload) -> None:
        row_payload = item.extra.get("row")
        row = row_payload if isinstance(row_payload, dict) else {}
        project_id = str(item.extra.get("project_id") or row.get("project_id") or "").strip()
        self.store.update_job_counts(job_id, downloaded_inc=1)
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                project_code=item.project_code,
                payload={
                    "source_file": item.source_file,
                    "page_url": item.page_url,
                    "project_code": item.project_code,
                    "project_id": project_id,
                },
            )
        )
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="queued_for_parse",
                status="queued",
                project_code=item.project_code,
                payload={"source_file": item.source_file},
            )
        )
        self._queue.put((job_id, item))

    def _run(self) -> None:
        while not self._stop.is_set():
            task = self._queue.get()
            if task is None:
                self._queue.task_done()
                break
            job_id, item = task
            try:
                result = self.runner.ingest(item)
                state = str(result.get("state") or "")
                if state in {"ready", "pending_mapping", "conflict"}:
                    self.store.update_job_counts(job_id, persisted_inc=1)
                    self.store.append_event(
                        ItemProgressEvent(
                            job_id=job_id,
                            stage="persisted",
                            status=state,
                            project_code=str(result.get("project_code") or item.project_code),
                            archive_path=str(result.get("archive_path") or ""),
                            payload=result,
                        )
                    )
                elif state == "skipped":
                    self.store.append_event(
                        ItemProgressEvent(
                            job_id=job_id,
                            stage="skipped",
                            status="skipped",
                            project_code=str(result.get("project_code") or item.project_code),
                            archive_path=str(result.get("archive_path") or ""),
                            error_type=str(result.get("error_type") or "skip_parse"),
                            error_message=str(result.get("error_message") or ""),
                            payload=result,
                        )
                    )
                else:
                    self.store.update_job_counts(job_id, exception_inc=1)
                    self.store.append_event(
                        ItemProgressEvent(
                            job_id=job_id,
                            stage="failed",
                            status=state or "failed",
                            project_code=str(result.get("project_code") or item.project_code),
                            archive_path=str(result.get("archive_path") or ""),
                            error_type=str(result.get("error_type") or state or "failed"),
                            error_message=str(result.get("error_message") or ""),
                            payload=result,
                        )
                    )
            except Exception as exc:  # pragma: no cover - defensive path
                self.store.update_job_counts(job_id, exception_inc=1)
                self.store.append_event(
                    ItemProgressEvent(
                        job_id=job_id,
                        stage="failed",
                        status="failed",
                        project_code=item.project_code,
                        error_type="worker_failed",
                        error_message=str(exc),
                        payload={"source_file": item.source_file},
                    )
                )
            finally:
                self._queue.task_done()
