"""Pure HTTP response contract helpers."""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from typing import Any, Iterable, Mapping

from .error_codes import ERROR_NOT_FOUND

DEFAULT_JOB_EVENT_LIMIT = 200


def normalize_job_event_limit(raw_value: Any) -> int:
    if raw_value in {None, ""}:
        return DEFAULT_JOB_EVENT_LIMIT
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid limit: {raw_value!r}") from exc
    return max(1, min(value, DEFAULT_JOB_EVENT_LIMIT))


def build_capacity_envelope(items: Iterable[Any], *, total_count: int, item_key: str = "items") -> dict:
    if items is None or isinstance(items, (str, bytes, bytearray)) or isinstance(items, MappingABC):
        raise ValueError("items must be a list")
    items_list = list(items)
    returned_count = len(items_list)
    if not isinstance(total_count, int) or isinstance(total_count, bool):
        raise ValueError("total_count must be an integer")
    normalized_total = max(returned_count, total_count)
    return {
        item_key: items_list,
        "returned_count": returned_count,
        "total_count": normalized_total,
        "truncated": normalized_total > returned_count,
    }


def build_job_events_envelope(events: list[dict], *, total_count: int) -> dict:
    return build_capacity_envelope(events, total_count=total_count, item_key="events")


def build_success_payload(*, data: Any, meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "data": data,
    }
    if meta:
        payload["meta"] = dict(meta)
    return payload


def build_error_payload(
    *,
    error_code: str,
    message: str | None = None,
    details: Mapping[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    error_payload: dict[str, Any] = {
        "code": str(error_code or ""),
        "message": str(message if message is not None else error_code),
    }
    merged_details: dict[str, Any] = {}
    if details:
        merged_details.update(dict(details))
    if extra:
        merged_details.update(dict(extra))
    if merged_details:
        error_payload["details"] = merged_details
    return {
        "ok": False,
        "error": error_payload,
    }


def build_not_found_payload(*, resource: str, resource_id: str = "") -> dict:
    return build_error_payload(
        error_code=ERROR_NOT_FOUND,
        message=ERROR_NOT_FOUND,
        details={
            "resource": str(resource or ""),
            "resource_id": str(resource_id or ""),
        },
    )
