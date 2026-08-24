import { businessTypeLabel, PENDING_REVIEW_METRIC_LABEL } from "../constants/index.js";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function toMetricMap(metrics = []) {
  const entries = new Map();
  asArray(metrics)
    .filter((item) => item && typeof item === "object")
    .forEach((item) => {
      const key = String(item.key || "").trim();
      if (!key) return;
      entries.set(key, {
        key,
        label: String(item.label || "").trim(),
        value: Number.parseInt(item.value, 10) || 0,
      });
    });
  return entries;
}

function metricValue(metricMap, key) {
  if (!metricMap.has(key)) return null;
  return Number.parseInt(metricMap.get(key)?.value, 10) || 0;
}

function metricLabel(metricMap, key, fallback) {
  if (!metricMap.has(key)) return fallback;
  return String(metricMap.get(key)?.label || "").trim() || fallback;
}

function buildMetricFragments(metricMap, keys, { num, labels = {} }) {
  const fragments = [];
  keys.forEach((key) => {
    if (!metricMap.has(key)) return;
    const label = labels[key] || metricLabel(metricMap, key, key);
    fragments.push(`${label} ${num(metricValue(metricMap, key))}`);
  });
  return fragments;
}

function hasTerminalStatus(status = "") {
  return ["success", "success_with_warnings", "failed", "interrupted"].includes(String(status || "").trim());
}

function hasSpecificResultMessage(message = "") {
  const text = String(message || "").trim();
  if (!text) return false;
  return !new Set([
    "任务已完成",
    "任务已完成，但有待处理项",
    "任务执行失败",
    "任务已中断",
  ]).has(text);
}

function buildTerminalResultText(source = {}) {
  const status = String(source.status || "").trim();
  if (!hasTerminalStatus(status)) return "";
  const result = asObject(source.result);
  const message = String(result.message || "").trim();
  const failureMessage = String(result.failure_message || "").trim();
  if (status === "failed") {
    if (message && failureMessage && failureMessage !== message) return `${message} · ${failureMessage}`;
    return failureMessage || message;
  }
  if (hasSpecificResultMessage(message)) return message;
  return "";
}

function buildExportMeta(resultMetrics, { num }) {
  const primary = buildMetricFragments(resultMetrics, ["new_records", "changed_records"], {
    num,
    labels: {
      new_records: "新增",
      changed_records: "变更",
    },
  });
  if (primary.length) return primary.join(" · ");
  const fallback = buildMetricFragments(resultMetrics, ["visible_count"], {
    num,
    labels: { visible_count: "可见" },
  });
  return fallback.join(" · ");
}

function buildBusinessReEvaluationMeta(progressMetrics, resultMetrics, { num }) {
  const labels = {
    pending_review_count: PENDING_REVIEW_METRIC_LABEL,
    pending_mapping_count: "待补映射",
    mapping_conflict_count: "映射冲突",
    accepted_completed_count: "已采纳",
    skipped_count: "已跳过",
    failed_count: "失败",
  };
  const progressLine = buildMetricFragments(
    progressMetrics,
    ["pending_review_count", "pending_mapping_count", "mapping_conflict_count", "accepted_completed_count", "skipped_count", "failed_count"],
    { num, labels },
  );
  if (progressLine.length) return progressLine.join(" · ");
  const resultLine = buildMetricFragments(
    resultMetrics,
    ["pending_review_count", "pending_mapping_count", "mapping_conflict_count", "accepted_completed_count", "skipped_count", "failed_count"],
    { num, labels },
  );
  return resultLine.join(" · ");
}

function buildDefaultMeta(progressMetrics, resultMetrics, counts, { num }) {
  const progressLine = buildMetricFragments(progressMetrics, ["downloaded_count", "persisted_count", "exception_count"], {
    num,
  });
  if (progressLine.length) return progressLine.join(" · ");
  const resultLine = buildMetricFragments(resultMetrics, ["imported_count", "pending_review_count", "pending_mapping_count", "skipped_count", "failed_count"], {
    num,
    labels: {
      pending_review_count: PENDING_REVIEW_METRIC_LABEL,
    },
  });
  if (resultLine.length) return resultLine.join(" · ");
  return `已下载 ${num(counts.downloaded)} · 已归档 ${num(counts.persisted)} · 异常 ${num(counts.exceptions)}`;
}

export function buildJobMetaText(job = {}, { num } = {}) {
  const formatNumber = typeof num === "function" ? num : (value) => Number.parseInt(value, 10) || 0;
  const source = asObject(job);
  const resultMetrics = toMetricMap(asObject(source.result).metrics);
  const progressMetrics = toMetricMap(asObject(source.progress).metrics);
  const counts = asObject(source.counts);
  const terminalText = buildTerminalResultText(source);
  if (terminalText) return terminalText;

  if (String(source.job_type || "").trim() === "export_excel") {
    const exportText = buildExportMeta(resultMetrics, { num: formatNumber });
    if (exportText) return exportText;
  }

  if (String(source.job_type || "").trim() === "business_re_evaluation") {
    const businessText = buildBusinessReEvaluationMeta(progressMetrics, resultMetrics, { num: formatNumber });
    if (businessText) return businessText;
  }

  return buildDefaultMeta(progressMetrics, resultMetrics, counts, { num: formatNumber });
}

export function buildRecentJobBadge(job = {}, { num } = {}) {
  const formatNumber = typeof num === "function" ? num : (value) => Number.parseInt(value, 10) || 0;
  const source = asObject(job);
  const status = String(source.status || "").trim();
  const resultMetrics = toMetricMap(asObject(source.result).metrics);
  const progressMetrics = toMetricMap(asObject(source.progress).metrics);
  const counts = asObject(source.counts);

  if (status === "failed") {
    return { tone: "failed", text: "失败", compact: false };
  }

  if (String(source.job_type || "").trim() === "export_excel") {
    const exportCount = ["new_records", "changed_records", "visible_count"]
      .map((key) => metricValue(resultMetrics, key))
      .find((value) => value != null && value > 0);
    if (exportCount != null) {
      return { tone: "ready", text: `${formatNumber(exportCount)} 条`, compact: false };
    }
  }

  if (String(source.job_type || "").trim() === "business_re_evaluation") {
    const accepted = metricValue(progressMetrics, "accepted_completed_count") ?? metricValue(resultMetrics, "accepted_completed_count");
    if (accepted != null && accepted > 0) {
      return { tone: "ready", text: `${formatNumber(accepted)} 条`, compact: false };
    }
    const pending = metricValue(progressMetrics, "pending_review_count") ?? metricValue(resultMetrics, "pending_review_count");
    if (pending != null && pending > 0) {
      return { tone: "plain", text: `${formatNumber(pending)} 条`, compact: false };
    }
  }

  const persisted = metricValue(progressMetrics, "persisted_count");
  if (persisted != null && persisted > 0) {
    return { tone: "ready", text: `${formatNumber(persisted)} 条`, compact: false };
  }

  const imported = metricValue(resultMetrics, "imported_count");
  if (imported != null && imported > 0) {
    return { tone: "ready", text: `${formatNumber(imported)} 条`, compact: false };
  }

  const downloaded = metricValue(progressMetrics, "downloaded_count");
  if (downloaded != null && downloaded > 0) {
    return { tone: "plain", text: `${formatNumber(downloaded)} 条`, compact: true };
  }

  if ((Number.parseInt(counts.persisted, 10) || 0) > 0) {
    return { tone: "ready", text: `${formatNumber(counts.persisted)} 条`, compact: false };
  }
  if ((Number.parseInt(counts.downloaded, 10) || 0) > 0) {
    return { tone: "plain", text: `${formatNumber(counts.downloaded)} 条`, compact: true };
  }

  return { tone: "plain", text: "—", compact: true };
}

export function buildProgressHintText(progress = {}, { num } = {}) {
  const formatNumber = typeof num === "function" ? num : (value) => Number.parseInt(value, 10) || 0;
  const source = asObject(progress);
  const progressMetrics = toMetricMap(source.metrics);
  const metricKeys = progressMetrics.has("pending_review_count") || progressMetrics.has("pending_mapping_count") || progressMetrics.has("mapping_conflict_count") || progressMetrics.has("accepted_completed_count")
    ? ["pending_review_count", "pending_mapping_count", "mapping_conflict_count", "accepted_completed_count", "skipped_count", "failed_count"]
    : ["downloaded_count", "persisted_count", "exception_count", "pending_mapping_count"];
  const fragments = buildMetricFragments(
    progressMetrics,
    metricKeys,
    {
      num: formatNumber,
      labels: {
        pending_review_count: PENDING_REVIEW_METRIC_LABEL,
        pending_mapping_count: "待补映射",
        mapping_conflict_count: "映射冲突",
        accepted_completed_count: "已采纳",
        skipped_count: "已跳过",
        failed_count: "失败",
      },
    },
  );
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
  const prefix = businessTypeLabel(businessId, rawBusinessLabel || businessId || String(source.current_task_label || "").trim());
  const body = fragments.join(" · ");
  if (prefix && body) return `当前：${prefix} · ${body}`;
  if (prefix) return `当前：${prefix}`;
  return body;
}
