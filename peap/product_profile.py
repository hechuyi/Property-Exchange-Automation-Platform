"""Fixed shipped product profile registry."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from typing import Dict

from peap_core.family_catalog import get_family_descriptor


@dataclass(frozen=True)
class ProductProfile:
    profile_id: str
    family_id: str
    source_ids: tuple[str, ...]
    postprocess_profile: str
    export_profile: str
    readiness_policy: str

    @property
    def record_family(self) -> str:
        return self.family_id


_LISTING_FAMILY = get_family_descriptor("listing")
DEFAULT_PRODUCT_PROFILE_ID = _LISTING_FAMILY.default_product_profile_id
_DEFAULT_POSTPROCESS_CONFIG_RELATIVE_PATH = ("ppe_config", "postprocess_external_template.json")
_DEFAULT_POSTPROCESS_FALLBACK_ROOTS = (
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "peap_postprocess")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "peap_postprocess")),
)

_PRODUCT_PROFILES: Dict[str, ProductProfile] = {
    DEFAULT_PRODUCT_PROFILE_ID: ProductProfile(
        profile_id=DEFAULT_PRODUCT_PROFILE_ID,
        family_id=_LISTING_FAMILY.family_id,
        source_ids=_LISTING_FAMILY.source_ids,
        postprocess_profile="postprocess_external",
        export_profile="ready_export",
        readiness_policy="browser_runtime_required",
    )
}
_DEAL_FAMILY = get_family_descriptor("deal")
_PRODUCT_PROFILES[_DEAL_FAMILY.default_product_profile_id] = ProductProfile(
    profile_id=_DEAL_FAMILY.default_product_profile_id,
    family_id=_DEAL_FAMILY.family_id,
    source_ids=_DEAL_FAMILY.source_ids,
    postprocess_profile="postprocess_external",
    export_profile="ready_export",
    readiness_policy="browser_runtime_required",
)


def list_product_profiles() -> list[ProductProfile]:
    return list(_PRODUCT_PROFILES.values())


def validate_product_profiles(
    profiles: tuple[ProductProfile, ...],
) -> tuple[ProductProfile, ...]:
    seen_profile_ids: set[str] = set()
    for profile in profiles:
        normalized_profile_id = str(profile.profile_id or "").strip()
        if not normalized_profile_id:
            raise ValueError("profile_id must be non-empty")
        if normalized_profile_id in seen_profile_ids:
            raise ValueError(f"duplicate profile_id {normalized_profile_id!r}")
        seen_profile_ids.add(normalized_profile_id)

        family = get_family_descriptor(profile.family_id)
        if profile.profile_id != family.default_product_profile_id:
            raise ValueError(
                f"profile {profile.profile_id!r} does not match family "
                f"{family.family_id!r} default profile {family.default_product_profile_id!r}"
            )
        if profile.source_ids != family.source_ids:
            raise ValueError(
                f"profile {profile.profile_id!r} source_ids {profile.source_ids!r} "
                f"do not match family {family.family_id!r} source_ids {family.source_ids!r}"
            )
    return profiles


def get_default_postprocess_config_path() -> str:
    packaged_root = resources.files("peap_postprocess")
    packaged_candidate = packaged_root.joinpath(*_DEFAULT_POSTPROCESS_CONFIG_RELATIVE_PATH)
    packaged_path = os.fspath(packaged_candidate)
    if os.path.isfile(packaged_path):
        return packaged_path

    for root in _DEFAULT_POSTPROCESS_FALLBACK_ROOTS:
        candidate = os.path.join(root, *_DEFAULT_POSTPROCESS_CONFIG_RELATIVE_PATH)
        if os.path.isfile(candidate):
            return candidate
    return ""


def get_product_profile(
    profile_id: str | None = None,
    *,
    record_family: str | None = None,
) -> ProductProfile:
    normalized_family = str(record_family or "").strip()
    if normalized_family:
        family = get_family_descriptor(normalized_family)
        normalized_profile = str(profile_id or "").strip()
        if not normalized_profile:
            normalized_profile = family.default_product_profile_id
        profile = _PRODUCT_PROFILES.get(normalized_profile)
        if profile is None:
            raise KeyError(normalized_profile)
        if profile.family_id != family.family_id:
            raise ValueError(
                f"profile {profile.profile_id!r} belongs to family {profile.family_id!r}, "
                f"not {family.family_id!r}"
            )
        return profile

    normalized = str(profile_id or "").strip() or DEFAULT_PRODUCT_PROFILE_ID
    try:
        return _PRODUCT_PROFILES[normalized]
    except KeyError as exc:
        raise KeyError(normalized) from exc


_PRODUCT_PROFILES = {
    profile.profile_id: profile
    for profile in validate_product_profiles(tuple(_PRODUCT_PROFILES.values()))
}
