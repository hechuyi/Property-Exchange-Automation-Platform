"""HTTP response contract helpers for the overview resource."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from .job_contract import build_job_view
from .progress_resource_contract import build_progress_resource
from .runtime_contract import build_runtime_view


def _mapping(value: Any, *, field_name: str = "value") -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _optional_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _optional_list(value: Any, *, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return list(value)


def _optional_job(value: Any, *, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return dict(value)


def _visibility_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {
            "mode": "listing_only",
            "visible_families": ["listing"],
        }
    source = _mapping(value, field_name="visibility")
    raw_visible_families = source.get("visible_families")
    if not isinstance(raw_visible_families, list):
        raise ValueError("visibility.visible_families must be a non-empty list")
    visible_families = [
        str(item).strip()
        for item in raw_visible_families
        if str(item).strip()
    ]
    if not visible_families:
        raise ValueError("visibility.visible_families must be a non-empty list")
    return {
        "mode": str(source.get("mode") or "listing_only").strip() or "listing_only",
        "visible_families": visible_families,
    }


def build_overview_view(
    payload: Mapping[str, Any] | None,
    *,
    build_job_progress: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if payload is None:
        source: dict[str, Any] = {}
    elif isinstance(payload, Mapping):
        source = dict(payload)
    else:
        raise ValueError("payload must be an object")
    record_summary = _optional_object(source.get("record_summary"), field_name="record_summary")
    runtime = _optional_object(source.get("runtime"), field_name="runtime")
    defaults = _mapping(source.get("defaults"), field_name="defaults")
    latest_job_raw = _optional_job(source.get("latest_job"), field_name="latest_job")
    latest_progress = _optional_object(source.get("latest_progress"), field_name="latest_progress")
    recent_jobs_raw = _optional_list(source.get("recent_jobs"), field_name="recent_jobs")
    state_counts = _optional_object(
        record_summary.get("state_counts"),
        field_name="record_summary.state_counts",
    )

    def _job_view(job: Any, *, progress: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        if job is None:
            return None
        if not isinstance(job, Mapping):
            raise ValueError("job must be an object")
        if progress is None:
            resolved_progress: dict[str, Any] = {}
        elif isinstance(progress, Mapping):
            resolved_progress = dict(progress)
        else:
            raise ValueError("job progress must be an object")
        if not resolved_progress and build_job_progress is not None:
            progress_result = build_job_progress(job)
            if progress_result is None:
                resolved_progress = {}
            elif isinstance(progress_result, Mapping):
                resolved_progress = dict(progress_result)
            else:
                raise ValueError("build_job_progress result must be an object")
        return build_job_view(job, progress=resolved_progress)

    return {
        "record_summary": {
            "state_counts": state_counts,
            "pending_mapping_count": int(record_summary.get("pending_mapping_count") or 0),
        },
        "latest_job": _job_view(latest_job_raw, progress=None if build_job_progress is not None else latest_progress),
        "latest_progress": build_progress_resource(latest_progress, job=latest_job_raw if isinstance(latest_job_raw, Mapping) else None),
        "recent_jobs": [
            item
            for item in (
                _job_view(_optional_job(job, field_name=f"recent_jobs[{index}]"))
                for index, job in enumerate(recent_jobs_raw)
            )
            if item is not None
        ],
        "runtime": build_runtime_view(runtime),
        "defaults": {
            "manual_import_input_dir": str(defaults.get("manual_import_input_dir") or "").strip(),
            "archive_root": str(defaults.get("archive_root") or "").strip(),
            "default_scope": _mapping(defaults.get("default_scope"), field_name="defaults.default_scope"),
        },
        "visibility": _visibility_payload(source.get("visibility")),
    }
