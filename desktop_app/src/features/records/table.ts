export type RecordsRow = {
  record_id?: string;
  state?: string;
  status_label?: string;
  status_detail?: string;
  project_code?: string;
  project_name?: string;
  listing_date?: string;
  updated_at?: string;
  archive_path?: string;
  source_file?: string;
};

export function resolveLocateTarget(row: RecordsRow) {
  return String(row.archive_path || row.source_file || "").trim();
}

export function statusText(row: RecordsRow) {
  const preferred = String(row.status_label || "").trim();
  if (preferred) {
    return preferred;
  }
  return String(row.state || "未知").trim() || "未知";
}
