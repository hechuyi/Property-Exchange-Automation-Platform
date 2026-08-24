import test from "node:test";
import assert from "node:assert/strict";

import { API } from "../api.js";

function createJsonResponse({ ok, status, payload, contentType = "application/json; charset=utf-8" }) {
  return {
    ok,
    status,
    headers: {
      get(name) {
        return String(name || "").toLowerCase() === "content-type" ? contentType : null;
      },
    },
    async json() {
      return payload;
    },
  };
}

function withWindowMock(t, value) {
  const previousWindow = globalThis.window;
  t.after(() => {
    if (previousWindow === undefined) {
      delete globalThis.window;
      return;
    }
    globalThis.window = previousWindow;
  });
  globalThis.window = value;
}

function withFetchMock(t, callback) {
  const previousFetch = globalThis.fetch;
  t.after(() => {
    if (previousFetch === undefined) {
      delete globalThis.fetch;
      return;
    }
    globalThis.fetch = previousFetch;
  });
  globalThis.fetch = callback;
}

test("API.request injects desktop token header from renderer backend config", { concurrency: false }, async (t) => {
  withWindowMock(t, {
    peapDesktop: {
      getBackendConfig() {
        return {
          apiToken: "desktop-secret",
        };
      },
    },
  });

  let capturedOptions = null;
  withFetchMock(t, async (_url, options) => {
    capturedOptions = options;
    return createJsonResponse({
      ok: true,
      status: 200,
      payload: {
        ok: true,
        data: { ready: true },
      },
    });
  });

  await API.request("/api/health");

  assert.equal(capturedOptions.headers["X-PEAP-Desktop-Token"], "desktop-secret");
});

test("API.getCatalog reads /api/catalog as the only static truth source", { concurrency: false }, async (t) => {
  withWindowMock(t, {});

  let capturedUrl = "";
  withFetchMock(t, async (url) => {
    capturedUrl = String(url || "");
    return createJsonResponse({
      ok: true,
      status: 200,
      payload: {
        ok: true,
        data: {
          active_profile: { profile_id: "desktop_listing" },
          visible_families: [
            {
              family_id: "listing",
              family_label: "Listing",
              businesses: [
                {
                  business_id: "equity_transfer",
                  business_label: "Equity Transfer",
                  supported_surfaces: ["records", "one_click", "export"],
                },
              ],
            },
          ],
          support_matrix: {
            listing: {
              equity_transfer: { records: true, one_click: true, export: true },
            },
          },
          default_scope: {
            record_family: "listing",
            business_id: "equity_transfer",
            business_label: "Equity Transfer",
            exchange: "cbex",
          },
          visibility: {
            mode: "listing_only",
            visible_families: ["listing"],
          },
        },
      },
    });
  });

  const data = await API.getCatalog();

  assert.equal(capturedUrl, "/api/catalog");
  assert.equal(data.default_scope.business_id, "equity_transfer");
  assert.equal(data.visibility.mode, "listing_only");
});

test("API.runManualImport posts canonical explicit scope when the caller provides it", { concurrency: false }, async (t) => {
  withWindowMock(t, {});

  let capturedUrl = "";
  let capturedMethod = "";
  let capturedBody = null;
  withFetchMock(t, async (url, options = {}) => {
    capturedUrl = String(url || "");
    capturedMethod = String(options.method || "");
    capturedBody = JSON.parse(String(options.body || "{}"));
    return createJsonResponse({
      ok: true,
      status: 202,
      payload: {
        ok: true,
        data: {
          job_id: "job-manual",
          job_type: "manual_import",
          record_family: "listing",
          business_id: "equity_transfer",
          scope: {
            record_family: "listing",
            business_id: "equity_transfer",
            business_label: "Equity Transfer",
            exchange: "sse",
          },
        },
      },
    });
  });

  await API.runManualImport({
    input_dir: "/tmp/manual-html",
    record_family: "listing",
    business_id: "equity_transfer",
    business_label: "Equity Transfer",
    exchange: "sse",
  });

  assert.equal(capturedUrl, "/api/jobs/manual-import");
  assert.equal(capturedMethod, "POST");
  assert.deepEqual(capturedBody, {
    input_dir: "/tmp/manual-html",
    record_family: "listing",
    business_id: "equity_transfer",
    business_label: "Equity Transfer",
    exchange: "sse",
  });
});

test("API.acknowledgeRecordFieldMissing posts to canonical acknowledgement action path", { concurrency: false }, async (t) => {
  withWindowMock(t, {});

  let capturedUrl = "";
  let capturedMethod = "";
  let capturedBody = null;
  withFetchMock(t, async (url, options = {}) => {
    capturedUrl = String(url || "");
    capturedMethod = String(options.method || "");
    capturedBody = JSON.parse(String(options.body || "{}"));
    return createJsonResponse({
      ok: true,
      status: 200,
      payload: {
        ok: true,
        data: {
          record_id: "rec-field-missing",
          state: "field_missing",
          exportable: false,
          field_missing_acknowledgement: {
            acknowledged: true,
            missing_fields_hash: "hash-1",
            revision_id: 7,
          },
          attention: {
            requires_attention: false,
            suppressed: true,
            reason: "acknowledged",
          },
        },
      },
    });
  });

  const result = await API.acknowledgeRecordFieldMissing("rec-field-missing");

  assert.equal(capturedUrl, "/api/records/rec-field-missing/field-missing/acknowledge");
  assert.equal(capturedMethod, "POST");
  assert.deepEqual(capturedBody, {});
  assert.equal(result.state, "field_missing");
  assert.equal(result.exportable, false);
  assert.equal(result.field_missing_acknowledgement.acknowledged, true);
  assert.equal(result.attention.requires_attention, false);
});

test("API.retryJob posts an encoded job identifier to the retry action", { concurrency: false }, async (t) => {
  withWindowMock(t, {});

  let capturedUrl = "";
  let capturedMethod = "";
  let capturedBody = null;
  withFetchMock(t, async (url, options = {}) => {
    capturedUrl = String(url || "");
    capturedMethod = String(options.method || "");
    capturedBody = JSON.parse(String(options.body || "{}"));
    return createJsonResponse({
      ok: true,
      status: 202,
      payload: {
        ok: true,
        data: { job_id: "retry-job", job_type: "one_click", retry_of_job_id: "job/unsafe id" },
      },
    });
  });

  const result = await API.retryJob("job/unsafe id");

  assert.equal(capturedUrl, "/api/jobs/job%2Funsafe%20id/retry");
  assert.equal(capturedMethod, "POST");
  assert.deepEqual(capturedBody, {});
  assert.equal(result.job_id, "retry-job");
  assert.equal(result.job_type, "one_click");
});

test("API.reprocessRecord posts an encoded record identifier and preserves the action result", { concurrency: false }, async (t) => {
  withWindowMock(t, {});

  let capturedUrl = "";
  let capturedMethod = "";
  let capturedBody = null;
  withFetchMock(t, async (url, options = {}) => {
    capturedUrl = String(url || "");
    capturedMethod = String(options.method || "");
    capturedBody = JSON.parse(String(options.body || "{}"));
    return createJsonResponse({
      ok: true,
      status: 200,
      payload: {
        ok: true,
        data: {
          record_id: "rec/unsafe id",
          state: "ready",
          project_code: "P-1",
          archive_path: "/tmp/p.html",
          error_code: "",
          error_message: "",
        },
      },
    });
  });

  const result = await API.reprocessRecord("rec/unsafe id");

  assert.equal(capturedUrl, "/api/records/rec%2Funsafe%20id/reprocess");
  assert.equal(capturedMethod, "POST");
  assert.deepEqual(capturedBody, {});
  assert.deepEqual(result, {
    record_id: "rec/unsafe id",
    state: "ready",
    project_code: "P-1",
    archive_path: "/tmp/p.html",
    error_code: "",
    error_message: "",
  });
});

test("API.runHistorical posts multi-family scope plans to the dedicated download-ingest endpoint", { concurrency: false }, async (t) => {
  withWindowMock(t, {});

  let capturedUrl = "";
  let capturedMethod = "";
  let capturedBody = null;
  withFetchMock(t, async (url, options = {}) => {
    capturedUrl = String(url || "");
    capturedMethod = String(options.method || "");
    capturedBody = JSON.parse(String(options.body || "{}"));
    return createJsonResponse({
      ok: true,
      status: 202,
      payload: {
        ok: true,
        data: {
          job_id: "job-history",
          job_type: "download_ingest",
        },
      },
    });
  });

  const result = await API.runHistorical({
    start_date: "2026-03-01",
    end_date: "2026-03-31",
    family_scopes: [
      {
        record_family: "listing",
        business_id: "all",
        business_label: "",
        exchange: "all",
      },
      {
        record_family: "deal",
        business_id: "all",
        business_label: "",
        exchange: "all",
      },
    ],
  });

  assert.equal(capturedUrl, "/api/jobs/download-ingest");
  assert.equal(capturedMethod, "POST");
  assert.deepEqual(capturedBody, {
    start_date: "2026-03-01",
    end_date: "2026-03-31",
    family_scopes: [
      {
        record_family: "listing",
        business_id: "all",
        exchange: "all",
      },
      {
        record_family: "deal",
        business_id: "all",
        exchange: "all",
      },
    ],
  });
  assert.equal(result.job_type, "download_ingest");
});

test("API.runOneClick preserves multi-family scope plans", { concurrency: false }, async (t) => {
  withWindowMock(t, {});

  let capturedUrl = "";
  let capturedMethod = "";
  let capturedBody = null;
  withFetchMock(t, async (url, options = {}) => {
    capturedUrl = String(url || "");
    capturedMethod = String(options.method || "");
    capturedBody = JSON.parse(String(options.body || "{}"));
    return createJsonResponse({
      ok: true,
      status: 202,
      payload: {
        ok: true,
        data: {
          job_id: "job-one-click",
          job_type: "one_click",
        },
      },
    });
  });

  const result = await API.runOneClick({
    start_date: "2026-04-01",
    end_date: "2026-05-10",
    max_pages: "1",
    family_scopes: [
      {
        record_family: "listing",
        business_id: "all",
        business_label: "",
        exchange: "all",
      },
      {
        record_family: "deal",
        business_id: "all",
        business_label: "",
        exchange: "all",
      },
    ],
  });

  assert.equal(capturedUrl, "/api/jobs/one-click");
  assert.equal(capturedMethod, "POST");
  assert.deepEqual(capturedBody, {
    start_date: "2026-04-01",
    end_date: "2026-05-10",
    max_pages: 1,
    family_scopes: [
      {
        record_family: "listing",
        business_id: "all",
        exchange: "all",
      },
      {
        record_family: "deal",
        business_id: "all",
        exchange: "all",
      },
    ],
  });
  assert.equal(result.job_type, "one_click");
});

test("API.runArchiveReprocess posts to the dedicated archive reprocess endpoint", { concurrency: false }, async (t) => {
  withWindowMock(t, {});

  let capturedUrl = "";
  let capturedMethod = "";
  let capturedBody = null;
  withFetchMock(t, async (url, options = {}) => {
    capturedUrl = String(url || "");
    capturedMethod = String(options.method || "");
    capturedBody = JSON.parse(String(options.body || "{}"));
    return createJsonResponse({
      ok: true,
      status: 202,
      payload: {
        ok: true,
        data: {
          job_id: "job-archive-reprocess",
          job_type: "archive_reprocess",
          input_dir: "/tmp/archive",
          discovered_count: 12,
        },
      },
    });
  });

  const result = await API.runArchiveReprocess();

  assert.equal(capturedUrl, "/api/jobs/archive-reprocess");
  assert.equal(capturedMethod, "POST");
  assert.deepEqual(capturedBody, {});
  assert.equal(result.job_type, "archive_reprocess");
  assert.equal(result.input_dir, "/tmp/archive");
});

test("API.listExportHistory reads the export history collection endpoint", { concurrency: false }, async (t) => {
  withWindowMock(t, {});

  let capturedUrl = "";
  withFetchMock(t, async (url) => {
    capturedUrl = String(url || "");
    return createJsonResponse({
      ok: true,
      status: 200,
      payload: {
        ok: true,
        data: {
          rows: [
            {
              export_id: "exp-1",
              cursor_id: "cursor-a",
              requested_export_mode: "full",
              revision_watermark: 42,
              artifact_count: 1,
              openable: true,
              rebuildable: true,
            },
          ],
        },
      },
    });
  });

  const result = await API.listExportHistory(25);

  assert.equal(capturedUrl, "/api/exports/history?limit=25");
  assert.equal(result.rows[0].export_id, "exp-1");
  assert.equal(result.rows[0].cursor_id, "cursor-a");
  assert.equal(result.rows[0].openable, true);
});

test("API.getExportHistoryDetail reads manifest detail by export id", { concurrency: false }, async (t) => {
  withWindowMock(t, {});

  let capturedUrl = "";
  withFetchMock(t, async (url) => {
    capturedUrl = String(url || "");
    return createJsonResponse({
      ok: true,
      status: 200,
      payload: {
        ok: true,
        data: {
          export_id: "exp/unsafe id",
          cursor_id: "cursor-a",
          manifest: {
            effective_export_mode: "incremental",
            export_profile_id: "listing/equity_transfer",
            canonical_scope_hash: "hash-1",
            included_count: 2,
          },
          cursor_value: {
            last_successful_revision_watermark: 41,
          },
          artifacts: ["/tmp/export/a.xlsx"],
          existing_artifacts: ["/tmp/export/a.xlsx"],
          openable: true,
          rebuildable: true,
        },
      },
    });
  });

  const result = await API.getExportHistoryDetail("exp/unsafe id");

  assert.equal(capturedUrl, "/api/exports/history/exp%2Funsafe%20id");
  assert.equal(result.export_id, "exp/unsafe id");
  assert.equal(result.manifest.effective_export_mode, "incremental");
  assert.equal(result.cursor_value.last_successful_revision_watermark, 41);
});

test("API.openExportHistory posts to the export history open action", { concurrency: false }, async (t) => {
  withWindowMock(t, {});

  let capturedUrl = "";
  let capturedMethod = "";
  let capturedBody = null;
  withFetchMock(t, async (url, options = {}) => {
    capturedUrl = String(url || "");
    capturedMethod = String(options.method || "");
    capturedBody = JSON.parse(String(options.body || "{}"));
    return createJsonResponse({
      ok: true,
      status: 200,
      payload: {
        ok: true,
        data: {
          export_id: "exp-1",
          opened: true,
          path: "/tmp/export/a.xlsx",
          openable: true,
          rebuildable: true,
        },
      },
    });
  });

  const result = await API.openExportHistory("exp-1");

  assert.equal(capturedUrl, "/api/exports/history/exp-1/open");
  assert.equal(capturedMethod, "POST");
  assert.deepEqual(capturedBody, {});
  assert.equal(result.opened, true);
  assert.equal(result.path, "/tmp/export/a.xlsx");
});

test("API.downloadExportHistory posts an optional output directory", { concurrency: false }, async (t) => {
  withWindowMock(t, {});

  let capturedUrl = "";
  let capturedMethod = "";
  let capturedBody = null;
  withFetchMock(t, async (url, options = {}) => {
    capturedUrl = String(url || "");
    capturedMethod = String(options.method || "");
    capturedBody = JSON.parse(String(options.body || "{}"));
    return createJsonResponse({
      ok: true,
      status: 200,
      payload: {
        ok: true,
        data: {
          export_id: "exp-1",
          downloaded: true,
          artifacts: ["/tmp/download/a.xlsx"],
          openable: true,
          rebuildable: true,
        },
      },
    });
  });

  const result = await API.downloadExportHistory("exp-1", "/tmp/download");

  assert.equal(capturedUrl, "/api/exports/history/exp-1/download");
  assert.equal(capturedMethod, "POST");
  assert.deepEqual(capturedBody, { output_dir: "/tmp/download" });
  assert.equal(result.downloaded, true);
  assert.deepEqual(result.artifacts, ["/tmp/download/a.xlsx"]);
});
