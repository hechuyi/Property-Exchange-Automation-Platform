from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from peap_core import CanonicalRecord
from peap.streaming_models import PostProcessFinding
from peap.streaming_postprocess import apply_mapping_entries


@dataclass(frozen=True)
class PolicyPatch:
    field: str
    old_value: Any
    new_value: Any
    reason: str


def _canonical_to_streaming_payload(canonical: CanonicalRecord) -> dict[str, Any]:
    fields = dict(canonical.canonical_fields)
    payload = {
        "项目编号": str(fields.get("project_code") or ""),
        "项目名称": str(fields.get("project_name") or ""),
        "转让方": str(fields.get("seller") or ""),
        "隶属集团": str(fields.get("group_name") or ""),
        "类型": str(fields.get("source_type") or ""),
    }
    return {key: value for key, value in payload.items() if value not in (None, "")}


def apply_policies_to_canonical_record(
    canonical: CanonicalRecord,
    *,
    mapping_entries: Iterable[dict[str, Any]] | None = None,
) -> tuple[CanonicalRecord, tuple[dict[str, Any], ...], tuple[PostProcessFinding, ...]]:
    payload = _canonical_to_streaming_payload(canonical)
    resolved, findings = apply_mapping_entries(payload, mapping_entries=mapping_entries)

    patches: list[dict[str, Any]] = []
    diagnostics: list[PostProcessFinding] = list(findings)
    updated_fields = dict(canonical.canonical_fields)

    for compat_key, canonical_key in (("隶属集团", "group_name"), ("类型", "source_type")):
        old_value = updated_fields.get(canonical_key)
        new_value = resolved.get(compat_key)

        if new_value in (None, ""):
            continue
        if old_value == new_value:
            continue

        if old_value not in (None, ""):
            confidence = canonical.field_provenance.get(canonical_key, {}).get("confidence")
            if confidence not in (None, "") and float(confidence) >= 1.0:
                diagnostics.append(
                    PostProcessFinding(
                        severity="warn",
                        type="policy_conflict",
                        message=f"refused overwrite for {canonical_key}",
                        evidence={"field": canonical_key, "old_value": old_value, "new_value": new_value},
                    )
                )
                continue

        updated_fields[canonical_key] = new_value
        patches.append(
            {
                "field": canonical_key,
                "old_value": old_value,
                "new_value": new_value,
                "reason": "mapping rule applied",
            }
        )

    updated = CanonicalRecord(
        record_id=canonical.record_id,
        record_family=canonical.record_family,
        source_identity=canonical.source_identity,
        business_identity=canonical.business_identity,
        canonical_fields=updated_fields,
        field_provenance=canonical.field_provenance,
        diagnostics=canonical.diagnostics,
        normalizer_version=canonical.normalizer_version,
        policy_state=canonical.policy_state,
    )
    return updated, tuple(patches), tuple(diagnostics)


__all__ = ["PolicyPatch", "apply_policies_to_canonical_record"]
