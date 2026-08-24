from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .business_catalog import get_business_descriptor
from .family_catalog import get_family_descriptor
from .source_catalog import canonical_source_code


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _preferred_business_label(descriptor) -> str:
    canonical_label = _text(descriptor.canonical_label)
    if canonical_label:
        return canonical_label
    for alias in descriptor.aliases:
        label = _text(alias)
        if label and any(ord(char) > 127 for char in label):
            return label
    return _text(descriptor.business_id)


def _project_type_fallback(descriptor) -> str:
    project_type_label = _text(getattr(descriptor, "project_type_label", ""))
    if project_type_label:
        return project_type_label
    preferred_label = _preferred_business_label(descriptor)
    if preferred_label:
        return preferred_label
    return _text(descriptor.business_id)


def _scope_object(scope: Mapping[str, Any] | None) -> dict:
    if scope is None:
        return {}
    if not isinstance(scope, Mapping):
        raise TypeError("scope must be an object")
    return dict(scope)


def validate_explicit_business_scope(scope: Mapping[str, Any] | None, *, context: str = "explicit business scope") -> None:
    source = _scope_object(scope)

    def scope_text(field_name: str) -> str:
        raw_value = source.get(field_name)
        if raw_value is None or raw_value == "":
            return ""
        if not isinstance(raw_value, str):
            raise ValueError(f"{field_name} must be a string in {context}")
        return raw_value.strip()

    record_family = scope_text("record_family")
    business_id = scope_text("business_id")
    business_label = scope_text("business_label")
    exchange = scope_text("exchange")
    has_any_explicit_scope = any((record_family, business_id, business_label, exchange))
    if not has_any_explicit_scope:
        return
    if not record_family or not business_id:
        raise ValueError(f"{context} requires record_family and business_id together")
    try:
        normalized_family = get_family_descriptor(record_family).family_id
    except KeyError as exc:
        raise ValueError(f"unknown record_family in {context}: {record_family}") from exc
    if business_id not in {"", "all"}:
        try:
            get_business_descriptor(business_id, family_id=normalized_family)
        except KeyError as exc:
            raise ValueError(f"unknown business_id in {context}: {business_id}") from exc


def build_business_hint(
    *,
    record_family: Any = "listing",
    business_id: Any = "",
    business_label: Any = "",
    exchange: Any = "",
) -> dict[str, str]:
    family_id = _text(record_family, "listing")
    raw_business_id = _text(business_id)
    if raw_business_id in {"", "all"}:
        return {}

    try:
        normalized_family = get_family_descriptor(family_id).family_id
    except KeyError as exc:
        raise ValueError(f"unknown record_family: {family_id}") from exc

    try:
        descriptor = get_business_descriptor(raw_business_id, family_id=normalized_family)
    except KeyError as exc:
        raise ValueError(f"unknown business_id: {raw_business_id}") from exc

    normalized_label = _text(business_label) or _preferred_business_label(descriptor)
    hint = {
        "record_family": normalized_family,
        "business_id": descriptor.business_id,
        "business_label": normalized_label,
        "project_type_fallback": _project_type_fallback(descriptor),
    }
    normalized_exchange = _text(canonical_source_code(exchange))
    if normalized_exchange:
        hint["exchange"] = normalized_exchange
    return hint


def build_business_hint_from_scope(scope: Mapping[str, Any] | None) -> dict[str, str]:
    source = _scope_object(scope)
    validate_explicit_business_scope(source, context="explicit business scope")
    return build_business_hint(
        record_family=source.get("record_family") or "listing",
        business_id=source.get("business_id"),
        business_label=source.get("business_label"),
        exchange=source.get("exchange"),
    )


def resolve_explicit_business_scope(scope: Mapping[str, Any] | None) -> dict[str, str]:
    hint = build_business_hint_from_scope(scope)
    if not hint:
        return {}
    return {
        key: value
        for key, value in {
            "record_family": hint.get("record_family"),
            "business_id": hint.get("business_id"),
            "business_label": hint.get("business_label"),
            "exchange": hint.get("exchange"),
        }.items()
        if _text(value)
    }
