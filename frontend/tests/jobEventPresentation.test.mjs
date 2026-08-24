import test from "node:test";
import assert from "node:assert/strict";

import {
  buildJobEventErrorText,
  buildJobEventLogText,
  eventStageLabel,
  hasJobEventActivity,
  isJobEventFailure,
} from "../src/presenters/jobEventsPresentation.mjs";

function num(value) {
  return Number.parseInt(value, 10) || 0;
}

test("job event presentation builds progress text from canonical event fields", () => {
  const event = {
    stage_code: "save_pages",
    stage_label: "正在保存网页",
    kind: "download",
    task_label: "北交所 - 增资扩股",
    phase_percent: 48,
    summary: {
      listed: 12,
      detail_fetched: 6,
      saved: 5,
      duplicate_skipped: 2,
      business_filter_skipped: 3,
    },
  };

  assert.equal(eventStageLabel(event), "正在保存网页");
  assert.equal(buildJobEventLogText(event, { num }), "北交所 - 增资扩股 · 已列 12 · 已抓 6 · 已保存 5 · 重复 2 · 业务过滤 3");
  assert.equal(hasJobEventActivity(event), true);
});

test("job event presentation explains date-filtered zero-candidate downloads", () => {
  const event = {
    stage_code: "save_pages",
    stage_label: "正在保存网页",
    kind: "download",
    business_label: "实物资产",
    summary: {
      listed: 30,
      list_date_skipped: 30,
      detail_candidates: 0,
      saved: 0,
      warning_code: "all_listed_rows_outside_date_range",
      warning_message: "已列出 30 条，30 条因披露日期不在 2026-03-01..2026-03-31 被跳过",
    },
  };

  assert.equal(
    buildJobEventLogText(event, { num }),
    "实物资产 · 已列 30 · 日期跳过 30 · 已列出 30 条，30 条因披露日期不在 2026-03-01..2026-03-31 被跳过",
  );
});

test("job event presentation explains empty export warnings", () => {
  const event = {
    stage_code: "exporting",
    stage_label: "正在导出 Excel",
    kind: "export",
    business_label: "实物资产",
    summary: {
      warning_code: "pending_mapping_blocked",
      warning_message: "当前条件下没有可导出的记录；待补映射 2 条",
    },
  };

  assert.equal(
    buildJobEventLogText(event, { num }),
    "实物资产 · 当前条件下没有可导出的记录；待补映射 2 条",
  );
});

test("job event presentation falls back to structured business identity when task label is absent", () => {
  const event = {
    business_label: "股权转让",
    stage_code: "save_pages",
    kind: "download",
    phase_percent: 48,
    summary: {
      listed: 12,
      detail_fetched: 6,
    },
  };

  assert.equal(buildJobEventLogText(event, { num }), "股权转让 · 已列 12 · 已抓 6");
});

test("job event presentation uses explicit canonical error fields", () => {
  const event = {
    stage_code: "save_pages",
    status: "failed",
    error_code: "collect_failed",
    error_message: "上游 500",
  };

  assert.equal(isJobEventFailure(event), true);
  assert.equal(buildJobEventErrorText(event), "collect_failed: 上游 500");
});

test("job event presentation does not treat skipped parse events as unknown errors", () => {
  const event = {
    stage_code: "skipped",
    status: "skipped",
    record_state: "skipped",
    warning_code: "skip_parse",
    error_code: "",
    error_message: "",
  };

  assert.equal(isJobEventFailure(event), false);
});
