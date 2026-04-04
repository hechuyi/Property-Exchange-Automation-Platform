"""Presentation helpers for one-click downloader stage events."""

from __future__ import annotations

from typing import Any, Callable

from .download_errors import DownloadError
from .download_reporting import totals_to_summary_dict


def format_download_error(error: DownloadError) -> dict[str, object]:
    return error.to_presenter_payload()


def _first_typed_error(raw_errors: object) -> DownloadError | None:
    if isinstance(raw_errors, (list, tuple)):
        for item in raw_errors:
            if isinstance(item, DownloadError):
                return item
    elif isinstance(raw_errors, DownloadError):
        return raw_errors
    return None



def present_collect_error(raw_errors: object) -> dict[str, Any]:
    typed_error = _first_typed_error(raw_errors)
    if typed_error is None:
        raise TypeError("present_collect_error requires typed DownloadError inputs")
    serialized = format_download_error(typed_error)
    return {
        **serialized,
        "typed_error": serialized,
    }



def stage_error_message(summary_payload: dict[str, Any] | None) -> str:
    payload = dict(summary_payload or {})
    explicit = str(payload.get("error_message") or "").strip()
    if explicit:
        return explicit
    typed_error = payload.get("typed_error")
    if isinstance(typed_error, dict):
        return str(typed_error.get("error_message") or "").strip()
    return ""



def emit_stage(
    callback: Callable[[dict[str, Any]], None] | None,
    *,
    phase_code: str,
    status: str,
    label: str,
    summary_payload: dict[str, Any] | None = None,
) -> None:
    if callback is None:
        return
    payload = {
        "phase_code": str(phase_code),
        "status": str(status),
        "label": str(label),
        "summary_payload": dict(summary_payload or {}),
    }
    error_message = stage_error_message(summary_payload)
    if error_message:
        payload["error_message"] = error_message
    callback(payload)



def build_phase_summary(
    *,
    totals: dict[str, int],
    task_index: int,
    task_total: int,
    task_label: str,
    phase_percent: int,
    collected_candidates: int = 0,
) -> dict[str, Any]:
    return {
        "task_label": task_label,
        "task_index": int(task_index),
        "task_total": int(task_total),
        "phase_percent": int(phase_percent),
        "summary": {
            **totals_to_summary_dict(totals),
            "collected_candidates": int(collected_candidates),
        },
    }


__all__ = [
    "build_phase_summary",
    "emit_stage",
    "format_download_error",
    "present_collect_error",
    "stage_error_message",
]
