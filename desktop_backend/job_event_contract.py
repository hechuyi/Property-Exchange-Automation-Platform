"""API-facing job event view contract.

This module turns raw stored ItemProgressEvent rows into stable resource views
for `/api/jobs/{job_id}/events`, so the frontend does not need to understand
pipeline-internal payload nesting.
"""

from __future__ import annotations

from typing import Any, Mapping

from .domain.constants import JOB_PHASE_LABELS
from .domain.normalizers import (
    normalize_job_event_payload,
    normalize_record_state_value,
)
from .progress_contract import extract_job_identity


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _object(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(value)
    except Exception:
        raise ValueError(f"{field_name} must be an integer") from None


def _int_field(source: Mapping[str, Any], key: str, *, field_name: str, default: int = 0) -> int:
    if key not in source:
        return default
    return _int(source.get(key), field_name=field_name)


def _state_counts(value: Any, *, field_name: str = "scope_state_counts") -> dict[str, int]:
    source = _object(value, field_name=field_name)
    return {
        str(key or "").strip(): _int(count, field_name=f"{field_name}.{str(key or '').strip()}")
        for key, count in source.items()
        if str(key or "").strip()
    }


def _validate_payload_shapes(payload: Mapping[str, Any]) -> None:
    if "summary" in payload:
        _object(payload.get("summary"), field_name="summary")
    if "summary_payload" not in payload:
        return
    summary_payload = _object(payload.get("summary_payload"), field_name="summary_payload")
    if "summary" in summary_payload:
        _object(summary_payload.get("summary"), field_name="summary")
    if "scope_state_counts" in summary_payload:
        _state_counts(summary_payload.get("scope_state_counts"))


def _event_identity_with_parent(
    raw_source: Mapping[str, Any],
    *,
    parent_job: Mapping[str, Any] | None,
) -> dict[str, Any]:
    event_identity = extract_job_identity(raw_source)
    if parent_job is None:
        return event_identity
    if not isinstance(parent_job, Mapping):
        raise ValueError("parent_job must be an object")
    parent_identity = extract_job_identity(parent_job)
    event_scope = event_identity.get("scope")
    if isinstance(event_scope, Mapping) and event_scope:
        return event_identity
    if any(
        str(event_identity.get(field) or "").strip()
        for field in ("record_family", "business_id", "business_label")
    ):
        # A partially scoped event keeps its own identity.  Parent metadata
        # is only a fallback for events that carry no identity at all.
        return event_identity
    return parent_identity


def build_job_event_view(
    raw_event: Mapping[str, Any],
    *,
    parent_job: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw_event, Mapping):
        raise ValueError("raw_event must be an object")
    raw_source = dict(raw_event)
    payload_source = _object(raw_source.get("payload"), field_name="payload")
    _validate_payload_shapes(payload_source)
    if "scope" in payload_source and "scope" not in raw_source:
        raw_source["scope"] = payload_source.get("scope")
    if "metadata" in payload_source and "metadata" not in raw_source:
        raw_source["metadata"] = payload_source.get("metadata")
    event = normalize_job_event_payload(raw_source)
    identity = _event_identity_with_parent(raw_source, parent_job=parent_job)
    payload = _dict(event.get("payload"))
    summary_payload = _dict(payload.get("summary_payload"))
    summary = _dict(summary_payload.get("summary"))
    empty_reason_code = str(
        raw_source.get("empty_reason_code")
        or payload.get("empty_reason_code")
        or payload_source.get("empty_reason_code")
        or summary_payload.get("empty_reason_code")
        or ""
    ).strip()
    scope_state_counts = _state_counts(
        raw_source.get("scope_state_counts")
        or payload.get("scope_state_counts")
        or payload_source.get("scope_state_counts")
        or summary_payload.get("scope_state_counts")
    )
    stage_code = str(event.get("stage") or "").strip()
    stage_label = str(JOB_PHASE_LABELS.get(stage_code) or stage_code)
    label = str(payload.get("label") or stage_label).strip()
    source_id = str(payload.get("source_id") or "").strip()
    status = str(event.get("status") or "").strip()
    record_state = normalize_record_state_value(payload.get("state"))
    error_code = str(event.get("error_code") or "").strip()
    error_message = str(event.get("error_message") or "").strip()
    warning_code = str(event.get("warning_code") or summary_payload.get("warning_code") or empty_reason_code or "").strip()
    warning_message = str(event.get("warning_message") or summary_payload.get("warning_message") or "").strip()
    if status == "skipped" and error_code == "skip_parse":
        warning_code = warning_code or "skip_parse"
        warning_message = warning_message or error_message
        error_code = ""
        error_message = ""
    return {
        "event_id": str(event.get("event_id") or ""),
        "record_family": identity["record_family"],
        "business_id": identity["business_id"],
        "business_label": identity["business_label"],
        "scope": identity["scope"],
        "stage_code": stage_code,
        "stage_label": stage_label,
        "status": str(event.get("status") or "").strip(),
        "label": label,
        "source_id": source_id,
        "kind": str(summary_payload.get("kind") or stage_code).strip(),
        "task_label": str(summary_payload.get("task_label") or "").strip(),
        "task_index": _int_field(summary_payload, "task_index", field_name="summary_payload.task_index"),
        "task_total": _int_field(summary_payload, "task_total", field_name="summary_payload.task_total"),
        "phase_percent": _int_field(summary_payload, "phase_percent", field_name="summary_payload.phase_percent"),
        "summary": summary,
        "project_code": str(event.get("project_code") or "").strip(),
        "record_state": record_state,
        "error_code": error_code,
        "error_message": error_message,
        "warning_code": warning_code,
        "warning_message": warning_message,
        "empty_reason_code": empty_reason_code,
        "scope_state_counts": scope_state_counts,
    }
