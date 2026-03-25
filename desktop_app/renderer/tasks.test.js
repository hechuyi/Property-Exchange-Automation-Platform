const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

async function loadTasksModule() {
  const moduleUrl = pathToFileURL(path.join(__dirname, "tasks.mjs")).href;
  return import(moduleUrl);
}

test("progressPreset treats interrupted as terminal", async () => {
  const { progressPreset } = await loadTasksModule();
  const preset = progressPreset({
    job_status: "interrupted",
    phase_code: "archive_pending",
  });

  assert.equal(preset.width, 100);
  assert.equal(preset.active, false);
});

test("eventTitle prefers terminal status semantics over stage semantics", async () => {
  const { formatEventTitle } = await loadTasksModule();
  const title = formatEventTitle({
    status: "interrupted",
    stage: "failed",
    project_code: "A001",
  });

  assert.match(title, /已中断/);
  assert.doesNotMatch(title, /处理失败/);
});

test("manual_import and mapping_refresh progress copy do not mention archive counts", async () => {
  const { formatProgressMeta, formatProgressHint } = await loadTasksModule();
  const manualImportView = {
    job_status: "running",
    phase_code: "manual_import_scan",
    phase_label: "正在整理手动导入文件",
    job_type: "manual_import",
    current_item_label: "foo.html",
    current_index: 2,
    current_total: 5,
    downloaded_count: 2,
    persisted_count: 1,
    pending_mapping_count: 0,
    skipped_count: 0,
    exception_count: 0,
    latest_stage_summary: {},
  };
  const mappingRefreshView = {
    ...manualImportView,
    job_type: "mapping_refresh",
    phase_label: "正在重处理记录",
    phase_code: "reprocessing",
  };
  const latestJob = { job_type: "manual_import", status: "running", job_id: "job-1" };
  const mappingJob = { job_type: "mapping_refresh", status: "running", job_id: "job-2" };

  const manualMeta = formatProgressMeta(manualImportView, latestJob, {});
  const manualHint = formatProgressHint(manualImportView, latestJob, {});
  const mappingMeta = formatProgressMeta(mappingRefreshView, mappingJob, {});
  const mappingHint = formatProgressHint(mappingRefreshView, mappingJob, {});

  for (const text of [manualMeta, manualHint, mappingMeta, mappingHint]) {
    assert.doesNotMatch(text, /归档|存档/);
  }
  assert.match(manualMeta, /手动导入/);
  assert.match(mappingMeta, /回刷|重处理/);
});
