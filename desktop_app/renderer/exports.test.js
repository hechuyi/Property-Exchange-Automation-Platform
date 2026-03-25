const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

async function loadExportsModule() {
  const moduleUrl = pathToFileURL(path.join(__dirname, "exports.mjs")).href;
  return import(moduleUrl);
}

test("buildExportRequestFromView carries current scope instead of date-only payload", async () => {
  const { buildExportRequestFromView } = await loadExportsModule();
  const request = buildExportRequestFromView({
    recordFamily: "listing",
    state: "pending_mapping",
    projectType: "all",
    keyword: "示例",
    dateFrom: "2026-03-01",
    dateTo: "2026-03-25",
    page: 3,
    pageSize: 25,
  });

  assert.deepEqual(request.scope, {
    record_family: "listing",
    state: "pending_mapping",
    project_type: "all",
    keyword: "示例",
    date_from: "2026-03-01",
    date_to: "2026-03-25",
    page: 3,
    page_size: 25,
  });
  assert.equal(request.date_from, "2026-03-01");
  assert.equal(request.date_to, "2026-03-25");
  assert.equal(Object.prototype.hasOwnProperty.call(request, "scope"), true);
});

test("formatEmptyExportMessage uses empty_reason_code and scope_state_counts", async () => {
  const { formatEmptyExportMessage } = await loadExportsModule();
  const message = formatEmptyExportMessage({
    empty_reason_code: "pending_mapping_blocked",
    scope_state_counts: {
      pending_mapping: 4,
      skipped: 1,
    },
  });

  assert.match(message, /待补映射 4 条/);
  assert.doesNotMatch(message, /4 条待补映射/);
});
