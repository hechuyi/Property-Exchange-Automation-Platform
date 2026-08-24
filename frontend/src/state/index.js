/**
 * Centralized application state store.
 *
 * Provides a simple observable store pattern — components can read
 * state directly and call `update()` to trigger re-renders.
 */

import { ACTIVE_STATUSES, TERMINAL_STATUSES, JOB_TYPE_LABELS, JOB_STATUS_LABELS } from "../constants/index.js";

// ── State ──
const state = {
  currentPanel: "overview",
  overview: {},
  jobs: [],
  records: { rows: [], summary: {}, display_columns: [] },
  mappings: { sections: [], summary: {}, entries: [] },
  settings: { basic: {}, advanced: {}, runtime: {} },
  recordPage: 1,
  recordPageSize: 50,
  recordFilters: { state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" },
  pollTimer: null,
  errorMsg: "",
  currentEvents: [],
  mappingDraft: createInitialMappingDraft(),
  mappingPreview: null,
  mappingMessage: "",
  settingsMessage: "",
};

export function getState() {
  return state;
}

export function updateState(patch) {
  Object.assign(state, patch);
}

// ── Mapping draft helpers ──

export function createInitialMappingDraft() {
  return {
    entry_id: "",
    rule_kind: "transferor_group",
    source_name: "",
    target_value: "",
    notes: "",
    confirm_overwrite: false,
  };
}

// ── Job status helpers ──

export function jobTypeLabel(v) {
  return JOB_TYPE_LABELS[String(v || "")] || String(v || "任务");
}
export function jobStatusLabel(v) {
  return JOB_STATUS_LABELS[String(v || "")] || String(v || "");
}
export function isActive(v) {
  return ACTIVE_STATUSES.has(String(v || "").trim().toLowerCase());
}
export function isTerminal(v) {
  return TERMINAL_STATUSES.has(String(v || "").trim().toLowerCase());
}

export function buildActiveJobBlockMessage(overview, actionLabel) {
  const latestJob = overview && typeof overview === "object" ? overview.latest_job : null;
  if (!latestJob || !isActive(latestJob.status)) return "";
  const normalizedActionLabel = String(actionLabel || "").trim();
  return `已有执行中的任务：${jobTypeLabel(latestJob.job_type)}，请等待完成后再${normalizedActionLabel}。`;
}

export function formatActionErrorMessage(prefix, error) {
  if (error?.localOnly) {
    return String(error.message || "").trim() || String(prefix || "").trim();
  }
  const directMessage = typeof error === "string"
    ? error.trim()
    : String(error?.message || error?.error?.message || "").trim();
  if (directMessage) {
    return `${prefix}: ${directMessage}`;
  }
  const status = Number.parseInt(error?.status, 10);
  if (Number.isFinite(status) && status > 0) {
    return `${prefix}: HTTP ${status}`;
  }
  return `${prefix}: 未知错误`;
}

export function stateDotClass(status) {
  if (isActive(status)) return "running";
  if (status === "success" || status === "completed") return "success";
  if (status === "failed") return "failed";
  return "idle";
}

export function buildRecordsScope() {
  const filters = state.recordFilters || {};
  return {
    record_family: String(filters.record_family || "").trim() || "listing",
    state: String(filters.state || "").trim() || "all",
    business_id: String(filters.business_id || "").trim() || "all",
    exchange: String(filters.exchange || "").trim() || "all",
    keyword: String(filters.keyword || "").trim(),
    date_from: String(filters.date_from || "").trim(),
    date_to: String(filters.date_to || "").trim(),
    page: state.recordPage,
    page_size: state.recordPageSize,
  };
}
