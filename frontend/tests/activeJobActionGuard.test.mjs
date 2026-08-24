import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { API } from "../api.js";
import { buildActiveJobBlockMessage } from "../src/state/index.js";
import { runActiveJobGuardedAction } from "../src/state/activeJobActionGuard.js";

function sliceBetween(source, startNeedle, endNeedle) {
  const start = source.indexOf(startNeedle);
  assert.notEqual(start, -1, `missing start snippet: ${startNeedle}`);
  const end = source.indexOf(endNeedle, start + startNeedle.length);
  assert.notEqual(end, -1, `missing end snippet: ${endNeedle}`);
  return source.slice(start, end);
}

function assertBlocksBeforePost(handlerSource, { actionLabel, postSnippet }) {
  const preflightIndex = handlerSource.indexOf("await refreshOverviewOnlyForActionGuard();");
  const messageIndex = handlerSource.indexOf(`buildActiveJobBlockMessage(overview, "${actionLabel}")`);
  const throwIndex = handlerSource.indexOf("throw Object.assign(new Error(activeJobBlockMessage), { localOnly: true });");
  const postIndex = handlerSource.indexOf(postSnippet);
  assert.ok(preflightIndex !== -1, `${actionLabel} handler should refresh overview-only before POST`);
  assert.ok(messageIndex !== -1, `${actionLabel} handler should build a local active-job block message`);
  assert.ok(throwIndex !== -1, `${actionLabel} handler should throw localOnly before POST when active`);
  assert.ok(postIndex !== -1, `${actionLabel} handler should keep the normal POST path`);
  assert.ok(preflightIndex < messageIndex, `${actionLabel} preflight should run before active-job check`);
  assert.ok(messageIndex < throwIndex, `${actionLabel} message should be checked before local throw`);
  assert.ok(throwIndex < postIndex, `${actionLabel} local block must happen before mutating POST`);
  assert.match(handlerSource, /e\?\.localOnly\s*\?\s*e\.message\s*:/, `${actionLabel} local block should display without POST error prefix`);
}

function createFakeElement(id = "") {
  const listeners = {};
  let html = "";
  return {
    id,
    dataset: {},
    disabled: false,
    checked: false,
    value: "",
    style: {},
    listeners,
    classList: {
      toggle() {},
      add() {},
      remove() {},
    },
    set innerHTML(value) {
      html = String(value || "");
    },
    get innerHTML() {
      return html;
    },
    addEventListener(eventName, listener) {
      listeners[eventName] = listener;
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    appendChild() {},
    remove() {},
  };
}

function createFakeDocument() {
  const readyListeners = [];
  const elements = new Map();
  const panelOverview = createFakeElement("panel-overview");
  const panelMappings = createFakeElement("panel-mappings");
  const buttons = [
    "btn-oneclick",
    "btn-historical",
    "btn-import",
    "btn-archive-reprocess",
    "btn-export",
  ];
  elements.set("panel-overview", panelOverview);
  elements.set("panel-mappings", panelMappings);
  buttons.forEach((id) => elements.set(id, createFakeElement(id)));
  return {
    elements,
    panelOverview,
    panelMappings,
    addEventListener(eventName, listener) {
      if (eventName === "DOMContentLoaded") readyListeners.push(listener);
    },
    dispatchReady() {
      readyListeners.forEach((listener) => listener({ type: "DOMContentLoaded" }));
    },
    querySelector(selector) {
      if (selector.startsWith("#")) {
        const id = selector.slice(1);
        if (!elements.has(id)) elements.set(id, createFakeElement(id));
        return elements.get(id);
      }
      return null;
    },
    querySelectorAll(selector) {
      if (selector === ".panel") return [panelOverview, panelMappings];
      if (selector === ".sidebar-nav-link") return [];
      return [];
    },
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, createFakeElement(id));
      return elements.get(id);
    },
    createElement(tagName) {
      return createFakeElement(tagName);
    },
    body: {
      appendChild() {},
    },
  };
}

async function waitFor(condition, description) {
  for (let attempt = 0; attempt < 25; attempt += 1) {
    if (condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  assert.fail(description);
}

function restoreGlobals(previousGlobals) {
  globalThis.document = previousGlobals.document;
  globalThis.window = previousGlobals.window;
  globalThis.EventSource = previousGlobals.EventSource;
}

function restoreApi(previousApi) {
  Object.assign(API, previousApi);
}

function buildExportReadyCatalog() {
  return {
    visible_families: [
      {
        family_id: "listing",
        family_label: "挂牌项目",
        businesses: [
          {
            business_id: "equity_transfer",
            business_label: "股权转让",
            supported_surfaces: ["records", "one_click", "export"],
          },
        ],
      },
    ],
    sources: [
      {
        source_id: "cbex",
        source_label: "北交所",
        record_families: ["listing"],
      },
    ],
    support_matrix: {
      listing: {
        equity_transfer: { records: true, one_click: true, export: true },
      },
    },
    surface_source_matrix: {
      listing: {
        equity_transfer: {
          records: ["cbex"],
          one_click: ["cbex"],
          export: ["cbex"],
        },
      },
    },
    default_scope: {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
    },
  };
}

async function runOverviewExportWithBusinessResult(exportResult) {
  const document = createFakeDocument();
  const previousGlobals = {
    document: globalThis.document,
    window: globalThis.window,
    EventSource: globalThis.EventSource,
  };
  class FakeEventSource {
    static CLOSED = 2;
    constructor(url) {
      this.url = url;
      this.readyState = 1;
    }
    close() {
      this.readyState = FakeEventSource.CLOSED;
    }
  }

  globalThis.document = document;
  globalThis.window = {
    location: { pathname: "/" },
    confirm: () => true,
  };
  globalThis.EventSource = FakeEventSource;

  const previousApi = { ...API };
  const catalog = buildExportReadyCatalog();
  const scope = catalog.default_scope;
  const calls = [];
  Object.assign(API, {
    base: "",
    apiToken: "",
    async getCatalog() {
      calls.push("getCatalog");
      return catalog;
    },
    async getSettingsBasic() {
      calls.push("getSettingsBasic");
      return {
        effective_default_scope: scope,
        stored_preference: scope,
        stale_default_metadata: {},
      };
    },
    async getOverview() {
      calls.push("getOverview");
      return {
        record_summary: { state_counts: { ready: 1 } },
        latest_job: null,
        latest_progress: {},
        recent_jobs: [],
        runtime: {},
      };
    },
    async runExport(receivedScope, requestedExportMode) {
      calls.push(["runExport", receivedScope, requestedExportMode]);
      return exportResult;
    },
  });

  try {
    await import(new URL(`../app.js?export-business-status=${Date.now()}-${Math.random()}`, import.meta.url).href);
    document.dispatchReady();
    await waitFor(
      () => typeof document.elements.get("btn-export").listeners.click === "function",
      "overview export button listener was not registered",
    );
    await document.elements.get("btn-export").listeners.click();
    return {
      calls,
      html: document.panelOverview.innerHTML,
    };
  } finally {
    restoreApi(previousApi);
    restoreGlobals(previousGlobals);
  }
}

async function renderMappingsAfterSuccessfulThenFailedLoad() {
  const document = createFakeDocument();
  const previousGlobals = {
    document: globalThis.document,
    window: globalThis.window,
    EventSource: globalThis.EventSource,
  };
  class FakeEventSource {
    static CLOSED = 2;
    constructor(url) {
      this.url = url;
      this.readyState = 1;
    }
    close() {
      this.readyState = FakeEventSource.CLOSED;
    }
  }

  globalThis.document = document;
  globalThis.window = {
    location: { pathname: "/" },
    confirm: () => true,
  };
  globalThis.EventSource = FakeEventSource;

  const previousApi = { ...API };
  const oldRuleText = "F2E_ONLY_OLD_RULE";
  let listMappingsCalls = 0;
  Object.assign(API, {
    base: "",
    apiToken: "",
    async getCatalog() {
      return buildExportReadyCatalog();
    },
    async getSettingsBasic() {
      return {
        effective_default_scope: buildExportReadyCatalog().default_scope,
        stored_preference: buildExportReadyCatalog().default_scope,
        stale_default_metadata: {},
      };
    },
    async getOverview() {
      return {
        record_summary: { state_counts: {} },
        latest_job: null,
        latest_progress: {},
        recent_jobs: [],
        runtime: {},
      };
    },
    async listMappings() {
      listMappingsCalls += 1;
      if (listMappingsCalls === 1) {
        return {
          summary: {
            actionable_count: 1,
            mapping_gap_count: 1,
            mapping_conflict_count: 0,
            audit_count: 0,
          },
          sections: [
            {
              section_id: "mapping_gap_resolution",
              title: "待映射补全",
              count: 1,
              items: [
                {
                  record_id: "rec-old",
                  project_code: "OLD_PENDING_ITEM_F2E",
                  state: "pending_mapping",
                },
              ],
            },
          ],
          entries: [
            {
              entry_id: "entry-old",
              rule_title: "转让方 -> 集团",
              source_name: oldRuleText,
              target_value: "旧集团",
              updated_at: "2026-01-01T00:00:00Z",
            },
          ],
        };
      }
      throw new Error("mapping backend unavailable");
    },
  });

  try {
    await import(new URL(`../app.js?mappings-load-failure=${Date.now()}-${Math.random()}`, import.meta.url).href);
    document.dispatchReady();
    await waitFor(
      () => typeof globalThis.window.switchPanel === "function",
      "switchPanel should be registered after app import",
    );
    await globalThis.window.switchPanel("mappings");
    await waitFor(
      () => document.panelMappings.innerHTML.includes(oldRuleText),
      "initial mappings load should render the saved rule",
    );
    await globalThis.window.switchPanel("mappings");
    await waitFor(
      () => listMappingsCalls === 2,
      "second mappings load should call listMappings",
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    return {
      html: document.panelMappings.innerHTML,
      oldRuleText,
    };
  } finally {
    restoreApi(previousApi);
    restoreGlobals(previousGlobals);
  }
}

async function deleteMappingWithBusinessResult(deleteResult) {
  const document = createFakeDocument();
  const previousGlobals = {
    document: globalThis.document,
    window: globalThis.window,
    EventSource: globalThis.EventSource,
  };
  class FakeEventSource {
    static CLOSED = 2;
    constructor(url) {
      this.url = url;
      this.readyState = 1;
    }
    close() {
      this.readyState = FakeEventSource.CLOSED;
    }
  }

  globalThis.document = document;
  globalThis.window = {
    location: { pathname: "/" },
    confirm: () => true,
  };
  globalThis.EventSource = FakeEventSource;

  const previousApi = { ...API };
  const catalog = buildExportReadyCatalog();
  const scope = catalog.default_scope;
  const deleteButtons = [];
  document.panelMappings.querySelectorAll = (selector) => {
    if (selector === ".btn-delete-mapping-entry") return deleteButtons;
    return [];
  };

  Object.assign(API, {
    base: "",
    apiToken: "",
    async getCatalog() {
      return catalog;
    },
    async getSettingsBasic() {
      return {
        effective_default_scope: scope,
        stored_preference: scope,
        stale_default_metadata: {},
      };
    },
    async getOverview() {
      return {
        record_summary: { state_counts: {} },
        latest_job: null,
        latest_progress: {},
        recent_jobs: [],
        runtime: {},
      };
    },
    async listMappings() {
      deleteButtons.length = 0;
      const deleteButton = createFakeElement("delete-entry-1");
      deleteButton.dataset.entryId = "entry-1";
      deleteButtons.push(deleteButton);
      return {
        summary: {
          actionable_count: 0,
          mapping_gap_count: 0,
          mapping_conflict_count: 0,
          audit_count: 0,
        },
        sections: [],
        entries: [
          {
            entry_id: "entry-1",
            rule_title: "转让方 -> 集团",
            source_name: "华润置地",
            target_value: "华润集团",
            updated_at: "2026-01-01T00:00:00Z",
          },
        ],
      };
    },
    async deleteMapping(entryId) {
      assert.equal(entryId, "entry-1");
      return deleteResult;
    },
  });

  try {
    await import(new URL(`../app.js?mapping-delete-business-result=${Date.now()}-${Math.random()}`, import.meta.url).href);
    document.dispatchReady();
    await waitFor(
      () => typeof globalThis.window.switchPanel === "function",
      "switchPanel should be registered after app import",
    );
    await globalThis.window.switchPanel("mappings");
    await waitFor(
      () => deleteButtons.length === 1 && typeof deleteButtons[0].listeners.click === "function",
      "mapping delete button listener should be registered",
    );
    await deleteButtons[0].listeners.click({ currentTarget: deleteButtons[0], target: deleteButtons[0] });
    return {
      html: document.panelMappings.innerHTML,
    };
  } finally {
    restoreApi(previousApi);
    restoreGlobals(previousGlobals);
  }
}

test("buildActiveJobBlockMessage names the active job and requested action", () => {
  assert.equal(
    buildActiveJobBlockMessage(
      {
        latest_job: {
          job_type: "one_click",
          status: "running",
        },
      },
      "导出",
    ),
    "已有执行中的任务：一键执行，请等待完成后再导出。",
  );

  assert.equal(
    buildActiveJobBlockMessage(
      {
        latest_job: {
          job_type: "archive_reprocess",
          status: "starting",
        },
      },
      "重新解析",
    ),
    "已有执行中的任务：重新解析+后处理，请等待完成后再重新解析。",
  );

  assert.equal(buildActiveJobBlockMessage({ latest_job: { job_type: "one_click", status: "success" } }, "导出"), "");
  assert.equal(buildActiveJobBlockMessage({}, "导出"), "");
});

test("runActiveJobGuardedAction blocks mutating actions while latest overview reports an active job", async () => {
  const calls = [];
  const blockedActions = [
    { actionLabel: "一键执行", postName: "one-click POST" },
    { actionLabel: "导出", postName: "export POST" },
    { actionLabel: "重新解析", postName: "archive POST" },
  ];

  for (const { actionLabel, postName } of blockedActions) {
    let postCalled = 0;
    let jobEventsCalled = 0;
    await assert.rejects(
      runActiveJobGuardedAction({
        fetchOverview: async () => {
          calls.push(`${actionLabel}:overview`);
          return {
            latest_job: {
              job_type: "download_ingest",
              status: "running",
            },
          };
        },
        actionLabel,
        execute: async () => {
          postCalled += 1;
          calls.push(`${actionLabel}:${postName}`);
        },
        fetchJobEvents: async () => {
          jobEventsCalled += 1;
        },
      }),
      (error) => error?.localOnly === true && /已有执行中的任务：历史区间任务/.test(error.message),
    );
    assert.equal(postCalled, 0, `${actionLabel} should not execute its POST path while another job is active`);
    assert.equal(jobEventsCalled, 0, `${actionLabel} guard should not fetch job events during preflight blocking`);
  }

  assert.deepEqual(calls, [
    "一键执行:overview",
    "导出:overview",
    "重新解析:overview",
  ]);
});

test("runActiveJobGuardedAction preserves normal POST path once latest overview is not active", async () => {
  const postCalls = [];
  const result = await runActiveJobGuardedAction({
    fetchOverview: async () => ({
      latest_job: {
        job_type: "one_click",
        status: "success",
      },
    }),
    actionLabel: "导出",
    execute: async (latestOverview) => {
      postCalls.push(latestOverview.latest_job.status);
      return "posted";
    },
  });

  assert.equal(result, "posted");
  assert.deepEqual(postCalls, ["success"]);
});

test("overview export treats failed business status as visible failure without success refresh", async () => {
  const { calls, html } = await runOverviewExportWithBusinessResult({
    status: "failed",
    failure_code: "export_field_missing",
    failure_message: "导出字段缺失",
  });

  const exportIndex = calls.findIndex((item) => Array.isArray(item) && item[0] === "runExport");
  assert.notEqual(exportIndex, -1, "export POST should be called");
  assert.deepEqual(calls.slice(exportIndex + 1), [], "business failure must not enter the success refresh path");
  assert.match(html, /alert-danger/);
  assert.match(html, /导出失败/);
  assert.match(html, /导出字段缺失/);
});

test("mappings load failure clears stale rules and pending items from the active panel", async () => {
  const { html, oldRuleText } = await renderMappingsAfterSuccessfulThenFailedLoad();

  assert.doesNotMatch(html, new RegExp(oldRuleText));
  assert.doesNotMatch(html, /OLD_PENDING_ITEM_F2E/);
  assert.match(html, /失败/);
  assert.match(html, /mapping backend unavailable/);
});

test("mapping delete business failure renders failure instead of success", async () => {
  const { html } = await deleteMappingWithBusinessResult({
    entry_id: "entry-1",
    deleted: false,
  });

  assert.match(html, /失败/);
  assert.doesNotMatch(html, /规则已删除/);
});

test("overview export treats empty business status as visible warning without success refresh", async () => {
  const { calls, html } = await runOverviewExportWithBusinessResult({
    status: "empty",
    message: "当前条件下没有可导出的记录",
    empty_reason_code: "no_exportable_records",
  });

  const exportIndex = calls.findIndex((item) => Array.isArray(item) && item[0] === "runExport");
  assert.notEqual(exportIndex, -1, "export POST should be called");
  assert.deepEqual(calls.slice(exportIndex + 1), [], "empty export must not enter the success refresh path");
  assert.match(html, /alert-danger/);
  assert.match(html, /导出失败/);
  assert.match(html, /当前条件下没有可导出的记录|no_exportable_records/);
});

test("overview export blocks field-missing business result without success refresh", async () => {
  const { calls, html } = await runOverviewExportWithBusinessResult({
    status: "success",
    message: "有记录因导出字段缺失被阻断",
    field_missing_blocked_records: 2,
    field_missing_diagnostics: [
      {
        record_id: "rec-missing",
        failure_code: "export_field_missing",
        missing_fields: [{ export_field: "类型", message: "export field 类型 is required" }],
      },
    ],
  });

  const exportIndex = calls.findIndex((item) => Array.isArray(item) && item[0] === "runExport");
  assert.notEqual(exportIndex, -1, "export POST should be called");
  assert.deepEqual(calls.slice(exportIndex + 1), [], "field-missing blocker must not enter the success refresh path");
  assert.match(html, /alert-danger/);
  assert.match(html, /导出失败/);
  assert.match(html, /字段缺失|field missing|类型/);
});

test("mutating action handlers wire the overview-only guard before one-click/export/archive POST", async () => {
  const source = await readFile(new URL("../app.js", import.meta.url), "utf8");

  const oneClickHandler = sliceBetween(
    source,
    "async function handleOneClick(payload = {}) {",
    "async function handleHistorical(payload = {}) {",
  );
  assert.match(oneClickHandler, /runActiveJobGuardedAction\(\{/);
  assert.match(oneClickHandler, /actionLabel:\s*"一键执行"/);
  assert.match(oneClickHandler, /await API\.runOneClick\(payload\);/);

  const archiveHandler = sliceBetween(source, "async function handleArchiveReprocess() {", "async function handleJobRetry(");
  assert.match(archiveHandler, /runActiveJobGuardedAction\(\{/);
  assert.match(archiveHandler, /actionLabel:\s*"重新解析"/);
  assert.match(archiveHandler, /await API\.runArchiveReprocess\(\);/);

  const retryHandler = sliceBetween(source, "async function handleJobRetry(job) {", "async function handleRecordReprocess(recordId) {");
  assert.match(retryHandler, /runActiveJobGuardedAction\(\{/);
  assert.match(retryHandler, /actionLabel:\s*"重试任务"/);
  assert.match(retryHandler, /API\.retryJob\(jobId\)/);

  const recordHandler = sliceBetween(source, "async function handleRecordReprocess(recordId) {", "async function handleExport(");
  assert.match(recordHandler, /runActiveJobGuardedAction\(\{/);
  assert.match(recordHandler, /actionLabel:\s*"重新处理记录"/);
  assert.match(recordHandler, /API\.reprocessRecord\(normalizedRecordId\)/);

  const exportHandler = sliceBetween(source, "async function handleExport(", "/* ── Data Fetch ── */");
  assert.match(exportHandler, /runActiveJobGuardedAction\(\{/);
  assert.match(exportHandler, /actionLabel:\s*"导出"/);
  assert.match(exportHandler, /API\.runExport\(scope, "full"\)/);
});

test("launched historical, manual-import, and archive jobs reconnect overview SSE after refresh", async () => {
  const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
  const historicalHandler = sliceBetween(
    source,
    "async function handleHistorical(payload = {}) {",
    "function handleManualImport() {",
  );
  const manualHandler = sliceBetween(
    source,
    "async function submitManualImport(request) {",
    "async function handleArchiveReprocess() {",
  );
  const archiveHandler = sliceBetween(
    source,
    "async function handleArchiveReprocess() {",
    "async function handleJobRetry(",
  );

  for (const handler of [historicalHandler, manualHandler, archiveHandler]) {
    assert.match(handler, /await refresh\(\);[^]*openOverviewStream\(\);/);
  }
});

test("overview export button invokes export without passing the DOM click event as a precheck error", async () => {
  const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
  const overviewRenderer = sliceBetween(
    source,
    "$(\"#btn-oneclick\").addEventListener(\"click\", actionModals.showOneClickModal);",
    "$$(\"[data-family-tab]\").forEach((btn) => {",
  );

  assert.ok(overviewRenderer.includes('$("#btn-export").addEventListener("click", () => handleExport());'));
  assert.ok(!overviewRenderer.includes('$("#btn-export").addEventListener("click", handleExport);'));
});
