"""Runtime bindings for source/family/business download support."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Type

from peap_core.business_catalog import get_business_descriptor
from peap_core.family_catalog import get_family_descriptor
from peap_core.source_business_contract import get_source_business_requirement
from peap_core.source_catalog import get_source_descriptor

from .downloaders import (
    CbexCapitalIncreaseDownloader,
    CbexDealCapitalIncreaseDownloader,
    CbexDealEquityTransferDownloader,
    CbexDealPhysicalAssetDownloader,
    CbexEquityTransferDownloader,
    CbexPhysicalAssetDownloader,
    CbexPreDisclosureDownloader,
    ChongqingCapitalIncreaseDownloader,
    ChongqingDealCapitalIncreaseDownloader,
    ChongqingDealEquityTransferDownloader,
    ChongqingEquityTransferDownloader,
    ChongqingPhysicalAssetDownloader,
    ChongqingPreDisclosureDownloader,
    GuangdongCapitalIncreaseDownloader,
    GuangdongEquityTransferDownloader,
    ShandongCapitalIncreaseDownloader,
    ShandongEquityTransferDownloader,
    ShanghaiCapitalIncreaseDownloader,
    ShanghaiDealCapitalIncreaseDownloader,
    ShanghaiDealEquityTransferDownloader,
    ShanghaiDealPhysicalAssetDownloader,
    ShanghaiEquityTransferDownloader,
    ShanghaiPhysicalAssetDownloader,
    ShanghaiPreDisclosureDownloader,
    ShenzhenCapitalIncreaseDownloader,
    ShenzhenEquityTransferDownloader,
    TianjinCapitalIncreaseDownloader,
    TianjinDealCapitalIncreaseDownloader,
    TianjinDealEquityTransferDownloader,
    TianjinEquityTransferDownloader,
    TianjinPhysicalAssetDownloader,
    TianjinPreDisclosureDownloader,
)


@dataclass(frozen=True)
class SourceBusinessBinding:
    source_id: str
    record_family: str
    business_id: str
    downloader_cls: Type
    progress_label: str
    manifest_detail_route: str | None = None
    manifest_render_page_route: str | None = None
    manifest_detail_api_endpoint: str | None = None
    manifest_transferee_details_endpoint: str | None = None
    manifest_list_endpoint: str | None = None
    manifest_date_field_candidates: tuple[str, ...] | None = None
    implemented: bool = True

    @property
    def task_id(self) -> str:
        return f"{self.source_id}:{self.record_family}:{self.business_id}"

    @property
    def display_name(self) -> str:
        source = get_source_descriptor(self.source_id)
        business = get_business_descriptor(self.business_id, family_id=self.record_family)
        return f"{source.site_label} - {business.canonical_label}"


def _validate_source_business_bindings(
    bindings: tuple[SourceBusinessBinding, ...],
) -> tuple[SourceBusinessBinding, ...]:
    seen_keys: set[tuple[str, str, str]] = set()
    for binding in bindings:
        key = (binding.source_id, binding.record_family, binding.business_id)
        if key in seen_keys:
            raise ValueError(f"duplicate source-business binding {key!r}")
        seen_keys.add(key)

        source = get_source_descriptor(binding.source_id)
        family = get_family_descriptor(binding.record_family)
        business = get_business_descriptor(binding.business_id, family_id=family.family_id)
        if family.family_id not in source.supported_record_families:
            raise ValueError(
                f"source {source.source_id!r} does not support family {family.family_id!r}"
            )
        if source.source_id not in family.source_ids:
            raise ValueError(
                f"family {family.family_id!r} does not include source {source.source_id!r}"
            )
        if business.business_id not in family.business_ids:
            raise ValueError(
                f"family {family.family_id!r} does not include business {business.business_id!r}"
            )
    return bindings


def _deal_manifest_fields(source_id: str, business_id: str) -> dict[str, object]:
    requirement = get_source_business_requirement(source_id, "deal", business_id)
    return {
        "manifest_list_endpoint": requirement.list_endpoint,
        "manifest_detail_route": requirement.detail_route,
        "manifest_render_page_route": requirement.render_page_route,
        "manifest_detail_api_endpoint": requirement.detail_api_endpoint,
        "manifest_transferee_details_endpoint": requirement.transferee_details_endpoint,
        "manifest_date_field_candidates": requirement.date_field_candidates,
    }


_BINDINGS: tuple[SourceBusinessBinding, ...] = _validate_source_business_bindings((
    SourceBusinessBinding(
        source_id="sse",
        record_family="listing",
        business_id="physical_asset",
        downloader_cls=ShanghaiPhysicalAssetDownloader,
        progress_label="挂牌实物资产",
    ),
    SourceBusinessBinding(
        source_id="sse",
        record_family="listing",
        business_id="equity_transfer",
        downloader_cls=ShanghaiEquityTransferDownloader,
        progress_label="挂牌股权转让",
        manifest_detail_route="jymhchanquan",
    ),
    SourceBusinessBinding(
        source_id="sse",
        record_family="listing",
        business_id="capital_increase",
        downloader_cls=ShanghaiCapitalIncreaseDownloader,
        progress_label="挂牌增资扩股",
        manifest_detail_route="jymhzengzi",
    ),
    SourceBusinessBinding(
        source_id="sse",
        record_family="listing",
        business_id="pre_disclosure",
        downloader_cls=ShanghaiPreDisclosureDownloader,
        progress_label="挂牌预披露",
        manifest_detail_route="jymhchanquanyu",
    ),
    SourceBusinessBinding(
        source_id="sse",
        record_family="deal",
        business_id="deal_physical_asset",
        downloader_cls=ShanghaiDealPhysicalAssetDownloader,
        progress_label="成交实物资产",
        **_deal_manifest_fields("sse", "deal_physical_asset"),
    ),
    SourceBusinessBinding(
        source_id="sse",
        record_family="deal",
        business_id="deal_equity_transfer",
        downloader_cls=ShanghaiDealEquityTransferDownloader,
        progress_label="成交股权转让",
        **_deal_manifest_fields("sse", "deal_equity_transfer"),
    ),
    SourceBusinessBinding(
        source_id="sse",
        record_family="deal",
        business_id="deal_capital_increase",
        downloader_cls=ShanghaiDealCapitalIncreaseDownloader,
        progress_label="成交增资扩股",
        **_deal_manifest_fields("sse", "deal_capital_increase"),
    ),
    SourceBusinessBinding(
        source_id="cbex",
        record_family="listing",
        business_id="physical_asset",
        downloader_cls=CbexPhysicalAssetDownloader,
        progress_label="挂牌实物资产",
    ),
    SourceBusinessBinding(
        source_id="cbex",
        record_family="listing",
        business_id="equity_transfer",
        downloader_cls=CbexEquityTransferDownloader,
        progress_label="挂牌股权转让",
        manifest_detail_route="/xm/cqzr/",
    ),
    SourceBusinessBinding(
        source_id="cbex",
        record_family="listing",
        business_id="capital_increase",
        downloader_cls=CbexCapitalIncreaseDownloader,
        progress_label="挂牌增资扩股",
        manifest_detail_route="/xm/qyzz/",
    ),
    SourceBusinessBinding(
        source_id="cbex",
        record_family="listing",
        business_id="pre_disclosure",
        downloader_cls=CbexPreDisclosureDownloader,
        progress_label="挂牌预披露",
    ),
    SourceBusinessBinding(
        source_id="cbex",
        record_family="deal",
        business_id="deal_physical_asset",
        downloader_cls=CbexDealPhysicalAssetDownloader,
        progress_label="成交实物资产",
        **_deal_manifest_fields("cbex", "deal_physical_asset"),
    ),
    SourceBusinessBinding(
        source_id="cbex",
        record_family="deal",
        business_id="deal_equity_transfer",
        downloader_cls=CbexDealEquityTransferDownloader,
        progress_label="成交股权转让",
        **_deal_manifest_fields("cbex", "deal_equity_transfer"),
    ),
    SourceBusinessBinding(
        source_id="cbex",
        record_family="deal",
        business_id="deal_capital_increase",
        downloader_cls=CbexDealCapitalIncreaseDownloader,
        progress_label="成交增资扩股",
        **_deal_manifest_fields("cbex", "deal_capital_increase"),
    ),
    SourceBusinessBinding(
        source_id="tpre",
        record_family="listing",
        business_id="physical_asset",
        downloader_cls=TianjinPhysicalAssetDownloader,
        progress_label="挂牌实物资产",
    ),
    SourceBusinessBinding(
        source_id="tpre",
        record_family="listing",
        business_id="equity_transfer",
        downloader_cls=TianjinEquityTransferDownloader,
        progress_label="挂牌股权转让",
    ),
    SourceBusinessBinding(
        source_id="tpre",
        record_family="listing",
        business_id="capital_increase",
        downloader_cls=TianjinCapitalIncreaseDownloader,
        progress_label="挂牌增资扩股",
    ),
    SourceBusinessBinding(
        source_id="tpre",
        record_family="listing",
        business_id="pre_disclosure",
        downloader_cls=TianjinPreDisclosureDownloader,
        progress_label="挂牌预披露",
    ),
    SourceBusinessBinding(
        source_id="tpre",
        record_family="deal",
        business_id="deal_equity_transfer",
        downloader_cls=TianjinDealEquityTransferDownloader,
        progress_label="成交股权转让",
        **_deal_manifest_fields("tpre", "deal_equity_transfer"),
    ),
    SourceBusinessBinding(
        source_id="tpre",
        record_family="deal",
        business_id="deal_capital_increase",
        downloader_cls=TianjinDealCapitalIncreaseDownloader,
        progress_label="成交增资扩股",
        **_deal_manifest_fields("tpre", "deal_capital_increase"),
    ),
    SourceBusinessBinding(
        source_id="cquae",
        record_family="listing",
        business_id="physical_asset",
        downloader_cls=ChongqingPhysicalAssetDownloader,
        progress_label="挂牌实物资产",
    ),
    SourceBusinessBinding(
        source_id="cquae",
        record_family="listing",
        business_id="equity_transfer",
        downloader_cls=ChongqingEquityTransferDownloader,
        progress_label="挂牌股权转让",
    ),
    SourceBusinessBinding(
        source_id="cquae",
        record_family="listing",
        business_id="capital_increase",
        downloader_cls=ChongqingCapitalIncreaseDownloader,
        progress_label="挂牌增资扩股",
    ),
    SourceBusinessBinding(
        source_id="cquae",
        record_family="listing",
        business_id="pre_disclosure",
        downloader_cls=ChongqingPreDisclosureDownloader,
        progress_label="挂牌预披露",
    ),
    SourceBusinessBinding(
        source_id="cquae",
        record_family="deal",
        business_id="deal_equity_transfer",
        downloader_cls=ChongqingDealEquityTransferDownloader,
        progress_label="成交股权转让",
        **_deal_manifest_fields("cquae", "deal_equity_transfer"),
    ),
    SourceBusinessBinding(
        source_id="cquae",
        record_family="deal",
        business_id="deal_capital_increase",
        downloader_cls=ChongqingDealCapitalIncreaseDownloader,
        progress_label="成交增资扩股",
        **_deal_manifest_fields("cquae", "deal_capital_increase"),
    ),
    SourceBusinessBinding(
        source_id="shandong",
        record_family="listing",
        business_id="equity_transfer",
        downloader_cls=ShandongEquityTransferDownloader,
        progress_label="挂牌股权转让",
    ),
    SourceBusinessBinding(
        source_id="shandong",
        record_family="listing",
        business_id="capital_increase",
        downloader_cls=ShandongCapitalIncreaseDownloader,
        progress_label="挂牌增资扩股",
    ),
    SourceBusinessBinding(
        source_id="guangdong",
        record_family="listing",
        business_id="equity_transfer",
        downloader_cls=GuangdongEquityTransferDownloader,
        progress_label="挂牌股权转让",
    ),
    SourceBusinessBinding(
        source_id="guangdong",
        record_family="listing",
        business_id="capital_increase",
        downloader_cls=GuangdongCapitalIncreaseDownloader,
        progress_label="挂牌增资扩股",
    ),
    SourceBusinessBinding(
        source_id="shenzhen",
        record_family="listing",
        business_id="equity_transfer",
        downloader_cls=ShenzhenEquityTransferDownloader,
        progress_label="挂牌股权转让",
    ),
    SourceBusinessBinding(
        source_id="shenzhen",
        record_family="listing",
        business_id="capital_increase",
        downloader_cls=ShenzhenCapitalIncreaseDownloader,
        progress_label="挂牌增资扩股",
    ),
))

_REGISTRY = {
    (binding.source_id, binding.record_family, binding.business_id): binding
    for binding in _BINDINGS
}


def build_source_business_registry() -> Dict[tuple[str, str, str], SourceBusinessBinding]:
    return dict(_REGISTRY)


def iter_source_business_bindings(
    *,
    source_id: str | None = None,
    record_family: str | None = None,
) -> Iterable[SourceBusinessBinding]:
    normalized_source = str(source_id or "").strip()
    normalized_family = str(record_family or "").strip()
    for binding in _BINDINGS:
        if normalized_source and binding.source_id != normalized_source:
            continue
        if normalized_family and binding.record_family != normalized_family:
            continue
        yield binding


def get_source_business_binding(
    source_id: str,
    *,
    record_family: str,
    business_id: str,
) -> SourceBusinessBinding:
    key = (str(source_id or "").strip(), str(record_family or "").strip(), str(business_id or "").strip())
    try:
        return _REGISTRY[key]
    except KeyError as exc:
        raise KeyError(key) from exc


def task_page_size_key(binding: SourceBusinessBinding) -> str:
    return binding.task_id


__all__ = [
    "SourceBusinessBinding",
    "build_source_business_registry",
    "get_source_business_binding",
    "iter_source_business_bindings",
    "task_page_size_key",
]
