"""Standard field model for parser outputs."""

from dataclasses import dataclass, fields
from typing import Any, Dict, List, Mapping, Optional

from .constants import KEY_IS_PRE_DISCLOSURE, KEY_LISTING_TIMES, KEY_PROJECT_TYPE, KEY_STATUS

FIELD_ALIASES = {
    "project_code": ["项目编号"],
    "project_name": ["项目名称"],
    "project_type": [KEY_PROJECT_TYPE],
    "status": [KEY_STATUS],
    "exchange": ["交易所"],
    "source_type": ["类型"],
    "seller": ["转让方", "融资方"],
    "deal_method": ["交易方式"],
    "buyer_name": ["受让方名称"],
    "group_name": ["隶属集团"],
    "industry": ["所属行业", "资产类别"],
    "region": ["所在地区"],
    "contact": ["经办人"],
    "agency": ["受托机构"],
    "price": ["挂牌价格", "挂牌价格（万）", "挂牌价格（万元）", "融资金额", "融资金额（万）", "成交金额"],
    "valuation": ["转让标的评估值", "转让标的评估值或账面净值"],
    "start_date": ["挂牌开始日期", "预披露开始日期", "披露开始日期"],
    "end_date": ["挂牌截止日期", "预披露截止日期", "披露截止日期", "成交日期"],
    "profit": ["近一年净利润", "近一年净利润（万）"],
    "asset_total": ["总资产", "总资产（万）"],
    "share_ratio": ["持股比例"],
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
    result.project_type = str(result.project_type or "")
    result.status = str(result.status or "")
    result.exchange = str(result.exchange or "")
    result.source_type = str(result.source_type or "")
    result.seller = str(result.seller or "")
    result.deal_method = str(result.deal_method or "")
    result.buyer_name = str(result.buyer_name or "")
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


@dataclass
class StandardProject:
    project_code: str = ""
    project_name: str = ""
    project_type: str = ""
    status: str = ""
    exchange: str = ""
    source_type: str = ""
    seller: str = ""
    deal_method: str = ""
    buyer_name: str = ""
    group_name: str = ""
    industry: str = ""
    region: str = ""
    contact: str = ""
    agency: str = ""
    price: Any = None
    valuation: Any = None
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

    def to_legacy_payload(self, *, include_raw: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "项目编号": self.project_code,
            "项目名称": self.project_name,
            "项目类型": self.project_type,
            "状态": self.status,
            "交易所": self.exchange,
            "类型": self.source_type,
            "转让方": self.seller,
            "交易方式": self.deal_method,
            "受让方名称": self.buyer_name,
            "隶属集团": self.group_name,
            "所属行业": self.industry,
            "所在地区": self.region,
            "经办人": self.contact,
            "受托机构": self.agency,
            "挂牌价格": self.price,
            "融资方": self.seller,
            "融资金额": self.price,
            "融资金额（万）": self.price,
            "转让标的评估值": self.valuation,
            "成交金额": self.price,
            "挂牌开始日期": self.start_date,
            "挂牌截止日期": self.end_date,
            "预披露开始日期": self.start_date,
            "预披露截止日期": self.end_date,
            "披露开始日期": self.start_date,
            "披露截止日期": self.end_date,
            "成交日期": self.end_date,
            "近一年净利润": self.profit,
            "近一年净利润（万）": self.profit,
            "总资产": self.asset_total,
            "总资产（万）": self.asset_total,
            "持股比例": self.share_ratio,
            "挂牌次数": self.listing_times,
            "是否预披露": self.is_pre_disclosure,
            "备注": self.remark,
        }
        if include_raw and self.raw:
            merged = dict(self.raw)
            merged.update({key: value for key, value in payload.items() if value not in (None, "")})
            return merged
        return payload


STANDARD_PROJECT_FIELD_NAMES = frozenset(
    field.name for field in fields(StandardProject) if field.name != "raw"
)
STANDARD_ROUTING_FIELDS = frozenset({"project_type", "status", "is_pre_disclosure"})
LEGACY_PAYLOAD_KEYS = frozenset(StandardProject().to_legacy_payload().keys())


def hydrate_standard_project(
    payload: Mapping[str, Any],
    *,
    raw: Optional[Mapping[str, Any]] = None,
) -> StandardProject:
    safe_payload = dict(payload or {})
    result = StandardProject(raw=dict(raw or {}))
    for field_name in STANDARD_PROJECT_FIELD_NAMES:
        if field_name in safe_payload:
            setattr(result, field_name, safe_payload[field_name])
    return _normalize_standard_project(result)


def build_standard_project(raw: Mapping[str, Any]) -> StandardProject:
    safe_raw = dict(raw or {})
    result = StandardProject(raw=safe_raw)
    for field_name, aliases in FIELD_ALIASES.items():
        value = _pick_value(safe_raw, aliases)
        if value is None:
            continue
        setattr(result, field_name, value)
    if str(result.project_type or "").strip() == "增资扩股":
        financing_value = _pick_value(safe_raw, ["融资金额", "融资金额（万）"])
        if financing_value not in (None, ""):
            result.price = financing_value
    return _normalize_standard_project(result)
