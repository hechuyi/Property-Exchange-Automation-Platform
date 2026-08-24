"""Helpers for concrete deal downloaders to consume source/business declarations."""

from __future__ import annotations

from peap_core import source_business_contract
from peap_core.source_business_contract import SourceBusinessRequirement


def get_deal_requirement(source_id: str, business_id: str) -> SourceBusinessRequirement:
    return source_business_contract.get_source_business_requirement(
        source_id,
        "deal",
        business_id,
    )


def apply_deal_manifest_fields(
    downloader: object,
    *,
    source_id: str,
    business_id: str,
) -> SourceBusinessRequirement:
    requirement = get_deal_requirement(source_id, business_id)
    downloader.manifest_list_endpoint = requirement.list_endpoint
    downloader.manifest_detail_route = requirement.detail_route
    downloader.manifest_render_page_route = requirement.render_page_route
    downloader.manifest_detail_api_endpoint = requirement.detail_api_endpoint
    downloader.manifest_transferee_details_endpoint = requirement.transferee_details_endpoint
    downloader.manifest_date_field_candidates = requirement.date_field_candidates
    return requirement


def preferred_deal_date_field(requirement: SourceBusinessRequirement) -> str:
    return str(
        requirement.date_basis
        or (requirement.date_field_candidates[0] if requirement.date_field_candidates else "")
        or "deal_date"
    )
