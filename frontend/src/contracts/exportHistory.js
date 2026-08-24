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
  throw new TypeError(`${context}: ${fieldName} must be an object`);
}

function requireArrayField(source, fieldName, context) {
  const value = source[fieldName];
  if (value == null) return [];
  if (Array.isArray(value)) return value;
  throw new TypeError(`${context}: ${fieldName} must be an array`);
}

function requireIntegerField(source, fieldName, context) {
  const value = source[fieldName];
  if (value == null || typeof value === "number" || typeof value === "string") return value;
  throw new TypeError(`${context}: ${fieldName} must be a number or numeric string`);
}

function asText(value) {
  return String(value ?? "").trim();
}

function asInt(value) {
  return Number.parseInt(value, 10) || 0;
}

function normalizeBoolean(value) {
  return Boolean(value);
}

function normalizeRetentionStatus(source = {}) {
  const data = asObject(source);
  if (data.pruned_by_retention) return "pruned_by_retention";
  if (asArray(data.missing_artifacts).length && asArray(data.existing_artifacts).length) return "artifact_incomplete";
  if (data.is_tombstone) return "artifact_unavailable";
  return "available";
}

function normalizeScope(scope = {}) {
  const source = asObject(scope);
  return {
    record_family: asText(source.record_family),
    business_id: asText(source.business_id),
    business_label: asText(source.business_label),
    exchange: asText(source.exchange),
  };
}

function normalizeCursorBasis(value = {}) {
  const source = asObject(value);
  return {
    export_id: asText(source.export_id),
    cursor_id: asText(source.cursor_id),
    eligible_set_hash: asText(source.eligible_set_hash),
    revision_watermark: asInt(source.revision_watermark),
  };
}

function normalizeManifest(value = {}) {
  const source = asObject(value);
  return {
    requested_export_mode: asText(source.requested_export_mode),
    effective_export_mode: asText(source.effective_export_mode),
    export_profile_id: asText(source.export_profile_id),
    canonical_scope_hash: asText(source.canonical_scope_hash),
    schema_version: asText(source.schema_version),
    header_version: asText(source.header_version),
    cursor_id: asText(source.cursor_id),
    cursor_basis: normalizeCursorBasis(source.cursor_basis),
    scope: normalizeScope(source.scope),
    included_count: asInt(source.included_count),
    excluded_count: asInt(source.excluded_count),
    field_missing_blocked_records: asInt(source.field_missing_blocked_records),
    artifact_checksum: asText(source.artifact_checksum),
    artifact_checksums: asObject(source.artifact_checksums),
    revision_watermark: asInt(source.revision_watermark),
  };
}

function normalizeCursorValue(value = {}) {
  const source = asObject(value);
  return {
    last_successful_revision_watermark: asInt(source.last_successful_revision_watermark),
    last_successful_export_id: asText(source.last_successful_export_id),
    cursor_basis_export_id: asText(source.cursor_basis_export_id),
    eligible_set_hash: asText(source.eligible_set_hash),
    canonical_scope_hash: asText(source.canonical_scope_hash),
  };
}

export function normalizeExportHistoryRow(value = {}) {
  const source = asObject(value);
  return {
    export_id: asText(source.export_id),
    cursor_id: asText(source.cursor_id),
    requested_export_mode: asText(source.requested_export_mode),
    revision_watermark: asInt(source.revision_watermark),
    created_at: asText(source.created_at),
    artifact_count: asInt(source.artifact_count),
    openable: normalizeBoolean(source.openable),
    rebuildable: normalizeBoolean(source.rebuildable),
    is_tombstone: normalizeBoolean(source.is_tombstone),
    pruned_by_retention: normalizeBoolean(source.pruned_by_retention),
    retention_status: asText(source.retention_status) || normalizeRetentionStatus(source),
    retention_count: asInt(source.retention_count) || 20,
  };
}

export function normalizeExportHistoryCollection(resource = {}) {
  const source = asObject(resource);
  return {
    rows: asArray(source.rows).map(normalizeExportHistoryRow).filter((row) => row.export_id),
  };
}

export function normalizeExportHistoryDetail(resource = {}) {
  const source = asObject(resource);
  return {
    export_id: asText(source.export_id),
    cursor_id: asText(source.cursor_id),
    requested_export_mode: asText(source.requested_export_mode),
    revision_watermark: asInt(source.revision_watermark),
    created_at: asText(source.created_at),
    artifacts: requireArrayField(source, "artifacts", "Invalid export history detail").map(asText).filter(Boolean),
    existing_artifacts: asArray(source.existing_artifacts).map(asText).filter(Boolean),
    missing_artifacts: asArray(source.missing_artifacts).map(asText).filter(Boolean),
    manifest: normalizeManifest(requireObjectField(source, "manifest", "Invalid export history detail")),
    cursor_value: normalizeCursorValue(requireObjectField(source, "cursor_value", "Invalid export history detail")),
    openable: normalizeBoolean(source.openable),
    rebuildable: normalizeBoolean(source.rebuildable),
    is_tombstone: normalizeBoolean(source.is_tombstone),
    pruned_by_retention: normalizeBoolean(source.pruned_by_retention),
    retention_status: asText(source.retention_status) || normalizeRetentionStatus(source),
    retention_count: asInt(requireIntegerField(source, "retention_count", "Invalid export history detail")) || 20,
  };
}

export function normalizeExportHistoryActionResult(resource = {}) {
  const source = asObject(resource);
  return {
    export_id: asText(source.export_id),
    opened: normalizeBoolean(source.opened),
    downloaded: normalizeBoolean(source.downloaded),
    path: asText(source.path),
    artifacts: asArray(source.artifacts).map(asText).filter(Boolean),
    openable: normalizeBoolean(source.openable),
    rebuildable: normalizeBoolean(source.rebuildable),
    is_tombstone: normalizeBoolean(source.is_tombstone),
    retention_status: asText(source.retention_status) || normalizeRetentionStatus(source),
  };
}
