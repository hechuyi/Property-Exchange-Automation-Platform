"""Output mapping layer from standard model to excel payload."""

from typing import Any, Dict, FrozenSet, List

from .constants import KEY_IS_PRE_DISCLOSURE, KEY_PROJECT_TYPE, KEY_STATUS
from .output_contract import (
    KIND_CAPITAL,
    KIND_EQUITY,
    KIND_PHYSICAL,
    KIND_PRE,
    KIND_PUBLIC_RESOURCE,
    detect_output_kind,
)
from .parsing import ParsedProject
from .standard_model import STANDARD_PROJECT_FIELD_NAMES, StandardProject

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
        "近一年净利润": "profit",
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
        "挂牌价格": "price",
        "所属行业": "industry",
        "挂牌开始日期": "start_date",
        "挂牌截止日期": "end_date",
        "受托机构": "agency",
        "交易所": "exchange",
        "经办人": "contact",
        "挂牌次数": "listing_times",
        "备注": "remark",
    },
    KIND_CAPITAL: {
        "类型": "source_type",
        "项目编号": "project_code",
        "隶属集团": "group_name",
        "融资方": "seller",
        "项目名称": "project_name",
        "融资金额（万）": "price",
        "持股比例": "share_ratio",
        "所属行业": "industry",
        "披露开始日期": "start_date",
        "披露截止日期": "end_date",
        "受托机构": "agency",
        "交易所": "exchange",
        "经办人": "contact",
        "近一年净利润": "profit",
        "所在地区": "region",
        "挂牌次数": "listing_times",
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
        "近一年净利润": "profit",
        "总资产": "asset_total",
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
    },
}

ROUTING_FIELD_MAP = {
    KEY_STATUS: "status",
    KEY_PROJECT_TYPE: "project_type",
    KEY_IS_PRE_DISCLOSURE: "is_pre_disclosure",
}

# No output columns should rely on implicit raw passthrough now.
LEGACY_RAW_FALLBACK_FIELDS: Dict[str, FrozenSet[str]] = {}


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

    unknown_raw_fallback_kinds = sorted(set(LEGACY_RAW_FALLBACK_FIELDS) - set(OUTPUT_FIELD_MAP))
    if unknown_raw_fallback_kinds:
        errors.append(f"raw fallback declared for unknown kinds: {unknown_raw_fallback_kinds}")
    return errors


_OUTPUT_FIELD_MAP_ERRORS = validate_output_field_map()
if _OUTPUT_FIELD_MAP_ERRORS:
    raise RuntimeError("; ".join(_OUTPUT_FIELD_MAP_ERRORS))


def get_output_mapping_contract() -> Dict[str, Dict[str, str]]:
    return {kind: dict(field_map) for kind, field_map in OUTPUT_FIELD_MAP.items()}


def get_raw_fallback_contract() -> Dict[str, List[str]]:
    return {
        kind: sorted(field_names)
        for kind, field_names in LEGACY_RAW_FALLBACK_FIELDS.items()
    }


def _resolve_standard_project(project: StandardProject | ParsedProject) -> StandardProject:
    if isinstance(project, ParsedProject):
        return project.standard_record
    return project


def _resolve_compat_payload(
    project: StandardProject | ParsedProject,
    *,
    include_raw_compat: bool,
) -> Dict[str, Any]:
    if isinstance(project, ParsedProject):
        return project.to_compat_payload(include_raw=include_raw_compat)
    return project.to_legacy_payload(include_raw=include_raw_compat)


def map_standard_to_excel_payload(
    project: StandardProject | ParsedProject,
    target_file: str,
    *,
    include_raw_compat: bool = True,
) -> Dict[str, Any]:
    """Convert a parsed or standard project record to the legacy excel payload."""
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

    compatible = _resolve_compat_payload(project, include_raw_compat=include_raw_compat)
    compatible.update(mapped)
    return compatible
