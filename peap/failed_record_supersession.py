"""Detect failed-record shells superseded by canonical records."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any, Iterable

from peap_core.business_catalog import resolve_business_descriptor
from peap_core.family_catalog import get_family_descriptor
from peap_core.pipeline_state_contracts import RecordState
from peap_core.record_identity import FAILED_RECORD_STATES, pick_reprocess_evidence_path
from peap_core.source_catalog import canonical_source_code

from .artifact_truth import resolve_declared_artifact_presence

SUPERSEDABLE_RECORD_STATES = (*FAILED_RECORD_STATES, "pending_review", "field_missing")
_SUPERSEDING_RECORD_STATES = frozenset(
    {
        RecordState.READY.value,
        RecordState.PENDING_MAPPING.value,
        RecordState.MAPPING_CONFLICT.value,
        RecordState.CONFLICT.value,
    }
)
_IDENTITY_TOKEN_KINDS = frozenset({"project_code", "project_id", "page_url"})


def source_identity_dict(record: dict[str, Any]) -> dict[str, Any]:
    if "source_identity_json" in record:
        source_identity_json = record.get("source_identity_json")
        if isinstance(source_identity_json, str):
            if source_identity_json.strip():
                source_identity = json.loads(source_identity_json)
                if not isinstance(source_identity, Mapping):
                    raise TypeError("source_identity_json must decode to an object")
                return dict(source_identity)
        elif source_identity_json is not None:
            if not isinstance(source_identity_json, Mapping):
                raise TypeError("source_identity_json must be an object")
            return dict(source_identity_json)

    source_identity = record.get("source_identity")
    if source_identity is None:
        return {}
    if not isinstance(source_identity, Mapping):
        raise TypeError("source_identity must be an object")
    return dict(source_identity)


def reprocess_source_path(record: dict[str, Any]) -> str:
    state = str(record.get("state") or "").strip()
    if state in FAILED_RECORD_STATES:
        return str(pick_reprocess_evidence_path({**record, "source_identity": source_identity_dict(record)}) or "").strip()
    artifact = resolve_declared_artifact_presence(
        source_file=record.get("source_file"),
        archive_path=record.get("archive_path"),
    )
    return artifact.authoritative_path


def normalized_path_key(path: str) -> str:
    value = str(path or "").strip()
    if not value:
        return ""
    return os.path.normcase(os.path.abspath(value))


def record_source_path_keys(record: dict[str, Any]) -> set[str]:
    source_identity = source_identity_dict(record)
    candidates = [
        source_identity.get("original_evidence_path"),
        record.get("original_evidence_path"),
        source_identity.get("original_source_file"),
        record.get("evidence_path"),
        record.get("archive_path"),
        record.get("source_file"),
    ]
    return {key for key in (normalized_path_key(str(value or "")) for value in candidates) if key}


def _record_family_key(record: dict[str, Any]) -> str:
    source_identity = source_identity_dict(record)
    value = str(record.get("record_family") or source_identity.get("record_family") or "").strip()
    if not value:
        return ""
    try:
        return str(get_family_descriptor(value).family_id or "").strip().lower()
    except KeyError:
        return value.lower()


def _canonical_source_key(value: Any) -> str:
    return str(canonical_source_code(value, allow_substring=True) or "").strip().lower()


def _record_source_keys(record: dict[str, Any]) -> set[str]:
    source_identity = source_identity_dict(record)
    values = (
        record.get("source_id"),
        source_identity.get("source_id"),
        record.get("exchange"),
        source_identity.get("exchange"),
    )
    return {key for value in values if (key := _canonical_source_key(value))}


def _canonical_business_key(value: Any, *, record_family: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        descriptor = resolve_business_descriptor(text, family_id=record_family)
    except KeyError:
        descriptor = None
    return str(descriptor.business_id if descriptor is not None else text).strip().lower()


def _record_business_keys(record: dict[str, Any]) -> set[str]:
    source_identity = source_identity_dict(record)
    record_family = _record_family_key(record)
    values = (
        record.get("business_id"),
        source_identity.get("business_id"),
        source_identity.get("business_id_hint"),
    )
    return {
        key
        for value in values
        if (key := _canonical_business_key(value, record_family=record_family))
    }


def _normalized_identity_token(raw_token: Any) -> str:
    kind, separator, value = str(raw_token or "").strip().partition(":")
    normalized_kind = kind.strip().lower()
    normalized_value = value.strip()
    if not separator or normalized_kind not in _IDENTITY_TOKEN_KINDS or not normalized_value:
        return ""
    if normalized_kind in {"project_code", "project_id"}:
        normalized_value = normalized_value.upper()
    return f"{normalized_kind}:{normalized_value}"


def _record_project_code(record: dict[str, Any]) -> str:
    source_identity = source_identity_dict(record)
    return str(record.get("project_code") or source_identity.get("project_code") or "").strip().upper()


def _record_candidate_tokens(record: dict[str, Any]) -> set[str]:
    source_identity = source_identity_dict(record)
    raw_tokens = source_identity.get("candidate_tokens")
    if raw_tokens is None:
        raw_tokens = record.get("candidate_tokens")
    if isinstance(raw_tokens, str):
        candidate_values: Iterable[Any] = (raw_tokens,)
    elif isinstance(raw_tokens, Iterable):
        candidate_values = raw_tokens
    else:
        candidate_values = ()
    tokens = {
        token
        for raw_token in candidate_values
        if (token := _normalized_identity_token(raw_token))
    }
    for container in (record, source_identity):
        for kind, fields in (
            ("project_code", ("project_code",)),
            ("project_id", ("project_id", "content_id", "contentId")),
            ("page_url", ("source_url", "page_url", "detail_url")),
        ):
            for field in fields:
                token = _normalized_identity_token(f"{kind}:{container.get(field) or ''}")
                if token:
                    tokens.add(token)
    return tokens


def _records_share_scope(
    record: dict[str, Any],
    candidate: dict[str, Any],
    *,
    require_source: bool,
) -> bool:
    if _record_family_key(record) != _record_family_key(candidate):
        return False

    record_sources = _record_source_keys(record)
    candidate_sources = _record_source_keys(candidate)
    if len(record_sources) > 1 or len(candidate_sources) > 1:
        return False
    if require_source and (not record_sources or not candidate_sources):
        return False
    if record_sources and candidate_sources and record_sources != candidate_sources:
        return False

    record_businesses = _record_business_keys(record)
    candidate_businesses = _record_business_keys(candidate)
    if len(record_businesses) > 1 or len(candidate_businesses) > 1:
        return False
    return not (
        record_businesses
        and candidate_businesses
        and record_businesses != candidate_businesses
    )


def _append_index_record(
    section: dict[str, list[dict[str, Any]]],
    key: str,
    record: dict[str, Any],
) -> None:
    section.setdefault(key, []).append(record)


def build_superseding_record_index(
    records: Iterable[dict[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    index: dict[str, dict[str, list[dict[str, Any]]]] = {
        "by_project_and_path": {},
        "by_candidate_and_path": {},
        "by_path": {},
    }
    for record in records:
        state = str(record.get("state") or "").strip()
        if state not in _SUPERSEDING_RECORD_STATES:
            continue
        record_family = _record_family_key(record)
        project_code = _record_project_code(record)
        candidate_tokens = _record_candidate_tokens(record) - {
            f"project_code:{project_code}" if project_code else ""
        }
        for path_key in record_source_path_keys(record):
            if project_code:
                _append_index_record(
                    index["by_project_and_path"],
                    f"{record_family}\n{project_code}\n{path_key}",
                    record,
                )
            for token in candidate_tokens:
                _append_index_record(
                    index["by_candidate_and_path"],
                    f"{record_family}\n{token}\n{path_key}",
                    record,
                )
            _append_index_record(index["by_path"], f"{record_family}\n{path_key}", record)
    return index


def find_superseding_record(
    record: dict[str, Any],
    superseding_index: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any] | None:
    if str(record.get("state") or "").strip() not in SUPERSEDABLE_RECORD_STATES:
        return None
    project_code = _record_project_code(record)
    record_family = _record_family_key(record)
    path_keys = record_source_path_keys(record)
    if project_code:
        for path_key in path_keys:
            candidates = superseding_index.get("by_project_and_path", {}).get(
                f"{record_family}\n{project_code}\n{path_key}"
            ) or []
            for superseding in candidates:
                if (
                    str(superseding.get("record_id") or "") != str(record.get("record_id") or "")
                    and _records_share_scope(record, superseding, require_source=False)
                ):
                    return superseding
        return None

    candidate_tokens = _record_candidate_tokens(record)
    if candidate_tokens:
        for token in candidate_tokens:
            for path_key in path_keys:
                candidates = superseding_index.get("by_candidate_and_path", {}).get(
                    f"{record_family}\n{token}\n{path_key}"
                ) or []
                for superseding in candidates:
                    if (
                        str(superseding.get("record_id") or "") != str(record.get("record_id") or "")
                        and _records_share_scope(record, superseding, require_source=False)
                    ):
                        return superseding
        return None

    for path_key in path_keys:
        candidates = superseding_index.get("by_path", {}).get(f"{record_family}\n{path_key}") or []
        for superseding in candidates:
            if (
                str(superseding.get("record_id") or "") != str(record.get("record_id") or "")
                and _records_share_scope(record, superseding, require_source=True)
            ):
                return superseding
    return None


def is_superseded_failed_record(
    record: dict[str, Any],
    superseding_index: dict[str, dict[str, list[dict[str, Any]]]],
) -> bool:
    return find_superseding_record(record, superseding_index) is not None


__all__ = [
    "build_superseding_record_index",
    "find_superseding_record",
    "is_superseded_failed_record",
    "record_source_path_keys",
    "reprocess_source_path",
    "source_identity_dict",
    "SUPERSEDABLE_RECORD_STATES",
]
