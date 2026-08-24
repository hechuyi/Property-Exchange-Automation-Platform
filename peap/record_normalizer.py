from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from peap_core import AssembledRecordCandidate, CanonicalRecord

from .deal_amounts import apply_deal_price_amount_fields


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


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _object_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be an object")
    return dict(value)


def _optional_object_mapping(business_object: dict[str, Any], field_name: str) -> dict[str, Any]:
    if field_name not in business_object:
        return {}
    return _object_mapping(
        business_object[field_name],
        field_name=f"raw_business_object.{field_name}",
    )


def _resolve_record_family(*, business_object: dict[str, Any], business_identity: Mapping[str, Any]) -> str:
    record_family = str(
        business_object.get("record_family")
        or business_identity.get("record_family")
        or ""
    ).strip()
    if record_family:
        return record_family
    raise ValueError("assembled record missing record_family")


def normalize_assembled_record(assembled: AssembledRecordCandidate) -> CanonicalRecord:
    business_object = _object_mapping(
        assembled.raw_business_object,
        field_name="raw_business_object",
    )
    first_page = assembled.page_results[0]
    source_identity = _optional_object_mapping(business_object, "source_identity")
    business_identity = _optional_object_mapping(business_object, "business_identity")
    business_fields = _optional_object_mapping(business_object, "business_fields")
    export_extras = _optional_object_mapping(business_object, "export_extras")
    record_family = _resolve_record_family(business_object=business_object, business_identity=business_identity)
    source_ids = tuple(
        str(item or "").strip()
        for item in (source_identity.get("source_ids") or assembled.source_ids)
        if str(item or "").strip()
    )
    source_id = str(
        source_identity.get("source_id")
        or (source_ids[0] if source_ids else "")
        or ""
    ).strip()
    business_id = str(
        business_identity.get("business_id")
        or source_identity.get("business_id")
        or business_object.get("business_id")
        or ""
    ).strip()
    project_code = str(
        business_identity.get("project_code")
        or business_object.get("project_code")
        or ""
    ).strip()
    project_name = str(
        business_identity.get("project_name")
        or business_object.get("project_name")
        or ""
    ).strip()
    project_type = _first_non_empty(
        business_identity.get("business_type"),
        business_identity.get("project_type"),
        business_fields.get("business_type"),
        business_fields.get("project_type"),
        business_object.get("business_type"),
        business_object.get("project_type"),
    )
    business_type = project_type
    canonical_fields = {
        "project_code": project_code,
        "project_name": project_name,
        "project_type": project_type,
        "business_type": business_type,
        "status": str(business_fields.get("status") or business_object.get("status") or "").strip(),
        "start_date": _normalize_date(
            business_fields.get("start_date")
            or business_object.get("start_date")
            or business_object.get("listing_date")
        ),
        "price": business_fields.get("price", business_object.get("price")),
        "seller": str(business_fields.get("seller") or business_object.get("seller") or "").strip(),
        "source_type": str(business_fields.get("source_type") or business_object.get("source_type") or "").strip(),
        "group_name": str(business_fields.get("group_name") or business_object.get("group_name") or "").strip(),
    }
    if record_family == "deal":
        deal_date = _normalize_date(
            business_fields.get("deal_date")
            or business_object.get("deal_date")
        )
        collection_date = _normalize_date(
            business_fields.get("collection_date")
            or business_object.get("collection_date")
        )
        raw_deal_date_basis = str(
            business_fields.get("deal_date_basis")
            or business_object.get("deal_date_basis")
            or ""
        ).strip()
        raw_deal_date_is_imputed = business_fields.get("deal_date_is_imputed")
        if raw_deal_date_is_imputed is None:
            raw_deal_date_is_imputed = business_object.get("deal_date_is_imputed")
        if isinstance(raw_deal_date_is_imputed, bool):
            deal_date_is_imputed = raw_deal_date_is_imputed
        else:
            bool_text = str(raw_deal_date_is_imputed or "").strip().lower()
            deal_date_is_imputed = bool_text in {"true", "1", "yes", "y"}
        if deal_date and deal_date_is_imputed and raw_deal_date_basis in {"", "collection_date"}:
            collection_date = collection_date or deal_date
            deal_date = ""
            raw_deal_date_basis = "collection_date"
        canonical_fields.update(
            {
                "deal_date": deal_date,
                "deal_date_basis": raw_deal_date_basis or ("collection_date" if deal_date_is_imputed else "deal_date"),
                "deal_date_is_imputed": bool(deal_date_is_imputed),
                "deal_price": business_fields.get("deal_price", business_object.get("deal_price", "")),
                "deal_price_unit_hint": business_fields.get("deal_price_unit_hint", business_object.get("deal_price_unit_hint", "")),
                "valuation": business_fields.get("valuation", business_object.get("valuation", "")),
                "reserve_price": business_fields.get("reserve_price", business_object.get("reserve_price", "")),
            }
        )
        canonical_fields = apply_deal_price_amount_fields(canonical_fields)
        if collection_date:
            canonical_fields["collection_date"] = collection_date
    normalized_source_identity = {
        **source_identity,
        "record_family": record_family,
        "source_ids": source_ids,
        "assembly_id": assembled.assembly_id,
    }
    if source_id:
        normalized_source_identity["source_id"] = source_id
    if business_id:
        normalized_source_identity["business_id"] = business_id
    normalized_business_identity = {
        "record_family": record_family,
        "project_code": canonical_fields["project_code"],
        "project_name": canonical_fields["project_name"],
        "project_type": canonical_fields["project_type"],
        "business_type": canonical_fields["business_type"],
        "entity_keys": assembled.entity_keys,
    }
    if business_id:
        normalized_business_identity["business_id"] = business_id
    return CanonicalRecord(
        record_id=assembled.assembly_id,
        record_family=record_family,
        source_identity=normalized_source_identity,
        business_identity=normalized_business_identity,
        canonical_fields=canonical_fields,
        export_extras=export_extras,
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
