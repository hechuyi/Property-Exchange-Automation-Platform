import test from "node:test";
import assert from "node:assert/strict";

import { normalizeJobEventsResource } from "../src/contracts/jobEvents.js";

test("normalizeJobEventsResource keeps only canonical event view fields", () => {
  const normalized = normalizeJobEventsResource({
    events: [
      {
        event_id: "event-1",
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "股权转让",
        scope: {
          record_family: "listing",
          business_id: "equity_transfer",
          business_label: "股权转让",
          exchange: "cbex",
        },
        stage_code: "save_pages",
        stage_label: "正在保存网页",
        status: "running",
        label: "保存中",
        kind: "download",
        task_label: "北交所 - 增资扩股",
        task_index: 2,
        task_total: 4,
        phase_percent: 50,
        summary: {
          listed: 12,
          saved: 6,
          list_date_skipped: 5,
          warning_code: "all_listed_rows_outside_date_range",
          warning_message: "已列出 12 条，5 条因披露日期被跳过",
          internal_only_count: 99,
        },
        project_code: "CODE-1",
        record_state: "ready",
        source_id: "",
        error_code: "",
        error_message: "",
        warning_code: "all_listed_rows_outside_date_range",
        warning_message: "已列出 12 条，5 条因披露日期被跳过",
        empty_reason_code: "pending_mapping_blocked",
        scope_state_counts: { pending_mapping: 2, conflict: 0 },
        payload: { legacy: true },
      },
    ],
    returned_count: 1,
    total_count: 3,
    truncated: true,
    legacy_field: true,
  });

  assert.deepEqual(normalized, {
    events: [
      {
        event_id: "event-1",
        record_family: "listing",
        business_id: "equity_transfer",
        business_label: "股权转让",
        scope: {
          record_family: "listing",
          business_id: "equity_transfer",
          business_label: "股权转让",
          exchange: "cbex",
          state: "",
          keyword: "",
          date_from: "",
          date_to: "",
          page: 0,
          page_size: 0,
        },
        stage_code: "save_pages",
        stage_label: "正在保存网页",
        status: "running",
        label: "保存中",
        kind: "download",
        task_label: "北交所 - 增资扩股",
        task_index: 2,
        task_total: 4,
        phase_percent: 50,
        summary: {
          listed: 12,
          saved: 6,
          list_date_skipped: 5,
          warning_code: "all_listed_rows_outside_date_range",
          warning_message: "已列出 12 条，5 条因披露日期被跳过",
        },
        project_code: "CODE-1",
        record_state: "ready",
        source_id: "",
        error_code: "",
        error_message: "",
        warning_code: "all_listed_rows_outside_date_range",
        warning_message: "已列出 12 条，5 条因披露日期被跳过",
        empty_reason_code: "pending_mapping_blocked",
        scope_state_counts: { pending_mapping: 2, conflict: 0 },
      },
    ],
    returned_count: 1,
    total_count: 3,
    truncated: true,
  });
});

test("normalizeJobEventsResource promotes warning fields from summary without throwing", () => {
  const normalized = normalizeJobEventsResource({
    events: [
      {
        event_id: "event-summary-warning",
        stage_code: "save_pages",
        status: "warning",
        summary: {
          listed: 8,
          warning_code: "all_listed_rows_outside_date_range",
          warning_message: "已列出 8 条，8 条因披露日期被跳过",
        },
      },
    ],
    returned_count: 1,
    total_count: 1,
    truncated: false,
  });

  assert.deepEqual(normalized.events[0], {
    event_id: "event-summary-warning",
    record_family: "",
    business_id: "",
    business_label: "",
    stage_code: "save_pages",
    stage_label: "",
    status: "warning",
    label: "",
    kind: "",
    task_label: "",
    task_index: 0,
    task_total: 0,
    phase_percent: 0,
    summary: {
      listed: 8,
      warning_code: "all_listed_rows_outside_date_range",
      warning_message: "已列出 8 条，8 条因披露日期被跳过",
    },
    project_code: "",
    record_state: "",
    source_id: "",
    error_code: "",
    error_message: "",
    warning_code: "all_listed_rows_outside_date_range",
    warning_message: "已列出 8 条，8 条因披露日期被跳过",
    empty_reason_code: "",
    scope_state_counts: {},
  });
});

test("normalizeJobEventsResource preserves filtered public-resource retry progress", () => {
  const normalized = normalizeJobEventsResource({
    events: [
      {
        event_id: "public-resource-retry-1",
        source_id: "public_resource",
        stage_code: "save_pages",
        status: "running",
        summary: {
          attempt: "2",
          attempt_total: 3,
          retry_in_seconds: "5",
          business_code: "800",
          transport: " playwright ",
          role: "search_transient_business_error",
          internal_retry_token: "must not leak",
        },
      },
    ],
  });

  assert.deepEqual(normalized.events[0].summary, {
    attempt: 2,
    attempt_total: 3,
    retry_in_seconds: 5,
    business_code: 800,
    transport: "playwright",
    role: "search_transient_business_error",
  });
  assert.equal(normalized.events[0].source_id, "public_resource");
  assert.equal("internal_retry_token" in normalized.events[0].summary, false);
});

test("normalizeJobEventsResource deduplicates stable event ids without collapsing id-less events", () => {
  const normalized = normalizeJobEventsResource({
    events: [
      { event_id: "42", stage_code: "download", label: "newest" },
      { event_id: "42", stage_code: "download", label: "duplicate" },
      { event_id: "", stage_code: "download", label: "id-less-a" },
      { event_id: "", stage_code: "download", label: "id-less-b" },
    ],
    returned_count: 4,
    total_count: 4,
  });

  assert.deepEqual(
    normalized.events.map((event) => event.label),
    ["newest", "id-less-a", "id-less-b"],
  );
});
