"""SSE current-live contract definitions for all four task types."""

from __future__ import annotations

from dataclasses import dataclass

from peap_core.source_business_contract import get_source_business_requirement


@dataclass(frozen=True)
class SseListRequest:
    """A single list-API request for an SSE task type."""
    endpoint: str          # e.g. "/prjs/equity/list"
    xmlx: str | None      # "1", "2", or None for physical assets
    page_size_field: str = "pageSize"
    page_no_field: str = "pageNo"


@dataclass(frozen=True)
class SseTaskContract:
    """Current-live contract for one SSE project type."""
    business_id: str
    list_requests: tuple[SseListRequest, ...]
    detail_route_kind: str   # "realright", "equity", "capital"
    date_field_candidates: tuple[str, ...] = ("PLKSRQ", "plksrq", "XMID", "xmid")


def _sse_contract(
    business_id: str,
    *,
    detail_route_kind: str,
    date_field_candidates: tuple[str, ...],
) -> SseTaskContract:
    requirement = get_source_business_requirement("sse", "listing", business_id)
    return SseTaskContract(
        business_id=business_id,
        list_requests=tuple(
            SseListRequest(
                endpoint=str(spec["endpoint"]),
                xmlx=str(spec["XMLX"]) if "XMLX" in spec else None,
            )
            for spec in requirement.list_query_specs
        ),
        detail_route_kind=detail_route_kind,
        date_field_candidates=date_field_candidates,
    )


SSE_PHYSICAL_ASSET_CONTRACT = _sse_contract(
    "physical_asset",
    detail_route_kind="realright",
    date_field_candidates=("disclosure_start",),
)
SSE_EQUITY_TRANSFER_CONTRACT = _sse_contract(
    "equity_transfer",
    detail_route_kind="equity",
    date_field_candidates=("disclosure_start", "disclosure_end"),
)
SSE_CAPITAL_INCREASE_CONTRACT = _sse_contract(
    "capital_increase",
    detail_route_kind="capital",
    date_field_candidates=("disclosure_start", "disclosure_end"),
)
SSE_PRE_DISCLOSURE_CONTRACT = _sse_contract(
    "pre_disclosure",
    detail_route_kind="equity",
    date_field_candidates=("disclosure_start",),
)

ALL_SSE_CONTRACTS: dict[str, SseTaskContract] = {
    "physical_asset": SSE_PHYSICAL_ASSET_CONTRACT,
    "equity_transfer": SSE_EQUITY_TRANSFER_CONTRACT,
    "capital_increase": SSE_CAPITAL_INCREASE_CONTRACT,
    "pre_disclosure": SSE_PRE_DISCLOSURE_CONTRACT,
}


def get_sse_task_contract(business_id: str) -> SseTaskContract:
    """Return the current-live contract for the given SSE business id."""
    contract = ALL_SSE_CONTRACTS.get(business_id)
    if contract is None:
        raise ValueError(f"No SSE contract for business_id={business_id!r}")
    return contract
