import {
  buildAdvancedSettingsRequest,
  buildBasicSettingsRequest,
} from "../contracts/settings.js";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asScope(value) {
  const source = asObject(value);
  return {
    record_family: String(source.record_family || "").trim(),
    business_id: String(source.business_id || "").trim(),
    business_label: String(source.business_label || "").trim(),
    exchange: String(source.exchange || "").trim(),
  };
}

function asStaleMetadata(value) {
  const source = asObject(value);
  return {
    is_stale: Boolean(source.is_stale),
    reason: String(source.reason || "").trim(),
    hint: String(source.hint || "").trim(),
  };
}

function asIssues(value) {
  return Array.isArray(value)
    ? value
      .filter((item) => item && typeof item === "object")
      .map((item) => ({
        code: String(item.code || "").trim(),
        severity: String(item.severity || "").trim(),
        message: String(item.message || "").trim(),
      }))
    : [];
}

export function buildSettingsViewModel(settings = {}) {
  const source = asObject(settings);
  const basic = asObject(source.basic);
  const advanced = asObject(source.advanced);
  const runtime = asObject(source.runtime);
  const basicPaths = asObject(basic.paths);
  const processing = asObject(advanced.processing);
  const ingestPaths = asObject(advanced.ingest_paths);
  const runtimePaths = asObject(advanced.runtime_paths);
  const browser = asObject(runtime.browser);
  const install = asObject(runtime.install);
  const readiness = asObject(runtime.readiness);

  return {
    defaults: {
      default_exchange: String(basic.default_exchange || "").trim(),
      default_concurrency: Number.parseInt(basic.default_concurrency, 10) || 0,
      retention_count: Number.parseInt(basic.retention_count, 10) || 20,
    },
    defaultScope: {
      effective_default_scope: asScope(basic.effective_default_scope),
      stored_preference: asScope(basic.stored_preference),
      stale_default_metadata: asStaleMetadata(basic.stale_default_metadata),
    },
    basicPaths: {
      workspace_root: String(basicPaths.workspace_root || "").trim(),
      archive_root: String(basicPaths.archive_root || "").trim(),
      export_root: String(basicPaths.export_root || "").trim(),
    },
    processing: {
      save_json: Boolean(processing.save_json),
      postprocess_config: String(processing.postprocess_config || "").trim(),
    },
    ingestPaths: {
      raw_manual_root: String(ingestPaths.raw_manual_root || "").trim(),
      raw_auto_root: String(ingestPaths.raw_auto_root || "").trim(),
    },
    runtimePaths: {
      app_home: String(runtimePaths.app_home || "").trim(),
      streaming_db: String(runtimePaths.streaming_db || "").trim(),
      log_dir: String(runtimePaths.log_dir || "").trim(),
      cache_dir: String(runtimePaths.cache_dir || "").trim(),
      browser_cache_dir: String(runtimePaths.browser_cache_dir || "").trim(),
      archive_root: String(runtimePaths.archive_root || "").trim(),
      export_root: String(runtimePaths.export_root || "").trim(),
    },
    browser: {
      installed: Boolean(browser.installed),
      browser_name: String(browser.browser_name || "").trim(),
      installation_source: String(browser.installation_source || "").trim(),
      error: String(browser.error || "").trim(),
    },
    install: {
      status: String(install.status || "").trim(),
      browser_name: String(install.browser_name || "").trim(),
      trigger: String(install.trigger || "").trim(),
      attempt_count: Number.parseInt(install.attempt_count, 10) || 0,
      started_at: String(install.started_at || "").trim(),
      updated_at: String(install.updated_at || "").trim(),
      completed_at: String(install.completed_at || "").trim(),
      message: String(install.message || "").trim(),
      running: Boolean(install.running),
    },
    readiness: {
      ready: Boolean(readiness.ready),
      download_ready: Boolean(readiness.download_ready),
      browser_runtime_ready: Boolean(readiness.browser_runtime_ready),
      issues: asIssues(readiness.issues),
    },
  };
}

export function buildBasicSettingsSavePayload(formState = {}) {
  const source = asObject(formState);
  const hasRetentionCount = Object.prototype.hasOwnProperty.call(source, "retention_count");
  const hasExplicitStoredPreference = Object.prototype.hasOwnProperty.call(source, "stored_preference");
  const nestedStoredPreference = asObject(source.stored_preference);
  const storedPreferenceSource = hasExplicitStoredPreference
    ? source.stored_preference
    : nestedStoredPreference.business_id || nestedStoredPreference.exchange
    ? source.stored_preference
    : {
        record_family: source.record_family,
        business_id: source.business_id,
        exchange: source.exchange,
      };
  const storedPreference = asScope(storedPreferenceSource);
  const payload = {
    default_exchange: String(source.default_exchange || "").trim(),
    default_concurrency: Number.parseInt(source.default_concurrency, 10) || 0,
    paths: {
      archive_root: String(asObject(source.paths).archive_root ?? source.archive_root ?? "").trim(),
      export_root: String(asObject(source.paths).export_root ?? source.export_root ?? "").trim(),
    },
  };
  if (hasRetentionCount) {
    payload.retention_count = source.retention_count;
  }
  if (
    hasExplicitStoredPreference
    || storedPreference.business_id
  ) {
    payload.stored_preference = storedPreference;
  }
  return buildBasicSettingsRequest(payload);
}

export function buildAdvancedSettingsSavePayload(formState = {}) {
  const source = asObject(formState);
  return buildAdvancedSettingsRequest({
    processing: {
      save_json: Boolean(source.save_json),
      postprocess_config: String(source.postprocess_config || "").trim(),
    },
    ingest_paths: {
      raw_manual_root: String(source.raw_manual_root || "").trim(),
      raw_auto_root: String(source.raw_auto_root || "").trim(),
    },
  });
}
