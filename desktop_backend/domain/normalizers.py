"""Pure data normalization and coercion helpers.

All functions in this module are stateless — they do not touch I/O,
threading, or configuration. They transform raw values into the
canonical forms expected by the service and presentation layers.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Mapping
from typing import Any, Dict

from peap_core.pipeline_state_contracts import RecordState
from peap_core.source_catalog import canonical_source_code, canonical_source_label

from ..legacy_contract import legacy_mapping_source_name
from ..product_errors import UserInputError
from .constants import (
    JOB_TYPE_LABELS,
    MAPPING_MATCH_FIELDS,
    MAPPING_RULE_SPECS,
    MAPPING_SOURCE_TYPES,
    PROJECT_TYPE_CODES_BY_LABEL,
    PROJECT_TYPE_LABELS,
    RECORD_STATE_LABELS,
)

# ── Scalar coercions ──


def coerce_int(raw_value: Any, *, default: int = 0) -> int:
    try:
        return int(raw_value)
    except Exception:
        return default


def coerce_bool(raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    return str(raw_value or "").strip().lower() in {"1", "true", "yes", "on"}


def parse_bool(raw_value: Any, *, field_name: str, default: bool = False) -> bool:
    if raw_value is None or raw_value == "":
        return bool(default)
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, int) and not isinstance(raw_value, bool):
        if raw_value in {0, 1}:
            return bool(raw_value)
        raise ValueError(f"invalid {field_name}: {raw_value!r}")
    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid {field_name}: {raw_value!r}")


def coerce_limit(raw_value: Any, *, default: int = 50, maximum: int = 200) -> int:
    try:
        value = int(raw_value)
    except Exception:
        value = default
    return max(1, min(value, maximum))


def normalize_local_path(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    return os.path.abspath(os.path.expanduser(text))


def parse_local_path(raw_value: Any, *, field_name: str) -> str:
    if raw_value is None or raw_value == "":
        return ""
    if not isinstance(raw_value, str):
        raise ValueError(f"{field_name} must be a string")
    return normalize_local_path(raw_value)


def parse_text(raw_value: Any, *, field_name: str, default: str = "") -> str:
    if raw_value is None or raw_value == "":
        return default
    if not isinstance(raw_value, str):
        raise ValueError(f"{field_name} must be a string")
    return raw_value.strip() or default


# ── Record-state normalization ──


def normalize_record_state_value(raw_state: Any) -> str:
    if isinstance(raw_state, RecordState):
        return raw_state.value
    text = str(raw_state or "").strip()
    if not text:
        return ""
    if text.startswith("RecordState."):
        _, _, member_name = text.partition(".")
        if member_name:
            try:
                return RecordState[member_name].value
            except KeyError:
                return member_name.lower()
    return text


def status_label(state: str) -> str:
    normalized = normalize_record_state_value(state)
    return RECORD_STATE_LABELS.get(normalized, normalized or "未知")


def normalize_record_states(raw_state: str) -> list[str] | None:
    state = normalize_record_state_value(raw_state or "all").strip().lower()
    if state in {"", "all"}:
        return None
    known_states = {item.value for item in RecordState}
    if state not in known_states:
        raise ValueError(f"unknown state: {raw_state!r}")
    return [state]


# ── Job event normalization ──


EVENT_SUMMARY_NUMBER_KEYS = (
    "listed",
    "pages",
    "collected_candidates",
    "detail_candidates",
    "detail_fetched",
    "saved",
    "list_date_skipped",
    "detail_date_skipped",
    "date_missing_skipped",
    "resume_skipped",
    "errors",
    "duplicate_skipped",
    "business_filter_skipped",
    "missing_xmid_skipped",
    "detail_unavailable_skipped",
    "detail_failed",
    "list_unaccounted",
    "detail_unaccounted",
    "pending_review_count",
    "accepted_completed_count",
    "skipped_count",
    "failed_count",
    # Public-resource collection reports use the same event summary channel
    # but add a small, explicitly supported set of progress counters.
    "record_count",
    "period_index",
    "period_total",
    "page",
    "official_total",
    "current",
    "total",
    "selected",
    "excluded",
    "failed",
    "attempt",
    "attempt_total",
    "retry_in_seconds",
    "business_code",
)
EVENT_SUMMARY_TEXT_KEYS = (
    "warning_code",
    "warning_message",
    # Keep public-resource result/provenance fields observable without
    # exposing arbitrary worker payload keys.
    "status",
    "workbook",
    "evidence_root",
    "archive_root",
    "error_type",
    "error_code",
    "error_message",
    "failure_code",
    "failure_message",
    "month",
    "phase",
    "time_begin",
    "time_end",
    "transport",
    "role",
)


def _as_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else dict(value) if hasattr(value, "items") else {}


def _event_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(value)
    except Exception:
        raise ValueError(f"{field_name} must be an integer") from None


def _event_int_field(source: Mapping[str, Any], key: str, *, field_name: str, default: int = 0) -> int:
    if key not in source:
        return default
    return _event_int(source.get(key), field_name=field_name)


def _event_int_field_with_fallback(
    primary: Mapping[str, Any],
    fallback: Mapping[str, Any],
    key: str,
    *,
    field_name: str,
    default: int = 0,
) -> int:
    source = primary if key in primary else fallback
    return _event_int_field(source, key, field_name=field_name, default=default)


def _event_text_field_with_fallback(
    primary: Mapping[str, Any],
    fallback: Mapping[str, Any],
    key: str,
) -> str:
    value = primary.get(key) if key in primary else fallback.get(key)
    return str(value or "").strip()


def _normalize_event_summary(summary: Any) -> Dict[str, Any]:
    source = _as_mapping(summary)
    normalized: Dict[str, Any] = {}
    for key in EVENT_SUMMARY_NUMBER_KEYS:
        if key not in source:
            continue
        normalized[key] = _event_int(source.get(key), field_name=f"summary.{key}")
    for key in EVENT_SUMMARY_TEXT_KEYS:
        value = str(source.get(key) or "").strip()
        if value:
            normalized[key] = value
    return normalized


def _first_error_message(raw_value: Any) -> str:
    if isinstance(raw_value, str):
        return str(raw_value).strip()
    source = _as_mapping(raw_value)
    if source:
        return str(source.get("error_message") or "").strip()
    if isinstance(raw_value, (list, tuple)):
        for item in raw_value:
            message = _first_error_message(item)
            if message:
                return message
    return ""


def _canonical_event_summary(payload: Dict[str, Any], summary_payload: Dict[str, Any]) -> Dict[str, Any]:
    for candidate in (
        summary_payload.get("summary"),
        summary_payload.get("aggregate_summary"),
        payload.get("summary"),
    ):
        summary = _normalize_event_summary(candidate)
        if summary:
            return summary
    return {}


def _canonical_event_error_code(event: Dict[str, Any], payload: Dict[str, Any], summary_payload: Dict[str, Any]) -> str:
    for value in (
        event.get("error_code"),
        event.get("error_type"),
        summary_payload.get("error_code"),
        summary_payload.get("error_type"),
        payload.get("error_code"),
        payload.get("error_type"),
        _as_mapping(summary_payload.get("typed_error")).get("error_code"),
    ):
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""


def _canonical_event_error_message(event: Dict[str, Any], payload: Dict[str, Any], summary_payload: Dict[str, Any]) -> str:
    for value in (
        event.get("error_message"),
        summary_payload.get("error_message"),
        payload.get("error_message"),
        _as_mapping(summary_payload.get("typed_error")).get("error_message"),
        summary_payload.get("errors"),
        summary_payload.get("typed_errors"),
        summary_payload.get("task_summaries"),
        payload.get("errors"),
        payload.get("task_summaries"),
    ):
        normalized = _first_error_message(value)
        if normalized:
            return normalized
    return ""


def _canonical_event_warning_code(event: Dict[str, Any], payload: Dict[str, Any], summary_payload: Dict[str, Any]) -> str:
    for value in (
        event.get("warning_code"),
        summary_payload.get("warning_code"),
        payload.get("warning_code"),
        _as_mapping(summary_payload.get("summary")).get("warning_code"),
        _as_mapping(payload.get("summary")).get("warning_code"),
    ):
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""


def _canonical_event_warning_message(event: Dict[str, Any], payload: Dict[str, Any], summary_payload: Dict[str, Any]) -> str:
    for value in (
        event.get("warning_message"),
        summary_payload.get("warning_message"),
        payload.get("warning_message"),
        _as_mapping(summary_payload.get("summary")).get("warning_message"),
        _as_mapping(payload.get("summary")).get("warning_message"),
    ):
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""


def normalize_job_event_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(event)
    normalized_status = normalize_record_state_value(normalized.get("status"))
    if normalized_status:
        normalized["status"] = normalized_status
    raw_payload = normalized.get("payload")
    if raw_payload is None:
        payload = {}
    elif isinstance(raw_payload, Mapping):
        payload = dict(raw_payload)
    else:
        raise ValueError("payload must be an object")
    payload_state = normalize_record_state_value(payload.get("state"))
    if payload_state:
        payload["state"] = payload_state
    summary_payload = _as_mapping(payload.get("summary_payload"))
    payload["summary_payload"] = {
        "kind": _event_text_field_with_fallback(summary_payload, payload, "kind"),
        "task_label": _event_text_field_with_fallback(summary_payload, payload, "task_label"),
        "task_index": _event_int_field_with_fallback(
            summary_payload,
            payload,
            "task_index",
            field_name="summary_payload.task_index",
        ),
        "task_total": _event_int_field_with_fallback(
            summary_payload,
            payload,
            "task_total",
            field_name="summary_payload.task_total",
        ),
        "phase_percent": _event_int_field_with_fallback(
            summary_payload,
            payload,
            "phase_percent",
            field_name="summary_payload.phase_percent",
        ),
        "summary": _canonical_event_summary(payload, summary_payload),
    }
    warning_code = _canonical_event_warning_code(normalized, payload, summary_payload)
    warning_message = _canonical_event_warning_message(normalized, payload, summary_payload)
    if warning_code:
        payload["summary_payload"]["warning_code"] = warning_code
    if warning_message:
        payload["summary_payload"]["warning_message"] = warning_message
    normalized["payload"] = payload
    normalized["error_code"] = _canonical_event_error_code(normalized, payload, summary_payload)
    normalized["error_message"] = _canonical_event_error_message(normalized, payload, summary_payload)
    normalized["warning_code"] = warning_code
    normalized["warning_message"] = warning_message
    return normalized


# ── Exchange normalization ──


def normalize_exchange_label(raw_value: str) -> str:
    return canonical_source_label(raw_value)


def normalize_exchange_code(raw_value: Any) -> str:
    return canonical_source_code(raw_value)


def normalize_project_type_label(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    return PROJECT_TYPE_LABELS.get(value, value)


def normalize_project_type_code(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if value in PROJECT_TYPE_LABELS:
        return value
    return PROJECT_TYPE_CODES_BY_LABEL.get(value, "")


# ── Date parsing ──


def parse_user_supplied_date(raw_value: Any, *, field_name: str) -> dt.date | None:
    if raw_value is None or raw_value == "":
        return None
    if not isinstance(raw_value, str):
        raise UserInputError(f"invalid {field_name}: {raw_value!r} (expected YYYY-MM-DD)")
    text = raw_value.strip()
    if not text:
        return None
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise UserInputError(f"invalid {field_name}: {text!r} (expected YYYY-MM-DD)") from exc


def validate_streaming_job_dates(payload: Dict[str, Any]) -> None:
    start_date = parse_user_supplied_date(payload.get("start_date"), field_name="start_date")
    end_date = parse_user_supplied_date(payload.get("end_date"), field_name="end_date")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise UserInputError("start_date must be on or before end_date")


def parse_positive_int(raw_value: Any, *, field_name: str, default: int) -> int:
    if raw_value in {None, ""}:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise UserInputError(f"invalid {field_name}: {raw_value!r} (expected integer)") from exc
    if value <= 0:
        raise UserInputError(f"invalid {field_name}: {raw_value!r} (expected integer > 0)")
    return value


# ── Mapping normalization ──


def mapping_rule_title(rule_kind: str) -> str:
    normalized = str(rule_kind or "").strip()
    spec = MAPPING_RULE_SPECS.get(normalized)
    return str(spec.get("title") or normalized) if spec else normalized


def mapping_rule_kind(match_field: str, target_field: str) -> str:
    normalized_match = str(match_field or "").strip().lower()
    normalized_target = str(target_field or "").strip().lower()
    for rule_kind, spec in MAPPING_RULE_SPECS.items():
        if spec["match_field"] == normalized_match and spec["target_field"] == normalized_target:
            return rule_kind
    return ""


def mapping_rule_metadata(*, match_field: str, target_field: str) -> Dict[str, str]:
    rule_kind_val = mapping_rule_kind(match_field, target_field)
    spec = MAPPING_RULE_SPECS.get(rule_kind_val, {})
    return {
        "rule_kind": rule_kind_val,
        "rule_title": str(spec.get("title") or ""),
        "source_label": str(spec.get("source_label") or "来源"),
        "target_label": str(spec.get("target_label") or "目标值"),
    }


def job_type_label(job_type: str) -> str:
    return JOB_TYPE_LABELS.get(str(job_type or "").strip(), str(job_type or "").strip() or "任务")


def normalize_match_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def normalize_mapping_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    if "company_name" in payload:
        company_name = parse_text(payload.get("company_name"), field_name="company_name")
        source_name = parse_text(payload.get("source_name"), field_name="source_name")
        if company_name and source_name and company_name != source_name:
            raise ValueError("company_name must match source_name")
    source_name = legacy_mapping_source_name(payload)
    target_value = parse_text(payload.get("target_value"), field_name="target_value")
    rule_kind_raw = parse_text(payload.get("rule_kind"), field_name="rule_kind")
    rule_spec = MAPPING_RULE_SPECS.get(rule_kind_raw, {})
    match_field = parse_text(
        payload.get("match_field") if "match_field" in payload else rule_spec.get("match_field"),
        field_name="match_field",
        default="transferor",
    )
    raw_target_field = payload.get("target_field") if "target_field" in payload else rule_spec.get("target_field")
    raw_group_name = payload.get("group_name")
    raw_source_type = payload.get("source_type")
    group_name_candidate = parse_text(raw_group_name, field_name="group_name") or target_value
    source_type_candidate = parse_text(raw_source_type, field_name="source_type")
    target_field = parse_text(raw_target_field, field_name="target_field") or (
        "group_name" if group_name_candidate and not source_type_candidate else "source_type"
    )
    group_name = group_name_candidate if target_field == "group_name" else parse_text(raw_group_name, field_name="group_name")
    source_type = (source_type_candidate or target_value) if target_field == "source_type" else parse_text(raw_source_type, field_name="source_type")
    normalized_rule_kind = rule_kind_raw or mapping_rule_kind(match_field, target_field)
    return {
        "source_name": source_name,
        "match_field": match_field,
        "target_field": target_field,
        "target_value": target_value,
        "group_name": group_name,
        "source_type": source_type,
        "rule_kind": normalized_rule_kind,
    }


def validate_mapping_payload(normalized: Dict[str, str]) -> None:
    source_name = str(normalized.get("source_name") or "").strip()
    target_value = str(normalized.get("target_value") or "").strip()
    match_field = str(normalized.get("match_field") or "").strip()
    target_field = str(normalized.get("target_field") or "").strip()
    if not source_name:
        raise ValueError("source_name is required")
    if not target_value:
        raise ValueError("target_value is required")
    if match_field not in MAPPING_MATCH_FIELDS:
        raise ValueError(f"invalid match_field: {match_field}")
    if target_field not in {"group_name", "source_type"}:
        raise ValueError(f"invalid target_field: {target_field}")
    if target_field == "source_type" and target_value not in MAPPING_SOURCE_TYPES:
        raise ValueError(f"invalid source_type: {target_value}")


def resolve_directory_setting(raw_value: Any, *, setting_name: str) -> str:
    path_value = parse_local_path(raw_value, field_name=setting_name)
    if not path_value:
        raise UserInputError(f"{setting_name} is required")
    try:
        os.makedirs(path_value, exist_ok=True)
    except OSError as exc:
        raise UserInputError(f"{setting_name} not writable: {exc}") from exc
    if not os.path.isdir(path_value):
        raise UserInputError(f"{setting_name} is not a directory: {path_value}")
    return path_value


def mapping_scope_miss_payload(*, source_name: str, match_field: str) -> Dict[str, Any]:
    source_label = str(source_name or "").strip() or "未命名来源"
    field_label = "集团" if str(match_field or "").strip() == "group" else "转让方"
    return {
        "scope_miss": True,
        "scope_miss_reason_code": "mapping_source_not_found",
        "scope_miss_message": "未找到匹配该" + field_label + "来源\u201c" + source_label + "\u201d的记录；本次仅保存规则，不启动回刷",
    }


def path_within_root(path_value: str, root_value: str) -> bool:
    target_value = str(path_value or "").strip()
    root_value_text = str(root_value or "").strip()
    if not target_value or not root_value_text:
        return False
    target = os.path.realpath(os.path.abspath(target_value))
    root = os.path.realpath(os.path.abspath(root_value_text))
    try:
        return os.path.commonpath([target, root]) == root
    except ValueError:
        return False
