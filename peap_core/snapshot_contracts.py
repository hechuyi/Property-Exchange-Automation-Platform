"""Snapshot and decoded-document contracts for the parser subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from types import MappingProxyType
from typing import Any, Mapping

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:  # pragma: no cover - optional import guard for lean environments
    BeautifulSoup = None


ImmutableValue = Any


def _freeze_value(value: Any) -> ImmutableValue:
    if BeautifulSoup is not None and isinstance(value, BeautifulSoup):
        return str(value)
    if is_dataclass(value):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _serialize_value(value: Any) -> Any:
    if is_dataclass(value):
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return {field.name: _serialize_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    return value


@dataclass(frozen=True)
class SnapshotEnvelope:
    snapshot_id: str
    captured_at: str
    source_url: str
    referrer_url: str
    content_type: str
    http_status: int
    storage_path: str
    digest: str
    fetch_metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fetch_metadata", _freeze_value(self.fetch_metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "captured_at": self.captured_at,
            "source_url": self.source_url,
            "referrer_url": self.referrer_url,
            "content_type": self.content_type,
            "http_status": self.http_status,
            "storage_path": self.storage_path,
            "digest": self.digest,
            "fetch_metadata": _serialize_value(self.fetch_metadata),
        }


@dataclass(frozen=True)
class DecodedDocument:
    snapshot_id: str
    document_kind: str
    primary_text: str
    dom: Any
    embedded_json: tuple[Any, ...] = ()
    links: tuple[str, ...] = ()
    attachments: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    decoder_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "dom", _freeze_value(self.dom))
        object.__setattr__(self, "embedded_json", _freeze_value(self.embedded_json))
        object.__setattr__(self, "links", tuple(str(item) for item in self.links))
        object.__setattr__(self, "attachments", tuple(str(item) for item in self.attachments))
        object.__setattr__(self, "metadata", _freeze_value(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "document_kind": self.document_kind,
            "primary_text": self.primary_text,
            "dom": _serialize_value(self.dom),
            "embedded_json": _serialize_value(self.embedded_json),
            "links": _serialize_value(self.links),
            "attachments": _serialize_value(self.attachments),
            "metadata": _serialize_value(self.metadata),
            "decoder_version": self.decoder_version,
        }


__all__ = ["DecodedDocument", "SnapshotEnvelope"]
