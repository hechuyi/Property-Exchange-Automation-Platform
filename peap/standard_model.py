"""Standard field model for parser outputs."""

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, fields
from typing import Any, Dict, List, Mapping, Optional

from .constants import KEY_IS_PRE_DISCLOSURE, KEY_LISTING_TIMES, KEY_PROJECT_TYPE, KEY_STATUS

FIELD_ALIASES = {
    "project_code": ["项目编号"],
    "project_name": ["项目名称", "标的名称"],
    "business_type": [KEY_PROJECT_TYPE],
    "status": [KEY_STATUS],
    "exchange": ["交易所"],
    "source_type": ["类型"],
    "seller": ["转让方", "融资方"],
    "deal_date": ["成交日期", "dealDate", "CJRQ", "contractSignTime", "contract_sign_time"],
    "deal_price": ["交易价格", "交易价格（万元）", "成交金额", "成交价格", "成交价", "dealAmount", "dealPrice", "transactionPrice", "CJJG", "cjjg"],
    "deal_method": ["交易方式"],
    "buyer_name": ["受让方名称"],
    "auction_flag": ["是否竞价"],
    "deal_status": ["是否成交"],
    "investor_name": ["投资方名称", "增资投资方", "投资方", "投资人", "TZFMC"],
    "investment_amount": ["投资金额（万元）", "投资金额", "投资额", "investmentAmount"],
    "capital_company_name": ["增资企业名称", "融资方", "融资方名称", "ZZFQYMC"],
    "total_investment_amount": [
        "投资总金额（万元）",
        "投资总金额",
        "融资金额",
        "融资金额（万元）",
        "totalInvestmentAmount",
        "totalFinancingAmount",
        "totalActualContribution",
        "ZJCZE",
    ],
    "holding_ratio": ["持股占比", "持股占比（%）", "holdingRatio", "stockPercentTotal", "ZZZHBL"],
    "group_name": ["隶属集团"],
    "industry": ["所属行业", "资产类别"],
    "region": ["所在地区"],
    "contact": ["经办人"],
    "agency": ["受托机构"],
    "price": ["挂牌价格", "挂牌价格（万）", "挂牌价格（万元）", "融资金额", "融资金额（万）", "成交金额"],
    "valuation": [
        "转让标的评估值",
        "转让标的评估结果",
        "转让标的评估值或账面净值",
        "评估值",
        "valuationValue",
        "assessmentValue",
        "DWPGZ",
        "PGZ",
        "DJPGZ",
    ],
    "reserve_price": ["转让底价", "转让底价（万元）", "挂牌底价", "reservePrice", "transferBasePrice", "ZRDJ", "ZRDANJ"],
    "start_date": ["挂牌开始日期", "预披露开始日期", "披露开始日期"],
    "end_date": ["挂牌截止日期", "预披露截止日期", "披露截止日期", "成交日期"],
    "profit": ["近一年净利润", "近一年净利润（万）"],
    "asset_total": ["总资产", "总资产（万）"],
    "share_ratio": ["持股比例", "持股比例（%）", "投资比例", "shareRatio", "stockPercent"],
    "listing_times": [KEY_LISTING_TIMES],
    "is_pre_disclosure": [KEY_IS_PRE_DISCLOSURE],
    "remark": ["备注"],
}


def _pick_value(raw: Mapping[str, Any], aliases: List[str]) -> Any:
    for key in aliases:
        if key in raw and raw.get(key) not in (None, ""):
            return raw.get(key)
    return None


def _normalize_standard_project(result: "StandardProject") -> "StandardProject":
    result.project_code = str(result.project_code or "")
    result.project_name = str(result.project_name or "")
    result.business_type = str(result.business_type or "")
    result.status = str(result.status or "")
    result.exchange = str(result.exchange or "")
    result.source_type = str(result.source_type or "")
    result.seller = str(result.seller or "")
    result.deal_date = str(result.deal_date or "")
    result.deal_price = str(result.deal_price or "")
    result.deal_method = str(result.deal_method or "")
    result.buyer_name = str(result.buyer_name or "")
    result.auction_flag = str(result.auction_flag or "")
    result.deal_status = str(result.deal_status or "")
    result.investor_name = str(result.investor_name or "")
    result.investment_amount = str(result.investment_amount or "")
    result.capital_company_name = str(result.capital_company_name or "")
    result.total_investment_amount = str(result.total_investment_amount or "")
    result.holding_ratio = str(result.holding_ratio or "")
    result.group_name = str(result.group_name or "")
    result.industry = str(result.industry or "")
    result.region = str(result.region or "")
    result.contact = str(result.contact or "")
    result.agency = str(result.agency or "")
    result.start_date = str(result.start_date or "")
    result.end_date = str(result.end_date or "")
    result.share_ratio = str(result.share_ratio or "")
    result.remark = str(result.remark or "")
    result.is_pre_disclosure = bool(result.is_pre_disclosure)
    return result


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, MappingABC):
        raise TypeError(f"{name} must be a mapping, got {type(value).__name__}")
    return value


@dataclass
class StandardProject:
    project_code: str = ""
    project_name: str = ""
    business_type: str = ""
    status: str = ""
    exchange: str = ""
    source_type: str = ""
    seller: str = ""
    deal_date: str = ""
    deal_price: Any = None
    deal_method: str = ""
    buyer_name: str = ""
    auction_flag: str = ""
    deal_status: str = ""
    investor_name: str = ""
    investment_amount: str = ""
    capital_company_name: str = ""
    total_investment_amount: str = ""
    holding_ratio: str = ""
    group_name: str = ""
    industry: str = ""
    region: str = ""
    contact: str = ""
    agency: str = ""
    price: Any = None
    valuation: Any = None
    reserve_price: Any = None
    start_date: str = ""
    end_date: str = ""
    profit: Any = None
    asset_total: Any = None
    share_ratio: str = ""
    listing_times: Optional[int] = None
    is_pre_disclosure: bool = False
    remark: str = ""
    raw: Optional[Dict[str, Any]] = None

    def to_standard_dict(self) -> Dict[str, Any]:
        return {field_name: getattr(self, field_name) for field_name in STANDARD_PROJECT_FIELD_NAMES}


STANDARD_PROJECT_FIELD_NAMES = frozenset(
    field.name for field in fields(StandardProject) if field.name != "raw"
)
STANDARD_ROUTING_FIELDS = frozenset({"business_type", "status", "is_pre_disclosure"})


def hydrate_standard_project(
    payload: Mapping[str, Any],
    *,
    raw: Optional[Mapping[str, Any]] = None,
) -> StandardProject:
    safe_raw = {} if raw is None else dict(_require_mapping(raw, "raw"))
    result = StandardProject(raw=safe_raw)
    safe_payload = dict(_require_mapping(payload, "payload"))
    for field_name in STANDARD_PROJECT_FIELD_NAMES:
        if field_name in safe_payload:
            setattr(result, field_name, safe_payload[field_name])
    return _normalize_standard_project(result)


def build_standard_project(raw: Mapping[str, Any]) -> StandardProject:
    safe_raw = dict(_require_mapping(raw, "raw"))
    result = StandardProject(raw=safe_raw)
    for field_name, aliases in FIELD_ALIASES.items():
        value = _pick_value(safe_raw, [field_name, *aliases])
        if value is None:
            continue
        setattr(result, field_name, value)
    if str(result.business_type or "").strip() == "增资扩股":
        financing_value = _pick_value(safe_raw, ["price", "融资金额", "融资金额（万）"])
        if financing_value not in (None, ""):
            result.price = financing_value
    return _normalize_standard_project(result)
