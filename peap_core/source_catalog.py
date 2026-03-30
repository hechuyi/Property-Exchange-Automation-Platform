"""Shared immutable source metadata for runtime consumers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDescriptor:
    source_id: str
    canonical_label: str
    site_label: str
    aliases: tuple[str, ...]
    supported_record_families: tuple[str, ...]
    supported_job_types: tuple[str, ...]
    downloader_key: str
    adapter_key: str
    enabled: bool = True


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_token(value: object) -> str:
    return _normalize_text(value).lower()


_SOURCE_DESCRIPTORS: tuple[SourceDescriptor, ...] = (
    SourceDescriptor(
        source_id="sse",
        canonical_label="上交所",
        site_label="Shanghai (SSE)",
        aliases=("shanghai", "上海联合产权交易所", "上交所"),
        supported_record_families=("listing",),
        supported_job_types=("download_ingest",),
        downloader_key="shanghai",
        adapter_key="sse",
    ),
    SourceDescriptor(
        source_id="cbex",
        canonical_label="北交所",
        site_label="Beijing (CBEX)",
        aliases=("beijing", "北京产权交易所", "北交所", "北交互联"),
        supported_record_families=("listing",),
        supported_job_types=("download_ingest",),
        downloader_key="beijing",
        adapter_key="cbex",
    ),
    SourceDescriptor(
        source_id="tpre",
        canonical_label="天交所",
        site_label="Tianjin (TPRE)",
        aliases=("tianjin", "天津产权交易中心", "天交所"),
        supported_record_families=("listing",),
        supported_job_types=("download_ingest",),
        downloader_key="tianjin",
        adapter_key="tpre",
    ),
    SourceDescriptor(
        source_id="cquae",
        canonical_label="重交所",
        site_label="Chongqing (CQUAE)",
        aliases=("chongqing", "重庆联交所", "重交所"),
        supported_record_families=("listing",),
        supported_job_types=("download_ingest",),
        downloader_key="chongqing",
        adapter_key="cquae",
    ),
)

_SOURCE_BY_ID = {descriptor.source_id: descriptor for descriptor in _SOURCE_DESCRIPTORS}
_ALIAS_INDEX = {
    token: descriptor
    for descriptor in _SOURCE_DESCRIPTORS
    for token in (
        _normalize_token(descriptor.source_id),
        _normalize_token(descriptor.canonical_label),
        _normalize_token(descriptor.site_label),
        *(_normalize_token(alias) for alias in descriptor.aliases),
    )
    if token
}


def list_source_descriptors(record_family: str | None = None) -> list[SourceDescriptor]:
    normalized_family = _normalize_token(record_family)
    results: list[SourceDescriptor] = []
    for descriptor in _SOURCE_DESCRIPTORS:
        if not descriptor.enabled:
            continue
        if normalized_family and normalized_family not in {
            _normalize_token(family) for family in descriptor.supported_record_families
        }:
            continue
        results.append(descriptor)
    return results


def get_source_descriptor(source_id: str) -> SourceDescriptor:
    normalized_source_id = _normalize_text(source_id)
    try:
        return _SOURCE_BY_ID[normalized_source_id]
    except KeyError as exc:
        raise KeyError(normalized_source_id) from exc


def resolve_source_descriptor(raw_value: object, *, allow_substring: bool = False) -> SourceDescriptor | None:
    normalized = _normalize_token(raw_value)
    if not normalized:
        return None
    direct = _ALIAS_INDEX.get(normalized)
    if direct is not None:
        return direct
    if not allow_substring:
        return None
    for token, descriptor in _ALIAS_INDEX.items():
        if token and token in normalized:
            return descriptor
    return None


def canonical_source_label(raw_value: object) -> str:
    text = _normalize_text(raw_value)
    descriptor = resolve_source_descriptor(text, allow_substring=True)
    if descriptor is not None:
        return descriptor.canonical_label
    return text


def canonical_source_code(raw_value: object) -> str:
    text = _normalize_text(raw_value)
    if _normalize_token(text) == "all":
        return "all"
    descriptor = resolve_source_descriptor(text, allow_substring=True)
    if descriptor is not None:
        return descriptor.source_id
    return text


def source_ids_for_record_family(record_family: str) -> tuple[str, ...]:
    return tuple(
        descriptor.source_id
        for descriptor in list_source_descriptors(record_family=record_family)
    )


__all__ = [
    "SourceDescriptor",
    "canonical_source_code",
    "canonical_source_label",
    "get_source_descriptor",
    "list_source_descriptors",
    "resolve_source_descriptor",
    "source_ids_for_record_family",
]
