"""Immutable family metadata catalog."""

from __future__ import annotations

from dataclasses import dataclass


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_token(value: object) -> str:
    return _normalize_text(value).lower()


@dataclass(frozen=True)
class FamilyDescriptor:
    family_id: str
    canonical_label: str
    aliases: tuple[str, ...]
    source_ids: tuple[str, ...]
    business_ids: tuple[str, ...]
    default_product_profile_id: str


@dataclass(frozen=True)
class SourceBusinessSupportDescriptor:
    family_id: str
    business_id: str
    source_ids: tuple[str, ...]


_LISTING_SOURCE_IDS = ("sse", "cbex", "tpre", "cquae", "shandong", "guangdong", "shenzhen")
_DEAL_SOURCE_IDS = ("sse", "cbex", "tpre", "cquae")


_FAMILY_DESCRIPTORS: tuple[FamilyDescriptor, ...] = (
    FamilyDescriptor(
        family_id="listing",
        canonical_label="Listing",
        aliases=("listing", "LISTING"),
        source_ids=_LISTING_SOURCE_IDS,
        business_ids=(
            "physical_asset",
            "equity_transfer",
            "capital_increase",
            "pre_disclosure",
        ),
        default_product_profile_id="desktop_listing",
    ),
    FamilyDescriptor(
        family_id="deal",
        canonical_label="Deal",
        aliases=("deal", "DEAL"),
        source_ids=_DEAL_SOURCE_IDS,
        business_ids=(
            "deal_physical_asset",
            "deal_equity_transfer",
            "deal_capital_increase",
        ),
        default_product_profile_id="desktop_deal",
    ),
)

_FAMILY_BY_ID = {descriptor.family_id: descriptor for descriptor in _FAMILY_DESCRIPTORS}
_ALIAS_INDEX = {
    token: descriptor
    for descriptor in _FAMILY_DESCRIPTORS
    for token in (
        _normalize_token(descriptor.family_id),
        _normalize_token(descriptor.canonical_label),
        *(_normalize_token(alias) for alias in descriptor.aliases),
    )
    if token
}


def _validate_declared_source_business_support(
    descriptors: tuple[SourceBusinessSupportDescriptor, ...],
) -> tuple[SourceBusinessSupportDescriptor, ...]:
    seen_keys: set[tuple[str, str]] = set()
    for descriptor in descriptors:
        family = _FAMILY_BY_ID.get(descriptor.family_id)
        if family is None:
            raise ValueError(f"unknown family_id {descriptor.family_id!r}")
        if descriptor.business_id not in family.business_ids:
            raise ValueError(
                f"family {family.family_id!r} does not include business {descriptor.business_id!r}"
            )
        unknown_sources = tuple(
            source_id for source_id in descriptor.source_ids if source_id not in family.source_ids
        )
        if unknown_sources:
            raise ValueError(
                f"family {family.family_id!r} does not include sources {unknown_sources!r}"
            )
        key = (descriptor.family_id, descriptor.business_id)
        if key in seen_keys:
            raise ValueError(f"duplicate source-business support descriptor {key!r}")
        seen_keys.add(key)
    return descriptors


_DECLARED_SOURCE_BUSINESS_SUPPORT: tuple[SourceBusinessSupportDescriptor, ...] = (
    SourceBusinessSupportDescriptor(
        family_id="listing",
        business_id="physical_asset",
        source_ids=("sse", "cbex", "tpre", "cquae"),
    ),
    SourceBusinessSupportDescriptor(
        family_id="listing",
        business_id="equity_transfer",
        source_ids=_LISTING_SOURCE_IDS,
    ),
    SourceBusinessSupportDescriptor(
        family_id="listing",
        business_id="capital_increase",
        source_ids=_LISTING_SOURCE_IDS,
    ),
    SourceBusinessSupportDescriptor(
        family_id="listing",
        business_id="pre_disclosure",
        source_ids=("sse", "cbex", "tpre", "cquae"),
    ),
    SourceBusinessSupportDescriptor(
        family_id="deal",
        business_id="deal_physical_asset",
        source_ids=("sse", "cbex"),
    ),
    SourceBusinessSupportDescriptor(
        family_id="deal",
        business_id="deal_equity_transfer",
        source_ids=_DEAL_SOURCE_IDS,
    ),
    SourceBusinessSupportDescriptor(
        family_id="deal",
        business_id="deal_capital_increase",
        source_ids=_DEAL_SOURCE_IDS,
    ),
)
_DECLARED_SOURCE_BUSINESS_SUPPORT = _validate_declared_source_business_support(
    _DECLARED_SOURCE_BUSINESS_SUPPORT
)


def list_family_descriptors() -> list[FamilyDescriptor]:
    return list(_FAMILY_DESCRIPTORS)


def list_declared_source_business_support(
    *,
    family_id: str | None = None,
) -> list[SourceBusinessSupportDescriptor]:
    normalized_family_id = _normalize_token(family_id)
    return [
        descriptor
        for descriptor in _DECLARED_SOURCE_BUSINESS_SUPPORT
        if not normalized_family_id or _normalize_token(descriptor.family_id) == normalized_family_id
    ]


def declared_source_ids_for_business(family_id: str, business_id: str) -> tuple[str, ...]:
    normalized_family_id = get_family_descriptor(family_id).family_id
    normalized_business_id = _normalize_text(business_id)
    for descriptor in _DECLARED_SOURCE_BUSINESS_SUPPORT:
        if (
            descriptor.family_id == normalized_family_id
            and descriptor.business_id == normalized_business_id
        ):
            return descriptor.source_ids
    raise KeyError((normalized_family_id, normalized_business_id))


def resolve_family_descriptor(raw_value: object) -> FamilyDescriptor | None:
    normalized = _normalize_token(raw_value)
    if not normalized:
        return None
    direct = _ALIAS_INDEX.get(normalized)
    if direct is not None:
        return direct
    return None


def get_family_descriptor(family_id: str) -> FamilyDescriptor:
    normalized = _normalize_text(family_id)
    descriptor = _FAMILY_BY_ID.get(normalized)
    if descriptor is None:
        descriptor = resolve_family_descriptor(normalized)
    if descriptor is None:
        raise KeyError(normalized)
    return descriptor


__all__ = [
    "FamilyDescriptor",
    "SourceBusinessSupportDescriptor",
    "declared_source_ids_for_business",
    "get_family_descriptor",
    "list_declared_source_business_support",
    "list_family_descriptors",
    "resolve_family_descriptor",
]
