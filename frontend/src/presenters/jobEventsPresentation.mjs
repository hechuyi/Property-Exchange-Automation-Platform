import { businessTypeLabel } from "../constants/index.js";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function normalizeNumber(formatter, value) {
  return typeof formatter === "function" ? formatter(value) : Number.parseInt(value, 10) || 0;
}

function summaryCounts(event = {}) {
  const summary = asObject(asObject(event).summary);
  return {
    listed: Number.parseInt(summary.listed, 10) || 0,
    collected_candidates: Number.parseInt(summary.collected_candidates, 10) || 0,
    detail_candidates: Number.parseInt(summary.detail_candidates, 10) || 0,
    detail_fetched: Number.parseInt(summary.detail_fetched, 10) || 0,
    saved: Number.parseInt(summary.saved, 10) || 0,
    list_date_skipped: Number.parseInt(summary.list_date_skipped, 10) || 0,
    detail_date_skipped: Number.parseInt(summary.detail_date_skipped, 10) || 0,
    date_missing_skipped: Number.parseInt(summary.date_missing_skipped, 10) || 0,
    resume_skipped: Number.parseInt(summary.resume_skipped, 10) || 0,
    list_unaccounted: Number.parseInt(summary.list_unaccounted, 10) || 0,
    errors: Number.parseInt(summary.errors, 10) || 0,
    duplicate_skipped: Number.parseInt(summary.duplicate_skipped, 10) || 0,
    business_filter_skipped: Number.parseInt(summary.business_filter_skipped, 10) || 0,
    warning_code: String(summary.warning_code || "").trim(),
    warning_message: String(summary.warning_message || "").trim(),
  };
}

function formatStatLine(event = {}, { num } = {}) {
  const source = asObject(event);
  const kind = String(source.kind || source.stage_code || "").trim();
  const stageCode = String(source.stage_code || "").trim();
  const phasePercent = Number.parseInt(source.phase_percent, 10) || 0;
  const counts = summaryCounts(source);
  const formatNumber = (value) => normalizeNumber(num, value);
  const candidateCount = counts.detail_candidates || counts.collected_candidates;
  const pieces = [];

  if (stageCode === "prepare_tasks" || kind === "collect") {
    if (counts.listed > 0) pieces.push(`已列 ${formatNumber(counts.listed)}`);
    if (candidateCount > 0) pieces.push(`候选 ${formatNumber(candidateCount)}`);
    if (counts.list_date_skipped > 0) pieces.push(`日期跳过 ${formatNumber(counts.list_date_skipped)}`);
    if (counts.duplicate_skipped > 0) pieces.push(`重复 ${formatNumber(counts.duplicate_skipped)}`);
    if (counts.warning_message) pieces.push(counts.warning_message);
    return pieces.length ? pieces.join(" · ") : `进行中 ${formatNumber(phasePercent)}%`;
  }

  if (stageCode === "save_pages" || kind === "download") {
    if (counts.listed > 0) pieces.push(`已列 ${formatNumber(counts.listed)}`);
    if (counts.detail_fetched > 0) pieces.push(`已抓 ${formatNumber(counts.detail_fetched)}`);
    if (counts.saved > 0) pieces.push(`已保存 ${formatNumber(counts.saved)}`);
    if (counts.list_date_skipped > 0) pieces.push(`日期跳过 ${formatNumber(counts.list_date_skipped)}`);
    if (counts.detail_date_skipped > 0) pieces.push(`详情日期跳过 ${formatNumber(counts.detail_date_skipped)}`);
    if (counts.errors > 0) pieces.push(`异常 ${formatNumber(counts.errors)}`);
    if (counts.duplicate_skipped > 0) pieces.push(`重复 ${formatNumber(counts.duplicate_skipped)}`);
    if (counts.business_filter_skipped > 0) pieces.push(`业务过滤 ${formatNumber(counts.business_filter_skipped)}`);
    if (counts.warning_message) pieces.push(counts.warning_message);
    return pieces.length ? pieces.join(" · ") : `进行中 ${formatNumber(phasePercent)}%`;
  }

  if (stageCode === "parse_documents") {
    if (counts.saved > 0) pieces.push(`已保存 ${formatNumber(counts.saved)}`);
    if (candidateCount > 0) pieces.push(`候选 ${formatNumber(candidateCount)}`);
    if (counts.errors > 0) pieces.push(`异常 ${formatNumber(counts.errors)}`);
    return pieces.length ? pieces.join(" · ") : `进行中 ${formatNumber(phasePercent)}%`;
  }

  if (counts.listed > 0) pieces.push(`已列 ${formatNumber(counts.listed)}`);
  if (counts.saved > 0) pieces.push(`已保存 ${formatNumber(counts.saved)}`);
  if (counts.errors > 0) pieces.push(`异常 ${formatNumber(counts.errors)}`);
  if (counts.warning_message) pieces.push(counts.warning_message);
  return pieces.length ? pieces.join(" · ") : (String(source.status || "").trim() === "done" ? "完成" : `进行中 ${formatNumber(phasePercent)}%`);
}

export function eventStageLabel(event = {}) {
  const source = asObject(event);
  return String(source.stage_label || source.stage_code || "").trim();
}

export function isJobEventFailure(event = {}) {
  const source = asObject(event);
  return Boolean(String(source.error_code || "").trim() || String(source.error_message || "").trim()) || String(source.status || "").trim() === "failed";
}

export function buildJobEventErrorText(event = {}) {
  const source = asObject(event);
  const code = String(source.error_code || "").trim();
  const message = String(source.error_message || "").trim() || "未知错误";
  return `${code ? `${code}: ` : ""}${message}`;
}

export function hasJobEventActivity(event = {}) {
  const counts = summaryCounts(event);
  return Object.values(counts).some((value) => value > 0);
}

export function buildJobEventLogText(event = {}, { num } = {}) {
  const source = asObject(event);
  const businessId = String(
    source.business_id
    || asObject(source.scope).business_id
    || "",
  ).trim();
  const rawBusinessLabel = String(
    source.business_label
    || asObject(source.scope).business_label
    || "",
  ).trim();
  const taskLabel = businessTypeLabel(businessId, rawBusinessLabel || businessId || String(source.task_label || "").trim());
  const statLine = formatStatLine(source, { num });
  return taskLabel ? `${taskLabel} · ${statLine}` : statLine;
}
