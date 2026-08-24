import test from "node:test";
import assert from "node:assert/strict";

import { normalizeJobResource } from "../src/contracts/jobs.js";
import { normalizeOverviewResource } from "../src/contracts/overview.js";
import { normalizeProgressResource } from "../src/contracts/progress.js";
import { normalizeRuntimeResource } from "../src/contracts/runtime.js";
import {
  normalizeAdvancedSettingsResource,
  normalizeBasicSettingsResource,
} from "../src/contracts/settings.js";

test("normalizeJobResource only consumes canonical job resource fields", () => {
  const normalized = normalizeJobResource({
    job_id: "job-1",
    job_type: "one_click",
    status: "running",
    actions: { retry: false },
    record_family: "listing",
    business_id: "equity_transfer",
    business_label: "股权转让",
    scope: {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
    },
    metadata: { record_family: "legacy-family", business_id: "legacy-business" },
    counts: {},
    downloaded_count: 9,
    persisted_count: 4,
    exception_count: 1,
    progress: {},
    result: {},
    summary: {},
  });

  assert.deepEqual(normalized, {
    job_id: "job-1",
    job_type: "one_click",
    status: "running",
    actions: { retry: false },
    created_at: "",
    updated_at: "",
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
    counts: {
      downloaded: 0,
      persisted: 0,
      exceptions: 0,
    },
    progress: {
      phase_code: "",
      phase_label: "",
      job_status: "",
      is_terminal: false,
      phase_percent: 0,
      current_task_label: "",
      task_index: 0,
      task_total: 0,
      metrics: [],
      latest_stage_code: "",
      latest_stage_label: "",
      latest_stage_summary: {},
    },
    result: {
      outcome: "",
      message: "",
      failure_code: "",
      failure_message: "",
      metrics: [],
      artifact_count: 0,
      download_archive_audit: {},
      public_resource: {},
    },
  });
});

test("normalizeOverviewResource only consumes canonical overview fields", () => {
  const normalized = normalizeOverviewResource({
    record_summary: {},
    record_state_counts: { ready: 9 },
    pending_mapping_count: 99,
    runtime: {},
    defaults: {
      manual_import_input_dir: "/tmp/manual",
      default_scope: {
        stored_preference: {
          record_family: "listing",
          business_id: "equity_transfer",
          exchange: "cbex",
        },
        effective_scope: {
          record_family: "listing",
          business_id: "equity_transfer",
          business_label: "股权转让",
          exchange: "cbex",
        },
        stale_resolution: {
          is_stale: false,
          reason: "",
          hint: "",
        },
      },
    },
    visibility: {
      mode: "listing_only",
      visible_families: ["listing"],
    },
    raw_manual_root: "/tmp/legacy",
  });

  assert.deepEqual(normalized, {
    record_summary: {
      state_counts: {},
      pending_mapping_count: 0,
    },
    latest_job: null,
    latest_progress: {
      phase_code: "",
      phase_label: "",
      job_status: "",
      is_terminal: false,
      phase_percent: 0,
      current_task_label: "",
      task_index: 0,
      task_total: 0,
      metrics: [],
      latest_stage_code: "",
      latest_stage_label: "",
      latest_stage_summary: {},
    },
    recent_jobs: [],
    runtime: {
      browser: {
        installed: false,
        browser_name: "",
        installation_source: "",
        error: "",
      },
      install: {
        status: "",
        browser_name: "",
        trigger: "",
        attempt_count: 0,
        started_at: "",
        updated_at: "",
        completed_at: "",
        message: "",
        running: false,
      },
      readiness: {
        ready: false,
        download_ready: false,
        browser_runtime_ready: false,
        issues: [],
      },
    },
    defaults: {
      manual_import_input_dir: "/tmp/manual",
      archive_root: "",
      default_scope: {
        stored_preference: {
          record_family: "listing",
          business_id: "equity_transfer",
          exchange: "cbex",
        },
        effective_scope: {
          record_family: "listing",
          business_id: "equity_transfer",
          business_label: "股权转让",
          exchange: "cbex",
        },
        stale_resolution: {
          is_stale: false,
          reason: "",
          hint: "",
        },
      },
    },
    visibility: {
      mode: "listing_only",
      visible_families: ["listing"],
    },
  });
});

test("progress and job adapters drop undeclared summary and metric fields", () => {
  const progress = normalizeProgressResource({
    metrics: [
      { key: "downloaded_count", label: "已下载", value: 9 },
      { key: "internal_metric", label: "internal", value: 99 },
    ],
    latest_stage_summary: {
      detail_candidates: 12,
      duplicate_skipped: 3,
      business_filter_skipped: 4,
      list_date_skipped: 9,
      warning_code: "all_listed_rows_outside_date_range",
      warning_message: "9 条因日期范围被跳过",
      internal_only_count: 77,
    },
  });

  const job = normalizeJobResource({
    job_id: "job-unknown",
    job_type: "custom_pipeline",
    status: "success",
    counts: {},
    progress: {},
    result: {
      metrics: [
        { key: "internal_metric", label: "internal", value: 9 },
        { key: "pending_mapping_count", label: "待补映射", value: 2 },
      ],
    },
  });

  assert.deepEqual(progress.metrics, [
    { key: "downloaded_count", label: "已下载", value: 9 },
  ]);
  assert.deepEqual(progress.latest_stage_summary, {
    detail_candidates: 12,
    duplicate_skipped: 3,
    business_filter_skipped: 4,
    list_date_skipped: 9,
    warning_code: "all_listed_rows_outside_date_range",
    warning_message: "9 条因日期范围被跳过",
  });
  assert.deepEqual(job.result.metrics, []);
});

test("job adapters preserve manual import pending-review metrics", () => {
  const job = normalizeJobResource({
    job_id: "job-manual-1",
    job_type: "manual_import",
    status: "success_with_warnings",
    counts: {},
    progress: {},
    result: {
      metrics: [
        { key: "imported_count", label: "导入成功", value: 2 },
        { key: "pending_review_count", label: "待人工复核", value: 1 },
        { key: "pending_mapping_count", label: "待补映射", value: 0 },
        { key: "failed_count", label: "失败", value: 0 },
        { key: "internal_metric", label: "internal", value: 9 },
      ],
    },
  });

  assert.deepEqual(job.result.metrics, [
    { key: "imported_count", label: "导入成功", value: 2 },
    { key: "pending_review_count", label: "待人工复核", value: 1 },
    { key: "pending_mapping_count", label: "待补映射", value: 0 },
    { key: "failed_count", label: "失败", value: 0 },
  ]);
});

test("job adapters preserve ingest operator-attention metrics", () => {
  const job = normalizeJobResource({
    job_id: "job-one-click-attention",
    job_type: "one_click",
    status: "success_with_warnings",
    counts: {},
    progress: {},
    result: {
      metrics: [
        { key: "downloaded_count", label: "已下载", value: 30 },
        { key: "persisted_count", label: "已归档", value: 28 },
        { key: "exception_count", label: "异常", value: 1 },
        { key: "pending_review_count", label: "待人工复核", value: 1 },
        { key: "pending_mapping_count", label: "待补映射", value: 2 },
        { key: "mapping_conflict_count", label: "映射冲突", value: 3 },
        { key: "skipped_count", label: "已跳过", value: 4 },
        { key: "failed_count", label: "失败", value: 5 },
        { key: "internal_metric", label: "internal", value: 9 },
      ],
    },
  });

  assert.equal(job.status, "success_with_warnings");
  assert.deepEqual(job.result.metrics, [
    { key: "downloaded_count", label: "已下载", value: 30 },
    { key: "persisted_count", label: "已归档", value: 28 },
    { key: "exception_count", label: "异常", value: 1 },
    { key: "pending_review_count", label: "待人工复核", value: 1 },
    { key: "pending_mapping_count", label: "待补映射", value: 2 },
    { key: "mapping_conflict_count", label: "映射冲突", value: 3 },
    { key: "skipped_count", label: "已跳过", value: 4 },
    { key: "failed_count", label: "失败", value: 5 },
  ]);
});

test("job adapters preserve download archive audit result evidence", () => {
  const archiveAudit = {
    root: "/tmp/archive",
    ok: true,
    html_count: 3,
    sidecar_count: 3,
    issue_count: 0,
    issues: [],
  };

  const job = normalizeJobResource({
    job_id: "job-download-audit",
    job_type: "download_ingest",
    status: "success",
    counts: {},
    progress: {},
    result: {
      download_archive_audit: archiveAudit,
    },
  });

  assert.deepEqual(job.result.download_archive_audit, archiveAudit);
});

test("job adapters preserve public-resource result evidence and failure fields", () => {
  const publicResource = {
    status: "failed",
    record_count: 0,
    period_index: 2,
    period_total: 4,
    workbook: "/tmp/public-resource.xlsx",
    evidence_root: "/tmp/evidence",
    archive_root: "/tmp/archive",
    error_type: "public_resource_collection_failed",
    error_code: "800",
    error_message: "系统繁忙",
    failure_code: "public_resource_collection_failed",
    failure_message: "search page 1 business error 800 exhausted retries",
  };

  const job = normalizeJobResource({
    job_id: "job-public-resource",
    job_type: "one_click",
    status: "failed",
    counts: {},
    progress: {},
    result: { public_resource: publicResource },
  });

  assert.deepEqual(job.result.public_resource, publicResource);
});

test("overview adapters collapse non-actionable success_with_warnings terminal labels when warning metrics are zero", () => {
  const overview = normalizeOverviewResource({
    latest_job: {
      job_id: "job-one-click-clean",
      job_type: "one_click",
      status: "success_with_warnings",
      counts: {
        downloaded: 62,
        persisted: 62,
        exceptions: 0,
      },
      progress: {
        metrics: [
          { key: "downloaded_count", label: "已下载", value: 62 },
          { key: "persisted_count", label: "已归档", value: 62 },
          { key: "exception_count", label: "异常", value: 0 },
          { key: "pending_mapping_count", label: "待补映射", value: 0 },
          { key: "skipped_count", label: "已跳过", value: 5 },
        ],
      },
      result: {
        metrics: [
          { key: "downloaded_count", label: "已下载", value: 62 },
          { key: "persisted_count", label: "已归档", value: 62 },
          { key: "exception_count", label: "异常", value: 0 },
          { key: "pending_mapping_count", label: "待补映射", value: 0 },
          { key: "skipped_count", label: "已跳过", value: 5 },
        ],
      },
    },
    latest_progress: {
      job_status: "success_with_warnings",
      phase_code: "completed_with_warnings",
      phase_label: "已完成，但有待处理项",
      metrics: [
        { key: "downloaded_count", label: "已下载", value: 62 },
        { key: "persisted_count", label: "已归档", value: 62 },
        { key: "exception_count", label: "异常", value: 0 },
        { key: "pending_mapping_count", label: "待补映射", value: 0 },
        { key: "skipped_count", label: "已跳过", value: 5 },
      ],
    },
    record_summary: {
      state_counts: { ready: 62, pending_mapping: 0 },
      pending_mapping_count: 0,
    },
  });

  assert.equal(overview.latest_job.status, "success");
  assert.equal(overview.latest_progress.job_status, "success");
  assert.equal(overview.latest_progress.phase_code, "completed");
  assert.equal(overview.latest_progress.phase_label, "已完成");
});

test("progress, job, and overview adapters preserve business re-evaluation public metrics", () => {
  const progress = normalizeProgressResource({
    job_status: "running",
    metrics: [
      { key: "pending_review_count", label: "待人工复核", value: 3 },
      { key: "accepted_completed_count", label: "已采纳", value: 7 },
      { key: "internal_metric", label: "internal", value: 99 },
    ],
  });

  const job = normalizeJobResource({
    job_id: "job-reeval",
    job_type: "business_re_evaluation",
    status: "running",
    counts: {},
    progress: {
      metrics: [
        { key: "pending_review_count", label: "待人工复核", value: 3 },
        { key: "accepted_completed_count", label: "已采纳", value: 7 },
      ],
    },
    result: {
      metrics: [
        { key: "accepted_completed_count", label: "已采纳", value: 7 },
        { key: "skipped_count", label: "已跳过", value: 1 },
      ],
    },
  });

  const overview = normalizeOverviewResource({
    latest_job: {
      job_id: "job-reeval",
      job_type: "business_re_evaluation",
      status: "running",
      counts: {},
      progress: {
        metrics: [
          { key: "pending_review_count", label: "待人工复核", value: 3 },
        ],
      },
      result: {
        metrics: [
          { key: "accepted_completed_count", label: "已采纳", value: 7 },
        ],
      },
    },
    latest_progress: {
      job_status: "running",
      metrics: [
        { key: "pending_review_count", label: "待人工复核", value: 3 },
        { key: "accepted_completed_count", label: "已采纳", value: 7 },
      ],
    },
  });

  assert.deepEqual(progress.metrics, [
    { key: "pending_review_count", label: "待人工复核", value: 3 },
    { key: "accepted_completed_count", label: "已采纳", value: 7 },
  ]);
  assert.deepEqual(job.progress.metrics, [
    { key: "pending_review_count", label: "待人工复核", value: 3 },
    { key: "accepted_completed_count", label: "已采纳", value: 7 },
  ]);
  assert.deepEqual(job.result.metrics, [
    { key: "accepted_completed_count", label: "已采纳", value: 7 },
    { key: "skipped_count", label: "已跳过", value: 1 },
  ]);
  assert.deepEqual(overview.latest_job.progress.metrics, [
    { key: "pending_review_count", label: "待人工复核", value: 3 },
  ]);
  assert.deepEqual(overview.latest_job.result.metrics, [
    { key: "accepted_completed_count", label: "已采纳", value: 7 },
  ]);
  assert.deepEqual(overview.latest_progress.metrics, [
    { key: "pending_review_count", label: "待人工复核", value: 3 },
    { key: "accepted_completed_count", label: "已采纳", value: 7 },
  ]);
});

test("normalizeRuntimeResource no longer falls back to legacy runtime field names", () => {
  const normalized = normalizeRuntimeResource({
    browser_runtime: {
      installed: true,
      browser_name: "chromium",
      installation_source: "system",
      error: "legacy",
    },
    browser_install: {
      status: "running",
      browser_name: "chromium",
      trigger: "legacy",
      attempt_count: 1,
      started_at: "a",
      updated_at: "b",
      completed_at: "c",
      message: "legacy",
      running: true,
    },
    product_readiness: {
      ready: true,
      download_ready: true,
      browser_runtime_ready: true,
      issues: [{ code: "legacy", severity: "info", message: "legacy" }],
    },
  });

  assert.deepEqual(normalized, {
    browser: {
      installed: false,
      browser_name: "",
      installation_source: "",
      error: "",
    },
    install: {
      status: "",
      browser_name: "",
      trigger: "",
      attempt_count: 0,
      started_at: "",
      updated_at: "",
      completed_at: "",
      message: "",
      running: false,
    },
    readiness: {
      ready: false,
      download_ready: false,
      browser_runtime_ready: false,
      issues: [],
    },
  });
});

test("settings adapters only consume canonical nested settings resources", () => {
  const basic = normalizeBasicSettingsResource({
    effective_default_scope: {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
    },
    stored_preference: {
      record_family: "listing",
      business_id: "equity_transfer",
      exchange: "cbex",
    },
    stale_default_metadata: {
      is_stale: false,
      reason: "",
      hint: "",
    },
    default_exchange: "cbex",
    default_concurrency: 99,
    retention_count: 7,
    paths: {
      workspace_root: "/tmp/workspace",
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
    },
  });
  const advanced = normalizeAdvancedSettingsResource({
    effective_default_scope: {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
    },
    stored_preference: {
      record_family: "listing",
      business_id: "equity_transfer",
      exchange: "cbex",
    },
    stale_default_metadata: {
      is_stale: false,
      reason: "",
      hint: "",
    },
    processing: {
      save_json: true,
      postprocess_config: "/tmp/postprocess.json",
    },
    ingest_paths: {
      raw_manual_root: "/tmp/manual",
      raw_auto_root: "/tmp/auto",
    },
    runtime_paths: {
      app_home: "/tmp/home",
      streaming_db: "/tmp/db",
      log_dir: "/tmp/log",
      cache_dir: "/tmp/cache",
      browser_cache_dir: "/tmp/browser-cache",
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
    },
  });

  assert.deepEqual(basic, {
    effective_default_scope: {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
    },
    stored_preference: {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "",
      exchange: "cbex",
    },
    stale_default_metadata: {
      is_stale: false,
      reason: "",
      hint: "",
    },
    default_exchange: "cbex",
    default_concurrency: 99,
    retention_count: 7,
    paths: {
      workspace_root: "/tmp/workspace",
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
    },
  });
  assert.deepEqual(advanced, {
    effective_default_scope: {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
    },
    stored_preference: {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "",
      exchange: "cbex",
    },
    stale_default_metadata: {
      is_stale: false,
      reason: "",
      hint: "",
    },
    processing: {
      save_json: true,
      postprocess_config: "/tmp/postprocess.json",
    },
    ingest_paths: {
      raw_manual_root: "/tmp/manual",
      raw_auto_root: "/tmp/auto",
    },
    runtime_paths: {
      app_home: "/tmp/home",
      streaming_db: "/tmp/db",
      log_dir: "/tmp/log",
      cache_dir: "/tmp/cache",
      browser_cache_dir: "/tmp/browser-cache",
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
    },
  });
});
