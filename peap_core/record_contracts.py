"""Assembled and canonical record contracts for the parser subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from .page_parse_contracts import PageParseResult
from .snapshot_contracts import _freeze_value, _serialize_value

CompletionState = Literal["partial", "sufficient", "conflicted", "blocked"]


@dataclass(frozen=True)
class AssembledRecordCandidate:
    assembly_id: str
    source_ids: tuple[str, ...]
    page_results: tuple[PageParseResult, ...]
    entity_keys: tuple[str, ...]
    completion_state: CompletionState
    missing_requirements: tuple[str, ...] = ()
    assembly_diagnostics: tuple[Any, ...] = ()
    raw_business_object: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_ids", tuple(str(item) for item in self.source_ids))
        object.__setattr__(self, "page_results", tuple(self.page_results))
        object.__setattr__(self, "entity_keys", tuple(str(item) for item in self.entity_keys))
        object.__setattr__(self, "missing_requirements", tuple(str(item) for item in self.missing_requirements))
        object.__setattr__(self, "assembly_diagnostics", _freeze_value(self.assembly_diagnostics))
        object.__setattr__(self, "raw_business_object", _freeze_value(self.raw_business_object))

    def to_dict(self) -> dict[str, Any]:
        return {
            "assembly_id": self.assembly_id,
            "source_ids": list(self.source_ids),
            "page_results": [item.to_dict() for item in self.page_results],
            "entity_keys": list(self.entity_keys),
            "completion_state": self.completion_state,
            "missing_requirements": list(self.missing_requirements),
            "assembly_diagnostics": _serialize_value(self.assembly_diagnostics),
            "raw_business_object": _serialize_value(self.raw_business_object),
        }


@dataclass(frozen=True)
class CanonicalRecord:
    record_id: str
    record_family: str
    source_identity: Mapping[str, Any]
    business_identity: Mapping[str, Any]
    canonical_fields: Mapping[str, Any]
    field_provenance: Mapping[str, Any]
    export_extras: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: tuple[Any, ...] = ()
    normalizer_version: str = ""
    policy_state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identity", _freeze_value(self.source_identity))
        object.__setattr__(self, "business_identity", _freeze_value(self.business_identity))
        object.__setattr__(self, "canonical_fields", _freeze_value(self.canonical_fields))
        object.__setattr__(self, "export_extras", _freeze_value(self.export_extras))
        object.__setattr__(self, "field_provenance", _freeze_value(self.field_provenance))
        object.__setattr__(self, "diagnostics", _freeze_value(self.diagnostics))
        object.__setattr__(self, "policy_state", _freeze_value(self.policy_state))

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "record_family": self.record_family,
            "source_identity": _serialize_value(self.source_identity),
            "business_identity": _serialize_value(self.business_identity),
            "canonical_fields": _serialize_value(self.canonical_fields),
            "export_extras": _serialize_value(self.export_extras),
            "field_provenance": _serialize_value(self.field_provenance),
            "diagnostics": _serialize_value(self.diagnostics),
            "normalizer_version": self.normalizer_version,
            "policy_state": _serialize_value(self.policy_state),
        }


__all__ = ["AssembledRecordCandidate", "CanonicalRecord", "CompletionState"]
