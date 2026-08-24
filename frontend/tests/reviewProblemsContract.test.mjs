import test from "node:test";
import assert from "node:assert/strict";

import { buildReviewProblemsQuery, normalizeReviewProblemsResource } from "../src/contracts/reviewProblems.js";

test("normalizeReviewProblemsResource rejects explicit non-array rows", () => {
  assert.throws(
    () => normalizeReviewProblemsResource({ rows: { bad: true }, total_count: 3 }),
    /Invalid review problems resource: rows must be an array/,
  );
});

test("normalizeReviewProblemsResource preserves unknown problem kinds as contract drift and strips actions from rows", () => {
  const payload = normalizeReviewProblemsResource({
    summary: {
      total_count: 1,
      project_type_unresolved_count: 1,
      business_family_unresolved_count: 0,
      deal_data_incomplete_count: 0,
      export_fields_missing_count: 0,
      manual_review_unclassified_count: 0,
    },
    rows: [
      {
        problem_id: "rec:1:unknown:x",
        problem_kind: "unknown_kind",
        problem_label: "",
        business_explanation: "归一化解释",
        has_local_artifact: true,
        local_artifact_name: "artifact.html",
        artifact_status: "available",
        evidence_verdict: {
          status: "stale_reference",
          reason_code: "authoritative_artifact_missing",
          identity_confidence: "verified",
          authoritative_path: "/tmp/archive/missing.html",
          inspection_openable_path: "/tmp/archive/artifact.html",
          safe_evidence: { path_authority: "archive_path" },
        },
        actions: { primary_action_kind: "assign_project_type" },
        evidence: { reason_code: "x" },
      },
    ],
    returned_count: 1,
    total_count: 1,
  });

  assert.equal(payload.rows[0].problem_kind, "unknown_kind");
  assert.equal(payload.rows[0].problem_label, "未知复核类型：unknown_kind");
  assert.equal(payload.rows[0].contract_error, true);
  assert.equal(payload.rows[0].business_explanation, "归一化解释");
  assert.equal(payload.rows[0].has_local_artifact, false);
  assert.equal(payload.rows[0].local_artifact_name, "");
  assert.equal(payload.rows[0].artifact_status, "available");
  assert.equal(payload.rows[0].evidence_verdict.status, "stale_reference");
  assert.equal(payload.rows[0].evidence_verdict.reason_code, "authoritative_artifact_missing");
  assert.equal(Object.hasOwn(payload.rows[0], "actions"), false);
});

test("normalizeReviewProblemsResource does not infer local artifact presence from legacy fields without usable evidence verdict", () => {
  const payload = normalizeReviewProblemsResource({
    rows: [
      {
        problem_id: "stale",
        has_local_artifact: true,
        local_artifact_name: "legacy-source.html",
        artifact_status: "available",
        source_file: "/legacy/source.html",
        archive_path: "/missing/archive.html",
        evidence_verdict: {
          status: "stale_reference",
          reason_code: "authoritative_artifact_missing",
          authoritative_path: "/missing/archive.html",
          inspection_openable_path: "/managed/provenance.html",
        },
      },
      {
        problem_id: "verified",
        has_local_artifact: false,
        local_artifact_name: "ignored.html",
        evidence_verdict: {
          status: "verified",
          inspection_openable_path: "/managed/verified.html",
        },
      },
    ],
  });

  assert.equal(payload.rows[0].has_local_artifact, false);
  assert.equal(payload.rows[0].local_artifact_name, "");
  assert.equal(payload.rows[1].has_local_artifact, true);
  assert.equal(payload.rows[1].local_artifact_name, "verified.html");
});

test("normalizeReviewProblemsResource derives source display from verdict path instead of legacy local artifact name", () => {
  const payload = normalizeReviewProblemsResource({
    rows: [
      {
        problem_id: "legacy-only",
        has_local_artifact: true,
        local_artifact_name: "legacy-only.html",
        artifact_status: "downloaded",
      },
      {
        problem_id: "verified",
        has_local_artifact: true,
        local_artifact_name: "legacy-wrong.html",
        artifact_status: "downloaded",
        evidence_verdict: {
          status: "verified",
          inspection_openable_path: "/managed/verdict-name.html",
        },
      },
    ],
  });

  assert.equal(payload.rows[0].has_local_artifact, false);
  assert.equal(payload.rows[0].local_artifact_name, "");
  assert.equal(payload.rows[1].has_local_artifact, true);
  assert.equal(payload.rows[1].local_artifact_name, "verdict-name.html");
});

test("normalizeReviewProblemsResource exposes only safe review evidence fields", () => {
  const payload = normalizeReviewProblemsResource({
    rows: [
      {
        problem_id: "raw-evidence",
        evidence: {
          reason_code: "canonical_field_missing",
          missing_fields: ["项目名称", { field: "project_code", label: "项目编号" }],
          related_finding_count: 1,
          artifact_evidence_verdict: { status: "present_unverified", reason_code: "identity_unresolved" },
          related_findings: [
            {
              finding_type: "canonical_field_missing",
              severity: "warning",
              reason_code: "canonical_field_missing",
              fields: [{ field: "project_name", label: "项目名称" }],
              message: "UNTRUSTED_EXTERNAL_TEXT",
              evidence: { raw_html: "UNTRUSTED_EXTERNAL_TEXT" },
            },
          ],
          message: "UNTRUSTED_EXTERNAL_TEXT",
          raw_html: "UNTRUSTED_EXTERNAL_TEXT",
          ocr_text: "UNTRUSTED_EXTERNAL_TEXT",
          browser_transcript: "UNTRUSTED_EXTERNAL_TEXT",
        },
      },
    ],
  });

  assert.deepEqual(payload.rows[0].evidence, {
    reason_code: "canonical_field_missing",
    missing_fields: [
      { field: "", label: "项目名称" },
      { field: "project_code", label: "项目编号" },
    ],
    related_finding_count: 1,
    artifact_evidence_verdict: { status: "present_unverified", reason_code: "identity_unresolved" },
    related_findings: [
      {
        finding_type: "canonical_field_missing",
        severity: "warning",
        reason_code: "canonical_field_missing",
        fields: [{ field: "project_name", label: "项目名称" }],
      },
    ],
  });
  assert.doesNotMatch(JSON.stringify(payload.rows[0].evidence), /UNTRUSTED_EXTERNAL_TEXT/);
});

test("normalizeReviewProblemsResource sanitizes raw business labels from review rows", () => {
  const payload = normalizeReviewProblemsResource({
    rows: [
      {
        problem_id: "raw-business-label",
        problem_kind: "project_type_unresolved",
        reason_code: "unrecognized_business",
        raw_business_label: "UNTRUSTED_EXTERNAL_TEXT",
        business_explanation: "原始项目类型为“UNTRUSTED_EXTERNAL_TEXT”。系统目录中没有可用匹配。",
        evidence: {
          reason_code: "unrecognized_business",
          raw_business_label: "UNTRUSTED_EXTERNAL_TEXT",
        },
      },
    ],
  });

  assert.equal(payload.rows[0].raw_business_label, "项目类型未识别");
  assert.match(payload.rows[0].business_explanation, /项目类型未识别/);
  assert.match(payload.rows[0].business_explanation, /系统目录中没有可用匹配/);
  assert.doesNotMatch(JSON.stringify(payload), /UNTRUSTED_EXTERNAL_TEXT/);
});

test("normalizeReviewProblemsResource localizes known family labels and preserves unknown backend labels", () => {
  const payload = normalizeReviewProblemsResource({
    rows: [
      {
        problem_id: "listing",
        record_family: "listing",
        record_family_label: "Listing",
      },
      {
        problem_id: "deal",
        record_family: "deal",
        record_family_label: "Deal",
      },
      {
        problem_id: "archive",
        record_family: "archive",
        record_family_label: "Archive Records",
      },
    ],
  });

  assert.equal(payload.rows[0].record_family_label, "挂牌业务");
  assert.equal(payload.rows[1].record_family_label, "成交业务");
  assert.equal(payload.rows[2].record_family_label, "Archive Records");
});

test("normalizeReviewProblemsResource recognizes source artifact unavailable problems", () => {
  const payload = normalizeReviewProblemsResource({
    summary: {
      total_count: 1,
      source_artifact_unavailable_count: 1,
    },
    rows: [
      {
        problem_kind: "source_artifact_unavailable",
        artifact_status: "unresolved",
        artifact_missing_reason: "source_artifact_invalid",
        business_explanation: "本地文件不是完整原网页。",
      },
    ],
    returned_count: 1,
    total_count: 1,
  });

  assert.equal(payload.summary.source_artifact_unavailable_count, 1);
  assert.equal(payload.summary.export_fields_missing_count, 0);
  assert.equal(payload.rows[0].problem_label, "原网页不可用");
  assert.equal(payload.rows[0].contract_error, false);
  assert.equal(payload.rows[0].artifact_missing_reason, "source_artifact_invalid");
});

test("buildReviewProblemsQuery serializes filters and pagination", () => {
  assert.equal(
    buildReviewProblemsQuery({
      problem_kind: "export_fields_missing",
      state: "field_missing",
      keyword: "G32026",
      page: 2,
      page_size: 100,
    }),
    "problem_kind=export_fields_missing&state=field_missing&keyword=G32026&page=2&page_size=100",
  );
});
