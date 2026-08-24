function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function normalizeScopeStateCounts(counts = {}) {
  const source = asObject(counts);
  return Object.fromEntries(
    Object.entries(source)
      .map(([key, value]) => [String(key || "").trim(), Number.parseInt(value, 10) || 0])
      .filter(([key]) => key),
  );
}

const EVENT_SUMMARY_KEYS = [
  "listed",
  "pages",
  "collected_candidates",
  "detail_candidates",
  "detail_fetched",
  "saved",
  "list_date_skipped",
  "detail_date_skipped",
  "date_missing_skipped",
  "resume_skipped",
  "errors",
  "duplicate_skipped",
  "business_filter_skipped",
  "missing_xmid_skipped",
  "detail_unavailable_skipped",
  "detail_failed",
  "list_unaccounted",
  "detail_unaccounted",
  "record_count",
  "period_index",
  "period_total",
  "page",
  "official_total",
  "current",
  "total",
  "selected",
  "excluded",
  "failed",
  "attempt",
  "attempt_total",
  "retry_in_seconds",
  "business_code",
];
const EVENT_SUMMARY_TEXT_KEYS = [
  "warning_code",
  "warning_message",
  "status",
  "workbook",
  "evidence_root",
  "archive_root",
  "error_type",
  "error_code",
  "error_message",
  "failure_code",
  "failure_message",
  "month",
  "phase",
  "time_begin",
  "time_end",
  "transport",
  "role",
];

function normalizeSummary(summary = {}) {
  const source = asObject(summary);
  const normalized = {};
  EVENT_SUMMARY_KEYS.forEach((key) => {
    if (!Object.prototype.hasOwnProperty.call(source, key)) return;
    const parsed = Number.parseInt(source[key], 10);
    if (Number.isNaN(parsed)) return;
    normalized[key] = parsed;
  });
  EVENT_SUMMARY_TEXT_KEYS.forEach((key) => {
    const value = String(source[key] || "").trim();
    if (value) normalized[key] = value;
  });
  return normalized;
}

function normalizeEvent(event = {}) {
  const source = asObject(event);
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
  const summary = normalizeSummary(source.summary);
  const normalized = {
    event_id: String(source.event_id || "").trim(),
    record_family: String(source.record_family || scope?.record_family || "").trim(),
    business_id: String(source.business_id || scope?.business_id || "").trim(),
    business_label: String(source.business_label || scope?.business_label || "").trim(),
    stage_code: String(source.stage_code || "").trim(),
    stage_label: String(source.stage_label || "").trim(),
    status: String(source.status || "").trim(),
    label: String(source.label || "").trim(),
    source_id: String(source.source_id || "").trim(),
    kind: String(source.kind || "").trim(),
    task_label: String(source.task_label || "").trim(),
    task_index: Number.parseInt(source.task_index, 10) || 0,
    task_total: Number.parseInt(source.task_total, 10) || 0,
    phase_percent: Number.parseInt(source.phase_percent, 10) || 0,
    summary,
    project_code: String(source.project_code || "").trim(),
    record_state: String(source.record_state || "").trim(),
    error_code: String(source.error_code || "").trim(),
    error_message: String(source.error_message || "").trim(),
    warning_code: String(source.warning_code || summary.warning_code || "").trim(),
    warning_message: String(source.warning_message || summary.warning_message || "").trim(),
    empty_reason_code: String(source.empty_reason_code || "").trim(),
    scope_state_counts: normalizeScopeStateCounts(source.scope_state_counts),
  };
  if (scope) {
    normalized.scope = scope;
  }
  return normalized;
}

export function normalizeJobEventList(events = []) {
  const seenEventIds = new Set();
  return asArray(events)
    .map((event) => normalizeEvent(event))
    .filter((event) => {
      const eventId = String(event.event_id || "").trim();
      if (!eventId) return true;
      if (seenEventIds.has(eventId)) return false;
      seenEventIds.add(eventId);
      return true;
    });
}

export function normalizeJobEventsResource(resource = {}) {
  const source = asObject(resource);
  return {
    events: normalizeJobEventList(source.events),
    returned_count: Number.parseInt(source.returned_count, 10) || 0,
    total_count: Number.parseInt(source.total_count, 10) || 0,
    truncated: Boolean(source.truncated),
  };
}
