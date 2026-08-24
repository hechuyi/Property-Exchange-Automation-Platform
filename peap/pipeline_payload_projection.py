"""Project mixed parser/standard payloads into pipeline-facing compat fields."""

from __future__ import annotations

import numbers
from typing import Any, Dict, Mapping

from .export_projection import CANONICAL_TO_COMPAT
from .output_contract import (
    KIND_DEAL_CAPITAL,
    clone_field_candidates,
    get_output_columns_for_kind,
    get_structured_export_extra_fields,
)
from .projection_registry import resolve_projection_profile
from .standard_model import build_standard_project
from .streaming_postprocess import is_summary_investor_name

STANDARD_TO_COMPAT = {
    "project_code": "项目编号",
    "project_name": "项目名称",
    "business_type": "项目类型",
    "status": "项目状态",
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
    "profit": "近一年净利润（万）",
    "asset_total": "总资产（万）",
    "share_ratio": "持股比例",
    "capital_company_name": "增资企业名称",
    "total_investment_amount": "投资总金额（万元）",
    "holding_ratio": "持股占比",
    "remark": "备注",
}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _first_value(payload: Mapping[str, Any], fields: list[str]) -> Any:
    for field_name in fields:
        value = payload.get(field_name)
        if _has_value(value):
            return value
    return ""


def _structured_list_value(payload: Mapping[str, Any], fields: tuple[str, ...]) -> list[Any]:
    for field_name in fields:
        value = payload.get(field_name)
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
    return []


def _first_text_value(payload: Mapping[str, Any], fields: tuple[str, ...]) -> str:
    for field_name in fields:
        value = payload.get(field_name)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, numbers.Number):
            text = str(value).strip()
        else:
            continue
        if text:
            return text
    return ""


def _investor_entry_text_value(entry: Any, fields: tuple[str, ...]) -> str:
    if isinstance(entry, Mapping):
        return _first_text_value(entry, fields)
    if "name" in fields and isinstance(entry, str):
        return entry.strip()
    return ""


def _has_export_ready_investor_entries(values: object) -> bool:
    if not isinstance(values, list):
        return False
    for entry in values:
        investor_name = _investor_entry_text_value(
            entry,
            ("name", "投资方名称", "投资方", "投资人", "investor_name"),
        )
        if not investor_name or is_summary_investor_name(investor_name):
            continue
        investor_amount = _investor_entry_text_value(
            entry,
            ("amount", "投资金额（万元）", "投资金额", "投资额", "investment_amount", "investmentAmount"),
        )
        if investor_amount:
            return True
    return False


def _maybe_synthesize_deal_capital_investor(
    payload: Mapping[str, Any],
    *,
    output_kind: str,
    export_extras: Dict[str, Any],
) -> None:
    if output_kind != KIND_DEAL_CAPITAL:
        return
    structured_investors = _structured_list_value(
        payload,
        ("investors", "investorList", "transferee_details", "transfereeDetails"),
    )
    if _has_export_ready_investor_entries(structured_investors):
        return
    if _has_export_ready_investor_entries(export_extras.get("investors")):
        return

    investor_name = _first_text_value(
        payload,
        ("investor_name", "投资方名称", "投资方", "投资人"),
    )
    if not investor_name or is_summary_investor_name(investor_name):
        return
    investor_amount = _first_text_value(
        payload,
        ("investment_amount", "investmentAmount", "投资金额（万元）", "投资金额", "投资额"),
    )
    if not investor_amount:
        return

    investor_ratio = _first_text_value(
        payload,
        ("holding_ratio", "ratio", "持股比例", "持股比例（%）", "投资比例", "持股占比", "持股占比（%）"),
    )
    investor_entry: Dict[str, Any] = {"name": investor_name, "amount": investor_amount}
    if investor_ratio:
        investor_entry["ratio"] = investor_ratio
    export_extras["investors"] = [investor_entry]


def normalize_pipeline_payload(
    raw_payload: Mapping[str, Any] | None,
    *,
    standard_payload: Mapping[str, Any] | None = None,
    project_code: str = "",
    project_name: str = "",
    project_type: str = "",
    status: str = "",
    exchange: str = "",
) -> Dict[str, Any]:
    if raw_payload is not None and not isinstance(raw_payload, Mapping):
        raise TypeError("raw_payload must be a mapping or None")
    if standard_payload is not None and not isinstance(standard_payload, Mapping):
        raise TypeError("standard_payload must be a mapping or None")

    payload = {
        str(key): value
        for key, value in ({} if raw_payload is None else dict(raw_payload)).items()
        if str(key or "").strip()
    }
    if standard_payload is None:
        standard = build_standard_project(payload).to_standard_dict()
    else:
        standard = dict(standard_payload)
    for standard_field, compat_field in STANDARD_TO_COMPAT.items():
        candidate = standard.get(standard_field)
        if _has_value(candidate) and not _has_value(payload.get(compat_field)):
            payload[compat_field] = candidate

    top_level = {
        "项目编号": project_code,
        "项目名称": project_name,
        "项目类型": project_type,
        "项目状态": status,
        "交易所": exchange,
    }
    for field_name, candidate in top_level.items():
        if _has_value(candidate) and not _has_value(payload.get(field_name)):
            payload[field_name] = candidate

    return {
        field_name: value
        for field_name, value in payload.items()
        if _has_value(value)
    }


def build_export_extras_from_payload(
    raw_payload: Mapping[str, Any] | None,
    *,
    record_family: str,
    project_type: str = "",
    business_id: str = "",
) -> Dict[str, Any]:
    normalized_payload = normalize_pipeline_payload(
        raw_payload,
        project_type=project_type,
    )
    profile = resolve_projection_profile(
        record_family,
        business_id or project_type or str(normalized_payload.get("项目类型") or ""),
    )
    if profile is None:
        return {}

    field_candidates = clone_field_candidates().get(profile.output_kind, {})
    canonical_columns = {"ID", *CANONICAL_TO_COMPAT.values()}
    export_extras: Dict[str, Any] = {}
    for column_name in get_output_columns_for_kind(profile.output_kind):
        if column_name in canonical_columns:
            continue
        candidate = _first_value(
            normalized_payload,
            list(field_candidates.get(column_name) or [column_name]),
        )
        if _has_value(candidate):
            export_extras[column_name] = candidate

    structured_candidates = {
        "investors": ("investors", "investorList", "transferee_details", "transfereeDetails"),
        "transferors": ("transferors", "transferorNames"),
        "financing_party_names": ("financing_party_names", "financingPartyNames"),
        "project_parties": ("project_parties", "projectParties", "partyList"),
    }
    for key in get_structured_export_extra_fields(profile.output_kind):
        aliases = structured_candidates.get(key)
        if not aliases:
            continue
        values = _structured_list_value(normalized_payload, aliases)
        if values:
            export_extras[key] = list(values)
    _maybe_synthesize_deal_capital_investor(
        normalized_payload,
        output_kind=profile.output_kind,
        export_extras=export_extras,
    )
    return export_extras


__all__ = [
    "build_export_extras_from_payload",
    "normalize_pipeline_payload",
]
