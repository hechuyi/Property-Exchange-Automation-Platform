function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
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

function normalizeScopeStateCounts(counts = {}) {
  return Object.fromEntries(
    Object.entries(asObject(counts)).map(([key, value]) => [String(key || "").trim(), Number.parseInt(value, 10) || 0]),
  );
}

function normalizeMissingFields(value = []) {
  return Array.isArray(value)
    ? value.map((field) => {
      const source = asObject(field);
      return {
        kind: String(source.kind || "").trim(),
        field: String(source.field || "").trim(),
        canonical_field: String(source.canonical_field || "").trim(),
        export_field: String(source.export_field || "").trim(),
        message: String(source.message || "").trim(),
      };
    }).filter((field) => field.field || field.canonical_field || field.export_field || field.message)
    : [];
}

function normalizeFieldMissingAcknowledgement(value = {}) {
  const source = asObject(value);
  return {
    acknowledged: Boolean(source.acknowledged),
    missing_fields_hash: String(source.missing_fields_hash || "").trim(),
    revision_id: Number.parseInt(source.revision_id, 10) || 0,
    missing_fields: normalizeMissingFields(source.missing_fields),
  };
}

function normalizeAttention(value = {}) {
  const source = asObject(value);
  return {
    requires_attention: Boolean(source.requires_attention),
    suppressed: Boolean(source.suppressed),
    reason: String(source.reason || "").trim(),
  };
}

export function normalizeStreamingJobLaunchResult(resource = {}) {
  const source = asObject(resource);
  const scope = source.scope && typeof source.scope === "object" ? normalizeScope(source.scope) : null;
  const normalized = {
    job_id: String(source.job_id || "").trim(),
    job_type: String(source.job_type || "").trim(),
    db_path: String(source.db_path || "").trim(),
    input_dir: String(source.input_dir || "").trim(),
    discovered_count: Number.parseInt(source.discovered_count, 10) || 0,
    affected_count: Number.parseInt(source.affected_count, 10) || 0,
  };
  const recordFamily = String(source.record_family || scope?.record_family || "").trim();
  if (recordFamily) normalized.record_family = recordFamily;
  const businessId = String(source.business_id || scope?.business_id || "").trim();
  if (businessId) normalized.business_id = businessId;
  const businessLabel = String(source.business_label || scope?.business_label || "").trim();
  if (businessLabel) normalized.business_label = businessLabel;
  if (scope) {
    normalized.scope = scope;
  }
  return normalized;
}

export function normalizeExportActionResult(resource = {}) {
  const source = asObject(resource);
  const diagnostics = Array.isArray(source.field_missing_diagnostics)
    ? source.field_missing_diagnostics.map((item) => {
      const diagnostic = asObject(item);
        const normalizedDiagnostic = {
          record_id: String(diagnostic.record_id || "").trim(),
          revision_id: Number.parseInt(diagnostic.revision_id, 10) || 0,
          record_family: String(diagnostic.record_family || "").trim(),
          business_id: String(diagnostic.business_id || "").trim(),
          failure_code: String(diagnostic.failure_code || "").trim(),
          missing_fields: Array.isArray(diagnostic.missing_fields)
            ? diagnostic.missing_fields.map((field) => {
                const fieldSource = asObject(field);
                return {
                  kind: String(fieldSource.kind || "").trim(),
                  field: String(fieldSource.field || "").trim(),
                  canonical_field: String(fieldSource.canonical_field || "").trim(),
                  export_field: String(fieldSource.export_field || "").trim(),
                  message: String(fieldSource.message || "").trim(),
                };
              })
            : [],
        };
        const projectCode = String(diagnostic.project_code || "").trim();
        if (projectCode) normalizedDiagnostic.project_code = projectCode;
        const projectName = String(diagnostic.project_name || "").trim();
        if (projectName) normalizedDiagnostic.project_name = projectName;
        return normalizedDiagnostic;
      })
    : [];
  return {
    job_id: String(source.job_id || "").trim(),
    job_type: String(source.job_type || "").trim(),
    status: String(source.status || "").trim(),
    message: String(source.message || "").trim(),
    failure_code: String(source.failure_code || "").trim(),
    failure_message: String(source.failure_message || "").trim(),
    empty_reason_code: String(source.empty_reason_code || "").trim(),
    scope_state_counts: normalizeScopeStateCounts(source.scope_state_counts),
    scope: normalizeScope(source.scope),
    export_id: String(source.export_id || "").trim(),
    cursor_id: String(source.cursor_id || "").trim(),
    requested_export_mode: String(source.requested_export_mode || "").trim(),
    revision_watermark: Number.parseInt(source.revision_watermark, 10) || 0,
    field_missing_blocked_records: Number.parseInt(source.field_missing_blocked_records, 10) || 0,
    field_missing_diagnostics: diagnostics,
    new_records: Number.parseInt(source.new_records, 10) || 0,
    changed_records: Number.parseInt(source.changed_records, 10) || 0,
    artifacts: Array.isArray(source.artifacts) ? source.artifacts.map((item) => String(item || "").trim()).filter(Boolean) : [],
  };
}

export function normalizePathSelectionResult(resource = {}) {
  const source = asObject(resource);
  return {
    selected: Boolean(source.selected),
    path: String(source.path || "").trim(),
    selection_kind: String(source.selection_kind || "").trim(),
  };
}

export function normalizePathOpenResult(resource = {}) {
  const source = asObject(resource);
  return {
    opened: Boolean(source.opened),
    path: String(source.path || "").trim(),
    reveal: Boolean(source.reveal),
  };
}

export function normalizeRecordRevealResult(resource = {}) {
  const source = asObject(resource);
  return {
    opened: Boolean(source.opened),
    record_id: String(source.record_id || "").trim(),
    path: String(source.path || "").trim(),
    artifact_name: String(source.artifact_name || "").trim(),
  };
}

export function normalizeRecordFieldMissingAckResult(resource = {}) {
  const source = asObject(resource);
  return {
    record_id: String(source.record_id || "").trim(),
    state: String(source.state || "").trim(),
    exportable: Boolean(source.exportable),
    field_missing_acknowledgement: normalizeFieldMissingAcknowledgement(source.field_missing_acknowledgement),
    attention: normalizeAttention(source.attention),
  };
}

export function normalizeRecordReprocessResult(resource = {}) {
  const source = asObject(resource);
  return {
    record_id: String(source.record_id || "").trim(),
    state: String(source.state || "").trim(),
    project_code: String(source.project_code || "").trim(),
    archive_path: String(source.archive_path || "").trim(),
    error_code: String(source.error_code || "").trim(),
    error_message: String(source.error_message || "").trim(),
  };
}

export function normalizeRuntimeInstallResult(resource = {}) {
  const source = asObject(resource);
  return {
    status: String(source.status || "").trim(),
    browser_name: String(source.browser_name || "").trim(),
    trigger: String(source.trigger || "").trim(),
    attempt_count: Number.parseInt(source.attempt_count, 10) || 0,
    started_at: String(source.started_at || "").trim(),
    updated_at: String(source.updated_at || "").trim(),
    completed_at: String(source.completed_at || "").trim(),
    message: String(source.message || "").trim(),
    running: Boolean(source.running),
  };
}
