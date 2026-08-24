"""Canonical field_missing DTO helpers shared by storage, export, and backend views."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


def normalize_missing_fields(raw_fields: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    if isinstance(raw_fields, dict):
        iterable: Iterable[Any] = [raw_fields]
    elif isinstance(raw_fields, list | tuple):
        iterable = raw_fields
    else:
        iterable = []

    for item in iterable:
        if isinstance(item, dict):
            export_field = str(item.get("export_field") or "").strip()
            canonical_field = str(item.get("canonical_field") or "").strip()
            field = str(item.get("field") or export_field or canonical_field or "").strip()
            kind = str(item.get("kind") or ("export" if export_field else "canonical")).strip()
            message = str(item.get("message") or "").strip()
        else:
            field = str(item or "").strip()
            if not field:
                continue
            export_field = field
            canonical_field = ""
            kind = "export"
            message = ""
        if not field and not export_field and not canonical_field:
            continue
        if not message:
            if kind == "canonical":
                message = f"canonical field {field or canonical_field} is required"
            else:
                message = f"export field {export_field or field} is required"
        entry = {
            "kind": kind or "export",
            "field": field or export_field or canonical_field,
            "canonical_field": canonical_field,
            "export_field": export_field,
            "message": message,
        }
        key = (
            entry["kind"],
            entry["field"],
            entry["canonical_field"],
            entry["export_field"],
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(entry)
    return normalized


def missing_fields_hash(missing_fields: Any) -> str:
    payload = normalize_missing_fields(missing_fields)
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def build_field_missing_ack_payload(
    *,
    previous_state: str,
    evidence_source: str,
    missing_fields: Any,
    revision_id: int | None = None,
) -> dict[str, Any]:
    normalized_fields = normalize_missing_fields(missing_fields)
    payload: dict[str, Any] = {
        "field_missing": {
            "previous_state": str(previous_state or ""),
            "evidence_source": str(evidence_source or ""),
            "missing_fields": normalized_fields,
            "missing_fields_hash": missing_fields_hash(normalized_fields),
        }
    }
    if revision_id is not None:
        payload["field_missing"]["revision_id"] = int(revision_id)
    return payload


__all__ = [
    "build_field_missing_ack_payload",
    "missing_fields_hash",
    "normalize_missing_fields",
]
