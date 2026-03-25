const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const renderer = fs.readFileSync(path.join(__dirname, "renderer.js"), "utf8");
const rendererApi = fs.readFileSync(path.join(__dirname, "renderer", "api.mjs"), "utf8");
const main = fs.readFileSync(path.join(__dirname, "main.js"), "utf8");
const preload = fs.readFileSync(path.join(__dirname, "preload.js"), "utf8");

test("desktop rail exposes a dedicated tasks panel instead of a global sidebar", () => {
  assert.match(html, /data-panel="tasks"/);
  assert.match(html, /id="panel-tasks"/);
  assert.doesNotMatch(html, /class="workspace-sidebar"/);
});

test("task failure hint points to the tasks panel", () => {
  assert.match(renderer, /任务页/);
  assert.doesNotMatch(renderer, /数据记录页的任务明细/);
});

test("export action participates in task tracking and uses export-specific task copy", () => {
  assert.match(renderer, /job\.job_type === "export_excel"/);
  assert.match(renderer, /selectedJobId = payload\.job_id \|\| selectedJobId/);
  assert.match(renderer, /生成文件/);
});

test("records panel exposes date and keyword filters for direct database inspection", () => {
  assert.match(html, /id="recordsDateFromInput"/);
  assert.match(html, /id="recordsDateToInput"/);
  assert.match(html, /id="recordsKeywordInput"/);
  assert.match(renderer, /date_from/);
  assert.match(renderer, /keyword/);
});

test("saved mapping rules are visible without forcing a scroll-to-bottom toggle flow", () => {
  assert.match(html, /id="mappingEntriesTableWrap" class="records-table-wrap compact-list"/);
  assert.match(html, /收起规则表/);
  assert.match(renderer, /let mappingEntriesExpanded = true;/);
});

test("homepage first screen keeps one-click export and manual import in the primary action grid", () => {
  assert.match(html, /id="homePrimaryActions"[\s\S]*id="runOneClickBtn"[\s\S]*id="runExportBtn"[\s\S]*id="runManualImportBtn"/);
  assert.match(html, /id="statPendingMappingCard"/);
});

test("mappings panel exposes a batch pending refresh action instead of moving it to homepage", () => {
  const batchIndex = html.indexOf('id="runPendingMappingRefreshBtn"');
  const overviewStart = html.indexOf('id="homePrimaryActions"');
  const overviewEnd = html.indexOf('id="panel-tasks"');
  const mappingsStart = html.indexOf('id="panel-mappings"');
  assert.notEqual(batchIndex, -1);
  assert.ok(batchIndex > mappingsStart);
  assert.equal(batchIndex > overviewStart && batchIndex < overviewEnd, false);
});

test("overview exposes a force-stop control wired to backend restart", () => {
  assert.match(html, /id="forceStopBtn"/);
  assert.match(renderer, /window\.peapDesktop\.restartBackend\(\)/);
  assert.match(preload, /restartBackend:\s*\(\)\s*=> ipcRenderer\.invoke\("peap:restart-backend"\)/);
  assert.match(main, /ipcMain\.handle\("peap:restart-backend"/);
});

test("desktop window no longer blocks first paint on backend readiness", () => {
  assert.doesNotMatch(main, /async function createMainWindow\(\)\s*\{[\s\S]*waitForBackend\(/);
  assert.match(rendererApi, /\/api\/ready/);
});
