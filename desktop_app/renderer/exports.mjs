function countValue(value) {
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

function readText(source, keys, defaultValue = "") {
  for (const key of keys) {
    const value = source?.[key];
    const text = String(value ?? "").trim();
    if (text) {
      return text;
    }
  }
  return defaultValue;
}

function readNumber(source, keys, defaultValue) {
  for (const key of keys) {
    if (source && Object.prototype.hasOwnProperty.call(source, key)) {
      const value = Number(source[key]);
      if (Number.isFinite(value)) {
        return value;
      }
    }
  }
  return defaultValue;
}

function normalizeExportScope(viewState = {}) {
  const scopeSource = viewState && typeof viewState.scope === "object" && viewState.scope !== null
    ? viewState.scope
    : viewState;
  return {
    record_family: readText(scopeSource, ["record_family", "recordFamily"], "listing"),
    state: readText(scopeSource, ["state"], "all"),
    project_type: readText(scopeSource, ["project_type", "projectType"], "all"),
    keyword: readText(scopeSource, ["keyword"], ""),
    date_from: readText(scopeSource, ["date_from", "dateFrom", "exportDateFrom"], ""),
    date_to: readText(scopeSource, ["date_to", "dateTo", "exportDateTo"], ""),
    page: readNumber(scopeSource, ["page"], 1) || 1,
    page_size: readNumber(scopeSource, ["page_size", "pageSize"], 50) || 50,
  };
}

export function buildExportRequestFromView(viewState = {}) {
  const scope = normalizeExportScope(viewState);
  return {
    scope,
    date_from: scope.date_from,
    date_to: scope.date_to,
    mode: readText(viewState, ["mode"], "rebuild"),
    cursor_key: readText(viewState, ["cursor_key", "cursorKey"], ""),
    output_dir: readText(viewState, ["output_dir", "outputDir"], ""),
  };
}

export function formatEmptyExportMessage(result = {}) {
  const emptyReasonCode = String(result.empty_reason_code || "").trim();
  const scopeStateCounts = result.scope_state_counts && typeof result.scope_state_counts === "object"
    ? result.scope_state_counts
    : {};
  const pendingCount = countValue(scopeStateCounts.pending_mapping);
  const skippedCount = countValue(scopeStateCounts.skipped);

  if (emptyReasonCode === "pending_mapping_blocked") {
    return pendingCount > 0
      ? `当前条件下没有可导出的记录；待补映射 ${pendingCount} 条`
      : "当前条件下没有可导出的记录";
  }
  if (emptyReasonCode === "skipped_only") {
    return skippedCount > 0
      ? `当前条件下没有可导出的记录；已跳过 ${skippedCount} 条`
      : "当前条件下没有可导出的记录";
  }
  if (emptyReasonCode === "no_matching_records") {
    return "当前条件下没有可导出的记录";
  }
  return String(result.message || "当前条件下没有可导出的记录");
}
