import test from "node:test";
import assert from "node:assert/strict";

import { API } from "../api.js";

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
  const panels = [
    "panel-overview",
    "panel-tasks",
    "panel-records",
    "panel-reviews",
    "panel-export-history",
    "panel-mappings",
    "panel-settings",
  ].map((id) => createFakeElement(id));
  panels.forEach((panel) => elements.set(panel.id, panel));

  return {
    elements,
    panelSettings: elements.get("panel-settings"),
    addEventListener(eventName, listener) {
      if (eventName === "DOMContentLoaded") readyListeners.push(listener);
    },
    async dispatchReady() {
      await Promise.all(readyListeners.map((listener) => listener({ type: "DOMContentLoaded" })));
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
      if (selector === ".panel") return panels;
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
  globalThis.location = previousGlobals.location;
  globalThis.confirm = previousGlobals.confirm;
  delete globalThis.switchPanel;
}

function restoreApi(previousApi) {
  Object.assign(API, previousApi);
}

function buildSettingsCatalog() {
  return {
    visible_families: [
      {
        family_id: "listing",
        family_label: "挂牌业务",
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
      { source_id: "cbex", source_label: "北京产权交易所" },
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
  };
}

function buildBasicSettings() {
  return {
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
    retention_count: 9,
    paths: {
      workspace_root: "/workspace",
      archive_root: "/old-archive-root",
      export_root: "/old-export-root",
    },
  };
}

async function saveSettingsWithReloadFailure(saveButtonId) {
  const document = createFakeDocument();
  const previousGlobals = {
    document: globalThis.document,
    window: globalThis.window,
    EventSource: globalThis.EventSource,
    location: globalThis.location,
    confirm: globalThis.confirm,
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
  globalThis.window = globalThis;
  globalThis.location = { pathname: "/settings" };
  globalThis.confirm = () => true;
  globalThis.EventSource = FakeEventSource;

  const previousApi = { ...API };
  let getSettingsBasicCalls = 0;
  const calls = [];
  Object.assign(API, {
    base: "",
    apiToken: "",
    async getCatalog() {
      calls.push("getCatalog");
      return buildSettingsCatalog();
    },
    async getSettingsBasic() {
      getSettingsBasicCalls += 1;
      calls.push("getSettingsBasic");
      if (getSettingsBasicCalls >= 3) {
        throw new Error("settings reload unavailable");
      }
      return buildBasicSettings();
    },
    async getSettingsAdvanced() {
      calls.push("getSettingsAdvanced");
      return {
        processing: {
          save_json: true,
          postprocess_config: "/postprocess.json",
        },
        ingest_paths: {
          raw_manual_root: "/manual",
          raw_auto_root: "/old-archive-root",
        },
        runtime_paths: {
          app_home: "/app",
          streaming_db: "/streaming.sqlite3",
          log_dir: "/logs",
          cache_dir: "/cache",
          browser_cache_dir: "/browser-cache",
          archive_root: "/old-archive-root",
          export_root: "/old-export-root",
        },
      };
    },
    async getRuntimeDependencies() {
      calls.push("getRuntimeDependencies");
      return {
        browser: {},
        install: {},
        readiness: {},
      };
    },
    async saveSettingsBasic() {
      calls.push("saveSettingsBasic");
      return buildBasicSettings();
    },
    async saveSettingsAdvanced() {
      calls.push("saveSettingsAdvanced");
      return {
        processing: {
          save_json: true,
          postprocess_config: "/postprocess.json",
        },
        ingest_paths: {
          raw_manual_root: "/manual",
          raw_auto_root: "/old-archive-root",
        },
      };
    },
  });

  try {
    await import(new URL(`../app.js?settings-reload-failure=${Date.now()}-${Math.random()}`, import.meta.url).href);
    await document.dispatchReady();
    await waitFor(
      () => typeof document.elements.get(saveButtonId)?.listeners.click === "function",
      "settings save listener should be registered",
    );
    await document.elements.get(saveButtonId).listeners.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    return {
      calls,
      html: document.panelSettings.innerHTML,
    };
  } finally {
    restoreApi(previousApi);
    restoreGlobals(previousGlobals);
  }
}

test("basic settings save reports reload failure instead of rendering old settings as a success state", async () => {
  const { calls, html } = await saveSettingsWithReloadFailure("btn-settings-basic-save");

  assert.deepEqual(calls.filter((call) => call === "saveSettingsBasic"), ["saveSettingsBasic"]);
  assert.match(html, /失败/);
  assert.match(html, /settings reload unavailable/);
  assert.doesNotMatch(html, /基本设置已保存/);
  assert.doesNotMatch(html, /股权转让/);
  assert.doesNotMatch(html, /old-archive-root/);
});

test("advanced settings save reports reload failure instead of rendering old settings as a success state", async () => {
  const { calls, html } = await saveSettingsWithReloadFailure("btn-settings-advanced-save");

  assert.deepEqual(calls.filter((call) => call === "saveSettingsAdvanced"), ["saveSettingsAdvanced"]);
  assert.match(html, /失败/);
  assert.match(html, /settings reload unavailable/);
  assert.doesNotMatch(html, /高级设置已保存/);
  assert.doesNotMatch(html, /股权转让/);
  assert.doesNotMatch(html, /old-archive-root/);
});
