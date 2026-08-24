"""Immutable source/business special requirement declarations."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping
from urllib.parse import urlsplit

from peap_core.business_catalog import get_business_descriptor
from peap_core.family_catalog import declared_source_ids_for_business, get_family_descriptor
from peap_core.source_catalog import get_source_descriptor


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class SourceBusinessRequirement:
    source_id: str
    record_family: str
    business_id: str
    scope_policy: str
    required_query_filters: Mapping[str, object]
    list_endpoint: str = ""
    list_query_specs: tuple[Mapping[str, object], ...] = ()
    detail_route: str = ""
    render_page_route: str = ""
    detail_api_endpoint: str = ""
    transferee_details_endpoint: str = ""
    date_field_candidates: tuple[str, ...] = ()
    date_basis: str = ""
    discovery_query_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScopePolicyDescriptor:
    policy_id: str
    label: str
    summary: str


@dataclass(frozen=True)
class ExportWorkbookSupport:
    source_id: str
    record_family: str
    business_id: str
    supported: bool
    sheet_name: str = ""
    unsupported_reason: str = ""


@dataclass(frozen=True)
class ExportReadinessRequirement:
    record_family: str
    business_id: str
    requires_deal_price: bool = False
    requires_non_summary_investor: bool = False
    requires_investor_amount: bool = False
    deal_date_policy: str = ""
    allows_collection_date_audit_fallback: bool = False


@dataclass(frozen=True)
class OptionalPostprocessRuleRequirement:
    record_family: str
    business_id: str
    rule_id: str
    purpose: str
    optional: bool = True
    listing_only: bool = False


@dataclass(frozen=True)
class SourceClassifierRouteMarkers:
    source_id: str
    record_family: str
    route_markers: tuple[str, ...]
    content_route_markers: tuple[str, ...]


def _freeze_filters(filters: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {
            key: tuple(value) if isinstance(value, (list, tuple)) else value
            for key, value in filters.items()
        }
    )


def _freeze_query_specs(specs: Iterable[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    return tuple(_freeze_filters(spec) for spec in specs)


def _listing_requirement(
    *,
    source_id: str,
    business_id: str,
    list_endpoint: str,
    list_query_specs: Iterable[Mapping[str, object]],
    scope_policy: str = "",
    required_query_filters: Mapping[str, object] | None = None,
) -> SourceBusinessRequirement:
    return SourceBusinessRequirement(
        source_id=source_id,
        record_family="listing",
        business_id=business_id,
        scope_policy=scope_policy,
        required_query_filters=_freeze_filters(required_query_filters or {}),
        list_endpoint=list_endpoint,
        list_query_specs=_freeze_query_specs(list_query_specs),
    )


def _deal_requirement(
    *,
    source_id: str,
    business_id: str,
    list_endpoint: str,
    detail_route: str,
    render_page_route: str | None = None,
    detail_api_endpoint: str = "",
    transferee_details_endpoint: str = "",
    date_field_candidates: Iterable[str],
    date_basis: str,
    discovery_query_ids: Iterable[str] = (),
) -> SourceBusinessRequirement:
    return SourceBusinessRequirement(
        source_id=source_id,
        record_family="deal",
        business_id=business_id,
        scope_policy="",
        required_query_filters=_freeze_filters({}),
        list_endpoint=list_endpoint,
        discovery_query_ids=tuple(
            normalized for value in discovery_query_ids if (normalized := _normalize_text(value))
        ),
        detail_route=detail_route,
        render_page_route=detail_route if render_page_route is None else render_page_route,
        detail_api_endpoint=detail_api_endpoint,
        transferee_details_endpoint=transferee_details_endpoint,
        date_field_candidates=tuple(str(value) for value in date_field_candidates if str(value)),
        date_basis=date_basis,
    )


def _requirement_key(requirement: SourceBusinessRequirement) -> tuple[str, str, str]:
    return (
        _normalize_text(requirement.source_id),
        _normalize_text(requirement.record_family),
        _normalize_text(requirement.business_id),
    )


def source_business_requirement_supported_by_catalog(
    requirement: SourceBusinessRequirement,
) -> bool:
    source_id, family_id, business_id = _requirement_key(requirement)
    if not (source_id and family_id and business_id):
        return False
    try:
        source = get_source_descriptor(source_id)
        family = get_family_descriptor(family_id)
        business = get_business_descriptor(business_id, family_id=family.family_id)
        declared_source_ids = declared_source_ids_for_business(family.family_id, business.business_id)
    except KeyError:
        return False
    return (
        source.source_id == source_id
        and family.family_id == family_id
        and business.business_id == business_id
        and family.family_id in tuple(source.supported_record_families or ())
        and source.source_id in tuple(family.source_ids or ())
        and source.source_id in tuple(declared_source_ids or ())
    )


def validate_source_business_requirements(
    requirements: tuple[SourceBusinessRequirement, ...],
) -> tuple[SourceBusinessRequirement, ...]:
    seen_keys: set[tuple[str, str, str]] = set()
    for requirement in requirements:
        key = _requirement_key(requirement)
        if not all(key):
            raise ValueError(f"source-business requirement has incomplete key {key!r}")
        if key in seen_keys:
            raise ValueError(f"duplicate source-business requirement {key!r}")
        seen_keys.add(key)
        if not source_business_requirement_supported_by_catalog(requirement):
            raise ValueError(f"source-business requirement is outside catalog support {key!r}")
    return requirements


_SOURCE_BUSINESS_REQUIREMENTS: tuple[SourceBusinessRequirement, ...] = validate_source_business_requirements((
    _deal_requirement(
        source_id="sse",
        business_id="deal_physical_asset",
        list_endpoint="/si/notice/getDealNoticeList",
        detail_route="/si/notice/getNoticeDetail",
        date_field_candidates=("CJRQ", "deal_date"),
        date_basis="CJRQ",
        discovery_query_ids=("deal-notice-list",),
    ),
    _deal_requirement(
        source_id="sse",
        business_id="deal_equity_transfer",
        list_endpoint="/si/notice/getDealNoticeList",
        detail_route="/si/notice/getNoticeDetail",
        date_field_candidates=("CJRQ", "deal_date"),
        date_basis="CJRQ",
        discovery_query_ids=("deal-notice-list",),
    ),
    _deal_requirement(
        source_id="sse",
        business_id="deal_capital_increase",
        list_endpoint="/si/notice/getDealNoticeList",
        detail_route="/si/notice/getNoticeDetail",
        date_field_candidates=("CJRQ", "deal_date"),
        date_basis="CJRQ",
        discovery_query_ids=("deal-notice-list",),
    ),
    _deal_requirement(
        source_id="cbex",
        business_id="deal_physical_asset",
        list_endpoint="/xm/zczr/cjjggs/",
        detail_route="/xm/zczr/",
        date_field_candidates=("deal_date",),
        date_basis="deal_date",
    ),
    _deal_requirement(
        source_id="cbex",
        business_id="deal_equity_transfer",
        list_endpoint="/xm/cqzr/cjjggs/",
        detail_route="/xm/cqzr/",
        date_field_candidates=("deal_date",),
        date_basis="deal_date",
    ),
    _deal_requirement(
        source_id="cbex",
        business_id="deal_capital_increase",
        list_endpoint="/xm/qyzz/cjjggs/",
        detail_route="/xm/qyzz/",
        date_field_candidates=("deal_date",),
        date_basis="deal_date",
    ),
    _deal_requirement(
        source_id="tpre",
        business_id="deal_equity_transfer",
        list_endpoint=(
            "/transaction/biz/transaction-management/anmuas/result-notice/page"
            "?bizType=PROPERTY_RIGHT_TRANSFER"
        ),
        detail_route="/transaction-view/data/common/transaction-announcement",
        date_field_candidates=("contractSignTime", "deal_date"),
        date_basis="contractSignTime",
    ),
    _deal_requirement(
        source_id="tpre",
        business_id="deal_capital_increase",
        list_endpoint=(
            "/transaction/biz/increase/transaction/anmuas/result-notice/page"
            "?bizType=ENTERPRISE_CAPITAL_INCREASE"
        ),
        detail_route="/transaction-view/data/common/transaction-announcement",
        transferee_details_endpoint=(
            "/transaction/biz/increase/transaction/transferee/anmuas/result-notice/details"
        ),
        date_field_candidates=("deal_date",),
        date_basis="deal_date",
    ),
    _deal_requirement(
        source_id="cquae",
        business_id="deal_equity_transfer",
        list_endpoint="/CquaeNews/cjgs/List.cshtml",
        detail_route="/CquaeNews/cjgs/",
        date_field_candidates=("deal_date",),
        date_basis="deal_date",
    ),
    _deal_requirement(
        source_id="cquae",
        business_id="deal_capital_increase",
        list_endpoint="/CquaeNews/cjgs/List.cshtml?type=1",
        detail_route="/CquaeNews/cjgs/",
        date_field_candidates=("deal_date",),
        date_basis="deal_date",
    ),
    _listing_requirement(
        source_id="sse",
        business_id="physical_asset",
        list_endpoint="/prjs/realright/list",
        list_query_specs=(
            {"endpoint": "/prjs/realright/list", "project_type": "ZICHANZHUANRANG", "gplx": "2"},
        ),
    ),
    _listing_requirement(
        source_id="sse",
        business_id="equity_transfer",
        list_endpoint="/prjs/equity/list",
        list_query_specs=(
            {
                "endpoint": "/prjs/equity/list",
                "project_type": "CHANQUAN",
                "gplx": "2",
                "XMLX": "2",
            },
        ),
    ),
    _listing_requirement(
        source_id="sse",
        business_id="capital_increase",
        list_endpoint="/prjs/capitalincrease/list",
        list_query_specs=(
            {
                "endpoint": "/prjs/capitalincrease/list",
                "project_type": "ZENGZI",
                "gplx": "2",
                "XMLX": "2",
            },
        ),
    ),
    _listing_requirement(
        source_id="sse",
        business_id="pre_disclosure",
        list_endpoint="/prjs/equity/list",
        list_query_specs=(
            {"endpoint": "/prjs/equity/list", "project_type": "CHANQUAN", "gplx": "1", "XMLX": "1"},
            {
                "endpoint": "/prjs/capitalincrease/list",
                "project_type": "ZENGZI",
                "gplx": "1",
                "XMLX": "1",
            },
        ),
    ),
    _listing_requirement(
        source_id="tpre",
        business_id="equity_transfer",
        list_endpoint="https://trade.tpre.cn/up/biz/project/anmuas/page",
        list_query_specs=(
            {"label": "equity-formal", "systemCode": "PROPERTY_RIGHT_TRANSFER", "bizTypeCode": "FORMAL"},
        ),
    ),
    _listing_requirement(
        source_id="tpre",
        business_id="capital_increase",
        list_endpoint="https://trade.tpre.cn/up/biz/project/anmuas/page",
        list_query_specs=(
            {
                "label": "capital-formal",
                "systemCode": "ENTERPRISE_CAPITAL_INCREASE",
                "bizTypeCode": "FORMAL",
            },
        ),
    ),
    _listing_requirement(
        source_id="tpre",
        business_id="pre_disclosure",
        list_endpoint="https://trade.tpre.cn/up/biz/project/anmuas/page",
        list_query_specs=(
            {"label": "equity-prepare", "systemCode": "PROPERTY_RIGHT_TRANSFER", "bizTypeCode": "PREPARE"},
            {
                "label": "capital-prepare",
                "systemCode": "ENTERPRISE_CAPITAL_INCREASE",
                "bizTypeCode": "PREPARE",
            },
        ),
    ),
    _listing_requirement(
        source_id="cquae",
        business_id="equity_transfer",
        list_endpoint="https://www.cquae.com/project",
        list_query_specs=({"label": "equity-formal", "q": "s", "projectID": 1, "nt": 1, "priceID": 32},),
    ),
    _listing_requirement(
        source_id="cquae",
        business_id="capital_increase",
        list_endpoint="https://www.cquae.com/project",
        list_query_specs=(
            {"label": "capital-formal", "q": "s", "projectID": 2, "ly": 34, "nt": 1, "priceID": 33},
        ),
    ),
    _listing_requirement(
        source_id="cquae",
        business_id="pre_disclosure",
        list_endpoint="https://www.cquae.com/project",
        list_query_specs=(
            {"label": "equity-pre", "q": "s", "projectID": 1, "nt": 3, "priceID": 35},
            {"label": "capital-pre", "q": "s", "projectID": 2, "ly": 34, "nt": 3, "priceID": 34},
        ),
    ),
    _listing_requirement(
        source_id="cbex",
        business_id="physical_asset",
        list_endpoint="https://www.cbex.com.cn/onss-api/jsonp/project/search",
        list_query_specs=(
            {"label": "房屋土地", "businessType": "SW", "assetType": "house"},
            {"label": "交通运输工具", "businessType": "SW", "assetType": "transport"},
            {"label": "设备", "businessType": "SW", "assetType": "equipment"},
        ),
    ),
    _listing_requirement(
        source_id="cbex",
        business_id="equity_transfer",
        list_endpoint="https://www.cbex.com.cn/onss-api/jsonp/project/search",
        list_query_specs=({"label": "股权转让", "businessType": "JC", "include_pre_disclosure": False},),
    ),
    _listing_requirement(
        source_id="cbex",
        business_id="capital_increase",
        list_endpoint="https://www.cbex.com.cn/onss-api/jsonp/project/search",
        list_query_specs=({"label": "增资扩股", "businessType": "GZ", "include_pre_disclosure": False},),
    ),
    _listing_requirement(
        source_id="cbex",
        business_id="pre_disclosure",
        list_endpoint="https://www.cbex.com.cn/onss-api/jsonp/project/search",
        list_query_specs=(
            {"label": "股权转让(预披露)", "businessType": "JC", "include_pre_disclosure": True},
            {"label": "增资扩股(预披露)", "businessType": "GZ", "include_pre_disclosure": True},
        ),
    ),
    SourceBusinessRequirement(
        source_id="shandong",
        record_family="listing",
        business_id="equity_transfer",
        scope_policy="central_soe_ministry_only",
        required_query_filters=_freeze_filters({"systemSource": "CQY"}),
        list_endpoint="http://www.sdcqjy.com/projlist/xmpd/yqgq",
    ),
    SourceBusinessRequirement(
        source_id="shandong",
        record_family="listing",
        business_id="capital_increase",
        scope_policy="central_soe_ministry_only",
        required_query_filters=_freeze_filters({"systemSource": "CQY"}),
        list_endpoint="http://www.sdcqjy.com/projlist/xmpd/zrzz",
    ),
    SourceBusinessRequirement(
        source_id="guangdong",
        record_family="listing",
        business_id="equity_transfer",
        scope_policy="central_soe_ministry_only",
        required_query_filters=_freeze_filters({"IN_CQLSGX": "'GQ100101'"}),
        list_endpoint="https://new.gduaee.com/si/prjs/equity/list",
    ),
    SourceBusinessRequirement(
        source_id="guangdong",
        record_family="listing",
        business_id="capital_increase",
        scope_policy="central_soe_ministry_only",
        required_query_filters=_freeze_filters({"IN_CQLSGX": "'1C100301'"}),
        list_endpoint="https://new.gduaee.com/si/prjs/capitalincrease/list",
    ),
    SourceBusinessRequirement(
        source_id="shenzhen",
        record_family="listing",
        business_id="equity_transfer",
        scope_policy="central_soe_ministry_only",
        required_query_filters=_freeze_filters(
            {
                "channelIds": ("3226",),
                "targetColumnIds": ("3961",),
                "projectSubjections": ("央属",),
            }
        ),
        list_endpoint="https://www.sotcbb.com/api/v1/sotcbb/local/project/list",
    ),
    SourceBusinessRequirement(
        source_id="shenzhen",
        record_family="listing",
        business_id="capital_increase",
        scope_policy="central_soe_ministry_only",
        required_query_filters=_freeze_filters(
            {
                "channelIds": ("3238",),
                "targetColumnIds": ("3966",),
                "projectSubjections": ("央属",),
            }
        ),
        list_endpoint="https://www.sotcbb.com/api/v1/sotcbb/local/project/list",
    ),
    _listing_requirement(
        source_id="tpre",
        business_id="physical_asset",
        list_endpoint="https://trade.tpre.cn/up/biz/project/anmuas/page",
        list_query_specs=(
            {
                "systemCode": "ENTERPRISE_ASSETS",
                "bizTypeCode": "FORMAL",
                "label": "physical-formal-5000plus",
                "priceBegin": 5000,
            },
        ),
        scope_policy="physical_asset_min_price_5000w",
        required_query_filters={"priceBegin": 5000},
    ),
    _listing_requirement(
        source_id="cquae",
        business_id="physical_asset",
        list_endpoint="https://www.cquae.com/project",
        list_query_specs=(
            {"label": "physical-5000w-to-1y", "q": "s", "projectID": 3, "price": "5000万-1亿"},
            {"label": "physical-over-1y", "q": "s", "projectID": 3, "price": "1亿以上"},
        ),
        scope_policy="physical_asset_min_price_5000w",
        required_query_filters={"price": ("5000万-1亿", "1亿以上")},
    ),
))

_SCOPE_POLICY_DESCRIPTORS: tuple[ScopePolicyDescriptor, ...] = (
    ScopePolicyDescriptor(
        policy_id="central_soe_ministry_only",
        label="央企范围限定",
        summary="仅覆盖中央企业及其所属单位项目。",
    ),
    ScopePolicyDescriptor(
        policy_id="physical_asset_min_price_5000w",
        label="实物资产金额门槛",
        summary="仅覆盖挂牌价不低于 5000 万元的实物资产项目。",
    ),
)

_REQUIREMENTS_BY_KEY = {
    (item.source_id, item.record_family, item.business_id): item
    for item in _SOURCE_BUSINESS_REQUIREMENTS
}


def _build_scope_policy_descriptors_by_id() -> dict[str, ScopePolicyDescriptor]:
    descriptors_by_id: dict[str, ScopePolicyDescriptor] = {}
    for descriptor in _SCOPE_POLICY_DESCRIPTORS:
        policy_id = _normalize_text(descriptor.policy_id)
        if not policy_id:
            raise ValueError("scope policy descriptor must declare a policy_id")
        if policy_id in descriptors_by_id:
            raise ValueError(f"duplicate scope policy descriptor: {policy_id}")
        if not _normalize_text(descriptor.label) or not _normalize_text(descriptor.summary):
            raise ValueError(f"scope policy descriptor is missing display metadata: {policy_id}")
        descriptors_by_id[policy_id] = descriptor

    missing = sorted(
        {
            _normalize_text(requirement.scope_policy)
            for requirement in _SOURCE_BUSINESS_REQUIREMENTS
            if _normalize_text(requirement.scope_policy)
        }
        - set(descriptors_by_id)
    )
    if missing:
        raise ValueError(f"missing scope policy descriptors: {', '.join(missing)}")
    return descriptors_by_id


_SCOPE_POLICY_DESCRIPTORS_BY_ID = _build_scope_policy_descriptors_by_id()

_DEAL_PHYSICAL_WORKBOOK_UNSUPPORTED_REASON = "source_has_no_deal_physical_workbook_sheet"

_EXPORT_WORKBOOK_SUPPORT: tuple[ExportWorkbookSupport, ...] = (
    ExportWorkbookSupport(
        source_id="cbex",
        record_family="deal",
        business_id="deal_physical_asset",
        supported=True,
        sheet_name="北交所资产成交项目",
    ),
    ExportWorkbookSupport(
        source_id="sse",
        record_family="deal",
        business_id="deal_physical_asset",
        supported=True,
        sheet_name="上交所资产成交项目",
    ),
    ExportWorkbookSupport(
        source_id="tpre",
        record_family="deal",
        business_id="deal_physical_asset",
        supported=False,
        unsupported_reason=_DEAL_PHYSICAL_WORKBOOK_UNSUPPORTED_REASON,
    ),
    ExportWorkbookSupport(
        source_id="cquae",
        record_family="deal",
        business_id="deal_physical_asset",
        supported=False,
        unsupported_reason=_DEAL_PHYSICAL_WORKBOOK_UNSUPPORTED_REASON,
    ),
    ExportWorkbookSupport(
        source_id="cbex",
        record_family="deal",
        business_id="deal_equity_transfer",
        supported=True,
        sheet_name="北交所",
    ),
    ExportWorkbookSupport(
        source_id="sse",
        record_family="deal",
        business_id="deal_equity_transfer",
        supported=True,
        sheet_name="上交所",
    ),
    ExportWorkbookSupport(
        source_id="tpre",
        record_family="deal",
        business_id="deal_equity_transfer",
        supported=True,
        sheet_name="天交所",
    ),
    ExportWorkbookSupport(
        source_id="cquae",
        record_family="deal",
        business_id="deal_equity_transfer",
        supported=True,
        sheet_name="重交所",
    ),
    ExportWorkbookSupport(
        source_id="cbex",
        record_family="deal",
        business_id="deal_capital_increase",
        supported=True,
        sheet_name="北交所增资项目",
    ),
    ExportWorkbookSupport(
        source_id="sse",
        record_family="deal",
        business_id="deal_capital_increase",
        supported=True,
        sheet_name="上海联交所增资项目",
    ),
    ExportWorkbookSupport(
        source_id="tpre",
        record_family="deal",
        business_id="deal_capital_increase",
        supported=True,
        sheet_name="天交所增资扩股项目成交",
    ),
    ExportWorkbookSupport(
        source_id="cquae",
        record_family="deal",
        business_id="deal_capital_increase",
        supported=True,
        sheet_name="重交所增资扩股项目成交",
    ),
)

_EXPORT_WORKBOOK_SUPPORT_BY_KEY = {
    (item.source_id, item.record_family, item.business_id): item
    for item in _EXPORT_WORKBOOK_SUPPORT
}

_EXPORT_READINESS_REQUIREMENTS: tuple[ExportReadinessRequirement, ...] = (
    ExportReadinessRequirement(
        record_family="deal",
        business_id="deal_physical_asset",
        requires_deal_price=True,
        deal_date_policy="deal_date_or_collection_date_audit",
        allows_collection_date_audit_fallback=True,
    ),
    ExportReadinessRequirement(
        record_family="deal",
        business_id="deal_equity_transfer",
        requires_deal_price=True,
        deal_date_policy="deal_date_or_collection_date_audit",
        allows_collection_date_audit_fallback=True,
    ),
    ExportReadinessRequirement(
        record_family="deal",
        business_id="deal_capital_increase",
        requires_non_summary_investor=True,
        requires_investor_amount=True,
        deal_date_policy="deal_date_or_collection_date_audit",
        allows_collection_date_audit_fallback=True,
    ),
)

_EXPORT_READINESS_REQUIREMENTS_BY_KEY = {
    (item.record_family, item.business_id): item
    for item in _EXPORT_READINESS_REQUIREMENTS
}

_OPTIONAL_POSTPROCESS_RULE_REQUIREMENTS: tuple[OptionalPostprocessRuleRequirement, ...] = (
    OptionalPostprocessRuleRequirement(
        record_family="listing",
        business_id="physical_asset",
        rule_id="R010_filter_scrap_physical_asset",
        purpose="scrap_disposal_filter",
        optional=True,
        listing_only=True,
    ),
)

_OPTIONAL_POSTPROCESS_RULE_REQUIREMENTS_BY_KEY = {
    (item.record_family, item.business_id, item.rule_id): item
    for item in _OPTIONAL_POSTPROCESS_RULE_REQUIREMENTS
}

_CLASSIFIER_ONLY_DEAL_ROUTE_MARKERS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "sse": ("/notice/deal/",),
        "tpre": ("result-notice",),
    }
)

_CLASSIFIER_CONTENT_DEAL_ROUTE_MARKERS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "sse": ("/deal/",),
        "tpre": ("transaction",),
        "cquae": ("cjgs",),
    }
)

_CLASSIFIER_DEAL_ENDPOINT_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "cbex": ("list_endpoint",),
    }
)


def _route_marker_from_endpoint(endpoint: str) -> str:
    value = _normalize_text(endpoint)
    if not value:
        return ""
    split = urlsplit(value)
    path = split.path or value.split("?", 1)[0]
    return path.lower()


def list_source_business_requirements() -> list[SourceBusinessRequirement]:
    return list(_SOURCE_BUSINESS_REQUIREMENTS)


def list_scope_policy_descriptors() -> list[ScopePolicyDescriptor]:
    return list(_SCOPE_POLICY_DESCRIPTORS)


def get_scope_policy_descriptor(policy_id: str) -> ScopePolicyDescriptor:
    normalized_policy_id = _normalize_text(policy_id)
    try:
        return _SCOPE_POLICY_DESCRIPTORS_BY_ID[normalized_policy_id]
    except KeyError as exc:
        raise KeyError(normalized_policy_id) from exc


def list_source_classifier_route_markers(
    *,
    source_id: str | None = None,
    record_family: str | None = None,
) -> list[SourceClassifierRouteMarkers]:
    normalized_source = _normalize_text(source_id)
    normalized_family = _normalize_text(record_family)
    grouped: dict[tuple[str, str], tuple[list[str], list[str]]] = {}
    for requirement in list_source_business_requirements():
        if normalized_source and requirement.source_id != normalized_source:
            continue
        if normalized_family and requirement.record_family != normalized_family:
            continue
        if requirement.record_family != "deal":
            continue

        key = (requirement.source_id, requirement.record_family)
        markers, _content_markers = grouped.setdefault(key, ([], []))
        endpoint_fields = _CLASSIFIER_DEAL_ENDPOINT_FIELDS.get(
            requirement.source_id,
            (
                "list_endpoint",
                "detail_route",
                "render_page_route",
                "detail_api_endpoint",
                "transferee_details_endpoint",
            ),
        )
        for endpoint in (getattr(requirement, field) for field in endpoint_fields):
            marker = _route_marker_from_endpoint(endpoint)
            if marker and marker not in markers:
                markers.append(marker)

    for supplemental_source, supplemental_markers in _CLASSIFIER_ONLY_DEAL_ROUTE_MARKERS.items():
        if normalized_source and supplemental_source != normalized_source:
            continue
        if normalized_family and normalized_family != "deal":
            continue
        key = (supplemental_source, "deal")
        markers, _content_markers = grouped.setdefault(key, ([], []))
        for marker in supplemental_markers:
            normalized_marker = _route_marker_from_endpoint(marker)
            if normalized_marker and normalized_marker not in markers:
                markers.append(normalized_marker)

    for supplemental_source, supplemental_markers in _CLASSIFIER_CONTENT_DEAL_ROUTE_MARKERS.items():
        if normalized_source and supplemental_source != normalized_source:
            continue
        if normalized_family and normalized_family != "deal":
            continue
        key = (supplemental_source, "deal")
        _markers, content_markers = grouped.setdefault(key, ([], []))
        for marker in supplemental_markers:
            normalized_marker = _route_marker_from_endpoint(marker)
            if normalized_marker and normalized_marker not in content_markers:
                content_markers.append(normalized_marker)

    return [
        SourceClassifierRouteMarkers(
            source_id=item_source_id,
            record_family=item_record_family,
            route_markers=tuple(markers),
            content_route_markers=tuple(content_markers),
        )
        for (item_source_id, item_record_family), (markers, content_markers) in sorted(grouped.items())
    ]


def get_source_business_requirement(
    source_id: str,
    record_family: str,
    business_id: str,
) -> SourceBusinessRequirement:
    key = (_normalize_text(source_id), _normalize_text(record_family), _normalize_text(business_id))
    try:
        return _REQUIREMENTS_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(key) from exc


def list_export_readiness_requirements(
    *,
    record_family: str | None = None,
    business_id: str | None = None,
) -> list[ExportReadinessRequirement]:
    normalized_family = _normalize_text(record_family)
    normalized_business = _normalize_text(business_id)
    return [
        item
        for item in _EXPORT_READINESS_REQUIREMENTS
        if (not normalized_family or item.record_family == normalized_family)
        and (not normalized_business or item.business_id == normalized_business)
    ]


def get_export_readiness_requirement(
    record_family: str,
    business_id: str,
) -> ExportReadinessRequirement:
    key = (_normalize_text(record_family), _normalize_text(business_id))
    try:
        return _EXPORT_READINESS_REQUIREMENTS_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(key) from exc


def list_optional_postprocess_rule_requirements(
    *,
    record_family: str | None = None,
    business_id: str | None = None,
    rule_id: str | None = None,
) -> list[OptionalPostprocessRuleRequirement]:
    normalized_family = _normalize_text(record_family)
    normalized_business = _normalize_text(business_id)
    normalized_rule = _normalize_text(rule_id)
    return [
        item
        for item in _OPTIONAL_POSTPROCESS_RULE_REQUIREMENTS
        if (not normalized_family or item.record_family == normalized_family)
        and (not normalized_business or item.business_id == normalized_business)
        and (not normalized_rule or item.rule_id == normalized_rule)
    ]


def get_optional_postprocess_rule_requirement(
    record_family: str,
    business_id: str,
    rule_id: str,
) -> OptionalPostprocessRuleRequirement:
    key = (_normalize_text(record_family), _normalize_text(business_id), _normalize_text(rule_id))
    try:
        return _OPTIONAL_POSTPROCESS_RULE_REQUIREMENTS_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(key) from exc


def list_export_workbook_support(
    *,
    record_family: str | None = None,
    business_id: str | None = None,
) -> list[ExportWorkbookSupport]:
    normalized_family = _normalize_text(record_family)
    normalized_business = _normalize_text(business_id)
    return [
        item
        for item in _EXPORT_WORKBOOK_SUPPORT
        if (not normalized_family or item.record_family == normalized_family)
        and (not normalized_business or item.business_id == normalized_business)
    ]


def get_export_workbook_support(
    source_id: str,
    record_family: str,
    business_id: str,
) -> ExportWorkbookSupport:
    key = (_normalize_text(source_id), _normalize_text(record_family), _normalize_text(business_id))
    try:
        return _EXPORT_WORKBOOK_SUPPORT_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(key) from exc


__all__ = [
    "ExportReadinessRequirement",
    "ExportWorkbookSupport",
    "OptionalPostprocessRuleRequirement",
    "SourceClassifierRouteMarkers",
    "SourceBusinessRequirement",
    "ScopePolicyDescriptor",
    "get_export_readiness_requirement",
    "get_export_workbook_support",
    "get_optional_postprocess_rule_requirement",
    "get_scope_policy_descriptor",
    "get_source_business_requirement",
    "list_export_readiness_requirements",
    "list_export_workbook_support",
    "list_optional_postprocess_rule_requirements",
    "list_scope_policy_descriptors",
    "list_source_classifier_route_markers",
    "list_source_business_requirements",
    "source_business_requirement_supported_by_catalog",
    "validate_source_business_requirements",
]
