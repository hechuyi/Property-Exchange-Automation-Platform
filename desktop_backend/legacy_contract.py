"""Explicit temporary legacy request aliases.

These helpers isolate the remaining public compatibility behaviors that still
need to be accepted during the migration window. New code should prefer the
canonical fields directly and route any legacy reads through this module so the
aliases can be removed in one place later.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _as_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("payload must be an object")
    return dict(value)


def legacy_mapping_source_name(payload: Mapping[str, Any] | None) -> str:
    source = _as_mapping(payload)
    source_name = source.get("source_name")
    company_name = source.get("company_name")
    normalized_source_name = _legacy_text(source_name, field_name="source_name")
    normalized_company_name = _legacy_text(company_name, field_name="company_name")
    if normalized_source_name:
        return normalized_source_name
    if normalized_company_name:
        return normalized_company_name
    return ""


def legacy_record_scope_page_size(source: Mapping[str, Any] | None) -> Any:
    payload = _as_mapping(source)
    if payload.get("page_size") not in (None, ""):
        return payload.get("page_size")
    return payload.get("limit")


def _legacy_text(value: Any, *, field_name: str) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value.strip()
