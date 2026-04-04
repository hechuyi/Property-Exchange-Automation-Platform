from __future__ import annotations

from typing import Any

from peap_core import AssembledRecordCandidate, CanonicalRecord


def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("-", "/")
    parts = text.split("/")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        year, month, day = parts
        return f"{int(year):04d}/{int(month):02d}/{int(day):02d}"
    return text


def normalize_assembled_record(assembled: AssembledRecordCandidate) -> CanonicalRecord:
    business_object = dict(assembled.raw_business_object or {})
    first_page = assembled.page_results[0]
    canonical_fields = {
        "project_code": str(business_object.get("project_code") or "").strip(),
        "project_name": str(business_object.get("project_name") or "").strip(),
        "project_type": str(business_object.get("project_type") or "").strip(),
        "status": str(business_object.get("status") or "").strip(),
        "start_date": _normalize_date(business_object.get("start_date") or business_object.get("listing_date")),
        "price": business_object.get("price"),
        "seller": str(business_object.get("seller") or "").strip(),
        "source_type": str(business_object.get("source_type") or "").strip(),
        "group_name": str(business_object.get("group_name") or "").strip(),
    }
    return CanonicalRecord(
        record_id=assembled.assembly_id,
        record_family="listing",
        source_identity={
            "source_ids": assembled.source_ids,
            "assembly_id": assembled.assembly_id,
        },
        business_identity={
            "project_code": canonical_fields["project_code"],
            "entity_keys": assembled.entity_keys,
        },
        canonical_fields=canonical_fields,
        field_provenance={
            "project_name": {
                "snapshot_id": first_page.snapshot_id,
                "page_kind": first_page.page_identity.get("page_kind"),
            }
        },
        diagnostics=(),
        normalizer_version="record_normalizer/v1",
        policy_state={},
    )


__all__ = ["normalize_assembled_record"]
