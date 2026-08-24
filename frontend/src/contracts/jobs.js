import { normalizeProgressResource } from "./progress.js";
import { normalizeTerminalJobStatus } from "./jobWarningStatus.js";

const RESULT_METRIC_KEYS_BY_JOB_TYPE = {
  manual_import: new Set(["imported_count", "pending_review_count", "pending_mapping_count", "skipped_count", "failed_count"]),
  export_excel: new Set(["new_records", "changed_records", "visible_count"]),
  one_click: new Set(["downloaded_count", "persisted_count", "exception_count", "pending_review_count", "pending_mapping_count", "mapping_conflict_count", "skipped_count", "failed_count"]),
  download_ingest: new Set(["downloaded_count", "persisted_count", "exception_count", "pending_review_count", "pending_mapping_count", "mapping_conflict_count", "skipped_count", "failed_count"]),
  archive_reprocess: new Set(["imported_count", "pending_review_count", "pending_mapping_count", "skipped_count", "failed_count"]),
  mapping_refresh: new Set(["downloaded_count", "persisted_count", "exception_count", "pending_review_count", "pending_mapping_count", "mapping_conflict_count", "skipped_count", "failed_count"]),
  business_re_evaluation: new Set(["pending_review_count", "pending_mapping_count", "mapping_conflict_count", "accepted_completed_count", "skipped_count", "failed_count"]),
};

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asMetrics(value, allowedKeys) {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item) => item && typeof item === "object")
    .map((item) => {
      const key = String(item.key || "").trim();
      if (!allowedKeys.has(key)) return null;
      return {
        key,
        label: String(item.label || "").trim(),
        value: Number.parseInt(item.value, 10) || 0,
      };
    })
    .filter(Boolean);
}

function normalizeScope(scope = {}) {
  const source = asObject(scope);
  const normalized = {
    record_family: String(source.record_family || "").trim(),
    state: String(source.state || "").trim(),
    exchange: String(source.exchange || "").trim(),
    keyword: String(source.keyword || "").trim(),
    date_from: String(source.date_from || "").trim(),
    date_to: String(source.date_to || "").trim(),
    page: Number.parseInt(source.page, 10) || 0,
    page_size: Number.parseInt(source.page_size, 10) || 0,
  };
  const businessId = String(source.business_id || "").trim();
  if (businessId) normalized.business_id = businessId;
  const businessLabel = String(source.business_label || "").trim();
  if (businessLabel) normalized.business_label = businessLabel;
  return normalized;
}

export function normalizeJobResource(resource = {}) {
  const source = asObject(resource);
  const jobType = String(source.job_type || "").trim();
  const counts = asObject(source.counts);
  const result = asObject(source.result);
  const actions = asObject(source.actions);
  const scope = source.scope && typeof source.scope === "object" ? normalizeScope(source.scope) : null;
  const normalized = {
    job_id: String(source.job_id || "").trim(),
    job_type: jobType,
    status: "",
    actions: {
      retry: actions.retry === true,
    },
    created_at: String(source.created_at || "").trim(),
    updated_at: String(source.updated_at || "").trim(),
    record_family: String(source.record_family || scope?.record_family || "").trim(),
    counts: {
      downloaded: Number.parseInt(counts.downloaded, 10) || 0,
      persisted: Number.parseInt(counts.persisted, 10) || 0,
      exceptions: Number.parseInt(counts.exceptions, 10) || 0,
    },
    progress: normalizeProgressResource(source.progress, { jobType }),
    result: {
      outcome: String(result.outcome || "").trim(),
      message: String(result.message || "").trim(),
      failure_code: String(result.failure_code || "").trim(),
      failure_message: String(result.failure_message || "").trim(),
      metrics: asMetrics(result.metrics, RESULT_METRIC_KEYS_BY_JOB_TYPE[jobType] || new Set()),
      artifact_count: Number.parseInt(result.artifact_count, 10) || 0,
      download_archive_audit: asObject(result.download_archive_audit),
      public_resource: asObject(result.public_resource),
    },
  };
  normalized.status = normalizeTerminalJobStatus({
    status: source.status,
    jobType,
    resultMetrics: normalized.result.metrics,
    progressMetrics: normalized.progress.metrics,
    counts: normalized.counts,
  });
  const businessId = String(source.business_id || scope?.business_id || "").trim();
  if (businessId) normalized.business_id = businessId;
  const businessLabel = String(source.business_label || scope?.business_label || "").trim();
  if (businessLabel) normalized.business_label = businessLabel;
  if (scope) {
    normalized.scope = scope;
  }
  return normalized;
}

export function normalizeJobsCollection(resource = {}) {
  const source = asObject(resource);
  return {
    jobs: Array.isArray(source.jobs) ? source.jobs.map((job) => normalizeJobResource(job)) : [],
  };
}

export function normalizeJobDetail(resource = {}) {
  return normalizeJobResource(resource);
}
