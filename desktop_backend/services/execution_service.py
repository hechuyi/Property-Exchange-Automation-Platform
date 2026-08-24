"""Execution orchestration service for jobs, imports, exports, and pipelines."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import threading
import time
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Callable, Dict

from peap.business_runtime import get_source_business_binding, iter_source_business_bindings
from peap.job_event_summary import failure_summary_fields, has_failed_job_event
from peap.streaming_export import (
    _default_cursor_id,
    count_records_in_export_scope_by_state,
    run_ready_export,
)
from peap.streaming_models import ExportRequest, ItemProgressEvent, ItemSavedPayload
from peap.surface_contract import (
    SURFACE_EXPORT,
    SURFACE_ONE_CLICK,
    SURFACE_RECORDS,
    scope_supported_for_surface,
)
from peap.write_coordinator import WriteCoordinator
from peap_core.business_catalog import get_business_descriptor
from peap_core.business_hint import build_business_hint_from_scope, resolve_explicit_business_scope
from peap_core.error_contracts import PipelineFailure
from peap_core.family_catalog import get_family_descriptor

from ..domain.constants import JOB_PHASE_LABELS
from ..domain.export_blockers import classify_empty_export_result as _classify_empty_export_result
from ..domain.normalizers import (
    coerce_int as _coerce_int,
)
from ..domain.normalizers import (
    job_type_label as _job_type_label,
)
from ..domain.normalizers import (
    normalize_exchange_code as _normalize_exchange_code,
)
from ..domain.normalizers import (
    normalize_job_event_payload as _normalize_job_event_payload,
)
from ..domain.normalizers import (
    normalize_record_state_value as _normalize_record_state_value,
)
from ..domain.normalizers import (
    parse_bool as _parse_bool,
)
from ..domain.normalizers import (
    parse_local_path as _parse_local_path,
)
from ..domain.normalizers import (
    parse_positive_int as _parse_positive_int,
)
from ..domain.normalizers import (
    parse_text as _parse_text,
)
from ..domain.normalizers import (
    validate_streaming_job_dates as _validate_streaming_job_dates,
)
from ..error_codes import (
    ERROR_BROWSER_RUNTIME_MISSING,
    ERROR_INVALID_REQUEST,
    ERROR_MANUAL_IMPORT_INPUT_DIR_NOT_FOUND,
    ERROR_MUTATING_JOB_IN_PROGRESS,
)
from ..job_contract import RETRYABLE_JOB_STATUSES, RETRYABLE_JOB_TYPES, job_actions
from ..progress_contract import build_progress_view
from ..repositories import PipelineRepository
from ..request_contract import (
    normalize_archive_reprocess_request,
    normalize_export_request_payload,
    normalize_one_click_request,
)
from ..runtime_dependencies import playwright_env
from .records_service import (
    normalize_request_scope as _records_normalize_request_scope,
)
from .records_service import (
    scope_business_ids as _records_scope_business_ids,
)


def _namespace(**kwargs):
    return argparse.Namespace(**kwargs)


def _resolved_job_date_range(payload: Mapping[str, Any]) -> tuple[dt.date, dt.date]:
    today = dt.date.today()
    start_text = str(payload.get("start_date") or "").strip()
    end_text = str(payload.get("end_date") or "").strip()
    start = dt.date.fromisoformat(start_text) if start_text else today
    end = dt.date.fromisoformat(end_text) if end_text else today
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    return start, end


def _basic_settings_payload_is_corrupt(settings: Mapping[str, Any]) -> bool:
    metadata = settings.get("stale_default_metadata")
    if not isinstance(metadata, Mapping):
        return False
    return bool(metadata.get("is_stale")) and str(metadata.get("reason") or "").strip() == "settings_payload_corrupt"


def _summary_count(summary: Dict[str, Any], key: str) -> int:
    return _coerce_int(summary.get(key), default=0)


def _optional_int_field(source: Mapping[str, Any], key: str, *, field_name: str, default: int = 0) -> int:
    value = source.get(key)
    if key not in source or value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


_MANUAL_IMPORT_PERSISTED_STATES = {
    "ready",
    "pending_review",
    "pending_mapping",
    "mapping_conflict",
    "conflict",
    "field_missing",
}
_MANUAL_IMPORT_KNOWN_STATES = {*_MANUAL_IMPORT_PERSISTED_STATES, "skipped"}


def _required_mapping_value(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return dict(value)


def _optional_mapping_value(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    return _required_mapping_value(value, field_name=field_name)


def _optional_mapping_field(payload: Mapping[str, Any], key: str, *, field_name: str) -> dict[str, Any]:
    if key not in payload:
        return {}
    return _optional_mapping_value(payload.get(key), field_name=field_name)


def _optional_list_field(payload: Mapping[str, Any], key: str, *, field_name: str) -> list[Any]:
    if key not in payload:
        return []
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return list(value)


def _optional_list_value(value: Any, *, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return list(value)


def _family_scopes_detail(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return []
    if "family_scopes" not in payload:
        return []
    value = payload.get("family_scopes")
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return value


_STREAMING_RETRY_FIELDS = (
    "start_date",
    "end_date",
    "exchange",
    "record_family",
    "business_id",
    "business_label",
    "record_families",
    "family_scopes",
    "concurrency",
    "page_size",
    "max_pages",
    "save_json",
    "no_resume",
    "include_public_resource",
    "verbose",
    "postprocess_config",
)


def _streaming_retry_payload(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild a retry request from the persisted, user-controlled fields only."""
    payload: dict[str, Any] = {}
    for field_name in _STREAMING_RETRY_FIELDS:
        if field_name not in metadata:
            continue
        value = metadata.get(field_name)
        if field_name == "family_scopes":
            if not isinstance(value, list):
                raise ValueError("metadata.family_scopes must be a list")
            scopes: list[dict[str, Any]] = []
            for index, scope in enumerate(value):
                if not isinstance(scope, Mapping):
                    raise ValueError(f"metadata.family_scopes[{index}] must be an object")
                scopes.append(dict(scope))
            payload[field_name] = scopes
        elif field_name == "record_families":
            if not isinstance(value, list):
                raise ValueError("metadata.record_families must be a list")
            payload[field_name] = list(value)
        else:
            payload[field_name] = value
    return payload


def _reject_non_list_family_scopes(payload: Any) -> None:
    if not isinstance(payload, Mapping) or "family_scopes" not in payload:
        return
    value = payload.get("family_scopes")
    if value is None or isinstance(value, list):
        return
    raise ValueError("family_scopes must be a list")


def _first_product_readiness_issue(product_readiness: Mapping[str, Any]) -> dict[str, Any]:
    issues = _optional_list_field(
        product_readiness,
        "issues",
        field_name="product_readiness.issues",
    )
    first_issue: dict[str, Any] | None = None
    for index, raw_issue in enumerate(issues):
        issue = _required_mapping_value(
            raw_issue,
            field_name=f"product_readiness.issues[{index}]",
        )
        if first_issue is None:
            first_issue = issue
    return first_issue or {}


def _progress_event_stage(event: Mapping[str, Any]) -> str:
    if not isinstance(event, Mapping):
        raise ValueError("progress_event must be a mapping")
    return str(event.get("stage") or "")


def _progress_event_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise ValueError("progress_event must be a mapping")
    return _optional_mapping_field(event, "payload", field_name="progress_event.payload")


def _job_failure_summary(job: Dict[str, Any]) -> dict[str, str]:
    summary = _optional_mapping_field(job, "summary", field_name="job.summary")
    return {
        "failure_code": str(summary.get("failure_code") or "").strip(),
        "failure_stage": str(summary.get("failure_stage") or "").strip(),
        "failure_message": str(summary.get("failure_message") or "").strip(),
    }


def _job_warning_summary(job: Dict[str, Any]) -> dict[str, str]:
    summary = _optional_mapping_field(job, "summary", field_name="job.summary")
    return {
        "warning_code": str(summary.get("warning_code") or "").strip(),
        "warning_message": str(summary.get("warning_message") or "").strip(),
    }


def _job_events_warning_summary(job_events: list[dict[str, Any]]) -> dict[str, str]:
    for event in reversed(job_events):
        payload = _progress_event_payload(event)
        summary = _optional_mapping_field(payload, "summary", field_name="progress_event.payload.summary")
        summary_payload = _optional_mapping_field(
            payload,
            "summary_payload",
            field_name="progress_event.payload.summary_payload",
        )
        summary_payload_summary = _optional_mapping_field(
            summary_payload,
            "summary",
            field_name="progress_event.payload.summary_payload.summary",
        )
        warning_code = str(
            payload.get("warning_code")
            or summary_payload.get("warning_code")
            or summary.get("warning_code")
            or summary_payload_summary.get("warning_code")
            or ""
        ).strip()
        warning_message = str(
            payload.get("warning_message")
            or summary_payload.get("warning_message")
            or summary.get("warning_message")
            or summary_payload_summary.get("warning_message")
            or ""
        ).strip()
        if warning_code or warning_message:
            return {
                "warning_code": warning_code,
                "warning_message": warning_message,
            }
    return {
        "warning_code": "",
        "warning_message": "",
    }


def _pipeline_archive_audit_scope(result: Any, spec: Mapping[str, Any]) -> dict[str, Any]:
    if result is None:
        return {}
    download_result = getattr(result, "download_result", None)
    if download_result is None or not hasattr(download_result, "archive_audit"):
        return {}
    archive_audit = download_result.archive_audit
    if archive_audit is None or archive_audit == {}:
        return {}
    if not isinstance(archive_audit, Mapping):
        raise ValueError("download_result.archive_audit must be a mapping")
    return {
        "record_family": str(spec.get("record_family") or "").strip(),
        "business_id": str(spec.get("business_id") or "").strip(),
        "business_label": str(spec.get("business_label") or "").strip(),
        "exchange": str(spec.get("exchange") or "").strip(),
        "archive_root": str(spec.get("archive_root") or "").strip(),
        "audit": dict(archive_audit),
    }


def _multi_family_download_archive_audit_summary(scoped_audits: list[dict[str, Any]]) -> dict[str, Any]:
    if not scoped_audits:
        return {}

    def _audit_value(item: Mapping[str, Any], key: str) -> int:
        audit = item.get("audit")
        if not isinstance(audit, Mapping):
            return 0
        return _coerce_int(audit.get(key), default=0)

    ok = True
    for item in scoped_audits:
        audit = item.get("audit")
        ok = ok and isinstance(audit, Mapping) and bool(audit.get("ok"))
    return {
        "download_archive_audit": {
            "ok": ok,
            "scope_count": len(scoped_audits),
            "root_count": sum(_audit_value(item, "root_count") for item in scoped_audits),
            "html_count": sum(_audit_value(item, "html_count") for item in scoped_audits),
            "sidecar_count": sum(_audit_value(item, "sidecar_count") for item in scoped_audits),
            "issue_count": sum(_audit_value(item, "issue_count") for item in scoped_audits),
            "scopes": list(scoped_audits),
        }
    }


def _normalize_mutating_job_type(job_type: Any) -> str:
    if not isinstance(job_type, str):
        raise ValueError("job_type must be a non-empty string")
    normalized = job_type.strip()
    if not normalized:
        raise ValueError("job_type must be a non-empty string")
    return normalized


def _business_re_evaluation_pending_count(status_counts: Dict[str, int]) -> int:
    return int(
        status_counts.get("pending_review", 0)
        + status_counts.get("pending_mapping", 0)
        + status_counts.get("mapping_conflict", 0)
    )


def _business_re_evaluation_accepted_count(status_counts: Dict[str, int]) -> int:
    return int(
        status_counts.get("ready", 0)
        + status_counts.get("conflict", 0)
        + status_counts.get("skipped", 0)
    )


def _discover_import_files(input_dir: str) -> list[str]:
    root = os.path.abspath(str(input_dir or "").strip())
    if not root or not os.path.isdir(root):
        return []
    matches: list[str] = []
    for current_root, dir_names, file_names in os.walk(root):
        dir_names.sort()
        for file_name in sorted(file_names):
            lowered = file_name.lower()
            if lowered.endswith((".html", ".htm", ".mhtml")):
                matches.append(os.path.join(current_root, file_name))
    return matches


class _NonExecutableDownloadScopeError(RuntimeError):
    def __init__(self, *, exchange: str, record_family: str, business_id: str, task_ids: list[str]) -> None:
        super().__init__("下载任务尚不可执行")
        self.exchange = exchange
        self.record_family = record_family
        self.business_id = business_id
        self.task_ids = task_ids


class _UnsupportedSurfaceScopeError(ValueError):
    def __init__(self, *, exchange: str, record_family: str, business_ids: list[str], surface: str) -> None:
        scope_business = business_ids[0] if len(business_ids) == 1 else "all"
        super().__init__(
            f"surface {surface} does not support scope {record_family}:{scope_business}@{exchange}"
        )
        self.exchange = exchange
        self.record_family = record_family
        self.business_ids = business_ids
        self.surface = surface


def _validated_download_scope(
    *,
    exchange: str,
    record_family: str,
    business_id: str,
) -> tuple[str, str]:
    family = get_family_descriptor(record_family)
    normalized_family = family.family_id
    normalized_business = str(business_id or "").strip()
    if normalized_business in {"", "all"}:
        normalized_business = "all"
    else:
        normalized_business = get_business_descriptor(
            normalized_business,
            family_id=normalized_family,
        ).business_id

    bindings = list(iter_source_business_bindings(record_family=normalized_family))
    if exchange != "all":
        bindings = [binding for binding in bindings if binding.source_id == exchange]
    if normalized_business != "all":
        if exchange == "all":
            bindings = [binding for binding in bindings if binding.business_id == normalized_business]
        else:
            get_source_business_binding(
                exchange,
                record_family=normalized_family,
                business_id=normalized_business,
            )
            bindings = [binding for binding in bindings if binding.business_id == normalized_business]
    if not bindings:
        raise KeyError((exchange, normalized_family, normalized_business))
    blocked_bindings = [binding for binding in bindings if not bool(getattr(binding, "implemented", True))]
    executable_bindings = [binding for binding in bindings if bool(getattr(binding, "implemented", True))]
    if not executable_bindings:
        raise _NonExecutableDownloadScopeError(
            exchange=exchange,
            record_family=normalized_family,
            business_id=normalized_business,
            task_ids=[binding.task_id for binding in blocked_bindings or bindings],
        )
    if exchange != "all" and normalized_business != "all" and blocked_bindings:
        raise _NonExecutableDownloadScopeError(
            exchange=exchange,
            record_family=normalized_family,
            business_id=normalized_business,
            task_ids=[binding.task_id for binding in blocked_bindings],
        )
    return normalized_family, normalized_business


def _manual_import_scope(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    scope = dict(resolve_explicit_business_scope(payload))
    if scope:
        _validated_surface_scope(
            exchange=_normalize_exchange_code(scope.get("exchange") or "all"),
            record_family=str(scope.get("record_family") or ""),
            business_ids=[str(scope.get("business_id") or "")],
            surface=SURFACE_RECORDS,
        )
    return scope


def _validated_surface_scope(
    *,
    exchange: str,
    record_family: str,
    business_ids: list[str],
    surface: str,
) -> None:
    if not business_ids:
        return
    if scope_supported_for_surface(
        record_family=record_family,
        business_ids=business_ids,
        exchange=exchange,
        surface=surface,
    ):
        return
    raise _UnsupportedSurfaceScopeError(
        exchange=exchange,
        record_family=record_family,
        business_ids=list(business_ids),
        surface=surface,
    )


def _normalize_family_scope_list(payload: Dict[str, Any] | None) -> list[dict[str, str]]:
    raw_scopes = payload.get("family_scopes") if isinstance(payload, dict) else None
    if not isinstance(raw_scopes, list):
        return []
    normalized_scopes: list[dict[str, str]] = []
    for raw_scope in raw_scopes:
        if not isinstance(raw_scope, dict):
            raise ValueError("family_scopes entries must be objects")
        record_family = str(raw_scope.get("record_family") or "").strip()
        business_id = str(raw_scope.get("business_id") or "").strip()
        exchange_raw = str(raw_scope.get("exchange") or "").strip()
        exchange = _normalize_exchange_code(exchange_raw) if exchange_raw else ""
        business_label = str(raw_scope.get("business_label") or "").strip()
        if not record_family or not business_id or not exchange:
            raise ValueError("family_scopes entries require record_family, business_id, and exchange")
        scope = {
            "record_family": record_family,
            "business_id": business_id,
            "exchange": exchange,
        }
        if business_label:
            scope["business_label"] = business_label
        normalized_scopes.append(scope)
    return normalized_scopes


class ExecutionService:
    """Own execution lifecycle, background jobs, and import/export orchestration."""

    def __init__(
        self,
        *,
        config_obj: object,
        repository: PipelineRepository | None = None,
        store=None,
        db_path: str,
        runtime_service,
        get_basic_settings: Callable[[], Dict[str, Any]],
        get_advanced_settings: Callable[[], Dict[str, Any]],
        run_store_maintenance: Callable[[], None],
        repair_missing_archives_once: Callable[[], None],
        build_ingest_runner: Callable[..., Any],
        user_error_cls,
        write_coordinator: WriteCoordinator | None = None,
        startup_session_id: str = "",
    ) -> None:
        self.config = config_obj
        if repository is None:
            if store is None:
                raise ValueError("repository or store is required")
            repository = PipelineRepository(store=store)
        self.repository = repository
        self.db_path = db_path
        self.runtime_service = runtime_service
        self.get_basic_settings = get_basic_settings
        self.get_advanced_settings = get_advanced_settings
        self.run_store_maintenance = run_store_maintenance
        self.repair_missing_archives_once = repair_missing_archives_once
        self.build_ingest_runner = build_ingest_runner
        self.user_error_cls = user_error_cls
        self.startup_session_id = str(startup_session_id or "").strip()
        if write_coordinator is None:
            write_coordinator = repository.build_write_coordinator()
        self.write_coordinator = write_coordinator
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._thread_state = threading.local()
        self._active_mutating_jobs: set[str] = set()
        self._mutating_job_leases: dict[str, dict[str, Any]] = {}
        self._startup_handshake_timeout_sec = 5.0

    def build_latest_progress(self, latest_job: Dict[str, Any] | None) -> Dict[str, Any]:
        if not latest_job:
            return {
                "phase_code": "",
                "phase_label": "暂无任务",
                "job_status": "",
                "downloaded_count": 0,
                "persisted_count": 0,
                "exception_count": 0,
                "pending_review_count": 0,
                "pending_mapping_count": 0,
                "mapping_conflict_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "archive_pending_count": 0,
                "archive_completed_count": 0,
                "current_task_label": "",
                "task_index": 0,
                "task_total": 0,
                "phase_percent": 0,
                "latest_stage_code": "",
                "latest_stage_label": "",
                "latest_stage_summary": {},
            }

        job_id = str(latest_job.get("job_id") or "")
        job_type = str(latest_job.get("job_type") or "")
        raw_job_metadata = latest_job.get("metadata")
        if raw_job_metadata is None:
            job_metadata = {}
        elif not isinstance(raw_job_metadata, Mapping):
            raise ValueError("job.metadata must be a mapping")
        else:
            job_metadata = dict(raw_job_metadata)
        record_family = str(job_metadata.get("record_family") or "").strip()
        record_families = _optional_list_field(
            job_metadata,
            "record_families",
            field_name="job.metadata.record_families",
        )
        if not record_family and not record_families:
            record_family = "listing"
        raw_status_counts = self.repository.get_job_event_counts(job_id) if job_id else {}
        status_counts: Dict[str, int] = {}
        for status_key, count in raw_status_counts.items():
            normalized_key = "total_count" if status_key == "total_count" else _normalize_record_state_value(status_key)
            normalized_key = normalized_key or str(status_key or "")
            status_counts[normalized_key] = status_counts.get(normalized_key, 0) + int(count or 0)
        recent_events = self.repository.list_job_events(job_id, limit=40) if job_id else []
        latest_phase_event = next(
            (event for event in recent_events if _progress_event_stage(event) in JOB_PHASE_LABELS),
            None,
        )
        latest_stage_event = next(
            (
                event
                for event in recent_events
                if _progress_event_stage(event) in {"prepare_tasks", "save_pages"}
            ),
            None,
        )
        latest_phase_payload = _progress_event_payload(latest_phase_event) if latest_phase_event is not None else {}
        latest_stage_payload = _progress_event_payload(latest_stage_event) if latest_stage_event is not None else {}
        latest_stage_summary = _optional_mapping_field(
            latest_stage_payload,
            "summary",
            field_name="progress_event.payload.summary",
        )
        if not latest_stage_summary and "summary_payload" in latest_stage_payload:
            latest_stage_summary_payload = _optional_mapping_field(
                latest_stage_payload,
                "summary_payload",
                field_name="progress_event.payload.summary_payload",
            )
            if "summary" in latest_stage_summary_payload:
                latest_stage_summary = _optional_mapping_field(
                    latest_stage_summary_payload,
                    "summary",
                    field_name="progress_event.payload.summary_payload.summary",
                )
            elif "aggregate_summary" in latest_stage_summary_payload:
                latest_stage_summary = _optional_mapping_field(
                    latest_stage_summary_payload,
                    "aggregate_summary",
                    field_name="progress_event.payload.summary_payload.aggregate_summary",
                )
            for key in ("warning_code", "warning_message"):
                value = str(latest_stage_summary_payload.get(key) or "").strip()
                if value:
                    latest_stage_summary[key] = value

        phase_code = ""
        phase_label = "任务进行中"
        job_status = str(latest_job.get("status") or "")
        downloaded_count = int(latest_job.get("downloaded_count") or 0)
        persisted_count = int(latest_job.get("persisted_count") or 0)
        exception_count = int(latest_job.get("exception_count") or 0)
        pending_review_count = int(status_counts.get("pending_review", 0))
        pending_mapping_count = int(status_counts.get("pending_mapping", 0))
        mapping_conflict_count = int(status_counts.get("mapping_conflict", 0))
        skipped_count = int(status_counts.get("skipped", 0))
        failed_count = int(status_counts.get("failed", 0))
        latest_phase_code = str(latest_phase_event.get("stage") or "") if latest_phase_event is not None else ""
        is_export_phase = latest_phase_code == "exporting"
        archive_pending_count = 0 if is_export_phase else max(downloaded_count - persisted_count - skipped_count - exception_count, 0)
        current_task_label = str(latest_phase_payload.get("task_label") or "").strip()
        task_index = _optional_int_field(
            latest_phase_payload,
            "task_index",
            field_name="progress_event.payload.task_index",
        )
        task_total = _optional_int_field(
            latest_phase_payload,
            "task_total",
            field_name="progress_event.payload.task_total",
        )
        phase_percent = _optional_int_field(
            latest_phase_payload,
            "phase_percent",
            field_name="progress_event.payload.phase_percent",
        )
        detail_candidates = _summary_count(latest_stage_summary, "detail_candidates")
        detail_date_skipped = _summary_count(latest_stage_summary, "detail_date_skipped")
        detail_fetched = _summary_count(latest_stage_summary, "detail_fetched")
        failure_summary = _job_failure_summary(latest_job)
        has_upstream_failure = bool(failure_summary["failure_code"] or failure_summary["failure_message"])
        warning_summary = _job_warning_summary(latest_job)
        if not (warning_summary["warning_code"] or warning_summary["warning_message"]):
            warning_summary = _job_events_warning_summary(recent_events)

        if job_status == "success":
            phase_code = "completed"
            phase_label = "已完成"
            phase_percent = 100
        elif job_status == "success_with_warnings":
            phase_code = "completed_with_warnings"
            phase_label = "已完成，但有待处理项"
            phase_percent = 100
        elif job_status == "interrupted":
            phase_code = "interrupted"
            phase_label = "已中断"
            phase_percent = 100
        elif job_status == "failed":
            phase_code = "failed"
            phase_label = "执行失败"
            phase_percent = 100
        elif job_status == "running":
            if is_export_phase:
                phase_code = "exporting"
                phase_label = str(latest_phase_payload.get("label") or JOB_PHASE_LABELS["exporting"])
            elif archive_pending_count > 0:
                phase_code = "archive_pending"
                phase_label = "正在存档"
                current_task_label = ""
                task_index = 0
                task_total = max(downloaded_count, 0)
                if downloaded_count > 0:
                    archived_ratio = (persisted_count + skipped_count + exception_count) / max(downloaded_count, 1)
                    phase_percent = min(98, 70 + int(archived_ratio * 25))
                else:
                    phase_percent = max(phase_percent, 70)
            elif latest_phase_event is not None and str(latest_phase_event.get("status") or "") == "running":
                phase_code = str(latest_phase_event.get("stage") or "")
                phase_label = str(latest_phase_payload.get("label") or JOB_PHASE_LABELS.get(phase_code) or phase_label)
            elif latest_phase_event is not None:
                phase_code = str(latest_phase_event.get("stage") or "")
                phase_label = str(latest_phase_payload.get("label") or JOB_PHASE_LABELS.get(phase_code) or phase_label)
            elif downloaded_count <= 0:
                phase_code = "prepare_tasks"
                phase_label = JOB_PHASE_LABELS["prepare_tasks"]
                phase_percent = max(phase_percent, 5)
        elif downloaded_count <= 0:
            phase_code = "prepare_tasks"
            phase_label = JOB_PHASE_LABELS["prepare_tasks"]
            phase_percent = max(phase_percent, 5)

        if phase_code == "prepare_tasks" and phase_percent <= 0:
            phase_percent = 12
        elif phase_code == "save_pages" and phase_percent <= 0:
            phase_percent = 48
        elif phase_code == "exporting" and phase_percent <= 0:
            phase_percent = 92

        if (
            phase_code in {"completed", "completed_with_warnings"}
            and latest_stage_event is not None
            and latest_stage_summary
            and downloaded_count <= 0
            and persisted_count <= 0
        ):
            if has_upstream_failure:
                phase_label = "外站访问失败，请检查任务告警"
            elif warning_summary["warning_message"]:
                phase_label = warning_summary["warning_message"]
            elif detail_candidates > 0 and detail_date_skipped >= detail_candidates and detail_fetched <= 0:
                phase_label = "本次没有符合日期条件的网页"
            elif _summary_count(latest_stage_summary, "duplicate_skipped") > 0 and detail_fetched <= 0:
                phase_label = "所选日期网页已存在，无需重复下载"
            elif detail_candidates <= 0 and detail_fetched <= 0:
                phase_label = "本次未发现新网页"
            else:
                phase_label = "本次未产生可录入记录"

        raw_progress = {
            "job_id": job_id,
            "job_type": job_type,
            "record_family": record_family,
            "job_status": job_status,
            "phase_code": phase_code,
            "phase_label": phase_label,
            "current_item_label": current_task_label,
            "current_index": task_index,
            "current_total": task_total,
            "latest_stage_code": str(latest_stage_event.get("stage") or "") if latest_stage_event is not None else "",
            "latest_stage_label": str(latest_stage_payload.get("label") or "") if latest_stage_event is not None else "",
            "latest_stage_summary": latest_stage_summary,
            "summary": {
                "downloaded_count": downloaded_count,
                "persisted_count": persisted_count,
                "exception_count": exception_count,
                "pending_review_count": pending_review_count,
                "pending_mapping_count": pending_mapping_count,
                "mapping_conflict_count": mapping_conflict_count,
                "skipped_count": skipped_count,
                "failed_count": failed_count,
                "archive_pending_count": archive_pending_count,
                "archive_completed_count": 0 if is_export_phase else persisted_count,
            },
        }
        if job_type == "business_re_evaluation":
            raw_progress["summary"] = {
                "pending_review_count": _business_re_evaluation_pending_count(status_counts),
                "accepted_completed_count": _business_re_evaluation_accepted_count(status_counts),
                "skipped_count": skipped_count,
                "failed_count": exception_count,
            }
        progress_view = _required_mapping_value(
            build_progress_view(
                job={
                    "job_id": job_id,
                    "job_type": job_type,
                    "status": job_status,
                    "record_family": record_family,
                },
                raw_progress=raw_progress,
            ),
            field_name="progress",
        )
        payload = {
            "phase_code": str(progress_view.get("phase_code") or ""),
            "phase_label": str(progress_view.get("phase_label") or ""),
            "job_status": job_status,
            "downloaded_count": downloaded_count,
            "persisted_count": persisted_count,
            "exception_count": exception_count,
            "pending_mapping_count": pending_mapping_count,
            "skipped_count": skipped_count,
            "archive_pending_count": archive_pending_count,
            "archive_completed_count": 0 if is_export_phase else persisted_count,
            "current_task_label": str(progress_view.get("current_item_label") or ""),
            "task_index": _coerce_int(progress_view.get("current_index"), default=0),
            "task_total": _coerce_int(progress_view.get("current_total"), default=0),
            "phase_percent": phase_percent,
            "job_id": job_id,
            "latest_stage_code": str(progress_view.get("latest_stage_code") or ""),
            "latest_stage_label": str(progress_view.get("latest_stage_label") or ""),
            "latest_stage_summary": _optional_mapping_field(
                progress_view,
                "latest_stage_summary",
                field_name="progress.latest_stage_summary",
            ),
        }
        if job_type == "business_re_evaluation":
            payload["pending_review_count"] = _business_re_evaluation_pending_count(status_counts)
            payload["accepted_completed_count"] = _business_re_evaluation_accepted_count(status_counts)
            payload["failed_count"] = exception_count
        return payload

    def list_jobs(self, *, limit: int = 20) -> list[Dict[str, Any]]:
        return self.repository.list_jobs(limit=limit)

    def get_job(self, job_id: str) -> Dict[str, Any]:
        data = self.repository.get_job(job_id)
        data["events"] = [
            _normalize_job_event_payload(event)
            for event in self.repository.list_job_events(job_id, limit=100)
        ]
        return data

    def get_job_events(self, job_id: str, *, limit: int = 200) -> list[Dict[str, Any]]:
        return [
            _normalize_job_event_payload(event)
            for event in self.repository.list_job_events(job_id, limit=limit)
        ]

    def count_job_events(self, job_id: str) -> int:
        return self.repository.count_job_events(job_id)

    def retry_job(self, job_id: str) -> Dict[str, Any]:
        normalized_job_id = _parse_text(job_id, field_name="job_id")
        job = self.repository.get_job(normalized_job_id)
        job_type = str(job.get("job_type") or "").strip()
        if job_type not in RETRYABLE_JOB_TYPES:
            raise ValueError(f"job type is not retryable: {job_type or 'unknown'}")
        status = str(job.get("status") or "").strip().lower()
        if status not in RETRYABLE_JOB_STATUSES:
            raise ValueError(f"job status is not retryable: {status or 'unknown'}")
        raw_metadata = job.get("metadata")
        if raw_metadata is None:
            raise ValueError("job.metadata is not replayable")
        elif not isinstance(raw_metadata, Mapping):
            raise ValueError("job.metadata must be a mapping")
        else:
            metadata = dict(raw_metadata)
        retry_of_job_id = str(job.get("job_id") or normalized_job_id or "").strip()
        launch_result: Dict[str, Any]
        if job_type in {"one_click", "download_ingest"}:
            launch_payload = _streaming_retry_payload(metadata)
            if not job_actions({**job, "metadata": metadata})["retry"]:
                raise ValueError("job.metadata is not replayable")
            if job_type == "one_click":
                launch_result = dict(self.launch_one_click(launch_payload))
            else:
                launch_result = dict(self.launch_download_ingest(launch_payload))
        elif job_type == "manual_import":
            input_dir = _parse_local_path(metadata.get("input_dir"), field_name="input_dir")
            if not input_dir:
                raise ValueError("input_dir is required for retrying manual_import jobs")
            if not job_actions({**job, "metadata": metadata})["retry"]:
                raise ValueError("job.metadata is not replayable")
            launch_payload = {
                "input_dir": input_dir,
            }
            scope = metadata.get("scope")
            if "scope" in metadata and not isinstance(scope, dict):
                raise ValueError("scope must be an object")
            if scope:
                for key in ("record_family", "business_id", "business_label", "exchange"):
                    if key in scope and key not in launch_payload:
                        launch_payload[key] = scope[key]
            launch_result = dict(self.launch_manual_import(launch_payload))
        else:
            input_dir = _parse_local_path(metadata.get("input_dir"), field_name="input_dir")
            if not input_dir:
                raise ValueError("input_dir is required for retrying archive_reprocess jobs")
            if not job_actions({**job, "metadata": metadata})["retry"]:
                raise ValueError("job.metadata is not replayable")
            launch_payload = {"input_dir": input_dir}
            launch_result = dict(self.launch_archive_reprocess(launch_payload))

        launch_result["retry_of_job_id"] = retry_of_job_id
        launch_result["notification"] = {"level": "success", "message": "任务重试已启动"}
        return launch_result

    def start_background_thread(self, *, name: str, target) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        with self._lock:
            self._threads[thread.name] = thread
        try:
            thread.start()
        except Exception:
            with self._lock:
                if self._threads.get(thread.name) is thread:
                    self._threads.pop(thread.name, None)
            raise

    def fail_active_job(
        self,
        job_id: str,
        *,
        job_type: str,
        stage: str,
        exc: Exception,
    ) -> None:
        """Best-effort persistence for an exception escaping a background worker."""
        try:
            self.repository.fail_job(
                str(job_id),
                failure=PipelineFailure(
                    code=f"{job_type}_failed",
                    component="desktop_execution_service",
                    stage=str(stage or job_type),
                    recoverability="retryable",
                    message=str(exc),
                    context={
                        "exception_type": exc.__class__.__name__,
                        "job_type": str(job_type),
                    },
                ),
            )
        except Exception:  # noqa: BLE001
            # Preserve the worker's original exception. The store method is
            # terminal-state guarded, so this path is only for persistence I/O.
            return

    def _thread_name_is_alive_locked(self, thread_name: str) -> bool:
        normalized_name = str(thread_name or "").strip()
        if not normalized_name:
            return False
        thread = self._threads.get(normalized_name)
        if thread is None:
            return False
        if thread.is_alive():
            return True
        self._threads.pop(normalized_name, None)
        return False

    def _thread_ident_is_alive_locked(self, thread_ident: Any) -> bool:
        try:
            normalized_ident = int(thread_ident)
        except (TypeError, ValueError):
            return False
        if normalized_ident <= 0:
            return False
        return any(thread.ident == normalized_ident and thread.is_alive() for thread in threading.enumerate())

    def _recover_stale_mutating_jobs_locked(self) -> None:
        terminal_statuses = {"success", "success_with_warnings", "failed", "interrupted"}
        recovered: list[dict[str, str]] = []
        now = time.monotonic()
        for thread_name, thread in list(self._threads.items()):
            if not thread.is_alive():
                self._threads.pop(thread_name, None)
        for job_type in list(sorted(self._active_mutating_jobs)):
            lease = self._mutating_job_leases.get(job_type, {})
            worker_thread_name = str(lease.get("worker_thread_name") or "").strip()
            if self._thread_name_is_alive_locked(worker_thread_name):
                continue

            job_id = str(lease.get("job_id") or "").strip()
            job_status = ""
            if job_id:
                try:
                    job_status = str(self.repository.get_job(job_id).get("status") or "").strip()
                except KeyError:
                    job_status = ""
            if job_status in terminal_statuses:
                recovered.append({"job_type": job_type, "job_id": job_id, "reason": f"terminal_status:{job_status}"})
                continue

            if self._thread_ident_is_alive_locked(lease.get("owner_thread_ident")):
                continue

            if job_status in {"starting", "running"}:
                self.repository.interrupt_job(
                    job_id,
                    reason="stale mutating job recovered after interrupted/dead current-session worker",
                )
                recovered.append({"job_type": job_type, "job_id": job_id, "reason": f"orphaned_status:{job_status}"})
                continue

            reserved_at = float(lease.get("reserved_at_monotonic") or 0.0)
            if reserved_at and now - reserved_at <= self._startup_handshake_timeout_sec:
                continue
            recovered.append({"job_type": job_type, "job_id": job_id, "reason": "expired_reservation"})

        for entry in recovered:
            normalized_job_type = str(entry.get("job_type") or "").strip()
            if not normalized_job_type:
                continue
            self._active_mutating_jobs.discard(normalized_job_type)
            self._mutating_job_leases.pop(normalized_job_type, None)
        if recovered:
            self.repository.add_audit_entry(
                "stale_mutating_jobs_recovered",
                {
                    "startup_session_id": self.startup_session_id,
                    "recovered": recovered,
                },
            )

    def reserve_mutating_job(self, job_type: str) -> None:
        normalized = _normalize_mutating_job_type(job_type)
        with self._lock:
            self._recover_stale_mutating_jobs_locked()
            if self._active_mutating_jobs:
                active_job_type = sorted(self._active_mutating_jobs)[0]
                raise self.user_error_cls(
                    message=f"已有执行中的任务：{_job_type_label(active_job_type)}",
                    error_code=ERROR_MUTATING_JOB_IN_PROGRESS,
                    http_status=409,
                    details={"active_job_type": active_job_type, "requested_job_type": normalized},
                )
            self._active_mutating_jobs.add(normalized)
            self._mutating_job_leases[normalized] = {
                "job_type": normalized,
                "job_id": "",
                "startup_session_id": self.startup_session_id,
                "owner_thread_ident": threading.get_ident(),
                "owner_thread_name": threading.current_thread().name,
                "worker_thread_name": "",
                "reserved_at_monotonic": time.monotonic(),
            }

    def release_mutating_job(self, job_type: str, *, job_id: str | None = None) -> bool:
        normalized = _normalize_mutating_job_type(job_type)
        requested_job_id = None if job_id is None else str(job_id or "").strip()
        with self._lock:
            lease = self._mutating_job_leases.get(normalized)
            if lease is None:
                return False
            bound_job_id = str(lease.get("job_id") or "").strip()
            current_thread_ident = threading.get_ident()
            owner_thread_ident = lease.get("owner_thread_ident")
            worker_thread_name = str(lease.get("worker_thread_name") or "").strip()
            current_thread_is_owner = owner_thread_ident == current_thread_ident
            current_thread_is_worker = bool(worker_thread_name) and worker_thread_name == threading.current_thread().name

            # A worker may outlive the request that launched it.  Never let a
            # stale worker release a newer lease for the same job type.
            if requested_job_id is not None:
                if bound_job_id and requested_job_id != bound_job_id:
                    return False
                if not bound_job_id and not current_thread_is_owner:
                    return False
            elif bound_job_id and not (current_thread_is_owner or current_thread_is_worker):
                return False

            self._active_mutating_jobs.discard(normalized)
            self._mutating_job_leases.pop(normalized, None)
            return True

    def bind_mutating_job(self, job_type: str, *, job_id: str = "", worker_thread_name: str = "") -> None:
        normalized = _normalize_mutating_job_type(job_type)
        with self._lock:
            lease = self._mutating_job_leases.get(normalized)
            if lease is None:
                raise RuntimeError(f"mutating job lease is not reserved: {normalized}")
            existing_job_id = str(lease.get("job_id") or "").strip()
            requested_job_id = str(job_id or "").strip()
            if existing_job_id and requested_job_id and existing_job_id != requested_job_id:
                raise RuntimeError(f"mutating job lease is already bound: {normalized}")
            if job_id:
                lease["job_id"] = requested_job_id
            if worker_thread_name:
                lease["worker_thread_name"] = str(worker_thread_name or "").strip()
            lease["bound_at_monotonic"] = time.monotonic()

    @contextmanager
    def mutating_job_scope(self, job_type: str):
        self.reserve_mutating_job(job_type)
        try:
            yield
        finally:
            self.release_mutating_job(job_type)

    def thread_job_stack(self) -> list[str]:
        stack = getattr(self._thread_state, "mutating_jobs", None)
        if stack is None:
            stack = []
            self._thread_state.mutating_jobs = stack
        return stack

    @contextmanager
    def thread_job_scope(self, job_type: str):
        normalized = _normalize_mutating_job_type(job_type)
        stack = self.thread_job_stack()
        stack.append(normalized)
        try:
            yield
        finally:
            stack.pop()

    def current_thread_holds_mutating_job(self, job_type: str) -> bool:
        normalized = _normalize_mutating_job_type(job_type)
        return normalized in self.thread_job_stack()

    def ingest_manual_import_file(
        self,
        file_path: str,
        *,
        import_scope: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        scope = _manual_import_scope(import_scope)
        basic = self.get_basic_settings()
        archive_root = str(basic["archive_root"])
        if str(scope.get("record_family") or "").strip() == "deal":
            archive_root = str(basic.get("deal_archive_root") or os.path.join(archive_root, "deal"))
        runner = self.build_ingest_runner(archive_root=archive_root)
        hint = build_business_hint_from_scope(scope)
        extra: Dict[str, Any] = {}
        if scope:
            extra["record_family"] = scope["record_family"]
            extra["business_id"] = scope["business_id"]
            if scope.get("business_label"):
                extra["business_label"] = scope["business_label"]
            if scope.get("exchange"):
                extra["exchange"] = scope["exchange"]
        if hint.get("project_type_fallback"):
            extra["project_type_fallback"] = str(hint["project_type_fallback"])
        return runner.ingest(
            ItemSavedPayload(
                source_file=str(file_path),
                exchange=str(scope.get("exchange") or ""),
                extra=extra,
            )
        )

    def manual_import_smoke_delay_seconds(self, file_path: str) -> float:
        raw_delay_ms = str(os.environ.get("PEAP_SMOKE_MANUAL_IMPORT_DELAY_MS") or "").strip()
        if not raw_delay_ms:
            return 0.0
        normalized_path = str(file_path or "").lower()
        if "smoke_delay" not in normalized_path:
            return 0.0
        try:
            delay_ms = int(raw_delay_ms)
        except (TypeError, ValueError):
            return 0.0
        return max(delay_ms, 0) / 1000.0

    def run_manual_import_job(
        self,
        *,
        job_id: str,
        files: list[str],
        ingest_file: Callable[[str], Dict[str, Any]] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        progress_label: str = "正在解析手动导入网页",
        failure_label: str = "手动导入失败",
        done_label: str = "手动导入完成",
    ) -> None:
        self.repository.ensure_job_running(job_id)
        imported = 0
        pending_review = 0
        pending = 0
        skipped = 0
        field_missing = 0
        failed = 0
        invalid_ingest_state = ""
        invalid_ingest_source_file = ""
        ingest = ingest_file or self.ingest_manual_import_file
        sleeper = sleep_fn or time.sleep
        for index, file_path in enumerate(files, start=1):
            if str(self.repository.get_job(job_id).get("status") or "").strip() != "running":
                return
            self.repository.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="reprocessing",
                    status="running",
                    payload={
                        "label": progress_label,
                        "task_index": index,
                        "task_total": len(files),
                        "task_label": os.path.basename(file_path),
                        "phase_percent": int(index * 100 / max(len(files), 1)),
                    },
                )
            )
            try:
                smoke_delay_seconds = self.manual_import_smoke_delay_seconds(file_path)
                if smoke_delay_seconds > 0:
                    sleeper(smoke_delay_seconds)
                if str(self.repository.get_job(job_id).get("status") or "").strip() != "running":
                    return
                result = ingest(file_path)
                state = _normalize_record_state_value(result.get("state"))
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.repository.update_job_counts(job_id, downloaded_inc=1, exception_inc=1)
                self.repository.append_event(
                    ItemProgressEvent(
                        job_id=job_id,
                        stage="reprocessing",
                        status="failed",
                        error_type="manual_import_failed",
                        error_message=str(exc),
                        payload={"label": failure_label, "source_file": file_path},
                    )
                )
                continue

            if state == "pending_review":
                imported += 1
                pending_review += 1
            elif state in {"pending_mapping", "mapping_conflict"}:
                imported += 1
                pending += 1
            elif state == "skipped":
                imported += 1
                skipped += 1
            elif state == "field_missing":
                imported += 1
                field_missing += 1
            elif state in {"ready", "conflict"}:
                imported += 1
            elif state not in _MANUAL_IMPORT_KNOWN_STATES:
                failed += 1
                if not invalid_ingest_state:
                    invalid_ingest_state = state or ""
                    invalid_ingest_source_file = file_path
            self.repository.update_job_counts(
                job_id,
                downloaded_inc=1,
                persisted_inc=1 if state in _MANUAL_IMPORT_PERSISTED_STATES else 0,
                exception_inc=1 if state not in _MANUAL_IMPORT_KNOWN_STATES else 0,
            )
            self.repository.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="reprocessing",
                    status=state or "done",
                    project_code=str(result.get("project_code") or ""),
                    archive_path=str(result.get("archive_path") or ""),
                    error_type=str(result.get("error_type") or ""),
                    error_message=str(result.get("last_error_message") or result.get("error_message") or ""),
                    payload={"label": done_label, "source_file": file_path, "state": state},
                )
            )

        final_status = "success"
        if invalid_ingest_state:
            final_status = "failed"
        elif imported <= 0:
            final_status = "failed"
        elif failed > 0 or pending_review > 0 or pending > 0 or skipped > 0 or field_missing > 0:
            final_status = "success_with_warnings"
        current_status = str(self.repository.get_job(job_id).get("status") or "")
        if current_status != "running":
            return
        summary = {
            "imported_count": imported,
            "pending_review_count": pending_review,
            "pending_mapping_count": pending,
            "skipped_count": skipped,
            "field_missing_count": field_missing,
            "failed_count": failed,
        }
        if invalid_ingest_state:
            summary.update(
                {
                    "failure_code": "invalid_ingest_state",
                    "failure_stage": "manual_import",
                    "failure_message": f"manual import ingest returned unknown state: {invalid_ingest_state}",
                    "invalid_ingest_state": invalid_ingest_state,
                    "invalid_ingest_source_file": invalid_ingest_source_file,
                }
            )
        self.repository.finish_job(
            job_id,
            status=final_status,
            summary=summary,
        )

    def launch_manual_import(
        self,
        payload: Dict[str, Any],
        *,
        ingest_file: Callable[[str], Dict[str, Any]] | None = None,
        start_background_thread: Callable[..., None] | None = None,
    ) -> Dict[str, Any]:
        self.reserve_mutating_job("manual_import")
        operation = None
        lease_job_id = ""
        try:
            raw_input_dir = payload.get("input_dir") if "input_dir" in payload else self.get_advanced_settings().get("raw_manual_root")
            if raw_input_dir == "":
                raw_input_dir = self.get_advanced_settings().get("raw_manual_root")
            input_dir = _parse_local_path(
                raw_input_dir,
                field_name="input_dir",
            )
            operation = self.write_coordinator.start_operation(
                "manual_import",
                {
                    "input_dir": input_dir,
                },
            )
            import_scope = _manual_import_scope(payload)
            if not input_dir or not os.path.isdir(input_dir):
                raise self.user_error_cls(
                    message=f"手动导入目录不存在：{input_dir or ''}",
                    error_code=ERROR_MANUAL_IMPORT_INPUT_DIR_NOT_FOUND,
                    http_status=400,
                    details={"input_dir": input_dir},
                )
            try:
                with os.scandir(input_dir):
                    pass
            except OSError as exc:
                raise self.user_error_cls(
                    message=f"手动导入目录不可访问：{input_dir}",
                    error_code=ERROR_INVALID_REQUEST,
                    http_status=400,
                    details={"input_dir": input_dir, "os_error": str(exc)},
                ) from exc
            files = _discover_import_files(input_dir)
            job_metadata: Dict[str, Any] = {"input_dir": input_dir, "discovered_count": len(files)}
            if import_scope:
                job_metadata.update(import_scope)
                job_metadata["scope"] = dict(import_scope)
            job_id = self.repository.create_job(
                "manual_import",
                metadata=job_metadata,
            )
            lease_job_id = job_id
            self.bind_mutating_job("manual_import", job_id=job_id)
            self.repository.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="manual_import_scan",
                    status="done",
                    payload={"label": "已整理待导入网页", "discovered_count": len(files)},
                )
            )
            thread_launcher = start_background_thread or self.start_background_thread
            if files:
                ingest = ingest_file
                if ingest is None:
                    def ingest(file_path: str) -> dict[str, Any]:
                        return self.ingest_manual_import_file(file_path, import_scope=import_scope)

                def _run_manual_import_wrapper() -> None:
                    try:
                        self.bind_mutating_job(
                            "manual_import",
                            job_id=job_id,
                            worker_thread_name=threading.current_thread().name,
                        )
                        self.repository.start_job(job_id)
                        self.run_manual_import_job(job_id=job_id, files=files, ingest_file=ingest)
                        final_job = self.repository.get_job(job_id)
                        operation.set_manifest(
                            {
                                "job": {
                                    "job_id": job_id,
                                    "job_type": "manual_import",
                                },
                                "summary": {
                                    "discovered_count": len(files),
                                    **_optional_mapping_field(
                                        final_job,
                                        "summary",
                                        field_name="final_job.summary",
                                    ),
                                },
                            }
                        )
                        if str(final_job.get("status") or "") in {"failed", "interrupted"}:
                            summary = _optional_mapping_field(
                                final_job,
                                "summary",
                                field_name="final_job.summary",
                            )
                            failure_code = str(summary.get("failure_code") or "job_failed")
                            operation.fail(
                                {
                                    "code": failure_code,
                                    "message": str(summary.get("message") or "manual import job failed"),
                                }
                            )
                        else:
                            operation.succeed()
                    except Exception as exc:  # noqa: BLE001
                        self.fail_active_job(
                            job_id,
                            job_type="manual_import",
                            stage="manual_import",
                            exc=exc,
                        )
                        if not operation.is_finished:
                            operation.fail(exc)
                        raise
                    finally:
                        self.release_mutating_job("manual_import", job_id=job_id)

                worker_name = f"peap-manual-import-{int(time.time())}"
                self.bind_mutating_job(
                    "manual_import",
                    job_id=job_id,
                    worker_thread_name=worker_name,
                )
                thread_launcher(
                    name=worker_name,
                    target=_run_manual_import_wrapper,
                )
            else:
                self.repository.start_job(job_id)
                self.repository.finish_job(
                    job_id,
                    status="success",
                    summary={
                        "imported_count": 0,
                        "pending_review_count": 0,
                        "pending_mapping_count": 0,
                        "skipped_count": 0,
                        "failed_count": 0,
                    },
                )
                operation.succeed(
                    {
                        "job": {
                            "job_id": job_id,
                            "job_type": "manual_import",
                        },
                        "summary": {
                            "discovered_count": len(files),
                            "imported_count": 0,
                            "pending_review_count": 0,
                            "pending_mapping_count": 0,
                            "skipped_count": 0,
                            "failed_count": 0,
                        },
                    }
                )
                self.release_mutating_job("manual_import", job_id=job_id)
            response = {
                "job_id": job_id,
                "job_type": "manual_import",
                "db_path": self.db_path,
                "input_dir": input_dir,
                "discovered_count": len(files),
            }
            if import_scope:
                response.update(import_scope)
                response["scope"] = dict(import_scope)
            return response
        except _UnsupportedSurfaceScopeError as exc:
            if operation is not None:
                operation.fail(
                    {
                        "code": ERROR_INVALID_REQUEST,
                        "message": "当前手动导入 scope 超出已交付记录来源支持范围",
                        "surface": exc.surface,
                    }
                )
            self.release_mutating_job("manual_import", job_id=lease_job_id or None)
            raise self.user_error_cls(
                message="当前手动导入 scope 超出已交付记录来源支持范围",
                error_code=ERROR_INVALID_REQUEST,
                http_status=400,
                details={
                    "scope": {
                        "exchange": exc.exchange,
                        "record_family": exc.record_family,
                        "business_id": exc.business_ids[0] if len(exc.business_ids) == 1 else "all",
                    },
                    "surface": exc.surface,
                    "business_ids": list(exc.business_ids),
                },
            ) from exc
        except Exception as exc:
            if lease_job_id:
                self.fail_active_job(
                    lease_job_id,
                    job_type="manual_import",
                    stage="manual_import_startup",
                    exc=exc,
                )
            if operation is not None:
                operation.fail(exc)
            self.release_mutating_job("manual_import", job_id=lease_job_id or None)
            raise

    def launch_archive_reprocess(
        self,
        payload: Dict[str, Any] | None = None,
        *,
        ingest_file: Callable[[str], Dict[str, Any]] | None = None,
        start_background_thread: Callable[..., None] | None = None,
    ) -> Dict[str, Any]:
        job_type = "archive_reprocess"
        self.reserve_mutating_job(job_type)
        operation = None
        lease_job_id = ""
        try:
            explicit_input_dir = isinstance(payload, Mapping) and "input_dir" in payload and payload.get("input_dir") != ""
            basic = self.get_basic_settings()
            normalized_payload = normalize_archive_reprocess_request(
                payload,
                default_input_dir=basic.get("archive_root") or "",
            )
            input_dir = normalized_payload["input_dir"]
            operation = self.write_coordinator.start_operation(job_type, dict(normalized_payload))
            if not input_dir:
                raise self.user_error_cls(
                    message="归档目录未配置",
                    error_code=ERROR_MANUAL_IMPORT_INPUT_DIR_NOT_FOUND,
                    http_status=400,
                    details={"input_dir": input_dir},
                )
            if explicit_input_dir and not os.path.isdir(input_dir):
                raise self.user_error_cls(
                    message=f"归档目录不存在：{input_dir}",
                    error_code=ERROR_MANUAL_IMPORT_INPUT_DIR_NOT_FOUND,
                    http_status=400,
                    details={"input_dir": input_dir},
                )
            os.makedirs(input_dir, exist_ok=True)
            files = _discover_import_files(input_dir)
            job_id = self.repository.create_job(
                job_type,
                metadata={"input_dir": input_dir, "discovered_count": len(files)},
            )
            lease_job_id = job_id
            self.bind_mutating_job(job_type, job_id=job_id)
            self.repository.append_event(
                ItemProgressEvent(
                    job_id=job_id,
                    stage="archive_reprocess_scan",
                    status="done",
                    payload={"label": "已整理归档网页", "discovered_count": len(files)},
                )
            )
            thread_launcher = start_background_thread or self.start_background_thread
            if files:
                ingest = ingest_file or self.ingest_manual_import_file

                def _run_archive_reprocess_wrapper() -> None:
                    try:
                        self.bind_mutating_job(
                            job_type,
                            job_id=job_id,
                            worker_thread_name=threading.current_thread().name,
                        )
                        self.repository.start_job(job_id)
                        self.run_manual_import_job(
                            job_id=job_id,
                            files=files,
                            ingest_file=ingest,
                            progress_label="正在重新解析归档网页",
                            failure_label="归档重新解析失败",
                            done_label="归档重新解析完成",
                        )
                        final_job = self.repository.get_job(job_id)
                        operation.set_manifest(
                            {
                                "job": {
                                    "job_id": job_id,
                                    "job_type": job_type,
                                },
                                "summary": {
                                    "discovered_count": len(files),
                                    **_optional_mapping_field(
                                        final_job,
                                        "summary",
                                        field_name="final_job.summary",
                                    ),
                                },
                            }
                        )
                        if str(final_job.get("status") or "") in {"failed", "interrupted"}:
                            summary = _optional_mapping_field(
                                final_job,
                                "summary",
                                field_name="final_job.summary",
                            )
                            operation.fail(
                                {
                                    "code": "job_failed",
                                    "message": str(summary.get("message") or "archive reprocess job failed"),
                                }
                            )
                        else:
                            operation.succeed()
                    except Exception as exc:  # noqa: BLE001
                        self.fail_active_job(
                            job_id,
                            job_type=job_type,
                            stage="archive_reprocess",
                            exc=exc,
                        )
                        if not operation.is_finished:
                            operation.fail(exc)
                        raise
                    finally:
                        self.release_mutating_job(job_type, job_id=job_id)

                worker_name = f"peap-archive-reprocess-{int(time.time())}"
                self.bind_mutating_job(
                    job_type,
                    job_id=job_id,
                    worker_thread_name=worker_name,
                )
                thread_launcher(
                    name=worker_name,
                    target=_run_archive_reprocess_wrapper,
                )
            else:
                self.repository.start_job(job_id)
                self.repository.finish_job(
                    job_id,
                    status="success",
                    summary={
                        "imported_count": 0,
                        "pending_review_count": 0,
                        "pending_mapping_count": 0,
                        "skipped_count": 0,
                        "failed_count": 0,
                    },
                )
                operation.succeed(
                    {
                        "job": {
                            "job_id": job_id,
                            "job_type": job_type,
                        },
                        "summary": {
                            "discovered_count": len(files),
                            "imported_count": 0,
                            "pending_review_count": 0,
                            "pending_mapping_count": 0,
                            "skipped_count": 0,
                            "failed_count": 0,
                        },
                    }
                )
                self.release_mutating_job(job_type, job_id=job_id)
            return {
                "job_id": job_id,
                "job_type": job_type,
                "db_path": self.db_path,
                "input_dir": input_dir,
                "discovered_count": len(files),
            }
        except Exception as exc:
            if lease_job_id:
                self.fail_active_job(
                    lease_job_id,
                    job_type=job_type,
                    stage="archive_reprocess_startup",
                    exc=exc,
                )
            if operation is not None:
                operation.fail(exc)
            self.release_mutating_job(job_type, job_id=lease_job_id or None)
            raise

    def run_export(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.run_export_with_contract(
            payload,
            run_ready_export_fn=run_ready_export,
            count_scope_fn=count_records_in_export_scope_by_state,
        )

    def run_export_with_contract(
        self,
        payload: Dict[str, Any],
        *,
        run_ready_export_fn,
        count_scope_fn,
    ) -> Dict[str, Any]:
        contract_payload = normalize_export_request_payload(payload)
        raw_payload, normalized_scope, scope = _records_normalize_request_scope(
            contract_payload,
            require_explicit_scope=True,
        )
        business_types = _records_scope_business_ids(normalized_scope)
        _validated_surface_scope(
            exchange=_normalize_exchange_code(normalized_scope.exchange),
            record_family=normalized_scope.record_family,
            business_ids=business_types,
            surface=SURFACE_EXPORT,
        )
        basic_settings = self.get_basic_settings()
        if _basic_settings_payload_is_corrupt(basic_settings):
            raise self.user_error_cls(
                message="basic settings are corrupt; repair or reset settings before exporting",
                error_code=ERROR_INVALID_REQUEST,
                http_status=400,
                details={
                    "settings_key": "app.settings.basic",
                    "reason": "settings_payload_corrupt",
                },
            )
        self.run_store_maintenance()

        def _run_export(operation) -> Dict[str, Any]:
            with self.mutating_job_scope("export_excel"):
                request = ExportRequest(
                    date_from=str(normalized_scope.date_from or "").strip() or None,
                    date_to=str(normalized_scope.date_to or "").strip() or None,
                    business_types=business_types,
                    exchange=_normalize_exchange_code(normalized_scope.exchange),
                    requested_state=str(normalized_scope.state or "all").strip() or "all",
                    keyword=str(normalized_scope.keyword or "").strip(),
                    requested_export_mode=str(raw_payload.get("requested_export_mode") or "full"),
                    output_dir=str(raw_payload.get("output_dir") or basic_settings["export_root"]),
                    record_family=normalized_scope.record_family,
                    retention_count=int(basic_settings.get("retention_count") or 20),
                )
                if not request.cursor_id:
                    request = replace(request, cursor_id=_default_cursor_id(request))
                event_scope = dict(scope)
                job_id = self.repository.create_job(
                    "export_excel",
                    metadata={
                        "date_from": request.date_from or "",
                        "date_to": request.date_to or "",
                        "business_types": list(request.business_types or []),
                        "exchange": request.exchange,
                        "output_dir": request.output_dir,
                        "record_family": request.record_family,
                        "scope": scope,
                    },
                )
                self.bind_mutating_job("export_excel", job_id=job_id)
                self.repository.start_job(job_id)
                self.repository.append_event(
                    ItemProgressEvent(
                        job_id=job_id,
                        stage="exporting",
                        status="running",
                        payload={
                            "label": "正在导出 Excel",
                            "scope": dict(event_scope),
                            "summary_payload": {
                                "kind": "export",
                                "task_label": "Excel 导出",
                                "phase_percent": 0,
                            },
                        },
                    )
                )
                export_audit_in_store_transaction = run_ready_export_fn is run_ready_export
                run_export_kwargs: Dict[str, Any] = {}
                if export_audit_in_store_transaction:
                    run_export_kwargs = {
                        "audit_action": "manual_export",
                        "audit_payload": {
                            "job_id": job_id,
                            "job_type": "export_excel",
                            "scope": scope,
                        },
                    }
                try:
                    result = self.repository.run_ready_export(
                        request,
                        run_ready_export_fn=run_ready_export_fn,
                        **run_export_kwargs,
                    )
                except Exception as exc:  # noqa: BLE001
                    summary = {
                        "job_id": job_id,
                        "job_type": "export_excel",
                        "scope": scope,
                        "export_id": "",
                        "cursor_id": request.cursor_id,
                        "requested_export_mode": request.requested_export_mode,
                        "new_records": 0,
                        "changed_records": 0,
                        "artifacts": [],
                        "status": "failed",
                        "failure_code": "export_failed",
                        "failure_message": str(exc),
                        "message": f"导出失败：{exc}",
                    }
                    self.repository.append_event(
                        ItemProgressEvent(
                            job_id=job_id,
                            stage="exporting",
                            status="failed",
                            error_type="export_failed",
                            error_message=str(exc),
                            payload={
                                "label": "导出失败",
                                "scope": dict(event_scope),
                                "summary_payload": {
                                    "kind": "export",
                                    "task_label": "Excel 导出",
                                    "phase_percent": 100,
                                },
                            },
                        )
                    )
                    self.repository.finish_job(job_id, status="failed", summary=summary)
                    self.repository.add_audit_entry("manual_export", summary)
                    operation.set_manifest(
                        {
                            "job": {"job_id": job_id, "job_type": "export_excel"},
                            "result": dict(summary),
                        }
                    )
                    operation.fail({"code": "export_failed", "message": str(exc)})
                    raise self.user_error_cls(
                        message=summary["message"],
                        error_code="export_failed",
                        http_status=500,
                        details={
                            "job_id": job_id,
                            "job_type": "export_excel",
                            "scope": dict(scope),
                            "failure_code": "export_failed",
                        },
                    ) from exc

                artifacts = [item.file_path for item in result.artifacts]
                export_status = "completed" if artifacts else "empty"
                message = f"导出完成，共生成 {len(artifacts)} 个文件"
                empty_reason_code = ""
                scope_state_counts: Dict[str, int] = {}
                if not artifacts:
                    scope_state_counts = self.repository.count_records_in_export_scope(
                        request,
                        count_scope_fn=count_scope_fn,
                    )
                    empty_reason_code, message = _classify_empty_export_result(
                        scope_state_counts,
                        field_missing_blocked_records=int(
                            getattr(result, "field_missing_blocked_records", 0) or 0
                        ),
                    )
                field_missing_diagnostics = _optional_list_value(
                    getattr(result, "field_missing_diagnostics", None),
                    field_name="export_result.field_missing_diagnostics",
                )
                summary = {
                    "job_id": job_id,
                    "job_type": "export_excel",
                    "scope": scope,
                    "export_id": result.export_id,
                    "cursor_id": result.cursor_id,
                    "requested_export_mode": request.requested_export_mode,
                    "revision_watermark": int(getattr(result, "revision_watermark", 0) or 0),
                    "field_missing_blocked_records": int(getattr(result, "field_missing_blocked_records", 0) or 0),
                    "field_missing_diagnostics": field_missing_diagnostics,
                    "retention_count": int(getattr(request, "retention_count", 20) or 20),
                    "new_records": result.new_records,
                    "changed_records": result.changed_records,
                    "artifacts": artifacts,
                    "status": export_status,
                    "message": message,
                }
                if not artifacts:
                    summary["empty_reason_code"] = empty_reason_code
                    summary["scope_state_counts"] = scope_state_counts
                    summary["field_missing_blocked_records"] = int(
                        getattr(result, "field_missing_blocked_records", 0) or 0
                    )
                self.repository.update_job_counts(
                    job_id,
                    downloaded_inc=int(result.new_records) + int(result.changed_records),
                    persisted_inc=len(artifacts),
                    exception_inc=0,
                )
                event_payload = {
                    "label": "导出完成" if artifacts else message,
                    "scope": dict(event_scope),
                    "artifacts": artifacts,
                    "new_records": int(result.new_records),
                    "changed_records": int(result.changed_records),
                    "field_missing_blocked_records": int(
                        getattr(result, "field_missing_blocked_records", 0) or 0
                    ),
                    "field_missing_diagnostics": field_missing_diagnostics,
                    "summary_payload": {
                        "kind": "export",
                        "task_label": "Excel 导出",
                        "phase_percent": 100,
                    },
                }
                if not artifacts:
                    event_payload["empty_reason_code"] = empty_reason_code
                    event_payload["scope_state_counts"] = dict(scope_state_counts)
                    event_payload["warning_code"] = empty_reason_code
                    event_payload["warning_message"] = message
                    event_payload["summary_payload"]["warning_code"] = empty_reason_code
                    event_payload["summary_payload"]["warning_message"] = message
                    event_payload["summary_payload"]["summary"] = {
                        "warning_code": empty_reason_code,
                        "warning_message": message,
                    }
                self.repository.append_event(
                    ItemProgressEvent(
                        job_id=job_id,
                        stage="exporting",
                        status="done" if artifacts else "empty",
                        payload=event_payload,
                    )
                )
                self.repository.finish_job(
                    job_id,
                    status="success" if artifacts else "success_with_warnings",
                    summary=summary,
                )
                if not artifacts or not export_audit_in_store_transaction:
                    self.repository.add_audit_entry("manual_export", summary)
                operation.set_manifest(
                    {
                        "job": {"job_id": job_id, "job_type": "export_excel"},
                        "result": dict(summary),
                    }
                )
                return summary

        return self.write_coordinator.write_operation(
            "export_excel",
            {
                "scope": scope,
                "requested_export_mode": str(raw_payload.get("requested_export_mode") or "full"),
            },
            _run_export,
        )

    def launch_one_click(
        self,
        payload: Dict[str, Any],
        *,
        start_background_thread: Callable[..., None] | None = None,
    ) -> Dict[str, Any]:
        try:
            _reject_non_list_family_scopes(payload)
            payload = normalize_one_click_request(
                payload,
                basic_settings=self.get_basic_settings(),
                advanced_settings=self.get_advanced_settings(),
            )
        except ValueError as exc:
            details: dict[str, Any] = {}
            if "family_scopes" in str(exc):
                details["family_scopes"] = _family_scopes_detail(payload)
            raise self.user_error_cls(
                message=str(exc),
                error_code=ERROR_INVALID_REQUEST,
                http_status=400,
                details=details,
            ) from exc
        browser_runtime = self.runtime_service.get_browser_runtime_status()
        product_readiness = self.runtime_service.build_product_readiness(browser_runtime=browser_runtime)
        if not bool(product_readiness.get("download_ready")):
            issue = _first_product_readiness_issue(product_readiness)
            raise self.user_error_cls(
                message=str(issue.get("message") or "download runtime not ready"),
                error_code=str(issue.get("code") or ERROR_BROWSER_RUNTIME_MISSING),
                http_status=409,
                details={
                    "product_readiness": product_readiness,
                    "browser_runtime": browser_runtime,
                },
            )
        record_families = payload.get("record_families")
        try:
            family_scopes = _normalize_family_scope_list(payload)
        except ValueError as exc:
            raise self.user_error_cls(
                message=str(exc),
                error_code=ERROR_INVALID_REQUEST,
                http_status=400,
                details={"family_scopes": _family_scopes_detail(payload)},
            ) from exc
        normalized_record_families = (
            [str(f).strip() for f in record_families if str(f).strip()]
            if isinstance(record_families, list)
            else []
        )
        if bool(payload.get("include_public_resource")) and not family_scopes:
            family_scopes = [
                {
                    "record_family": str(payload.get("record_family") or ""),
                    "business_id": str(payload.get("business_id") or ""),
                    "business_label": str(payload.get("business_label") or ""),
                    "exchange": str(payload.get("exchange") or "all"),
                }
            ]
        if len(normalized_record_families) == 1 and not str(payload.get("record_family") or "").strip():
            payload = dict(payload)
            payload["record_family"] = normalized_record_families[0]
            payload.pop("record_families", None)
        if family_scopes and bool(payload.get("include_public_resource")):
            return self.launch_multi_family_streaming_job(
                payload,
                family_scopes=family_scopes,
                job_type="one_click",
                auto_export=False,
                start_background_thread=start_background_thread,
            )
        if len(family_scopes) > 1:
            return self.launch_multi_family_streaming_job(
                payload,
                family_scopes=family_scopes,
                job_type="one_click",
                auto_export=False,
                start_background_thread=start_background_thread,
            )
        if len(normalized_record_families) > 1:
            raise self.user_error_cls(
                message="multi-family one-click request requires family_scopes",
                error_code=ERROR_INVALID_REQUEST,
                http_status=400,
                details={"record_families": normalized_record_families},
            )
        if len(family_scopes) == 1 and not str(payload.get("record_family") or "").strip():
            payload = dict(payload)
            payload.update(family_scopes[0])
            payload.pop("family_scopes", None)
        return self.launch_streaming_job(
            payload,
            job_type="one_click",
            auto_export=False,
            start_background_thread=start_background_thread,
        )

    def launch_download_ingest(
        self,
        payload: Dict[str, Any],
        *,
        start_background_thread: Callable[..., None] | None = None,
    ) -> Dict[str, Any]:
        try:
            _reject_non_list_family_scopes(payload)
            payload = normalize_one_click_request(
                payload,
                basic_settings=self.get_basic_settings(),
                advanced_settings=self.get_advanced_settings(),
            )
        except ValueError as exc:
            details: dict[str, Any] = {}
            if "family_scopes" in str(exc):
                details["family_scopes"] = _family_scopes_detail(payload)
            raise self.user_error_cls(
                message=str(exc),
                error_code=ERROR_INVALID_REQUEST,
                http_status=400,
                details=details,
            ) from exc
        browser_runtime = self.runtime_service.get_browser_runtime_status()
        product_readiness = self.runtime_service.build_product_readiness(browser_runtime=browser_runtime)
        if not bool(product_readiness.get("download_ready")):
            issue = _first_product_readiness_issue(product_readiness)
            raise self.user_error_cls(
                message=str(issue.get("message") or "download runtime not ready"),
                error_code=str(issue.get("code") or ERROR_BROWSER_RUNTIME_MISSING),
                http_status=409,
                details={
                    "product_readiness": product_readiness,
                    "browser_runtime": browser_runtime,
                },
            )
        record_families = payload.get("record_families")
        try:
            family_scopes = _normalize_family_scope_list(payload)
        except ValueError as exc:
            raise self.user_error_cls(
                message=str(exc),
                error_code=ERROR_INVALID_REQUEST,
                http_status=400,
                details={"family_scopes": _family_scopes_detail(payload)},
            ) from exc
        normalized_record_families = (
            [str(f).strip() for f in record_families if str(f).strip()]
            if isinstance(record_families, list)
            else []
        )
        if bool(payload.get("include_public_resource")) and not family_scopes:
            family_scopes = [
                {
                    "record_family": str(payload.get("record_family") or ""),
                    "business_id": str(payload.get("business_id") or ""),
                    "business_label": str(payload.get("business_label") or ""),
                    "exchange": str(payload.get("exchange") or "all"),
                }
            ]
        if len(normalized_record_families) == 1 and not str(payload.get("record_family") or "").strip():
            payload = dict(payload)
            payload["record_family"] = normalized_record_families[0]
            payload.pop("record_families", None)
        if family_scopes and bool(payload.get("include_public_resource")):
            return self.launch_multi_family_streaming_job(
                payload,
                family_scopes=family_scopes,
                job_type="download_ingest",
                auto_export=False,
                start_background_thread=start_background_thread,
            )
        if len(family_scopes) > 1:
            return self.launch_multi_family_streaming_job(
                payload,
                family_scopes=family_scopes,
                job_type="download_ingest",
                auto_export=False,
                start_background_thread=start_background_thread,
            )
        if len(normalized_record_families) > 1:
            raise self.user_error_cls(
                message="multi-family download ingest request requires family_scopes",
                error_code=ERROR_INVALID_REQUEST,
                http_status=400,
                details={"record_families": normalized_record_families},
            )
        if len(family_scopes) == 1 and not str(payload.get("record_family") or "").strip():
            payload = dict(payload)
            payload.update(family_scopes[0])
            payload.pop("family_scopes", None)
        return self.launch_streaming_job(
            payload,
            job_type="download_ingest",
            auto_export=False,
            start_background_thread=start_background_thread,
        )

    def launch_streaming_job(
        self,
        payload: Dict[str, Any],
        *,
        job_type: str,
        auto_export: bool,
        start_background_thread: Callable[..., None] | None = None,
    ) -> Dict[str, Any]:
        from peap.streaming_daily_pipeline import run_streaming_daily_pipeline

        self.reserve_mutating_job(job_type)
        lease_job_id = ""
        try:
            basic = self.get_basic_settings()
            advanced = self.get_advanced_settings()
            _validate_streaming_job_dates(payload)
            requested_exchange = _parse_text(
                payload.get("exchange"),
                field_name="exchange",
                default=_parse_text(basic.get("default_exchange"), field_name="default_exchange", default="all"),
            )
            normalized_exchange = _normalize_exchange_code(requested_exchange)
            requested_record_family = _parse_text(payload.get("record_family"), field_name="record_family", default="listing")
            requested_business_id = _parse_text(payload.get("business_id"), field_name="business_id", default="all")
            try:
                normalized_record_family, normalized_business_id = _validated_download_scope(
                    exchange=normalized_exchange,
                    record_family=requested_record_family,
                    business_id=requested_business_id,
                )
                scope_business_ids = (
                    [normalized_business_id]
                    if normalized_business_id != "all"
                    else sorted(
                        {
                            str(binding.business_id or "").strip()
                            for binding in iter_source_business_bindings(record_family=normalized_record_family)
                            if str(binding.business_id or "").strip()
                        }
                    )
                )
                _validated_surface_scope(
                    exchange=normalized_exchange,
                    record_family=normalized_record_family,
                    business_ids=scope_business_ids,
                    surface=SURFACE_ONE_CLICK,
                )
            except _NonExecutableDownloadScopeError as exc:
                raise self.user_error_cls(
                    message="下载任务尚不可执行",
                    error_code=ERROR_INVALID_REQUEST,
                    http_status=400,
                    details={
                        "scope": {
                            "exchange": exc.exchange,
                            "record_family": exc.record_family,
                            "business_id": exc.business_id,
                        },
                        "task_ids": list(exc.task_ids),
                    },
                ) from exc
            except _UnsupportedSurfaceScopeError as exc:
                raise self.user_error_cls(
                    message="当前作用域超出已交付产品合同支持范围",
                    error_code=ERROR_INVALID_REQUEST,
                    http_status=400,
                    details={
                        "scope": {
                            "exchange": exc.exchange,
                            "record_family": exc.record_family,
                            "business_id": exc.business_ids[0] if len(exc.business_ids) == 1 else "all",
                        },
                        "surface": exc.surface,
                        "business_ids": list(exc.business_ids),
                    },
                ) from exc
            except KeyError as exc:
                raise self.user_error_cls(
                    message="下载任务作用域不受支持",
                    error_code=ERROR_INVALID_REQUEST,
                    http_status=400,
                    details={
                        "scope": {
                            "exchange": normalized_exchange,
                            "record_family": requested_record_family,
                            "business_id": requested_business_id,
                        }
                    },
                ) from exc
            normalized_business_label = ""
            if normalized_business_id != "all":
                try:
                    normalized_business_label = get_business_descriptor(
                        normalized_business_id,
                        family_id=normalized_record_family,
                    ).canonical_label
                except KeyError:
                    normalized_business_label = ""
            concurrency = _parse_positive_int(
                payload.get("concurrency"),
                field_name="concurrency",
                default=int(basic["default_concurrency"]),
            )
            page_size = payload.get("page_size")
            resolved_page_size = (
                None
                if page_size in {None, ""}
                else _parse_positive_int(page_size, field_name="page_size", default=1)
            )
            max_pages = payload.get("max_pages")
            resolved_max_pages = (
                None
                if max_pages in {None, ""}
                else _parse_positive_int(max_pages, field_name="max_pages", default=1)
            )
            save_json = _parse_bool(
                payload.get("save_json"),
                field_name="save_json",
                default=bool(advanced.get("save_json", False)),
            )
            no_resume = _parse_bool(payload.get("no_resume"), field_name="no_resume")
            verbose = _parse_bool(payload.get("verbose"), field_name="verbose")
            postprocess_config = str(
                payload.get("postprocess_config")
                or advanced.get("postprocess_config")
                or ""
            )
            deal_root = basic.get("deal_archive_root") or os.path.join(basic["archive_root"], "deal")
            resolved_archive_root = deal_root if normalized_record_family == "deal" else basic["archive_root"]
            job_metadata = {
                "start_date": str(payload.get("start_date") or ""),
                "end_date": str(payload.get("end_date") or ""),
                "exchange": normalized_exchange,
                "record_family": normalized_record_family,
                "business_id": normalized_business_id,
                "business_label": normalized_business_label,
                "concurrency": concurrency,
                "page_size": resolved_page_size,
                "max_pages": resolved_max_pages,
                "save_json": save_json,
                "no_resume": no_resume,
                "verbose": verbose,
                "postprocess_config": postprocess_config,
                "scope": {
                    "record_family": normalized_record_family,
                    "business_id": normalized_business_id,
                    "business_label": normalized_business_label,
                    "exchange": normalized_exchange,
                },
                "archive_root": resolved_archive_root,
                "export_root": basic["export_root"],
            }
            response: dict[str, Any] = {"job_id": uuid.uuid4().hex, "db_path": self.db_path}
            lease_job_id = str(response["job_id"])
            self.bind_mutating_job(job_type, job_id=lease_job_id)
            ready = threading.Event()

            def _job_created(_callback_job_id: str, db_path: str) -> None:
                callback_job_id = str(_callback_job_id or "").strip()
                if callback_job_id and callback_job_id != str(response["job_id"]):
                    raise RuntimeError(
                        f"{job_type} job startup returned unexpected job_id: {callback_job_id}"
                    )
                response["db_path"] = db_path
                ready.set()

            args = _namespace(
                start_date=str(payload.get("start_date") or ""),
                end_date=str(payload.get("end_date") or ""),
                exchange=normalized_exchange,
                record_family=normalized_record_family,
                business_id=normalized_business_id,
                business_label=normalized_business_label,
                concurrency=concurrency,
                page_size=resolved_page_size,
                max_pages=resolved_max_pages,
                with_refresh=False,
                no_resume=no_resume,
                save_json=save_json,
                postprocess_config=postprocess_config,
                verbose=verbose,
                streaming_db=self.db_path,
                no_auto_export=not auto_export,
            )

            # Persist the asynchronous job before pipeline bootstrap. Database
            # migration and maintenance can legitimately exceed the launch
            # handshake timeout; they must not make an already-started worker
            # look like a startup failure to the request thread.
            self.repository.create_job(
                str(job_type),
                metadata=job_metadata,
                job_id=str(response["job_id"]),
            )
            _job_created(str(response["job_id"]), self.db_path)

            def _mark_startup_failed(exc: Exception) -> None:
                job_id = str(response["job_id"])
                try:
                    try:
                        self.repository.get_job(job_id)
                    except KeyError:
                        self.repository.create_job(
                            str(job_type),
                            metadata=job_metadata,
                            job_id=job_id,
                        )
                    self.repository.fail_job(
                        job_id,
                        failure=PipelineFailure(
                            code="job_startup_failed",
                            component="desktop_app_service",
                            stage="startup",
                            recoverability="retryable",
                            message=str(exc),
                            context={"exception_type": exc.__class__.__name__},
                        ),
                    )
                finally:
                    ready.set()

            def _run() -> None:
                try:
                    self.bind_mutating_job(
                        job_type,
                        job_id=str(response["job_id"]),
                        worker_thread_name=threading.current_thread().name,
                    )
                    with playwright_env(str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", ""))):
                        run_streaming_daily_pipeline(
                            args,
                            config_obj=self.config,
                            emit_console=False,
                            job_created_callback=_job_created,
                            job_type=job_type,
                            archive_root=resolved_archive_root,
                            export_root=basic["export_root"],
                            auto_export=auto_export,
                            job_id=str(response["job_id"]),
                        )
                except Exception as exc:
                    _mark_startup_failed(exc)
                finally:
                    self.release_mutating_job(job_type, job_id=str(response["job_id"]))

            (start_background_thread or self.start_background_thread)(
                name=f"peap-{job_type}-{int(time.time())}",
                target=_run,
            )
            if not ready.wait(timeout=self._startup_handshake_timeout_sec):
                _mark_startup_failed(
                    RuntimeError(f"{job_type} job startup handshake timed out")
                )
                raise RuntimeError(f"{job_type} job startup handshake timed out")
            response["job_type"] = job_type
            if not str(response.get("job_id") or "").strip():
                raise RuntimeError(f"{job_type} job did not provide job_id")
            return response
        except Exception:
            self.release_mutating_job(job_type, job_id=lease_job_id or None)
            raise

    def launch_multi_family_streaming_job(
        self,
        payload: Dict[str, Any],
        *,
        family_scopes: list[dict[str, str]],
        job_type: str,
        auto_export: bool,
        start_background_thread: Callable[..., None] | None = None,
    ) -> Dict[str, Any]:
        """Launch a single background job that processes multiple record families sequentially."""
        from peap.streaming_daily_pipeline import run_streaming_daily_pipeline
        from scripts.collect_public_resource_deals import collect_date_range

        if not family_scopes:
            raise self.user_error_cls(
                message="family_scopes are required for multi-family streaming job",
                error_code=ERROR_INVALID_REQUEST,
                http_status=400,
                details={"family_scopes": []},
            )
        self.reserve_mutating_job(job_type)
        lease_job_id = ""
        try:
            basic = self.get_basic_settings()
            advanced = self.get_advanced_settings()
            _validate_streaming_job_dates(payload)
            requested_exchange = _parse_text(
                payload.get("exchange"),
                field_name="exchange",
                default=_parse_text(basic.get("default_exchange"), field_name="default_exchange", default="all"),
            )
            normalized_exchange = _normalize_exchange_code(requested_exchange)

            family_specs: list[dict[str, Any]] = []
            for requested_scope in family_scopes:
                requested_scope_exchange = _parse_text(
                    requested_scope.get("exchange"),
                    field_name="exchange",
                    default=normalized_exchange,
                )
                requested_family = _parse_text(requested_scope.get("record_family"), field_name="record_family")
                requested_business_id = _parse_text(requested_scope.get("business_id"), field_name="business_id")
                requested_exchange = _normalize_exchange_code(requested_scope_exchange)
                try:
                    normalized_family, normalized_business_id = _validated_download_scope(
                        exchange=requested_exchange,
                        record_family=requested_family,
                        business_id=requested_business_id,
                    )
                except KeyError as exc:
                    raise self.user_error_cls(
                        message="下载任务作用域不受支持",
                        error_code=ERROR_INVALID_REQUEST,
                        http_status=400,
                        details={
                            "scope": {
                                "exchange": requested_exchange,
                                "record_family": requested_family,
                                "business_id": requested_business_id,
                            }
                        },
                    ) from exc
                scope_business_ids = (
                    [normalized_business_id]
                    if normalized_business_id != "all"
                    else sorted(
                        {
                            str(binding.business_id or "").strip()
                            for binding in iter_source_business_bindings(record_family=normalized_family)
                            if str(binding.business_id or "").strip()
                        }
                    )
                )
                _validated_surface_scope(
                    exchange=requested_exchange,
                    record_family=normalized_family,
                    business_ids=scope_business_ids,
                    surface=SURFACE_ONE_CLICK,
                )
                business_label = ""
                if normalized_business_id != "all":
                    try:
                        business_label = get_business_descriptor(
                            normalized_business_id,
                            family_id=normalized_family,
                        ).canonical_label
                    except KeyError:
                        business_label = ""
                deal_root = basic.get("deal_archive_root") or os.path.join(basic["archive_root"], "deal")
                archive_root = deal_root if normalized_family == "deal" else basic["archive_root"]
                family_specs.append({
                    "record_family": normalized_family,
                    "business_id": normalized_business_id,
                    "business_label": business_label,
                    "exchange": requested_exchange,
                    "archive_root": archive_root,
                })

            concurrency = _parse_positive_int(
                payload.get("concurrency"),
                field_name="concurrency",
                default=int(basic["default_concurrency"]),
            )
            page_size = payload.get("page_size")
            resolved_page_size = (
                None
                if page_size in {None, ""}
                else _parse_positive_int(page_size, field_name="page_size", default=1)
            )
            max_pages = payload.get("max_pages")
            resolved_max_pages = (
                None
                if max_pages in {None, ""}
                else _parse_positive_int(max_pages, field_name="max_pages", default=1)
            )
            save_json = _parse_bool(
                payload.get("save_json"),
                field_name="save_json",
                default=bool(advanced.get("save_json", False)),
            )
            no_resume = _parse_bool(payload.get("no_resume"), field_name="no_resume")
            include_public_resource = _parse_bool(
                payload.get("include_public_resource"),
                field_name="include_public_resource",
                default=False,
            )
            verbose = _parse_bool(payload.get("verbose"), field_name="verbose")
            postprocess_config = str(
                payload.get("postprocess_config")
                or advanced.get("postprocess_config")
                or ""
            )
            response: dict[str, Any] = {"job_id": uuid.uuid4().hex, "db_path": self.db_path}
            lease_job_id = str(response["job_id"])
            self.bind_mutating_job(job_type, job_id=lease_job_id)
            ready = threading.Event()
            job_metadata = {
                "start_date": str(payload.get("start_date") or ""),
                "end_date": str(payload.get("end_date") or ""),
                "exchange": normalized_exchange,
                "record_family": "",
                "record_families": [spec["record_family"] for spec in family_specs],
                "family_scopes": [
                    {
                        "record_family": spec["record_family"],
                        "business_id": spec["business_id"],
                        "business_label": spec.get("business_label", ""),
                        "exchange": spec["exchange"],
                    }
                    for spec in family_specs
                ],
                "concurrency": concurrency,
                "page_size": resolved_page_size,
                "max_pages": resolved_max_pages,
                "save_json": save_json,
                "no_resume": no_resume,
                "include_public_resource": include_public_resource,
                "verbose": verbose,
                "postprocess_config": postprocess_config,
                "archive_root": basic["archive_root"],
                "export_root": basic["export_root"],
            }
            self.repository.create_job(str(job_type), metadata=job_metadata, job_id=str(response["job_id"]))
            self.repository.start_job(str(response["job_id"]))
            ready.set()

            def _mark_startup_failed(exc: Exception) -> None:
                try:
                    self.repository.fail_job(
                        str(response["job_id"]),
                        failure=PipelineFailure(
                            code="job_startup_failed",
                            component="desktop_app_service",
                            stage="startup",
                            recoverability="retryable",
                            message=str(exc),
                            context={"exception_type": exc.__class__.__name__},
                        ),
                    )
                finally:
                    ready.set()

            def _run() -> None:
                exit_code = 0
                archive_audit_scopes: list[dict[str, Any]] = []
                try:
                    self.bind_mutating_job(
                        job_type,
                        job_id=str(response["job_id"]),
                        worker_thread_name=threading.current_thread().name,
                    )
                    with playwright_env(str(getattr(self.config, "PLAYWRIGHT_BROWSERS_PATH", ""))):
                        for spec in family_specs:
                            args = _namespace(
                                start_date=str(payload.get("start_date") or ""),
                                end_date=str(payload.get("end_date") or ""),
                                exchange=spec["exchange"],
                                record_family=spec["record_family"],
                                business_id=spec["business_id"],
                                business_label=spec.get("business_label", ""),
                                concurrency=concurrency,
                                page_size=resolved_page_size,
                                max_pages=resolved_max_pages,
                                with_refresh=False,
                                no_resume=no_resume,
                                save_json=save_json,
                                postprocess_config=postprocess_config,
                                verbose=verbose,
                                streaming_db=self.db_path,
                                no_auto_export=not auto_export,
                            )
                            result = run_streaming_daily_pipeline(
                                args,
                                config_obj=self.config,
                                emit_console=False,
                                job_created_callback=None,
                                job_type=job_type,
                                archive_root=spec["archive_root"],
                                export_root=basic["export_root"],
                                auto_export=auto_export,
                                job_id=str(response["job_id"]),
                                manage_job_lifecycle=False,
                            )
                            exit_code = max(exit_code, int(getattr(result, "exit_code", 0) or 0))
                            scoped_archive_audit = _pipeline_archive_audit_scope(result, spec)
                            if scoped_archive_audit:
                                archive_audit_scopes.append(scoped_archive_audit)
                        public_resource_summary: dict[str, Any] = {}
                        if include_public_resource:
                            start_date, end_date = _resolved_job_date_range(payload)
                            public_resource_archive_root = os.path.join(
                                str(
                                    basic.get("deal_archive_root")
                                    or os.path.join(basic["archive_root"], "deal")
                                ),
                                "public_resource__deal__deal_equity_transfer",
                            )
                            public_resource_evidence_root = os.path.join(
                                str(basic["archive_root"]),
                                "_evidence",
                                "public_resource",
                            )
                            public_resource_export_root = os.path.join(
                                str(basic["export_root"]),
                                "public_resource",
                            )

                            def _public_resource_progress(progress: dict[str, Any]) -> None:
                                task_index = int(
                                    progress.get("current")
                                    or progress.get("period_index")
                                    or progress.get("page")
                                    or 0
                                )
                                task_total = int(
                                    progress.get("total")
                                    or progress.get("period_total")
                                    or progress.get("pages")
                                    or 0
                                )
                                self.repository.append_event(
                                    ItemProgressEvent(
                                        job_id=str(response["job_id"]),
                                        stage="save_pages",
                                        status="running",
                                        record_family="deal",
                                        payload={
                                            "label": "正在下载公共资源网成交",
                                            "task_label": str(
                                                progress.get("month") or "公共资源网成交"
                                            ),
                                            "task_index": task_index,
                                            "task_total": task_total,
                                            "phase_percent": 85,
                                            "source_id": "public_resource",
                                            "summary": dict(progress),
                                        },
                                    )
                                )

                            try:
                                collected_public_resource = collect_date_range(
                                    start_date,
                                    end_date,
                                    evidence_root=public_resource_evidence_root,
                                    manual_root=public_resource_archive_root,
                                    export_root=public_resource_export_root,
                                    resume=not no_resume,
                                    progress_callback=_public_resource_progress,
                                )
                            except Exception as exc:  # noqa: BLE001
                                exit_code = max(exit_code, 1)
                                self.repository.update_job_counts(
                                    str(response["job_id"]),
                                    exception_inc=1,
                                )
                                public_resource_summary = {
                                    "status": "failed",
                                    "record_count": 0,
                                    "workbook": "",
                                    "evidence_root": public_resource_evidence_root,
                                    "archive_root": public_resource_archive_root,
                                    "error_type": "public_resource_collection_failed",
                                    "error_message": str(exc),
                                }
                                self.repository.append_event(
                                    ItemProgressEvent(
                                        job_id=str(response["job_id"]),
                                        stage="save_pages",
                                        status="failed",
                                        record_family="deal",
                                        error_type="public_resource_collection_failed",
                                        error_message=str(exc),
                                        payload={
                                            "label": "公共资源网成交下载失败",
                                            "source_id": "public_resource",
                                            "task_label": "公共资源网成交",
                                            "phase_percent": 100,
                                            "summary": dict(public_resource_summary),
                                        },
                                    )
                                )
                            else:
                                downloaded_count = int(
                                    collected_public_resource.get("unique_selected") or 0
                                )
                                public_resource_summary = {
                                    "status": "success",
                                    "record_count": downloaded_count,
                                    "workbook": str(
                                        collected_public_resource.get("workbook") or ""
                                    ),
                                    "evidence_root": str(
                                        collected_public_resource.get("evidence_root")
                                        or public_resource_evidence_root
                                    ),
                                    "archive_root": str(
                                        collected_public_resource.get("manual_root")
                                        or public_resource_archive_root
                                    ),
                                }
                                self.repository.update_job_counts(
                                    str(response["job_id"]),
                                    downloaded_inc=downloaded_count,
                                    persisted_inc=downloaded_count,
                                )
                                self.repository.append_event(
                                    ItemProgressEvent(
                                        job_id=str(response["job_id"]),
                                        stage="save_pages",
                                        status="done",
                                        record_family="deal",
                                        payload={
                                            "label": "公共资源网成交下载及解析完成",
                                            "source_id": "public_resource",
                                            "task_label": "公共资源网成交",
                                            "phase_percent": 98,
                                            "summary": dict(public_resource_summary),
                                        },
                                    )
                                )
                    job_info = self.repository.get_job(str(response["job_id"]))
                    job_events = self.repository.list_job_events(str(response["job_id"]), limit=100000)
                    status_counts = self.repository.get_job_event_counts(str(response["job_id"]))
                    review_statuses = {"pending_review", "pending_mapping", "mapping_conflict"}
                    has_review_backlog = any(str(event.get("status") or "") in review_statuses for event in job_events)
                    warning_summary = _job_events_warning_summary(job_events)
                    archive_audit_summary = _multi_family_download_archive_audit_summary(archive_audit_scopes)
                    has_warning_summary = bool(warning_summary["warning_code"] or warning_summary["warning_message"])
                    if exit_code == 0 and has_failed_job_event(job_events):
                        exit_code = 1
                    final_status = "failed"
                    if exit_code == 0:
                        final_status = (
                            "success_with_warnings"
                            if int(job_info.get("exception_count") or 0) > 0 or has_review_backlog or has_warning_summary
                            else "success"
                        )
                    self.repository.finish_job(
                        str(response["job_id"]),
                        status=final_status,
                        summary={
                            "download_exit_code": exit_code,
                            "downloaded_count": int(job_info.get("downloaded_count") or 0),
                            "persisted_count": int(job_info.get("persisted_count") or 0),
                            "exception_count": int(job_info.get("exception_count") or 0),
                            "pending_review": any(str(event.get("status") or "") == "pending_review" for event in job_events),
                            "pending_mapping": any(str(event.get("status") or "") == "pending_mapping" for event in job_events),
                            "pending_mapping_count": int(status_counts.get("pending_mapping", 0)),
                            "pending_review_count": int(status_counts.get("pending_review", 0)),
                            "mapping_conflict_count": int(status_counts.get("mapping_conflict", 0)),
                            "skipped_count": int(status_counts.get("skipped", 0)),
                            "record_families": [spec["record_family"] for spec in family_specs],
                            "public_resource": public_resource_summary,
                            **archive_audit_summary,
                            **failure_summary_fields(job_events),
                            **warning_summary,
                        },
                    )
                except Exception as exc:
                    _mark_startup_failed(exc)
                finally:
                    self.release_mutating_job(job_type, job_id=str(response["job_id"]))

            (start_background_thread or self.start_background_thread)(
                name=f"peap-{job_type}-{int(time.time())}",
                target=_run,
            )
            if not ready.wait(timeout=self._startup_handshake_timeout_sec):
                _mark_startup_failed(
                    RuntimeError(f"{job_type} job startup handshake timed out")
                )
                raise RuntimeError(f"{job_type} job startup handshake timed out")
            response["job_type"] = job_type
            if not str(response.get("job_id") or "").strip():
                raise RuntimeError(f"{job_type} job did not provide job_id")
            return response
        except Exception:
            self.release_mutating_job(job_type, job_id=lease_job_id or None)
            raise
