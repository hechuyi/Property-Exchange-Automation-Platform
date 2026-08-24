/**
 * Frontend application constants.
 *
 * Centralizes all label maps, option lists, and magic values
 * so they are not scattered across component files.
 */

// ── Mapping rules ──
export const MAPPING_RULES = {
  transferor_group: { sourceLabel: "转让方", targetLabel: "集团", title: "转让方 -> 集团", matchField: "transferor", targetField: "group_name" },
  transferor_type: { sourceLabel: "转让方", targetLabel: "类型", title: "转让方 -> 类型", matchField: "transferor", targetField: "source_type" },
  group_group: { sourceLabel: "集团", targetLabel: "集团", title: "集团 -> 集团", matchField: "group", targetField: "group_name" },
  group_type: { sourceLabel: "集团", targetLabel: "类型", title: "集团 -> 类型", matchField: "group", targetField: "source_type" },
};

// ── Exchange options (value, label) ──
export const RECORD_EXCHANGES = [
  ["all", "全部交易所"],
  ["sse", "上交所"],
  ["shanghai", "上交所（兼容）"],
  ["cbex", "北交所"],
  ["tpre", "天交所"],
  ["cquae", "重交所"],
  ["shandong", "山东产权交易中心"],
  ["guangdong", "广东产权交易中心"],
  ["shenzhen", "深圳联合产权交易所"],
];

export const EXCHANGE_DISPLAY_LABELS = {
  all: "全部交易所",
  sse: "上交所",
  shanghai: "上交所",
  cbex: "北交所",
  beijing: "北交所",
  tpre: "天交所",
  tianjin: "天交所",
  cquae: "重交所",
  chongqing: "重交所",
  shenzhen: "深圳联合产权交易所",
  shandong: "山东产权交易中心",
  guangdong: "广东产权交易中心",
  guangzhou: "广州产权交易所",
};

// ── Record family / business display labels ──
export const RECORD_FAMILY_LABELS = {
  listing: "挂牌业务",
  deal: "成交业务",
};

export const BUSINESS_TYPE_LABELS = {
  all: "全部业务类型",
  physical_asset: "实物资产",
  equity_transfer: "股权转让",
  capital_increase: "增资扩股",
  pre_disclosure: "预披露",
  deal_equity_transfer: "股权转让成交",
  deal_physical_asset: "实物资产成交",
  deal_capital_increase: "增资扩股成交",
};

export const FAMILY_EMPTY_STATE_MESSAGES = {
  listing: "暂无挂牌记录。",
  deal: "暂无成交记录。运行一键处理开始导入。",
};

export function recordFamilyLabel(value, fallback = "") {
  const normalized = String(value ?? "").trim();
  return RECORD_FAMILY_LABELS[normalized] || fallback || normalized;
}

export function businessTypeLabel(value, fallback = "") {
  const normalized = String(value ?? "").trim();
  const normalizedFallback = String(fallback ?? "").trim();
  return (normalizedFallback !== normalized && normalizedFallback) || BUSINESS_TYPE_LABELS[normalized] || normalized;
}

export function exchangeDisplayLabel(value, fallback = "") {
  const normalized = String(value ?? "").trim();
  return EXCHANGE_DISPLAY_LABELS[normalized] || fallback || normalized;
}

// ── Mapping source types ──
export const MAPPING_SOURCE_TYPES = ["央企", "部委", "市属", "民营"];

// ── Job statuses ──
export const ACTIVE_STATUSES = new Set(["starting", "running"]);
export const TERMINAL_STATUSES = new Set(["success", "success_with_warnings", "failed", "interrupted"]);

// ── Polling intervals (ms) ──
export const ACTIVE_POLL_MS = 8000;
export const IDLE_OVERVIEW_POLL_MS = 15000;
export const TASKS_POLL_MS = 10000;

// ── Job type / status labels ──
export const JOB_TYPE_LABELS = {
  one_click: "一键执行",
  download_ingest: "历史区间任务",
  archive_reprocess: "重新解析+后处理",
  export_excel: "导出 Excel",
  manual_import: "手动导入解析",
  mapping_refresh: "映射回刷",
  business_re_evaluation: "业务重判（内部兼容）",
};

export const JOB_STATUS_LABELS = {
  starting: "启动中",
  running: "执行中",
  success: "已完成",
  success_with_warnings: "已完成（有待处理）",
  failed: "执行失败",
  interrupted: "已中断",
};

export const RECORD_STATE_LABELS = {
  ready: "已录入",
  field_missing: "字段缺失",
  pending_review: "待人工复核",
  pending_mapping: "待补映射",
  mapping_conflict: "映射冲突",
  skipped: "已跳过",
  parse_failed: "解析失败",
  postprocess_failed: "处理失败",
  conflict: "归档重名",
};

export const PENDING_REVIEW_METRIC_LABEL = RECORD_STATE_LABELS.pending_review;

export function recordStateLabel(value) {
  const normalized = String(value ?? "").trim();
  return RECORD_STATE_LABELS[normalized] || normalized;
}
