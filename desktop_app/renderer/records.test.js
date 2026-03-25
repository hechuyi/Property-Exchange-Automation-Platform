const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

async function loadRecordsModule() {
  const moduleUrl = pathToFileURL(path.join(__dirname, "records.mjs")).href;
  return import(moduleUrl);
}

test("formatRecordsSummary distinguishes total rows from current page rows", async () => {
  const { formatRecordsSummary } = await loadRecordsModule();
  const text = formatRecordsSummary({
    page: 1,
    page_count: 2,
    summary: {
      visible_count: 2,
      total_count: 3,
      state_counts: {
        ready: 2,
      },
    },
  });

  assert.match(text, /共 3 条/);
  assert.match(text, /第 1 \/ 2 页/);
  assert.match(text, /本页 2 条/);
  assert.match(text, /已录入 2 条/);
});
