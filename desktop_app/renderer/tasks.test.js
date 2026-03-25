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
    job_status: "interrupted",
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
    job_status: "failed",
    phase_label: "正在重处理记录",
    phase_code: "reprocessing",
  };
  const latestJob = { job_type: "manual_import", status: "interrupted", job_id: "job-1" };
  const mappingJob = { job_type: "mapping_refresh", status: "failed", job_id: "job-2" };

  const manualMeta = formatProgressMeta(manualImportView, latestJob, {});
  const manualHint = formatProgressHint(manualImportView, latestJob, {});
  const mappingMeta = formatProgressMeta(mappingRefreshView, mappingJob, {});
  const mappingHint = formatProgressHint(mappingRefreshView, mappingJob, {});

  for (const text of [manualMeta, manualHint, mappingMeta, mappingHint]) {
    assert.doesNotMatch(text, /正在/);
    assert.doesNotMatch(text, /归档|存档/);
  }
  assert.match(manualMeta, /手动导入/);
  assert.match(manualHint, /手动导入/);
  assert.match(mappingMeta, /回刷|重处理/);
  assert.match(mappingHint, /回刷/);
});

test("formatProgressHint treats terminal download tasks as finished states", async () => {
  const { formatProgressHint } = await loadTasksModule();
  const interrupted = formatProgressHint({
    job_type: "one_click",
    job_status: "interrupted",
    phase_code: "prepare_tasks",
    current_item_label: "A001",
  }, { job_type: "one_click", status: "interrupted", job_id: "job-3" }, {});
  const failed = formatProgressHint({
    job_type: "download_ingest",
    job_status: "failed",
    phase_code: "save_pages",
    current_item_label: "B002",
  }, { job_type: "download_ingest", status: "failed", job_id: "job-4" }, {});
  const completed = formatProgressHint({
    job_type: "one_click",
    job_status: "success",
    phase_code: "prepare_tasks",
    current_item_label: "C003",
  }, { job_type: "one_click", status: "success", job_id: "job-5" }, {});

  for (const text of [interrupted, failed, completed]) {
    assert.doesNotMatch(text, /正在/);
  }
  assert.match(interrupted, /已中断/);
  assert.match(failed, /失败/);
  assert.match(completed, /已完成/);
});

test("formatProgressMeta restores archive_pending backlog counts", async () => {
  const { formatProgressMeta } = await loadTasksModule();
  const text = formatProgressMeta({
    job_type: "one_click",
    job_status: "running",
    phase_code: "archive_pending",
    downloaded_count: 8,
    persisted_count: 3,
    skipped_count: 1,
    pending_mapping_count: 2,
    exception_count: 1,
    archive_pending_count: 4,
    archive_completed_count: 3,
  }, { job_type: "one_click", status: "running", job_id: "job-6" }, {});

  assert.match(text, /待存档 4 条/);
  assert.match(text, /已存档 3 条/);
  assert.match(text, /已保存网页 8 条/);
});
