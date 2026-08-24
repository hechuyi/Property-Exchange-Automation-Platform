import { normalizeTerminalProgressStatus } from "./jobWarningStatus.js";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

const PROGRESS_METRIC_KEYS = new Set([
  "downloaded_count",
  "persisted_count",
  "exception_count",
  "pending_mapping_count",
  "pending_review_count",
  "mapping_conflict_count",
  "accepted_completed_count",
  "skipped_count",
  "failed_count",
  "archive_pending_count",
  "archive_completed_count",
]);
const STAGE_SUMMARY_NUMBER_KEYS = [
  "listed",
  "pages",
  "collected_candidates",
  "detail_candidates",
  "list_date_skipped",
  "detail_date_skipped",
  "date_missing_skipped",
  "detail_fetched",
  "saved",
  "resume_skipped",
  "duplicate_skipped",
  "business_filter_skipped",
  "missing_xmid_skipped",
  "detail_unavailable_skipped",
  "detail_failed",
  "list_unaccounted",
  "detail_unaccounted",
];
const STAGE_SUMMARY_TEXT_KEYS = [
  "warning_code",
  "warning_message",
];

function asMetrics(value) {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item) => item && typeof item === "object")
    .map((item) => {
      const key = String(item.key || "").trim();
      if (!PROGRESS_METRIC_KEYS.has(key)) return null;
      return {
        key,
        label: String(item.label || "").trim(),
        value: Number.parseInt(item.value, 10) || 0,
      };
    })
    .filter(Boolean);
}

function normalizeStageSummary(value) {
  const source = asObject(value);
  const normalized = {};
  const text = String(source.text || "").trim();
  if (text) normalized.text = text;
  STAGE_SUMMARY_NUMBER_KEYS.forEach((key) => {
    if (!Object.prototype.hasOwnProperty.call(source, key)) return;
    const parsed = Number.parseInt(source[key], 10);
    if (Number.isNaN(parsed)) return;
    normalized[key] = parsed;
  });
  STAGE_SUMMARY_TEXT_KEYS.forEach((key) => {
    const value = String(source[key] || "").trim();
    if (value) normalized[key] = value;
  });
  return normalized;
}

export function normalizeProgressResource(resource = {}, { jobType = "" } = {}) {
  const source = asObject(resource);
  const scope = source.scope && typeof source.scope === "object"
    ? (() => {
        const normalizedScope = asObject(source.scope);
        const normalized = {
          record_family: String(normalizedScope.record_family || "").trim(),
          state: String(normalizedScope.state || "").trim(),
          exchange: String(normalizedScope.exchange || "").trim(),
          keyword: String(normalizedScope.keyword || "").trim(),
          date_from: String(normalizedScope.date_from || "").trim(),
          date_to: String(normalizedScope.date_to || "").trim(),
          page: Number.parseInt(normalizedScope.page, 10) || 0,
          page_size: Number.parseInt(normalizedScope.page_size, 10) || 0,
        };
        if (String(normalizedScope.business_id || "").trim()) {
          normalized.business_id = String(normalizedScope.business_id || "").trim();
        }
        if (String(normalizedScope.business_label || "").trim()) {
          normalized.business_label = String(normalizedScope.business_label || "").trim();
        }
        return normalized;
      })()
    : null;
  const normalized = {
    phase_code: String(source.phase_code || "").trim(),
    phase_label: String(source.phase_label || "").trim(),
    job_status: String(source.job_status || "").trim(),
    is_terminal: Boolean(source.is_terminal),
    phase_percent: Number.parseInt(source.phase_percent, 10) || 0,
    current_task_label: String(source.current_task_label || "").trim(),
    task_index: Number.parseInt(source.task_index, 10) || 0,
    task_total: Number.parseInt(source.task_total, 10) || 0,
    metrics: asMetrics(source.metrics),
    latest_stage_code: String(source.latest_stage_code || "").trim(),
    latest_stage_label: String(source.latest_stage_label || "").trim(),
    latest_stage_summary: normalizeStageSummary(source.latest_stage_summary),
  };
  const recordFamily = String(source.record_family || scope?.record_family || "").trim();
  if (recordFamily) normalized.record_family = recordFamily;
  const businessId = String(source.business_id || scope?.business_id || "").trim();
  if (businessId) normalized.business_id = businessId;
  const businessLabel = String(source.business_label || scope?.business_label || "").trim();
  if (businessLabel) normalized.business_label = businessLabel;
  if (scope) {
    normalized.scope = scope;
  }
  return normalizeTerminalProgressStatus(normalized, { jobType });
}
