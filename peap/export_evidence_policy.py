"""Export acceptance policy for artifact evidence verdicts."""

from __future__ import annotations

from typing import Mapping


def _verdict_value(verdict: object, key: str, default: object = "") -> object:
    if isinstance(verdict, Mapping):
        return verdict.get(key, default)
    return getattr(verdict, key, default)


def export_evidence_verdict_accepted(verdict: object) -> bool:
    """Return whether an evidence-only verdict is acceptable for export."""

    status = str(_verdict_value(verdict, "status") or "").strip()
    if status == "verified":
        return True
    if status != "shared_official_page":
        return False

    safe_evidence = _verdict_value(verdict, "safe_evidence", {})
    if not isinstance(safe_evidence, Mapping):
        raise TypeError("safe_evidence must be a mapping")
    return str(safe_evidence.get("page_kind") or "").strip() == "shared_official_page"
