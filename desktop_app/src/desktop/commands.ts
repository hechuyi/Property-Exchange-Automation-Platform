import { DesktopHttpClient } from "./http";
import { buildExportPayload, buildJobEventsPath, buildJobsPath, buildMappingPayload, buildRecordsPath } from "./queries";

function normalizeMappingDraftPayload(draft: Record<string, any> = {}) {
  const hasSnakeCaseMappingFields = [
    "source_name",
    "target_value",
    "match_field",
    "target_field",
    "confirm_overwrite",
  ].some((key) => Object.prototype.hasOwnProperty.call(draft, key));
  if (!hasSnakeCaseMappingFields) {
    return buildMappingPayload(draft);
  }
  return {
    source_name: String(draft.source_name ?? "").trim(),
    target_value: String(draft.target_value ?? "").trim(),
    notes: String(draft.notes ?? "").trim(),
    match_field: String(draft.match_field || "transferor").trim() || "transferor",
    target_field: String(draft.target_field || "group_name").trim() || "group_name",
    ...(draft.confirm_overwrite === true ? { confirm_overwrite: true } : {}),
  };
}

export function createDesktopCommands({ client }: { client: DesktopHttpClient }) {
  return {
    getOverview: () => client("/api/overview"),
    listJobs: ({ limit = 20 }: { limit?: number } = {}) => client(buildJobsPath({ limit })),
    getJob: (jobId: string) => client(`/api/jobs/${encodeURIComponent(String(jobId || "").trim())}`),
    listJobEvents: (jobId: string, { limit = 200 }: { limit?: number } = {}) =>
      client(buildJobEventsPath(jobId, { limit })),
    listRecords: (scope = {}) => client(buildRecordsPath(scope)),
    listMappings: () => client("/api/mappings"),
    runOneClick: (payload = {}) => client("/api/jobs/one-click", { method: "POST", body: JSON.stringify(payload) }),
    runManualImport: (payload = {}) =>
      client("/api/jobs/manual-import", { method: "POST", body: JSON.stringify(payload) }),
    runExport: (viewState = {}) =>
      client("/api/exports", { method: "POST", body: JSON.stringify(buildExportPayload(viewState)) }),
    saveMapping: (draft = {}) =>
      client("/api/mappings", { method: "POST", body: JSON.stringify(normalizeMappingDraftPayload(draft as Record<string, any>)) }),
    previewMapping: (draft = {}) =>
      client("/api/mappings/preview", { method: "POST", body: JSON.stringify(normalizeMappingDraftPayload(draft as Record<string, any>)) }),
    reprocessPendingMappings: (payload = {}) =>
      client("/api/mappings/reprocess-pending", { method: "POST", body: JSON.stringify(payload) }),
    reprocessRecord: (recordId: string, payload = {}) =>
      client(`/api/records/${encodeURIComponent(String(recordId || "").trim())}/reprocess`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  };
}
