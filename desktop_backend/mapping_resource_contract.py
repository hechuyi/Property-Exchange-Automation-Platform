"""Read-resource contract helpers for mappings resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterable

UNKNOWN_BUSINESS_LABEL = "未识别项目类型"
UNTRUSTED_EXTERNAL_TEXT = "UNTRUSTED_EXTERNAL_TEXT"


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unsafe_business_values(source: Mapping[str, Any]) -> set[str]:
    values = {_text(source.get("raw_business_label"))}
    if _text(source.get("business_label")) == UNTRUSTED_EXTERNAL_TEXT:
        values.add(UNTRUSTED_EXTERNAL_TEXT)
    return {value for value in values if value}


def _safe_text(value: Any, fallback: str = "", unsafe_values: set[str] | None = None) -> str:
    text = _text(value)
    unsafe = unsafe_values or set()
    return fallback if text == UNTRUSTED_EXTERNAL_TEXT or text in unsafe else text


def _safe_business_label(source: Mapping[str, Any]) -> str:
    label = _safe_text(source.get("business_label"), unsafe_values=_unsafe_business_values(source))
    if label:
        return label
    return UNKNOWN_BUSINESS_LABEL if _text(source.get("raw_business_label")) else ""


def _integer(value: Any, field_name: str) -> int:
    if value is None or (isinstance(value, str) and value == ""):
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _optional_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"mappings backlog sections[*].items[*].{field_name} must be an object")
    return dict(value)


def _optional_list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"mappings backlog sections[*].items[*].{field_name} must be a list")
    return value


def _normalize_evidence_entry(entry: Any, unsafe_values: set[str] | None = None) -> str | dict[str, str]:
    if isinstance(entry, Mapping):
        safe_entry = {
            safe_key: _safe_text(value, unsafe_values=unsafe_values)
            for key, value in entry.items()
            if (safe_key := _safe_text(key, unsafe_values=unsafe_values)) and safe_key != "raw_business_label"
        }
        return {key: value for key, value in safe_entry.items() if value}
    return _safe_text(entry, unsafe_values=unsafe_values)


def _normalize_recommended_rule(rule: Any, unsafe_values: set[str] | None = None) -> dict[str, Any]:
    source = _optional_mapping(rule, "recommended_rule")
    return {
        "rule_kind": _safe_text(source.get("rule_kind"), unsafe_values=unsafe_values),
        "title": _safe_text(source.get("title"), unsafe_values=unsafe_values),
        "source_name": _safe_text(source.get("source_name"), unsafe_values=unsafe_values),
        "target_value": _safe_text(source.get("target_value"), unsafe_values=unsafe_values),
    }


def _normalize_candidate_resolution(item: Any, unsafe_values: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ValueError("mappings backlog sections[*].items[*].candidate_resolutions[*] must be objects")
    source = dict(item)
    evidence_chain = _optional_list(
        source.get("evidence_chain"),
        "candidate_resolutions[*].evidence_chain",
    )
    return {
        "field": _text(source.get("field")),
        "rule_kind": _text(source.get("rule_kind")),
        "match_field": _text(source.get("match_field")),
        "target_field": _text(source.get("target_field")),
        "source_name": _safe_text(source.get("source_name"), unsafe_values=unsafe_values),
        "target_value": _safe_text(source.get("target_value"), unsafe_values=unsafe_values),
        "label": _safe_text(source.get("label"), unsafe_values=unsafe_values),
        "title": _safe_text(source.get("title"), unsafe_values=unsafe_values),
        "evidence_chain": [
            safe_entry
            for entry in evidence_chain
            if (safe_entry := _normalize_evidence_entry(entry, unsafe_values=unsafe_values))
        ],
    }


def build_mapping_entry_view(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, Mapping):
        raise ValueError("mappings entries[*] must be objects")
    source = _as_mapping(entry)
    return {
        "entry_id": _text(source.get("entry_id")),
        "rule_kind": _text(source.get("rule_kind")),
        "rule_title": _text(source.get("rule_title")),
        "source_name": _text(source.get("source_name")),
        "target_value": _text(source.get("target_value")),
        "match_field": _text(source.get("match_field")),
        "target_field": _text(source.get("target_field")),
        "notes": _text(source.get("notes")),
        "updated_at": _text(source.get("updated_at")),
    }


def build_pending_mapping_view(item: Any, unsafe_values: set[str] | None = None) -> dict[str, Any]:
    source = _as_mapping(item)
    gap_codes = _optional_list(source.get("gap_codes"), "gap_codes")
    available_rule_kinds = _optional_list(source.get("available_rule_kinds"), "available_rule_kinds")
    candidate_resolutions = _optional_list(source.get("candidate_resolutions"), "candidate_resolutions")
    return {
        "record_id": _text(source.get("record_id")),
        "revision_id": _integer(source.get("revision_id"), "mappings backlog sections[*].items[*].revision_id"),
        "project_code": _text(source.get("project_code")),
        "project_name": _text(source.get("project_name")),
        "project_type_code": _text(source.get("project_type_code")),
        "project_type_label": _text(source.get("project_type_label")),
        "exchange_code": _text(source.get("exchange_code")),
        "exchange_label": _text(source.get("exchange_label")),
        "created_at": _text(source.get("created_at")),
        "state": _text(source.get("state")),
        "status_label": _text(source.get("status_label")),
        "status_detail": _text(source.get("status_detail")),
        "source_name": _safe_text(source.get("source_name"), unsafe_values=unsafe_values),
        "current_group": _safe_text(source.get("current_group"), unsafe_values=unsafe_values),
        "current_type": _safe_text(source.get("current_type"), unsafe_values=unsafe_values),
        "resolved_group": _safe_text(source.get("resolved_group"), unsafe_values=unsafe_values),
        "resolved_type": _safe_text(source.get("resolved_type"), unsafe_values=unsafe_values),
        "gap_codes": [_text(code) for code in gap_codes],
        "blocking_reason_code": _text(source.get("blocking_reason_code")),
        "recommended_rule": _normalize_recommended_rule(source.get("recommended_rule"), unsafe_values),
        "available_rule_kinds": [_text(kind) for kind in available_rule_kinds],
        "candidate_resolutions": [
            _normalize_candidate_resolution(resolution, unsafe_values)
            for resolution in candidate_resolutions
        ],
        "has_conflict": bool(source.get("has_conflict")),
    }


def _build_mapping_section_item_view(item: Any, section_id: str) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ValueError("mappings backlog sections[*].items[*] must be objects")
    source = _as_mapping(item)
    unsafe_values = _unsafe_business_values(source)
    evidence_codes = _optional_list(source.get("evidence_codes"), "evidence_codes")
    view = build_pending_mapping_view(source, unsafe_values)
    view.update(
        {
            "record_id": _text(source.get("record_id")),
            "revision_id": _integer(source.get("revision_id"), "mappings backlog sections[*].items[*].revision_id"),
            "business_id": _text(source.get("business_id")),
            "business_label": _safe_business_label(source),
            "blocker_kind": _text(source.get("blocker_kind")),
            "blocker_subtype": _text(source.get("blocker_subtype")),
            "queue_section": _text(source.get("queue_section")) or section_id,
            "record_family": _text(source.get("record_family")),
            "exchange_code": _text(source.get("exchange_code")),
            "exchange_label": _text(source.get("exchange_label")),
            "state": _text(source.get("state")),
            "status_label": _text(source.get("status_label")),
            "status_detail": _text(source.get("status_detail")),
            "actionable": bool(source.get("actionable")),
            "audit_only": bool(source.get("audit_only")),
            "evidence_codes": [_text(code) for code in evidence_codes],
        }
    )
    return view


def _build_mapping_section_view(section: Any) -> dict[str, Any]:
    source = _as_mapping(section)
    section_id = _text(source.get("section_id"))
    items = source.get("items")
    if not isinstance(items, list):
        raise ValueError("mappings backlog section.items must be a list")
    return {
        "section_id": section_id,
        "title": _text(source.get("title")),
        "count": _integer(source.get("count"), "mappings backlog sections[*].count"),
        "cta_kind": _text(source.get("cta_kind")),
        "items": [
            _build_mapping_section_item_view(item, section_id)
            for item in items
        ] if isinstance(items, list) else [],
    }


def build_mappings_resource(*, entries: Iterable[Any], backlog: Any) -> dict[str, Any]:
    if not isinstance(backlog, Mapping):
        raise TypeError("mappings backlog must be a canonical mapping resource with sections")
    if entries is None:
        raise ValueError("mappings entries must be provided")
    try:
        entry_items = list(entries)
    except TypeError as exc:
        raise ValueError("mappings entries must be iterable") from exc
    payload = dict(backlog)
    sections = payload.get("sections")
    if not isinstance(sections, list):
        raise ValueError("mappings backlog sections must be a list")
    payload["sections"] = [_build_mapping_section_view(section) for section in sections]
    payload["entries"] = [build_mapping_entry_view(item) for item in entry_items]
    return payload
