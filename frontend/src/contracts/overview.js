import { normalizeRuntimeResource } from "./runtime.js";
import { normalizeProgressResource } from "./progress.js";
import { normalizeJobResource } from "./jobs.js";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

export function normalizeOverviewResource(resource = {}) {
  const source = asObject(resource);
  const recordSummary = asObject(source.record_summary);
  const defaults = asObject(source.defaults);
  const latestJob = source.latest_job && typeof source.latest_job === "object"
    ? normalizeJobResource(source.latest_job)
    : null;
  return {
    record_summary: {
      state_counts: asObject(recordSummary.state_counts),
      pending_mapping_count: Number.parseInt(recordSummary.pending_mapping_count, 10) || 0,
    },
    latest_job: latestJob,
    latest_progress: normalizeProgressResource(source.latest_progress, { jobType: latestJob?.job_type || "" }),
    recent_jobs: asArray(source.recent_jobs).map((job) => normalizeJobResource(job)),
    runtime: normalizeRuntimeResource(source.runtime),
    defaults: {
      manual_import_input_dir: String(defaults.manual_import_input_dir || "").trim(),
      archive_root: String(defaults.archive_root || "").trim(),
      default_scope: asObject(defaults.default_scope),
    },
    visibility: asObject(source.visibility),
  };
}
