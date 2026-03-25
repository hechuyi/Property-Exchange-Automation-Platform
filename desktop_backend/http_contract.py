"""Pure HTTP response contract helpers."""

from __future__ import annotations

from typing import Any, Iterable

DEFAULT_JOB_EVENT_LIMIT = 200


def normalize_job_event_limit(raw_value: Any) -> int:
    try:
        value = int(raw_value)
    except Exception:
        return DEFAULT_JOB_EVENT_LIMIT
    return max(1, min(value, DEFAULT_JOB_EVENT_LIMIT))


def build_job_events_envelope(events: list[dict], *, total_count: int) -> dict:
    events_list = list(events or [])
    returned_count = len(events_list)
    normalized_total = max(returned_count, int(total_count or 0))
    return {
        "events": events_list,
        "returned_count": returned_count,
        "total_count": normalized_total,
        "truncated": normalized_total > returned_count,
    }


def build_not_found_payload(*, resource: str, resource_id: str = "") -> dict:
    return {
        "error": "not_found",
        "resource": str(resource or ""),
        "resource_id": str(resource_id or ""),
    }
