"""Downloader CLI/output payload helpers."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .download_models import DownloadRunResult


def _generated_at_text(generated_at: str | None) -> str:
    return str(generated_at or dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def format_task_list_lines(tasks: list[dict[str, Any]]) -> list[str]:
    lines = ["Registered downloader tasks:"]
    for item in tasks:
        lines.append(
            f"- {item['task_id']} | {item['display_name']} | "
            f"default_page_size={item['default_page_size']}"
        )
    return lines


def download_task_list_to_summary_payload(
    tasks: list[dict[str, Any]],
    *,
    generated_at: str | None = None,
    exit_code: int = 0,
) -> dict[str, Any]:
    return {
        "kind": "download_task_list",
        "generated_at": _generated_at_text(generated_at),
        "exit_code": int(exit_code),
        "tasks": [dict(item) for item in tasks],
    }


def download_result_to_summary_payload(
    result: DownloadRunResult,
    *,
    log_file: str,
    split_plan_only: bool,
    generated_at: str | None = None,
    start: str | None = None,
    end: str | None = None,
    duration_sec: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "download",
        "generated_at": _generated_at_text(generated_at),
        "exit_code": result.exit_code,
        "log_file": log_file,
        "task_count": result.task_count,
        "split_plan_only": bool(split_plan_only),
        "aggregate_summary": dict(result.aggregate_summary),
        "task_summaries": dict(result.task_summaries),
        "errors": list(result.errors),
    }
    if start is not None:
        payload["start"] = start
    if end is not None:
        payload["end"] = end
    if duration_sec is not None:
        payload["duration_sec"] = round(float(duration_sec), 3)
    return payload


def download_error_to_summary_payload(
    *,
    log_file: str,
    split_plan_only: bool,
    error: str,
    generated_at: str | None = None,
    exit_code: int = 2,
) -> dict[str, Any]:
    return {
        "kind": "download",
        "generated_at": _generated_at_text(generated_at),
        "exit_code": int(exit_code),
        "log_file": log_file,
        "task_count": 0,
        "split_plan_only": bool(split_plan_only),
        "aggregate_summary": {},
        "task_summaries": {},
        "errors": [error] if str(error).strip() else [],
    }


def download_run_finished_message(
    *,
    result: DownloadRunResult,
    log_file: str,
    start: str,
    end: str,
    duration_sec: float,
) -> str:
    return (
        f"Run finished: status={'FAILED' if result.any_failure else 'OK'}, "
        f"start={start}, "
        f"end={end}, "
        f"duration_sec={duration_sec:.2f}, "
        f"tasks={result.task_count}, errors={len(result.errors)}, log_file={log_file}"
    )


__all__ = [
    "download_error_to_summary_payload",
    "download_result_to_summary_payload",
    "download_run_finished_message",
    "download_task_list_to_summary_payload",
    "format_task_list_lines",
]
