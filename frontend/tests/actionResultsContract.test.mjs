import test from "node:test";
import assert from "node:assert/strict";

import {
  normalizeExportActionResult,
  normalizePathOpenResult,
  normalizePathSelectionResult,
  normalizeRecordFieldMissingAckResult,
  normalizeRecordRevealResult,
  normalizeRuntimeInstallResult,
  normalizeStreamingJobLaunchResult,
} from "../src/contracts/actionResults.js";

test("normalizeStreamingJobLaunchResult preserves structured business identity when provided", () => {
  const normalized = normalizeStreamingJobLaunchResult({
    job_id: "job-1",
    job_type: "manual_import",
    db_path: "/tmp/db.sqlite3",
    input_dir: "/tmp/manual",
    discovered_count: 7,
    affected_count: 99,
    record_family: "listing",
    business_id: "equity_transfer",
    business_label: "股权转让",
    scope: {
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
    },
    payload: { legacy: true },
  });

  assert.deepEqual(normalized, {
    job_id: "job-1",
    job_type: "manual_import",
    db_path: "/tmp/db.sqlite3",
    input_dir: "/tmp/manual",
    discovered_count: 7,
    affected_count: 99,
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
  });
});

test("normalizeExportActionResult keeps only canonical export summary fields", () => {
  const normalized = normalizeExportActionResult({
    job_id: "job-1",
    job_type: "export_excel",
    status: "empty",
    message: "当前条件下没有可导出的记录；记录冲突 2 条",
    failure_code: "",
    failure_message: "",
    empty_reason_code: "conflict_blocked",
    scope_state_counts: { conflict: 2 },
    scope: {
      record_family: "listing",
      state: "ready",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
      keyword: "",
      date_from: "",
      date_to: "",
      page: 1,
      page_size: 50,
    },
    export_id: "exp-1",
    cursor_id: "cursor-1",
    requested_export_mode: "full",
    revision_watermark: 42,
    field_missing_blocked_records: 0,
    field_missing_diagnostics: [
      {
        record_id: "rec-missing",
        revision_id: 7,
        record_family: "listing",
        business_id: "physical_asset",
        failure_code: "export_field_missing",
        missing_fields: [
          {
            kind: "export",
            field: "类型",
            canonical_field: "source_type",
            export_field: "类型",
            message: "export field 类型 is required",
          },
        ],
      },
    ],
    incomplete_diagnostics: [{ record_id: "legacy" }],
    new_records: 3,
    changed_records: 1,
    artifacts: ["/tmp/export.xlsx"],
    payload: { legacy: true },
  });

  assert.deepEqual(normalized, {
    job_id: "job-1",
    job_type: "export_excel",
    status: "empty",
    message: "当前条件下没有可导出的记录；记录冲突 2 条",
    failure_code: "",
    failure_message: "",
    empty_reason_code: "conflict_blocked",
    scope_state_counts: { conflict: 2 },
    scope: {
      record_family: "listing",
      state: "ready",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
      keyword: "",
      date_from: "",
      date_to: "",
      page: 1,
      page_size: 50,
    },
    export_id: "exp-1",
    cursor_id: "cursor-1",
    requested_export_mode: "full",
    revision_watermark: 42,
    field_missing_blocked_records: 0,
    field_missing_diagnostics: [
      {
        record_id: "rec-missing",
        revision_id: 7,
        record_family: "listing",
        business_id: "physical_asset",
        failure_code: "export_field_missing",
        missing_fields: [
          {
            kind: "export",
            field: "类型",
            canonical_field: "source_type",
            export_field: "类型",
            message: "export field 类型 is required",
          },
        ],
      },
    ],
    new_records: 3,
    changed_records: 1,
    artifacts: ["/tmp/export.xlsx"],
  });
});

test("normalizePathSelectionResult keeps only canonical picker fields", () => {
  const normalized = normalizePathSelectionResult({
    selected: true,
    path: "/tmp/chosen",
    selection_kind: "directory",
    payload: { legacy: true },
  });

  assert.deepEqual(normalized, {
    selected: true,
    path: "/tmp/chosen",
    selection_kind: "directory",
  });
});

test("normalizePathOpenResult and normalizeRecordRevealResult keep only canonical file-manager fields", () => {
  assert.deepEqual(
    normalizePathOpenResult({
      opened: true,
      path: "/tmp/demo",
      reveal: false,
      payload: { legacy: true },
    }),
    {
      opened: true,
      path: "/tmp/demo",
      reveal: false,
    },
  );

  assert.deepEqual(
    normalizeRecordRevealResult({
      opened: true,
      record_id: "rec-1",
      path: "/tmp/archive/demo.html",
      artifact_name: "demo.html",
      payload: { legacy: true },
    }),
    {
      opened: true,
      record_id: "rec-1",
      path: "/tmp/archive/demo.html",
      artifact_name: "demo.html",
    },
  );
});

test("normalizeRecordFieldMissingAckResult keeps noise-only acknowledgement contract", () => {
  const normalized = normalizeRecordFieldMissingAckResult({
    record_id: "rec-field-missing",
    state: "field_missing",
    exportable: false,
    field_missing_acknowledgement: {
      acknowledged: true,
      missing_fields_hash: "hash-1",
      revision_id: 7,
    },
    attention: {
      requires_attention: false,
      suppressed: true,
      reason: "acknowledged",
    },
    legacy: true,
  });

  assert.deepEqual(normalized, {
    record_id: "rec-field-missing",
    state: "field_missing",
    exportable: false,
    field_missing_acknowledgement: {
      acknowledged: true,
      missing_fields_hash: "hash-1",
      revision_id: 7,
      missing_fields: [],
    },
    attention: {
      requires_attention: false,
      suppressed: true,
      reason: "acknowledged",
    },
  });
});

test("normalizeRuntimeInstallResult keeps only canonical runtime-install fields", () => {
  const normalized = normalizeRuntimeInstallResult({
    status: "running",
    browser_name: "chromium",
    trigger: "manual",
    attempt_count: 2,
    started_at: "2026-04-12T12:00:00",
    updated_at: "2026-04-12T12:00:01",
    completed_at: "",
    message: "Installing chromium",
    running: true,
    payload: { legacy: true },
  });

  assert.deepEqual(normalized, {
    status: "running",
    browser_name: "chromium",
    trigger: "manual",
    attempt_count: 2,
    started_at: "2026-04-12T12:00:00",
    updated_at: "2026-04-12T12:00:01",
    completed_at: "",
    message: "Installing chromium",
    running: true,
  });
});
