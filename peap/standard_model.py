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
        """Project standard fields to legacy compat (Chinese) field names."""
        # Direct field projection - no external compat_payload dependency
        compat_mapping = {
            "project_code": "项目编号",
            "project_name": "项目名称",
            "project_type": "项目类型",
            "status": "状态",
            "exchange": "交易所",
            "source_type": "类型",
            "seller": "转让方",
            "deal_method": "交易方式",
            "buyer_name": "受让方名称",
            "group_name": "隶属集团",
            "industry": "所属行业",
            "region": "所在地区",
            "contact": "经办人",
            "agency": "受托机构",
            "price": "挂牌价格",
            "valuation": "转让标的评估值",
            "start_date": "挂牌开始日期",
            "end_date": "挂牌截止日期",
            "profit": "近一年净利润",
            "asset_total": "总资产",
            "share_ratio": "持股比例",
            "listing_times": "挂牌次数",
            "is_pre_disclosure": "是否预披露",
            "remark": "备注",
        }
        payload = {}
        for standard_field, compat_field in compat_mapping.items():
            value = getattr(self, standard_field, None)
            if value is not None and value != "":
                payload[compat_field] = value
        return payload


STANDARD_PROJECT_FIELD_NAMES = frozenset(
    field.name for field in fields(StandardProject) if field.name != "raw"
)
STANDARD_ROUTING_FIELDS = frozenset({"project_type", "status", "is_pre_disclosure"})

# Legacy compat keys - defined directly to avoid compat_payload dependency
# These are the Chinese field names used in legacy output
LEGACY_PAYLOAD_KEYS = frozenset({
    "项目编号", "项目名称", "项目类型", "状态", "交易所", "类型", "转让方",
    "交易方式", "受让方名称", "隶属集团", "所属行业", "所在地区", "经办人",
    "受托机构", "挂牌价格", "融资方", "融资金额", "融资金额（万）",
    "转让标的评估值", "成交金额", "挂牌开始日期", "挂牌截止日期",
    "预披露开始日期", "预披露截止日期", "披露开始日期", "披露截止日期",
    "成交日期", "近一年净利润", "近一年净利润（万）", "总资产", "总资产（万）",
    "持股比例", "挂牌次数", "是否预披露", "备注",
})


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
