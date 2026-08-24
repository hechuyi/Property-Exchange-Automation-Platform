import test from "node:test";
import assert from "node:assert/strict";

import {
  buildJobMetaText,
  buildRecentJobBadge,
  buildProgressHintText,
} from "../src/presenters/jobPresentation.mjs";

function num(value) {
  return Number.parseInt(value, 10) || 0;
}

test("buildJobMetaText prefers export result metrics over raw summary fields", () => {
  const text = buildJobMetaText(
    {
      job_type: "export_excel",
      result: {
        metrics: [
          { key: "new_records", label: "新增记录", value: 5 },
          { key: "changed_records", label: "变更记录", value: 2 },
          { key: "visible_count", label: "可见记录", value: 8 },
        ],
      },
      progress: { metrics: [] },
      counts: { downloaded: 99, persisted: 88, exceptions: 77 },
      summary: { new_records: 100 },
    },
    { num },
  );

  assert.equal(text, "新增 5 · 变更 2");
});

test("buildJobMetaText falls back to progress metrics for running ingest jobs", () => {
  const text = buildJobMetaText(
    {
      job_type: "one_click",
      result: { metrics: [] },
      progress: {
        metrics: [
          { key: "downloaded_count", label: "已下载", value: 9 },
          { key: "persisted_count", label: "已归档", value: 4 },
          { key: "exception_count", label: "异常", value: 1 },
        ],
      },
      counts: { downloaded: 0, persisted: 0, exceptions: 0 },
    },
    { num },
  );

  assert.equal(text, "已下载 9 · 已归档 4 · 异常 1");
});

test("buildJobMetaText uses user-facing terminal result messages", () => {
  const text = buildJobMetaText(
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
    { num },
  );

  assert.equal(text, "已完成但有待处理：已下载 30，已归档 28，待补映射 2");
});

test("buildJobMetaText exposes failed terminal reasons instead of zero counters", () => {
  const text = buildJobMetaText(
    {
      job_type: "download_ingest",
      status: "failed",
      result: {
        outcome: "failed",
        message: "未完成：导出失败",
        failure_code: "export_failed",
        failure_message: "Excel 写入失败",
        metrics: [],
      },
      progress: { metrics: [] },
      counts: { downloaded: 0, persisted: 0, exceptions: 0 },
    },
    { num },
  );

  assert.equal(text, "未完成：导出失败 · Excel 写入失败");
});

test("buildJobMetaText preserves pending-review warnings for manual import jobs", () => {
  const text = buildJobMetaText(
    {
      job_type: "manual_import",
      result: {
        metrics: [
          { key: "imported_count", label: "导入成功", value: 2 },
          { key: "pending_review_count", label: "待人工复核", value: 1 },
          { key: "pending_mapping_count", label: "待补映射", value: 0 },
        ],
      },
      progress: { metrics: [] },
      counts: { downloaded: 0, persisted: 0, exceptions: 0 },
    },
    { num },
  );

  assert.equal(text, "导入成功 2 · 待人工复核 1 · 待补映射 0");
});

test("buildRecentJobBadge uses export result metrics instead of summary payload", () => {
  const badge = buildRecentJobBadge(
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

  assert.deepEqual(badge, { tone: "ready", text: "6 条", compact: false });
});

test("buildProgressHintText reads public progress metrics rather than hidden top-level counters", () => {
  const text = buildProgressHintText(
    {
      business_label: "股权转让",
      metrics: [
        { key: "downloaded_count", label: "已下载", value: 9 },
        { key: "persisted_count", label: "已归档", value: 4 },
      ],
      downloaded_count: 12345,
    },
    { num },
  );

  assert.equal(text, "当前：股权转让 · 已下载 9 · 已归档 4");
});

test("business re-evaluation presenters render distinct public metrics instead of ingest defaults", () => {
  const jobText = buildJobMetaText(
    {
      job_type: "business_re_evaluation",
      result: {
        metrics: [
          { key: "accepted_completed_count", label: "已采纳", value: 7 },
          { key: "skipped_count", label: "已跳过", value: 1 },
        ],
      },
      progress: {
        metrics: [
          { key: "pending_review_count", label: "待人工复核", value: 3 },
          { key: "accepted_completed_count", label: "已采纳", value: 7 },
        ],
      },
      counts: { downloaded: 0, persisted: 0, exceptions: 0 },
    },
    { num },
  );
  const progressText = buildProgressHintText(
    {
      business_label: "股权转让",
      metrics: [
        { key: "pending_review_count", label: "待人工复核", value: 3 },
        { key: "accepted_completed_count", label: "已采纳", value: 7 },
      ],
    },
    { num },
  );

  assert.equal(jobText, "待人工复核 3 · 已采纳 7");
  assert.equal(progressText, "当前：股权转让 · 待人工复核 3 · 已采纳 7");
});
