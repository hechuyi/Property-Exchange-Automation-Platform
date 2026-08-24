import { normalizeCatalogResource } from "./src/contracts/catalog.js";
import { buildExportRequest, buildRecordScopeQuery } from "./src/contracts/recordScope.js";
import {
  buildManualImportRequest,
  buildMappingConflictResolutionRequest,
  buildMappingRequest,
  buildOneClickRequest,
  buildPathOpenRequest,
  buildPathSelectionRequest,
  buildRuntimeInstallRequest,
} from "./src/contracts/actionRequests.js";
import {
  normalizeExportActionResult,
  normalizePathOpenResult,
  normalizePathSelectionResult,
  normalizeRecordFieldMissingAckResult,
  normalizeRecordReprocessResult,
  normalizeRecordRevealResult,
  normalizeRuntimeInstallResult,
  normalizeStreamingJobLaunchResult,
} from "./src/contracts/actionResults.js";
import { normalizeOverviewResource } from "./src/contracts/overview.js";
import { normalizeRuntimeResource } from "./src/contracts/runtime.js";
import { normalizeJobDetail, normalizeJobsCollection } from "./src/contracts/jobs.js";
import { normalizeJobEventsResource } from "./src/contracts/jobEvents.js";
import {
  normalizeExportHistoryActionResult,
  normalizeExportHistoryCollection,
  normalizeExportHistoryDetail,
} from "./src/contracts/exportHistory.js";
import {
  normalizeMappingConflictResolutionResult,
  normalizeMappingDeleteResult,
  normalizeMappingPreviewResult,
  normalizeMappingSaveResult,
  normalizeMappingUndoResult,
} from "./src/contracts/mappingActions.js";
import { normalizeMappingsResource } from "./src/contracts/mappings.js";
import { buildReviewProblemsQuery, normalizeReviewProblemsResource } from "./src/contracts/reviewProblems.js";
import { normalizeRecordsResource } from "./src/contracts/records.js";
import {
  buildAdvancedSettingsRequest,
  buildBasicSettingsRequest,
  normalizeAdvancedSettingsResource,
  normalizeBasicSettingsResource,
} from "./src/contracts/settings.js";
import {
  DESKTOP_API_TOKEN_HEADER,
  resolveBrowserBackendConfig,
} from "./src/backendConfig.js";
import { readTransportData } from "./src/contracts/transport.js";

/* ── API Client ── */
export const API = {
  base: "",
  apiToken: "",

  async request(path, options = {}) {
    const init = { ...options };
    const headers = { ...(init.headers || {}) };
    const runtimeConfig = resolveBrowserBackendConfig();
    const requestBase = String(runtimeConfig.baseUrl || this.base || "").trim();
    const apiToken = String(runtimeConfig.apiToken || this.apiToken || "").trim();
    if (init.body != null && !(init.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    if (apiToken && !headers[DESKTOP_API_TOKEN_HEADER]) {
      headers[DESKTOP_API_TOKEN_HEADER] = apiToken;
    }
    if (init.body != null && typeof init.body !== "string" && !(init.body instanceof FormData)) {
      init.body = JSON.stringify(init.body);
    }
    init.headers = headers;

    const res = await fetch(`${requestBase}${path}`, init);
    return readTransportData(res, path);
  },

  /* GET /api/overview */
  async getOverview() {
    return normalizeOverviewResource(await this.request("/api/overview"));
  },

  /* GET /api/catalog */
  async getCatalog() {
    return normalizeCatalogResource(await this.request("/api/catalog"));
  },

  /* GET /api/jobs?limit=N */
  async listJobs(limit = 50) {
    return normalizeJobsCollection(await this.request(`/api/jobs?limit=${limit}`));
  },

  /* GET /api/jobs/{job_id} */
  async getJob(jobId) {
    return normalizeJobDetail(await this.request(`/api/jobs/${encodeURIComponent(jobId)}`));
  },

  /* GET /api/jobs/{job_id}/events?limit=N */
  async getJobEvents(jobId, limit = 200) {
    return normalizeJobEventsResource(await this.request(`/api/jobs/${encodeURIComponent(jobId)}/events?limit=${limit}`));
  },

  /* POST /api/jobs/{job_id}/retry */
  async retryJob(jobId) {
    return normalizeStreamingJobLaunchResult(await this.request(`/api/jobs/${encodeURIComponent(jobId)}/retry`, {
      method: "POST",
      body: {},
    }));
  },

  /* GET /api/records — uses query params */
  async listRecords(params = {}) {
    return normalizeRecordsResource(await this.request(`/api/records?${buildRecordScopeQuery(params)}`));
  },

  /* POST /api/jobs/one-click */
  async runOneClick(payload = {}) {
    return normalizeStreamingJobLaunchResult(await this.request("/api/jobs/one-click", { method: "POST", body: buildOneClickRequest(payload) }));
  },

  /* POST /api/jobs/download-ingest */
  async runHistorical(payload = {}) {
    return normalizeStreamingJobLaunchResult(await this.request("/api/jobs/download-ingest", { method: "POST", body: buildOneClickRequest(payload) }));
  },

  /* POST /api/jobs/manual-import */
  async runManualImport(payload = {}) {
    return normalizeStreamingJobLaunchResult(await this.request("/api/jobs/manual-import", {
      method: "POST",
      body: buildManualImportRequest(payload),
    }));
  },

  /* POST /api/jobs/archive-reprocess */
  async runArchiveReprocess() {
    return normalizeStreamingJobLaunchResult(await this.request("/api/jobs/archive-reprocess", {
      method: "POST",
      body: {},
    }));
  },

  /* POST /api/exports */
  async runExport(scope = {}, requestedExportMode = "full") {
    return normalizeExportActionResult(await this.request("/api/exports", {
      method: "POST",
      body: buildExportRequest(
        typeof scope === "string" ? { record_family: scope } : scope,
        { requested_export_mode: requestedExportMode },
      ),
    }));
  },

  /* GET /api/exports/history?limit=N */
  async listExportHistory(limit = 100) {
    return normalizeExportHistoryCollection(await this.request(`/api/exports/history?limit=${limit}`));
  },

  /* GET /api/exports/history/{export_id} */
  async getExportHistoryDetail(exportId) {
    return normalizeExportHistoryDetail(await this.request(`/api/exports/history/${encodeURIComponent(exportId)}`));
  },

  /* POST /api/exports/history/{export_id}/open */
  async openExportHistory(exportId) {
    return normalizeExportHistoryActionResult(await this.request(`/api/exports/history/${encodeURIComponent(exportId)}/open`, {
      method: "POST",
      body: {},
    }));
  },

  /* POST /api/exports/history/{export_id}/download */
  async downloadExportHistory(exportId, outputDir = "") {
    const body = String(outputDir || "").trim() ? { output_dir: String(outputDir || "").trim() } : {};
    return normalizeExportHistoryActionResult(await this.request(`/api/exports/history/${encodeURIComponent(exportId)}/download`, {
      method: "POST",
      body,
    }));
  },

  /* GET /api/mappings */
  async listMappings() {
    return normalizeMappingsResource(await this.request("/api/mappings"));
  },

  /* GET /api/review-problems */
  async listReviewProblems(params = {}) {
    const query = buildReviewProblemsQuery(params);
    return normalizeReviewProblemsResource(await this.request(`/api/review-problems${query ? `?${query}` : ""}`));
  },

  /* POST /api/mappings/preview */
  async previewMapping(draft) {
    return normalizeMappingPreviewResult(await this.request("/api/mappings/preview", {
      method: "POST",
      body: buildMappingRequest(draft),
    }));
  },

  /* POST /api/mappings */
  async saveMapping(draft) {
    return normalizeMappingSaveResult(await this.request("/api/mappings", {
      method: "POST",
      body: buildMappingRequest(draft),
    }));
  },

  /* PUT /api/mappings/{entry_id} */
  async updateMapping(entryId, draft) {
    return normalizeMappingSaveResult(await this.request(`/api/mappings/${encodeURIComponent(entryId)}`, {
      method: "PUT",
      body: buildMappingRequest(draft),
    }));
  },

  /* DELETE /api/mappings/{entry_id} */
  async deleteMapping(entryId) {
    return normalizeMappingDeleteResult(await this.request(`/api/mappings/${encodeURIComponent(entryId)}`, {
      method: "DELETE",
    }));
  },

  /* POST /api/mappings/undo */
  async undoMapping(startupSessionId) {
    const normalizedSessionId = String(startupSessionId || "").trim();
    if (!normalizedSessionId) throw new Error("startup_session_id is required");
    return normalizeMappingUndoResult(await this.request("/api/mappings/undo", {
      method: "POST",
      body: { startup_session_id: normalizedSessionId },
    }));
  },

  /* POST /api/mappings/resolve-conflict */
  async resolveMappingConflict(payload) {
    return normalizeMappingConflictResolutionResult(await this.request("/api/mappings/resolve-conflict", {
      method: "POST",
      body: buildMappingConflictResolutionRequest(payload),
    }));
  },

  /* POST /api/mappings/reprocess-pending */
  async reprocessPendingMappings() {
    return normalizeStreamingJobLaunchResult(await this.request("/api/mappings/reprocess-pending", {
      method: "POST",
      body: {},
    }));
  },

  async chooseLocalPath(payload = {}) {
    return normalizePathSelectionResult(await this.request("/api/system/select-path", {
      method: "POST",
      body: buildPathSelectionRequest(payload),
    }));
  },

  async openLocalPath(payload = {}) {
    return normalizePathOpenResult(await this.request("/api/system/open-path", {
      method: "POST",
      body: buildPathOpenRequest(payload),
    }));
  },

  async revealRecordFolder(recordId) {
    return normalizeRecordRevealResult(await this.request(`/api/records/${encodeURIComponent(recordId)}/reveal-folder`, {
      method: "POST",
      body: {},
    }));
  },

  async acknowledgeRecordFieldMissing(recordId) {
    return normalizeRecordFieldMissingAckResult(
      await this.request(`/api/records/${encodeURIComponent(recordId)}/field-missing/acknowledge`, {
        method: "POST",
        body: {},
      }),
    );
  },

  /* POST /api/records/{record_id}/reprocess */
  async reprocessRecord(recordId) {
    return normalizeRecordReprocessResult(await this.request(`/api/records/${encodeURIComponent(recordId)}/reprocess`, {
      method: "POST",
      body: {},
    }));
  },

  /* GET /api/settings/basic */
  async getSettingsBasic() {
    return normalizeBasicSettingsResource(await this.request("/api/settings/basic"));
  },

  /* POST /api/settings/basic */
  async saveSettingsBasic(payload) {
    const source = payload && typeof payload === "object" ? payload : {};
    const body = (
      Object.prototype.hasOwnProperty.call(source, "stored_preference")
      || Object.prototype.hasOwnProperty.call(source, "paths")
    )
      ? source
      : buildBasicSettingsRequest(payload);
    return normalizeBasicSettingsResource(await this.request("/api/settings/basic", {
      method: "POST",
      body,
    }));
  },

  /* GET /api/settings/advanced */
  async getSettingsAdvanced() {
    return normalizeAdvancedSettingsResource(await this.request("/api/settings/advanced"));
  },

  /* POST /api/settings/advanced */
  async saveSettingsAdvanced(payload) {
    return normalizeAdvancedSettingsResource(await this.request("/api/settings/advanced", {
      method: "POST",
      body: buildAdvancedSettingsRequest(payload),
    }));
  },

  /* GET /api/runtime/dependencies */
  async getRuntimeDependencies() {
    return normalizeRuntimeResource(await this.request("/api/runtime/dependencies"));
  },

  /* POST /api/runtime/install-browser */
  async installBrowser(payload = {}) {
    return normalizeRuntimeInstallResult(await this.request("/api/runtime/install-browser", { method: "POST", body: buildRuntimeInstallRequest(payload) }));
  },
};
