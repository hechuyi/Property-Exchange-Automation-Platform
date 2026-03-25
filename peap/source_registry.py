"""Pure source capability registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable

from .streaming_models import RecordFamily


@dataclass(frozen=True)
class SourceCapability:
    source_id: str
    site_label: str
    supported_record_families: tuple[RecordFamily, ...]
    supported_job_types: tuple[str, ...]
    downloader_key: str
    adapter_key: str
    enabled: bool = True


_SOURCE_REGISTRY: Dict[str, SourceCapability] = {}


def _normalize_source_id(source_id: str) -> str:
    return str(source_id or "").strip()


def register_source(capability: SourceCapability) -> None:
    source_id = _normalize_source_id(capability.source_id)
    if not source_id:
        raise ValueError("source_id is empty")
    _SOURCE_REGISTRY[source_id] = capability


def get_source(source_id: str) -> SourceCapability:
    normalized = _normalize_source_id(source_id)
    try:
        return _SOURCE_REGISTRY[normalized]
    except KeyError as exc:
        raise KeyError(normalized) from exc


def list_sources(record_family: str | None = None) -> list[SourceCapability]:
    normalized_family = _normalize_source_id(record_family)
    results: list[SourceCapability] = []
    for capability in _SOURCE_REGISTRY.values():
        if not capability.enabled:
            continue
        if normalized_family and normalized_family not in capability.supported_record_families:
            continue
        results.append(capability)
    return results
