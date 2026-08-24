function asInt(value) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function metricMap(metrics = []) {
  const map = new Map();
  if (!Array.isArray(metrics)) return map;
  metrics.forEach((item) => {
    if (!item || typeof item !== "object") return;
    const key = String(item.key || "").trim();
    if (!key) return;
    map.set(key, asInt(item.value));
  });
  return map;
}

const WARNING_KEYS_BY_JOB_TYPE = {
  one_click: ["exception_count", "pending_review_count", "pending_mapping_count", "mapping_conflict_count", "failed_count"],
  download_ingest: ["exception_count", "pending_review_count", "pending_mapping_count", "mapping_conflict_count", "failed_count"],
  mapping_refresh: ["exception_count", "pending_review_count", "pending_mapping_count", "mapping_conflict_count", "failed_count"],
  manual_import: ["pending_review_count", "pending_mapping_count", "failed_count"],
  archive_reprocess: ["pending_review_count", "pending_mapping_count", "failed_count"],
  business_re_evaluation: ["pending_review_count", "pending_mapping_count", "mapping_conflict_count", "failed_count"],
};

function hasOperatorAttention(jobType = "", metrics = [], counts = {}) {
  const warningKeys = WARNING_KEYS_BY_JOB_TYPE[String(jobType || "").trim()] || [];
  if (warningKeys.length === 0) return false;
  const values = metricMap(metrics);
  return warningKeys.some((key) => {
    if (values.has(key)) {
      return values.get(key) > 0;
    }
    if (key === "exception_count") {
      return asInt(counts.exceptions) > 0;
    }
    return false;
  });
}

export function normalizeTerminalJobStatus({
  status,
  jobType,
  resultMetrics = [],
  progressMetrics = [],
  counts = {},
} = {}) {
  const normalizedStatus = String(status || "").trim();
  if (normalizedStatus !== "success_with_warnings") {
    return normalizedStatus;
  }
  return hasOperatorAttention(jobType, [...progressMetrics, ...resultMetrics], counts)
    ? normalizedStatus
    : "success";
}

export function normalizeTerminalProgressStatus(progress = {}, { jobType = "" } = {}) {
  const normalized = { ...progress };
  if (String(normalized.job_status || "").trim() !== "success_with_warnings") {
    return normalized;
  }
  if (hasOperatorAttention(jobType, normalized.metrics || [])) {
    return normalized;
  }
  normalized.job_status = "success";
  if (String(normalized.phase_code || "").trim() === "completed_with_warnings") {
    normalized.phase_code = "completed";
  }
  if (/有待处理/.test(String(normalized.phase_label || "").trim())) {
    normalized.phase_label = "已完成";
  }
  return normalized;
}
