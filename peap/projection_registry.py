"""Projection/export profile registry.

This registry owns family/business -> output/profile selection.
Column shapes stay in `output_contract.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from peap_core.business_catalog import (
    get_business_descriptor,
    resolve_business_descriptor,
    resolve_business_descriptor_by_project_type,
)
from peap_core.family_catalog import get_family_descriptor

from .output_contract import (
    KIND_CAPITAL,
    KIND_DEAL_CAPITAL,
    KIND_DEAL_EQUITY,
    KIND_DEAL_PHYSICAL,
    KIND_EQUITY,
    KIND_PHYSICAL,
    KIND_PRE,
    get_output_stem_for_kind,
)


@dataclass(frozen=True)
class ProjectionProfile:
    profile_id: str
    record_family: str
    business_id: str
    output_kind: str
    output_stem: str
    business_label: str


_PROFILES: tuple[ProjectionProfile, ...] = (
    ProjectionProfile(
        profile_id="listing/equity_transfer",
        record_family="listing",
        business_id="equity_transfer",
        output_kind=KIND_EQUITY,
        output_stem=get_output_stem_for_kind(KIND_EQUITY),
        business_label="股权转让",
    ),
    ProjectionProfile(
        profile_id="listing/physical_asset",
        record_family="listing",
        business_id="physical_asset",
        output_kind=KIND_PHYSICAL,
        output_stem=get_output_stem_for_kind(KIND_PHYSICAL),
        business_label="实物资产",
    ),
    ProjectionProfile(
        profile_id="listing/capital_increase",
        record_family="listing",
        business_id="capital_increase",
        output_kind=KIND_CAPITAL,
        output_stem=get_output_stem_for_kind(KIND_CAPITAL),
        business_label="增资扩股",
    ),
    ProjectionProfile(
        profile_id="listing/pre_disclosure",
        record_family="listing",
        business_id="pre_disclosure",
        output_kind=KIND_PRE,
        output_stem=get_output_stem_for_kind(KIND_PRE),
        business_label="预披露",
    ),
    ProjectionProfile(
        profile_id="deal/equity_transfer",
        record_family="deal",
        business_id="deal_equity_transfer",
        output_kind=KIND_DEAL_EQUITY,
        output_stem="成交_股权转让",
        business_label="股权转让",
    ),
    ProjectionProfile(
        profile_id="deal/physical_asset",
        record_family="deal",
        business_id="deal_physical_asset",
        output_kind=KIND_DEAL_PHYSICAL,
        output_stem="成交_实物资产",
        business_label="实物资产",
    ),
    ProjectionProfile(
        profile_id="deal/capital_increase",
        record_family="deal",
        business_id="deal_capital_increase",
        output_kind=KIND_DEAL_CAPITAL,
        output_stem="成交_增资扩股",
        business_label="增资扩股",
    ),
)

_PROFILE_BY_KEY = {
    (profile.record_family, profile.business_id): profile
    for profile in _PROFILES
}


def _normalized_family_id(record_family: str) -> str:
    text = str(record_family or "").strip()
    if not text:
        return ""
    try:
        return get_family_descriptor(text).family_id
    except KeyError:
        return text


def list_projection_profiles(*, record_family: str | None = None) -> list[ProjectionProfile]:
    normalized_family = str(record_family or "").strip()
    return [
        profile
        for profile in _PROFILES
        if not normalized_family or profile.record_family == normalized_family
    ]


def resolve_projection_profile(record_family: str, business_id: str) -> ProjectionProfile | None:
    normalized_family = _normalized_family_id(record_family)
    raw_business = str(business_id or "").strip()
    if not normalized_family or not raw_business:
        return None
    try:
        descriptor = resolve_business_descriptor(raw_business, family_id=normalized_family)
    except KeyError:
        descriptor = None
    if descriptor is None:
        descriptor = resolve_business_descriptor_by_project_type(
            raw_business,
            family_id=normalized_family,
        )
    candidate_ids = {raw_business}
    if descriptor is not None:
        candidate_ids.add(descriptor.business_id)
    for profile in _PROFILES:
        if profile.record_family != normalized_family:
            continue
        if (
            profile.business_id in candidate_ids
            or profile.business_label == raw_business
            or profile.output_kind == raw_business
            or profile.profile_id.split("/")[-1] == raw_business
        ):
            return profile
    return None


def get_projection_profile(record_family: str, business_id: str) -> ProjectionProfile:
    profile = resolve_projection_profile(record_family, business_id)
    if profile is not None:
        return profile
    normalized_family = _normalized_family_id(record_family)
    try:
        descriptor = get_business_descriptor(business_id, family_id=normalized_family or None)
        key = descriptor.business_id
    except KeyError:
        key = str(business_id or "").strip()
    raise KeyError((normalized_family, key))


__all__ = [
    "ProjectionProfile",
    "get_projection_profile",
    "list_projection_profiles",
    "resolve_projection_profile",
]
