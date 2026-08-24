import test from "node:test";
import assert from "node:assert/strict";

import { normalizeRecordsResource } from "../src/contracts/records.js";
import { createRecordsPanel, isRecordReprocessable } from "../src/panels/records.js";

function createFakeElement({ value = "" } = {}) {
  const listeners = new Map();
  return {
    innerHTML: "",
    value,
    disabled: false,
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    async trigger(type, event = {}) {
      const handler = listeners.get(type);
      if (typeof handler === "function") {
        return handler(event);
      }
      return undefined;
    },
  };
}

test("record reprocessing eligibility covers failed and intervention-required states only", () => {
  assert.equal(isRecordReprocessable({ state: "parse_failed" }), true);
  assert.equal(isRecordReprocessable({ state: "field_missing" }), true);
  assert.equal(isRecordReprocessable({ state: "pending_mapping" }), true);
  assert.equal(isRecordReprocessable({ state: "ready" }), false);
  assert.equal(isRecordReprocessable({ state: "skipped" }), false);
});

test("records panel renders business options from catalog instead of local project-type tables", () => {
  const panelEl = createFakeElement();
  const elementMap = new Map([
    ["#panel-records", panelEl],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", createFakeElement({ value: "equity_transfer" })],
    ["#filter-exchange", createFakeElement({ value: "all" })],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#btn-records-search", createFakeElement()],
    ["#btn-records-export", createFakeElement()],
    ["#records-thead", createFakeElement()],
    ["#records-tbody", createFakeElement()],
    ["#records-count", createFakeElement()],
    ["#records-page-info", createFakeElement()],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);

  const panel = createRecordsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    API: {
      async listRecords() {
        return { rows: [], summary: {}, display_columns: [] };
      },
    },
    LISTING_PROJECT_TYPE_OPTIONS: [["legacy", "遗留业务"]],
    RECORD_EXCHANGES: [["all", "全部交易所"], ["cbex", "北交所"]],
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    buildRecordsScope() {
      return {
        record_family: "listing",
        business_id: "equity_transfer",
        exchange: "cbex",
        state: "all",
        page: 1,
        page_size: 50,
      };
    },
    recordCellValue() {
      return "";
    },
    handleExport() {},
    getCatalog() {
      return {
        visible_families: [
          {
            family_id: "listing",
            businesses: [
              { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "one_click", "export"] },
              { business_id: "physical_asset", business_label: "实物资产", supported_surfaces: ["records"] },
            ],
          },
        ],
      };
    },
    getOverview() {
      return {};
    },
    setOverview() {},
    getRecords() {
      return { rows: [], summary: {}, display_columns: [] };
    },
    setRecords() {},
    getRecordFilters() {
      return { state: "all", business_id: "equity_transfer", exchange: "all", keyword: "", date_from: "", date_to: "" };
    },
    setRecordFilters() {},
    getRecordPage() {
      return 1;
    },
    setRecordPage() {},
  });

  panel.renderLayout();

  assert.match(panelEl.innerHTML, /股权转让/);
  assert.match(panelEl.innerHTML, /实物资产/);
  assert.doesNotMatch(panelEl.innerHTML, /遗留业务/);
  assert.match(panelEl.innerHTML, /filter-business/);
});

test("records panel stays browsable when actionable default-scope readiness is unavailable", () => {
  const panelEl = createFakeElement();
  const elementMap = new Map([
    ["#panel-records", panelEl],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", createFakeElement({ value: "all" })],
    ["#filter-exchange", createFakeElement({ value: "all" })],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#btn-records-search", createFakeElement()],
    ["#btn-records-export", createFakeElement()],
    ["#records-thead", createFakeElement()],
    ["#records-tbody", createFakeElement()],
    ["#records-count", createFakeElement()],
    ["#records-page-info", createFakeElement()],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);

  const panel = createRecordsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    API: {
      async listRecords() {
        return { rows: [], summary: {}, display_columns: [] };
      },
    },
    RECORD_EXCHANGES: [["all", "全部交易所"], ["cbex", "北交所"]],
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    buildRecordsScope() {
      return {
        record_family: "listing",
        business_id: "all",
        exchange: "all",
        state: "all",
        page: 1,
        page_size: 50,
      };
    },
    recordCellValue() {
      return "";
    },
    handleExport() {},
    getCatalog() {
      return {
        visible_families: [
          {
            family_id: "listing",
            businesses: [
              { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records"] },
            ],
          },
        ],
        sources: [{ source_id: "cbex", source_label: "北京产权交易所" }],
        surface_source_matrix: {
          listing: {
            equity_transfer: { records: ["cbex"] },
          },
        },
      };
    },
    getRecordsBrowseRuntime() {
      return {
        state: "ready",
        scope: {
          record_family: "listing",
          business_id: "all",
          business_label: "",
          exchange: "all",
        },
      };
    },
    getOverview() {
      return {};
    },
    setOverview() {},
    getRecords() {
      return { rows: [], summary: {}, display_columns: [] };
    },
    setRecords() {},
    getRecordFilters() {
      return { state: "all", business_id: "", exchange: "", keyword: "", date_from: "", date_to: "" };
    },
    setRecordFilters() {},
    getRecordPage() {
      return 1;
    },
    setRecordPage() {},
  });

  panel.renderLayout();

  assert.match(panelEl.innerHTML, /全部业务/);
  assert.match(panelEl.innerHTML, /filter-business/);
});

test("records panel blocks when catalog browse families are unavailable instead of inventing listing scope", () => {
  const panelEl = createFakeElement();
  const panel = createRecordsPanel({
    $(selector) {
      return selector === "#panel-records" ? panelEl : null;
    },
    $$() {
      return [];
    },
    API: {},
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    buildRecordsScope() {
      return {
        record_family: "listing",
        business_id: "all",
        exchange: "all",
        state: "all",
        page: 1,
        page_size: 50,
      };
    },
    recordCellValue() {
      return "";
    },
    handleExport() {},
    getCatalog() {
      return {};
    },
    getRecordsBrowseRuntime() {
      return { state: "ready" };
    },
    getOverview() {
      return {};
    },
    setOverview() {},
    getRecords() {
      return { rows: [], summary: {}, display_columns: [] };
    },
    setRecords() {},
    getRecordFilters() {
      return { state: "all", business_id: "", exchange: "", keyword: "", date_from: "", date_to: "" };
    },
    setRecordFilters() {},
    getRecordPage() {
      return 1;
    },
    setRecordPage() {},
  });

  panel.renderLayout();

  assert.match(panelEl.innerHTML, /记录目录不可用/);
  assert.doesNotMatch(panelEl.innerHTML, /value="listing"/);
  assert.doesNotMatch(panelEl.innerHTML, /filter-business/);
});

test("records panel default filters and stats include field-missing and failed states", () => {
  const panelEl = createFakeElement();
  const elementMap = new Map([
    ["#panel-records", panelEl],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", createFakeElement({ value: "all" })],
    ["#filter-exchange", createFakeElement({ value: "all" })],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#btn-records-search", createFakeElement()],
    ["#btn-records-export", createFakeElement()],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);

  const panel = createRecordsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    API: {
      async listRecords() {
        return { rows: [], summary: {}, display_columns: [] };
      },
    },
    RECORD_EXCHANGES: [["all", "全部交易所"], ["sse", "上交所"]],
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    buildRecordsScope() {
      return {
        record_family: "listing",
        business_id: "all",
        exchange: "all",
        state: "all",
        page: 1,
        page_size: 50,
      };
    },
    recordCellValue() {
      return "";
    },
    handleExport() {},
    getCatalog() {
      return {
        visible_families: [
          {
            family_id: "listing",
            businesses: [
              { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records"] },
            ],
          },
        ],
        sources: [{ source_id: "sse", source_label: "上海联合产权交易所" }],
        surface_source_matrix: {
          listing: {
            equity_transfer: { records: ["sse"] },
          },
        },
      };
    },
    getOverview() {
      return {};
    },
    setOverview() {},
    getRecords() {
      return { rows: [], summary: {}, display_columns: [] };
    },
    setRecords() {},
    getRecordFilters() {
      return { state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
    },
    setRecordFilters() {},
    getRecordPage() {
      return 1;
    },
    setRecordPage() {},
  });

  panel.renderLayout();

  assert.match(panelEl.innerHTML, /待人工复核/);
  assert.match(panelEl.innerHTML, /已跳过/);
  assert.match(panelEl.innerHTML, /字段缺失/);
  assert.match(panelEl.innerHTML, /解析失败/);
  assert.match(panelEl.innerHTML, /处理失败/);
  assert.match(panelEl.innerHTML, /value="field_missing"/);
  assert.match(panelEl.innerHTML, /value="parse_failed"/);
  assert.match(panelEl.innerHTML, /value="postprocess_failed"/);
});

test("records panel exposes field-missing acknowledgement as attention-only row action", async () => {
  const previousDocument = globalThis.document;
  globalThis.document = {
    querySelector() {
      return null;
    },
    getElementById() {
      return null;
    },
  };
  try {
  const panelEl = createFakeElement();
  const tbodyEl = createFakeElement();
  const ackButton = createFakeElement();
  ackButton.dataset = { recordId: "rec-field-missing" };
  const elementMap = new Map([
    ["#panel-records", panelEl],
    ["#record-family", createFakeElement({ value: "listing" })],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", createFakeElement({ value: "all" })],
    ["#filter-exchange", createFakeElement({ value: "all" })],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#btn-records-search", createFakeElement()],
    ["#btn-records-export", createFakeElement()],
    ["#records-thead", createFakeElement()],
    ["#records-tbody", tbodyEl],
    ["#records-count", createFakeElement()],
    ["#records-page-info", createFakeElement()],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);
  let acknowledgedRecordId = "";
  let reloadCount = 0;

  const panel = createRecordsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$(selector) {
      return selector === ".btn-field-missing-ack" ? [ackButton] : [];
    },
    API: {
      async listRecords() {
        reloadCount += 1;
        return { rows: [], summary: {}, display_columns: [] };
      },
      async acknowledgeRecordFieldMissing(recordId) {
        acknowledgedRecordId = recordId;
        return { record_id: recordId, state: "field_missing", exportable: false };
      },
    },
    RECORD_EXCHANGES: [["all", "全部交易所"]],
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    buildRecordsScope() {
      return {
        record_family: "listing",
        business_id: "all",
        exchange: "all",
        state: "all",
        page: 1,
        page_size: 50,
      };
    },
    recordCellValue(row, column) {
      return row.display_values?.[column] || "";
    },
    handleExport() {},
    getCatalog() {
      return {
        visible_families: [{ family_id: "listing", businesses: [] }],
      };
    },
    getOverview() {
      return {};
    },
    setOverview() {},
    getRecords() {
      return {
        rows: [
          {
            record_id: "rec-field-missing",
            state: "field_missing",
            status_label: "field_missing",
            status_detail: "导出字段缺失：类型",
            has_local_artifact: false,
            archive_path: "/missing/field.html",
            artifact_missing_reason: "artifact_path_unresolved",
            attention: { requires_attention: true, suppressed: false, reason: "field_missing" },
            field_missing_acknowledgement: { acknowledged: false },
            display_values: { 项目编号: "CODE-1" },
            updated_at: "",
          },
        ],
        summary: {
          filtered_state_counts: { field_missing: 1 },
          total_count: 1,
          visible_count: 1,
          page_count: 1,
        },
        display_columns: ["项目编号"],
      };
    },
    setRecords() {},
    getRecordFilters() {
      return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
    },
    setRecordFilters() {},
    getRecordPage() {
      return 1;
    },
    setRecordPage() {},
  });

  panel.renderLayout();
  panel.updateTable();
  await ackButton.trigger("click");

  assert.match(tbodyEl.innerHTML, /确认缺失提示/);
  assert.equal(acknowledgedRecordId, "rec-field-missing");
  assert.equal(reloadCount, 1);
  } finally {
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

function createRecordReprocessTestPanel({ onReprocess }) {
  const tbodyEl = createFakeElement();
  const reprocessButton = createFakeElement();
  reprocessButton.dataset = { recordId: "rec-reprocess" };
  const elements = new Map([
    ["#records-thead", createFakeElement()],
    ["#records-tbody", tbodyEl],
    ["#record-family", createFakeElement({ value: "listing" })],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", createFakeElement({ value: "all" })],
    ["#filter-exchange", createFakeElement({ value: "all" })],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#records-count", createFakeElement()],
    ["#records-page-info", createFakeElement()],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);
  let records = {
    rows: [{
      record_id: "rec-reprocess",
      state: "parse_failed",
      status_label: "parse_failed",
      status_detail: "解析未完成",
      display_values: { 项目编号: "REPROCESS-1" },
      updated_at: "",
    }],
    summary: {
      filtered_state_counts: { parse_failed: 1 },
      total_count: 1,
      visible_count: 1,
      page_count: 1,
    },
    display_columns: ["项目编号"],
  };
  let reloadCount = 0;
  const panel = createRecordsPanel({
    $: (selector) => elements.get(selector) || createFakeElement(),
    $$: (selector) => selector === ".btn-record-reprocess" ? [reprocessButton] : [],
    API: {
      async listRecords() {
        reloadCount += 1;
        return { rows: [], summary: {}, display_columns: [] };
      },
    },
    escapeHtml: (value) => String(value ?? ""),
    display: (value) => String(value ?? ""),
    formatJobTime: (value) => String(value ?? ""),
    num: (value) => Number.parseInt(value, 10) || 0,
    buildRecordsScope() {
      return { record_family: "listing", business_id: "all", exchange: "all", state: "all", page: 1, page_size: 50 };
    },
    recordCellValue(row, column) {
      return row.display_values?.[column] || "";
    },
    handleExport() {},
    onReprocess,
    getCatalog() {
      return { visible_families: [{ family_id: "listing", businesses: [] }] };
    },
    getOverview() {
      return {};
    },
    setOverview() {},
    getRecords() {
      return records;
    },
    setRecords(nextRecords) {
      records = nextRecords;
    },
    getRecordFilters() {
      return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
    },
    setRecordFilters() {},
    getRecordPage() {
      return 1;
    },
    setRecordPage() {},
  });
  return { panel, tbodyEl, reprocessButton, getReloadCount: () => reloadCount };
}

test("records panel reprocesses an eligible record and reloads on a successful action result", async () => {
  const previousDocument = globalThis.document;
  globalThis.document = { querySelector: () => null, getElementById: () => null };
  try {
    let receivedRecordId = "";
    const { panel, tbodyEl, reprocessButton, getReloadCount } = createRecordReprocessTestPanel({
      onReprocess: async (recordId) => {
        receivedRecordId = recordId;
        return { record_id: recordId, state: "ready", error_code: "", error_message: "" };
      },
    });

    panel.updateTable();
    assert.match(tbodyEl.innerHTML, /重新处理/);
    await reprocessButton.trigger("click");

    assert.equal(receivedRecordId, "rec-reprocess");
    assert.equal(getReloadCount(), 1);
  } finally {
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
  }
});

test("records panel keeps a reprocess business failure visible without reloading", async () => {
  const previousDocument = globalThis.document;
  const previousConsoleError = console.error;
  globalThis.document = { querySelector: () => null, getElementById: () => null };
  console.error = () => {};
  try {
    const { panel, tbodyEl, reprocessButton, getReloadCount } = createRecordReprocessTestPanel({
      onReprocess: async () => ({
        record_id: "rec-reprocess",
        state: "parse_failed",
        error_code: "source_missing",
        error_message: "source artifact is unavailable",
      }),
    });

    panel.updateTable();
    await reprocessButton.trigger("click");

    assert.equal(getReloadCount(), 0);
    assert.match(tbodyEl.innerHTML, /重新处理记录失败/);
    assert.match(tbodyEl.innerHTML, /source artifact is unavailable/);
  } finally {
    console.error = previousConsoleError;
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
  }
});

test("records panel renders safe status detail without legacy last error text", () => {
  const previousDocument = globalThis.document;
  globalThis.document = {
    querySelector() {
      return null;
    },
    getElementById() {
      return null;
    },
  };
  try {
    const panelEl = createFakeElement();
    const tbodyEl = createFakeElement();
    const elementMap = new Map([
      ["#panel-records", panelEl],
      ["#record-family", createFakeElement({ value: "listing" })],
      ["#filter-state", createFakeElement({ value: "all" })],
      ["#filter-business", createFakeElement({ value: "all" })],
      ["#filter-exchange", createFakeElement({ value: "all" })],
      ["#filter-keyword", createFakeElement({ value: "" })],
      ["#filter-date-from", createFakeElement({ value: "" })],
      ["#filter-date-to", createFakeElement({ value: "" })],
      ["#btn-records-search", createFakeElement()],
      ["#btn-records-export", createFakeElement()],
      ["#records-thead", createFakeElement()],
      ["#records-tbody", tbodyEl],
      ["#records-count", createFakeElement()],
      ["#records-page-info", createFakeElement()],
      ["#btn-records-prev", createFakeElement()],
      ["#btn-records-next", createFakeElement()],
    ]);

    const panel = createRecordsPanel({
      $(selector) {
        return elementMap.get(selector) || null;
      },
      $$() {
        return [];
      },
      API: {
        async listRecords() {
          return { rows: [], summary: {}, display_columns: [] };
        },
      },
      RECORD_EXCHANGES: [["all", "全部交易所"]],
      escapeHtml(value) {
        return String(value || "");
      },
      display(value) {
        return String(value || "");
      },
      formatJobTime(value) {
        return String(value || "");
      },
      num(value) {
        return Number.parseInt(value, 10) || 0;
      },
      buildRecordsScope() {
        return {
          record_family: "listing",
          business_id: "all",
          exchange: "all",
          state: "all",
          page: 1,
          page_size: 50,
        };
      },
      recordCellValue(row, column) {
        return row.display_values?.[column] || "";
      },
      handleExport() {},
      getCatalog() {
        return {
          visible_families: [{ family_id: "listing", businesses: [] }],
        };
      },
      getOverview() {
        return {};
      },
      setOverview() {},
      getRecords() {
        return {
          rows: [
            {
              record_id: "rec-parse-failed",
              state: "parse_failed",
              status_label: "parse_failed",
              status_detail: "解析失败，暂不能进入录入",
              last_error_message: "UNTRUSTED_EXTERNAL_TEXT",
              display_values: { 项目编号: "CODE-FAILED" },
              updated_at: "",
            },
          ],
          summary: {
            filtered_state_counts: { parse_failed: 1 },
            total_count: 1,
            visible_count: 1,
            page_count: 1,
          },
          display_columns: ["项目编号"],
        };
      },
      setRecords() {},
      getRecordFilters() {
        return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
      },
      setRecordFilters() {},
      getRecordPage() {
        return 1;
      },
      setRecordPage() {},
    });

    panel.renderLayout();
    panel.updateTable();

    assert.match(tbodyEl.innerHTML, /解析失败，暂不能进入录入/);
    assert.doesNotMatch(tbodyEl.innerHTML, /UNTRUSTED_EXTERNAL_TEXT/);
  } finally {
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

test("records load failure clears stale rows and renders a visible error", async () => {
  const previousDocument = globalThis.document;
  const elements = new Map([
    ["#records-thead", createFakeElement()],
    ["#records-tbody", createFakeElement()],
    ["#record-family", createFakeElement({ value: "listing" })],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", createFakeElement({ value: "all" })],
    ["#filter-exchange", createFakeElement({ value: "all" })],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#records-count", createFakeElement()],
    ["#records-page-info", createFakeElement()],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);
  globalThis.document = {
    querySelector() {
      return null;
    },
    getElementById() {
      return createFakeElement();
    },
  };
  const previousConsoleError = console.error;
  console.error = () => {};
  try {
    let currentRecords = { rows: [], summary: {}, display_columns: [] };
    let callCount = 0;
    const panel = createRecordsPanel({
      $: (selector) => elements.get(selector) || createFakeElement(),
      $$: () => [],
      API: {
        async listRecords() {
          callCount += 1;
          if (callCount === 1) {
            return {
              rows: [
                {
                  record_id: "rec-stale-after-failed-load",
                  state: "ready",
                  status_label: "ready",
                  status_detail: "",
                  canonical_ready: true,
                  evidence_status: "verified",
                  export_eligible: true,
                  evidence_verdict: {
                    status: "verified",
                    inspection_openable_path: "/managed/stale.html",
                  },
                  local_artifact_name: "stale.html",
                  display_values: { 项目编号: "STALE-CODE" },
                  updated_at: "",
                },
              ],
              summary: {
                filtered_state_counts: { ready: 1 },
                total_count: 1,
                visible_count: 1,
                page_count: 1,
              },
              display_columns: ["项目编号"],
            };
          }
          throw new Error("records backend unavailable");
        },
      },
      escapeHtml: (value) => String(value ?? ""),
      display: (value) => value || "—",
      formatJobTime: (value) => value,
      num: (value) => Number.parseInt(value, 10) || 0,
      buildRecordsScope() {
        return {
          record_family: "listing",
          business_id: "all",
          exchange: "all",
          state: "all",
          page: 1,
          page_size: 50,
        };
      },
      recordCellValue(row, column) {
        return row.display_values?.[column] || "";
      },
      getCatalog() {
        return { visible_families: [{ family_id: "listing", businesses: [] }] };
      },
      getOverview() {
        return {};
      },
      setOverview() {},
      getRecords() {
        return currentRecords;
      },
      setRecords(next) {
        currentRecords = next;
      },
      getRecordFilters() {
        return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
      },
      setRecordFilters() {},
      getRecordPage() {
        return 1;
      },
      setRecordPage() {},
    });

    await panel.load();
    assert.match(elements.get("#records-tbody").innerHTML, /STALE-CODE/);
    assert.match(elements.get("#records-count").textContent, /共 1 条/);

    await panel.load();

    assert.doesNotMatch(elements.get("#records-tbody").innerHTML, /STALE-CODE/);
    assert.match(elements.get("#records-tbody").innerHTML, /记录加载失败/);
    assert.match(elements.get("#records-count").textContent, /加载失败/);
    assert.equal(currentRecords.rows.length, 0);
  } finally {
    console.error = previousConsoleError;
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

test("field-missing acknowledgement failure renders a visible error without successful reload semantics", async () => {
  const previousDocument = globalThis.document;
  const tbodyEl = createFakeElement();
  const ackButton = createFakeElement();
  ackButton.dataset = { recordId: "rec-ack-fails" };
  const elements = new Map([
    ["#records-thead", createFakeElement()],
    ["#records-tbody", tbodyEl],
    ["#record-family", createFakeElement({ value: "listing" })],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", createFakeElement({ value: "all" })],
    ["#filter-exchange", createFakeElement({ value: "all" })],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#records-count", createFakeElement()],
    ["#records-page-info", createFakeElement()],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);
  globalThis.document = {
    querySelector() {
      return null;
    },
    getElementById(id) {
      return id === "record-action-error" ? elements.get("#record-action-error") || null : createFakeElement();
    },
  };
  const previousConsoleError = console.error;
  console.error = () => {};
  try {
    let acknowledgedRecordId = "";
    let reloadCount = 0;
    const panel = createRecordsPanel({
      $: (selector) => elements.get(selector) || createFakeElement(),
      $$: (selector) => selector === ".btn-field-missing-ack" ? [ackButton] : [],
      API: {
        async listRecords() {
          reloadCount += 1;
          return { rows: [], summary: {}, display_columns: [] };
        },
        async acknowledgeRecordFieldMissing(recordId) {
          acknowledgedRecordId = recordId;
          throw new Error("ack endpoint unavailable");
        },
      },
      escapeHtml: (value) => String(value ?? ""),
      display: (value) => value || "—",
      formatJobTime: (value) => value,
      num: (value) => Number.parseInt(value, 10) || 0,
      buildRecordsScope() {
        return {
          record_family: "listing",
          business_id: "all",
          exchange: "all",
          state: "all",
          page: 1,
          page_size: 50,
        };
      },
      recordCellValue(row, column) {
        return row.display_values?.[column] || "";
      },
      getCatalog() {
        return { visible_families: [{ family_id: "listing", businesses: [] }] };
      },
      getOverview() {
        return {};
      },
      setOverview() {},
      getRecords() {
        return {
          rows: [
            {
              record_id: "rec-ack-fails",
              state: "field_missing",
              status_label: "field_missing",
              status_detail: "导出字段缺失：类型",
              attention: { requires_attention: true, suppressed: false, reason: "field_missing" },
              field_missing_acknowledgement: { acknowledged: false },
              display_values: { 项目编号: "ACK-CODE" },
              updated_at: "",
            },
          ],
          summary: {
            filtered_state_counts: { field_missing: 1 },
            total_count: 1,
            visible_count: 1,
            page_count: 1,
          },
          display_columns: ["项目编号"],
        };
      },
      setRecords() {},
      getRecordFilters() {
        return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
      },
      setRecordFilters() {},
      getRecordPage() {
        return 1;
      },
      setRecordPage() {},
    });

    panel.updateTable();
    await ackButton.trigger("click");

    assert.equal(acknowledgedRecordId, "rec-ack-fails");
    assert.equal(reloadCount, 0);
    assert.match(tbodyEl.innerHTML, /确认缺失提示失败/);
    assert.match(tbodyEl.innerHTML, /ACK-CODE/);
  } finally {
    console.error = previousConsoleError;
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

test("record folder reveal failure renders a visible error", async () => {
  const previousDocument = globalThis.document;
  const tbodyEl = createFakeElement();
  const revealButton = createFakeElement();
  revealButton.dataset = { recordId: "rec-reveal-fails" };
  const elements = new Map([
    ["#records-thead", createFakeElement()],
    ["#records-tbody", tbodyEl],
    ["#record-family", createFakeElement({ value: "listing" })],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", createFakeElement({ value: "all" })],
    ["#filter-exchange", createFakeElement({ value: "all" })],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#records-count", createFakeElement()],
    ["#records-page-info", createFakeElement()],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);
  globalThis.document = {
    querySelector() {
      return null;
    },
    getElementById(id) {
      return id === "record-action-error" ? elements.get("#record-action-error") || null : createFakeElement();
    },
  };
  const previousConsoleError = console.error;
  console.error = () => {};
  try {
    let revealedRecordId = "";
    const panel = createRecordsPanel({
      $: (selector) => elements.get(selector) || createFakeElement(),
      $$: (selector) => selector === ".btn-record-folder" ? [revealButton] : [],
      API: {
        async revealRecordFolder(recordId) {
          revealedRecordId = recordId;
          throw new Error("filesystem reveal unavailable");
        },
      },
      escapeHtml: (value) => String(value ?? ""),
      display: (value) => value || "—",
      formatJobTime: (value) => value,
      num: (value) => Number.parseInt(value, 10) || 0,
      buildRecordsScope() {
        return {
          record_family: "listing",
          business_id: "all",
          exchange: "all",
          state: "all",
          page: 1,
          page_size: 50,
        };
      },
      recordCellValue(row, column) {
        return row.display_values?.[column] || "";
      },
      getCatalog() {
        return { visible_families: [{ family_id: "listing", businesses: [] }] };
      },
      getOverview() {
        return {};
      },
      setOverview() {},
      getRecords() {
        return {
          rows: [
            {
              record_id: "rec-reveal-fails",
              state: "ready",
              status_label: "ready",
              status_detail: "",
              canonical_ready: true,
              evidence_status: "verified",
              export_eligible: true,
              evidence_verdict: {
                status: "verified",
                inspection_openable_path: "/managed/reveal.html",
              },
              local_artifact_name: "reveal.html",
              display_values: { 项目编号: "REVEAL-CODE" },
              updated_at: "",
            },
          ],
          summary: {
            filtered_state_counts: { ready: 1 },
            total_count: 1,
            visible_count: 1,
            page_count: 1,
          },
          display_columns: ["项目编号"],
        };
      },
      setRecords() {},
      getRecordFilters() {
        return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
      },
      setRecordFilters() {},
      getRecordPage() {
        return 1;
      },
      setRecordPage() {},
    });

    panel.updateTable();
    await revealButton.trigger("click");

    assert.equal(revealedRecordId, "rec-reveal-fails");
    assert.match(tbodyEl.innerHTML, /打开记录文件失败/);
    assert.match(tbodyEl.innerHTML, /REVEAL-CODE/);
  } finally {
    console.error = previousConsoleError;
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

test("records panel renders visible data from sanitized DTO fields over contradictory legacy fields", () => {
  const previousDocument = globalThis.document;
  const tbodyEl = createFakeElement();
  const theadEl = createFakeElement();
  const controls = new Map([
    ["#records-thead", theadEl],
    ["#records-tbody", tbodyEl],
    ["#record-family", createFakeElement({ value: "listing" })],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", createFakeElement({ value: "all" })],
    ["#filter-exchange", createFakeElement({ value: "all" })],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#records-count", createFakeElement()],
    ["#records-page-info", createFakeElement()],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);
  const documentStub = {
    querySelector() {
      return null;
    },
    getElementById() {
      return createFakeElement();
    },
  };
  globalThis.document = documentStub;
  try {
    const records = normalizeRecordsResource({
      rows: [
        {
          record_id: "rec-dto-sentinel",
          project_code: "LEGACY-CODE-A73F",
          project_name: "LEGACY-NAME-A73F",
          listing_date: "1999-01-01",
          price: "999999.99",
          state: "ready",
          status_label: "ready",
          status_detail: "DTO-STATUS-A73F",
          last_error_message: "LEGACY-STATUS-A73F",
          archive_path: "/legacy/legacy-archive-a73f.html",
          source_file: "/legacy/legacy-source-a73f.html",
          artifact_status: "downloaded",
          has_local_artifact: true,
          local_artifact_name: "legacy-local-a73f.html",
          evidence_verdict: {
            status: "present_unverified",
            inspection_openable_path: "/managed/dto-evidence-a73f.html",
            reason_code: "identity_unresolved",
          },
          canonical_ready: true,
          evidence_status: "present_unverified",
          export_eligible: false,
          exportable: true,
          display_values: {
            项目编号: "DTO-CODE-A73F",
            项目名称: "DTO-NAME-A73F",
            挂牌开始日期: "2026-05-25",
            挂牌价格: "12345.67",
          },
          updated_at: "",
        },
      ],
      summary: {
        filtered_state_counts: { ready: 1 },
        total_count: 1,
        visible_count: 1,
        page: 1,
        page_size: 50,
        page_count: 1,
      },
      display_columns: ["项目编号", "项目名称", "挂牌开始日期", "挂牌价格"],
    });
    const panel = createRecordsPanel({
      $: (selector) => controls.get(selector) || createFakeElement(),
      $$: () => [],
      escapeHtml: (value) => String(value ?? ""),
      display: (value) => value || "—",
      formatJobTime: (value) => value,
      num: (value) => Number.parseInt(value, 10) || 0,
      buildRecordsScope() {
        return {
          record_family: "listing",
          business_id: "all",
          exchange: "all",
          state: "all",
          page: 1,
          page_size: 50,
        };
      },
      recordCellValue(row, column) {
        return row.display_values?.[column] || "";
      },
      getCatalog() {
        return { visible_families: [{ family_id: "listing", businesses: [] }] };
      },
      getOverview() {
        return {};
      },
      setOverview() {},
      getRecords() {
        return records;
      },
      setRecords() {},
      getRecordFilters() {
        return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
      },
      setRecordFilters() {},
      getRecordPage() {
        return 1;
      },
      setRecordPage() {},
    });

    panel.renderLayout();
    panel.updateTable();

    assert.match(tbodyEl.innerHTML, /DTO-CODE-A73F/);
    assert.match(tbodyEl.innerHTML, /DTO-NAME-A73F/);
    assert.match(tbodyEl.innerHTML, /2026-05-25/);
    assert.match(tbodyEl.innerHTML, /12345\.67/);
    assert.match(tbodyEl.innerHTML, /DTO-STATUS-A73F/);
    assert.match(tbodyEl.innerHTML, /未验证/);
    assert.match(tbodyEl.innerHTML, /受限/);
    assert.match(tbodyEl.innerHTML, /仅可检查证据/);
    assert.match(tbodyEl.innerHTML, /dto-evidence-a73f\.html/);
    assert.doesNotMatch(tbodyEl.innerHTML, /LEGACY-CODE-A73F/);
    assert.doesNotMatch(tbodyEl.innerHTML, /LEGACY-NAME-A73F/);
    assert.doesNotMatch(tbodyEl.innerHTML, /1999-01-01/);
    assert.doesNotMatch(tbodyEl.innerHTML, /999999\.99/);
    assert.doesNotMatch(tbodyEl.innerHTML, /LEGACY-STATUS-A73F/);
    assert.doesNotMatch(tbodyEl.innerHTML, /legacy-(archive|source|local)-a73f\.html/);
    assert.doesNotMatch(tbodyEl.innerHTML, /可导出/);
  } finally {
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

test("records panel renders resolvable artifact and broken association states distinctly", () => {
  const previousDocument = globalThis.document;
  const tbodyEl = createFakeElement();
  const controls = new Map([
    ["#records-thead", createFakeElement()],
    ["#records-tbody", tbodyEl],
    ["#record-family", createFakeElement()],
    ["#filter-state", createFakeElement()],
    ["#filter-business", createFakeElement()],
    ["#filter-exchange", createFakeElement()],
    ["#filter-keyword", createFakeElement()],
    ["#filter-date-from", createFakeElement()],
    ["#filter-date-to", createFakeElement()],
  ]);
  const documentStub = {
    querySelector() {
      return null;
    },
    getElementById() {
      return createFakeElement();
    },
  };
  globalThis.document = documentStub;
  try {
  const panel = createRecordsPanel({
    $: (selector) => controls.get(selector) || createFakeElement(),
    $$: () => [],
    document: documentStub,
    escapeHtml: (value) => String(value ?? ""),
    display: (value) => value || "—",
    formatJobTime: (value) => value,
    num: (value) => Number.parseInt(value, 10) || 0,
    buildRecordsScope() {
      return {
        record_family: "listing",
        business_id: "all",
        exchange: "all",
        state: "all",
        page: 1,
        page_size: 50,
      };
    },
    recordCellValue(row, column) {
      return row.display_values?.[column] || "";
    },
    getCatalog() {
      return { visible_families: [{ family_id: "listing", businesses: [] }] };
    },
    getOverview() {
      return {};
    },
    setOverview() {},
    getRecords() {
      return {
        rows: [
          {
            record_id: "rec-has-file",
            state: "ready",
            status_label: "ready",
            status_detail: "",
            has_local_artifact: true,
            local_artifact_name: "good.html",
            evidence_verdict: {
              status: "verified",
              inspection_openable_path: "/archive/good.html",
            },
            display_values: { 项目编号: "CODE-1" },
            updated_at: "",
          },
          {
            record_id: "rec-broken-file",
            state: "ready",
            status_label: "ready",
            status_detail: "",
            has_local_artifact: false,
            archive_path: "/archive/missing.html",
            artifact_missing_reason: "artifact_path_unresolved",
            display_values: { 项目编号: "CODE-2" },
            updated_at: "",
          },
        ],
        summary: {
          filtered_state_counts: { ready: 2 },
          total_count: 2,
          visible_count: 2,
          page_count: 1,
        },
        display_columns: ["项目编号"],
      };
    },
    setRecords() {},
    getRecordFilters() {
      return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
    },
    setRecordFilters() {},
    getRecordPage() {
      return 1;
    },
    setRecordPage() {},
  });

  panel.renderLayout();
  panel.updateTable();

  assert.match(tbodyEl.innerHTML, /定位网页文件/);
  assert.match(tbodyEl.innerHTML, /good\.html/);
  assert.match(tbodyEl.innerHTML, /文件路径不可访问/);
  } finally {
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

test("records panel does not infer normal open action from legacy artifact flags without evidence verdict", () => {
  const previousDocument = globalThis.document;
  const tbodyEl = createFakeElement();
  const controls = new Map([
    ["#records-thead", createFakeElement()],
    ["#records-tbody", tbodyEl],
    ["#record-family", createFakeElement()],
    ["#filter-state", createFakeElement()],
    ["#filter-business", createFakeElement()],
    ["#filter-exchange", createFakeElement()],
    ["#filter-keyword", createFakeElement()],
    ["#filter-date-from", createFakeElement()],
    ["#filter-date-to", createFakeElement()],
  ]);
  const documentStub = {
    querySelector() {
      return null;
    },
    getElementById() {
      return createFakeElement();
    },
  };
  globalThis.document = documentStub;
  try {
    const panel = createRecordsPanel({
      $: (selector) => controls.get(selector) || createFakeElement(),
      $$: () => [],
      document: documentStub,
      escapeHtml: (value) => String(value ?? ""),
      display: (value) => value || "—",
      formatJobTime: (value) => value,
      num: (value) => Number.parseInt(value, 10) || 0,
      buildRecordsScope() {
        return {
          record_family: "listing",
          business_id: "all",
          exchange: "all",
          state: "all",
          page: 1,
          page_size: 50,
        };
      },
      recordCellValue(row, column) {
        return row.display_values?.[column] || "";
      },
      getCatalog() {
        return { visible_families: [{ family_id: "listing", businesses: [] }] };
      },
      getOverview() {
        return {};
      },
      setOverview() {},
      getRecords() {
        return {
          rows: [
            {
              record_id: "rec-legacy-file-only",
              state: "ready",
              status_label: "ready",
              status_detail: "",
              has_local_artifact: true,
              local_artifact_name: "legacy-only.html",
              archive_path: "/archive/legacy-only.html",
              display_values: { 项目编号: "CODE-LEGACY" },
              updated_at: "",
            },
          ],
          summary: {
            filtered_state_counts: { ready: 1 },
            total_count: 1,
            visible_count: 1,
            page_count: 1,
          },
          display_columns: ["项目编号"],
        };
      },
      setRecords() {},
      getRecordFilters() {
        return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
      },
      setRecordFilters() {},
      getRecordPage() {
        return 1;
      },
      setRecordPage() {},
    });

    panel.renderLayout();
    panel.updateTable();

    assert.doesNotMatch(tbodyEl.innerHTML, /btn-record-folder/);
    assert.doesNotMatch(tbodyEl.innerHTML, /定位网页文件/);
    assert.match(tbodyEl.innerHTML, /文件未定位/);
  } finally {
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

test("records panel gates normal open buttons on evidence verdict status", () => {
  const previousDocument = globalThis.document;
  const tbodyEl = createFakeElement();
  const controls = new Map([
    ["#records-thead", createFakeElement()],
    ["#records-tbody", tbodyEl],
    ["#record-family", createFakeElement()],
    ["#filter-state", createFakeElement()],
    ["#filter-business", createFakeElement()],
    ["#filter-exchange", createFakeElement()],
    ["#filter-keyword", createFakeElement()],
    ["#filter-date-from", createFakeElement()],
    ["#filter-date-to", createFakeElement()],
  ]);
  const documentStub = {
    querySelector() {
      return null;
    },
    getElementById() {
      return createFakeElement();
    },
  };
  globalThis.document = documentStub;
  try {
    const panel = createRecordsPanel({
      $: (selector) => controls.get(selector) || createFakeElement(),
      $$: () => [],
      document: documentStub,
      escapeHtml: (value) => String(value ?? ""),
      display: (value) => value || "—",
      formatJobTime: (value) => value,
      num: (value) => Number.parseInt(value, 10) || 0,
      buildRecordsScope() {
        return {
          record_family: "listing",
          business_id: "all",
          exchange: "all",
          state: "all",
          page: 1,
          page_size: 50,
        };
      },
      recordCellValue(row, column) {
        return row.display_values?.[column] || "";
      },
      getCatalog() {
        return { visible_families: [{ family_id: "listing", businesses: [] }] };
      },
      getOverview() {
        return {};
      },
      setOverview() {},
      getRecords() {
        return {
          rows: [
            {
              record_id: "rec-verified",
              state: "ready",
              status_label: "ready",
              status_detail: "",
              has_local_artifact: true,
              local_artifact_name: "verified.html",
              evidence_verdict: {
                status: "verified",
                inspection_openable_path: "/tmp/verified.html",
                reason_code: "identity_verified_artifact_present",
              },
              display_values: { 项目编号: "CODE-1" },
              updated_at: "",
            },
            {
              record_id: "rec-stale",
              state: "ready",
              status_label: "ready",
              status_detail: "",
              has_local_artifact: true,
              local_artifact_name: "stale-provenance.html",
              archive_path: "/missing/archive.html",
              evidence_verdict: {
                status: "stale_reference",
                inspection_openable_path: "/tmp/stale-provenance.html",
                reason_code: "authoritative_artifact_missing",
              },
              display_values: { 项目编号: "CODE-2" },
              updated_at: "",
            },
            {
              record_id: "rec-undeclared",
              state: "ready",
              status_label: "ready",
              status_detail: "",
              has_local_artifact: true,
              local_artifact_name: "managed-provenance.html",
              evidence_verdict: {
                status: "undeclared",
                inspection_openable_path: "/tmp/managed-provenance.html",
                reason_code: "artifact_path_undeclared",
              },
              display_values: { 项目编号: "CODE-3" },
              updated_at: "",
            },
          ],
          summary: {
            filtered_state_counts: { ready: 3 },
            total_count: 3,
            visible_count: 3,
            page_count: 1,
          },
          display_columns: ["项目编号"],
        };
      },
      setRecords() {},
      getRecordFilters() {
        return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
      },
      setRecordFilters() {},
      getRecordPage() {
        return 1;
      },
      setRecordPage() {},
    });

    panel.renderLayout();
    panel.updateTable();

    assert.equal((tbodyEl.innerHTML.match(/btn-record-folder/g) || []).length, 1);
    assert.match(tbodyEl.innerHTML, /verified\.html/);
    assert.doesNotMatch(tbodyEl.innerHTML, /data-record-id="rec-stale"/);
    assert.doesNotMatch(tbodyEl.innerHTML, /data-record-id="rec-undeclared"/);
    assert.match(tbodyEl.innerHTML, /仅可检查证据/);
    assert.match(tbodyEl.innerHTML, /未记录来源文件/);
  } finally {
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

test("records panel requires shared official page evidence metadata before normal open action", () => {
  const previousDocument = globalThis.document;
  const tbodyEl = createFakeElement();
  const controls = new Map([
    ["#records-thead", createFakeElement()],
    ["#records-tbody", tbodyEl],
    ["#record-family", createFakeElement()],
    ["#filter-state", createFakeElement()],
    ["#filter-business", createFakeElement()],
    ["#filter-exchange", createFakeElement()],
    ["#filter-keyword", createFakeElement()],
    ["#filter-date-from", createFakeElement()],
    ["#filter-date-to", createFakeElement()],
  ]);
  const documentStub = {
    querySelector() {
      return null;
    },
    getElementById() {
      return createFakeElement();
    },
  };
  globalThis.document = documentStub;
  try {
    const panel = createRecordsPanel({
      $: (selector) => controls.get(selector) || createFakeElement(),
      $$: () => [],
      document: documentStub,
      escapeHtml: (value) => String(value ?? ""),
      display: (value) => value || "—",
      formatJobTime: (value) => value,
      num: (value) => Number.parseInt(value, 10) || 0,
      buildRecordsScope() {
        return {
          record_family: "listing",
          business_id: "all",
          exchange: "all",
          state: "all",
          page: 1,
          page_size: 50,
        };
      },
      recordCellValue(row, column) {
        return row.display_values?.[column] || "";
      },
      getCatalog() {
        return { visible_families: [{ family_id: "listing", businesses: [] }] };
      },
      getOverview() {
        return {};
      },
      setOverview() {},
      getRecords() {
        return {
          rows: [
            {
              record_id: "rec-shared-valid",
              state: "ready",
              status_label: "ready",
              evidence_verdict: {
                status: "shared_official_page",
                inspection_openable_path: "/managed/shared-valid.html",
                safe_evidence: { page_kind: "shared_official_page" },
              },
              local_artifact_name: "shared-valid.html",
              display_values: { 项目编号: "CODE-1" },
              updated_at: "",
            },
            {
              record_id: "rec-shared-metadata-missing",
              state: "ready",
              status_label: "ready",
              evidence_verdict: {
                status: "shared_official_page",
                inspection_openable_path: "/managed/shared-missing.html",
                safe_evidence: {},
              },
              local_artifact_name: "shared-missing.html",
              display_values: { 项目编号: "CODE-2" },
              updated_at: "",
            },
          ],
          summary: {
            filtered_state_counts: { ready: 2 },
            total_count: 2,
            visible_count: 2,
            page_count: 1,
          },
          display_columns: ["项目编号"],
        };
      },
      setRecords() {},
      getRecordFilters() {
        return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
      },
      setRecordFilters() {},
      getRecordPage() {
        return 1;
      },
      setRecordPage() {},
    });

    panel.renderLayout();
    panel.updateTable();

    assert.equal((tbodyEl.innerHTML.match(/btn-record-folder/g) || []).length, 1);
    assert.match(tbodyEl.innerHTML, /data-record-id="rec-shared-valid"/);
    assert.doesNotMatch(tbodyEl.innerHTML, /data-record-id="rec-shared-metadata-missing"/);
  } finally {
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

test("records panel renders unsafe openable evidence as inspection-only without normal reveal actions", () => {
  const previousDocument = globalThis.document;
  const tbodyEl = createFakeElement();
  const theadEl = createFakeElement();
  const controls = new Map([
    ["#records-thead", theadEl],
    ["#records-tbody", tbodyEl],
    ["#record-family", createFakeElement()],
    ["#filter-state", createFakeElement()],
    ["#filter-business", createFakeElement()],
    ["#filter-exchange", createFakeElement()],
    ["#filter-keyword", createFakeElement()],
    ["#filter-date-from", createFakeElement()],
    ["#filter-date-to", createFakeElement()],
  ]);
  const documentStub = {
    querySelector() {
      return null;
    },
    getElementById() {
      return createFakeElement();
    },
  };
  globalThis.document = documentStub;
  try {
    const panel = createRecordsPanel({
      $: (selector) => controls.get(selector) || createFakeElement(),
      $$: () => [],
      document: documentStub,
      escapeHtml: (value) => String(value ?? ""),
      display: (value) => value || "—",
      formatJobTime: (value) => value,
      num: (value) => Number.parseInt(value, 10) || 0,
      buildRecordsScope() {
        return {
          record_family: "listing",
          business_id: "all",
          exchange: "all",
          state: "all",
          page: 1,
          page_size: 50,
        };
      },
      recordCellValue(row, column) {
        return row.display_values?.[column] || "";
      },
      getCatalog() {
        return { visible_families: [{ family_id: "listing", businesses: [] }] };
      },
      getOverview() {
        return {};
      },
      setOverview() {},
      getRecords() {
        return {
          rows: [
            {
              record_id: "rec-verified",
              state: "ready",
              status_label: "ready",
              canonical_ready: true,
              evidence_status: "verified",
              export_eligible: true,
              has_local_artifact: true,
              local_artifact_name: "verified.html",
              evidence_verdict: {
                status: "verified",
                inspection_openable_path: "/managed/verified.html",
              },
              display_values: { 项目编号: "CODE-1" },
              updated_at: "",
            },
            {
              record_id: "rec-present-unverified",
              state: "ready",
              status_label: "ready",
              canonical_ready: true,
              evidence_status: "present_unverified",
              export_eligible: false,
              has_local_artifact: true,
              local_artifact_name: "legacy-path-only.html",
              archive_path: "/legacy/path-only.html",
              evidence_verdict: {
                status: "present_unverified",
                inspection_openable_path: "/legacy/path-only.html",
              },
              display_values: { 项目编号: "CODE-2" },
              updated_at: "",
            },
            {
              record_id: "rec-stale-reference",
              state: "ready",
              status_label: "ready",
              canonical_ready: true,
              evidence_status: "stale_reference",
              export_eligible: false,
              has_local_artifact: true,
              local_artifact_name: "stale-provenance.html",
              source_file: "/managed/stale-provenance.html",
              archive_path: "/missing/archive.html",
              evidence_verdict: {
                status: "stale_reference",
                inspection_openable_path: "/managed/stale-provenance.html",
                reason_code: "authoritative_artifact_missing",
              },
              display_values: { 项目编号: "CODE-3" },
              updated_at: "",
            },
          ],
          summary: {
            filtered_state_counts: { ready: 3 },
            total_count: 3,
            visible_count: 3,
            page_count: 1,
          },
          display_columns: ["项目编号"],
        };
      },
      setRecords() {},
      getRecordFilters() {
        return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
      },
      setRecordFilters() {},
      getRecordPage() {
        return 1;
      },
      setRecordPage() {},
    });

    panel.renderLayout();
    panel.updateTable();

    assert.equal((tbodyEl.innerHTML.match(/btn-record-folder/g) || []).length, 1);
    assert.match(tbodyEl.innerHTML, /data-record-id="rec-verified"/);
    assert.doesNotMatch(tbodyEl.innerHTML, /data-record-id="rec-present-unverified"/);
    assert.doesNotMatch(tbodyEl.innerHTML, /data-record-id="rec-stale-reference"/);
    assert.equal((tbodyEl.innerHTML.match(/仅可检查证据/g) || []).length, 2);
    assert.match(tbodyEl.innerHTML, /legacy-path-only\.html/);
    assert.match(tbodyEl.innerHTML, /stale-provenance\.html/);
  } finally {
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

test("records panel renders canonical readiness, evidence status, and export eligibility independently", () => {
  const previousDocument = globalThis.document;
  const tbodyEl = createFakeElement();
  const theadEl = createFakeElement();
  const controls = new Map([
    ["#records-thead", theadEl],
    ["#records-tbody", tbodyEl],
    ["#record-family", createFakeElement()],
    ["#filter-state", createFakeElement()],
    ["#filter-business", createFakeElement()],
    ["#filter-exchange", createFakeElement()],
    ["#filter-keyword", createFakeElement()],
    ["#filter-date-from", createFakeElement()],
    ["#filter-date-to", createFakeElement()],
  ]);
  const documentStub = {
    querySelector() {
      return null;
    },
    getElementById() {
      return createFakeElement();
    },
  };
  globalThis.document = documentStub;
  try {
    const panel = createRecordsPanel({
      $: (selector) => controls.get(selector) || createFakeElement(),
      $$: () => [],
      document: documentStub,
      escapeHtml: (value) => String(value ?? ""),
      display: (value) => value || "—",
      formatJobTime: (value) => value,
      num: (value) => Number.parseInt(value, 10) || 0,
      buildRecordsScope() {
        return {
          record_family: "listing",
          business_id: "all",
          exchange: "all",
          state: "all",
          page: 1,
          page_size: 50,
        };
      },
      recordCellValue(row, column) {
        return row.display_values?.[column] || "";
      },
      getCatalog() {
        return { visible_families: [{ family_id: "listing", businesses: [] }] };
      },
      getOverview() {
        return {};
      },
      setOverview() {},
      getRecords() {
        return {
          rows: [
            {
              record_id: "rec-eligible",
              state: "ready",
              status_label: "ready",
              status_detail: "",
              canonical_ready: true,
              evidence_status: "verified",
              export_eligible: true,
              has_local_artifact: true,
              local_artifact_name: "eligible.html",
              evidence_verdict: {
                status: "verified",
                inspection_openable_path: "/managed/eligible.html",
              },
              display_values: { 项目编号: "CODE-1" },
              updated_at: "",
            },
            {
              record_id: "rec-path-only",
              state: "ready",
              status_label: "ready",
              status_detail: "",
              canonical_ready: true,
              evidence_status: "present_unverified",
              export_eligible: false,
              exportable: true,
              has_local_artifact: true,
              local_artifact_name: "path-only.html",
              archive_path: "/legacy/path-only.html",
              source_file: "/legacy/path-only.html",
              evidence_verdict: {
                status: "present_unverified",
                inspection_openable_path: "/legacy/path-only.html",
              },
              display_values: { 项目编号: "CODE-2" },
              updated_at: "",
            },
            {
              record_id: "rec-field-missing",
              state: "field_missing",
              status_label: "field_missing",
              status_detail: "",
              canonical_ready: false,
              evidence_status: "verified",
              export_eligible: false,
              has_local_artifact: true,
              local_artifact_name: "field-missing.html",
              evidence_verdict: {
                status: "verified",
                inspection_openable_path: "/managed/field-missing.html",
              },
              attention: { requires_attention: false },
              field_missing_acknowledgement: { acknowledged: true },
              display_values: { 项目编号: "CODE-3" },
              updated_at: "",
            },
          ],
          summary: {
            filtered_state_counts: { ready: 2, field_missing: 1 },
            total_count: 3,
            visible_count: 3,
            page_count: 1,
          },
          display_columns: ["项目编号"],
        };
      },
      setRecords() {},
      getRecordFilters() {
        return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
      },
      setRecordFilters() {},
      getRecordPage() {
        return 1;
      },
      setRecordPage() {},
    });

    panel.renderLayout();
    panel.updateTable();

    assert.match(theadEl.innerHTML, /规范就绪/);
    assert.match(theadEl.innerHTML, /证据状态/);
    assert.match(theadEl.innerHTML, /导出资格/);
    assert.equal((tbodyEl.innerHTML.match(/可导出/g) || []).length, 1);
    assert.match(tbodyEl.innerHTML, /未验证/);
    assert.match(tbodyEl.innerHTML, /未就绪/);
    assert.doesNotMatch(tbodyEl.innerHTML, /path-only\.html[\s\S]*可导出/);
  } finally {
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

test("records panel renders unsafe evidence as inspection-only with readable labels", () => {
  const previousDocument = globalThis.document;
  const tbodyEl = createFakeElement();
  const theadEl = createFakeElement();
  const controls = new Map([
    ["#records-thead", theadEl],
    ["#records-tbody", tbodyEl],
    ["#record-family", createFakeElement()],
    ["#filter-state", createFakeElement()],
    ["#filter-business", createFakeElement()],
    ["#filter-exchange", createFakeElement()],
    ["#filter-keyword", createFakeElement()],
    ["#filter-date-from", createFakeElement()],
    ["#filter-date-to", createFakeElement()],
  ]);
  const documentStub = {
    querySelector() {
      return null;
    },
    getElementById() {
      return createFakeElement();
    },
  };
  globalThis.document = documentStub;
  try {
    const panel = createRecordsPanel({
      $: (selector) => controls.get(selector) || createFakeElement(),
      $$: () => [],
      document: documentStub,
      escapeHtml: (value) => String(value ?? ""),
      display: (value) => value || "—",
      formatJobTime: (value) => value,
      num: (value) => Number.parseInt(value, 10) || 0,
      buildRecordsScope() {
        return {
          record_family: "listing",
          business_id: "all",
          exchange: "all",
          state: "all",
          page: 1,
          page_size: 50,
        };
      },
      recordCellValue(row, column) {
        return row.display_values?.[column] || "";
      },
      getCatalog() {
        return { visible_families: [{ family_id: "listing", businesses: [] }] };
      },
      getOverview() {
        return {};
      },
      setOverview() {},
      getRecords() {
        return {
          rows: [
            {
              record_id: "rec-invalid-shell",
              state: "ready",
              status_label: "ready",
              status_detail: "",
              canonical_ready: true,
              evidence_status: "invalid_shell",
              export_eligible: false,
              has_local_artifact: true,
              local_artifact_name: "shell.html",
              archive_path: "/managed/shell.html",
              evidence_verdict: {
                status: "invalid_shell",
                inspection_openable_path: "/managed/shell.html",
                reason_code: "sse_deal_notice_shell",
              },
              display_values: { 项目编号: "CODE-1" },
              updated_at: "",
            },
            {
              record_id: "rec-identity-mismatch",
              state: "ready",
              status_label: "ready",
              status_detail: "",
              canonical_ready: true,
              evidence_status: "identity_mismatch",
              export_eligible: false,
              has_local_artifact: true,
              local_artifact_name: "wrong-record.html",
              source_file: "/managed/wrong-record.html",
              evidence_verdict: {
                status: "identity_mismatch",
                inspection_openable_path: "/managed/wrong-record.html",
                reason_code: "artifact_identity_mismatch",
              },
              display_values: { 项目编号: "CODE-2" },
              updated_at: "",
            },
          ],
          summary: {
            filtered_state_counts: { ready: 2 },
            total_count: 2,
            visible_count: 2,
            page_count: 1,
          },
          display_columns: ["项目编号"],
        };
      },
      setRecords() {},
      getRecordFilters() {
        return { record_family: "listing", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
      },
      setRecordFilters() {},
      getRecordPage() {
        return 1;
      },
      setRecordPage() {},
    });

    panel.renderLayout();
    panel.updateTable();

    assert.match(tbodyEl.innerHTML, /无效壳页面/);
    assert.match(tbodyEl.innerHTML, /身份不匹配/);
    assert.doesNotMatch(tbodyEl.innerHTML, /invalid_shell/);
    assert.doesNotMatch(tbodyEl.innerHTML, /identity_mismatch/);
    assert.equal((tbodyEl.innerHTML.match(/仅可检查证据/g) || []).length, 2);
    assert.doesNotMatch(tbodyEl.innerHTML, /文件未定位/);
    assert.doesNotMatch(tbodyEl.innerHTML, /定位网页文件/);
    assert.doesNotMatch(tbodyEl.innerHTML, /btn-record-folder/);
  } finally {
    if (previousDocument === undefined) {
      delete globalThis.document;
    } else {
      globalThis.document = previousDocument;
    }
  }
});

test("records panel derives exchange options from catalog records surface and falls back invalid filter exchange", () => {
  const panelEl = createFakeElement();
  const exchangeEl = createFakeElement({ value: "tpre" });
  const filterState = {
    record_family: "deal",
    state: "all",
    business_id: "deal_physical_asset",
    exchange: "tpre",
    keyword: "",
    date_from: "",
    date_to: "",
  };
  const updates = [];
  const elementMap = new Map([
    ["#panel-records", panelEl],
    ["#record-family", createFakeElement({ value: "deal" })],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", createFakeElement({ value: "deal_physical_asset" })],
    ["#filter-exchange", exchangeEl],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#btn-records-search", createFakeElement()],
    ["#btn-records-export", createFakeElement()],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);

  const panel = createRecordsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    API: {
      async listRecords() {
        return { rows: [], summary: {}, display_columns: [] };
      },
    },
    RECORD_EXCHANGES: [["all", "全部交易所"], ["sse", "上交所"], ["cbex", "北交所"], ["tpre", "天交所"], ["cquae", "重交所"]],
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    buildRecordsScope() {
      return {
        record_family: "deal",
        business_id: "deal_physical_asset",
        exchange: "tpre",
        state: "all",
        page: 1,
        page_size: 50,
      };
    },
    recordCellValue() {
      return "";
    },
    handleExport() {},
    getCatalog() {
      return {
        visible_families: [
          {
            family_id: "deal",
            businesses: [
              { business_id: "deal_equity_transfer", business_label: "股权转让成交", supported_surfaces: ["records", "export"] },
              { business_id: "deal_physical_asset", business_label: "实物资产成交", supported_surfaces: ["records", "export"] },
            ],
          },
        ],
        sources: [
          { source_id: "cbex", source_label: "北京产权交易所" },
          { source_id: "sse", source_label: "上海联合产权交易所" },
        ],
        surface_source_matrix: {
          deal: {
            deal_equity_transfer: { records: ["cbex", "sse", "tpre", "cquae"] },
            deal_physical_asset: { records: ["cbex", "sse"] },
          },
        },
      };
    },
    getOverview() {
      return {};
    },
    setOverview() {},
    getRecords() {
      return { rows: [], summary: {}, display_columns: [] };
    },
    setRecords() {},
    getRecordFilters() {
      return { ...filterState };
    },
    setRecordFilters(next) {
      updates.push(next);
      Object.assign(filterState, next);
    },
    getRecordPage() {
      return 1;
    },
    setRecordPage() {},
  });

  panel.renderLayout();

  assert.match(panelEl.innerHTML, /北京产权交易所/);
  assert.match(panelEl.innerHTML, /上海联合产权交易所/);
  assert.doesNotMatch(panelEl.innerHTML, /天交所/);
  assert.doesNotMatch(panelEl.innerHTML, /重交所/);
  assert.equal(filterState.exchange, "all");
  assert.ok(updates.some((update) => update.exchange === "all"));
});

test("records panel uses union exchange options for all-business browsing while keeping single-business restrictions", () => {
  const panelEl = createFakeElement();
  const filterState = {
    record_family: "deal",
    state: "all",
    business_id: "all",
    exchange: "all",
    keyword: "",
    date_from: "",
    date_to: "",
  };
  const businessEl = createFakeElement({ value: "all" });
  const exchangeEl = createFakeElement({ value: "all" });
  const elementMap = new Map([
    ["#panel-records", panelEl],
    ["#record-family", createFakeElement({ value: "deal" })],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", businessEl],
    ["#filter-exchange", exchangeEl],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#btn-records-search", createFakeElement()],
    ["#btn-records-export", createFakeElement()],
    ["#records-thead", createFakeElement()],
    ["#records-tbody", createFakeElement()],
    ["#records-count", createFakeElement()],
    ["#records-page-info", createFakeElement()],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);

  const panel = createRecordsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    API: {
      async listRecords() {
        return { rows: [], summary: {}, display_columns: [] };
      },
    },
    RECORD_EXCHANGES: [["all", "全部交易所"], ["sse", "上交所"], ["cbex", "北交所"], ["tpre", "天交所"], ["cquae", "重交所"]],
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    buildRecordsScope() {
      return {
        record_family: "deal",
        business_id: "all",
        exchange: "all",
        state: "all",
        page: 1,
        page_size: 50,
      };
    },
    recordCellValue() {
      return "";
    },
    handleExport() {},
    getCatalog() {
      return {
        visible_families: [
          {
            family_id: "deal",
            businesses: [
              { business_id: "deal_equity_transfer", business_label: "股权转让成交", supported_surfaces: ["records", "export"] },
              { business_id: "deal_physical_asset", business_label: "实物资产成交", supported_surfaces: ["records", "export"] },
            ],
          },
        ],
        sources: [
          { source_id: "cbex", source_label: "北京产权交易所" },
          { source_id: "sse", source_label: "上海联合产权交易所" },
          { source_id: "tpre", source_label: "天津产权交易中心" },
          { source_id: "cquae", source_label: "重庆联交所" },
        ],
        support_matrix: {
          deal: {
            deal_equity_transfer: { records: true, export: true },
            deal_physical_asset: { records: true, export: true },
          },
        },
        surface_source_matrix: {
          deal: {
            deal_equity_transfer: { records: ["cbex", "sse", "tpre", "cquae"] },
            deal_physical_asset: { records: ["cbex", "sse"] },
          },
        },
      };
    },
    getOverview() {
      return {};
    },
    setOverview() {},
    getRecords() {
      return { rows: [], summary: {}, display_columns: [] };
    },
    setRecords() {},
    getRecordFilters() {
      return { ...filterState };
    },
    setRecordFilters(next) {
      Object.assign(filterState, next);
    },
    getRecordPage() {
      return 1;
    },
    setRecordPage() {},
  });

  panel.renderLayout();

  assert.match(panelEl.innerHTML, /北京产权交易所/);
  assert.match(panelEl.innerHTML, /上海联合产权交易所/);
  assert.match(panelEl.innerHTML, /天津产权交易中心/);
  assert.match(panelEl.innerHTML, /重庆联交所/);

  filterState.business_id = "deal_physical_asset";
  panel.renderLayout();

  assert.match(panelEl.innerHTML, /北京产权交易所/);
  assert.match(panelEl.innerHTML, /上海联合产权交易所/);
  assert.doesNotMatch(panelEl.innerHTML, /天津产权交易中心/);
  assert.doesNotMatch(panelEl.innerHTML, /重庆联交所/);
});

test("records export blocks invalid scope and does not call export handler", async () => {
  const panelEl = createFakeElement();
  const exportButton = createFakeElement();
  let exported = false;
  let receivedError = null;
  const elementMap = new Map([
    ["#panel-records", panelEl],
    ["#record-family", createFakeElement({ value: "deal" })],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", createFakeElement({ value: "deal_physical_asset" })],
    ["#filter-exchange", createFakeElement({ value: "tpre" })],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#btn-records-search", createFakeElement()],
    ["#btn-records-export", exportButton],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);

  const panel = createRecordsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    API: {},
    RECORD_EXCHANGES: [["all", "全部交易所"], ["sse", "上交所"], ["cbex", "北交所"], ["tpre", "天交所"]],
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    buildRecordsScope() {
      return {
        record_family: "deal",
        state: "all",
        business_id: "deal_physical_asset",
        exchange: "tpre",
        page: 1,
        page_size: 50,
      };
    },
    recordCellValue() {
      return "";
    },
    handleExport() {
      if (arguments[0]) {
        receivedError = arguments[0];
        return;
      }
      exported = true;
    },
    getCatalog() {
      return {
        visible_families: [
          {
            family_id: "deal",
            businesses: [
              { business_id: "deal_physical_asset", business_label: "实物资产成交", supported_surfaces: ["records", "export"] },
            ],
          },
        ],
        surface_source_matrix: {
          deal: {
            deal_physical_asset: {
              records: ["cbex", "sse"],
              export: ["cbex", "sse"],
            },
          },
        },
      };
    },
    getOverview() {
      return {};
    },
    setOverview() {},
    getRecords() {
      return { rows: [], summary: {}, display_columns: [] };
    },
    setRecords() {},
    getRecordFilters() {
      return {
        record_family: "deal",
        state: "all",
        business_id: "deal_physical_asset",
        exchange: "tpre",
        keyword: "",
        date_from: "",
        date_to: "",
      };
    },
    setRecordFilters() {},
    getRecordPage() {
      return 1;
    },
    setRecordPage() {},
  });

  panel.renderLayout();

  await exportButton.trigger("click");
  assert.equal(exported, false);
  assert.match(String(receivedError?.message || ""), /当前筛选范围不支持导出/);
});

test("records panel exchange filter hides unsupported listing capital-increase sources", () => {
  const panelEl = createFakeElement();
  const exchangeEl = createFakeElement({ value: "guangdong" });
  const filterState = {
    record_family: "listing",
    state: "all",
    business_id: "capital_increase",
    exchange: "guangdong",
    keyword: "",
    date_from: "",
    date_to: "",
  };
  const updates = [];
  const elementMap = new Map([
    ["#panel-records", panelEl],
    ["#record-family", createFakeElement({ value: "listing" })],
    ["#filter-state", createFakeElement({ value: "all" })],
    ["#filter-business", createFakeElement({ value: "capital_increase" })],
    ["#filter-exchange", exchangeEl],
    ["#filter-keyword", createFakeElement({ value: "" })],
    ["#filter-date-from", createFakeElement({ value: "" })],
    ["#filter-date-to", createFakeElement({ value: "" })],
    ["#btn-records-search", createFakeElement()],
    ["#btn-records-export", createFakeElement()],
    ["#records-thead", createFakeElement()],
    ["#records-tbody", createFakeElement()],
    ["#records-count", createFakeElement()],
    ["#records-page-info", createFakeElement()],
    ["#btn-records-prev", createFakeElement()],
    ["#btn-records-next", createFakeElement()],
  ]);

  const panel = createRecordsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    API: {
      async listRecords() {
        return { rows: [], summary: {}, display_columns: [] };
      },
    },
    RECORD_EXCHANGES: [
      ["all", "全部交易所"],
      ["shandong", "山东产权交易中心"],
      ["guangdong", "广东联合产权交易中心"],
      ["shenzhen", "深圳联合产权交易所"],
    ],
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    buildRecordsScope() {
      return {
        record_family: "listing",
        business_id: "capital_increase",
        exchange: "guangdong",
        state: "all",
        page: 1,
        page_size: 50,
      };
    },
    recordCellValue() {
      return "";
    },
    handleExport() {},
    getCatalog() {
      return {
        visible_families: [
          {
            family_id: "listing",
            businesses: [
              { business_id: "equity_transfer", business_label: "股权转让", supported_surfaces: ["records", "export"] },
              { business_id: "capital_increase", business_label: "增资扩股", supported_surfaces: ["records", "export"] },
            ],
          },
        ],
        sources: [
          { source_id: "shandong", source_label: "山东产权交易中心" },
          { source_id: "guangdong", source_label: "广东联合产权交易中心" },
          { source_id: "shenzhen", source_label: "深圳联合产权交易所" },
        ],
        support_matrix: {
          listing: {
            equity_transfer: { records: true, export: true },
            capital_increase: { records: true, export: true },
          },
        },
        surface_source_matrix: {
          listing: {
            equity_transfer: { records: ["shandong", "guangdong", "shenzhen"], export: ["shandong", "guangdong", "shenzhen"] },
            capital_increase: { records: ["shenzhen"], export: ["shenzhen"] },
          },
        },
      };
    },
    getOverview() {
      return {};
    },
    setOverview() {},
    getRecords() {
      return { rows: [], summary: {}, display_columns: [] };
    },
    setRecords() {},
    getRecordFilters() {
      return { ...filterState };
    },
    setRecordFilters(next) {
      updates.push(next);
      Object.assign(filterState, next);
    },
    getRecordPage() {
      return 1;
    },
    setRecordPage() {},
  });

  panel.renderLayout();

  assert.doesNotMatch(panelEl.innerHTML, /山东产权交易中心/);
  assert.doesNotMatch(panelEl.innerHTML, /广东联合产权交易中心/);
  assert.match(panelEl.innerHTML, /深圳联合产权交易所/);
  assert.equal(filterState.exchange, "all");
  assert.ok(updates.some((update) => update.exchange === "all"));
});
