import test from "node:test";
import assert from "node:assert/strict";

import {
  buildManualImportRequest,
  buildMappingConflictResolutionRequest,
  buildMappingRequest,
  buildOneClickRequest,
  buildPathOpenRequest,
  buildPathSelectionRequest,
  buildRuntimeInstallRequest,
} from "../src/contracts/actionRequests.js";

test("buildOneClickRequest keeps only explicit family-aware overrides and coerces numeric fields", () => {
  assert.deepEqual(
    buildOneClickRequest({
      start_date: "2026-04-01",
      end_date: "2026-04-02",
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
      project_type: "legacy",
      max_pages: "3",
      concurrency: "5",
      no_resume: true,
      save_json: false,
      postprocess_config: " /tmp/postprocess.json ",
      verbose: true,
      ignored: "legacy",
    }),
    {
      start_date: "2026-04-01",
      end_date: "2026-04-02",
      record_family: "listing",
      business_id: "equity_transfer",
      exchange: "cbex",
      max_pages: 3,
      concurrency: 5,
      no_resume: true,
      save_json: false,
      postprocess_config: "/tmp/postprocess.json",
      verbose: true,
    },
  );
});

test("buildOneClickRequest rejects incomplete default scope payloads but allows all-business or all-exchange scopes", () => {
  assert.throws(() => buildOneClickRequest({}), /default scope/i);
  assert.throws(
    () => buildOneClickRequest({ record_family: "listing", business_id: "equity_transfer" }),
    /default scope/i,
  );
  assert.deepEqual(
    buildOneClickRequest({
      record_family: "listing",
      business_id: "all",
      exchange: "all",
    }),
    {
      record_family: "listing",
      business_id: "all",
      exchange: "all",
    },
  );
});

test("buildOneClickRequest preserves multi-family scope plans without requiring a single top-level scope", () => {
  assert.deepEqual(
    buildOneClickRequest({
      start_date: "2026-04-01",
      end_date: "2026-05-10",
      max_pages: "2",
      include_public_resource: true,
      family_scopes: [
        {
          record_family: "listing",
          business_id: "all",
          business_label: "",
          exchange: "all",
        },
        {
          record_family: "deal",
          business_id: "all",
          business_label: "",
          exchange: "all",
        },
      ],
    }),
    {
      start_date: "2026-04-01",
      end_date: "2026-05-10",
      max_pages: 2,
      include_public_resource: true,
      family_scopes: [
        {
          record_family: "listing",
          business_id: "all",
          exchange: "all",
        },
        {
          record_family: "deal",
          business_id: "all",
          exchange: "all",
        },
      ],
    },
  );
});

test("buildOneClickRequest rejects incomplete family scope entries", () => {
  assert.throws(
    () => buildOneClickRequest({
      record_family: "listing",
      business_id: "equity_transfer",
      exchange: "cbex",
      include_public_resource: "true",
    }),
    /include_public_resource.*boolean/i,
  );
  assert.throws(
    () => buildOneClickRequest({
      family_scopes: [
        {
          record_family: "listing",
          business_id: "all",
        },
      ],
    }),
    /family_scopes/i,
  );
});

test("buildOneClickRequest keeps explicit single-family scope when no family scope plan is provided", () => {
  assert.deepEqual(
    buildOneClickRequest({
      start_date: "2026-04-01",
      end_date: "2026-05-10",
      record_family: "listing",
      business_id: "physical_asset",
      exchange: "cbex",
      max_pages: "1",
      concurrency: "2",
    }),
    {
      start_date: "2026-04-01",
      end_date: "2026-05-10",
      record_family: "listing",
      business_id: "physical_asset",
      exchange: "cbex",
      max_pages: 1,
      concurrency: 2,
    },
  );
});

test("buildManualImportRequest trims the input directory", () => {
  assert.deepEqual(buildManualImportRequest(" /tmp/manual-html "), { input_dir: "/tmp/manual-html" });
});

test("buildManualImportRequest preserves explicit manual-import scope when the operator provides it", () => {
  assert.deepEqual(
    buildManualImportRequest({
      input_dir: " /tmp/manual-html ",
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "sse",
      ignored: "legacy",
    }),
    {
      input_dir: "/tmp/manual-html",
      record_family: "listing",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "sse",
    },
  );
});

test("buildManualImportRequest rejects incomplete explicit manual-import scope", () => {
  assert.throws(
    () => buildManualImportRequest({
      input_dir: "/tmp/manual-html",
      business_id: "equity_transfer",
      exchange: "sse",
    }),
    /record_family and business_id/i,
  );
  assert.throws(
    () => buildManualImportRequest({
      input_dir: "/tmp/manual-html",
      record_family: "listing",
      exchange: "sse",
    }),
    /record_family and business_id/i,
  );
});

test("buildMappingRequest keeps only canonical mapping draft fields", () => {
  assert.deepEqual(
    buildMappingRequest({
      entry_id: "entry-1",
      rule_kind: "group_type",
      source_name: "华润",
      target_value: "央企",
      notes: " new note ",
      confirm_overwrite: true,
      match_field: "group",
      target_field: "source_type",
      extra_field: "legacy",
    }),
    {
      entry_id: "entry-1",
      rule_kind: "group_type",
      source_name: "华润",
      target_value: "央企",
      notes: "new note",
      confirm_overwrite: true,
    },
  );
});

test("action request builders reject malformed boolean fields instead of coercing string false", () => {
  assert.throws(
    () => buildOneClickRequest({
      record_family: "listing",
      business_id: "equity_transfer",
      exchange: "cbex",
      no_resume: "false",
    }),
    /no_resume.*boolean/i,
  );
  assert.throws(
    () => buildOneClickRequest({
      record_family: "listing",
      business_id: "equity_transfer",
      exchange: "cbex",
      save_json: "false",
    }),
    /save_json.*boolean/i,
  );
  assert.throws(
    () => buildOneClickRequest({
      record_family: "listing",
      business_id: "equity_transfer",
      exchange: "cbex",
      verbose: "false",
    }),
    /verbose.*boolean/i,
  );
  assert.throws(
    () => buildMappingRequest({
      rule_kind: "group_type",
      source_name: "中铁",
      target_value: "央企",
      confirm_overwrite: "false",
    }),
    /confirm_overwrite.*boolean/i,
  );
  assert.throws(
    () => buildMappingConflictResolutionRequest({
      record_id: "rec-1",
      confirm_overwrite: "false",
      selected_resolution: {
        rule_kind: "group_type",
        source_name: "中铁",
        target_value: "央企",
      },
    }),
    /confirm_overwrite.*boolean/i,
  );
  assert.throws(
    () => buildPathOpenRequest({ path: "/tmp/demo", reveal: "false" }),
    /reveal.*boolean/i,
  );
  assert.throws(
    () => buildPathOpenRequest({ path: "/tmp/demo", reveal: 1 }),
    /reveal.*boolean/i,
  );
});

test("buildMappingConflictResolutionRequest canonicalizes nested selected resolution", () => {
  assert.deepEqual(
    buildMappingConflictResolutionRequest({
      record_id: "rec-1",
      notes: "人工裁决",
      confirm_overwrite: false,
      selected_resolution: {
        field: "source_type",
        label: "央企",
        title: "集团 -> 类型",
        rule_kind: "group_type",
        source_name: "中铁",
        target_value: "央企",
        legacy: true,
      },
    }),
    {
      record_id: "rec-1",
      selected_resolution: {
        field: "source_type",
        label: "央企",
        title: "集团 -> 类型",
        notes: "",
        rule_kind: "group_type",
        source_name: "中铁",
        target_value: "央企",
        confirm_overwrite: false,
      },
      notes: "人工裁决",
      confirm_overwrite: false,
    },
  );
});

test("path and runtime request builders keep canonical fields only", () => {
  assert.deepEqual(
    buildPathSelectionRequest({ selection_kind: "directory", prompt: "选择目录", current_path: " /tmp/demo " }),
    { selection_kind: "directory", prompt: "选择目录", current_path: "/tmp/demo" },
  );
  assert.deepEqual(buildPathOpenRequest({ path: " /tmp/demo ", reveal: true, ignored: true }), { path: "/tmp/demo", reveal: true });
  assert.deepEqual(buildRuntimeInstallRequest({ browser_name: "chromium", trigger: "auto", ignored: true }), { browser_name: "chromium", trigger: "auto" });
});
