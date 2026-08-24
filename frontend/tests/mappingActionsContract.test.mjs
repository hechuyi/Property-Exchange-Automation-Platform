import test from "node:test";
import assert from "node:assert/strict";

import {
  normalizeMappingPreviewResult,
  normalizeMappingSaveResult,
  normalizeMappingDeleteResult,
  normalizeMappingConflictResolutionResult,
  normalizeMappingUndoResult,
} from "../src/contracts/mappingActions.js";

test("normalizeMappingPreviewResult keeps only canonical preview fields", () => {
  const normalized = normalizeMappingPreviewResult({
    conflict: true,
    mode: "overwrite",
    existing_entry: {
      entry_id: "entry-1",
      rule_title: "集团 -> 类型",
      source_name: "中铁集团",
      company_name: "legacy-company",
      target_value: "央企",
      metadata: { legacy: true },
    },
    affected_count: 3,
    affected_pending_count: 2,
    match_field: "group_name",
    target_field: "source_type",
    target_value: "央企",
    source_name: "中铁集团",
    rule_kind: "group_source_type",
    rule_title: "集团 -> 类型",
    source_label: "集团名称",
    target_label: "主体类型",
    scope_miss: false,
    scope_miss_message: "",
    payload: { legacy: true },
  });

  assert.deepEqual(normalized, {
    conflict: true,
    mode: "overwrite",
    existing_entry: {
      entry_id: "entry-1",
      rule_title: "集团 -> 类型",
      source_name: "中铁集团",
      target_value: "央企",
    },
    affected_count: 3,
    affected_pending_count: 2,
    match_field: "group_name",
    target_field: "source_type",
    target_value: "央企",
    source_name: "中铁集团",
    rule_kind: "group_source_type",
    rule_title: "集团 -> 类型",
    source_label: "集团名称",
    target_label: "主体类型",
    scope_miss: false,
    scope_miss_message: "",
  });
});

test("normalizeMappingSaveResult keeps refresh result fields and canonical preview data", () => {
  const normalized = normalizeMappingSaveResult({
    entry_id: "entry-1",
    job_id: "job-1",
    job_type: "mapping_refresh",
    affected_count: 3,
    conflict: false,
    mode: "create",
    existing_entry: {},
    affected_pending_count: 1,
    match_field: "transferor",
    target_field: "group_name",
    target_value: "中铁集团",
    source_name: "中铁",
    rule_kind: "transferor_group",
    rule_title: "转让方 -> 集团",
    source_label: "转让方名称",
    target_label: "集团名称",
    scope_miss: true,
    scope_miss_message: "当前记录范围内未命中",
    payload: { legacy: true },
  });

  assert.deepEqual(normalized, {
    entry_id: "entry-1",
    job_id: "job-1",
    job_type: "mapping_refresh",
    affected_count: 3,
    conflict: false,
    mode: "create",
    existing_entry: {
      entry_id: "",
      rule_title: "",
      source_name: "",
      target_value: "",
    },
    affected_pending_count: 1,
    match_field: "transferor",
    target_field: "group_name",
    target_value: "中铁集团",
    source_name: "中铁",
    rule_kind: "transferor_group",
    rule_title: "转让方 -> 集团",
    source_label: "转让方名称",
    target_label: "集团名称",
    scope_miss: true,
    scope_miss_message: "当前记录范围内未命中",
  });
});

test("normalizeMappingDeleteResult rejects explicit not-deleted business result", () => {
  assert.throws(
    () => normalizeMappingDeleteResult({
      entry_id: "entry-1",
      deleted: false,
    }),
    /删除规则未成功/,
  );
});

test("normalizeMappingUndoResult keeps the current-session undo result", () => {
  assert.deepEqual(
    normalizeMappingUndoResult({
      undone: true,
      undo_kind: "update",
      entry_id: "entry-1",
      startup_session_id: "must-not-leak-back",
    }),
    {
      undone: true,
      undo_kind: "update",
      entry_id: "entry-1",
    },
  );
});

test("normalizeMappingConflictResolutionResult keeps only canonical resolution fields", () => {
  const normalized = normalizeMappingConflictResolutionResult({
    job_id: "job-1",
    job_type: "mapping_refresh",
    affected_count: 3,
    record_id: "rec-1",
    resolution_mode: "rule_saved_and_refresh_started",
    resolution: {
      field: "source_type",
      rule_kind: "group_source_type",
      source_name: "中铁集团",
      target_value: "央企",
      payload: { legacy: true },
    },
    payload: { legacy: true },
  });

  assert.deepEqual(normalized, {
    job_id: "job-1",
    job_type: "mapping_refresh",
    affected_count: 3,
    record_id: "rec-1",
    resolution_mode: "rule_saved_and_refresh_started",
    resolution: {
      field: "source_type",
      rule_kind: "group_source_type",
      source_name: "中铁集团",
      target_value: "央企",
    },
  });
});

test("normalizeMappingConflictResolutionResult preserves mapping-resolution launch vocabulary", () => {
  const normalized = normalizeMappingConflictResolutionResult({
    job_id: "job-2",
    job_type: "mapping_refresh",
    affected_count: 4,
    record_id: "rec-2",
    resolution_mode: "rule_saved_and_refresh_started",
    blocker_kind: "mapping_resolution",
    queue_section: "mapping_resolution",
    resolution: {
      field: "source_type",
      rule_kind: "group_source_type",
      source_name: "中铁集团",
      target_value: "央企",
    },
  });

  assert.deepEqual(normalized, {
    job_id: "job-2",
    job_type: "mapping_refresh",
    affected_count: 4,
    record_id: "rec-2",
    resolution_mode: "rule_saved_and_refresh_started",
    blocker_kind: "mapping_resolution",
    queue_section: "mapping_resolution",
    resolution: {
      field: "source_type",
      rule_kind: "group_source_type",
      source_name: "中铁集团",
      target_value: "央企",
    },
  });
});
