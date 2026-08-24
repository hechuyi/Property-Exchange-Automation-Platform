"""Business constants and display labels for the desktop application.

Centralizes all label maps, field configurations, and mapping rule
specifications so they are not scattered across the service layer.
"""

from __future__ import annotations

from peap.export_projection import CANONICAL_TO_COMPAT
from peap.output_contract import (
    KIND_CAPITAL,
    KIND_EQUITY,
    KIND_PHYSICAL,
    KIND_PRE,
)

# ── Record states ──

RECORD_STATE_LABELS = {
    "ready": "已录入",
    "field_missing": "缺少必填字段",
    "pending_review": "待人工复核",
    "pending_mapping": "待补映射",
    "mapping_conflict": "映射冲突",
    "skipped": "已跳过",
    "parse_failed": "解析失败",
    "postprocess_failed": "处理失败",
    "conflict": "归档重名",
}

# ── Job classifications ──

JOB_TYPE_LABELS = {
    "one_click": "一键执行",
    "download_ingest": "历史区间任务",
    "archive_reprocess": "重新解析+后处理",
    "export_excel": "导出 Excel",
    "manual_import": "手动导入解析",
    "mapping_refresh": "映射回刷",
    "refresh_postprocess": "后处理刷新",
    "record_reprocess": "记录完整重跑",
    "business_re_evaluation": "业务重判（内部兼容）",
}

JOB_PHASE_LABELS = {
    "prepare_tasks": "正在扫描网页",
    "save_pages": "正在保存网页",
    "manual_import_scan": "正在整理手动导入文件",
    "archive_reprocess_scan": "正在整理归档文件",
    "reprocessing": "正在重处理记录",
    "exporting": "正在导出 Excel",
}

# ── Project types ──

PROJECT_TYPE_LABELS = {
    "equity_transfer": "股权转让",
    "physical_asset": "实物资产",
    "capital_increase": "增资扩股",
    "pre_disclosure": "预披露",
}

PROJECT_TYPE_CODES_BY_LABEL = {
    label: code
    for code, label in PROJECT_TYPE_LABELS.items()
}

PROJECT_TYPE_TO_KIND = {
    "股权转让": KIND_EQUITY,
    "实物资产": KIND_PHYSICAL,
    "增资扩股": KIND_CAPITAL,
    "预披露": KIND_PRE,
}

# ── Display fields ──

DISPLAY_ALIAS_FIELDS = {
    "agency": "受托机构",
    "asset_total": "总资产（万）",
    "contact": "经办人",
    "end_date": "挂牌截止日期",
    "industry": "所属行业",
    "listing_times": "挂牌次数",
    "profit": "近一年净利润（万）",
    "region": "所在地区",
}

from peap.output_contract import get_output_columns_for_kind  # noqa: E402

DISPLAY_COMPATIBLE_KEYS = frozenset(
    {"ID"}
    | set(CANONICAL_TO_COMPAT.values())
    | {
        column
        for kind in PROJECT_TYPE_TO_KIND.values()
        for column in get_output_columns_for_kind(kind)
    }
)

# ── Mapping rules ──

MAPPING_MATCH_FIELDS = {
    "transferor": ("转让方", "融资方", "转让方名称", "融资方名称", "company_name_primary", "seller"),
    "group": ("隶属集团", "集团名称", "group_name"),
}

MAPPING_RULE_SPECS = {
    "transferor_group": {
        "match_field": "transferor",
        "target_field": "group_name",
        "title": "转让方 -> 集团",
        "source_label": "转让方",
        "target_label": "集团",
    },
    "transferor_type": {
        "match_field": "transferor",
        "target_field": "source_type",
        "title": "转让方 -> 类型",
        "source_label": "转让方",
        "target_label": "类型",
    },
    "group_group": {
        "match_field": "group",
        "target_field": "group_name",
        "title": "集团 -> 集团",
        "source_label": "集团",
        "target_label": "集团",
    },
    "group_type": {
        "match_field": "group",
        "target_field": "source_type",
        "title": "集团 -> 类型",
        "source_label": "集团",
        "target_label": "类型",
    },
}

MAPPING_SOURCE_TYPES = frozenset({"央企", "部委", "市属", "民营"})
