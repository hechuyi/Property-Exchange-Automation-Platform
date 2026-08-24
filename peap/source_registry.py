"""Compatibility facade over the shared source catalog."""

from __future__ import annotations

from peap_core.source_catalog import (
    SourceDescriptor,
    get_source_descriptor,
    list_source_descriptors,
)

SourceCapability = SourceDescriptor


def register_source(capability: SourceCapability) -> None:
    raise RuntimeError(
        "source catalog is immutable at runtime; update peap_core.source_catalog instead"
    )


def get_source(source_id: str) -> SourceCapability:
    return get_source_descriptor(source_id)


def list_sources(record_family: str | None = None) -> list[SourceCapability]:
    return list_source_descriptors(record_family=record_family)


__all__ = ["SourceCapability", "get_source", "list_sources", "register_source"]
