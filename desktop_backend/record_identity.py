"""Pure identity and evidence selection contract for failed records."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

FAILED_RECORD_STATES = ("parse_failed", "postprocess_failed")


def is_failed_record_state(state: str) -> bool:
    return str(state or "").strip() in FAILED_RECORD_STATES


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _unique_tokens(tokens: Iterable[Any] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens or []:
        text = _coerce_text(token)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


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
) -> dict:
    return {
        "record_family": _coerce_text(record_family) or "listing",
        "original_source_file": _coerce_text(source_file),
        "source_url": _coerce_text(source_url),
        "project_code": _coerce_text(project_code),
        "project_name": _coerce_text(project_name),
        "exchange": _coerce_text(exchange),
        "listing_date": _coerce_text(listing_date),
        "candidate_tokens": _unique_tokens(candidate_tokens),
    }


def build_identity_anchor(*, record_state: str, source_identity: dict) -> str:
    identity = dict(source_identity or {})
    payload = {
        "record_state": _coerce_text(record_state),
        "record_family": _coerce_text(identity.get("record_family")) or "listing",
        "source_url": _coerce_text(identity.get("source_url")),
        "project_code": _coerce_text(identity.get("project_code")),
        "project_name": _coerce_text(identity.get("project_name")),
        "exchange": _coerce_text(identity.get("exchange")),
        "listing_date": _coerce_text(identity.get("listing_date")),
        "candidate_tokens": _unique_tokens(identity.get("candidate_tokens")),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _coerce_text(value)
        if text:
            return text
    return ""


def pick_reprocess_evidence_path(record: dict) -> str:
    data = dict(record or {})
    source_identity = data.get("source_identity")
    if isinstance(source_identity, str):
        try:
            source_identity = json.loads(source_identity)
        except Exception:
            source_identity = {}
    if not isinstance(source_identity, dict):
        source_identity = {}
    return _first_non_empty(
        source_identity.get("original_evidence_path"),
        data.get("original_evidence_path"),
        source_identity.get("original_source_file"),
        data.get("evidence_path"),
        data.get("archive_path"),
        data.get("source_file"),
    )
