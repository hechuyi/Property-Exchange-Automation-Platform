function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function normalizeExistingEntry(entry = {}) {
  const source = asObject(entry);
  return {
    entry_id: String(source.entry_id || "").trim(),
    rule_title: String(source.rule_title || "").trim(),
    source_name: String(source.source_name || "").trim(),
    target_value: String(source.target_value || "").trim(),
  };
}

function normalizeResolution(resolution = {}) {
  const source = asObject(resolution);
  return {
    field: String(source.field || "").trim(),
    rule_kind: String(source.rule_kind || "").trim(),
    source_name: String(source.source_name || "").trim(),
    target_value: String(source.target_value || "").trim(),
  };
}

export function normalizeMappingPreviewResult(resource = {}) {
  const source = asObject(resource);
  return {
    conflict: Boolean(source.conflict),
    mode: String(source.mode || "").trim(),
    existing_entry: normalizeExistingEntry(source.existing_entry),
    affected_count: Number.parseInt(source.affected_count, 10) || 0,
    affected_pending_count: Number.parseInt(source.affected_pending_count, 10) || 0,
    match_field: String(source.match_field || "").trim(),
    target_field: String(source.target_field || "").trim(),
    target_value: String(source.target_value || "").trim(),
    source_name: String(source.source_name || "").trim(),
    rule_kind: String(source.rule_kind || "").trim(),
    rule_title: String(source.rule_title || "").trim(),
    source_label: String(source.source_label || "").trim(),
    target_label: String(source.target_label || "").trim(),
    scope_miss: Boolean(source.scope_miss),
    scope_miss_message: String(source.scope_miss_message || "").trim(),
  };
}

export function normalizeMappingSaveResult(resource = {}) {
    const source = asObject(resource);
    const preview = normalizeMappingPreviewResult(source);
    return {
    entry_id: String(source.entry_id || "").trim(),
    job_id: String(source.job_id || "").trim(),
    job_type: String(source.job_type || "").trim(),
    affected_count: Number.parseInt(source.affected_count, 10) || preview.affected_count,
    ...preview,
  };
}

export function normalizeMappingDeleteResult(resource = {}) {
  const source = asObject(resource);
  const entryId = String(source.entry_id || "").trim();
  const status = String(source.status || "").trim().toLowerCase();
  const failureMessage = String(source.failure_message || source.message || "").trim();
  if (source.deleted === false || status === "failed" || status === "failure" || status === "error") {
    throw new Error(failureMessage || `删除规则未成功${entryId ? `: ${entryId}` : ""}`);
  }
  return {
    entry_id: entryId,
    deleted: Boolean(source.deleted),
    job_id: String(source.job_id || "").trim(),
    job_type: String(source.job_type || "").trim(),
    affected_count: Number.parseInt(source.affected_count, 10) || 0,
  };
}

export function normalizeMappingUndoResult(resource = {}) {
  const source = asObject(resource);
  return {
    undone: source.undone === true,
    undo_kind: String(source.undo_kind || "").trim(),
    entry_id: String(source.entry_id || "").trim(),
  };
}

export function normalizeMappingConflictResolutionResult(resource = {}) {
  const source = asObject(resource);
  const blockerKind = String(source.blocker_kind || "").trim();
  const queueSection = String(source.queue_section || "").trim();
  return {
    job_id: String(source.job_id || "").trim(),
    job_type: String(source.job_type || "").trim(),
    affected_count: Number.parseInt(source.affected_count, 10) || 0,
    record_id: String(source.record_id || "").trim(),
    resolution_mode: String(source.resolution_mode || "").trim(),
    ...(blockerKind ? { blocker_kind: blockerKind } : {}),
    ...(queueSection ? { queue_section: queueSection } : {}),
    resolution: normalizeResolution(source.resolution),
  };
}
