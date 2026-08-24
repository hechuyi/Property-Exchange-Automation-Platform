"""Source-aware surface support contract for records/export/one-click."""

from __future__ import annotations

from typing import Iterable

from peap_core.business_catalog import get_business_descriptor
from peap_core.family_catalog import get_family_descriptor
from peap_core.source_catalog import canonical_source_code

from .business_runtime import iter_source_business_bindings
from .output_contract import get_supported_source_ids_for_kind
from .projection_registry import resolve_projection_profile

SURFACE_RECORDS = "records"
SURFACE_EXPORT = "export"
SURFACE_ONE_CLICK = "one_click"
KNOWN_SURFACES = (SURFACE_RECORDS, SURFACE_ONE_CLICK, SURFACE_EXPORT)
_KNOWN_SURFACE_SET = frozenset(KNOWN_SURFACES)


def _normalized_family_id(record_family: str) -> str:
    return get_family_descriptor(record_family).family_id


def _normalized_business_id(record_family: str, business_id: str) -> str:
    return get_business_descriptor(business_id, family_id=record_family).business_id


def family_source_ids(record_family: str) -> tuple[str, ...]:
    family_id = _normalized_family_id(record_family)
    return tuple(
        str(source_id or "").strip()
        for source_id in tuple(get_family_descriptor(family_id).source_ids or ())
        if str(source_id or "").strip()
    )


def _implemented_runtime_source_ids(family_id: str, business_id: str) -> tuple[str, ...]:
    normalized_business = _normalized_business_id(family_id, business_id)
    ordered: list[str] = []
    seen: set[str] = set()
    for binding in iter_source_business_bindings(record_family=family_id):
        if binding.business_id != normalized_business:
            continue
        if not bool(getattr(binding, "implemented", True)):
            continue
        source_id = str(binding.source_id or "").strip()
        if source_id and source_id not in seen:
            seen.add(source_id)
            ordered.append(source_id)
    return tuple(ordered)


def supported_sources_for_surface(
    *,
    record_family: str,
    business_id: str,
    surface: str,
) -> tuple[str, ...]:
    family_id = _normalized_family_id(record_family)
    normalized_business = _normalized_business_id(family_id, business_id)
    normalized_surface = str(surface or "").strip()
    if normalized_surface not in _KNOWN_SURFACE_SET:
        raise ValueError(f"unknown surface: {surface}")

    if normalized_surface in {SURFACE_RECORDS, SURFACE_EXPORT}:
        profile = resolve_projection_profile(family_id, normalized_business)
        if profile is None:
            return ()
        constrained_sources = get_supported_source_ids_for_kind(profile.output_kind)
        runtime_sources = set(_implemented_runtime_source_ids(family_id, normalized_business))
        if constrained_sources is None:
            return tuple(
                source_id for source_id in family_source_ids(family_id) if source_id in runtime_sources
            )
        return tuple(source_id for source_id in constrained_sources if source_id in runtime_sources)

    runtime_sources = [
        str(binding.source_id or "").strip()
        for binding in iter_source_business_bindings(record_family=family_id)
        if binding.business_id == normalized_business and bool(getattr(binding, "implemented", True))
    ]
    projection_sources = supported_sources_for_surface(
        record_family=family_id,
        business_id=normalized_business,
        surface=SURFACE_EXPORT,
    )
    if projection_sources:
        runtime_source_set = {source_id for source_id in runtime_sources if source_id}
        return tuple(source_id for source_id in projection_sources if source_id in runtime_source_set)
    ordered: list[str] = []
    seen: set[str] = set()
    for source_id in runtime_sources:
        if source_id and source_id not in seen:
            seen.add(source_id)
            ordered.append(source_id)
    return tuple(ordered)


def scope_supported_for_surface(
    *,
    record_family: str,
    business_ids: Iterable[str],
    exchange: str,
    surface: str,
) -> bool:
    family_id = _normalized_family_id(record_family)
    normalized_exchange = str(canonical_source_code(exchange) or exchange or "").strip() or "all"
    for business_id in business_ids:
        supported = set(
            supported_sources_for_surface(
                record_family=family_id,
                business_id=business_id,
                surface=surface,
            )
        )
        if not supported:
            return False
        if normalized_exchange == "all":
            continue
        elif normalized_exchange not in supported:
            return False
    return True


__all__ = [
    "KNOWN_SURFACES",
    "SURFACE_EXPORT",
    "SURFACE_ONE_CLICK",
    "SURFACE_RECORDS",
    "family_source_ids",
    "scope_supported_for_surface",
    "supported_sources_for_surface",
]
