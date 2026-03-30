"""Fixed shipped product profile registry."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from typing import Dict

from peap_core.source_catalog import source_ids_for_record_family


@dataclass(frozen=True)
class ProductProfile:
    profile_id: str
    record_family: str
    source_ids: tuple[str, ...]
    parser_compat: str
    postprocess_profile: str
    export_profile: str
    readiness_policy: str


DEFAULT_PRODUCT_PROFILE_ID = "desktop_listing"
_DEFAULT_POSTPROCESS_CONFIG_RELATIVE_PATH = ("ppe_config", "postprocess_external_template.json")
_DEFAULT_POSTPROCESS_FALLBACK_ROOTS = (
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "peap_postprocess")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "peap_postprocess")),
)
_DESKTOP_LISTING_SOURCE_IDS = source_ids_for_record_family("listing")

_PRODUCT_PROFILES: Dict[str, ProductProfile] = {
    DEFAULT_PRODUCT_PROFILE_ID: ProductProfile(
        profile_id=DEFAULT_PRODUCT_PROFILE_ID,
        record_family="listing",
        source_ids=_DESKTOP_LISTING_SOURCE_IDS,
        parser_compat="listing_v1",
        postprocess_profile="postprocess_external",
        export_profile="ready_export",
        readiness_policy="browser_runtime_required",
    )
}


def list_product_profiles() -> list[ProductProfile]:
    return list(_PRODUCT_PROFILES.values())


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


def get_product_profile(profile_id: str = DEFAULT_PRODUCT_PROFILE_ID) -> ProductProfile:
    normalized = str(profile_id or "").strip() or DEFAULT_PRODUCT_PROFILE_ID
    try:
        return _PRODUCT_PROFILES[normalized]
    except KeyError as exc:
        raise KeyError(normalized) from exc
