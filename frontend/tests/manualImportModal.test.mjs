import test from "node:test";
import assert from "node:assert/strict";

import { createActionModals } from "../src/actions/modals.js";

function createFakeElement({ value = "" } = {}) {
  const listeners = new Map();
  return {
    disabled: false,
    innerHTML: "",
    value,
    focused: false,
    selected: false,
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    async trigger(type) {
      const handler = listeners.get(type);
      if (handler) {
        await handler();
      }
    },
    async click() {
      await this.trigger("click");
    },
    focus() {
      this.focused = true;
    },
    select() {
      this.selected = true;
    },
  };
}

function installFakeDocument(elementMap) {
  const attached = [];
  const documentStub = {
    createElement() {
      return {
        id: "",
        innerHTML: "",
        style: {},
        _elements: elementMap,
      };
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

test("manual import modal writes chosen directory back into the input and submits it", async () => {
  const statusEl = createFakeElement();
  const inputEl = createFakeElement({ value: "/tmp/default-manual" });
  const familyEl = createFakeElement({ value: "listing" });
  const businessEl = createFakeElement({ value: "" });
  const exchangeEl = createFakeElement({ value: "sse" });
  const browseBtn = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const elementMap = new Map([
    ["#manual-import-status", statusEl],
    ["#manual-import-dir", inputEl],
    ["#manual-import-family", familyEl],
    ["#manual-import-business", businessEl],
    ["#manual-import-exchange", exchangeEl],
    ["#manual-import-browse", browseBtn],
    ["#manual-import-confirm", confirmBtn],
    ["#manual-import-cancel", cancelBtn],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const pickerCalls = [];
  const submittedDirs = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [],
      escapeHtml(value) {
        return String(value || "");
      },
      text(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      async chooseLocalPath(payload) {
        pickerCalls.push(payload);
        return "/tmp/chosen-manual";
      },
      getSettings() {
        return {};
      },
      getOverview() {
        return {
          defaults: {
            manual_import_input_dir: "/tmp/default-manual",
          },
        };
      },
      async runOneClick() {},
      async runManualImport(request) {
        submittedDirs.push(request);
      },
    });

    modals.showManualImportModal();
    await Promise.resolve();

    assert.equal(inputEl.value, "/tmp/default-manual");
    assert.equal(inputEl.focused, true);
    assert.equal(inputEl.selected, true);

    await browseBtn.click();

    assert.deepEqual(pickerCalls, [
      {
        selection_kind: "directory",
        prompt: "选择待导入网页目录",
        current_path: "/tmp/default-manual",
      },
    ]);
    assert.equal(inputEl.value, "/tmp/chosen-manual");

    await confirmBtn.click();

    assert.deepEqual(submittedDirs, [{ input_dir: "/tmp/chosen-manual" }]);
    assert.equal(documentHarness.attached.length, 0);
    assert.equal(statusEl.innerHTML.includes("导入失败"), false);
  } finally {
    documentHarness.restore();
  }
});

test("manual import modal blocks submission when catalog loading fails", async () => {
  const statusEl = createFakeElement();
  const inputEl = createFakeElement({ value: "/tmp/default-manual" });
  const familyEl = createFakeElement({ value: "" });
  const businessEl = createFakeElement({ value: "" });
  const exchangeEl = createFakeElement({ value: "" });
  const browseBtn = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const elementMap = new Map([
    ["#manual-import-status", statusEl],
    ["#manual-import-dir", inputEl],
    ["#manual-import-family", familyEl],
    ["#manual-import-business", businessEl],
    ["#manual-import-exchange", exchangeEl],
    ["#manual-import-browse", browseBtn],
    ["#manual-import-confirm", confirmBtn],
    ["#manual-import-cancel", cancelBtn],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {
        async getCatalog() {
          throw new Error("catalog unavailable");
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
      getOverview() {
        return {
          defaults: {
            manual_import_input_dir: "/tmp/default-manual",
          },
        };
      },
      async runOneClick() {},
      async runManualImport(request) {
        submittedPayloads.push(request);
      },
    });

    modals.showManualImportModal();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    await confirmBtn.click();

    assert.deepEqual(submittedPayloads, []);
    assert.equal(confirmBtn.disabled, true);
    assert.equal(statusEl.innerHTML.includes("读取业务目录失败"), true);
    assert.equal(statusEl.innerHTML.includes("catalog unavailable"), true);
    assert.equal(documentHarness.attached.length, 1);
  } finally {
    documentHarness.restore();
  }
});

test("manual import modal can submit an explicit business scope without inheriting it implicitly", async () => {
  const statusEl = createFakeElement();
  const inputEl = createFakeElement({ value: "/tmp/default-manual" });
  const familyEl = createFakeElement({ value: "listing" });
  const businessEl = createFakeElement({ value: "equity_transfer" });
  const exchangeEl = createFakeElement({ value: "sse" });
  const browseBtn = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const elementMap = new Map([
    ["#manual-import-status", statusEl],
    ["#manual-import-dir", inputEl],
    ["#manual-import-family", familyEl],
    ["#manual-import-business", businessEl],
    ["#manual-import-exchange", exchangeEl],
    ["#manual-import-browse", browseBtn],
    ["#manual-import-confirm", confirmBtn],
    ["#manual-import-cancel", cancelBtn],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["all", "全部"], ["sse", "上交所"], ["cbex", "北交所"]],
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
            default_exchange: "sse",
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
                { business_id: "physical_asset", business_label: "实物资产" },
                { business_id: "equity_transfer", business_label: "股权转让" },
              ],
            },
          ],
        };
      },
      getOverview() {
        return {
          defaults: {
            manual_import_input_dir: "/tmp/default-manual",
          },
        };
      },
      async runOneClick() {},
      async runManualImport(request) {
        submittedPayloads.push(request);
      },
    });

    modals.showManualImportModal();
    await Promise.resolve();
    await Promise.resolve();
    businessEl.value = "equity_transfer";
    await businessEl.trigger("change");
    exchangeEl.value = "sse";

    await confirmBtn.click();

    assert.deepEqual(submittedPayloads, [
      {
        input_dir: "/tmp/default-manual",
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "股权转让",
        exchange: "sse",
      },
    ]);
    assert.equal(documentHarness.attached.length, 0);
    assert.equal(statusEl.innerHTML.includes("导入失败"), false);
  } finally {
    documentHarness.restore();
  }
});

test("manual import modal does not silently inherit a default exchange when the user only selects business", async () => {
  const statusEl = createFakeElement();
  const inputEl = createFakeElement({ value: "/tmp/default-manual" });
  const familyEl = createFakeElement({ value: "listing" });
  const businessEl = createFakeElement({ value: "" });
  const exchangeEl = createFakeElement({ value: "" });
  const browseBtn = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const elementMap = new Map([
    ["#manual-import-status", statusEl],
    ["#manual-import-dir", inputEl],
    ["#manual-import-family", familyEl],
    ["#manual-import-business", businessEl],
    ["#manual-import-exchange", exchangeEl],
    ["#manual-import-browse", browseBtn],
    ["#manual-import-confirm", confirmBtn],
    ["#manual-import-cancel", cancelBtn],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["all", "全部"], ["sse", "上交所"], ["cbex", "北交所"]],
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
            default_exchange: "sse",
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
                { business_id: "physical_asset", business_label: "实物资产" },
                { business_id: "equity_transfer", business_label: "股权转让" },
              ],
            },
          ],
        };
      },
      getOverview() {
        return {
          defaults: {
            manual_import_input_dir: "/tmp/default-manual",
          },
        };
      },
      async runOneClick() {},
      async runManualImport(request) {
        submittedPayloads.push(request);
      },
    });

    modals.showManualImportModal();
    await Promise.resolve();
    await Promise.resolve();

    assert.equal(exchangeEl.value, "");
    assert.equal(exchangeEl.disabled, true);

    businessEl.value = "equity_transfer";
    await businessEl.trigger("change");

    assert.equal(exchangeEl.disabled, true);
    assert.equal(exchangeEl.value, "");

    await confirmBtn.click();

    assert.deepEqual(submittedPayloads, []);
    assert.equal(statusEl.innerHTML.includes("显式业务 hint 需要同时选择交易所"), true);
    assert.equal(documentHarness.attached.length, 1);
  } finally {
    documentHarness.restore();
  }
});

test("manual import modal fails closed instead of inventing a listing family for explicit scope", async () => {
  const statusEl = createFakeElement();
  const inputEl = createFakeElement({ value: "/tmp/default-manual" });
  const familyEl = createFakeElement({ value: "" });
  const businessEl = createFakeElement({ value: "" });
  const exchangeEl = createFakeElement({ value: "" });
  const browseBtn = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const elementMap = new Map([
    ["#manual-import-status", statusEl],
    ["#manual-import-dir", inputEl],
    ["#manual-import-family", familyEl],
    ["#manual-import-business", businessEl],
    ["#manual-import-exchange", exchangeEl],
    ["#manual-import-browse", browseBtn],
    ["#manual-import-confirm", confirmBtn],
    ["#manual-import-cancel", cancelBtn],
  ]);
  const documentHarness = installFakeDocument(elementMap);
  const submittedPayloads = [];

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [["all", "全部"], ["sse", "上交所"]],
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
        return { visible_families: [] };
      },
      getOverview() {
        return {
          defaults: {
            manual_import_input_dir: "/tmp/default-manual",
          },
        };
      },
      async runOneClick() {},
      async runManualImport(request) {
        submittedPayloads.push(request);
      },
    });

    modals.showManualImportModal();
    await Promise.resolve();
    await Promise.resolve();

    businessEl.value = "equity_transfer";
    exchangeEl.value = "sse";

    await confirmBtn.click();

    assert.deepEqual(submittedPayloads, []);
    assert.equal(statusEl.innerHTML.includes("显式业务 hint 缺少业务类别"), true);
    assert.equal(documentHarness.attached.length, 1);
  } finally {
    documentHarness.restore();
  }
});

test("manual import modal filters exchange hints by selected business source contract", async () => {
  const statusEl = createFakeElement();
  const inputEl = createFakeElement({ value: "/tmp/default-manual" });
  const familyEl = createFakeElement({ value: "deal" });
  const businessEl = createFakeElement({ value: "" });
  const exchangeEl = createFakeElement({ value: "" });
  const browseBtn = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const elementMap = new Map([
    ["#manual-import-status", statusEl],
    ["#manual-import-dir", inputEl],
    ["#manual-import-family", familyEl],
    ["#manual-import-business", businessEl],
    ["#manual-import-exchange", exchangeEl],
    ["#manual-import-browse", browseBtn],
    ["#manual-import-confirm", confirmBtn],
    ["#manual-import-cancel", cancelBtn],
  ]);
  const documentHarness = installFakeDocument(elementMap);

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [
        ["all", "全部"],
        ["cbex", "北交所"],
        ["sse", "上交所"],
        ["tpre", "天交所"],
        ["cquae", "重交所"],
      ],
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
          visible_families: [
            {
              family_id: "deal",
              family_label: "成交业务",
              businesses: [
                { business_id: "deal_equity_transfer", business_label: "股权成交" },
                { business_id: "deal_physical_asset", business_label: "实物资产成交" },
              ],
            },
          ],
          sources: [
            { source_id: "cbex", source_label: "北交所", record_families: ["deal"] },
            { source_id: "sse", source_label: "上交所", record_families: ["deal"] },
            { source_id: "tpre", source_label: "天交所", record_families: ["deal"] },
            { source_id: "cquae", source_label: "重交所", record_families: ["deal"] },
          ],
          surface_source_matrix: {
            deal: {
              deal_equity_transfer: {
                records: ["cbex", "sse", "tpre", "cquae"],
              },
              deal_physical_asset: {
                records: ["cbex", "sse"],
              },
            },
          },
        };
      },
      getOverview() {
        return {
          defaults: {
            manual_import_input_dir: "/tmp/default-manual",
          },
        };
      },
      async runOneClick() {},
      async runManualImport() {},
    });

    modals.showManualImportModal();
    await Promise.resolve();
    await Promise.resolve();

    businessEl.value = "deal_equity_transfer";
    await businessEl.trigger("change");
    assert.match(exchangeEl.innerHTML, /value="tpre"/);

    exchangeEl.value = "tpre";
    businessEl.value = "deal_physical_asset";
    await businessEl.trigger("change");

    assert.match(exchangeEl.innerHTML, /value="cbex"/);
    assert.match(exchangeEl.innerHTML, /value="sse"/);
    assert.doesNotMatch(exchangeEl.innerHTML, /value="tpre"/);
    assert.doesNotMatch(exchangeEl.innerHTML, /value="cquae"/);
    assert.equal(exchangeEl.value, "");
  } finally {
    documentHarness.restore();
  }
});

test("manual import listing exchange hints hide unsupported capital-increase exchanges", async () => {
  const statusEl = createFakeElement();
  const inputEl = createFakeElement({ value: "/tmp/default-manual" });
  const familyEl = createFakeElement({ value: "listing" });
  const businessEl = createFakeElement({ value: "" });
  const exchangeEl = createFakeElement({ value: "" });
  const browseBtn = createFakeElement();
  const confirmBtn = createFakeElement();
  const cancelBtn = createFakeElement();
  const elementMap = new Map([
    ["#manual-import-status", statusEl],
    ["#manual-import-dir", inputEl],
    ["#manual-import-family", familyEl],
    ["#manual-import-business", businessEl],
    ["#manual-import-exchange", exchangeEl],
    ["#manual-import-browse", browseBtn],
    ["#manual-import-confirm", confirmBtn],
    ["#manual-import-cancel", cancelBtn],
  ]);
  const documentHarness = installFakeDocument(elementMap);

  try {
    const modals = createActionModals({
      $(selector, container) {
        return container?._elements.get(selector) || null;
      },
      API: {},
      RECORD_EXCHANGES: [
        ["all", "全部交易所"],
        ["shandong", "山东产权交易中心"],
        ["guangdong", "广东联合产权交易中心"],
        ["shenzhen", "深圳联合产权交易所"],
      ],
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
          visible_families: [
            {
              family_id: "listing",
              family_label: "挂牌业务",
              businesses: [
                { business_id: "equity_transfer", business_label: "股权转让" },
                { business_id: "capital_increase", business_label: "增资扩股" },
              ],
            },
          ],
          sources: [
            { source_id: "shandong", source_label: "山东产权交易中心", record_families: ["listing"] },
            { source_id: "guangdong", source_label: "广东联合产权交易中心", record_families: ["listing"] },
            { source_id: "shenzhen", source_label: "深圳联合产权交易所", record_families: ["listing"] },
          ],
          surface_source_matrix: {
            listing: {
              equity_transfer: {
                records: ["shandong", "guangdong", "shenzhen"],
              },
              capital_increase: {
                records: ["shenzhen"],
              },
            },
          },
        };
      },
      getOverview() {
        return {
          defaults: {
            manual_import_input_dir: "/tmp/default-manual",
          },
        };
      },
      async runOneClick() {},
      async runManualImport() {},
    });

    modals.showManualImportModal();
    await Promise.resolve();
    await Promise.resolve();

    businessEl.value = "equity_transfer";
    await businessEl.trigger("change");
    assert.match(exchangeEl.innerHTML, /value="shandong"/);
    assert.match(exchangeEl.innerHTML, /value="guangdong"/);
    assert.match(exchangeEl.innerHTML, /value="shenzhen"/);

    exchangeEl.value = "guangdong";
    businessEl.value = "capital_increase";
    await businessEl.trigger("change");

    assert.doesNotMatch(exchangeEl.innerHTML, /value="shandong"/);
    assert.doesNotMatch(exchangeEl.innerHTML, /value="guangdong"/);
    assert.match(exchangeEl.innerHTML, /value="shenzhen"/);
    assert.equal(exchangeEl.value, "");
  } finally {
    documentHarness.restore();
  }
});
