"""Output mapping layer from standard model to excel payload."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from .constants import KEY_IS_PRE_DISCLOSURE, KEY_PROJECT_TYPE, KEY_STATUS
from .output_contract import (
    BASE_OUTPUT_COLUMNS,
    KIND_CAPITAL,
    KIND_DEAL_CAPITAL,
    KIND_DEAL_EQUITY,
    KIND_DEAL_PHYSICAL,
    KIND_EQUITY,
    KIND_PHYSICAL,
    KIND_PRE,
    KIND_PUBLIC_RESOURCE,
    detect_output_kind,
)
from .standard_model import STANDARD_PROJECT_FIELD_NAMES, StandardProject

if TYPE_CHECKING:
    from .parsing import ParsedProject

OUTPUT_FIELD_MAP = {
    KIND_EQUITY: {
        "类型": "source_type",
        "项目编号": "project_code",
        "隶属集团": "group_name",
        "转让方": "seller",
        "项目名称": "project_name",
        "挂牌价格": "price",
        "所属行业": "industry",
        "挂牌开始日期": "start_date",
        "挂牌截止日期": "end_date",
        "受托机构": "agency",
        "交易所": "exchange",
        "经办人": "contact",
        "近一年净利润（万）": "profit",
        "所在地区": "region",
        "挂牌次数": "listing_times",
        "备注": "remark",
    },
    KIND_PHYSICAL: {
        "类型": "source_type",
        "项目编号": "project_code",
        "隶属集团": "group_name",
        "转让方": "seller",
        "项目名称": "project_name",
        "挂牌价格（万元）": "price",
        "资产类别": "industry",
        "挂牌开始日期": "start_date",
        "挂牌截止日期": "end_date",
        "受托机构": "agency",
        "交易所": "exchange",
        "经办人": "contact",
        "挂牌次数": "listing_times",
        "备注": "remark",
    },
    KIND_CAPITAL: {
        "项目编号": "project_code",
        "隶属集团": "group_name",
        "融资方": "seller",
        "项目名称": "project_name",
        "融资金额": "price",
        "持股比例": "share_ratio",
        "所属行业": "industry",
        "披露开始日期": "start_date",
        "披露截止日期": "end_date",
        "受托机构": "agency",
        "交易所": "exchange",
        "经办人": "contact",
        "近一年净利润（万）": "profit",
        "所在地区": "region",
        "备注": "remark",
    },
    KIND_PRE: {
        "类型": "source_type",
        "项目编号": "project_code",
        "隶属集团": "group_name",
        "转让方": "seller",
        "项目名称": "project_name",
        "所属行业": "industry",
        "披露开始日期": "start_date",
        "披露截止日期": "end_date",
        "受托机构": "agency",
        "交易所": "exchange",
        "经办人": "contact",
        "近一年净利润（万）": "profit",
        "总资产（万）": "asset_total",
        "挂牌次数": "listing_times",
        "备注": "remark",
    },
    KIND_PUBLIC_RESOURCE: {
        "交易所": "exchange",
        "项目编号": "project_code",
        "项目名称": "project_name",
        "交易方式": "deal_method",
        "受让方名称": "buyer_name",
        "转让标的评估值": "valuation",
        "成交金额": "price",
        "成交日期": "end_date",
        "备注": "remark",
    },
}

OUTPUT_FIELD_MAP[KIND_DEAL_EQUITY] = {
    "项目编号": "project_code",
    "项目名称": "project_name",
    "标的名称": "project_name",
    "转让标的评估结果": "valuation",
    "转让标的评估值": "valuation",
    "转让底价（万元）": "reserve_price",
    "转让底价": "reserve_price",
    "交易价格（万元）": "deal_price",
    "交易价格": "deal_price",
    "成交日期": "deal_date",
    "交易方式": "deal_method",
    "受让方名称": "buyer_name",
    "备注": "remark",
    "是否竞价": "auction_flag",
    "是否成交": "deal_status",
}
OUTPUT_FIELD_MAP[KIND_DEAL_PHYSICAL] = {
    "项目编号": "project_code",
    "标的名称": "project_name",
    "转让标的评估结果": "valuation",
    "转让底价": "reserve_price",
    "交易价格": "deal_price",
    "成交日期": "deal_date",
    "备注": "remark",
}
OUTPUT_FIELD_MAP[KIND_DEAL_CAPITAL] = {
    "项目编号": "project_code",
    "项目名称": "project_name",
    "标的名称": "project_name",
    "增资企业名称": "capital_company_name",
    "成交日期": "deal_date",
    "投资方名称": "investor_name",
    "投资金额（万元）": "investment_amount",
    "持股比例": "share_ratio",
    "投资总金额（万元）": "total_investment_amount",
    "持股占比": "holding_ratio",
    "备注": "remark",
}

ROUTING_FIELD_MAP = {
    KEY_STATUS: "status",
    KEY_PROJECT_TYPE: "business_type",
    KEY_IS_PRE_DISCLOSURE: "is_pre_disclosure",
}


def validate_output_field_map() -> List[str]:
    errors: List[str] = []
    for kind, field_map in OUTPUT_FIELD_MAP.items():
        if not field_map:
            errors.append(f"output field map is empty: {kind}")
            continue
        for output_field, standard_field in field_map.items():
            if not output_field:
                errors.append(f"output field name is empty: {kind}")
            if standard_field not in STANDARD_PROJECT_FIELD_NAMES:
                errors.append(
                    f"unknown standard field in output map: kind={kind}, field={output_field}, standard={standard_field}"
                )
        expected_columns = set(BASE_OUTPUT_COLUMNS.get(kind, ())) - {"ID"}
        mapped_columns = set(field_map)
        missing_columns = sorted(expected_columns - mapped_columns)
        extra_columns = sorted(mapped_columns - expected_columns)
        if missing_columns:
            errors.append(
                f"output field map misses workbook columns: kind={kind}, columns={missing_columns}"
            )
        if extra_columns:
            errors.append(
                f"output field map contains unknown workbook columns: kind={kind}, columns={extra_columns}"
            )

    return errors


_OUTPUT_FIELD_MAP_ERRORS = validate_output_field_map()
if _OUTPUT_FIELD_MAP_ERRORS:
    raise RuntimeError("; ".join(_OUTPUT_FIELD_MAP_ERRORS))


def get_output_mapping_contract() -> Dict[str, Dict[str, str]]:
    return {kind: dict(field_map) for kind, field_map in OUTPUT_FIELD_MAP.items()}


def _resolve_standard_project(project: StandardProject | ParsedProject) -> StandardProject:
    # Keep the parser orchestration layer out of this module's import graph.
    from .parsing import ParsedProject

    if isinstance(project, ParsedProject):
        return project.standard_record
    return project


def map_standard_to_excel_payload(
    project: StandardProject | ParsedProject,
    target_file: str,
) -> Dict[str, Any]:
    """Convert a parsed or standard project record to the excel output payload."""
    kind = detect_output_kind(target_file)
    field_map = OUTPUT_FIELD_MAP[kind]
    standard = _resolve_standard_project(project)
    standard_data = standard.to_standard_dict()

    mapped: Dict[str, Any] = {}
    for output_field, standard_field in field_map.items():
        value = standard_data.get(standard_field)
        if value in (None, ""):
            continue
        mapped[output_field] = value

    for output_field, standard_field in ROUTING_FIELD_MAP.items():
        mapped[output_field] = getattr(standard, standard_field)

    return mapped
