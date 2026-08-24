import test from "node:test";
import assert from "node:assert/strict";

import { normalizeRecordsResource } from "../src/contracts/records.js";

test("normalizeRecordsResource rejects explicitly malformed collection DTO fields", () => {
  for (const [field, value] of [
    ["rows", {}],
    ["display_columns", "项目编号"],
    ["summary", []],
    ["scope", "listing"],
  ]) {
    assert.throws(
      () => normalizeRecordsResource({ [field]: value }),
      /Invalid records resource/,
      `${field} should not be silently normalized`,
    );
  }
});

test("normalizeRecordsResource keeps canonical business-aware scope in a records-only suite", () => {
  const normalized = normalizeRecordsResource({
    record_family: "listing",
    scope: {
      record_family: "listing",
      state: "ready",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
      keyword: "北交所",
      date_from: "2026-03-01",
      date_to: "2026-03-31",
      page: 2,
      page_size: 25,
      project_type: "legacy",
    },
    display_columns: ["项目编号", "项目名称"],
    rows: [
      {
        record_id: "rec-1",
        project_code: "CODE-1",
        project_name: "项目 1",
        project_type_code: "equity_transfer",
        project_type_label: "股权转让",
        exchange_code: "cbex",
        exchange_label: "北交所",
        listing_date: "2026-03-01",
        state: "ready",
        status_label: "已录入",
        status_detail: "导入完成",
        archive_path: "/tmp/archive/demo.html",
        source_file: "/tmp/raw/demo.html",
        artifact_status: "available",
        artifact_missing_reason: "",
        evidence_verdict: {
          status: "verified",
          reason_code: "identity_verified_artifact_present",
          logical_record_identity: "",
          identity_confidence: "verified",
          authoritative_path: "/tmp/archive/demo.html",
          inspection_openable_path: "/tmp/archive/demo.html",
          safe_evidence: { path_authority: "archive_path" },
        },
        has_local_artifact: 1,
        local_artifact_name: "demo.html",
        updated_at: "2026-04-12T12:00:00",
        seller: "转让方",
        price: "1000",
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
        canonical_ready: true,
        evidence_status: "verified",
        export_eligible: true,
        exportable: true,
        display_values: { 项目编号: "CODE-1", 项目名称: "项目 1" },
        payload: { legacy: true },
      },
    ],
    summary: {
      filtered_state_counts: { ready: 1 },
      page_state_counts: { ready: 1 },
      total_count: 11,
      visible_count: 1,
      page: 2,
      page_size: 25,
      page_count: 1,
    },
    total_count: 999,
    page_count: 999,
    has_more: true,
    db_path: "/tmp/legacy.sqlite3",
    keyword: "legacy",
  });

  assert.deepEqual(normalized, {
    record_family: "listing",
    scope: {
      record_family: "listing",
      state: "ready",
      business_id: "equity_transfer",
      business_label: "股权转让",
      exchange: "cbex",
      keyword: "北交所",
      date_from: "2026-03-01",
      date_to: "2026-03-31",
      page: 2,
      page_size: 25,
    },
    display_columns: ["项目编号", "项目名称"],
    rows: [
      {
        record_id: "rec-1",
        project_code: "CODE-1",
        project_name: "项目 1",
        project_type_code: "equity_transfer",
        project_type_label: "股权转让",
        exchange_code: "cbex",
        exchange_label: "北交所",
        listing_date: "2026-03-01",
        state: "ready",
        status_label: "已录入",
        status_detail: "导入完成",
        archive_path: "/tmp/archive/demo.html",
        source_file: "/tmp/raw/demo.html",
        artifact_status: "available",
        artifact_missing_reason: "",
        evidence_verdict: {
          status: "verified",
          reason_code: "identity_verified_artifact_present",
          logical_record_identity: "",
          identity_confidence: "verified",
          authoritative_path: "/tmp/archive/demo.html",
          inspection_openable_path: "/tmp/archive/demo.html",
          safe_evidence: { path_authority: "archive_path" },
        },
        has_local_artifact: true,
        local_artifact_name: "demo.html",
        updated_at: "2026-04-12T12:00:00",
        seller: "转让方",
        price: "1000",
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
        canonical_ready: true,
        evidence_status: "verified",
        export_eligible: true,
        exportable: true,
        display_values: { 项目编号: "CODE-1", 项目名称: "项目 1" },
      },
    ],
    summary: {
      filtered_state_counts: { ready: 1 },
      page_state_counts: { ready: 1 },
      total_count: 11,
      visible_count: 1,
      page: 2,
      page_size: 25,
      page_count: 1,
    },
    total_count: 11,
    visible_count: 1,
    page: 2,
    page_size: 25,
    page_count: 1,
    has_more: false,
  });
});

test("normalizeRecordsResource uses explicit export eligibility instead of legacy path fields", () => {
  const normalized = normalizeRecordsResource({
    rows: [
      {
        record_id: "rec-path-only",
        state: "ready",
        archive_path: "/legacy/archive.html",
        source_file: "/legacy/source.html",
        has_local_artifact: true,
        local_artifact_name: "source.html",
        evidence_verdict: {
          status: "present_unverified",
          inspection_openable_path: "/legacy/source.html",
        },
        canonical_ready: true,
        evidence_status: "present_unverified",
        export_eligible: false,
        exportable: true,
      },
      {
        record_id: "rec-shared-official-page",
        state: "ready",
        archive_path: "",
        source_file: "",
        has_local_artifact: false,
        evidence_verdict: {
          status: "shared_official_page",
          safe_evidence: { page_kind: "shared_official_page" },
        },
        canonical_ready: true,
        evidence_status: "shared_official_page",
        export_eligible: true,
      },
    ],
    summary: {
      total_count: 2,
      visible_count: 2,
      page: 1,
      page_size: 50,
      page_count: 1,
    },
  });

  const pathOnly = normalized.rows[0];
  assert.equal(pathOnly.canonical_ready, true);
  assert.equal(pathOnly.evidence_status, "present_unverified");
  assert.equal(pathOnly.export_eligible, false);
  assert.equal(pathOnly.exportable, false);

  const sharedOfficialPage = normalized.rows[1];
  assert.equal(sharedOfficialPage.canonical_ready, true);
  assert.equal(sharedOfficialPage.evidence_status, "shared_official_page");
  assert.equal(sharedOfficialPage.export_eligible, true);
  assert.equal(sharedOfficialPage.exportable, true);
});

test("normalizeRecordsResource ignores legacy raw last error text outside safe status detail", () => {
  const normalized = normalizeRecordsResource({
    rows: [
      {
        record_id: "rec-parse-failed",
        state: "parse_failed",
        status_detail: "解析失败，暂不能进入录入",
        last_error_message: "UNTRUSTED_EXTERNAL_TEXT",
      },
    ],
  });

  assert.equal(normalized.rows[0].status_detail, "解析失败，暂不能进入录入");
  assert.equal(Object.hasOwn(normalized.rows[0], "last_error_message"), false);
  assert.doesNotMatch(JSON.stringify(normalized.rows[0]), /UNTRUSTED_EXTERNAL_TEXT/);
});

test("normalizeRecordsResource does not create local artifact affordance from legacy fields without usable evidence verdict", () => {
  const normalized = normalizeRecordsResource({
    rows: [
      {
        record_id: "legacy-only",
        has_local_artifact: true,
        local_artifact_name: "legacy-only.html",
        artifact_status: "downloaded",
        archive_path: "/legacy/archive.html",
        source_file: "/legacy/source.html",
      },
      {
        record_id: "stale-no-openable",
        has_local_artifact: true,
        local_artifact_name: "stale-legacy.html",
        artifact_status: "downloaded",
        evidence_verdict: {
          status: "stale_reference",
          authoritative_path: "/missing/archive.html",
          inspection_openable_path: "",
          reason_code: "authoritative_artifact_missing",
        },
      },
    ],
  });

  assert.equal(normalized.rows[0].has_local_artifact, false);
  assert.equal(normalized.rows[0].local_artifact_name, "");
  assert.equal(normalized.rows[1].has_local_artifact, false);
  assert.equal(normalized.rows[1].local_artifact_name, "");
});

test("normalizeRecordsResource derives local artifact name from usable verdict path instead of legacy name", () => {
  const normalized = normalizeRecordsResource({
    rows: [
      {
        record_id: "verified",
        has_local_artifact: true,
        local_artifact_name: "legacy-wrong.html",
        artifact_status: "downloaded",
        evidence_verdict: {
          status: "verified",
          inspection_openable_path: "/managed/verdict-name.html",
          reason_code: "identity_verified_artifact_present",
        },
      },
      {
        record_id: "shared-official",
        has_local_artifact: false,
        local_artifact_name: "legacy-missing.html",
        evidence_verdict: {
          status: "shared_official_page",
          inspection_openable_path: "/managed/shared-name.html",
          safe_evidence: { page_kind: "shared_official_page" },
        },
      },
    ],
  });

  assert.equal(normalized.rows[0].has_local_artifact, true);
  assert.equal(normalized.rows[0].local_artifact_name, "verdict-name.html");
  assert.equal(normalized.rows[1].has_local_artifact, true);
  assert.equal(normalized.rows[1].local_artifact_name, "shared-name.html");
});

test("normalizeRecordsResource keeps deal family scope and deal display payload", () => {
  const normalized = normalizeRecordsResource({
    record_family: "deal",
    scope: {
      record_family: "deal",
      state: "all",
      business_id: "deal_equity_transfer",
      business_label: "股权转让成交",
      exchange: "cbex",
      keyword: "协议转让",
      date_from: "2026-04-20",
      date_to: "2026-04-20",
      page: 1,
      page_size: 50,
    },
    display_columns: ["项目编号", "项目名称", "成交日期", "金额"],
    rows: [
      {
        record_id: "rec-deal-1",
        project_code: "D32026BJ000001",
        project_name: "北交所成交项目",
        project_type_code: "",
        project_type_label: "",
        exchange_code: "cbex",
        exchange_label: "北交所",
        listing_date: "2026-04-20",
        state: "ready",
        status_label: "已录入",
        status_detail: "导入完成",
        archive_path: "/tmp/archive/deal.html",
        source_file: "/tmp/raw/deal.html",
        has_local_artifact: true,
        local_artifact_name: "deal.html",
        updated_at: "2026-04-20T12:00:00",
        seller: "",
        price: "8800",
        display_values: { 项目编号: "D32026BJ000001", 项目名称: "北交所成交项目", 成交日期: "2026-04-20", 金额: "8800" },
      },
    ],
    summary: {
      filtered_state_counts: { ready: 1 },
      page_state_counts: { ready: 1 },
      total_count: 1,
      visible_count: 1,
      page: 1,
      page_size: 50,
      page_count: 1,
    },
  });

  assert.equal(normalized.record_family, "deal");
  assert.equal(normalized.scope.record_family, "deal");
  assert.equal(normalized.scope.business_id, "deal_equity_transfer");
  assert.equal(normalized.scope.exchange, "cbex");
  assert.equal(normalized.rows[0].listing_date, "2026-04-20");
  assert.deepEqual(normalized.display_columns, ["项目编号", "项目名称", "成交日期", "金额"]);
});
