"""HTTP response contract helpers for action-style resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _as_mapping(value: Any, *, field_name: str = "service result", allow_none: bool = False) -> dict[str, Any]:
    if value is None and allow_none:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    raise ValueError(f"{field_name} must be an object")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _required_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} is empty")
    return text


def _integer(value: Any, *, field_name: str = "value") -> int:
    if value is None or (isinstance(value, str) and value == ""):
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _boolean(value: Any, *, field_name: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean")


def _normalize_scope(scope: Any) -> dict[str, Any]:
    source = _as_mapping(scope, field_name="scope", allow_none=True)
    normalized = {
        "record_family": _text(source.get("record_family")),
        "state": _text(source.get("state")),
        "exchange": _text(source.get("exchange")),
        "keyword": _text(source.get("keyword")),
        "date_from": _text(source.get("date_from")),
        "date_to": _text(source.get("date_to")),
        "page": _integer(source.get("page"), field_name="scope.page"),
        "page_size": _integer(source.get("page_size"), field_name="scope.page_size"),
    }
    business_id = _text(source.get("business_id"))
    if business_id:
        normalized["business_id"] = business_id
    business_label = _text(source.get("business_label"))
    if business_label:
        normalized["business_label"] = business_label
    return normalized


def _normalize_scope_state_counts(counts: Any) -> dict[str, int]:
    source = _as_mapping(counts, field_name="scope_state_counts", allow_none=True)
    return {
        str(key or "").strip(): _integer(value, field_name=f"scope_state_counts.{key}")
        for key, value in source.items()
        if str(key or "").strip()
    }


def _normalize_missing_fields(value: Any) -> list[dict[str, str]]:
    if value is None:
        items = []
    elif isinstance(value, Mapping):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError("missing_fields must be a list or object")
    normalized: list[dict[str, str]] = []
    for item in items:
        source = _as_mapping(item, field_name="missing_fields item")
        if not source:
            continue
        normalized.append(
            {
                "kind": _text(source.get("kind")),
                "field": _text(source.get("field")),
                "canonical_field": _text(source.get("canonical_field")),
                "export_field": _text(source.get("export_field")),
                "message": _text(source.get("message")),
            }
        )
    return normalized


def _normalize_field_missing_acknowledgement(value: Any) -> dict[str, Any]:
    source = _as_mapping(value, field_name="field_missing_acknowledgement", allow_none=True)
    return {
        "acknowledged": _boolean(
            source.get("acknowledged"),
            field_name="field_missing_acknowledgement.acknowledged",
        ),
        "missing_fields_hash": _text(source.get("missing_fields_hash")),
        "revision_id": _integer(source.get("revision_id"), field_name="field_missing_acknowledgement.revision_id"),
        "missing_fields": _normalize_missing_fields(source.get("missing_fields")),
    }


def _normalize_attention(value: Any) -> dict[str, Any]:
    source = _as_mapping(value, field_name="attention", allow_none=True)
    return {
        "requires_attention": _boolean(source.get("requires_attention"), field_name="attention.requires_attention"),
        "suppressed": _boolean(source.get("suppressed"), field_name="attention.suppressed"),
        "reason": _text(source.get("reason")),
    }


def _normalize_field_missing_diagnostics(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("field_missing_diagnostics must be a list")
    diagnostics: list[dict[str, Any]] = []
    for item in value:
        source = _as_mapping(item, field_name="field_missing_diagnostics item")
        if not source:
            continue
        diagnostic = {
            "record_id": _text(source.get("record_id")),
            "revision_id": _integer(source.get("revision_id"), field_name="field_missing_diagnostics.revision_id"),
            "record_family": _text(source.get("record_family")),
            "business_id": _text(source.get("business_id")),
            "failure_code": _text(source.get("failure_code")),
            "missing_fields": _normalize_missing_fields(source.get("missing_fields")),
        }
        project_code = _text(source.get("project_code"))
        if project_code:
            diagnostic["project_code"] = project_code
        project_name = _text(source.get("project_name"))
        if project_name:
            diagnostic["project_name"] = project_name
        diagnostics.append(diagnostic)
    return diagnostics


def _normalize_existing_entry(entry: Any) -> dict[str, Any]:
    source = _as_mapping(entry, field_name="existing_entry", allow_none=True)
    return {
        "entry_id": _text(source.get("entry_id")),
        "rule_title": _text(source.get("rule_title")),
        "source_name": _text(source.get("source_name")),
        "target_value": _text(source.get("target_value")),
    }


def _normalize_resolution(resolution: Any) -> dict[str, Any]:
    source = _as_mapping(resolution, field_name="resolution", allow_none=True)
    return {
        "field": _text(source.get("field")),
        "rule_kind": _text(source.get("rule_kind")),
        "source_name": _text(source.get("source_name")),
        "target_value": _text(source.get("target_value")),
    }


def build_streaming_job_launch_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _as_mapping(payload)
    scope = _normalize_scope(source.get("scope"))
    normalized = {
        "job_id": _required_text(source.get("job_id"), field_name="job_id"),
        "job_type": _required_text(source.get("job_type"), field_name="job_type"),
        "db_path": _text(source.get("db_path")),
        "input_dir": _text(source.get("input_dir")),
        "discovered_count": _integer(source.get("discovered_count"), field_name="discovered_count"),
        "affected_count": _integer(source.get("affected_count"), field_name="affected_count"),
    }
    record_family = _text(source.get("record_family") or scope.get("record_family"))
    if record_family:
        normalized["record_family"] = record_family
    business_id = _text(source.get("business_id") or scope.get("business_id"))
    if business_id:
        normalized["business_id"] = business_id
    business_label = _text(source.get("business_label") or scope.get("business_label"))
    if business_label:
        normalized["business_label"] = business_label
    if any(scope.values()):
        normalized["scope"] = scope
    retry_of_job_id = _text(source.get("retry_of_job_id"))
    if retry_of_job_id:
        normalized["retry_of_job_id"] = retry_of_job_id
    log_path = _text(source.get("log_path"))
    if log_path:
        normalized["log_path"] = log_path
    notification = _as_mapping(source.get("notification"), field_name="notification", allow_none=True)
    if notification:
        normalized["notification"] = {
            "level": _text(notification.get("level")),
            "message": _text(notification.get("message")),
        }
    return normalized


def build_export_action_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _as_mapping(payload)
    artifacts = source.get("artifacts")
    if artifacts is None:
        normalized_artifacts: list[str] = []
    elif isinstance(artifacts, list):
        normalized_artifacts = [_text(item) for item in artifacts]
    else:
        raise ValueError("artifacts must be a list")
    return {
        "job_id": _text(source.get("job_id")),
        "job_type": _text(source.get("job_type")),
        "status": _text(source.get("status")),
        "message": _text(source.get("message")),
        "failure_code": _text(source.get("failure_code")),
        "failure_message": _text(source.get("failure_message")),
        "empty_reason_code": _text(source.get("empty_reason_code")),
        "scope_state_counts": _normalize_scope_state_counts(source.get("scope_state_counts")),
        "scope": _normalize_scope(source.get("scope")),
        "export_id": _text(source.get("export_id")),
        "cursor_id": _text(source.get("cursor_id")),
        "requested_export_mode": _text(source.get("requested_export_mode")),
        "revision_watermark": _integer(source.get("revision_watermark"), field_name="revision_watermark"),
        "field_missing_blocked_records": _integer(source.get("field_missing_blocked_records"), field_name="field_missing_blocked_records"),
        "field_missing_diagnostics": _normalize_field_missing_diagnostics(
            source.get("field_missing_diagnostics")
        ),
        "retention_count": _integer(source.get("retention_count"), field_name="retention_count"),
        "new_records": _integer(source.get("new_records"), field_name="new_records"),
        "changed_records": _integer(source.get("changed_records"), field_name="changed_records"),
        "artifacts": normalized_artifacts,
    }


def build_mapping_preview_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _as_mapping(payload)
    return {
        "conflict": _boolean(source.get("conflict"), field_name="conflict"),
        "mode": _text(source.get("mode")),
        "existing_entry": _normalize_existing_entry(source.get("existing_entry")),
        "affected_count": _integer(source.get("affected_count"), field_name="affected_count"),
        "affected_pending_count": _integer(source.get("affected_pending_count"), field_name="affected_pending_count"),
        "match_field": _text(source.get("match_field")),
        "target_field": _text(source.get("target_field")),
        "target_value": _text(source.get("target_value")),
        "source_name": _text(source.get("source_name")),
        "rule_kind": _text(source.get("rule_kind")),
        "rule_title": _text(source.get("rule_title")),
        "source_label": _text(source.get("source_label")),
        "target_label": _text(source.get("target_label")),
        "scope_miss": _boolean(source.get("scope_miss"), field_name="scope_miss"),
        "scope_miss_message": _text(source.get("scope_miss_message")),
    }


def build_mapping_save_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _as_mapping(payload)
    preview = build_mapping_preview_view(source)
    return {
        "entry_id": _text(source.get("entry_id")),
        "job_id": _text(source.get("job_id")),
        "job_type": _text(source.get("job_type")),
        "affected_count": _integer(source.get("affected_count") or preview["affected_count"], field_name="affected_count"),
        **preview,
    }


def build_mapping_delete_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _as_mapping(payload)
    return {
        "entry_id": _text(source.get("entry_id")),
        "deleted": _boolean(source.get("deleted"), field_name="deleted"),
        "job_id": _text(source.get("job_id")),
        "job_type": _text(source.get("job_type")),
        "affected_count": _integer(source.get("affected_count"), field_name="affected_count"),
    }


def build_mapping_conflict_resolution_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _as_mapping(payload)
    return {
        "job_id": _text(source.get("job_id")),
        "job_type": _text(source.get("job_type")),
        "affected_count": _integer(source.get("affected_count"), field_name="affected_count"),
        "record_id": _text(source.get("record_id")),
        "resolution_mode": _text(source.get("resolution_mode")),
        "resolution": _normalize_resolution(source.get("resolution")),
    }


def build_mapping_undo_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _as_mapping(payload)
    return {
        "undone": _boolean(source.get("undone"), field_name="undone"),
        "undo_kind": _text(source.get("undo_kind")),
        "entry_id": _text(source.get("entry_id")),
    }


def build_path_selection_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _as_mapping(payload)
    return {
        "selected": _boolean(source.get("selected"), field_name="selected"),
        "path": _text(source.get("path")),
        "selection_kind": _text(source.get("selection_kind")),
    }


def build_path_open_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _as_mapping(payload)
    return {
        "opened": _boolean(source.get("opened"), field_name="opened"),
        "path": _text(source.get("path")),
        "reveal": _boolean(source.get("reveal"), field_name="reveal"),
    }


def build_record_reveal_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _as_mapping(payload)
    return {
        "opened": _boolean(source.get("opened"), field_name="opened"),
        "record_id": _text(source.get("record_id")),
        "path": _text(source.get("path")),
        "artifact_name": _text(source.get("artifact_name")),
    }


def build_record_field_missing_ack_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _as_mapping(payload)
    ack = source.get("field_missing_acknowledgement", source.get("acknowledgement"))
    return {
        "record_id": _text(source.get("record_id")),
        "state": _text(source.get("state")),
        "field_missing_acknowledgement": _normalize_field_missing_acknowledgement(ack),
        "attention": _normalize_attention(source.get("attention")),
        "exportable": _boolean(source.get("exportable"), field_name="exportable"),
    }


def build_record_reprocess_view(payload: Mapping[str, Any] | None, *, record_id: str = "") -> dict[str, Any]:
    source = _as_mapping(payload)
    return {
        "record_id": _text(source.get("record_id") or record_id),
        "state": _text(source.get("state")),
        "project_code": _text(source.get("project_code")),
        "archive_path": _text(source.get("archive_path")),
        "error_code": _text(source.get("error_code") or source.get("error_type")),
        "error_message": _text(source.get("error_message") or source.get("last_error_message")),
    }


def build_runtime_install_action_view(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _as_mapping(payload)
    return {
        "status": _text(source.get("status")),
        "browser_name": _text(source.get("browser_name")),
        "trigger": _text(source.get("trigger")),
        "attempt_count": _integer(source.get("attempt_count"), field_name="attempt_count"),
        "started_at": _text(source.get("started_at")),
        "updated_at": _text(source.get("updated_at")),
        "completed_at": _text(source.get("completed_at")),
        "message": _text(source.get("message")),
        "running": bool(source.get("running")),
    }
