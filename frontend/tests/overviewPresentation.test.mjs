import test from "node:test";
import assert from "node:assert/strict";

import {
  buildOverviewPrimaryStats,
  buildOverviewProgressFallback,
  buildOverviewRecentJobView,
  buildOverviewViewModel,
} from "../src/presenters/overviewPresentation.mjs";

function num(value) {
  return Number.parseInt(value, 10) || 0;
}

test("buildOverviewViewModel reads normalized overview runtime and record summary fields", () => {
  const view = buildOverviewViewModel({
    record_summary: {
      state_counts: { ready: 3, pending_mapping: 2, parse_failed: 1 },
      pending_mapping_count: 4,
    },
    runtime: {
      browser: { installed: false, error: "runtime missing" },
      install: { status: "running", message: "Installing chromium" },
      readiness: {
        issues: [{ message: "runtime missing" }, { message: "browser cache invalid" }],
      },
    },
  });

  assert.deepEqual(view.stateCounts, { ready: 3, pending_mapping: 2, parse_failed: 1 });
  assert.equal(view.pendingMappingCount, 4);
  assert.equal(view.headline, "正在准备浏览器运行环境");
  assert.equal(view.browserState, "浏览器正在安装");
  assert.deepEqual(view.runtimeIssues, ["Installing chromium", "runtime missing", "runtime missing", "browser cache invalid"]);
});

test("buildOverviewPrimaryStats omits failed-state counters from primary cards", () => {
  const stats = buildOverviewPrimaryStats(
    buildOverviewViewModel({
      record_summary: {
        state_counts: {
          ready: 3,
          pending_mapping: 2,
          pending_review: 1,
          field_missing: 2,
          parse_failed: 7,
          postprocess_failed: 4,
        },
      },
    }),
  );

  assert.deepEqual(
    stats.map((item) => item.key),
    ["ready", "pending_mapping", "pending_review"],
  );
  assert.equal(stats[0].value, 3);
  assert.equal(stats[1].value, 2);
  assert.equal(stats[2].value, 3);
});

test("buildOverviewProgressFallback uses public job and progress presenters", () => {
  const text = buildOverviewProgressFallback(
    {
      job_type: "export_excel",
      result: {
        metrics: [
          { key: "new_records", label: "新增记录", value: 5 },
          { key: "changed_records", label: "变更记录", value: 2 },
        ],
      },
      progress: { metrics: [] },
      counts: { downloaded: 0, persisted: 0, exceptions: 0 },
    },
    { current_task_label: "", metrics: [] },
    { num },
  );

  assert.equal(text, "新增 5 · 变更 2");
});

test("buildOverviewProgressFallback shows terminal job result summary before mechanical progress metrics", () => {
  const text = buildOverviewProgressFallback(
    {
      job_type: "one_click",
      status: "success_with_warnings",
      result: {
        outcome: "succeeded_with_warnings",
        message: "已完成但有待处理：已下载 30，已归档 28，待补映射 2",
        metrics: [
          { key: "downloaded_count", label: "已下载", value: 30 },
          { key: "persisted_count", label: "已归档", value: 28 },
          { key: "pending_mapping_count", label: "待补映射", value: 2 },
        ],
      },
      progress: { metrics: [] },
      counts: { downloaded: 0, persisted: 0, exceptions: 0 },
    },
    {
      job_status: "success_with_warnings",
      metrics: [
        { key: "downloaded_count", label: "已下载", value: 30 },
        { key: "persisted_count", label: "已归档", value: 28 },
      ],
    },
    { num },
  );

  assert.equal(text, "已完成但有待处理：已下载 30，已归档 28，待补映射 2");
});

test("buildOverviewRecentJobView derives badge and meta from canonical job metrics", () => {
  const view = buildOverviewRecentJobView(
    {
      job_type: "export_excel",
      status: "success",
      result: {
        metrics: [
          { key: "new_records", label: "新增记录", value: 6 },
          { key: "changed_records", label: "变更记录", value: 3 },
        ],
      },
      progress: { metrics: [] },
      counts: { downloaded: 0, persisted: 0, exceptions: 0 },
      summary: { new_records: 99 },
    },
    { num },
  );

  assert.deepEqual(view.badge, { tone: "ready", text: "6 条", compact: false });
  assert.equal(view.metaText, "新增 6 · 变更 3");
});

test("overview presenters preserve business re-evaluation metrics end-to-end", () => {
  const text = buildOverviewProgressFallback(
    {
      job_type: "business_re_evaluation",
      result: {
        metrics: [
          { key: "accepted_completed_count", label: "已采纳", value: 7 },
        ],
      },
      progress: {
        metrics: [
          { key: "pending_review_count", label: "待人工复核", value: 3 },
        ],
      },
      counts: { downloaded: 0, persisted: 0, exceptions: 0 },
    },
    {
      business_label: "股权转让",
      metrics: [
        { key: "pending_review_count", label: "待人工复核", value: 3 },
        { key: "accepted_completed_count", label: "已采纳", value: 7 },
      ],
    },
    { num },
  );
  const view = buildOverviewRecentJobView(
    {
      job_type: "business_re_evaluation",
      status: "success",
      result: {
        metrics: [
          { key: "accepted_completed_count", label: "已采纳", value: 7 },
        ],
      },
      progress: { metrics: [] },
      counts: { downloaded: 0, persisted: 0, exceptions: 0 },
    },
    { num },
  );

  assert.equal(text, "当前：股权转让 · 待人工复核 3 · 已采纳 7");
  assert.equal(view.metaText, "已采纳 7");
});
