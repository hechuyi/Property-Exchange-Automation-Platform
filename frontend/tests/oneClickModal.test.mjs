import test from "node:test";
import assert from "node:assert/strict";

import { createActionModals } from "../src/actions/modals.js";

function createFakeElement({ value = "" } = {}) {
  const listeners = new Map();
  const children = [];
  return {
    disabled: false,
    innerHTML: "",
    value,
    dataset: {},
    style: {},
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    appendChild(child) {
      children.push(child);
    },
    querySelector() {
      return null;
    },
    async click() {
      const handler = listeners.get("click");
      if (handler) {
        await handler();
      }
    },
    async change() {
      const handler = listeners.get("change");
      if (handler) {
        await handler();
      }
    },
  };
}

function createFakeDomNode(elementMap) {
  const children = [];
  return {
    id: "",
    innerHTML: "",
    textContent: "",
    dataset: {},
    style: {},
    disabled: false,
    className: "",
    _elements: elementMap,
    appendChild(child) {
      children.push(child);
    },
    querySelector() {
      return null;
    },
  };
}

function installFakeDocument(elementMap) {
  const attached = [];
  const documentStub = {
    createElement() {
      return createFakeDomNode(elementMap);
    },
    body: {
      appendChild(node) {
        attached.push(node);
      },
      removeChild(node) {
        const index = attached.indexOf(node);
        if (index >= 0) {
          attached.splice(index, 1);
        }
      },
      contains(node) {
        return attached.includes(node);
      },
    },
  };
  const previousDocument = globalThis.document;
  globalThis.document = documentStub;
  return {
    restore() {
      globalThis.document = previousDocument;
    },
    attached,
  };
}

test("one-click modal reloads the catalog when the cached catalog is still empty", async () => {
  const defaultsEl = createFakeElement();
  const statusEl = createFakeElement();
  const familyStatusEl = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const startDateEl = createFakeElement();
  const endDateEl = createFakeElement();
  const maxPagesEl = createFakeElement();
  const concurrencyEl = createFakeElement();
  const elementMap = new Map([
    ["#oneclick-defaults", defaultsEl],
    ["#oneclick-status", statusEl],
    ["#oneclick-family-status", familyStatusEl],
    ["#oneclick-confirm", confirmBtn],
    ["#oneclick-cancel", cancelBtn],
    ["#oneclick-start-date", startDateEl],
    ["#oneclick-end-date", endDateEl],
    ["#oneclick-max-pages", maxPagesEl],
    ["#oneclick-concurrency", concurrencyEl],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];
  let catalogCalls = 0;
  const loadedCatalog = {
    visible_families: [{
      family_id: "listing",
      businesses: [{ business_id: "equity_transfer", supported_surfaces: ["one_click"] }],
    }],
    support_matrix: { listing: { equity_transfer: { one_click: true } } },
    surface_source_matrix: { listing: { equity_transfer: { one_click: ["sse"] } } },
  };

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {
        async getCatalog() {
          catalogCalls += 1;
          return loadedCatalog;
        },
        async getSettingsBasic() {
          return {};
        },
      },
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return { basic: {} };
      },
      getCatalog() {
        return {};
      },
      getOverview() {
        return {};
      },
      async runOneClick(payload) {
        submittedPayloads.push(payload);
      },
      async runHistorical() {},
      async runManualImport() {},
    });

    await modals.showOneClickModal();

    assert.equal(catalogCalls, 1);
    assert.equal(confirmBtn.disabled, false);
    await confirmBtn.click();
    assert.equal(submittedPayloads.length, 1);
  } finally {
    documentHarness.restore();
  }
});

test("historical modal submits catalog family scopes via the dedicated historical runner", async () => {
  const defaultsEl = createFakeElement();
  const startDateEl = createFakeElement({ value: "2026-03-01" });
  const endDateEl = createFakeElement({ value: "2026-03-31" });
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const elementMap = new Map([
    ["#historical-defaults", defaultsEl],
    ["#hist-start", startDateEl],
    ["#hist-end", endDateEl],
    ["#hist-confirm", confirmBtn],
    ["#hist-cancel", cancelBtn],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const historicalPayloads = [];
  const oneClickPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["all", "全部交易所"], ["sse", "上交所"]],
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return {
          basic: {
            effective_default_scope: {
              record_family: "listing",
              business_id: "equity_transfer",
              business_label: "股权转让",
              exchange: "sse",
            },
            stored_preference: {
              record_family: "listing",
              business_id: "equity_transfer",
              exchange: "sse",
            },
            stale_default_metadata: {
              is_stale: false,
              reason: "",
              hint: "",
            },
            default_exchange: "sse",
            default_concurrency: 2,
          },
        };
      },
      getCatalog() {
        return {
          visible_families: [
            {
              family_id: "listing",
              businesses: [
                { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
              ],
            },
          ],
          support_matrix: {
            listing: {
              equity_transfer: { records: true, one_click: true, export: true },
            },
          },
          surface_source_matrix: {
            listing: {
              equity_transfer: { one_click: ["sse"] },
            },
          },
        };
      },
      getOverview() {
        return {};
      },
      async runHistorical(payload) {
        historicalPayloads.push(payload);
      },
      async runOneClick(payload) {
        oneClickPayloads.push(payload);
      },
      async runManualImport() {},
    });

    modals.showHistoricalModal();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.match(defaultsEl.innerHTML, /包含公共资源网成交/);
    await confirmBtn.click();

    assert.deepEqual(historicalPayloads, [
      {
        family_scopes: [
          { record_family: "listing", business_id: "all", business_label: "", exchange: "all" },
        ],
        include_public_resource: true,
        start_date: "2026-03-01",
        end_date: "2026-03-31",
      },
    ]);
    assert.deepEqual(oneClickPayloads, []);
    assert.equal(documentHarness.attached.length, 0);
  } finally {
    documentHarness.restore();
  }
});

test("historical modal keeps the form open and retryable when launch fails", async () => {
  const defaultsEl = createFakeElement();
  const startDateEl = createFakeElement({ value: "2026-03-01" });
  const endDateEl = createFakeElement({ value: "2026-03-31" });
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const elementMap = new Map([
    ["#historical-defaults", defaultsEl],
    ["#hist-start", startDateEl],
    ["#hist-end", endDateEl],
    ["#hist-confirm", confirmBtn],
    ["#hist-cancel", cancelBtn],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  let launchCount = 0;

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {
        async getOverview() {
          return { latest_job: null };
        },
      },
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return { basic: { default_concurrency: 2 } };
      },
      getCatalog() {
        return {
          visible_families: [{
            family_id: "listing",
            businesses: [{
              business_id: "equity_transfer",
              supported_surfaces: ["one_click"],
            }],
          }],
          support_matrix: {
            listing: { equity_transfer: { one_click: true } },
          },
          surface_source_matrix: {
            listing: { equity_transfer: { one_click: ["sse"] } },
          },
        };
      },
      getOverview() {
        return {};
      },
      async runHistorical() {
        launchCount += 1;
        throw new Error("backend unavailable");
      },
      async runOneClick() {},
      async runManualImport() {},
    });

    modals.showHistoricalModal();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await confirmBtn.click();

    assert.equal(launchCount, 1);
    assert.equal(documentHarness.attached.length, 1);
    assert.equal(confirmBtn.disabled, false);
    assert.equal(cancelBtn.disabled, false);
    assert.equal(startDateEl.disabled, false);
    assert.equal(endDateEl.disabled, false);
    assert.match(defaultsEl.className, /alert-danger/);
    assert.match(defaultsEl.textContent, /backend unavailable/);
  } finally {
    documentHarness.restore();
  }
});

test("historical modal blocks an active job without launching or closing", async () => {
  const defaultsEl = createFakeElement();
  const startDateEl = createFakeElement();
  const endDateEl = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const elementMap = new Map([
    ["#historical-defaults", defaultsEl],
    ["#hist-start", startDateEl],
    ["#hist-end", endDateEl],
    ["#hist-confirm", confirmBtn],
    ["#hist-cancel", cancelBtn],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  let launchCount = 0;

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {
        async getOverview() {
          return {
            latest_job: {
              job_type: "download_ingest",
              status: "running",
            },
          };
        },
      },
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return { basic: {} };
      },
      getCatalog() {
        return {
          visible_families: [{
            family_id: "listing",
            businesses: [{
              business_id: "equity_transfer",
              supported_surfaces: ["one_click"],
            }],
          }],
          support_matrix: {
            listing: { equity_transfer: { one_click: true } },
          },
          surface_source_matrix: {
            listing: { equity_transfer: { one_click: ["sse"] } },
          },
        };
      },
      getOverview() {
        return {};
      },
      async runHistorical() {
        launchCount += 1;
      },
      async runOneClick() {},
      async runManualImport() {},
    });

    modals.showHistoricalModal();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await confirmBtn.click();

    assert.equal(launchCount, 0);
    assert.equal(documentHarness.attached.length, 1);
    assert.equal(confirmBtn.disabled, false);
    assert.match(defaultsEl.textContent, /已有执行中的任务/);
  } finally {
    documentHarness.restore();
  }
});

test("historical modal reloads the catalog when the cached catalog is still empty", async () => {
  const defaultsEl = createFakeElement();
  const startDateEl = createFakeElement();
  const endDateEl = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const elementMap = new Map([
    ["#historical-defaults", defaultsEl],
    ["#hist-start", startDateEl],
    ["#hist-end", endDateEl],
    ["#hist-confirm", confirmBtn],
    ["#hist-cancel", cancelBtn],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const historicalPayloads = [];
  let catalogCalls = 0;
  const loadedCatalog = {
    visible_families: [{
      family_id: "listing",
      businesses: [{ business_id: "equity_transfer", supported_surfaces: ["one_click"] }],
    }],
    support_matrix: { listing: { equity_transfer: { one_click: true } } },
    surface_source_matrix: { listing: { equity_transfer: { one_click: ["sse"] } } },
  };

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {
        async getCatalog() {
          catalogCalls += 1;
          return loadedCatalog;
        },
        async getSettingsBasic() {
          return {};
        },
      },
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return { basic: {} };
      },
      getCatalog() {
        return {};
      },
      getOverview() {
        return {};
      },
      async runHistorical(payload) {
        historicalPayloads.push(payload);
      },
      async runOneClick() {},
      async runManualImport() {},
    });

    modals.showHistoricalModal();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(catalogCalls, 1);
    assert.equal(confirmBtn.disabled, false);
    await confirmBtn.click();
    assert.equal(historicalPayloads.length, 1);
  } finally {
    documentHarness.restore();
  }
});

test("historical modal submits catalog-wide listing and deal family scopes", async () => {
  const defaultsEl = createFakeElement();
  const startDateEl = createFakeElement({ value: "2026-04-01" });
  const endDateEl = createFakeElement({ value: "2026-04-30" });
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const familySelect = createFakeElement({ value: "listing" });
  const elementMap = new Map([
    ["#historical-defaults", defaultsEl],
    ["#hist-start", startDateEl],
    ["#hist-end", endDateEl],
    ["#hist-confirm", confirmBtn],
    ["#hist-cancel", cancelBtn],
    ["#hist-family", familySelect],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const historicalPayloads = [];
  const catalog = {
    visible_families: [
      {
        family_id: "listing",
        family_label: "挂牌业务",
        businesses: [
          { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
        ],
      },
      {
        family_id: "deal",
        family_label: "成交业务",
        businesses: [
          { business_id: "deal_equity_transfer", business_label: "股权转让成交", supported_surfaces: ["records", "one_click", "export"] },
        ],
      },
    ],
    support_matrix: {
      listing: {
        equity_transfer: { records: true, one_click: true, export: true },
      },
      deal: {
        deal_equity_transfer: { records: true, one_click: true, export: true },
      },
    },
    surface_source_matrix: {
      listing: {
        equity_transfer: { one_click: ["sse"] },
      },
      deal: {
        deal_equity_transfer: { one_click: ["sse"] },
      },
    },
  };

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["sse", "上交所"]],
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return {
          basic: {
            effective_default_scope: {
              record_family: "listing",
              business_id: "equity_transfer",
              business_label: "股权转让",
              exchange: "sse",
            },
            stored_preference: {
              record_family: "listing",
              business_id: "equity_transfer",
              exchange: "sse",
            },
            stale_default_metadata: {
              is_stale: false,
              reason: "",
              hint: "",
            },
            default_exchange: "sse",
            default_concurrency: 2,
          },
        };
      },
      getCatalog() {
        return catalog;
      },
      getOverview() {
        return {};
      },
      async runHistorical(payload) {
        historicalPayloads.push(payload);
      },
      async runOneClick() {},
      async runManualImport() {},
    });

    modals.showHistoricalModal();
    await new Promise((resolve) => setTimeout(resolve, 0));
    familySelect.value = "deal";
    await familySelect.change();
    await confirmBtn.click();

    assert.deepEqual(historicalPayloads, [
      {
        family_scopes: [
          { record_family: "listing", business_id: "all", business_label: "", exchange: "all" },
          { record_family: "deal", business_id: "all", business_label: "", exchange: "all" },
        ],
        include_public_resource: true,
        start_date: "2026-04-01",
        end_date: "2026-04-30",
      },
    ]);
  } finally {
    documentHarness.restore();
  }
});

test("historical modal hides source-business requirement policies without changing launch payload", async () => {
  const defaultsEl = createFakeElement();
  const startDateEl = createFakeElement({ value: "2026-05-01" });
  const endDateEl = createFakeElement({ value: "2026-05-31" });
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const elementMap = new Map([
    ["#historical-defaults", defaultsEl],
    ["#hist-start", startDateEl],
    ["#hist-end", endDateEl],
    ["#hist-confirm", confirmBtn],
    ["#hist-cancel", cancelBtn],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const historicalPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["shandong", "山东产权"]],
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return {
          basic: {
            effective_default_scope: {
              record_family: "listing",
              business_id: "equity_transfer",
              business_label: "股权转让",
              exchange: "shandong",
            },
            stored_preference: {
              record_family: "listing",
              business_id: "equity_transfer",
              exchange: "shandong",
            },
            stale_default_metadata: {
              is_stale: false,
              reason: "",
              hint: "",
            },
            default_exchange: "shandong",
            default_concurrency: 2,
          },
        };
      },
      getCatalog() {
        return {
          visible_families: [
            {
              family_id: "listing",
              family_label: "挂牌业务",
              businesses: [
                { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
              ],
            },
          ],
          sources: [
            { source_id: "shandong", source_label: "山东产权" },
          ],
          support_matrix: {
            listing: {
              equity_transfer: { records: true, one_click: true, export: true },
            },
          },
          surface_source_matrix: {
            listing: {
              equity_transfer: { one_click: ["shandong"], export: ["shandong"], records: ["shandong"] },
            },
          },
          source_business_requirements: {
            listing: {
              equity_transfer: {
                shandong: {
                  scope_policy: "central_soe_ministry_only",
                  scope_policy_label: "央企范围限定",
                  scope_policy_summary: "仅覆盖中央企业及其所属单位项目。",
                },
              },
            },
          },
        };
      },
      getOverview() {
        return {};
      },
      async runHistorical(payload) {
        historicalPayloads.push(payload);
      },
      async runOneClick() {},
      async runManualImport() {},
    });

    modals.showHistoricalModal();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.doesNotMatch(defaultsEl.innerHTML, /范围策略/);
    assert.doesNotMatch(defaultsEl.innerHTML, /央企范围限定/);
    assert.doesNotMatch(defaultsEl.innerHTML, /仅覆盖中央企业及其所属单位项目/);
    assert.doesNotMatch(defaultsEl.innerHTML, /central_soe_ministry_only/);

    await confirmBtn.click();

    assert.deepEqual(historicalPayloads, [
      {
        family_scopes: [
          { record_family: "listing", business_id: "all", business_label: "", exchange: "all" },
        ],
        include_public_resource: true,
        start_date: "2026-05-01",
        end_date: "2026-05-31",
      },
    ]);
  } finally {
    documentHarness.restore();
  }
});

test("one-click modal submits catalog family scopes with shared overrides", async () => {
  const defaultsEl = createFakeElement();
  const statusEl = createFakeElement();
  const familyStatusEl = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const startDateEl = createFakeElement({ value: "2026-03-01" });
  const endDateEl = createFakeElement({ value: "2026-03-31" });
  const maxPagesEl = createFakeElement({ value: "10" });
  const concurrencyEl = createFakeElement({ value: "2" });
  const elementMap = new Map([
    ["#oneclick-defaults", defaultsEl],
    ["#oneclick-status", statusEl],
    ["#oneclick-family-status", familyStatusEl],
    ["#oneclick-confirm", confirmBtn],
    ["#oneclick-cancel", cancelBtn],
    ["#oneclick-start-date", startDateEl],
    ["#oneclick-end-date", endDateEl],
    ["#oneclick-max-pages", maxPagesEl],
    ["#oneclick-concurrency", concurrencyEl],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["all", "全部交易所"], ["cbex", "北交所"]],
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return {
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
            default_concurrency: 4,
          },
        };
      },
      getCatalog() {
        return {
          visible_families: [
            {
              family_id: "listing",
              family_label: "挂牌业务",
              businesses: [
                { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
                { business_id: "physical_asset", business_label: "实物资产", supported_surfaces: ["records", "one_click", "export"] },
              ],
            },
            {
              family_id: "deal",
              family_label: "成交业务",
              businesses: [
                { business_id: "deal_physical_asset", business_label: "实物资产成交", supported_surfaces: ["records", "one_click", "export"] },
                { business_id: "deal_equity_transfer", business_label: "股权转让成交", supported_surfaces: ["records", "one_click", "export"] },
                { business_id: "deal_capital_increase", business_label: "增资扩股成交", supported_surfaces: ["records", "one_click", "export"] },
              ],
            },
          ],
          support_matrix: {
            listing: {
              equity_transfer: { records: true, one_click: true, export: true },
              physical_asset: { records: true, one_click: true, export: true },
            },
            deal: {
              deal_physical_asset: { records: true, one_click: true, export: true },
              deal_equity_transfer: { records: true, one_click: true, export: true },
              deal_capital_increase: { records: true, one_click: true, export: true },
            },
          },
          surface_source_matrix: {
            listing: {
              equity_transfer: { one_click: ["cbex"] },
              physical_asset: { one_click: ["cbex"] },
            },
            deal: {
              deal_physical_asset: { one_click: ["cbex", "sse"] },
              deal_equity_transfer: { one_click: ["cbex", "sse", "tpre", "cquae"] },
              deal_capital_increase: { one_click: ["cbex", "sse", "tpre", "cquae"] },
            },
          },
        };
      },
      getOverview() {
        return {};
      },
      async runOneClick(payload) {
        submittedPayloads.push(payload);
      },
      async runManualImport() {},
    });

    await modals.showOneClickModal();

    assert.match(defaultsEl.innerHTML, /2/);
    assert.match(defaultsEl.innerHTML, /包含公共资源网成交/);
    assert.match(familyStatusEl.innerHTML, /挂牌业务/);
    assert.match(familyStatusEl.innerHTML, /成交业务/);

    await confirmBtn.click();

    assert.equal(submittedPayloads.length, 1);
    assert.deepEqual(submittedPayloads[0].family_scopes, [
      { record_family: "listing", business_id: "all", business_label: "", exchange: "all" },
      { record_family: "deal", business_id: "all", business_label: "", exchange: "all" },
    ]);
    assert.equal(submittedPayloads[0].record_family, undefined);
    assert.equal(submittedPayloads[0].business_id, undefined);
    assert.equal(submittedPayloads[0].exchange, undefined);
    assert.equal(submittedPayloads[0].start_date, "2026-03-01");
    assert.equal(submittedPayloads[0].end_date, "2026-03-31");
    assert.equal(submittedPayloads[0].max_pages, 10);
    assert.equal(submittedPayloads[0].concurrency, 2);
    assert.equal(submittedPayloads[0].include_public_resource, true);
    assert.equal(statusEl.innerHTML.includes("启动失败"), false);
    assert.equal(statusEl.innerHTML.includes("已启动"), true);
  } finally {
    documentHarness.restore();
  }
});

test("one-click modal blocks when catalog has no executable one-click family scope", async () => {
  const defaultsEl = createFakeElement();
  const statusEl = createFakeElement();
  const familyStatusEl = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const startDateEl = createFakeElement();
  const endDateEl = createFakeElement();
  const maxPagesEl = createFakeElement();
  const concurrencyEl = createFakeElement();
  const elementMap = new Map([
    ["#oneclick-defaults", defaultsEl],
    ["#oneclick-status", statusEl],
    ["#oneclick-family-status", familyStatusEl],
    ["#oneclick-confirm", confirmBtn],
    ["#oneclick-cancel", cancelBtn],
    ["#oneclick-start-date", startDateEl],
    ["#oneclick-end-date", endDateEl],
    ["#oneclick-max-pages", maxPagesEl],
    ["#oneclick-concurrency", concurrencyEl],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["all", "全部交易所"], ["cbex", "北交所"]],
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return {
          basic: {
            effective_default_scope: {
              record_family: "listing",
              business_id: "physical_asset",
              business_label: "实物资产",
              exchange: "cbex",
            },
            stored_preference: {
              record_family: "listing",
              business_id: "physical_asset",
              exchange: "cbex",
            },
            stale_default_metadata: {
              is_stale: false,
              reason: "",
              hint: "",
            },
            default_exchange: "cbex",
            default_concurrency: 4,
          },
        };
      },
      getCatalog() {
        return {
          visible_families: [
            {
              family_id: "listing",
              family_label: "挂牌业务",
              businesses: [
                { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
                { business_id: "physical_asset", business_label: "实物资产", supported_surfaces: ["records"] },
              ],
            },
          ],
          support_matrix: {
            listing: {
              equity_transfer: { records: true, one_click: true, export: true },
              physical_asset: { records: true, one_click: false, export: false },
            },
          },
        };
      },
      getOverview() {
        return {};
      },
      async runOneClick(payload) {
        submittedPayloads.push(payload);
      },
      async runManualImport() {},
    });

    await modals.showOneClickModal();

    assert.match(defaultsEl.innerHTML, /没有可一键执行/);
    assert.equal(confirmBtn.disabled, true);

    await confirmBtn.click();

    assert.equal(submittedPayloads.length, 0);
  } finally {
    documentHarness.restore();
  }
});

test("one-click modal submits a single executable family as a family scope", async () => {
  const defaultsEl = createFakeElement();
  const statusEl = createFakeElement();
  const familyStatusEl = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const startDateEl = createFakeElement({ value: "2026-01-01" });
  const endDateEl = createFakeElement({ value: "2026-06-30" });
  const maxPagesEl = createFakeElement();
  const concurrencyEl = createFakeElement();
  const elementMap = new Map([
    ["#oneclick-defaults", defaultsEl],
    ["#oneclick-status", statusEl],
    ["#oneclick-family-status", familyStatusEl],
    ["#oneclick-confirm", confirmBtn],
    ["#oneclick-cancel", cancelBtn],
    ["#oneclick-start-date", startDateEl],
    ["#oneclick-end-date", endDateEl],
    ["#oneclick-max-pages", maxPagesEl],
    ["#oneclick-concurrency", concurrencyEl],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["all", "全部交易所"], ["cbex", "北交所"]],
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return {
          basic: {
            effective_default_scope: {
              record_family: "listing",
              business_id: "all",
              business_label: "",
              exchange: "all",
            },
            stored_preference: {
              record_family: "listing",
              business_id: "all",
              exchange: "all",
            },
            stale_default_metadata: {
              is_stale: false,
              reason: "",
              hint: "",
            },
            default_exchange: "cbex",
            default_concurrency: 4,
          },
        };
      },
      getCatalog() {
        return {
          visible_families: [
            {
              family_id: "listing",
              family_label: "挂牌业务",
              businesses: [
                { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
              ],
            },
          ],
          support_matrix: {
            listing: {
              equity_transfer: { records: true, one_click: true, export: true },
            },
          },
          surface_source_matrix: {
            listing: {
              equity_transfer: { one_click: ["cbex"] },
            },
          },
        };
      },
      getOverview() {
        return {};
      },
      async runOneClick(payload) {
        submittedPayloads.push(payload);
      },
      async runManualImport() {},
    });

    await modals.showOneClickModal();

    assert.match(defaultsEl.innerHTML, /1/);

    await confirmBtn.click();

    assert.equal(submittedPayloads.length, 1);
    assert.deepEqual(submittedPayloads[0].family_scopes, [
      { record_family: "listing", business_id: "all", business_label: "", exchange: "all" },
    ]);
    assert.equal(submittedPayloads[0].record_family, undefined);
    assert.equal(submittedPayloads[0].business_id, undefined);
    assert.equal(submittedPayloads[0].exchange, undefined);
    assert.equal(submittedPayloads[0].start_date, "2026-01-01");
    assert.equal(submittedPayloads[0].end_date, "2026-06-30");
    assert.equal(statusEl.innerHTML.includes("已启动"), true);
  } finally {
    documentHarness.restore();
  }
});

test("one-click modal submits catalog-wide listing and deal family scopes by default", async () => {
  const defaultsEl = createFakeElement();
  const statusEl = createFakeElement();
  const familyStatusEl = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const startDateEl = createFakeElement({ value: "2026-06-01" });
  const endDateEl = createFakeElement({ value: "2026-06-06" });
  const maxPagesEl = createFakeElement();
  const concurrencyEl = createFakeElement();
  const elementMap = new Map([
    ["#oneclick-defaults", defaultsEl],
    ["#oneclick-status", statusEl],
    ["#oneclick-family-status", familyStatusEl],
    ["#oneclick-confirm", confirmBtn],
    ["#oneclick-cancel", cancelBtn],
    ["#oneclick-start-date", startDateEl],
    ["#oneclick-end-date", endDateEl],
    ["#oneclick-max-pages", maxPagesEl],
    ["#oneclick-concurrency", concurrencyEl],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["all", "全部交易所"], ["cbex", "北交所"]],
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return {
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
            default_concurrency: 4,
          },
        };
      },
      getCatalog() {
        return {
          visible_families: [
            {
              family_id: "listing",
              family_label: "挂牌业务",
              businesses: [
                { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
                { business_id: "physical_asset", business_label: "实物资产", supported_surfaces: ["records", "one_click", "export"] },
              ],
            },
            {
              family_id: "deal",
              family_label: "成交业务",
              businesses: [
                { business_id: "deal_physical_asset", business_label: "实物资产成交", supported_surfaces: ["records", "one_click", "export"] },
                { business_id: "deal_equity_transfer", business_label: "股权转让成交", supported_surfaces: ["records", "one_click", "export"] },
                { business_id: "deal_capital_increase", business_label: "增资扩股成交", supported_surfaces: ["records", "one_click", "export"] },
              ],
            },
          ],
          sources: [
            { source_id: "cbex", source_label: "北交所" },
            { source_id: "sse", source_label: "上交所" },
            { source_id: "tpre", source_label: "天交所" },
            { source_id: "cquae", source_label: "重交所" },
          ],
          support_matrix: {
            listing: {
              equity_transfer: { records: true, one_click: true, export: true },
              physical_asset: { records: true, one_click: true, export: true },
            },
            deal: {
              deal_physical_asset: { records: true, one_click: true, export: true },
              deal_equity_transfer: { records: true, one_click: true, export: true },
              deal_capital_increase: { records: true, one_click: true, export: true },
            },
          },
          surface_source_matrix: {
            listing: {
              equity_transfer: { one_click: ["cbex", "sse"] },
              physical_asset: { one_click: ["cbex"] },
            },
            deal: {
              deal_physical_asset: { one_click: ["cbex", "sse"] },
              deal_equity_transfer: { one_click: ["cbex", "sse", "tpre", "cquae"] },
              deal_capital_increase: { one_click: ["cbex", "sse", "tpre", "cquae"] },
            },
          },
        };
      },
      getOverview() {
        return {};
      },
      async runOneClick(payload) {
        submittedPayloads.push(payload);
      },
      async runManualImport() {},
    });

    await modals.showOneClickModal();
    await confirmBtn.click();

    assert.equal(submittedPayloads.length, 1);
    assert.deepEqual(submittedPayloads[0], {
      family_scopes: [
        { record_family: "listing", business_id: "all", business_label: "", exchange: "all" },
        { record_family: "deal", business_id: "all", business_label: "", exchange: "all" },
      ],
      include_public_resource: true,
      start_date: "2026-06-01",
      end_date: "2026-06-06",
    });
    assert.equal(statusEl.innerHTML.includes("已启动"), true);
  } finally {
    documentHarness.restore();
  }
});

test("one-click modal does not replace the catalog-wide plan with a single deal scope", async () => {
  const defaultsEl = createFakeElement();
  const statusEl = createFakeElement();
  const familyStatusEl = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const startDateEl = createFakeElement({ value: "2026-06-01" });
  const endDateEl = createFakeElement({ value: "2026-06-06" });
  const maxPagesEl = createFakeElement();
  const concurrencyEl = createFakeElement();
  const elementMap = new Map([
    ["#oneclick-defaults", defaultsEl],
    ["#oneclick-status", statusEl],
    ["#oneclick-family-status", familyStatusEl],
    ["#oneclick-confirm", confirmBtn],
    ["#oneclick-cancel", cancelBtn],
    ["#oneclick-start-date", startDateEl],
    ["#oneclick-end-date", endDateEl],
    ["#oneclick-max-pages", maxPagesEl],
    ["#oneclick-concurrency", concurrencyEl],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["all", "全部交易所"], ["cbex", "北交所"]],
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return {
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
            default_concurrency: 4,
          },
        };
      },
      getCatalog() {
        return {
          visible_families: [
            {
              family_id: "listing",
              family_label: "挂牌业务",
              businesses: [
                { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
              ],
            },
            {
              family_id: "deal",
              family_label: "成交业务",
              businesses: [
                { business_id: "deal_physical_asset", business_label: "实物资产成交", supported_surfaces: ["records", "one_click", "export"] },
                { business_id: "deal_equity_transfer", business_label: "股权转让成交", supported_surfaces: ["records", "one_click", "export"] },
                { business_id: "deal_capital_increase", business_label: "增资扩股成交", supported_surfaces: ["records", "one_click", "export"] },
              ],
            },
          ],
          sources: [
            { source_id: "cbex", source_label: "北交所" },
            { source_id: "sse", source_label: "上交所" },
            { source_id: "tpre", source_label: "天交所" },
            { source_id: "cquae", source_label: "重交所" },
          ],
          support_matrix: {
            listing: {
              equity_transfer: { records: true, one_click: true, export: true },
            },
            deal: {
              deal_physical_asset: { records: true, one_click: true, export: true },
              deal_equity_transfer: { records: true, one_click: true, export: true },
              deal_capital_increase: { records: true, one_click: true, export: true },
            },
          },
          surface_source_matrix: {
            listing: {
              equity_transfer: { one_click: ["cbex"] },
            },
            deal: {
              deal_physical_asset: { one_click: ["cbex", "sse"] },
              deal_equity_transfer: { one_click: ["cbex", "sse", "tpre", "cquae"] },
              deal_capital_increase: { one_click: ["cbex", "sse", "tpre", "cquae"] },
            },
          },
        };
      },
      getOverview() {
        return {};
      },
      async runOneClick(payload) {
        submittedPayloads.push(payload);
      },
      async runManualImport() {},
    });

    await modals.showOneClickModal();
    await confirmBtn.click();

    assert.equal(submittedPayloads.length, 1);
    assert.deepEqual(submittedPayloads[0], {
      family_scopes: [
        { record_family: "listing", business_id: "all", business_label: "", exchange: "all" },
        { record_family: "deal", business_id: "all", business_label: "", exchange: "all" },
      ],
      include_public_resource: true,
      start_date: "2026-06-01",
      end_date: "2026-06-06",
    });
    assert.equal(statusEl.innerHTML.includes("已启动"), true);
  } finally {
    documentHarness.restore();
  }
});

test("one-click modal blocks launch when latest overview reports an active job", async () => {
  const defaultsEl = createFakeElement();
  const statusEl = createFakeElement();
  const familyStatusEl = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const startDateEl = createFakeElement();
  const endDateEl = createFakeElement();
  const maxPagesEl = createFakeElement();
  const concurrencyEl = createFakeElement();
  const elementMap = new Map([
    ["#oneclick-defaults", defaultsEl],
    ["#oneclick-status", statusEl],
    ["#oneclick-family-status", familyStatusEl],
    ["#oneclick-confirm", confirmBtn],
    ["#oneclick-cancel", cancelBtn],
    ["#oneclick-start-date", startDateEl],
    ["#oneclick-end-date", endDateEl],
    ["#oneclick-max-pages", maxPagesEl],
    ["#oneclick-concurrency", concurrencyEl],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];
  let overviewCalls = 0;

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {
        async getOverview() {
          overviewCalls += 1;
          return {
            latest_job: {
              job_type: "download_ingest",
              status: "running",
            },
          };
        },
      },
      RECORD_EXCHANGES: [["all", "全部交易所"], ["cbex", "北交所"]],
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return {
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
            default_concurrency: 2,
          },
        };
      },
      getCatalog() {
        return {
          visible_families: [
            {
              family_id: "listing",
              family_label: "挂牌业务",
              businesses: [
                { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
              ],
            },
          ],
          support_matrix: {
            listing: {
              equity_transfer: { records: true, one_click: true, export: true },
            },
          },
          surface_source_matrix: {
            listing: {
              equity_transfer: { one_click: ["cbex"] },
            },
          },
        };
      },
      getOverview() {
        return {};
      },
      async runOneClick(payload) {
        submittedPayloads.push(payload);
      },
      async runManualImport() {},
    });

    await modals.showOneClickModal();
    await confirmBtn.click();

    assert.equal(overviewCalls, 1);
    assert.deepEqual(submittedPayloads, []);
    assert.match(statusEl.innerHTML, /已有执行中的任务：历史区间任务，请等待完成后再一键执行。/);
  } finally {
    documentHarness.restore();
  }
});

test("one-click modal uses all-business all-exchange family scope instead of the default listing exchange", async () => {
  const defaultsEl = createFakeElement();
  const statusEl = createFakeElement();
  const familyStatusEl = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const startDateEl = createFakeElement();
  const endDateEl = createFakeElement();
  const maxPagesEl = createFakeElement({ value: "3" });
  const concurrencyEl = createFakeElement({ value: "2" });
  const elementMap = new Map([
    ["#oneclick-defaults", defaultsEl],
    ["#oneclick-status", statusEl],
    ["#oneclick-family-status", familyStatusEl],
    ["#oneclick-confirm", confirmBtn],
    ["#oneclick-cancel", cancelBtn],
    ["#oneclick-start-date", startDateEl],
    ["#oneclick-end-date", endDateEl],
    ["#oneclick-max-pages", maxPagesEl],
    ["#oneclick-concurrency", concurrencyEl],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["all", "全部交易所"], ["guangdong", "广交所"], ["shenzhen", "深交所"]],
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return {
          basic: {
            effective_default_scope: {
              record_family: "listing",
              business_id: "equity_transfer",
              business_label: "股权转让",
              exchange: "guangdong",
            },
            stored_preference: {
              record_family: "listing",
              business_id: "equity_transfer",
              exchange: "guangdong",
            },
            stale_default_metadata: {
              is_stale: false,
              reason: "",
              hint: "",
            },
            default_exchange: "guangdong",
            default_concurrency: 2,
          },
        };
      },
      getCatalog() {
        return {
          visible_families: [
            {
              family_id: "listing",
              family_label: "挂牌业务",
              businesses: [
                { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
                { business_id: "capital_increase", business_label: "增资扩股", supported_surfaces: ["records", "one_click", "export"] },
              ],
            },
          ],
          support_matrix: {
            listing: {
              equity_transfer: { records: true, one_click: true, export: true },
              capital_increase: { records: true, one_click: true, export: true },
            },
          },
          surface_source_matrix: {
            listing: {
              equity_transfer: { one_click: ["shandong", "guangdong", "shenzhen"] },
              capital_increase: { one_click: ["shenzhen"] },
            },
          },
        };
      },
      getOverview() {
        return {};
      },
      async runOneClick(payload) {
        submittedPayloads.push(payload);
      },
      async runManualImport() {},
    });

    await modals.showOneClickModal();
    assert.match(familyStatusEl.innerHTML, /全部业务类型/);
    assert.doesNotMatch(familyStatusEl.innerHTML, /跳过/);

    await confirmBtn.click();

    assert.equal(submittedPayloads.length, 1);
    assert.deepEqual(submittedPayloads[0].family_scopes, [
      { record_family: "listing", business_id: "all", business_label: "", exchange: "all" },
    ]);
    assert.equal(submittedPayloads[0].record_family, undefined);
    assert.equal(submittedPayloads[0].business_id, undefined);
    assert.equal(submittedPayloads[0].exchange, undefined);
    assert.equal(submittedPayloads[0].max_pages, 3);
    assert.equal(submittedPayloads[0].concurrency, 2);
    assert.equal(statusEl.innerHTML.includes("已启动一键执行任务。"), true);
  } finally {
    documentHarness.restore();
  }
});

test("one-click modal hides source-business requirement policies without changing launch payload", async () => {
  const backendOnlyField = ["backend", "only", "filters"].join("_");
  const defaultsEl = createFakeElement();
  const statusEl = createFakeElement();
  const familyStatusEl = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const startDateEl = createFakeElement();
  const endDateEl = createFakeElement();
  const maxPagesEl = createFakeElement();
  const concurrencyEl = createFakeElement();
  const elementMap = new Map([
    ["#oneclick-defaults", defaultsEl],
    ["#oneclick-status", statusEl],
    ["#oneclick-family-status", familyStatusEl],
    ["#oneclick-confirm", confirmBtn],
    ["#oneclick-cancel", cancelBtn],
    ["#oneclick-start-date", startDateEl],
    ["#oneclick-end-date", endDateEl],
    ["#oneclick-max-pages", maxPagesEl],
    ["#oneclick-concurrency", concurrencyEl],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["shandong", "山东产权"]],
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath() {
        return "";
      },
      getSettings() {
        return {
          basic: {
            effective_default_scope: {
              record_family: "listing",
              business_id: "equity_transfer",
              business_label: "股权转让",
              exchange: "shandong",
            },
            stored_preference: {
              record_family: "listing",
              business_id: "equity_transfer",
              exchange: "shandong",
            },
            stale_default_metadata: {
              is_stale: false,
              reason: "",
              hint: "",
            },
            default_exchange: "shandong",
            default_concurrency: 2,
          },
        };
      },
      getCatalog() {
        return {
          visible_families: [
            {
              family_id: "listing",
              family_label: "挂牌业务",
              businesses: [
                { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
              ],
            },
          ],
          sources: [
            { source_id: "shandong", source_label: "山东产权" },
          ],
          support_matrix: {
            listing: {
              equity_transfer: { records: true, one_click: true, export: true },
            },
          },
          surface_source_matrix: {
            listing: {
              equity_transfer: { one_click: ["shandong"], export: ["shandong"], records: ["shandong"] },
            },
          },
          source_business_requirements: {
            listing: {
              equity_transfer: {
                shandong: {
                  scope_policy: "central_soe_ministry_only",
                  scope_policy_label: "央企范围限定",
                  scope_policy_summary: "仅覆盖中央企业及其所属单位项目。",
                  [backendOnlyField]: { opaque: "backend-only" },
                },
              },
            },
          },
        };
      },
      getOverview() {
        return {};
      },
      async runOneClick(payload) {
        submittedPayloads.push(payload);
      },
      async runManualImport() {},
    });

    await modals.showOneClickModal();

    assert.doesNotMatch(familyStatusEl.innerHTML, /范围策略/);
    assert.doesNotMatch(familyStatusEl.innerHTML, /央企范围限定/);
    assert.doesNotMatch(familyStatusEl.innerHTML, /仅覆盖中央企业及其所属单位项目/);
    assert.doesNotMatch(familyStatusEl.innerHTML, /central_soe_ministry_only/);
    assert.doesNotMatch(familyStatusEl.innerHTML, /backend-only/);

    await confirmBtn.click();

    assert.equal(submittedPayloads.length, 1);
    assert.deepEqual(submittedPayloads[0].family_scopes, [
      { record_family: "listing", business_id: "all", business_label: "", exchange: "all" },
    ]);
    assert.equal("source_business_requirements" in submittedPayloads[0], false);
  } finally {
    documentHarness.restore();
  }
});
