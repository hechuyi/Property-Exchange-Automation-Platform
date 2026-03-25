"""Shared output contract definitions for excel writers and exporters."""

from typing import Dict, List

from .constants import KEY_IS_PRE_DISCLOSURE, KEY_PROJECT_TYPE, KEY_STATUS

KIND_PRE = "pre_disclosure"
KIND_PHYSICAL = "physical_asset"
KIND_CAPITAL = "capital_increase"
KIND_EQUITY = "equity_transfer"
KIND_PUBLIC_RESOURCE = "public_resource_deals"

PUBLIC_RESOURCE_OUTPUT_STEM = "公共资源网四大交易所股权转让成交信息统计"
PUBLIC_RESOURCE_OUTPUT_FILENAME = f"{PUBLIC_RESOURCE_OUTPUT_STEM}.xlsx"

DEFAULT_INTERNAL_KEYS = {KEY_STATUS, KEY_PROJECT_TYPE, KEY_IS_PRE_DISCLOSURE}

OUTPUT_KIND_MARKERS = (
    (KIND_PUBLIC_RESOURCE, PUBLIC_RESOURCE_OUTPUT_STEM),
    (KIND_PRE, "预披露"),
    (KIND_PHYSICAL, "实物资产"),
    (KIND_CAPITAL, "增资扩股"),
)

BASE_OUTPUT_COLUMNS: Dict[str, List[str]] = {
    KIND_PRE: [
        "ID",
        "类型",
        "项目编号",
        "隶属集团",
        "转让方",
        "项目名称",
        "所属行业",
        "披露开始日期",
        "披露截止日期",
        "受托机构",
        "交易所",
        "经办人",
        "近一年净利润（万）",
        "总资产（万）",
        "挂牌次数",
        "备注",
    ],
    KIND_PHYSICAL: [
        "ID",
        "类型",
        "项目编号",
        "隶属集团",
        "转让方",
        "项目名称",
        "挂牌价格",
        "所属行业",
        "挂牌开始日期",
        "挂牌截止日期",
        "受托机构",
        "交易所",
        "经办人",
        "挂牌次数",
        "备注",
    ],
    KIND_CAPITAL: [
        "ID",
        "类型",
        "项目编号",
        "隶属集团",
        "融资方",
        "项目名称",
        "融资金额（万）",
        "持股比例",
        "所属行业",
        "披露开始日期",
        "披露截止日期",
        "受托机构",
        "交易所",
        "经办人",
        "近一年净利润（万）",
        "所在地区",
        "挂牌次数",
        "备注",
    ],
    KIND_EQUITY: [
        "ID",
        "类型",
        "项目编号",
        "隶属集团",
        "转让方",
        "项目名称",
        "挂牌价格",
        "所属行业",
        "挂牌开始日期",
        "挂牌截止日期",
        "受托机构",
        "交易所",
        "经办人",
        "近一年净利润（万）",
        "所在地区",
        "挂牌次数",
        "备注",
    ],
    KIND_PUBLIC_RESOURCE: [
        "交易所",
        "项目编号",
        "项目名称",
        "交易方式",
        "受让方名称",
        "转让标的评估值",
        "成交金额",
        "成交日期",
    ],
}

BASE_FIELD_CANDIDATES: Dict[str, Dict[str, List[str]]] = {
    KIND_PRE: {
        "类型": ["类型"],
        "项目编号": ["项目编号"],
        "隶属集团": ["隶属集团"],
        "转让方": ["转让方"],
        "项目名称": ["项目名称"],
        "所属行业": ["所属行业"],
        "披露开始日期": ["披露开始日期", "预披露开始日期", "挂牌开始日期"],
        "披露截止日期": ["披露截止日期", "预披露截止日期", "挂牌截止日期"],
        "受托机构": ["受托机构"],
        "交易所": ["交易所"],
        "经办人": ["经办人"],
        "近一年净利润（万）": ["近一年净利润（万）", "近一年净利润"],
        "总资产（万）": ["总资产（万）", "总资产"],
        "挂牌次数": ["挂牌次数"],
        "备注": ["备注"],
    },
    KIND_PHYSICAL: {
        "类型": ["类型"],
        "项目编号": ["项目编号"],
        "隶属集团": ["隶属集团"],
        "转让方": ["转让方"],
        "项目名称": ["项目名称"],
        "挂牌价格": ["挂牌价格（万元）", "挂牌价格"],
        "所属行业": ["所属行业", "资产类别"],
        "挂牌开始日期": ["挂牌开始日期", "预披露开始日期"],
        "挂牌截止日期": ["挂牌截止日期", "预披露截止日期"],
        "受托机构": ["受托机构"],
        "交易所": ["交易所"],
        "经办人": ["经办人"],
        "挂牌次数": ["挂牌次数"],
        "备注": ["备注"],
    },
    KIND_CAPITAL: {
        "类型": ["类型"],
        "项目编号": ["项目编号"],
        "隶属集团": ["隶属集团"],
        "融资方": ["融资方", "转让方"],
        "项目名称": ["项目名称"],
        "融资金额（万）": ["融资金额（万）", "融资金额", "挂牌价格"],
        "持股比例": ["持股比例"],
        "所属行业": ["所属行业"],
        "披露开始日期": ["披露开始日期", "挂牌开始日期", "预披露开始日期"],
        "披露截止日期": ["披露截止日期", "挂牌截止日期", "预披露截止日期"],
        "受托机构": ["受托机构"],
        "交易所": ["交易所"],
        "经办人": ["经办人"],
        "近一年净利润（万）": ["近一年净利润（万）", "近一年净利润"],
        "所在地区": ["所在地区"],
        "挂牌次数": ["挂牌次数"],
        "备注": ["备注"],
    },
    KIND_EQUITY: {
        "类型": ["类型"],
        "项目编号": ["项目编号"],
        "隶属集团": ["隶属集团"],
        "转让方": ["转让方"],
        "项目名称": ["项目名称"],
        "挂牌价格": ["挂牌价格（万）", "挂牌价格"],
        "所属行业": ["所属行业"],
        "挂牌开始日期": ["挂牌开始日期", "预披露开始日期"],
        "挂牌截止日期": ["挂牌截止日期", "预披露截止日期"],
        "受托机构": ["受托机构"],
        "交易所": ["交易所"],
        "经办人": ["经办人"],
        "近一年净利润（万）": ["近一年净利润（万）", "近一年净利润"],
        "所在地区": ["所在地区"],
        "挂牌次数": ["挂牌次数"],
        "备注": ["备注"],
    },
    KIND_PUBLIC_RESOURCE: {
        "交易所": ["交易所"],
        "项目编号": ["项目编号"],
        "项目名称": ["项目名称"],
        "交易方式": ["交易方式"],
        "受让方名称": ["受让方名称"],
        "转让标的评估值": ["转让标的评估值"],
        "成交金额": ["成交金额", "挂牌价格"],
        "成交日期": ["成交日期"],
    },
}


def detect_output_kind(target_file: str) -> str:
    target = str(target_file or "")
    for kind, marker in OUTPUT_KIND_MARKERS:
        if marker in target:
            return kind
    return KIND_EQUITY


def clone_output_columns(
    payload: Dict[str, List[str]] = None,
) -> Dict[str, List[str]]:
    source = payload or BASE_OUTPUT_COLUMNS
    return {kind: list(columns) for kind, columns in source.items()}


def clone_field_candidates(
    payload: Dict[str, Dict[str, List[str]]] = None,
) -> Dict[str, Dict[str, List[str]]]:
    source = payload or BASE_FIELD_CANDIDATES
    return {
        kind: {column_name: list(candidates) for column_name, candidates in mapping.items()}
        for kind, mapping in source.items()
    }


def get_output_columns_for_kind(kind: str) -> List[str]:
    return list(BASE_OUTPUT_COLUMNS[kind])
