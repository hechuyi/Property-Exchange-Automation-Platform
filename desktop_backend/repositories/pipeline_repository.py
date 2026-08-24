"""Pipeline-domain repository over StreamingStore.

This repository is intentionally scoped around the persistence workflows that
the desktop service layer orchestrates: overview snapshot reads, mapping-refresh
jobs, mapping rule writes, and record reprocess state transitions.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator

from peap.streaming_models import ItemProgressEvent
from peap.streaming_store_maintenance import run_streaming_store_maintenance
from peap.write_coordinator import WriteCoordinator


def _metadata_object(metadata: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    return dict(metadata)


def _payload_object(payload: Dict[str, Any], *, field: str = "payload") -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{field} must be an object")
    return dict(payload)


def _required_text(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} is empty")
    return text


@dataclass(frozen=True)
class OverviewSnapshot:
    recent_jobs: list[Dict[str, Any]]
    latest_job: Dict[str, Any] | None
    record_state_counts: Dict[str, int]
    pending_mapping_count: int


class PipelineRepository:
    """Persistence adapter used by service-layer orchestration code."""

    def __init__(self, *, store) -> None:
        self._store = store

    def build_write_coordinator(self) -> WriteCoordinator:
        """Build the operation coordinator against this repository's store."""

        return WriteCoordinator(store=self._store)

    @contextmanager
    def write_transaction(self) -> Iterator[sqlite3.Connection]:
        """Expose a repository-owned transaction for compound workflows."""

        with self._store.write_transaction() as connection:
            yield connection

    def load_overview_snapshot(self, *, recent_job_limit: int = 5) -> OverviewSnapshot:
        recent_jobs = list(self._store.list_jobs(limit=recent_job_limit))
        latest_job = recent_jobs[0] if recent_jobs else None
        return OverviewSnapshot(
            recent_jobs=recent_jobs,
            latest_job=latest_job,
            record_state_counts=dict(self._store.count_records_by_state()),
            pending_mapping_count=int(self._store.count_pending_mappings()),
        )

    def load_record(self, record_id: str) -> Dict[str, Any]:
        return dict(self._store.get_record(record_id))

    def acknowledge_field_missing(
        self,
        *,
        record_id: str,
        missing_fields: list[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return dict(
            self._store.acknowledge_field_missing(
                record_id,
                missing_fields=missing_fields,
                evidence_source="operator_acknowledge",
            )
        )

    def iter_latest_records(
        self,
        *,
        states: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        record_family: str | None = None,
        limit: int | None = None,
        sort: str = "recent",
    ) -> list[Dict[str, Any]]:
        return list(
            self._store.iter_latest_records(
                states=states,
                date_from=date_from,
                date_to=date_to,
                record_family=record_family,
                limit=limit,
                sort=sort,
            )
        )

    def list_mapping_entries(self) -> list[Dict[str, Any]]:
        return [dict(item) for item in self._store.list_mapping_entries()]

    def get_mapping_entry(self, *, entry_id: str) -> Dict[str, Any]:
        return dict(self._store.get_mapping_entry(entry_id=_required_text(entry_id, field="entry_id")))

    def add_audit_entry(self, action: str, payload: Dict[str, Any]) -> None:
        self._store.add_audit_entry(_required_text(action, field="action"), _payload_object(payload))

    def run_store_maintenance(self, *, rules_config: Dict[str, Any] | None = None, mutate: bool = False):
        return run_streaming_store_maintenance(self._store, rules_config=rules_config, mutate=mutate)

    def get_setting(self, key: str, *, default: Dict[str, Any]) -> Dict[str, Any]:
        return dict(self._store.get_setting(str(key or ""), default=_payload_object(default, field="default")))

    def set_setting(self, key: str, value: Dict[str, Any]) -> None:
        self._store.set_setting(_required_text(key, field="key"), _payload_object(value, field="value"))

    def interrupt_running_jobs(self, *, reason: str) -> list[str]:
        return list(self._store.interrupt_running_jobs(reason=_required_text(reason, field="reason")))

    def interrupt_job(self, job_id: str, *, reason: str) -> bool:
        return bool(
            self._store.interrupt_job(
                _required_text(job_id, field="job_id"),
                reason=_required_text(reason, field="reason"),
            )
        )

    def update_record_source_to_archive(
        self,
        *,
        record_id: str,
        previous_source_file: str,
        archive_path: str,
    ) -> int:
        resolved_record_id = _required_text(record_id, field="record_id")
        resolved_previous_source_file = _required_text(previous_source_file, field="previous_source_file")
        resolved_archive_path = _required_text(archive_path, field="archive_path")
        self._store.update_record_source_file(resolved_record_id, resolved_archive_path)
        return int(self._store.update_downloaded_event_source_file(resolved_previous_source_file, resolved_archive_path))

    def set_record_archive_path(self, *, record_id: str, archive_path: str) -> None:
        self._store.update_record_archive_path(
            _required_text(record_id, field="record_id"),
            _required_text(archive_path, field="archive_path"),
        )

    def rewire_record_to_archive(
        self,
        *,
        record_id: str,
        previous_source_file: str,
        archive_path: str,
    ) -> int:
        resolved_record_id = _required_text(record_id, field="record_id")
        resolved_previous_source_file = _required_text(previous_source_file, field="previous_source_file")
        resolved_archive_path = _required_text(archive_path, field="archive_path")
        self._store.update_record_archive_path(resolved_record_id, resolved_archive_path)
        self._store.update_record_source_file(resolved_record_id, resolved_archive_path)
        return int(self._store.update_downloaded_event_source_file(resolved_previous_source_file, resolved_archive_path))

    def create_mapping_refresh_job(self, *, metadata: Dict[str, Any]) -> str:
        return str(self._store.create_job("mapping_refresh", metadata=_metadata_object(metadata)))

    def create_job(self, job_type: str, *, metadata: Dict[str, Any], job_id: str | None = None) -> str:
        return str(
            self._store.create_job(
                _required_text(job_type, field="job_type"),
                metadata=_metadata_object(metadata),
                job_id=job_id,
            )
        )

    def list_jobs(self, *, limit: int = 20) -> list[Dict[str, Any]]:
        return [dict(item) for item in self._store.list_jobs(limit=limit)]

    def get_job(self, job_id: str) -> Dict[str, Any]:
        return dict(self._store.get_job(job_id))

    def list_job_events(self, job_id: str, *, limit: int = 200) -> list[Dict[str, Any]]:
        return [dict(item) for item in self._store.list_job_events(job_id, limit=limit)]

    def count_job_events(self, job_id: str) -> int:
        counts = self.get_job_event_counts(job_id)
        return int(counts.get("total_count") or 0)

    def get_job_event_counts(self, job_id: str) -> Dict[str, int]:
        return dict(self._store.get_job_event_counts(job_id))

    def start_job(self, job_id: str) -> None:
        self._store.start_job(job_id)

    def ensure_job_running(self, job_id: str) -> None:
        current = str(self._store.get_job(job_id).get("status") or "")
        if current == "starting":
            self._store.start_job(job_id)

    def ensure_mapping_refresh_job_running(self, job_id: str) -> None:
        self.ensure_job_running(job_id)

    def append_event(self, event: ItemProgressEvent) -> None:
        self._store.append_event(event)

    def update_job_counts(
        self,
        job_id: str,
        *,
        downloaded_inc: int = 0,
        persisted_inc: int = 0,
        exception_inc: int = 0,
    ) -> None:
        self._store.update_job_counts(
            job_id,
            downloaded_inc=downloaded_inc,
            persisted_inc=persisted_inc,
            exception_inc=exception_inc,
        )

    def finish_job(self, job_id: str, *, status: str, summary: Dict[str, Any]) -> None:
        self._store.finish_job(
            job_id,
            status=_required_text(status, field="status"),
            summary=_payload_object(summary, field="summary"),
        )

    def fail_job(self, job_id: str, *, failure) -> None:
        self._store.fail_job(job_id, failure=failure)

    def run_ready_export(self, request, *, run_ready_export_fn, **kwargs):
        return run_ready_export_fn(self._store, request, **kwargs)

    def list_exports(self, *, limit: int = 100) -> list[Dict[str, Any]]:
        return [dict(item) for item in self._store.list_exports(limit=limit)]

    def get_export(self, export_id: str) -> Dict[str, Any]:
        return dict(self._store.get_export(export_id))

    def get_export_manifest(self, export_id: str) -> Dict[str, Any]:
        return dict(self._store.get_export_manifest(export_id))

    def get_export_cursor_value(self, cursor_id: str) -> Dict[str, Any]:
        return dict(self._store.get_export_cursor_value(cursor_id))

    def count_records_in_export_scope(self, request, *, count_scope_fn) -> Dict[str, int]:
        return dict(count_scope_fn(self._store, request))

    def append_mapping_refresh_progress(
        self,
        *,
        job_id: str,
        record_id: str,
        index: int,
        total: int,
    ) -> None:
        self._store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="reprocessing",
                status="running",
                project_code=record_id,
                payload={
                    "label": "正在重处理记录",
                    "task_index": index,
                    "task_total": total,
                    "task_label": record_id,
                    "phase_percent": int(index * 100 / max(total, 1)),
                },
            )
        )

    def append_business_re_evaluation_progress(
        self,
        *,
        job_id: str,
        record_id: str,
        index: int,
        total: int,
    ) -> None:
        self._store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="reprocessing",
                status="running",
                project_code=record_id,
                payload={
                    "label": "正在执行业务重判（内部兼容）",
                    "task_index": index,
                    "task_total": total,
                    "task_label": record_id,
                    "phase_percent": int(index * 100 / max(total, 1)),
                },
            )
        )

    def record_mapping_refresh_failure(
        self,
        *,
        job_id: str,
        record_id: str,
        error_message: str,
    ) -> None:
        self._store.update_job_counts(job_id, downloaded_inc=1, exception_inc=1)
        self._store.record_operation_result(
            record_id,
            kind="mapping_refresh",
            code="failed",
            message=error_message,
        )
        self._store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="reprocessing",
                status="failed",
                project_code=record_id,
                error_type="mapping_refresh_failed",
                error_message=error_message,
                payload={"label": "重处理失败", "record_id": record_id},
            )
        )

    def record_mapping_refresh_result(
        self,
        *,
        job_id: str,
        record_id: str,
        result: Dict[str, Any],
        state: str,
    ) -> None:
        persisted_states = {"ready", "pending_mapping", "mapping_conflict", "conflict"}
        acceptable_states = persisted_states | {"skipped"}
        self._store.update_job_counts(
            job_id,
            downloaded_inc=1,
            persisted_inc=1 if state in persisted_states else 0,
            exception_inc=1 if state not in acceptable_states else 0,
        )
        self._store.record_operation_result(
            record_id,
            kind="mapping_refresh",
            code="ok",
            message="",
        )
        self._store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="reprocessing",
                status=state or "done",
                project_code=str(result.get("project_code") or record_id),
                archive_path=str(result.get("archive_path") or ""),
                payload={
                    "label": "映射回刷完成" if state in {"ready", "conflict"} else "映射回刷仍有待处理项",
                    "record_id": record_id,
                    "state": state,
                },
            )
        )

    def record_business_re_evaluation_failure(
        self,
        *,
        job_id: str,
        record_id: str,
        error_message: str,
    ) -> None:
        self._store.update_job_counts(job_id, downloaded_inc=1, exception_inc=1)
        self._store.record_operation_result(
            record_id,
            kind="business_re_evaluation",
            code="failed",
            message=error_message,
        )
        self._store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="reprocessing",
                status="failed",
                project_code=record_id,
                error_type="business_re_evaluation_failed",
                error_message=error_message,
                payload={"label": "业务重判（内部兼容）失败", "record_id": record_id},
            )
        )

    def record_business_re_evaluation_result(
        self,
        *,
        job_id: str,
        record_id: str,
        result: Dict[str, Any],
        state: str,
    ) -> None:
        persisted_states = {"ready", "pending_review", "pending_mapping", "mapping_conflict", "conflict"}
        acceptable_states = persisted_states | {"skipped"}
        self._store.update_job_counts(
            job_id,
            downloaded_inc=1,
            persisted_inc=1 if state in persisted_states else 0,
            exception_inc=1 if state not in acceptable_states else 0,
        )
        self._store.record_operation_result(
            record_id,
            kind="business_re_evaluation",
            code="ok",
            message="",
        )
        self._store.append_event(
            ItemProgressEvent(
                job_id=job_id,
                stage="reprocessing",
                status=state or "done",
                project_code=str(result.get("project_code") or record_id),
                archive_path=str(result.get("archive_path") or ""),
                payload={
                    "label": "业务重判（内部兼容）完成" if state in {"ready", "conflict", "skipped"} else "业务重判（内部兼容）后仍有待处理项",
                    "record_id": record_id,
                    "state": state,
                    "summary_payload": {
                        "kind": "business_re_evaluation",
                    },
                },
            )
        )

    def finish_mapping_refresh_job(
        self,
        *,
        job_id: str,
        refreshed_count: int,
        pending_mapping_count: int,
        mapping_conflict_count: int,
        skipped_count: int,
        failed_count: int,
    ) -> None:
        final_status = "success"
        if refreshed_count <= 0:
            final_status = "failed"
        elif failed_count > 0 or pending_mapping_count > 0 or mapping_conflict_count > 0 or skipped_count > 0:
            final_status = "success_with_warnings"
        self._store.finish_job(
            job_id,
            status=final_status,
            summary={
                "refreshed_count": refreshed_count,
                "pending_mapping_count": pending_mapping_count,
                "mapping_conflict_count": mapping_conflict_count,
                "skipped_count": skipped_count,
                "failed_count": failed_count,
            },
        )

    def save_mapping_rule(
        self,
        *,
        source_name: str,
        group_name: str,
        source_type: str,
        rule_kind: str,
        match_field: str,
        target_field: str,
        metadata: Dict[str, Any],
        replace_entry_id: str = "",
    ) -> str:
        replace_target = str(replace_entry_id or "").strip()
        metadata_payload = _payload_object(metadata, field="metadata")
        if replace_target:
            entry_id = str(
                self._store.replace_mapping_entry(
                    entry_id=replace_target,
                    company_name=source_name,
                    group_name=group_name,
                    source_type=source_type,
                    metadata=metadata_payload,
                )
            )
        else:
            entry_id = str(
                self._store.upsert_mapping_entry(
                    company_name=source_name,
                    group_name=group_name,
                    source_type=source_type,
                    metadata=metadata_payload,
                )
            )
        self._store.add_audit_entry(
            "mapping_updated" if replace_target else "mapping_upserted",
            {
                "entry_id": entry_id,
                "previous_entry_id": replace_target,
                "source_name": source_name,
                "rule_kind": rule_kind,
                "match_field": match_field,
                "target_field": target_field,
                "group_name": group_name,
                "source_type": source_type,
            },
        )
        return entry_id

    def delete_mapping_rule(self, *, entry_id: str) -> bool:
        resolved_entry_id = _required_text(entry_id, field="entry_id")
        deleted = bool(self._store.delete_mapping_entry(entry_id=resolved_entry_id))
        if deleted:
            self._store.add_audit_entry(
                "mapping_deleted",
                {
                    "entry_id": resolved_entry_id,
                },
            )
        return deleted

    def mark_postprocess_refresh_failed(
        self,
        *,
        record_id: str,
        error_message: str,
        caller: str = "",
        job_id: str = "",
    ) -> None:
        self._store.record_operation_result(
            record_id=record_id,
            kind="postprocess_refresh",
            code="failed",
            message=error_message,
        )
        self._store.add_audit_entry(
            "record_postprocess_refresh_failed",
            {
                "record_id": record_id,
                "error_message": error_message,
                "caller": str(caller or ""),
                "job_id": str(job_id or ""),
            },
        )

    def record_postprocess_refreshed(
        self,
        *,
        record_id: str,
        result: Dict[str, Any],
        caller: str = "",
        job_id: str = "",
    ) -> None:
        self._store.record_operation_result(
            record_id=record_id,
            kind="postprocess_refresh",
            code="ok",
            message="",
        )
        self._store.add_audit_entry(
            "record_postprocess_refreshed",
            {
                "record_id": record_id,
                "result": result,
                "caller": str(caller or ""),
                "job_id": str(job_id or ""),
            },
        )

    def mark_reprocess_source_missing(self, *, record_id: str, error_message: str) -> None:
        self._store.record_operation_result(
            record_id=record_id,
            kind="reprocess",
            code="source_missing",
            message=error_message,
            artifact_status="missing",
        )

    def mark_reprocess_failed(self, *, record_id: str, error_message: str) -> None:
        self._store.record_operation_result(
            record_id=record_id,
            kind="reprocess",
            code="failed",
            message=error_message,
        )

    def record_reprocessed(
        self,
        *,
        record_id: str,
        result: Dict[str, Any],
        _connection: sqlite3.Connection | None = None,
    ) -> None:
        self._store.record_reprocess_result(
            _required_text(record_id, field="record_id"),
            result=_payload_object(result, field="result"),
            _connection=_connection,
        )
