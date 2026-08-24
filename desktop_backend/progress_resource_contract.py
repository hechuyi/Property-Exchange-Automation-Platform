"""HTTP response contract helpers for public progress resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .progress_contract import build_progress_view

_LEGACY_RESOLUTION_SCOPE_STRINGS = {"mapping_resolution", "business_resolution"}


def _parse_int(value: Any, *, field_name: str, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(value)
    except Exception:
        raise ValueError(f"{field_name} must be an integer") from None


def _optional_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return dict(value)


def _validate_scope_snapshot(job_payload: Mapping[str, Any]) -> None:
    scope = job_payload.get("scope")
    if scope is not None and not isinstance(scope, Mapping):
        raise ValueError("job.scope must be a mapping")
    metadata = job_payload.get("metadata")
    if metadata is not None and not isinstance(metadata, Mapping):
        raise ValueError("job.metadata must be a mapping")
    if isinstance(metadata, Mapping):
        metadata_scope = metadata.get("scope")
        if metadata_scope is not None and not isinstance(metadata_scope, Mapping):
            if isinstance(metadata_scope, str) and metadata_scope.strip() in _LEGACY_RESOLUTION_SCOPE_STRINGS:
                return
            raise ValueError("job.metadata.scope must be a mapping")


def _stage_summary_payload(raw: Mapping[str, Any]) -> Any:
    if "latest_stage_summary" not in raw:
        return None
    value = raw.get("latest_stage_summary")
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return dict(value)
    raise ValueError("latest_stage_summary must be a mapping or text")


def _progress_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return dict(value)


def _progress_list(value: Any, *, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return list(value)


def build_progress_resource(
    payload: Mapping[str, Any] | None,
    *,
    job: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    raw = _optional_mapping(payload, field_name="payload")
    job_payload = _optional_mapping(job, field_name="job")
    _validate_scope_snapshot(job_payload)
    latest_stage_summary = _stage_summary_payload(raw)
    phase_percent = _parse_int(raw.get("phase_percent"), field_name="phase_percent", default=0)
    _parse_int(raw.get("task_index"), field_name="task_index", default=0)
    _parse_int(raw.get("task_total"), field_name="task_total", default=0)
    summary_keys = (
        "downloaded_count",
        "persisted_count",
        "exception_count",
        "pending_mapping_count",
        "pending_review_count",
        "mapping_conflict_count",
        "accepted_completed_count",
        "skipped_count",
        "failed_count",
        "archive_pending_count",
        "archive_completed_count",
    )
    summary = {
        key: raw.get(key)
        for key in summary_keys
        if key in raw
    }
    progress_view = build_progress_view(
        job=job_payload,
        raw_progress={
            "job_status": raw.get("job_status"),
            "phase_code": raw.get("phase_code"),
            "phase_label": raw.get("phase_label"),
            "current_item_label": raw.get("current_task_label"),
            "current_index": raw.get("task_index"),
            "current_total": raw.get("task_total"),
            "latest_stage_code": raw.get("latest_stage_code"),
            "latest_stage_label": raw.get("latest_stage_label"),
            "latest_stage_summary": latest_stage_summary,
        },
        summary=summary or None,
        stage_summary=latest_stage_summary,
    )
    return {
        "record_family": str(progress_view.get("record_family") or ""),
        "business_id": str(progress_view.get("business_id") or ""),
        "business_label": str(progress_view.get("business_label") or ""),
        "scope": _progress_mapping(progress_view.get("scope"), field_name="progress.scope"),
        "phase_code": str(progress_view.get("phase_code") or ""),
        "phase_label": str(progress_view.get("phase_label") or ""),
        "job_status": str(progress_view.get("job_status") or ""),
        "is_terminal": bool(progress_view.get("is_terminal")),
        "phase_percent": phase_percent,
        "current_task_label": str(progress_view.get("current_item_label") or ""),
        "task_index": int(progress_view.get("current_index") or 0),
        "task_total": int(progress_view.get("current_total") or 0),
        "metrics": _progress_list(progress_view.get("metrics"), field_name="progress.metrics"),
        "latest_stage_code": str(progress_view.get("latest_stage_code") or ""),
        "latest_stage_label": str(progress_view.get("latest_stage_label") or ""),
        "latest_stage_summary": _progress_mapping(
            progress_view.get("latest_stage_summary"),
            field_name="progress.latest_stage_summary",
        ),
    }
