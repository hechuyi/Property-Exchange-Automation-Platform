"""Pure job progress view contract."""

from __future__ import annotations

import json
from typing import Any, Iterable

from peap.streaming_models import RecordFamily

TERMINAL_JOB_STATUSES = ("success", "success_with_warnings", "interrupted", "failed")


def is_terminal_job_status(status: str) -> bool:
    return str(status or "").strip() in TERMINAL_JOB_STATUSES


def _coerce_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _metric_items(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        return []
    metric_specs = (
        ("downloaded_count", "已下载"),
        ("persisted_count", "已归档"),
        ("exception_count", "异常"),
        ("pending_mapping_count", "待补映射"),
        ("skipped_count", "已跳过"),
        ("archive_pending_count", "待归档"),
        ("archive_completed_count", "已完成归档"),
    )
    metrics: list[dict[str, Any]] = []
    for key, label in metric_specs:
        if key not in summary:
            continue
        value = summary.get(key)
        if isinstance(value, (int, float)):
            value = int(value)
        metrics.append({"key": key, "label": label, "value": value})
    return metrics


def _summarize_stage(summary: Any) -> str:
    if isinstance(summary, str):
        return summary
    if isinstance(summary, dict):
        if not summary:
            return ""
        return json.dumps(summary, ensure_ascii=False, sort_keys=True)
    return ""


def sanitize_terminal_progress(raw_progress: dict[str, Any]) -> dict[str, Any]:
    progress = dict(raw_progress or {})
    if not is_terminal_job_status(progress.get("job_status", "")):
        progress["is_terminal"] = False
        progress["metrics"] = list(progress.get("metrics") or [])
        return progress
    progress["is_terminal"] = True
    progress["current_item_label"] = ""
    progress["current_index"] = 0
    progress["current_total"] = 0
    progress["metrics"] = list(progress.get("metrics") or [])
    return progress


def build_progress_view(*, job: dict | None, raw_progress: dict, summary: dict | None = None) -> dict:
    job_data = dict(job or {})
    progress = dict(raw_progress or {})
    job_status = str(progress.get("job_status") or job_data.get("status") or "")
    record_family = str(job_data.get("record_family") or progress.get("record_family") or "listing").strip() or "listing"
    if record_family not in {"listing", "deal"}:
        record_family = "listing"

    view = {
        "job_id": str(job_data.get("job_id") or progress.get("job_id") or ""),
        "job_type": str(job_data.get("job_type") or progress.get("job_type") or ""),
        "record_family": record_family,
        "job_status": job_status,
        "phase_code": str(progress.get("phase_code") or ""),
        "phase_label": str(progress.get("phase_label") or ""),
        "is_terminal": is_terminal_job_status(job_status),
        "current_item_label": str(progress.get("current_item_label") or ""),
        "current_index": _coerce_int(progress.get("current_index"), default=0),
        "current_total": _coerce_int(progress.get("current_total"), default=0),
        "metrics": _metric_items(summary if summary is not None else progress.get("summary")),
        "latest_stage_code": str(progress.get("latest_stage_code") or ""),
        "latest_stage_label": str(progress.get("latest_stage_label") or ""),
        "latest_stage_summary": _summarize_stage(summary if summary is not None else progress.get("latest_stage_summary")),
    }
    if view["is_terminal"]:
        return sanitize_terminal_progress(view)
    return view
