import { DEFAULT_RECORD_SCOPE, RecordScope } from "./contracts";
import { toPositiveNumber, toText } from "../lib/formatters";

function normalizeRecordScope(scope: RecordScope = {}) {
  return {
    recordFamily: toText(scope.recordFamily, DEFAULT_RECORD_SCOPE.recordFamily),
    state: toText(scope.state, DEFAULT_RECORD_SCOPE.state),
    projectType: toText(scope.projectType, DEFAULT_RECORD_SCOPE.projectType),
    keyword: toText(scope.keyword),
    dateFrom: toText(scope.dateFrom),
    dateTo: toText(scope.dateTo),
    page: toPositiveNumber(scope.page, DEFAULT_RECORD_SCOPE.page),
    pageSize: toPositiveNumber(scope.pageSize, DEFAULT_RECORD_SCOPE.pageSize),
  };
}

export function buildRecordsPath(scope: RecordScope = {}) {
  const normalized = normalizeRecordScope(scope);
  const query = new URLSearchParams({
    record_family: normalized.recordFamily,
    state: normalized.state,
    project_type: normalized.projectType,
    page: String(normalized.page),
    page_size: String(normalized.pageSize),
  });
  if (normalized.keyword) {
    query.set("keyword", normalized.keyword);
  }
  if (normalized.dateFrom) {
    query.set("date_from", normalized.dateFrom);
  }
  if (normalized.dateTo) {
    query.set("date_to", normalized.dateTo);
  }
  return `/api/records?${query.toString()}`;
}

export function buildJobsPath({ limit = 20 }: { limit?: number } = {}) {
  return `/api/jobs?limit=${toPositiveNumber(limit, 20)}`;
}

export function buildJobEventsPath(jobId: string, { limit = 200 }: { limit?: number } = {}) {
  return `/api/jobs/${encodeURIComponent(String(jobId || "").trim())}/events?limit=${toPositiveNumber(limit, 200)}`;
}

export function buildExportPayload(viewState: {
  scope?: RecordScope;
  dateFrom?: string;
  dateTo?: string;
  mode?: string;
  cursorKey?: string;
  outputDir?: string;
} = {}) {
  const normalized = normalizeRecordScope(viewState.scope || viewState);
  return {
    scope: {
      record_family: normalized.recordFamily,
      state: normalized.state,
      project_type: normalized.projectType,
      keyword: normalized.keyword,
      date_from: normalized.dateFrom,
      date_to: normalized.dateTo,
      page: normalized.page,
      page_size: normalized.pageSize,
    },
    date_from: normalized.dateFrom,
    date_to: normalized.dateTo,
    mode: toText(viewState.mode, "rebuild"),
    cursor_key: toText(viewState.cursorKey),
    output_dir: toText(viewState.outputDir),
  };
}

export function buildMappingPayload(draft: {
  sourceName?: string;
  targetValue?: string;
  notes?: string;
  matchField?: string;
  targetField?: string;
} = {}) {
  return {
    source_name: toText(draft.sourceName),
    target_value: toText(draft.targetValue),
    notes: toText(draft.notes),
    match_field: toText(draft.matchField, "transferor"),
    target_field: toText(draft.targetField, "group_name"),
  };
}
