"""Immutable business metadata catalog."""

from __future__ import annotations

from dataclasses import dataclass

from peap_core.family_catalog import (
    FamilyDescriptor,
    get_family_descriptor,
    list_family_descriptors,
)


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_token(value: object) -> str:
    return _normalize_text(value).lower()


@dataclass(frozen=True)
class BusinessDescriptor:
    business_id: str
    family_id: str
    canonical_label: str
    aliases: tuple[str, ...]
    project_type_label: str = ""


def validate_business_descriptors(
    descriptors: tuple[BusinessDescriptor, ...],
) -> tuple[BusinessDescriptor, ...]:
    seen_business_ids: dict[str, BusinessDescriptor] = {}
    seen_aliases: dict[str, BusinessDescriptor] = {}
    normalized_descriptors: list[BusinessDescriptor] = []

    for descriptor in descriptors:
        normalized_business_id = _normalize_token(descriptor.business_id)
        if not normalized_business_id:
            raise ValueError("business_id must be non-empty")
        if normalized_business_id in seen_business_ids:
            previous = seen_business_ids[normalized_business_id]
            raise ValueError(
                f"duplicate business_id {descriptor.business_id!r} conflicts with "
                f"{previous.family_id}:{previous.business_id}"
            )
        seen_business_ids[normalized_business_id] = descriptor

        normalized_aliases: set[str] = set()
        for raw_alias in (
            descriptor.business_id,
            descriptor.canonical_label,
            *descriptor.aliases,
        ):
            normalized_alias = _normalize_token(raw_alias)
            if not normalized_alias or normalized_alias in normalized_aliases:
                continue
            normalized_aliases.add(normalized_alias)
            previous = seen_aliases.get(normalized_alias)
            if previous is not None:
                raise ValueError(
                    f"duplicate alias {raw_alias!r} conflicts with "
                    f"{previous.family_id}:{previous.business_id}"
                )
            seen_aliases[normalized_alias] = descriptor

        normalized_descriptors.append(descriptor)

    return tuple(normalized_descriptors)


def validate_family_business_alignment(
    descriptors: tuple[BusinessDescriptor, ...],
    *,
    families: tuple[FamilyDescriptor, ...] | None = None,
) -> tuple[BusinessDescriptor, ...]:
    family_descriptors = tuple(list_family_descriptors()) if families is None else families
    business_ids_by_family: dict[str, tuple[str, ...]] = {}
    for family in family_descriptors:
        business_ids = tuple(
            descriptor.business_id
            for descriptor in descriptors
            if descriptor.family_id == family.family_id
        )
        business_ids_by_family[family.family_id] = business_ids
        if family.business_ids != business_ids:
            raise ValueError(
                f"family {family.family_id!r} business_ids {family.business_ids!r} "
                f"do not match descriptor business_ids {business_ids!r}"
            )
    for descriptor in descriptors:
        if descriptor.family_id not in business_ids_by_family:
            raise ValueError(
                f"business {descriptor.business_id!r} references unknown family {descriptor.family_id!r}"
            )
    return descriptors


_BUSINESS_DESCRIPTORS: tuple[BusinessDescriptor, ...] = validate_family_business_alignment(
    validate_business_descriptors(
        (
            BusinessDescriptor(
                business_id="physical_asset",
                family_id="listing",
                canonical_label="实物资产",
                aliases=("physical asset", "Physical Asset", "physical_asset", "挂牌实物资产"),
                project_type_label="实物资产",
            ),
            BusinessDescriptor(
                business_id="equity_transfer",
                family_id="listing",
                canonical_label="股权转让",
                aliases=("equity transfer", "Equity Transfer", "equity_transfer", "挂牌产权交易"),
                project_type_label="股权转让",
            ),
            BusinessDescriptor(
                business_id="capital_increase",
                family_id="listing",
                canonical_label="增资扩股",
                aliases=("capital increase", "Capital Increase", "capital_increase", "挂牌增资"),
                project_type_label="增资扩股",
            ),
            BusinessDescriptor(
                business_id="pre_disclosure",
                family_id="listing",
                canonical_label="预披露",
                aliases=("pre disclosure", "Pre Disclosure", "pre_disclosure", "挂牌预披露"),
                project_type_label="预披露",
            ),
            BusinessDescriptor(
                business_id="deal_physical_asset",
                family_id="deal",
                canonical_label="实物资产成交",
                aliases=("deal physical asset", "Deal Physical Asset", "deal_physical_asset", "成交实物资产"),
                project_type_label="实物资产",
            ),
            BusinessDescriptor(
                business_id="deal_equity_transfer",
                family_id="deal",
                canonical_label="股权转让成交",
                aliases=("deal equity transfer", "Deal Equity Transfer", "deal_equity_transfer", "成交股权转让"),
                project_type_label="股权转让",
            ),
            BusinessDescriptor(
                business_id="deal_capital_increase",
                family_id="deal",
                canonical_label="增资扩股成交",
                aliases=("deal capital increase", "Deal Capital Increase", "deal_capital_increase", "成交增资扩股"),
                project_type_label="增资扩股",
            ),
        )
    )
)

_BUSINESS_BY_ID = {descriptor.business_id: descriptor for descriptor in _BUSINESS_DESCRIPTORS}
_ALIAS_INDEX = {
    token: descriptor
    for descriptor in _BUSINESS_DESCRIPTORS
    for token in (
        _normalize_token(descriptor.business_id),
        _normalize_token(descriptor.canonical_label),
        *(_normalize_token(alias) for alias in descriptor.aliases),
    )
    if token
}


def _validate_family_membership(descriptor: BusinessDescriptor, family_id: str | None) -> None:
    if family_id is None:
        return
    normalized_family_id = _normalize_text(family_id)
    family_descriptor = get_family_descriptor(normalized_family_id)
    if descriptor.family_id != family_descriptor.family_id:
        raise KeyError(normalized_family_id)


def _family_project_type_aliases(family_id: str) -> dict[str, BusinessDescriptor]:
    aliases: dict[str, BusinessDescriptor] = {}
    for descriptor in _BUSINESS_DESCRIPTORS:
        if descriptor.family_id != family_id:
            continue
        for raw_value in (descriptor.project_type_label,):
            normalized = _normalize_token(raw_value)
            if normalized:
                aliases[normalized] = descriptor
    return aliases


_PROJECT_TYPE_INDEX = {
    family.family_id: _family_project_type_aliases(family.family_id)
    for family in list_family_descriptors()
}


def list_business_descriptors(family_id: str | None = None) -> list[BusinessDescriptor]:
    normalized_family_id = _normalize_token(family_id)
    results: list[BusinessDescriptor] = []
    for descriptor in _BUSINESS_DESCRIPTORS:
        if normalized_family_id and _normalize_token(descriptor.family_id) != normalized_family_id:
            continue
        results.append(descriptor)
    return results


def resolve_business_descriptor(
    raw_value: object,
    *,
    family_id: str | None = None,
) -> BusinessDescriptor | None:
    normalized = _normalize_token(raw_value)
    if not normalized:
        return None
    descriptor = _ALIAS_INDEX.get(normalized)
    if descriptor is None:
        return None
    _validate_family_membership(descriptor, family_id)
    return descriptor


def get_business_descriptor(
    business_id: str,
    *,
    family_id: str | None = None,
) -> BusinessDescriptor:
    normalized = _normalize_text(business_id)
    descriptor = _BUSINESS_BY_ID.get(normalized)
    if descriptor is None:
        descriptor = resolve_business_descriptor(normalized, family_id=family_id)
    else:
        _validate_family_membership(descriptor, family_id)
    if descriptor is None:
        raise KeyError(normalized)
    return descriptor


def resolve_business_descriptor_by_project_type(
    raw_value: object,
    *,
    family_id: str,
) -> BusinessDescriptor | None:
    normalized_family_id = get_family_descriptor(family_id).family_id
    normalized = _normalize_token(raw_value)
    if not normalized:
        return None
    return _PROJECT_TYPE_INDEX.get(normalized_family_id, {}).get(normalized)


def project_type_label_for_business(
    business_id: str,
    *,
    family_id: str | None = None,
) -> str:
    descriptor = get_business_descriptor(business_id, family_id=family_id)
    return _normalize_text(descriptor.project_type_label)


__all__ = [
    "BusinessDescriptor",
    "get_business_descriptor",
    "list_business_descriptors",
    "project_type_label_for_business",
    "resolve_business_descriptor",
    "resolve_business_descriptor_by_project_type",
    "validate_business_descriptors",
    "validate_family_business_alignment",
]
