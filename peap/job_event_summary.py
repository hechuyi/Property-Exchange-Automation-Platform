"""Shared summaries derived from persisted streaming job events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _event_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise ValueError("progress_event must be a mapping")
    value = event.get("payload")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("progress_event.payload must be a mapping")
    return dict(value)


def failure_summary_fields(job_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the latest persisted failure fields for a job summary."""

    for event in reversed(job_events):
        if str(event.get("status") or "").strip() != "failed":
            continue
        payload = _event_payload(event)
        failure_code = str(event.get("error_type") or payload.get("error_code") or "").strip()
        failure_message = str(event.get("error_message") or "").strip()
        if not failure_code and not failure_message:
            continue
        return {
            "failure_code": failure_code,
            "failure_stage": str(event.get("stage") or "").strip(),
            "failure_message": failure_message,
        }
    return {}


def has_failed_job_event(job_events: list[dict[str, Any]]) -> bool:
    """Whether a job emitted a terminal or explicitly failed event."""

    return any(
        str(event.get("stage") or "").strip() == "failed"
        or str(event.get("status") or "").strip() == "failed"
        for event in job_events
    )


__all__ = ["failure_summary_fields", "has_failed_job_event"]
