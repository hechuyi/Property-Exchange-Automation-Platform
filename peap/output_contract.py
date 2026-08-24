"""Shared output contract definitions for excel writers and exporters."""

from dataclasses import dataclass
from typing import Dict, List

from peap_core.source_business_contract import list_export_workbook_support

from .constants import KEY_IS_PRE_DISCLOSURE, KEY_PROJECT_TYPE, KEY_STATUS

KIND_PRE = "pre_disclosure"
KIND_PHYSICAL = "physical_asset"
KIND_CAPITAL = "capital_increase"
KIND_EQUITY = "equity_transfer"
KIND_DEAL_PHYSICAL = "deal_physical_asset"
KIND_DEAL_CAPITAL = "deal_capital_increase"
KIND_DEAL_EQUITY = "deal_equity_transfer"
KIND_PUBLIC_RESOURCE = "public_resource_deals"

PUBLIC_RESOURCE_OUTPUT_STEM = "公共资源网四大交易所股权转让成交信息统计"
PUBLIC_RESOURCE_OUTPUT_FILENAME = f"{PUBLIC_RESOURCE_OUTPUT_STEM}.xlsx"

OUTPUT_FILE_STEMS = {
    KIND_PRE: "挂牌_预披露",
    KIND_PHYSICAL: "挂牌_实物资产",
    KIND_CAPITAL: "挂牌_增资扩股",
    KIND_EQUITY: "挂牌_股权转让",
    KIND_DEAL_PHYSICAL: "成交_实物资产",
    KIND_DEAL_CAPITAL: "成交_增资扩股",
    KIND_DEAL_EQUITY: "成交_股权转让",
    KIND_PUBLIC_RESOURCE: PUBLIC_RESOURCE_OUTPUT_STEM,
}

DEFAULT_INTERNAL_KEYS = {KEY_STATUS, KEY_PROJECT_TYPE, KEY_IS_PRE_DISCLOSURE}

OUTPUT_KIND_MARKERS = (
    (KIND_DEAL_EQUITY, "成交_股权转让"),
    (KIND_DEAL_PHYSICAL, "成交_实物资产"),
    (KIND_DEAL_CAPITAL, "成交_增资扩股"),
    (KIND_PUBLIC_RESOURCE, PUBLIC_RESOURCE_OUTPUT_STEM),
    (KIND_PRE, "预披露"),
    (KIND_PHYSICAL, "实物资产"),
    (KIND_CAPITAL, "增资扩股"),
)

DEAL_SOURCE_ORDER = ("cbex", "sse", "tpre", "cquae")


@dataclass(frozen=True)
class DealWorkbookSheetSpec:
    source_id: str
    sheet_name: str
    headers: tuple[str, ...]
    merge_headers: tuple[str, ...] = ()


DEAL_PHYSICAL_WORKBOOK_HEADERS = (
    "项目编号",
    "标的名称",
    "转让标的评估结果",
    "转让底价",
    "交易价格",
    "成交日期",
    "备注",
)
DEAL_EQUITY_WORKBOOK_HEADERS_BY_SOURCE = {
    "cbex": ("项目编号", "标的名称", "转让标的评估结果", "转让底价（万元）", "交易价格（万元）", "成交日期", "备注", "交易方式", "受让方名称"),
    "sse": ("项目编号", "标的名称", "转让标的评估值", "转让底价", "交易价格", "成交日期", "备注", "是否竞价"),
    "tpre": ("项目编号", "标的名称", "转让标的评估值", "转让底价（万元）", "交易价格（万元）", "成交日期", "备注", "是否成交"),
    "cquae": ("项目编号", "标的名称", "转让标的评估值", "转让底价（万元）", "交易价格（万元）", "成交日期", "备注"),
}
DEAL_CAPITAL_WORKBOOK_HEADERS_BY_SOURCE = {
    "cbex": ("项目编号", "项目名称", "成交日期", "投资方名称", "投资金额（万元）", "持股比例", "投资总金额（万元）", "持股占比", "备注"),
    "sse": ("项目编号", "项目名称", "增资企业名称", "成交日期", "投资方名称", "投资金额（万元）", "持股比例", "投资总金额（万元）", "持股占比", "备注"),
    "tpre": ("项目编号", "项目名称", "成交日期", "投资方名称", "投资金额（万元）", "持股比例", "投资总金额（万元）", "持股占比", "备注"),
    "cquae": ("序号", "项目编号", "标的名称", "投资方名称", "投资金额（万元）", "持股比例", "成交日期", "备注"),
}
DEAL_CAPITAL_MERGE_HEADERS_BY_SOURCE = {
    "cbex": ("项目编号", "项目名称", "成交日期", "投资总金额（万元）", "持股占比", "备注"),
    "sse": ("项目编号", "项目名称", "增资企业名称", "成交日期", "投资总金额（万元）", "持股占比", "备注"),
    "tpre": ("项目编号", "项目名称", "成交日期", "投资总金额（万元）", "持股占比", "备注"),
    "cquae": ("项目编号", "标的名称", "成交日期", "备注"),
}

STRUCTURED_EXPORT_EXTRA_FIELDS: Dict[str, tuple[str, ...]] = {
    KIND_DEAL_CAPITAL: ("investors", "transferors", "financing_party_names", "project_parties"),
}

DEAL_EQUITY_SHEET_NAMES: Dict[str, str] = {
    item.source_id: item.sheet_name
    for item in list_export_workbook_support(record_family="deal", business_id=KIND_DEAL_EQUITY)
    if item.supported
}
DEAL_CAPITAL_SHEET_NAMES: Dict[str, str] = {
    item.source_id: item.sheet_name
    for item in list_export_workbook_support(record_family="deal", business_id=KIND_DEAL_CAPITAL)
    if item.supported
}
DEAL_PHYSICAL_SHEET_NAMES: Dict[str, str] = {
    item.source_id: item.sheet_name
    for item in list_export_workbook_support(record_family="deal", business_id=KIND_DEAL_PHYSICAL)
    if item.supported
}

DEAL_WORKBOOK_SPECS_BY_KIND: Dict[str, tuple[DealWorkbookSheetSpec, ...]] = {
    KIND_DEAL_EQUITY: tuple(
        DealWorkbookSheetSpec(
            source_id=source_id,
            sheet_name=sheet_name,
            headers=DEAL_EQUITY_WORKBOOK_HEADERS_BY_SOURCE[source_id],
        )
        for source_id in DEAL_SOURCE_ORDER
        if (sheet_name := DEAL_EQUITY_SHEET_NAMES.get(source_id)) is not None
        and source_id in DEAL_EQUITY_WORKBOOK_HEADERS_BY_SOURCE
    ),
    KIND_DEAL_PHYSICAL: tuple(
        DealWorkbookSheetSpec(
            source_id=source_id,
            sheet_name=sheet_name,
            headers=DEAL_PHYSICAL_WORKBOOK_HEADERS,
        )
        for source_id in DEAL_SOURCE_ORDER
        if (sheet_name := DEAL_PHYSICAL_SHEET_NAMES.get(source_id)) is not None
    ),
    KIND_DEAL_CAPITAL: tuple(
        DealWorkbookSheetSpec(
            source_id=source_id,
            sheet_name=sheet_name,
            headers=DEAL_CAPITAL_WORKBOOK_HEADERS_BY_SOURCE[source_id],
            merge_headers=DEAL_CAPITAL_MERGE_HEADERS_BY_SOURCE.get(source_id, ()),
        )
        for source_id in DEAL_SOURCE_ORDER
        if (sheet_name := DEAL_CAPITAL_SHEET_NAMES.get(source_id)) is not None
        and source_id in DEAL_CAPITAL_WORKBOOK_HEADERS_BY_SOURCE
    ),
}

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
        "挂牌价格（万元）",
        "资产类别",
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
        "项目编号",
        "隶属集团",
        "融资方",
        "项目名称",
        "融资金额",
        "持股比例",
        "所属行业",
        "披露开始日期",
        "披露截止日期",
        "受托机构",
        "交易所",
        "经办人",
        "近一年净利润（万）",
        "所在地区",
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
    KIND_DEAL_EQUITY: [
        "项目编号",
        "项目名称",
        "标的名称",
        "转让标的评估结果",
        "转让标的评估值",
        "转让底价（万元）",
        "转让底价",
        "交易价格（万元）",
        "交易价格",
        "成交日期",
        "交易方式",
        "受让方名称",
        "备注",
        "是否竞价",
        "是否成交",
    ],
    KIND_DEAL_PHYSICAL: [
        *DEAL_PHYSICAL_WORKBOOK_HEADERS,
    ],
    KIND_DEAL_CAPITAL: [
        "项目编号",
        "项目名称",
        "标的名称",
        "增资企业名称",
        "成交日期",
        "投资方名称",
        "投资金额（万元）",
        "持股比例",
        "投资总金额（万元）",
        "持股占比",
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
        "备注",
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
        "挂牌价格（万元）": ["挂牌价格（万元）", "挂牌价格"],
        "资产类别": ["资产类别", "所属行业"],
        "挂牌开始日期": ["挂牌开始日期", "预披露开始日期"],
        "挂牌截止日期": ["挂牌截止日期", "预披露截止日期"],
        "受托机构": ["受托机构"],
        "交易所": ["交易所"],
        "经办人": ["经办人"],
        "挂牌次数": ["挂牌次数"],
        "备注": ["备注"],
    },
    KIND_CAPITAL: {
        "项目编号": ["项目编号"],
        "隶属集团": ["隶属集团"],
        "融资方": ["融资方", "转让方"],
        "项目名称": ["项目名称"],
        "融资金额": ["融资金额", "融资金额（万）", "挂牌价格"],
        "持股比例": ["持股比例"],
        "所属行业": ["所属行业"],
        "披露开始日期": ["披露开始日期", "挂牌开始日期", "预披露开始日期"],
        "披露截止日期": ["披露截止日期", "挂牌截止日期", "预披露截止日期"],
        "受托机构": ["受托机构"],
        "交易所": ["交易所"],
        "经办人": ["经办人"],
        "近一年净利润（万）": ["近一年净利润（万）", "近一年净利润"],
        "所在地区": ["所在地区"],
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
    KIND_DEAL_EQUITY: {
        "项目编号": ["项目编号"],
        "项目名称": ["项目名称", "标的名称"],
        "标的名称": ["标的名称", "项目名称"],
        "转让标的评估结果": ["转让标的评估结果", "转让标的评估值"],
        "转让标的评估值": ["转让标的评估值", "转让标的评估结果"],
        "转让底价（万元）": ["转让底价（万元）", "转让底价"],
        "转让底价": ["转让底价", "转让底价（万元）"],
        "交易价格（万元）": ["交易价格（万元）", "交易价格", "成交金额"],
        "交易价格": ["交易价格", "交易价格（万元）", "成交金额"],
        "成交日期": ["成交日期"],
        "交易方式": ["交易方式"],
        "受让方名称": ["受让方名称"],
        "备注": ["备注"],
        "是否竞价": ["是否竞价"],
        "是否成交": ["是否成交"],
    },
    KIND_DEAL_PHYSICAL: {
        "项目编号": ["项目编号"],
        "标的名称": ["标的名称", "项目名称"],
        "转让标的评估结果": ["转让标的评估结果", "转让标的评估值"],
        "转让底价": ["转让底价", "转让底价（万元）"],
        "交易价格": ["交易价格", "交易价格（万元）", "成交金额"],
        "成交日期": ["成交日期"],
        "备注": ["备注"],
    },
    KIND_DEAL_CAPITAL: {
        "项目编号": ["项目编号"],
        "项目名称": ["项目名称", "标的名称"],
        "标的名称": ["标的名称", "项目名称"],
        "增资企业名称": ["增资企业名称", "融资方", "融资方名称", "capital_company_name"],
        "成交日期": ["成交日期"],
        "投资方名称": ["投资方名称", "投资方", "投资人"],
        "投资金额（万元）": ["投资金额（万元）", "投资金额", "投资额"],
        "持股比例": ["持股比例", "持股比例（%）", "投资比例", "持股占比", "持股占比（%）"],
        "投资总金额（万元）": ["投资总金额（万元）", "投资总金额", "融资金额", "融资金额（万元）", "total_investment_amount"],
        "持股占比": ["持股占比", "持股占比（%）", "持股比例", "持股比例（%）", "holding_ratio"],
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
        "备注": ["备注"],
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


def get_output_stem_for_kind(kind: str) -> str:
    return str(OUTPUT_FILE_STEMS[kind])


def get_structured_export_extra_fields(kind: str) -> List[str]:
    return list(STRUCTURED_EXPORT_EXTRA_FIELDS.get(kind) or ())


def list_deal_workbook_sheet_specs(kind: str) -> List[DealWorkbookSheetSpec]:
    return list(DEAL_WORKBOOK_SPECS_BY_KIND.get(kind) or ())


def get_supported_source_ids_for_kind(kind: str) -> List[str] | None:
    specs = DEAL_WORKBOOK_SPECS_BY_KIND.get(kind)
    if specs is None:
        return None
    return [spec.source_id for spec in specs]
