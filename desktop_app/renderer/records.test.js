const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

async function loadRecordsModule() {
  const moduleUrl = pathToFileURL(path.join(__dirname, "records.mjs")).href;
  return import(moduleUrl);
}

test("buildRecordsQuery defaults to listing + all", async () => {
  const { buildRecordsQuery } = await loadRecordsModule();
  const query = buildRecordsQuery();

  assert.equal(query.get("record_family"), "listing");
  assert.equal(query.get("state"), "all");
  assert.equal(query.get("project_type"), "all");
  assert.equal(query.get("page"), "1");
  assert.equal(query.get("page_size"), "50");
});

test("formatRecordsSummary prefers filtered_state_counts over page_state_counts for overview copy", async () => {
  const { formatRecordsSummary } = await loadRecordsModule();
  const text = formatRecordsSummary({
    page: 1,
    page_count: 2,
    summary: {
      visible_count: 2,
      total_count: 3,
      filtered_state_counts: {
        ready: 7,
        pending_mapping: 1,
      },
      page_state_counts: {
        ready: 2,
        pending_mapping: 9,
      },
    },
  });

  assert.match(text, /共 3 条/);
  assert.match(text, /第 1 \/ 2 页/);
  assert.match(text, /本页 2 条/);
  assert.match(text, /已录入 7 条/);
  assert.match(text, /待补映射 1 条/);
  assert.doesNotMatch(text, /已录入 2 条/);
});

test("formatPendingMappingsSummary exposes truncation hint for capped pending list", async () => {
  const { formatPendingMappingsSummary } = await loadRecordsModule();
  const text = formatPendingMappingsSummary({
    pending: [{ record_id: "p-1" }, { record_id: "p-2" }],
    returned_count: 2,
    total_count: 5,
    truncated: true,
  });

  assert.match(text, /当前 2 条待补项/);
  assert.match(text, /只显示前 2 条/);
  assert.match(text, /仍有剩余 3 条/);
});
