import test from "node:test";
import assert from "node:assert/strict";

import {
  buildAdvancedSettingsRequest,
} from "../src/contracts/settings.js";
import {
  buildAdvancedSettingsSavePayload,
  buildBasicSettingsSavePayload,
  buildSettingsViewModel,
} from "../src/panels/settingsState.js";

test("buildSettingsViewModel consumes only canonical nested settings and runtime fields", () => {
  const view = buildSettingsViewModel({
    basic: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "股权转让",
        exchange: "cbex",
      },
      stored_preference: {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "cbex",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
      default_exchange: "cbex",
      default_concurrency: 3,
      retention_count: 8,
      paths: {
        workspace_root: "/tmp/workspace",
        archive_root: "/tmp/archive",
        export_root: "/tmp/export",
      },
    },
    advanced: {
      processing: {
        save_json: true,
        postprocess_config: "/tmp/postprocess.json",
      },
      ingest_paths: {
        raw_manual_root: "/tmp/manual",
        raw_auto_root: "/tmp/auto",
      },
      runtime_paths: {
        app_home: "/tmp/home",
        streaming_db: "/tmp/data.sqlite3",
        log_dir: "/tmp/logs",
        cache_dir: "/tmp/cache",
        browser_cache_dir: "/tmp/browser-cache",
        archive_root: "/tmp/archive",
        export_root: "/tmp/export",
      },
    },
    runtime: {
      browser: {
        installed: true,
        browser_name: "chromium",
        installation_source: "system",
        error: "",
      },
      install: {
        status: "running",
        browser_name: "chromium",
        trigger: "manual",
        attempt_count: 2,
        started_at: "2026-04-13T10:00:00",
        updated_at: "2026-04-13T10:00:01",
        completed_at: "",
        message: "Installing",
        running: true,
      },
      readiness: {
        ready: true,
        download_ready: true,
        browser_runtime_ready: true,
        issues: [{ code: "runtime_ready", severity: "info", message: "ready" }],
      },
    },
    default_exchange: "legacy-flat",
    raw_manual_root: "/tmp/legacy-manual",
    browser_install: { status: "legacy" },
    product_readiness: { ready: false },
  });

  assert.deepEqual(view, {
    defaults: {
      default_exchange: "cbex",
      default_concurrency: 3,
      retention_count: 8,
    },
    defaultScope: {
      effective_default_scope: {
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "股权转让",
        exchange: "cbex",
      },
      stored_preference: {
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "",
        exchange: "cbex",
      },
      stale_default_metadata: {
        is_stale: false,
        reason: "",
        hint: "",
      },
    },
    basicPaths: {
      workspace_root: "/tmp/workspace",
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
    },
    processing: {
      save_json: true,
      postprocess_config: "/tmp/postprocess.json",
    },
    ingestPaths: {
      raw_manual_root: "/tmp/manual",
      raw_auto_root: "/tmp/auto",
    },
    runtimePaths: {
      app_home: "/tmp/home",
      streaming_db: "/tmp/data.sqlite3",
      log_dir: "/tmp/logs",
      cache_dir: "/tmp/cache",
      browser_cache_dir: "/tmp/browser-cache",
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
    },
    browser: {
      installed: true,
      browser_name: "chromium",
      installation_source: "system",
      error: "",
    },
    install: {
      status: "running",
      browser_name: "chromium",
      trigger: "manual",
      attempt_count: 2,
      started_at: "2026-04-13T10:00:00",
      updated_at: "2026-04-13T10:00:01",
      completed_at: "",
      message: "Installing",
      running: true,
    },
    readiness: {
      ready: true,
      download_ready: true,
      browser_runtime_ready: true,
      issues: [{ code: "runtime_ready", severity: "info", message: "ready" }],
    },
  });
});

test("settings save payload builders emit canonical nested requests", () => {
  assert.deepEqual(
    buildBasicSettingsSavePayload({
      record_family: "listing",
      business_id: "physical_asset",
      exchange: "shanghai",
      default_exchange: "shanghai",
      default_concurrency: 6,
      retention_count: 12,
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
    }),
    {
      stored_preference: {
        record_family: "listing",
        business_id: "physical_asset",
        exchange: "shanghai",
      },
      default_exchange: "shanghai",
      default_concurrency: 6,
      retention_count: 12,
      paths: {
        archive_root: "/tmp/archive",
        export_root: "/tmp/export",
      },
    },
  );

  assert.deepEqual(
    buildAdvancedSettingsSavePayload({
      postprocess_config: "/tmp/postprocess.json",
      raw_manual_root: "/tmp/manual",
      raw_auto_root: "/tmp/auto",
      save_json: true,
    }),
    {
      processing: {
        save_json: true,
        postprocess_config: "/tmp/postprocess.json",
      },
      ingest_paths: {
        raw_manual_root: "/tmp/manual",
        raw_auto_root: "/tmp/auto",
      },
    },
  );
});

test("advanced settings request rejects malformed save_json booleans", () => {
  assert.throws(
    () => buildAdvancedSettingsRequest({
      processing: {
        save_json: "false",
        postprocess_config: "/tmp/postprocess.json",
      },
      ingest_paths: {
        raw_manual_root: "/tmp/manual",
        raw_auto_root: "/tmp/auto",
      },
    }),
    /save_json.*boolean/i,
  );
});

test("settings save payload builder ignores legacy default_project_type alias", () => {
  assert.deepEqual(
    buildBasicSettingsSavePayload({
      record_family: "listing",
      business_id: "",
      default_project_type: "physical_asset",
      exchange: "cbex",
      default_exchange: "cbex",
    }),
    {
      default_exchange: "cbex",
      default_concurrency: 0,
      paths: {
        archive_root: "",
        export_root: "",
      },
    },
  );
});

test("settings save payload builder keeps actionable stored_preference even when family is implicit in the editor state", () => {
  assert.deepEqual(
    buildBasicSettingsSavePayload({
      business_id: "equity_transfer",
      exchange: "sse",
      default_exchange: "sse",
      default_concurrency: 4,
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
    }),
    {
      stored_preference: {
        business_id: "equity_transfer",
        exchange: "sse",
      },
      default_exchange: "sse",
      default_concurrency: 4,
      paths: {
        archive_root: "/tmp/archive",
        export_root: "/tmp/export",
      },
    },
  );
});

test("buildBasicSettingsSavePayload does not synthesize stored preference from scalar defaults", () => {
  assert.deepEqual(
    buildBasicSettingsSavePayload({
      record_family: "",
      business_id: "",
      exchange: "",
      default_exchange: "cbex",
      default_concurrency: 4,
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
    }),
    {
      default_exchange: "cbex",
      default_concurrency: 4,
      paths: {
        archive_root: "/tmp/archive",
        export_root: "/tmp/export",
      },
    },
  );
});

test("buildBasicSettingsSavePayload validates retention count as a positive integer", () => {
  assert.deepEqual(
    buildBasicSettingsSavePayload({
      default_exchange: "cbex",
      default_concurrency: 4,
      retention_count: 5,
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
    }),
    {
      default_exchange: "cbex",
      default_concurrency: 4,
      retention_count: 5,
      paths: {
        archive_root: "/tmp/archive",
        export_root: "/tmp/export",
      },
    },
  );
  assert.throws(
    () => buildBasicSettingsSavePayload({ retention_count: 0 }),
    /retention_count/,
  );
});

test("buildBasicSettingsSavePayload keeps stored_preference separate from scalar default_exchange", () => {
  assert.deepEqual(
    buildBasicSettingsSavePayload({
      stored_preference: {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "sse",
      },
      default_exchange: "cbex",
      default_concurrency: 4,
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
    }),
    {
      stored_preference: {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "sse",
      },
      default_exchange: "cbex",
      default_concurrency: 4,
      paths: {
        archive_root: "/tmp/archive",
        export_root: "/tmp/export",
      },
    },
  );
});

test("buildBasicSettingsSavePayload preserves explicit empty stored_preference so the shared scope can be cleared", () => {
  assert.deepEqual(
    buildBasicSettingsSavePayload({
      stored_preference: {
        record_family: "",
        business_id: "",
        exchange: "",
      },
      default_exchange: "cbex",
      default_concurrency: 4,
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
    }),
    {
      stored_preference: {},
      default_exchange: "cbex",
      default_concurrency: 4,
      paths: {
        archive_root: "/tmp/archive",
        export_root: "/tmp/export",
      },
    },
  );
});
