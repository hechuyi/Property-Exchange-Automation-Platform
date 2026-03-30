export type RecordsRow = {
  record_id?: string;
  state?: string;
  status_label?: string;
  status_detail?: string;
  project_code?: string;
  project_name?: string;
  project_type?: string;
  exchange?: string;
  listing_date?: string;
  updated_at?: string;
  archive_path?: string;
  source_file?: string;
  values?: Record<string, unknown>;
};

export type RecordStatusTone = "ready" | "blocked" | "attention" | "muted";

export const RECORD_WORKFLOW_LABELS = {
  ready: "已就绪",
  blocked: "待补映射",
  attention: "需人工处理",
  skipped: "已跳过",
} as const;

type RecordDisplayStatus = {
  label: string;
  tone: RecordStatusTone;
  detail: string;
};

const PROJECT_TYPE_LABELS: Record<string, string> = {
  equity_transfer: "股权转让",
  physical_asset: "实物资产",
  capital_increase: "增资扩股",
  pre_disclosure: "预披露",
};

const FALLBACK_STATUS_DETAIL: Record<string, string> = {
  ready: "记录已整理完成，可直接查看或导出。",
  pending_mapping: "待补映射尚未完成，请先补齐后再继续处理。",
  mapping_conflict: "存在待确认的映射口径，请先统一后再继续处理。",
  skipped: "该记录已被明确跳过，不会参与后续导出。",
  parse_failed: "内容解析未完成，需要人工检查源文件。",
  postprocess_failed: "后处理未完成，需要人工检查整理结果。",
  conflict: "归档文件存在重名，需要人工确认保留方式。",
};

export function resolveLocateTarget(row: RecordsRow) {
  return String(row.archive_path || row.source_file || "").trim();
}

export function resolveOpenFileTarget(row: RecordsRow) {
  return String(row.archive_path || row.source_file || "").trim();
}

export function projectTypeText(projectType: string | undefined) {
  const key = String(projectType || "").trim();
  return PROJECT_TYPE_LABELS[key] || key || "未指定";
}

export function resolveRecordStatus(row: RecordsRow): RecordDisplayStatus {
  const state = String(row.state || "").trim();
  const detail = String(row.status_detail || "").trim() || FALLBACK_STATUS_DETAIL[state] || "记录状态需要人工确认。";

  if (state === "ready") {
    return { label: RECORD_WORKFLOW_LABELS.ready, tone: "ready", detail };
  }
  if (state === "pending_mapping" || state === "mapping_conflict") {
    return { label: RECORD_WORKFLOW_LABELS.blocked, tone: "blocked", detail };
  }
  if (state === "skipped") {
    return { label: RECORD_WORKFLOW_LABELS.skipped, tone: "muted", detail };
  }
  return { label: RECORD_WORKFLOW_LABELS.attention, tone: "attention", detail };
}

export function statusText(row: RecordsRow) {
  return resolveRecordStatus(row).label;
}
