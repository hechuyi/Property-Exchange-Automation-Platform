function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asRequestBoolean(value, fieldName, defaultValue = false) {
  if (typeof value === "boolean") return value;
  if (value == null) return defaultValue;
  throw new TypeError(`${fieldName} must be a boolean`);
}

function normalizeScope(scope = {}) {
  const source = asObject(scope);
  return {
    record_family: String(source.record_family || "").trim(),
    business_id: String(source.business_id || "").trim(),
    business_label: String(source.business_label || "").trim(),
    exchange: String(source.exchange || "").trim(),
  };
}

function normalizeStaleMetadata(metadata = {}) {
  const source = asObject(metadata);
  return {
    is_stale: Boolean(source.is_stale),
    reason: String(source.reason || "").trim(),
    hint: String(source.hint || "").trim(),
  };
}

export function normalizeBasicSettingsResource(resource = {}) {
  const source = asObject(resource);
  const paths = asObject(source.paths);
  const retentionCount = Number.parseInt(source.retention_count, 10);
  return {
    effective_default_scope: normalizeScope(source.effective_default_scope),
    stored_preference: normalizeScope(source.stored_preference),
    stale_default_metadata: normalizeStaleMetadata(source.stale_default_metadata),
    default_exchange: String(source.default_exchange || "").trim(),
    default_concurrency: Number.parseInt(source.default_concurrency, 10) || 0,
    retention_count: retentionCount > 0 ? retentionCount : 20,
    paths: {
      workspace_root: String(paths.workspace_root || "").trim(),
      archive_root: String(paths.archive_root || "").trim(),
      export_root: String(paths.export_root || "").trim(),
    },
  };
}

export function normalizeAdvancedSettingsResource(resource = {}) {
  const source = asObject(resource);
  const processing = asObject(source.processing);
  const ingestPaths = asObject(source.ingest_paths);
  const runtimePaths = asObject(source.runtime_paths);
  return {
    effective_default_scope: normalizeScope(source.effective_default_scope),
    stored_preference: normalizeScope(source.stored_preference),
    stale_default_metadata: normalizeStaleMetadata(source.stale_default_metadata),
    processing: {
      save_json: Boolean(processing.save_json),
      postprocess_config: String(processing.postprocess_config || "").trim(),
    },
    ingest_paths: {
      raw_manual_root: String(ingestPaths.raw_manual_root || "").trim(),
      raw_auto_root: String(ingestPaths.raw_auto_root || "").trim(),
    },
    runtime_paths: {
      app_home: String(runtimePaths.app_home || "").trim(),
      streaming_db: String(runtimePaths.streaming_db || "").trim(),
      log_dir: String(runtimePaths.log_dir || "").trim(),
      cache_dir: String(runtimePaths.cache_dir || "").trim(),
      browser_cache_dir: String(runtimePaths.browser_cache_dir || "").trim(),
      archive_root: String(runtimePaths.archive_root || "").trim(),
      export_root: String(runtimePaths.export_root || "").trim(),
    },
  };
}

export function buildBasicSettingsRequest(payload = {}) {
  const source = asObject(payload);
  const paths = asObject(source.paths);
  const retentionCount = Number.parseInt(source.retention_count, 10);
  if ("retention_count" in source && (!Number.isInteger(retentionCount) || retentionCount < 1)) {
    throw new Error("retention_count must be a positive integer");
  }
  const hasExplicitStoredPreference = Object.prototype.hasOwnProperty.call(source, "stored_preference");
  const storedPreferenceSource = asObject(source.stored_preference);
  const storedPreference = {
    record_family: String(storedPreferenceSource.record_family || source.record_family || "").trim(),
    business_id: String(storedPreferenceSource.business_id || source.business_id || "").trim(),
    exchange: String(storedPreferenceSource.exchange || source.exchange || "").trim(),
  };
  const request = {
    default_exchange: String(source.default_exchange || "").trim(),
    default_concurrency: Number.parseInt(source.default_concurrency, 10) || 0,
    retention_count: retentionCount > 0 ? retentionCount : undefined,
    paths: {
      archive_root: String(paths.archive_root || "").trim(),
      export_root: String(paths.export_root || "").trim(),
    },
  };
  if (storedPreference.business_id && storedPreference.exchange) {
    request.stored_preference = {
      business_id: storedPreference.business_id,
      exchange: storedPreference.exchange,
    };
    if (storedPreference.record_family) {
      request.stored_preference.record_family = storedPreference.record_family;
    }
  } else if (hasExplicitStoredPreference) {
    request.stored_preference = {};
  }
  return Object.fromEntries(Object.entries(request).filter(([, value]) => value !== undefined));
}

export function buildAdvancedSettingsRequest(payload = {}) {
  const source = asObject(payload);
  const processing = asObject(source.processing);
  const ingestPaths = asObject(source.ingest_paths);
  return {
    processing: {
      save_json: asRequestBoolean(processing.save_json, "save_json"),
      postprocess_config: String(processing.postprocess_config || "").trim(),
    },
    ingest_paths: {
      raw_manual_root: String(ingestPaths.raw_manual_root || "").trim(),
      raw_auto_root: String(ingestPaths.raw_auto_root || "").trim(),
    },
  };
}
