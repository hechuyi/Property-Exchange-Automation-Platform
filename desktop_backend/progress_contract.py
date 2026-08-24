"""Pure job progress view contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from peap_core.family_catalog import get_family_descriptor

from .domain.constants import RECORD_STATE_LABELS

TERMINAL_JOB_STATUSES = ("success", "success_with_warnings", "interrupted", "failed")
STAGE_SUMMARY_NUMBER_KEYS = (
    "listed",
    "pages",
    "collected_candidates",
    "detail_candidates",
    "list_date_skipped",
    "detail_date_skipped",
    "date_missing_skipped",
    "detail_fetched",
    "saved",
    "resume_skipped",
    "duplicate_skipped",
    "business_filter_skipped",
    "missing_xmid_skipped",
    "detail_unavailable_skipped",
    "detail_failed",
    "list_unaccounted",
    "detail_unaccounted",
)
STAGE_SUMMARY_TEXT_KEYS = (
    "warning_code",
    "warning_message",
)


def is_terminal_job_status(status: str) -> bool:
    return str(status or "").strip() in TERMINAL_JOB_STATUSES


def _coerce_int(value: Any, *, default: int = 0, field_name: str = "value") -> int:
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _require_mapping(value: Any, *, name: str, allow_none: bool = False) -> dict[str, Any]:
    if value is None and allow_none:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return dict(value)


def _optional_list(value: Any, *, name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list")
    return list(value)


def _text(value: Any, *, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _validated_record_family(raw_value: Any) -> str:
    family = _text(raw_value)
    if not family:
        return ""
    if family == "all":
        return ""
    try:
        return get_family_descriptor(family).family_id
    except KeyError as exc:
        raise ValueError(f"unknown record_family: {family}") from exc


def _aggregate_scope(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Expose the persisted scope set for a multi-family parent job.

    A parent job intentionally has no single ``record_family``.  Keeping its
    family set in the public scope prevents list/detail/result consumers from
    mistaking an empty scope for missing identity while preserving the
    per-family snapshots used by workers and retry.
    """
    raw_families = metadata.get("record_families")
    raw_scopes = metadata.get("family_scopes")
    if raw_families is None and raw_scopes is None:
        return {}

    scope: dict[str, Any] = {}
    if raw_families is not None:
        if not isinstance(raw_families, list):
            raise TypeError("metadata.record_families must be a list")
        scope["record_families"] = [
            _validated_record_family(value)
            for value in raw_families
            if str(value or "").strip()
        ]
    if raw_scopes is not None:
        if not isinstance(raw_scopes, list):
            raise TypeError("metadata.family_scopes must be a list")
        normalized_scopes: list[dict[str, Any]] = []
        for index, raw_scope in enumerate(raw_scopes):
            if not isinstance(raw_scope, Mapping):
                raise TypeError(f"metadata.family_scopes[{index}] must be a mapping")
            item = dict(raw_scope)
            if "record_family" in item:
                item["record_family"] = _validated_record_family(item.get("record_family"))
            if "business_id" in item:
                item["business_id"] = _text(item.get("business_id"))
            if "business_label" in item:
                item["business_label"] = _text(item.get("business_label"))
            if "exchange" in item:
                item["exchange"] = _text(item.get("exchange"))
            normalized_scopes.append(item)
        scope["family_scopes"] = normalized_scopes
    return scope


def extract_job_identity(job: Mapping[str, Any] | None) -> dict[str, Any]:
    job_data = _dict(job)
    metadata = _dict(job_data.get("metadata"))
    scope_source = _dict(job_data.get("scope"))
    if not scope_source:
        scope_source = _dict(metadata.get("scope"))
    if not scope_source:
        scope_source = _aggregate_scope(metadata)

    if "record_family" in scope_source and str(scope_source.get("record_family") or "").strip():
        record_family = _validated_record_family(scope_source.get("record_family"))
    elif str(metadata.get("record_family") or "").strip():
        record_family = _validated_record_family(metadata.get("record_family"))
    elif str(job_data.get("record_family") or "").strip():
        record_family = _validated_record_family(job_data.get("record_family"))
    else:
        record_family = ""

    business_id = _text(
        scope_source.get("business_id")
        or metadata.get("business_id")
        or job_data.get("business_id")
    )
    business_label = _text(
        scope_source.get("business_label")
        or metadata.get("business_label")
        or job_data.get("business_label")
    )

    scope = dict(scope_source)
    if "record_family" in scope:
        scope["record_family"] = _validated_record_family(scope.get("record_family"))
    if "business_id" in scope:
        scope["business_id"] = _text(scope.get("business_id"))
    if "business_label" in scope:
        scope["business_label"] = _text(scope.get("business_label"))

    identity = {
        "record_family": record_family,
        "business_id": business_id,
        "business_label": business_label,
        "scope": scope,
    }
    return identity


def _metric_items(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        return []
    metric_specs = (
        ("downloaded_count", "已下载"),
        ("persisted_count", "已归档"),
        ("exception_count", "异常"),
        ("pending_review_count", RECORD_STATE_LABELS["pending_review"]),
        ("pending_mapping_count", "待补映射"),
        ("mapping_conflict_count", "映射冲突"),
        ("skipped_count", "已跳过"),
        ("failed_count", "失败"),
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


def _progress_metric_items(summary: dict[str, Any] | None, *, job_type: str) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        return []
    if str(job_type or "").strip() == "business_re_evaluation":
        pending_review_label = RECORD_STATE_LABELS["pending_review"]
        metric_specs = (
            ("pending_review_count", pending_review_label),
            ("pending_mapping_count", "待补映射"),
            ("mapping_conflict_count", "映射冲突"),
            ("accepted_completed_count", "已采纳"),
            ("skipped_count", "已跳过"),
            ("failed_count", "失败"),
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
    return _metric_items(summary)


def _stage_summary(summary: Any) -> dict[str, Any]:
    if isinstance(summary, str):
        text = summary.strip()
        return {"text": text} if text else {}
    if not isinstance(summary, dict):
        return {}
    normalized: dict[str, Any] = {}
    text = str(summary.get("text") or "").strip()
    if text:
        normalized["text"] = text
    for key in STAGE_SUMMARY_NUMBER_KEYS:
        if key not in summary:
            continue
        normalized[key] = _coerce_int(summary.get(key), field_name=f"latest_stage_summary.{key}")
    for key in STAGE_SUMMARY_TEXT_KEYS:
        value = str(summary.get(key) or "").strip()
        if value:
            normalized[key] = value
    return normalized


def sanitize_terminal_progress(raw_progress: dict[str, Any]) -> dict[str, Any]:
    progress = _require_mapping(raw_progress, name="raw_progress")
    if not is_terminal_job_status(progress.get("job_status", "")):
        progress["is_terminal"] = False
        progress["metrics"] = _optional_list(progress.get("metrics"), name="raw_progress.metrics")
        return progress
    progress["is_terminal"] = True
    progress["current_item_label"] = ""
    progress["current_index"] = 0
    progress["current_total"] = 0
    progress["metrics"] = _optional_list(progress.get("metrics"), name="raw_progress.metrics")
    return progress


def build_progress_view(
    *,
    job: dict | None,
    raw_progress: dict,
    summary: dict | None = None,
    stage_summary: Any | None = None,
) -> dict:
    job_data = _require_mapping(job, name="job", allow_none=True)
    progress = _require_mapping(raw_progress, name="raw_progress")
    job_status = str(progress.get("job_status") or job_data.get("status") or "")
    job_type = str(job_data.get("job_type") or progress.get("job_type") or "")
    identity = extract_job_identity(job_data)

    view = {
        "job_id": str(job_data.get("job_id") or progress.get("job_id") or ""),
        "job_type": job_type,
        "record_family": identity["record_family"],
        "business_id": identity["business_id"],
        "business_label": identity["business_label"],
        "scope": identity["scope"],
        "job_status": job_status,
        "phase_code": str(progress.get("phase_code") or ""),
        "phase_label": str(progress.get("phase_label") or ""),
        "is_terminal": is_terminal_job_status(job_status),
        "current_item_label": str(progress.get("current_item_label") or ""),
        "current_index": _coerce_int(progress.get("current_index"), default=0, field_name="current_index"),
        "current_total": _coerce_int(progress.get("current_total"), default=0, field_name="current_total"),
        "metrics": _progress_metric_items(
            summary if summary is not None else progress.get("summary"),
            job_type=job_type,
        ),
        "latest_stage_code": str(progress.get("latest_stage_code") or ""),
        "latest_stage_label": str(progress.get("latest_stage_label") or ""),
        "latest_stage_summary": _stage_summary(stage_summary if stage_summary is not None else progress.get("latest_stage_summary")),
    }
    if view["is_terminal"]:
        return sanitize_terminal_progress(view)
    return view
