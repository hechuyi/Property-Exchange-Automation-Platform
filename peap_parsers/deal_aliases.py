"""Shared alias helpers for deal parsers."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from peap.output_contract import KIND_DEAL_CAPITAL, KIND_DEAL_EQUITY, KIND_DEAL_PHYSICAL
from peap.standard_model import FIELD_ALIASES

_DEAL_OUTPUT_KINDS = (KIND_DEAL_EQUITY, KIND_DEAL_PHYSICAL, KIND_DEAL_CAPITAL)
_DEAL_OUTPUT_ALIASES: dict[str, tuple[str, ...]] | None = None
_DEAL_SOURCE_ALIASES: dict[str, tuple[str, ...]] = {
    "project_code": ("projectCode", "project_code", "projectcode", "ProjectNo", "xmbh", "XMBH", "项目编号"),
    "project_name": ("projectName", "project_name", "projectname", "ProjectName", "xmmc", "XMMC", "title", "项目名称", "标的名称"),
    "business_type": ("bizType", "businessType", "business_type", "xmlx", "XMLX", "业务类型", "项目类型"),
    "deal_date": ("contractSignTime", "dealDate", "deal_date", "cjrq", "CJRQ", "成交日期"),
    "collection_date": ("collectionDate", "collection_date", "publishDate", "fbsj", "FBSJ", "采集日期", "发布日期"),
    "deal_price": ("transactionPrice", "dealAmount", "dealPrice", "deal_price", "cjjg", "CJJG", "成交金额", "成交价格", "成交价"),
    "valuation": (
        "assessmentValue",
        "valuationValue",
        "valuation",
        "pgjz",
        "PGJZ",
        "DWPGZ",
        "PGZ",
        "DJPGZ",
        "评估值",
        "转让标的评估值",
        "转让标的评估结果",
    ),
    "reserve_price": (
        "transferBasePrice",
        "reservePrice",
        "reserve_price",
        "zrdf",
        "ZRDF",
        "ZRDJ",
        "ZRDANJ",
        "转让底价",
        "转让底价（万元）",
        "挂牌底价",
    ),
    "project_parties": ("projectParties", "project_parties", "partyList"),
    "party_label": ("label", "role", "type", "partyType", "参与方类型", "参与方类别", "角色", "类型"),
    "party_name": ("name", "partyName", "projectPartyName", "参与方名称", "名称", "企业名称"),
    "transferors": ("transferorNames", "transferors"),
    "financing_party_names": ("financingPartyNames", "financing_party_names", "ZZFQYMC", "融资方", "融资方名称", "增资企业名称"),
    "capital_company_name": (
        "capitalIncreaseCompanyName",
        "capital_company_name",
        "capital_increase_company_name",
        "ZZFQYMC",
        "增资企业名称",
        "融资方",
        "融资方名称",
    ),
    "investors": ("investorList", "investors", "transferee_details", "transfereeDetails"),
    "investor_name": (
        "name",
        "investorName",
        "investor",
        "transfereeName",
        "transferee",
        "TZFMC",
        "投资方名称",
        "增资投资方",
        "投资方",
        "投资人",
        "受让方名称",
        "受让方",
        "value",
    ),
    "investment_amount": (
        "amount",
        "investmentAmount",
        "investment_amount",
        "dealAmount",
        "transfereeAmount",
        "subscriptionAmount",
        "认购金额",
        "投资金额（万元）",
        "投资金额",
        "投资额",
    ),
    "actual_contribution": ("actualContribution", "actual_contribution", "实际出资额", "实际出资金额"),
    "share_ratio": ("ratio", "shareRatio", "share_ratio", "stockPercent", "holdingRatio", "持股比例", "持股比例（%）", "投资比例"),
    "total_investment_amount": (
        "ZJCZE",
        "totalInvestmentAmount",
        "totalFinancingAmount",
        "totalActualContribution",
        "投资总金额（万元）",
        "投资总金额",
        "融资金额",
        "融资金额（万元）",
    ),
    "holding_ratio": ("ZZZHBL", "holdingRatio", "stockPercentTotal", "持股占比", "持股占比（%）"),
    "remark": ("remark", "备注"),
    "deal_method": ("dealMethod", "deal_method", "transactionMethod", "transaction_method", "交易方式"),
    "buyer_name": ("buyerName", "buyer_name", "transfereeName", "transferee", "受让方名称", "受让方"),
    "auction_flag": ("isAuction", "is_auction", "auctionFlag", "sfjj", "SFJJ", "是否竞价"),
    "deal_status": ("isDeal", "dealStatus", "deal_status", "sf_cj", "SFCJ", "是否成交"),
}


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _deal_output_aliases_by_standard_field() -> dict[str, tuple[str, ...]]:
    from peap.output_mapping import OUTPUT_FIELD_MAP

    aliases: dict[str, list[str]] = {}
    for kind in _DEAL_OUTPUT_KINDS:
        for output_field, standard_field in OUTPUT_FIELD_MAP.get(kind, {}).items():
            aliases.setdefault(standard_field, []).append(output_field)
    return {field_name: _ordered_unique(values) for field_name, values in aliases.items()}


def _deal_output_aliases() -> dict[str, tuple[str, ...]]:
    global _DEAL_OUTPUT_ALIASES
    if _DEAL_OUTPUT_ALIASES is None:
        _DEAL_OUTPUT_ALIASES = _deal_output_aliases_by_standard_field()
    return _DEAL_OUTPUT_ALIASES


def deal_field_aliases(field_name: str, source_aliases: Iterable[str] = ()) -> tuple[str, ...]:
    """Return parser aliases from canonical, standard-model, output, then source names."""
    return _ordered_unique(
        (
            field_name,
            *FIELD_ALIASES.get(field_name, ()),
            *_deal_output_aliases().get(field_name, ()),
            *_DEAL_SOURCE_ALIASES.get(field_name, ()),
            *source_aliases,
        )
    )


def normalize_field_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("(", "（").replace(")", "）")
    text = re.sub(r"\s+", "", text)
    return text.strip(":：;；,，").lower()


def lookup_by_alias(mapping: Mapping[str, Any], aliases: Iterable[str]) -> tuple[Any, str]:
    alias_tuple = tuple(str(alias or "").strip() for alias in aliases if str(alias or "").strip())
    for alias in alias_tuple:
        if alias in mapping:
            return mapping[alias], alias

    normalized_aliases = {normalize_field_key(alias): alias for alias in alias_tuple}
    for key, value in mapping.items():
        normalized_key = normalize_field_key(key)
        if normalized_key in normalized_aliases:
            return value, normalized_aliases[normalized_key]
    return None, ""


__all__ = ["deal_field_aliases", "lookup_by_alias", "normalize_field_key"]
