"""Shared local artifact truth checks for persisted PEAP records."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from peap_core.record_identity import resolve_logical_record_identity

ArtifactPresenceStatus = Literal["available", "missing", "undeclared"]
EvidenceVerdictStatus = Literal[
    "verified",
    "present_unverified",
    "undeclared",
    "stale_reference",
    "invalid_shell",
    "identity_mismatch",
    "shared_official_page",
]
IdentityConfidence = Literal["verified", "unresolved"]

ARTIFACT_EVIDENCE_REPORT_CLASSIFICATIONS = frozenset(
    {
        "present_unverified",
        "stale_reference",
        "undeclared",
        "invalid_shell",
        "identity_mismatch",
    }
)
_ARTIFACT_EVIDENCE_PRIORITY_CLASSIFICATIONS = frozenset({"stale_reference", "undeclared", "invalid_shell"})
ARTIFACT_TRUTH_CONSUMED_SIDECAR_PAGE_KINDS = frozenset({"shared_official_page", "invalid_shell"})
_SIDECAR_LOCATOR_HASH_FIELDS = (
    "source_locator_hash",
    "final_locator_hash",
    "source_url_hash",
    "final_url_hash",
)

_SHELL_MARKER_READ_LIMIT_BYTES = 64 * 1024
_SSE_DEAL_NOTICE_SHELL_MARKER = bytes(
    (
        60,
        104,
        49,
        62,
        83,
        83,
        69,
        32,
        68,
        101,
        97,
        108,
        32,
        78,
        111,
        116,
        105,
        99,
        101,
        60,
        47,
        104,
        49,
        62,
    )
)


@dataclass(frozen=True)
class ArtifactPresence:
    status: ArtifactPresenceStatus
    authoritative_path: str = ""
    checked_paths: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.status == "available"

    @property
    def missing(self) -> bool:
        return self.status == "missing"


@dataclass(frozen=True)
class EvidenceVerdict:
    status: EvidenceVerdictStatus
    logical_record_identity: str
    identity_confidence: IdentityConfidence
    authoritative_path: str
    inspection_openable_path: str
    reason_code: str
    safe_evidence: Mapping[str, object]


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _clean_artifact_path(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, os.PathLike):
        path_value = os.fspath(value)
        if isinstance(path_value, str):
            return path_value.strip()
    raise TypeError("artifact path must be a string or os.PathLike")


def _json_object(value: object, *, field_name: str = "JSON value") -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        if not value.strip():
            return {}
        value = json.loads(value)
    if not isinstance(value, MappingABC):
        raise TypeError(f"{field_name} must decode to an object")
    return dict(value)


def _source_identity_object(data: Mapping[str, Any]) -> dict[str, Any]:
    if "source_identity_json" in data:
        source_identity_json = data.get("source_identity_json")
        if source_identity_json is not None and not (
            isinstance(source_identity_json, str) and not source_identity_json.strip()
        ):
            return _json_object(source_identity_json, field_name="source_identity_json")
    if "source_identity" in data:
        return _json_object(data.get("source_identity"), field_name="source_identity")
    return {}


def _first_existing_path(*paths: str) -> str:
    for path in paths:
        if path and os.path.isfile(path):
            return path
    return ""


def _artifact_declaration(source_file: object = "", archive_path: object = "") -> tuple[str, str]:
    archive = _clean_artifact_path(archive_path)
    source = _clean_artifact_path(source_file)
    if archive:
        return archive, "archive_path"
    if source:
        return source, "source_file"
    return "", "undeclared"


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _sha256_text(value: object) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _is_sse_deal_notice_shell(path: str) -> bool:
    with open(path, "rb") as handle:
        prefix = handle.read(_SHELL_MARKER_READ_LIMIT_BYTES)
    return _SSE_DEAL_NOTICE_SHELL_MARKER in prefix


def _safe_evidence(
    *,
    path_authority: str,
    authoritative_path: str,
    inspection_openable_path: str,
    content_sha256: str = "",
    invalid_shell_marker_family: str = "",
    identity_mismatch: Mapping[str, object] | None = None,
    sidecar_metadata: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    evidence: dict[str, object] = {
        "path_authority": path_authority,
        "authoritative_path": authoritative_path,
        "inspection_openable_path": inspection_openable_path,
    }
    if authoritative_path:
        evidence["checked_paths"] = (authoritative_path,)
    if content_sha256:
        evidence["content_sha256"] = content_sha256
    if invalid_shell_marker_family:
        evidence["invalid_shell_marker_family"] = invalid_shell_marker_family
    if identity_mismatch:
        evidence["identity_mismatch"] = dict(identity_mismatch)
    if sidecar_metadata:
        evidence.update(dict(sidecar_metadata))
    return evidence


def _project_code_identity_mismatch(data: Mapping[str, Any]) -> Mapping[str, object]:
    source_identity = _source_identity_object(data)
    db_project_code = _clean_text(data.get("project_code"))
    evidence_project_code = _clean_text(source_identity.get("project_code"))
    if not db_project_code or not evidence_project_code or db_project_code == evidence_project_code:
        return {}
    return {
        "field": "project_code",
        "db_value": db_project_code,
        "evidence_value": evidence_project_code,
    }


def classify_artifact_evidence_verdict(
    record: Mapping[str, Any],
    verdict: EvidenceVerdict,
) -> tuple[str, str]:
    """Map an evidence verdict to report/manifest artifact classification."""

    if verdict.status not in _ARTIFACT_EVIDENCE_PRIORITY_CLASSIFICATIONS and _project_code_identity_mismatch(record):
        return "identity_mismatch", "project_code_mismatch"
    return verdict.status, verdict.reason_code


def _read_evidence_sidecar(authoritative_path: str) -> dict[str, Any]:
    sidecar_path = Path(f"{authoritative_path}.peap-evidence.json")
    if not sidecar_path.is_file():
        return {}
    try:
        with sidecar_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    if not isinstance(payload, dict):
        raise TypeError("artifact evidence sidecar must decode to an object")
    return dict(payload)


def _hash_field_is_safe(value: object) -> bool:
    text = _clean_text(value)
    if not text.startswith("sha256:"):
        return False
    digest = text.removeprefix("sha256:")
    return len(digest) > 0 and all(char in "0123456789abcdefABCDEF" for char in digest)


def _identity_hints_match(
    hints: Mapping[str, Any],
    identity_components: Mapping[str, str],
) -> bool:
    if not isinstance(identity_components, MappingABC):
        raise TypeError("identity_components must be a mapping")
    expected = dict(identity_components)
    hint_record_family = _clean_text(hints.get("record_family"))
    hint_business_id = _clean_text(hints.get("business_id"))
    hint_project_code = _clean_text(hints.get("project_code"))
    hint_exchange = _clean_text(hints.get("exchange") or hints.get("source_id"))
    return (
        bool(hint_record_family)
        and hint_record_family == expected.get("record_family")
        and bool(hint_business_id)
        and hint_business_id == expected.get("business_id")
        and bool(hint_project_code)
        and hint_project_code == expected.get("project_code")
        and bool(hint_exchange)
        and hint_exchange == expected.get("exchange")
    )


def _hashed_identity_hints_match(hints: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    checked = False
    for field_name in ("project_code", "project_name"):
        expected_value = _clean_text(record.get(field_name))
        expected_hash = _clean_text(hints.get(f"{field_name}_hash"))
        if not expected_hash:
            continue
        checked = True
        if not expected_value or expected_hash != _sha256_text(expected_value):
            return False
    return checked


def _sidecar_identity_hints_match(
    hints: object,
    *,
    identity_components: Mapping[str, str],
    record: Mapping[str, Any],
) -> bool:
    if not isinstance(hints, dict):
        return False
    return _identity_hints_match(hints, identity_components) or _hashed_identity_hints_match(hints, record)


def _sidecar_locator_hashes_are_safe(sidecar: Mapping[str, Any]) -> bool:
    present_values = {
        field_name: _clean_text(sidecar.get(field_name))
        for field_name in _SIDECAR_LOCATOR_HASH_FIELDS
        if _clean_text(sidecar.get(field_name))
    }
    if not present_values:
        return False
    if not all(_hash_field_is_safe(value) for value in present_values.values()):
        return False
    has_source = bool(present_values.get("source_locator_hash") or present_values.get("source_url_hash"))
    has_final = bool(present_values.get("final_locator_hash") or present_values.get("final_url_hash"))
    return has_source and has_final


def _shared_official_page_sidecar_is_accepted(
    sidecar: Mapping[str, Any],
    *,
    content_sha256: str,
    identity_components: Mapping[str, str],
) -> bool:
    if sidecar.get("schema_version") != 1:
        return False
    if _clean_text(sidecar.get("page_kind")) != "shared_official_page":
        return False
    if _clean_text(sidecar.get("content_sha256")) != content_sha256:
        return False
    identity_hints = sidecar.get("identity_hints")
    if not isinstance(identity_hints, dict) or not _identity_hints_match(identity_hints, identity_components):
        return False
    return _hash_field_is_safe(sidecar.get("source_locator_hash")) and _hash_field_is_safe(
        sidecar.get("final_locator_hash")
    )


def _invalid_shell_sidecar_is_accepted(
    sidecar: Mapping[str, Any],
    *,
    content_sha256: str,
    identity_components: Mapping[str, str],
    record: Mapping[str, Any],
) -> bool:
    if sidecar.get("schema_version") != 1:
        return False
    if _clean_text(sidecar.get("page_kind")) != "invalid_shell":
        return False
    if _clean_text(sidecar.get("content_sha256")) != content_sha256:
        return False
    if not _sidecar_identity_hints_match(
        sidecar.get("identity_hints"),
        identity_components=identity_components,
        record=record,
    ):
        return False
    return _sidecar_locator_hashes_are_safe(sidecar)


def _safe_sidecar_evidence(sidecar: Mapping[str, Any], *, page_kind: str) -> Mapping[str, object]:
    evidence: dict[str, object] = {
        "page_kind": page_kind,
        "sidecar_schema_version": 1,
    }
    for key in _SIDECAR_LOCATOR_HASH_FIELDS:
        value = _clean_text(sidecar.get(key))
        if value and _hash_field_is_safe(value):
            evidence[key] = value
    return evidence


def _unaccepted_sidecar_evidence(page_kind: str) -> Mapping[str, object]:
    return {
        "page_kind": page_kind,
        "sidecar_schema_version": 1,
    }


def _sidecar_page_kind(sidecar: Mapping[str, Any]) -> str:
    page_kind = _clean_text(sidecar.get("page_kind"))
    if page_kind in ARTIFACT_TRUTH_CONSUMED_SIDECAR_PAGE_KINDS:
        return page_kind
    return ""


def _logical_record_identity_components(identity: object) -> Mapping[str, str]:
    try:
        components = identity.components
    except AttributeError as exc:
        raise TypeError("logical record identity components must be a mapping") from exc
    if not isinstance(components, MappingABC):
        raise TypeError("logical record identity components must be a mapping")
    return components


def resolve_declared_artifact_presence(
    *,
    source_file: object = "",
    archive_path: object = "",
) -> ArtifactPresence:
    """Resolve whether a persisted record still points at a real local artifact.

    `archive_path` is the authoritative target once declared. A stale archive path
    must not be masked by an older or alternate `source_file`.
    """

    authoritative_path, path_authority = _artifact_declaration(source_file, archive_path)
    if path_authority == "archive_path":
        return ArtifactPresence(
            status="available" if os.path.isfile(authoritative_path) else "missing",
            authoritative_path=authoritative_path,
            checked_paths=(authoritative_path,),
        )
    if path_authority == "source_file":
        return ArtifactPresence(
            status="available" if os.path.isfile(authoritative_path) else "missing",
            authoritative_path=authoritative_path,
            checked_paths=(authoritative_path,),
        )
    return ArtifactPresence(status="undeclared")


def resolve_artifact_evidence_verdict(
    record: Mapping[str, Any] | None = None,
    **overrides: Any,
) -> EvidenceVerdict:
    """Resolve local artifact evidence without making downstream action decisions."""

    if record is None:
        data: dict[str, Any] = {}
    elif isinstance(record, MappingABC):
        data = dict(record)
    else:
        raise TypeError("record must be a mapping")
    data.update(overrides)

    authoritative_path, path_authority = _artifact_declaration(
        data.get("source_file"),
        data.get("archive_path"),
    )
    managed_provenance_path = _clean_artifact_path(data.get("managed_provenance_path"))
    managed_openable_path = _first_existing_path(managed_provenance_path)
    identity = resolve_logical_record_identity(data)

    if not authoritative_path:
        return EvidenceVerdict(
            status="undeclared",
            logical_record_identity=identity.logical_record_identity,
            identity_confidence=identity.identity_confidence,
            authoritative_path="",
            inspection_openable_path=managed_openable_path,
            reason_code="artifact_path_undeclared",
            safe_evidence=_safe_evidence(
                path_authority=path_authority,
                authoritative_path="",
                inspection_openable_path=managed_openable_path,
            ),
        )

    if not os.path.isfile(authoritative_path):
        return EvidenceVerdict(
            status="stale_reference",
            logical_record_identity=identity.logical_record_identity,
            identity_confidence=identity.identity_confidence,
            authoritative_path=authoritative_path,
            inspection_openable_path=managed_openable_path,
            reason_code="authoritative_artifact_missing",
            safe_evidence=_safe_evidence(
                path_authority=path_authority,
                authoritative_path=authoritative_path,
                inspection_openable_path=managed_openable_path,
            ),
        )

    content_sha256 = _sha256_file(authoritative_path)
    if _is_sse_deal_notice_shell(authoritative_path):
        return EvidenceVerdict(
            status="invalid_shell",
            logical_record_identity=identity.logical_record_identity,
            identity_confidence=identity.identity_confidence,
            authoritative_path=authoritative_path,
            inspection_openable_path=authoritative_path,
            reason_code="sse_deal_notice_shell",
            safe_evidence=_safe_evidence(
                path_authority=path_authority,
                authoritative_path=authoritative_path,
                inspection_openable_path=authoritative_path,
                content_sha256=content_sha256,
                invalid_shell_marker_family="sse_deal_notice",
            ),
        )

    identity_mismatch = _project_code_identity_mismatch(data)
    if identity_mismatch:
        return EvidenceVerdict(
            status="identity_mismatch",
            logical_record_identity=identity.logical_record_identity,
            identity_confidence=identity.identity_confidence,
            authoritative_path=authoritative_path,
            inspection_openable_path=authoritative_path,
            reason_code="project_code_mismatch",
            safe_evidence=_safe_evidence(
                path_authority=path_authority,
                authoritative_path=authoritative_path,
                inspection_openable_path=authoritative_path,
                content_sha256=content_sha256,
                identity_mismatch=identity_mismatch,
            ),
        )

    sidecar = _read_evidence_sidecar(authoritative_path)
    sidecar_page_kind = _sidecar_page_kind(sidecar)
    if sidecar_page_kind == "shared_official_page":
        accepted = _shared_official_page_sidecar_is_accepted(
            sidecar,
            content_sha256=content_sha256,
            identity_components=_logical_record_identity_components(identity),
        )
        if accepted and identity.identity_confidence == "verified":
            return EvidenceVerdict(
                status="shared_official_page",
                logical_record_identity=identity.logical_record_identity,
                identity_confidence=identity.identity_confidence,
                authoritative_path=authoritative_path,
                inspection_openable_path=authoritative_path,
                reason_code="shared_official_page_explicit",
                safe_evidence=_safe_evidence(
                    path_authority=path_authority,
                    authoritative_path=authoritative_path,
                    inspection_openable_path=authoritative_path,
                    content_sha256=content_sha256,
                    sidecar_metadata=_safe_sidecar_evidence(sidecar, page_kind="shared_official_page"),
                ),
            )
        return EvidenceVerdict(
            status="present_unverified",
            logical_record_identity=identity.logical_record_identity,
            identity_confidence=identity.identity_confidence,
            authoritative_path=authoritative_path,
            inspection_openable_path=authoritative_path,
            reason_code="shared_official_page_metadata_unaccepted",
            safe_evidence=_safe_evidence(
                path_authority=path_authority,
                authoritative_path=authoritative_path,
                inspection_openable_path=authoritative_path,
                content_sha256=content_sha256,
                sidecar_metadata=_unaccepted_sidecar_evidence("shared_official_page"),
            ),
        )
    if sidecar_page_kind == "invalid_shell":
        accepted = _invalid_shell_sidecar_is_accepted(
            sidecar,
            content_sha256=content_sha256,
            identity_components=_logical_record_identity_components(identity),
            record=data,
        )
        if accepted:
            return EvidenceVerdict(
                status="invalid_shell",
                logical_record_identity=identity.logical_record_identity,
                identity_confidence=identity.identity_confidence,
                authoritative_path=authoritative_path,
                inspection_openable_path=authoritative_path,
                reason_code="invalid_shell_sidecar_explicit",
                safe_evidence=_safe_evidence(
                    path_authority=path_authority,
                    authoritative_path=authoritative_path,
                    inspection_openable_path=authoritative_path,
                    content_sha256=content_sha256,
                    sidecar_metadata=_safe_sidecar_evidence(sidecar, page_kind="invalid_shell"),
                ),
            )
        return EvidenceVerdict(
            status="present_unverified",
            logical_record_identity=identity.logical_record_identity,
            identity_confidence=identity.identity_confidence,
            authoritative_path=authoritative_path,
            inspection_openable_path=authoritative_path,
            reason_code="invalid_shell_metadata_unaccepted",
            safe_evidence=_safe_evidence(
                path_authority=path_authority,
                authoritative_path=authoritative_path,
                inspection_openable_path=authoritative_path,
                content_sha256=content_sha256,
                sidecar_metadata=_unaccepted_sidecar_evidence("invalid_shell"),
            ),
        )

    if identity.identity_confidence == "verified":
        return EvidenceVerdict(
            status="verified",
            logical_record_identity=identity.logical_record_identity,
            identity_confidence=identity.identity_confidence,
            authoritative_path=authoritative_path,
            inspection_openable_path=authoritative_path,
            reason_code="identity_verified_artifact_present",
            safe_evidence=_safe_evidence(
                path_authority=path_authority,
                authoritative_path=authoritative_path,
                inspection_openable_path=authoritative_path,
                content_sha256=content_sha256,
            ),
        )

    return EvidenceVerdict(
        status="present_unverified",
        logical_record_identity=identity.logical_record_identity,
        identity_confidence=identity.identity_confidence,
        authoritative_path=authoritative_path,
        inspection_openable_path=authoritative_path,
        reason_code="identity_unresolved_artifact_present",
        safe_evidence=_safe_evidence(
            path_authority=path_authority,
            authoritative_path=authoritative_path,
            inspection_openable_path=authoritative_path,
            content_sha256=content_sha256,
        ),
    )


def declared_artifact_is_available(*, source_file: object = "", archive_path: object = "") -> bool:
    return resolve_declared_artifact_presence(
        source_file=source_file,
        archive_path=archive_path,
    ).available


def declared_artifact_is_missing(*, source_file: object = "", archive_path: object = "") -> bool:
    return resolve_declared_artifact_presence(
        source_file=source_file,
        archive_path=archive_path,
    ).missing
