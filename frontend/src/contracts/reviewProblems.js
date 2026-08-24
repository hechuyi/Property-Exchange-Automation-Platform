import { recordFamilyLabel } from "../constants/index.js";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asText(value) {
  return String(value ?? "").trim();
}

function asInt(value) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

export const REVIEW_PROBLEM_KINDS = [
  "project_type_unresolved",
  "business_family_unresolved",
  "deal_data_incomplete",
  "source_artifact_unavailable",
  "export_fields_missing",
  "manual_review_unclassified",
];

export const REVIEW_PROBLEM_LABELS = {
  project_type_unresolved: "项目类型待确认",
  business_family_unresolved: "业务大类待确认",
  deal_data_incomplete: "成交数据待复核",
  source_artifact_unavailable: "原网页不可用",
  export_fields_missing: "导出必填字段缺失",
  manual_review_unclassified: "未归类复核事项",
};

const UNRESOLVED_PROJECT_TYPE_LABEL = "项目类型未识别";
const PROJECT_TYPE_TEMPLATE_MISSING_LABEL = "项目类型映射模板缺失";

function normalizeProblemKind(value) {
  const kind = asText(value);
  return REVIEW_PROBLEM_KINDS.includes(kind) ? kind : kind;
}

function normalizeEvidenceVerdict(value = {}) {
  const source = asObject(value);
  return {
    status: asText(source.status),
    logical_record_identity: asText(source.logical_record_identity),
    identity_confidence: asText(source.identity_confidence),
    authoritative_path: asText(source.authoritative_path),
    inspection_openable_path: asText(source.inspection_openable_path),
    reason_code: asText(source.reason_code),
    safe_evidence: asObject(source.safe_evidence),
  };
}

function normalizeFieldRef(value = {}) {
  if (!asObject(value) || Object.keys(asObject(value)).length === 0) {
    return { field: "", label: asText(value) };
  }
  const source = asObject(value);
  const field = asText(source.field || source.canonical_field || source.export_field || source.name);
  const label = asText(source.label || source.name || field);
  return { field, label };
}

function normalizeFieldRefs(value) {
  return asArray(value)
    .map((item) => normalizeFieldRef(item))
    .filter((item) => item.field || item.label);
}

function normalizeRelatedFinding(value = {}) {
  const source = asObject(value);
  const fields = normalizeFieldRefs(source.fields || source.missing_fields);
  return {
    finding_type: asText(source.finding_type || source.type),
    severity: asText(source.severity) || "warning",
    reason_code: asText(source.reason_code),
    ...(fields.length ? { fields } : {}),
  };
}

function normalizeReviewEvidence(value = {}) {
  const source = asObject(value);
  const verdict = asObject(source.artifact_evidence_verdict);
  return {
    reason_code: asText(source.reason_code),
    missing_fields: normalizeFieldRefs(source.missing_fields),
    related_finding_count: asInt(source.related_finding_count),
    artifact_evidence_verdict: {
      status: asText(verdict.status),
      reason_code: asText(verdict.reason_code),
    },
    related_findings: asArray(source.related_findings).map((item) => normalizeRelatedFinding(item)),
  };
}

function normalizeReviewBusinessLabel(source, problemKind, reasonCode) {
  const businessLabel = asText(source.business_label);
  if (businessLabel) return businessLabel;
  if (problemKind === "project_type_unresolved" && reasonCode === "project_type_mapping_template_missing") {
    return PROJECT_TYPE_TEMPLATE_MISSING_LABEL;
  }
  return problemKind === "project_type_unresolved" ? UNRESOLVED_PROJECT_TYPE_LABEL : "";
}

function normalizeBusinessExplanation(source, problemKind, reasonCode) {
  if (problemKind !== "project_type_unresolved") return asText(source.business_explanation);
  if (reasonCode === "project_type_mapping_template_missing") {
    return "项目类型映射模板缺失。这是配置问题，不是单条记录内容错误。";
  }
  return `${UNRESOLVED_PROJECT_TYPE_LABEL}。系统目录中没有可用匹配，当前不能判断应使用哪套业务规则和导出表头。`;
}

const REVIEW_LOCAL_ARTIFACT_STATUSES = new Set(["verified", "present_unverified", "shared_official_page"]);

function basename(pathValue) {
  const text = asText(pathValue);
  if (!text) return "";
  return text.split(/[\\/]+/).filter(Boolean).at(-1) || "";
}

function normalizeReviewProblemRow(row = {}) {
  const source = asObject(row);
  const problemKind = normalizeProblemKind(source.problem_kind);
  const knownProblemKind = REVIEW_PROBLEM_KINDS.includes(problemKind);
  const evidenceVerdict = normalizeEvidenceVerdict(source.evidence_verdict);
  const reasonCode = asText(source.reason_code);
  const recordFamily = asText(source.record_family);
  const verdictLocalArtifactName = REVIEW_LOCAL_ARTIFACT_STATUSES.has(evidenceVerdict.status)
    ? basename(evidenceVerdict.inspection_openable_path)
    : "";
  return {
    problem_id: asText(source.problem_id),
    record_id: asText(source.record_id),
    revision_id: asInt(source.revision_id),
    state: asText(source.state),
    status_label: asText(source.status_label) || "待人工复核",
    problem_kind: problemKind,
    problem_label: asText(source.problem_label) || (knownProblemKind ? REVIEW_PROBLEM_LABELS[problemKind] : `未知复核类型：${problemKind || "未声明"}`),
    contract_error: !knownProblemKind,
    reason_code: reasonCode,
    severity: asText(source.severity) || "warning",
    business_explanation: normalizeBusinessExplanation(source, problemKind, reasonCode),
    business_impact: asText(source.business_impact) || "该记录暂不能进入导出。",
    suggested_review: asText(source.suggested_review),
    record_family: recordFamily,
    record_family_label: recordFamilyLabel(recordFamily, asText(source.record_family_label)),
    business_id: asText(source.business_id),
    business_label: asText(source.business_label),
    raw_business_label: normalizeReviewBusinessLabel(source, problemKind, reasonCode),
    project_code: asText(source.project_code),
    project_name: asText(source.project_name),
    exchange_code: asText(source.exchange_code),
    exchange_label: asText(source.exchange_label),
    source_file: asText(source.source_file),
    archive_path: asText(source.archive_path),
    artifact_status: asText(source.artifact_status),
    artifact_missing_reason: asText(source.artifact_missing_reason),
    evidence_verdict: evidenceVerdict,
    has_local_artifact: Boolean(verdictLocalArtifactName),
    local_artifact_name: verdictLocalArtifactName,
    updated_at: asText(source.updated_at),
    evidence: normalizeReviewEvidence(source.evidence),
  };
}

function normalizeSummary(summary = {}) {
  const source = asObject(summary);
  return {
    total_count: asInt(source.total_count),
    project_type_unresolved_count: asInt(source.project_type_unresolved_count),
    business_family_unresolved_count: asInt(source.business_family_unresolved_count),
    deal_data_incomplete_count: asInt(source.deal_data_incomplete_count),
    source_artifact_unavailable_count: asInt(source.source_artifact_unavailable_count),
    export_fields_missing_count: asInt(source.export_fields_missing_count),
    manual_review_unclassified_count: asInt(source.manual_review_unclassified_count),
  };
}

export function buildReviewProblemsQuery(params = {}) {
  const source = asObject(params);
  const query = new URLSearchParams();
  [
    "problem_kind",
    "record_family",
    "business_id",
    "exchange",
    "state",
    "keyword",
    "date_from",
    "date_to",
    "page",
    "page_size",
  ].forEach((key) => {
    const value = asText(source[key]);
    if (value) query.set(key, value);
  });
  return query.toString();
}

export function normalizeReviewProblemsResource(resource = {}) {
  const source = asObject(resource);
  if (Object.hasOwn(source, "rows") && !Array.isArray(source.rows)) {
    throw new Error("Invalid review problems resource: rows must be an array");
  }
  return {
    summary: normalizeSummary(source.summary),
    rows: asArray(source.rows).map((row) => normalizeReviewProblemRow(row)),
    returned_count: asInt(source.returned_count),
    total_count: asInt(source.total_count),
    truncated: Boolean(source.truncated),
  };
}
