function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function requireObjectField(source, fieldName, context) {
  const value = source[fieldName];
  if (value == null) return {};
  if (typeof value === "object" && !Array.isArray(value)) return value;
  throw new TypeError(`${context}.${fieldName} must be an object`);
}

function requireArrayField(source, fieldName, context) {
  const value = source[fieldName];
  if (value == null) return [];
  if (Array.isArray(value)) return value;
  throw new TypeError(`${context}.${fieldName} must be an array`);
}

function normalizeDisplayValues(values) {
  return Object.fromEntries(
    Object.entries(asObject(values)).map(([key, value]) => [String(key || "").trim(), value]),
  );
}

function normalizeMissingFields(value) {
  return asArray(value)
    .map((item) => {
      const source = asObject(item);
      return {
        kind: String(source.kind || "").trim(),
        field: String(source.field || "").trim(),
        canonical_field: String(source.canonical_field || "").trim(),
        export_field: String(source.export_field || "").trim(),
        message: String(source.message || "").trim(),
      };
    })
    .filter((item) => item.field || item.canonical_field || item.export_field || item.message);
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

function normalizeEvidenceVerdict(value = {}) {
  const source = asObject(value);
  return {
    status: String(source.status || "").trim(),
    logical_record_identity: String(source.logical_record_identity || "").trim(),
    identity_confidence: String(source.identity_confidence || "").trim(),
    authoritative_path: String(source.authoritative_path || "").trim(),
    inspection_openable_path: String(source.inspection_openable_path || "").trim(),
    reason_code: String(source.reason_code || "").trim(),
    safe_evidence: asObject(source.safe_evidence),
  };
}

const LOCAL_ARTIFACT_VERDICT_STATUSES = new Set([
  "verified",
  "present_unverified",
  "shared_official_page",
  "stale_reference",
  "invalid_shell",
  "identity_mismatch",
]);

function basename(pathValue) {
  const text = String(pathValue ?? "").trim();
  if (!text) return "";
  return text.split(/[\\/]+/).filter(Boolean).at(-1) || "";
}

function normalizeRecordRow(row = {}) {
  const source = asObject(row);
  const evidenceVerdict = normalizeEvidenceVerdict(source.evidence_verdict);
  const exportEligible = Boolean(source.export_eligible);
  const localArtifactName = LOCAL_ARTIFACT_VERDICT_STATUSES.has(evidenceVerdict.status)
    ? basename(evidenceVerdict.inspection_openable_path)
    : "";
  return {
    record_id: String(source.record_id || "").trim(),
    project_code: String(source.project_code || "").trim(),
    project_name: String(source.project_name || "").trim(),
    project_type_code: String(source.project_type_code || "").trim(),
    project_type_label: String(source.project_type_label || "").trim(),
    exchange_code: String(source.exchange_code || "").trim(),
    exchange_label: String(source.exchange_label || "").trim(),
    listing_date: String(source.listing_date || "").trim(),
    state: String(source.state || "").trim(),
    status_label: String(source.status_label || "").trim(),
    status_detail: String(source.status_detail || "").trim(),
    field_missing_acknowledgement: normalizeFieldMissingAcknowledgement(source.field_missing_acknowledgement),
    attention: normalizeAttention(source.attention),
    canonical_ready: Boolean(source.canonical_ready),
    evidence_status: String(source.evidence_status || evidenceVerdict.status || "").trim(),
    export_eligible: exportEligible,
    exportable: exportEligible,
    archive_path: String(source.archive_path || "").trim(),
    source_file: String(source.source_file || "").trim(),
    artifact_status: String(source.artifact_status || "").trim(),
    artifact_missing_reason: String(source.artifact_missing_reason || "").trim(),
    evidence_verdict: evidenceVerdict,
    has_local_artifact: Boolean(localArtifactName),
    local_artifact_name: localArtifactName,
    updated_at: String(source.updated_at || "").trim(),
    seller: String(source.seller || "").trim(),
    price: String(source.price || "").trim(),
    display_values: normalizeDisplayValues(source.display_values),
  };
}

function normalizeRecordSummary(summary = {}) {
  const source = asObject(summary);
  return {
    filtered_state_counts: asObject(source.filtered_state_counts),
    page_state_counts: asObject(source.page_state_counts),
    total_count: Number.parseInt(source.total_count, 10) || 0,
    visible_count: Number.parseInt(source.visible_count, 10) || 0,
    page: Number.parseInt(source.page, 10) || 0,
    page_size: Number.parseInt(source.page_size, 10) || 0,
    page_count: Number.parseInt(source.page_count, 10) || 0,
  };
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

export function normalizeRecordsResource(resource = {}) {
  const source = asObject(resource);
  try {
    const summary = normalizeRecordSummary(requireObjectField(source, "summary", "Invalid records resource"));
    const scope = normalizeScope(requireObjectField(source, "scope", "Invalid records resource"));
    return {
      record_family: String(source.record_family || "").trim() || scope.record_family,
      scope,
      display_columns: requireArrayField(source, "display_columns", "Invalid records resource")
        .map((column) => String(column || "").trim())
        .filter(Boolean),
      rows: requireArrayField(source, "rows", "Invalid records resource").map((row) => normalizeRecordRow(row)),
      summary,
      total_count: summary.total_count,
      visible_count: summary.visible_count,
      page: summary.page,
      page_size: summary.page_size,
      page_count: summary.page_count,
      has_more: Boolean(source.has_more) && summary.page < summary.page_count,
    };
  } catch (error) {
    if (error instanceof TypeError && /^Invalid records resource\./.test(error.message)) {
      throw new TypeError(`Invalid records resource: ${error.message.slice("Invalid records resource.".length)}`);
    }
    throw error;
  }
}
