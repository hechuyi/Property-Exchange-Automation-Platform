"""SSE current-live contract definitions for all four task types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
    project_type: str
    list_requests: tuple[SseListRequest, ...]
    detail_route_kind: str   # "realright", "equity", "capital"
    date_field_candidates: tuple[str, ...] = ("PLKSRQ", "plksrq", "XMID", "xmid")


# Live-validated contract: SSE physical assets (SWZC / ZICHANZHUANRANG)
SSE_PHYSICAL_ASSET_CONTRACT = SseTaskContract(
    project_type="physical_asset",
    list_requests=(SseListRequest(endpoint="/prjs/realright/list", xmlx=None),),
    detail_route_kind="realright",
    date_field_candidates=("disclosure_start",),
)

# Live-validated contract: SSE equity transfer (CQZR / CHANQUAN XMLX=2)
SSE_EQUITY_TRANSFER_CONTRACT = SseTaskContract(
    project_type="equity_transfer",
    list_requests=(SseListRequest(endpoint="/prjs/equity/list", xmlx="2"),),
    detail_route_kind="equity",
    date_field_candidates=("disclosure_start", "disclosure_end"),
)

# Live-validated contract: SSE capital increase (QYZZ / ZENGZI XMLX=2)
SSE_CAPITAL_INCREASE_CONTRACT = SseTaskContract(
    project_type="capital_increase",
    list_requests=(SseListRequest(endpoint="/prjs/capitalincrease/list", xmlx="2"),),
    detail_route_kind="capital",
    date_field_candidates=("disclosure_start", "disclosure_end"),
)

# Live-validated contract: SSE pre-disclosure (equity + capital XMLX=1)
SSE_PRE_DISCLOSURE_CONTRACT = SseTaskContract(
    project_type="pre_disclosure",
    list_requests=(
        SseListRequest(endpoint="/prjs/equity/list", xmlx="1"),
        SseListRequest(endpoint="/prjs/capitalincrease/list", xmlx="1"),
    ),
    detail_route_kind="equity",  # both use equity-style detail route
    date_field_candidates=("disclosure_start",),
)

ALL_SSE_CONTRACTS: dict[str, SseTaskContract] = {
    "physical_asset": SSE_PHYSICAL_ASSET_CONTRACT,
    "equity_transfer": SSE_EQUITY_TRANSFER_CONTRACT,
    "capital_increase": SSE_CAPITAL_INCREASE_CONTRACT,
    "pre_disclosure": SSE_PRE_DISCLOSURE_CONTRACT,
}


def get_sse_task_contract(project_type: str) -> SseTaskContract:
    """Return the current-live contract for the given SSE project type."""
    contract = ALL_SSE_CONTRACTS.get(project_type)
    if contract is None:
        raise ValueError(f"No SSE contract for project_type={project_type!r}")
    return contract
