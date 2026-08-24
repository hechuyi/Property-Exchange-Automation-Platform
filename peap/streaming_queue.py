"""Background queue for download-to-ingest processing."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from typing import Dict

from peap_core.business_catalog import get_business_descriptor
from peap_core.business_hint import build_business_hint
from peap_core.source_catalog import canonical_source_code

from .streaming_ingest import StreamingIngestRunner
from .streaming_models import ItemProgressEvent, ItemSavedPayload
from .streaming_store import StreamingStore

_ARTIFACT_READY_TIMEOUT_SECONDS = 5.0
_ARTIFACT_READY_POLL_SECONDS = 0.05
_PENDING_SAVE_STATUSES = {"pending", "writing"}


def _business_family_from_id(business_id: object) -> str:
    text = str(business_id or "").strip()
    if not text or text == "all":
        return ""
    try:
        return get_business_descriptor(text).family_id
    except KeyError:
        return ""


def _build_queue_business_hint(
    *,
    record_family: object,
    business_id: object,
    business_label: object,
    exchange: object,
) -> dict[str, str]:
    family = str(record_family or "").strip()
    if not family:
        family = _business_family_from_id(business_id)
    return build_business_hint(
        record_family=family or "listing",
        business_id=business_id,
        business_label=business_label,
        exchange=exchange,
    )


def _item_event_scope(item: ItemSavedPayload) -> dict[str, str]:
    """Build the stable scope snapshot attached to every queue event."""
    row_payload = item.extra.get("row")
    row = row_payload if isinstance(row_payload, dict) else {}
    record_family = str(item.extra.get("record_family") or "").strip()
    business_id = str(item.extra.get("business_id") or row.get("business_id") or "").strip()
    business_label = str(
        item.extra.get("business_label")
        or item.extra.get("project_type_label")
        or row.get("business_label")
        or ""
    ).strip()
    source_id = str(
        item.extra.get("source_id")
        or item.extra.get("exchange")
        or item.exchange
        or ""
    ).strip()
    business_hint = _build_queue_business_hint(
        record_family=record_family,
        business_id=business_id,
        business_label=business_label,
        exchange=source_id or item.exchange,
    )
    if business_hint:
        record_family = business_hint["record_family"]
        business_id = business_hint["business_id"]
        business_label = business_hint["business_label"]
        source_id = business_hint.get("exchange") or source_id
    else:
        record_family = record_family or "listing"
    return {
        "record_family": record_family,
        "business_id": business_id,
        "business_label": business_label,
        "exchange": str(canonical_source_code(source_id or item.exchange or "") or "").strip(),
    }


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
            if self._thread.is_alive():
                return
            self._thread = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="streaming-ingest", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if not thread.is_alive():
            self._thread = None
            return
        self._stop.set()
        self._queue.put(None)
        thread.join(timeout=_ARTIFACT_READY_TIMEOUT_SECONDS + 1.0)
        if not thread.is_alive():
            self._thread = None

    def wait_for_idle(self) -> None:
        self._queue.join()

    def build_callback(self, *, job_id: str):
        def _callback(payload: Dict[str, object]) -> None:
            extra = {
                key: value for key, value in payload.items() if key not in {
                    "source_file", "html_path", "page_url", "project_code", "project_name", "exchange", "listing_date"
                }
            }
            business_hint = _build_queue_business_hint(
                record_family=extra.get("record_family"),
                business_id=extra.get("business_id") or payload.get("business_id"),
                business_label=extra.get("business_label") or extra.get("project_type_label"),
                exchange=extra.get("source_id") or payload.get("source_id") or payload.get("exchange"),
            )
            if business_hint:
                extra["record_family"] = business_hint["record_family"]
                extra["business_id"] = business_hint["business_id"]
                extra["business_label"] = business_hint["business_label"]
                extra.setdefault("project_type_fallback", business_hint["project_type_fallback"])
            item = ItemSavedPayload(
                source_file=str(payload.get("source_file") or payload.get("html_path") or ""),
                page_url=str(payload.get("page_url") or ""),
                project_code=str(payload.get("project_code") or ""),
                project_name=str(payload.get("project_name") or ""),
                exchange=str(payload.get("exchange") or ""),
                listing_date=str(payload.get("listing_date") or ""),
                extra=extra,
            )
            self.enqueue(job_id=job_id, item=item)

        return _callback

    def enqueue(self, *, job_id: str, item: ItemSavedPayload) -> None:
        row_payload = item.extra.get("row")
        row = row_payload if isinstance(row_payload, dict) else {}
        project_id = str(item.extra.get("project_id") or row.get("project_id") or "").strip()
        record_family = str(item.extra.get("record_family") or "").strip()
        business_id = str(item.extra.get("business_id") or "").strip()
        source_id = str(item.extra.get("source_id") or item.extra.get("exchange") or item.exchange or "").strip()
        business_hint = _build_queue_business_hint(
            record_family=record_family,
            business_id=business_id,
            business_label=item.extra.get("business_label") or item.extra.get("project_type_label"),
            exchange=source_id or item.exchange,
        )
        if business_hint:
            record_family = business_hint["record_family"]
            business_id = business_hint["business_id"]
            source_id = business_hint.get("exchange") or source_id
        else:
            record_family = record_family or "listing"
        source_id = str(canonical_source_code(source_id or item.exchange or "") or "").strip()
        scope = _item_event_scope(item)
        self.store.update_job_counts(job_id, downloaded_inc=1)
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="downloaded",
                status="ok",
                project_code=item.project_code,
                record_family=scope["record_family"],
                payload={
                    "source_file": item.source_file,
                    "page_url": item.page_url,
                    "project_code": item.project_code,
                    "project_id": project_id,
                    "record_family": record_family,
                    "business_id": business_id,
                    "source_id": source_id,
                    "scope": scope,
                },
            )
        )
        self.store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="queued_for_parse",
                status="queued",
                project_code=item.project_code,
                record_family=scope["record_family"],
                payload={"source_file": item.source_file, "scope": scope},
            )
        )
        self._queue.put((job_id, item))

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                self._queue.task_done()
                break
            job_id, item = task
            scope = _item_event_scope(item)
            try:
                if not self._wait_for_artifact_ready(item.source_file):
                    self.store.update_job_counts(job_id, exception_inc=1)
                    self.store.append_event(
                        ItemProgressEvent(
                            job_id=job_id,
                            stage="failed",
                            status="failed",
                            project_code=item.project_code,
                            error_type="artifact_not_ready",
                            error_message="artifact sidecar did not reach a readable complete state before ingest timeout",
                            record_family=scope["record_family"],
                            payload={"source_file": item.source_file, "scope": scope},
                        )
                    )
                    continue
                result = self.runner.ingest(item)
                raw_state = result.get("state") or ""
                state = raw_state.value if hasattr(raw_state, "value") else str(raw_state)
                if state in {
                    "ready",
                    "pending_review",
                    "pending_mapping",
                    "mapping_conflict",
                    "conflict",
                    "field_missing",
                }:
                    self.store.update_job_counts(job_id, persisted_inc=1)
                    self.store.append_event(
                        ItemProgressEvent(
                            job_id=job_id,
                            stage="field_missing" if state == "field_missing" else "persisted",
                            status=state,
                            project_code=str(result.get("project_code") or item.project_code),
                            archive_path=str(result.get("archive_path") or ""),
                            record_family=scope["record_family"],
                            payload={**dict(result), "scope": scope},
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
                            record_family=scope["record_family"],
                            payload={**dict(result), "scope": scope},
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
                            record_family=scope["record_family"],
                            payload={**dict(result), "scope": scope},
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
                        record_family=scope["record_family"],
                        payload={"source_file": item.source_file, "scope": scope},
                    )
                )
            finally:
                self._queue.task_done()

    def _wait_for_artifact_ready(self, source_file: str) -> bool:
        deadline = time.monotonic() + _ARTIFACT_READY_TIMEOUT_SECONDS
        while not self._artifact_ready_for_ingest(source_file):
            if self._stop.is_set() or time.monotonic() >= deadline:
                return False
            time.sleep(_ARTIFACT_READY_POLL_SECONDS)
        return True

    @staticmethod
    def _artifact_ready_for_ingest(source_file: str) -> bool:
        path = str(source_file or "").strip()
        if not path:
            return True
        sidecar_paths = (
            os.path.splitext(path)[0] + ".json",
            f"{path}.peap-save-status.json",
        )
        for sidecar_path in sidecar_paths:
            if not os.path.exists(sidecar_path):
                continue
            try:
                with open(sidecar_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return False
            if not isinstance(payload, dict):
                return False
            save_status = str(payload.get("save_status") or "").strip().lower()
            if save_status in _PENDING_SAVE_STATUSES:
                return False
        return True
