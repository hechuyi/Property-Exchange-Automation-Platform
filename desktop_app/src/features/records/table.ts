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
  pending_mapping: "映射信息仍需补齐后，才能继续后续处理。",
  mapping_conflict: "现有映射规则存在冲突，需要先确认映射口径。",
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
    return { label: "已就绪", tone: "ready", detail };
  }
  if (state === "pending_mapping" || state === "mapping_conflict") {
    return { label: "待补映射", tone: "blocked", detail };
  }
  if (state === "skipped") {
    return { label: "已跳过", tone: "muted", detail };
  }
  return { label: "需人工处理", tone: "attention", detail };
}

export function statusText(row: RecordsRow) {
  return resolveRecordStatus(row).label;
}
