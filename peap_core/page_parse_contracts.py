"""Page-parse contracts for the parser subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from .snapshot_contracts import _freeze_value, _serialize_value

DiagnosticSeverity = Literal["info", "warn", "error"]
DiagnosticStage = Literal["decode", "classify", "parse", "assemble", "normalize", "policy"]
Recoverability = Literal["none", "partial", "recoverable", "unrecoverable"]
SourceMatchStatus = Literal["matched", "ambiguous", "unknown"]
EvidenceSourceKind = Literal["dom", "json", "meta", "url", "derived"]


@dataclass(frozen=True)
class EvidenceRef:
    snapshot_id: str
    source_kind: EvidenceSourceKind
    locator: str
    excerpt: str
    transform_ids: tuple[str, ...] = ()
    confidence: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "transform_ids", tuple(str(item) for item in self.transform_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "source_kind": self.source_kind,
            "locator": self.locator,
            "excerpt": self.excerpt,
            "transform_ids": list(self.transform_ids),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class Diagnostic:
    severity: DiagnosticSeverity
    type: str
    message: str
    stage: DiagnosticStage
    evidence_refs: tuple[EvidenceRef, ...] = ()
    recoverability: Recoverability = "none"

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "type": self.type,
            "message": self.message,
            "stage": self.stage,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "recoverability": self.recoverability,
        }


@dataclass(frozen=True)
class SourceMatch:
    source_id: str
    page_kind: str
    confidence: float
    status: SourceMatchStatus
    reasons: tuple[str, ...] = ()
    classifier_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "page_kind": self.page_kind,
            "confidence": self.confidence,
            "status": self.status,
            "reasons": list(self.reasons),
            "classifier_version": self.classifier_version,
        }


@dataclass(frozen=True)
class PageParseResult:
    snapshot_id: str
    source_match: SourceMatch
    parser_family_id: str
    parser_family_version: str
    variant_id: str
    variant_version: str
    page_identity: Mapping[str, Any]
    facts: tuple[Any, ...] = ()
    outgoing_refs: tuple[Any, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    provenance: tuple[EvidenceRef, ...] = ()
    recoverability: Recoverability = "none"

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_identity", _freeze_value(self.page_identity))
        object.__setattr__(self, "facts", _freeze_value(self.facts))
        object.__setattr__(self, "outgoing_refs", _freeze_value(self.outgoing_refs))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "provenance", tuple(self.provenance))

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "source_match": self.source_match.to_dict(),
            "parser_family_id": self.parser_family_id,
            "parser_family_version": self.parser_family_version,
            "variant_id": self.variant_id,
            "variant_version": self.variant_version,
            "page_identity": _serialize_value(self.page_identity),
            "facts": _serialize_value(self.facts),
            "outgoing_refs": _serialize_value(self.outgoing_refs),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "provenance": [item.to_dict() for item in self.provenance],
            "recoverability": self.recoverability,
        }


__all__ = [
    "Diagnostic",
    "DiagnosticSeverity",
    "DiagnosticStage",
    "EvidenceRef",
    "EvidenceSourceKind",
    "PageParseResult",
    "Recoverability",
    "SourceMatch",
    "SourceMatchStatus",
]
