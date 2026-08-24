"""Pure identity and evidence selection contract for failed records."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping

from peap_core.family_catalog import get_family_descriptor

FAILED_RECORD_STATES = ("parse_failed", "postprocess_failed")
IdentityConfidence = Literal["verified", "unresolved"]
_LEGACY_SOURCE_SHA1_RE = re.compile(r"^source:[0-9a-f]{40}$", re.IGNORECASE)


@dataclass(frozen=True)
class LogicalRecordIdentity:
    logical_record_identity: str
    identity_confidence: IdentityConfidence
    components: Mapping[str, str]


def is_failed_record_state(state: str) -> bool:
    return str(state or "").strip() in FAILED_RECORD_STATES


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _optional_text(value: Any, *, field_name: str) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value.strip()


def _validated_record_family(value: Any) -> str:
    family = _coerce_text(value)
    if not family:
        raise ValueError("record_family is required")
    try:
        return get_family_descriptor(family).family_id
    except KeyError as exc:
        raise ValueError(f"unknown record_family: {family}") from exc


def _unique_tokens(tokens: Iterable[Any] | None) -> list[str]:
    if isinstance(tokens, (str, bytes)):
        raise TypeError("candidate_tokens must be an iterable of strings, not a string")
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens or []:
        if not isinstance(token, str):
            raise TypeError("candidate_tokens elements must be strings")
        text = token.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _json_object(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        if not value.strip():
            return {}
        value = json.loads(value)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError("source_identity_json must decode to an object")


def _record_object(record: Mapping[str, Any] | None) -> dict:
    if record is None:
        return {}
    if not isinstance(record, MappingABC):
        raise TypeError("record must be an object")
    return dict(record)


def _optional_record_family(value: Any) -> str:
    family = _coerce_text(value)
    if not family:
        return ""
    try:
        return get_family_descriptor(family).family_id
    except KeyError:
        return ""


def _is_legacy_source_sha1_anchor(value: Any) -> bool:
    return bool(_LEGACY_SOURCE_SHA1_RE.fullmatch(_coerce_text(value)))


def _logical_identity_digest(components: Mapping[str, str]) -> str:
    serialized = json.dumps(
        dict(components),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "logical:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_source_identity_payload(
    *,
    record_family: str,
    source_file: str,
    source_url: str = "",
    project_code: str = "",
    project_name: str = "",
    exchange: str = "",
    listing_date: str = "",
    candidate_tokens: list[str] | None = None,
    business_id_hint: str = "",
    business_label_hint: str = "",
    project_type_fallback: str = "",
) -> dict:
    payload = {
        "record_family": _validated_record_family(record_family),
        "original_source_file": _coerce_text(source_file),
        "source_url": _coerce_text(source_url),
        "project_code": _coerce_text(project_code),
        "project_name": _coerce_text(project_name),
        "exchange": _coerce_text(exchange),
        "listing_date": _coerce_text(listing_date),
        "candidate_tokens": _unique_tokens(candidate_tokens),
    }
    normalized_business_id_hint = _coerce_text(business_id_hint)
    normalized_business_label_hint = _coerce_text(business_label_hint)
    normalized_project_type_fallback = _coerce_text(project_type_fallback)
    if normalized_business_id_hint:
        payload["business_id_hint"] = normalized_business_id_hint
    if normalized_business_label_hint:
        payload["business_label_hint"] = normalized_business_label_hint
    if normalized_project_type_fallback:
        payload["project_type_fallback"] = normalized_project_type_fallback
    return payload


def build_identity_anchor(*, record_state: str, source_identity: dict) -> str:
    if not isinstance(source_identity, MappingABC):
        raise TypeError("source_identity must be an object")
    identity = dict(source_identity)
    payload = {
        "record_state": _coerce_text(record_state),
        "record_family": _validated_record_family(identity.get("record_family")),
        "source_url": _coerce_text(identity.get("source_url")),
        "project_code": _coerce_text(identity.get("project_code")),
        "project_name": _coerce_text(identity.get("project_name")),
        "exchange": _coerce_text(identity.get("exchange")),
        "listing_date": _coerce_text(identity.get("listing_date")),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()


def resolve_logical_record_identity(
    record: Mapping[str, Any] | None = None,
    **overrides: Any,
) -> LogicalRecordIdentity:
    data = _record_object(record)
    data.update(overrides)
    raw_source_identity_json = data.get("source_identity_json")
    if isinstance(raw_source_identity_json, str) and raw_source_identity_json.strip():
        source_identity = _json_object(raw_source_identity_json)
    elif raw_source_identity_json not in (None, ""):
        source_identity = _json_object(raw_source_identity_json)
    else:
        source_identity = _json_object(data.get("source_identity"))

    record_family = _optional_record_family(
        _first_non_empty(
            _optional_text(data.get("record_family"), field_name="record_family"),
            _optional_text(source_identity.get("record_family"), field_name="source_identity.record_family"),
        )
    )
    business_id = _first_non_empty(
        _optional_text(data.get("business_id"), field_name="business_id"),
        _optional_text(source_identity.get("business_id"), field_name="source_identity.business_id"),
    )
    exchange = _first_non_empty(
        _optional_text(data.get("exchange"), field_name="exchange"),
        _optional_text(data.get("source_id"), field_name="source_id"),
        _optional_text(source_identity.get("exchange"), field_name="source_identity.exchange"),
        _optional_text(source_identity.get("source_id"), field_name="source_identity.source_id"),
    )
    project_code = _first_non_empty(
        _optional_text(data.get("project_code"), field_name="project_code"),
        _optional_text(source_identity.get("project_code"), field_name="source_identity.project_code"),
    )

    components = {
        "record_family": record_family,
        "business_id": business_id,
        "exchange": exchange,
        "project_code": project_code,
    }
    has_verified_components = all(components.values()) and not any(
        _is_legacy_source_sha1_anchor(value) for value in components.values()
    )
    confidence: IdentityConfidence = "verified" if has_verified_components else "unresolved"

    return LogicalRecordIdentity(
        logical_record_identity=_logical_identity_digest(components),
        identity_confidence=confidence,
        components=components,
    )


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _coerce_text(value)
        if text:
            return text
    return ""


def pick_reprocess_evidence_path(record: dict) -> str:
    data = _record_object(record)
    raw_source_identity_json = data.get("source_identity_json")
    if isinstance(raw_source_identity_json, str) and raw_source_identity_json.strip():
        source_identity = _json_object(raw_source_identity_json)
    elif raw_source_identity_json not in (None, ""):
        source_identity = _json_object(raw_source_identity_json)
    else:
        source_identity = _json_object(data.get("source_identity"))
    return _first_non_empty(
        source_identity.get("original_evidence_path"),
        data.get("original_evidence_path"),
        source_identity.get("original_source_file"),
        data.get("evidence_path"),
        data.get("archive_path"),
        data.get("source_file"),
    )


__all__ = [
    "FAILED_RECORD_STATES",
    "LogicalRecordIdentity",
    "build_identity_anchor",
    "build_source_identity_payload",
    "is_failed_record_state",
    "pick_reprocess_evidence_path",
    "resolve_logical_record_identity",
]
