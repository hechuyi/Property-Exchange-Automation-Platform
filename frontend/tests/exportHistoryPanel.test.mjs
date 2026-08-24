import test from "node:test";
import assert from "node:assert/strict";

import { createExportHistoryPanel } from "../src/panels/exportHistory.js";

function createControl({ value = "" } = {}) {
  return {
    value,
    disabled: false,
    listeners: {},
    addEventListener(eventName, handler) {
      this.listeners[eventName] = handler;
    },
  };
}

function createHarness({
  history = { rows: [] },
  detail = null,
  loading = false,
  error = "",
  message = "",
  selectedExportId = "",
  api = {},
} = {}) {
  const panelEl = { innerHTML: "" };
  const controls = new Map([
    ["#btn-export-history-open", createControl()],
    ["#btn-export-history-download", createControl()],
    ["#export-history-download-dir", createControl()],
    ["#btn-export-history-refresh", createControl()],
  ]);
  const $ = (selector) => {
    if (selector === "#panel-export-history") return panelEl;
    if (!controls.has(selector)) controls.set(selector, createControl());
    return controls.get(selector);
  };
  const list = [];
  const setSelected = [];
  const setDetail = [];
  const setLoading = [];
  const setError = [];
  const setMessage = [];
  let currentHistory = history;
  let currentDetail = detail;
  let currentLoading = loading;
  let currentError = error;
  let currentMessage = message;
  let currentSelectedExportId = selectedExportId;
  const panel = createExportHistoryPanel({
    $,
    API: {
      async listExportHistory() {
        list.push(true);
        return history;
      },
      async getExportHistoryDetail(exportId) {
        setSelected.push(exportId);
        return detail;
      },
      async openExportHistory(exportId) {
        return { export_id: exportId, opened: true, path: "/tmp/export/a.xlsx", openable: true };
      },
      async downloadExportHistory(exportId, outputDir) {
        return { export_id: exportId, downloaded: true, artifacts: [`${outputDir}/a.xlsx`], openable: true };
      },
      ...api,
    },
    escapeHtml: (value) => String(value ?? ""),
    display: (value) => String(value || "—"),
    formatJobTime: (value) => String(value || ""),
    num: (value) => Number.parseInt(value, 10) || 0,
    getHistory: () => currentHistory,
    setHistory: (value) => {
      currentHistory = value;
      setDetail.push(["history", value]);
    },
    getDetail: () => currentDetail,
    setDetail: (value) => {
      currentDetail = value;
      setDetail.push(value);
    },
    getLoading: () => currentLoading,
    setLoading: (value) => {
      currentLoading = value;
      setLoading.push(value);
    },
    getError: () => currentError,
    setError: (value) => {
      currentError = value;
      setError.push(value);
    },
    getMessage: () => currentMessage,
    setMessage: (value) => {
      currentMessage = value;
      setMessage.push(value);
    },
    getSelectedExportId: () => currentSelectedExportId,
    setSelectedExportId: (value) => {
      currentSelectedExportId = value;
      setSelected.push(value);
    },
  });
  panel.render();
  return { panelEl, html: panelEl.innerHTML, controls, list, setSelected, setDetail, setLoading, setError, setMessage, panel };
}

const sampleDetail = {
  export_id: "exp-1",
  cursor_id: "cursor-a",
  requested_export_mode: "full",
  revision_watermark: 42,
  created_at: "2026-05-18T09:30:00+08:00",
  artifacts: ["/tmp/export/a.xlsx"],
  existing_artifacts: ["/tmp/export/a.xlsx"],
  manifest: {
    effective_export_mode: "full",
    export_profile_id: "listing/equity_transfer",
    canonical_scope_hash: "hash-1",
    schema_version: "schema-v1",
    header_version: "headers-v1",
    included_count: 7,
    excluded_count: 1,
    artifact_checksum: "sha256:abc",
    field_missing_blocked_records: 2,
    cursor_basis: { export_id: "exp-1", eligible_set_hash: "eligible-1" },
    scope: { record_family: "listing", business_id: "equity_transfer", exchange: "sse" },
  },
  cursor_value: {
    last_successful_export_id: "exp-1",
    last_successful_revision_watermark: 42,
  },
  openable: true,
  rebuildable: true,
  is_tombstone: false,
  pruned_by_retention: false,
  retention_status: "available",
  retention_count: 20,
};

test("export history panel renders list rows and selected manifest summary", () => {
  const { html } = createHarness({
    history: {
      rows: [
        {
          export_id: "exp-1",
          cursor_id: "cursor-a",
          requested_export_mode: "full",
          created_at: "2026-05-18T09:30:00+08:00",
          artifact_count: 1,
          openable: true,
          rebuildable: true,
          retention_status: "available",
          retention_count: 20,
        },
        {
          export_id: "exp-old",
          requested_export_mode: "incremental",
          openable: false,
          rebuildable: false,
          retention_status: "pruned_by_retention",
          is_tombstone: true,
          retention_count: 3,
        },
      ],
    },
    detail: sampleDetail,
    selectedExportId: "exp-1",
  });

  assert.match(html, /导出历史/);
  assert.match(html, /exp-1/);
  assert.match(html, /exp-old/);
  assert.match(html, /已保留/);
  assert.match(html, /保留策略移除/);
  assert.match(html, /listing\/equity_transfer/);
  assert.match(html, /hash-1/);
  assert.match(html, /sha256:abc/);
  assert.match(html, /eligible-1/);
  assert.match(html, /打开/);
  assert.match(html, /下载/);
});

test("export history panel disables open and download for tombstones", () => {
  const { html } = createHarness({
    history: { rows: [{ export_id: "exp-old", openable: false, rebuildable: false, retention_status: "pruned_by_retention" }] },
    detail: { ...sampleDetail, export_id: "exp-old", openable: false, rebuildable: false, is_tombstone: true, retention_status: "pruned_by_retention" },
    selectedExportId: "exp-old",
  });

  assert.match(html, /id="btn-export-history-open"[^>]*disabled/);
  assert.match(html, /id="btn-export-history-download"[^>]*disabled/);
  assert.match(html, /不可打开/);
});

test("export history panel wires open and download actions to the API client", async () => {
  const calls = [];
  const { controls, setMessage } = createHarness({
    history: { rows: [{ export_id: "exp-1", openable: true, rebuildable: true, retention_status: "available" }] },
    detail: sampleDetail,
    selectedExportId: "exp-1",
    api: {
      async openExportHistory(exportId) {
        calls.push(["open", exportId]);
        return { export_id: exportId, opened: true, path: "/tmp/export/a.xlsx", openable: true };
      },
      async downloadExportHistory(exportId, outputDir) {
        calls.push(["download", exportId, outputDir]);
        return { export_id: exportId, downloaded: true, artifacts: ["/tmp/download/a.xlsx"], openable: true };
      },
    },
  });

  controls.get("#export-history-download-dir").value = "/tmp/download";
  await controls.get("#btn-export-history-open").listeners.click();
  await controls.get("#btn-export-history-download").listeners.click();

  assert.deepEqual(calls, [
    ["open", "exp-1"],
    ["download", "exp-1", "/tmp/download"],
  ]);
  assert.ok(setMessage.some((value) => String(value).includes("已打开")));
  assert.ok(setMessage.some((value) => String(value).includes("已下载")));
});

test("export history selection failure clears stale detail and disables detail actions", async () => {
  const calls = [];
  const actionCalls = [];
  const { panel, panelEl, controls } = createHarness({
    history: {
      rows: [
        { export_id: "exp-old", requested_export_mode: "full", openable: true, retention_status: "available" },
        { export_id: "exp-missing", requested_export_mode: "full", openable: true, retention_status: "artifact_unavailable" },
      ],
    },
    detail: { ...sampleDetail, export_id: "exp-old" },
    selectedExportId: "exp-old",
    api: {
      async getExportHistoryDetail(exportId) {
        calls.push(exportId);
        if (exportId === "exp-missing") throw new Error("detail not found");
        return { ...sampleDetail, export_id: exportId };
      },
      async openExportHistory(exportId) {
        actionCalls.push(["open", exportId]);
        return { export_id: exportId, opened: true, path: "/tmp/export/missing.xlsx", openable: true };
      },
      async downloadExportHistory(exportId, outputDir) {
        actionCalls.push(["download", exportId, outputDir]);
        return { export_id: exportId, downloaded: true, artifacts: [], openable: true };
      },
    },
  });

  assert.match(panelEl.innerHTML, /exp-old/);
  assert.match(panelEl.innerHTML, /hash-1/);

  await panel.selectExport("exp-missing");

  assert.deepEqual(calls, ["exp-missing"]);
  assert.doesNotMatch(panelEl.innerHTML, /hash-1/);
  assert.doesNotMatch(panelEl.innerHTML, /sha256:abc/);
  assert.match(panelEl.innerHTML, /详情加载失败/);
  assert.match(panelEl.innerHTML, /detail not found/);
  assert.match(panelEl.innerHTML, /id="btn-export-history-open"[^>]*disabled/);
  assert.match(panelEl.innerHTML, /id="btn-export-history-download"[^>]*disabled/);

  await controls.get("#btn-export-history-open").listeners.click();
  await controls.get("#btn-export-history-download").listeners.click();

  assert.deepEqual(actionCalls, []);
});

test("export history load falls back when the previous selection is no longer in the current list", async () => {
  const detailForExp2 = { ...sampleDetail, export_id: "exp-2" };
  const selected = [];
  const detailWrites = [];
  const panelEl = { innerHTML: "" };
  const controls = new Map([
    ["#btn-export-history-refresh", createControl()],
    ["#btn-export-history-open", createControl()],
    ["#btn-export-history-download", createControl()],
    ["#export-history-download-dir", createControl()],
  ]);
  const panel = createExportHistoryPanel({
    $: (selector) => {
      if (selector === "#panel-export-history") return panelEl;
      if (!controls.has(selector)) controls.set(selector, createControl());
      return controls.get(selector);
    },
    API: {
      async listExportHistory() {
        return { rows: [{ export_id: "exp-2", openable: true, retention_status: "available" }] };
      },
      async getExportHistoryDetail(exportId) {
        assert.equal(exportId, "exp-2");
        return detailForExp2;
      },
    },
    escapeHtml: (value) => String(value ?? ""),
    display: (value) => String(value || "—"),
    formatJobTime: (value) => String(value || ""),
    num: (value) => Number.parseInt(value, 10) || 0,
    getHistory: () => ({ rows: [] }),
    setHistory: () => {},
    getDetail: () => null,
    setDetail: (value) => detailWrites.push(value),
    getLoading: () => false,
    setLoading: () => {},
    getError: () => "",
    setError: () => {},
    getMessage: () => "",
    setMessage: () => {},
    getSelectedExportId: () => "exp-pruned",
    setSelectedExportId: (value) => selected.push(value),
  });

  await panel.load();

  assert.deepEqual(selected, ["exp-2"]);
  assert.equal(detailWrites.at(-1).export_id, "exp-2");
});

test("export history ignores a stale detail response after a newer selection", async () => {
  let resolveOld;
  let resolveNew;
  const oldDetail = new Promise((resolve) => { resolveOld = resolve; });
  const newDetail = new Promise((resolve) => { resolveNew = resolve; });
  const { panel, panelEl } = createHarness({
    history: {
      rows: [
        { export_id: "exp-old", openable: true, retention_status: "available" },
        { export_id: "exp-new", openable: true, retention_status: "available" },
      ],
    },
    selectedExportId: "exp-old",
    api: {
      async getExportHistoryDetail(exportId) {
        return exportId === "exp-old" ? oldDetail : newDetail;
      },
    },
  });

  const selectingOld = panel.selectExport("exp-old");
  const selectingNew = panel.selectExport("exp-new");
  resolveNew({
    ...sampleDetail,
    export_id: "exp-new",
    manifest: { ...sampleDetail.manifest, canonical_scope_hash: "new-scope-hash" },
  });
  await selectingNew;
  resolveOld({
    ...sampleDetail,
    export_id: "exp-old",
    manifest: { ...sampleDetail.manifest, canonical_scope_hash: "stale-scope-hash" },
  });
  await selectingOld;

  assert.match(panelEl.innerHTML, /new-scope-hash/);
  assert.doesNotMatch(panelEl.innerHTML, /stale-scope-hash/);
});

test("export history keeps a successful collection visible when its initial detail fails", async () => {
  const { panel, panelEl } = createHarness({
    history: { rows: [] },
    api: {
      async listExportHistory() {
        return { rows: [{ export_id: "exp-visible", openable: true, retention_status: "available" }] };
      },
      async getExportHistoryDetail() {
        throw new Error("detail unavailable");
      },
    },
  });

  await panel.load();

  assert.match(panelEl.innerHTML, /exp-visible/);
  assert.match(panelEl.innerHTML, /详情加载失败/);
  assert.match(panelEl.innerHTML, /detail unavailable/);
  assert.doesNotMatch(panelEl.innerHTML, /导出历史加载失败/);
});
