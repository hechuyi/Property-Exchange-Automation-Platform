import test from "node:test";
import assert from "node:assert/strict";

import { API } from "../api.js";

function createElement(id = "") {
  const listeners = new Map();
  return {
    id,
    dataset: {},
    innerHTML: "",
    textContent: "",
    value: "",
    disabled: false,
    style: {},
    classList: {
      toggle() {},
      add() {},
      remove() {},
    },
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    listeners,
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

function createDocument() {
  const elements = new Map();
  ["panel-overview", "panel-tasks", "panel-records", "panel-reviews", "panel-export-history", "panel-mappings", "panel-settings"]
    .forEach((id) => elements.set(id, createElement(id)));
  const readyListeners = [];
  return {
    elements,
    createElement: () => createElement(),
    addEventListener(type, listener) {
      if (type === "DOMContentLoaded") readyListeners.push(listener);
    },
    dispatchReady() {
      readyListeners.forEach((listener) => listener({ type: "DOMContentLoaded" }));
    },
    querySelector(selector) {
      if (selector.startsWith("#")) {
        const id = selector.slice(1);
        if (!elements.has(id)) elements.set(id, createElement(id));
        return elements.get(id);
      }
      return null;
    },
    querySelectorAll(selector) {
      if (selector === ".panel") {
        return [...elements.values()].filter((element) => element.id.startsWith("panel-"));
      }
      if (selector === ".sidebar-nav-link" || selector === "[data-family-tab]") return [];
      return [];
    },
    body: {
      appendChild() {},
      contains() { return false; },
    },
  };
}

function buildCatalog() {
  return {
    visible_families: [
      { family_id: "listing", family_label: "挂牌业务", businesses: [] },
      { family_id: "deal", family_label: "成交业务", businesses: [] },
    ],
  };
}

function activeOverview() {
  return {
    record_summary: { state_counts: { ready: 1 } },
    latest_job: {
      job_id: "job-1",
      job_type: "one_click",
      status: "running",
      created_at: "2026-08-19 10:00:00",
      updated_at: "2026-08-19 10:00:00",
    },
    latest_progress: {},
    recent_jobs: [],
    runtime: {},
  };
}

function terminalFrame() {
  return {
    job_id: "job-1",
    overview: {
      ...activeOverview(),
      latest_job: {
        ...activeOverview().latest_job,
        status: "success",
      },
    },
    events: [],
  };
}

async function bootScenario({ onStats } = {}) {
  const document = createDocument();
  const previousGlobals = {
    document: globalThis.document,
    window: globalThis.window,
    EventSource: globalThis.EventSource,
  };
  const streams = [];
  class FakeEventSource {
    static CLOSED = 2;

    constructor(url) {
      this.url = url;
      this.readyState = 1;
      this.closeCalls = 0;
      streams.push(this);
    }

    close() {
      this.closeCalls += 1;
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
  let overviewCalls = 0;
  let statsCalls = 0;
  Object.assign(API, {
    base: "",
    apiToken: "",
    async getCatalog() {
      return buildCatalog();
    },
    async getSettingsBasic() {
      return {};
    },
    async getOverview() {
      overviewCalls += 1;
      return activeOverview();
    },
    async getJobEvents() {
      return { events: [] };
    },
    async listRecords() {
      statsCalls += 1;
      if (typeof onStats === "function") await onStats(statsCalls);
      return { summary: { filtered_state_counts: { ready: statsCalls } }, rows: [] };
    },
    async listMappings() {
      return { sections: [], summary: {}, entries: [] };
    },
  });

  try {
    await import(new URL(`../app.js?overview-stream-lifecycle=${Date.now()}-${Math.random()}`, import.meta.url).href);
    document.dispatchReady();
    for (let attempt = 0; attempt < 40 && streams.length === 0; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    assert.equal(streams.length, 1, "overview should open one SSE connection for the active job");
    return {
      document,
      streams,
      getOverviewCalls: () => overviewCalls,
      getStatsCalls: () => statsCalls,
      async cleanup() {
        if (typeof globalThis.window?.switchPanel === "function") {
          await globalThis.window.switchPanel("mappings");
        }
        Object.assign(API, previousApi);
        globalThis.document = previousGlobals.document;
        globalThis.window = previousGlobals.window;
        globalThis.EventSource = previousGlobals.EventSource;
      },
    };
  } catch (error) {
    Object.assign(API, previousApi);
    globalThis.document = previousGlobals.document;
    globalThis.window = previousGlobals.window;
    globalThis.EventSource = previousGlobals.EventSource;
    throw error;
  }
}

test("overview SSE terminal frame closes once and refreshes family stats once", async () => {
  const scenario = await bootScenario();
  try {
    const stream = scenario.streams[0];
    const initialStatsCalls = scenario.getStatsCalls();
    assert.equal(initialStatsCalls, 2);

    stream.onmessage({ data: JSON.stringify(terminalFrame()) });
    stream.onmessage({ data: JSON.stringify(terminalFrame()) });
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(stream.closeCalls, 1);
    assert.equal(scenario.getStatsCalls(), initialStatsCalls + 2);
  } finally {
    await scenario.cleanup();
  }
});

test("overview SSE transport error closes the stream and falls back to polling", async () => {
  const scenario = await bootScenario();
  try {
    const stream = scenario.streams[0];
    const scheduled = [];
    const originalSetTimeout = globalThis.setTimeout;
    const originalClearTimeout = globalThis.clearTimeout;
    globalThis.setTimeout = (callback, delay) => {
      scheduled.push({ callback, delay });
      return scheduled.length;
    };
    globalThis.clearTimeout = () => {};
    try {
      const initialOverviewCalls = scenario.getOverviewCalls();
      stream.onerror(new Error("connection lost"));

      assert.equal(stream.closeCalls, 1);
      assert.equal(scheduled.length, 1);
      assert.equal(scheduled[0].delay, 8000);

      await scheduled[0].callback();
      assert.equal(scenario.getOverviewCalls(), initialOverviewCalls + 1);
    } finally {
      globalThis.setTimeout = originalSetTimeout;
      globalThis.clearTimeout = originalClearTimeout;
    }
  } finally {
    await scenario.cleanup();
  }
});

test("overlapping terminal and navigation refreshes share one family-stats load", async () => {
  let releaseSlowStats;
  const scenario = await bootScenario({
    onStats(callCount) {
      if (callCount === 3) {
        return new Promise((resolve) => {
          releaseSlowStats = resolve;
        });
      }
      return undefined;
    },
  });
  try {
    const stream = scenario.streams[0];
    stream.onmessage({ data: JSON.stringify(terminalFrame()) });
    const navigation = globalThis.window.switchPanel("overview");
    await Promise.resolve();
    await Promise.resolve();

    assert.equal(scenario.getStatsCalls(), 3);
    releaseSlowStats();
    await navigation;
    await Promise.resolve();

    assert.equal(scenario.getStatsCalls(), 4);
  } finally {
    await scenario.cleanup();
  }
});
