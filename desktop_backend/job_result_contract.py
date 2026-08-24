"""HTTP response contract helpers for normalized job terminal/result views."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain.constants import RECORD_STATE_LABELS
from .progress_contract import extract_job_identity

_LEGACY_RESOLUTION_SCOPE_STRINGS = {"mapping_resolution", "business_resolution"}


def _metric_specs(job_type: str, summary: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    pending_review_label = RECORD_STATE_LABELS["pending_review"]
    if job_type in {"manual_import", "archive_reprocess"}:
        return (
            ("imported_count", "导入成功"),
            ("pending_review_count", pending_review_label),
            ("pending_mapping_count", "待补映射"),
            ("skipped_count", "已跳过"),
            ("failed_count", "失败"),
        )
    if job_type == "export_excel":
        return (
            ("new_records", "新增记录"),
            ("changed_records", "变更记录"),
            ("visible_count", "可见记录"),
        )
    if job_type == "business_re_evaluation":
        return (
            ("pending_review_count", pending_review_label),
            ("pending_mapping_count", "待补映射"),
            ("mapping_conflict_count", "映射冲突"),
            ("accepted_completed_count", "已采纳"),
            ("skipped_count", "已跳过"),
            ("failed_count", "失败"),
        )
    if job_type in {"one_click", "download_ingest", "mapping_refresh"}:
        return (
            ("downloaded_count", "已下载"),
            ("persisted_count", "已归档"),
            ("exception_count", "异常"),
            ("pending_review_count", pending_review_label),
            ("pending_mapping_count", "待补映射"),
            ("mapping_conflict_count", "映射冲突"),
            ("skipped_count", "已跳过"),
            ("failed_count", "失败"),
        )
    return ()


def _parse_metric_value(value: Any, *, field_name: str) -> int:
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


def _artifact_count(summary: Mapping[str, Any]) -> int:
    for key in ("artifacts", "export_artifacts"):
        artifacts = summary.get(key)
        if isinstance(artifacts, list):
            return len(artifacts)
    return 0


def _download_archive_audit(summary: Mapping[str, Any]) -> dict[str, Any]:
    if "download_archive_audit" not in summary:
        return {}
    return _optional_mapping(
        summary.get("download_archive_audit"),
        field_name="summary.download_archive_audit",
    )


_PUBLIC_RESOURCE_NUMBER_KEYS = ("record_count",)
_PUBLIC_RESOURCE_TEXT_KEYS = (
    "status",
    "workbook",
    "evidence_root",
    "archive_root",
    "error_type",
    "error_code",
    "error_message",
    "failure_code",
    "failure_message",
)


def _public_resource_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Expose the stable public-resource result subset, filtering worker internals."""
    if "public_resource" not in summary:
        return {}
    source = _optional_mapping(
        summary.get("public_resource"),
        field_name="summary.public_resource",
    )
    normalized: dict[str, Any] = {}
    for key in _PUBLIC_RESOURCE_NUMBER_KEYS:
        if key not in source:
            continue
        normalized[key] = _parse_metric_value(
            source.get(key),
            field_name=f"summary.public_resource.{key}",
        )
    for key in _PUBLIC_RESOURCE_TEXT_KEYS:
        if key not in source:
            continue
        value = str(source.get(key) or "").strip()
        if value:
            normalized[key] = value
    return normalized


def _positive_metric_fragments(summary: Mapping[str, Any], specs: tuple[tuple[str, str], ...]) -> list[str]:
    fragments: list[str] = []
    for key, label in specs:
        if key not in summary:
            continue
        value = _parse_metric_value(summary.get(key), field_name=f"summary.{key}")
        if value <= 0:
            continue
        fragments.append(f"{label} {value}")
    return fragments


def _terminal_result_message(
    *,
    outcome: str,
    job_type: str,
    summary: dict[str, Any],
    fallback: str,
    failure_message: str,
) -> str:
    explicit_message = str(summary.get("message") or "").strip()
    if explicit_message:
        return explicit_message
    if outcome == "failed":
        reason = failure_message or fallback
        return f"未完成：{reason}" if reason else "未完成"
    if outcome == "interrupted":
        return fallback or "任务已中断"

    fragments = _positive_metric_fragments(summary, _metric_specs(job_type, summary))
    artifact_count = _artifact_count(summary)
    if artifact_count > 0:
        fragments.append(f"生成文件 {artifact_count} 个")
    if outcome == "succeeded_with_warnings":
        return "已完成但有待处理" + (f"：{'，'.join(fragments)}" if fragments else "")
    if outcome == "succeeded":
        return "已完成" + (f"：{'，'.join(fragments)}" if fragments else "")
    return fallback


def build_job_result_view(job: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _optional_mapping(job, field_name="job")
    _validate_scope_snapshot(source)
    summary = _optional_mapping(source.get("summary"), field_name="job.summary")
    identity = extract_job_identity(source)
    job_type = str(source.get("job_type") or "").strip()
    status = str(source.get("status") or "").strip()
    outcome_map = {
        "success": "succeeded",
        "success_with_warnings": "succeeded_with_warnings",
        "failed": "failed",
        "interrupted": "interrupted",
        "running": "running",
        "starting": "running",
    }
    outcome = outcome_map.get(status, status or "unknown")
    failure_code = str(
        summary.get("failure_code")
        or summary.get("error_code")
        or ""
    ).strip()
    failure_message = str(summary.get("failure_message") or "").strip()
    if not failure_message and outcome == "failed":
        failure_message = str(summary.get("message") or "").strip()
    default_messages = {
        "succeeded": "任务已完成",
        "succeeded_with_warnings": "任务已完成，但有待处理项",
        "failed": "任务执行失败",
        "interrupted": "任务已中断",
        "running": "",
    }
    message = _terminal_result_message(
        outcome=outcome,
        job_type=job_type,
        summary=summary,
        fallback=default_messages.get(outcome, ""),
        failure_message=failure_message,
    )
    metrics = []
    for key, label in _metric_specs(job_type, summary):
        if key not in summary:
            continue
        value = summary.get(key)
        normalized_value = _parse_metric_value(value, field_name=f"summary.{key}")
        metrics.append({"key": key, "label": label, "value": normalized_value})
    artifact_count = _artifact_count(summary)
    download_archive_audit = _download_archive_audit(summary)
    result = {
        "record_family": identity["record_family"],
        "business_id": identity["business_id"],
        "business_label": identity["business_label"],
        "scope": identity["scope"],
        "outcome": outcome,
        "message": message,
        "failure_code": failure_code,
        "failure_message": failure_message,
        "metrics": metrics,
        "artifact_count": artifact_count,
        "download_archive_audit": download_archive_audit,
    }
    if "public_resource" in summary:
        result["public_resource"] = _public_resource_summary(summary)
    return result
