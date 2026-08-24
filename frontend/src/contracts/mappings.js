function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function requireArrayField(source, fieldName, context) {
  const value = source[fieldName];
  if (value == null) return [];
  if (Array.isArray(value)) return value;
  throw new TypeError(`${context}: ${fieldName} must be an array`);
}

function asInt(value) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

const UNKNOWN_BUSINESS_LABEL = "未识别项目类型";
const UNTRUSTED_EXTERNAL_TEXT = "UNTRUSTED_EXTERNAL_TEXT";

function asText(value) {
  return String(value || "").trim();
}

function unsafeBusinessValues(source = {}) {
  return new Set([
    asText(source.raw_business_label),
    asText(source.business_label) === UNTRUSTED_EXTERNAL_TEXT ? UNTRUSTED_EXTERNAL_TEXT : "",
  ].filter(Boolean));
}

function safeDisplayText(value, fallback = "", unsafeValues = new Set()) {
  const text = asText(value);
  return text === UNTRUSTED_EXTERNAL_TEXT || unsafeValues.has(text) ? fallback : text;
}

function safeBusinessLabel(source = {}) {
  const text = safeDisplayText(source.business_label, "", unsafeBusinessValues(source));
  return text || (asText(source.raw_business_label) ? UNKNOWN_BUSINESS_LABEL : "");
}

export function normalizeEvidenceEntry(entry, unsafeValues = new Set()) {
  if (typeof entry === "string") {
    return safeDisplayText(entry, "", unsafeValues);
  }
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
    return safeDisplayText(entry, "", unsafeValues);
  }

  const source = asObject(entry);
  const orderedFields = [
    "label",
    "match_field",
    "target_field",
    "source_name",
    "target_value",
    "text",
    "code",
    "type",
    "source",
    "reason",
  ];
  const fragments = orderedFields
    .map((field) => {
      const value = source[field];
      const text = safeDisplayText(typeof value === "string" ? value : String(value || ""), "", unsafeValues);
      return text ? `${field}: ${text}` : "";
    })
    .filter(Boolean);

  return fragments.join("; ");
}

function normalizeRecommendedRule(rule = {}, unsafeValues = new Set()) {
  const source = asObject(rule);
  return {
    rule_kind: safeDisplayText(source.rule_kind, "", unsafeValues),
    title: safeDisplayText(source.title, "", unsafeValues),
    source_name: safeDisplayText(source.source_name, "", unsafeValues),
    target_value: safeDisplayText(source.target_value, "", unsafeValues),
  };
}

function normalizeCandidateResolution(item = {}, unsafeValues = new Set()) {
  const source = asObject(item);
  return {
    field: safeDisplayText(source.field, "", unsafeValues),
    rule_kind: safeDisplayText(source.rule_kind, "", unsafeValues),
    match_field: safeDisplayText(source.match_field, "", unsafeValues),
    target_field: safeDisplayText(source.target_field, "", unsafeValues),
    source_name: safeDisplayText(source.source_name, "", unsafeValues),
    target_value: safeDisplayText(source.target_value, "", unsafeValues),
    label: safeDisplayText(source.label, "", unsafeValues),
    title: safeDisplayText(source.title, "", unsafeValues),
    evidence_chain: asArray(source.evidence_chain).map((entry) => normalizeEvidenceEntry(entry, unsafeValues)).filter(Boolean),
  };
}

function normalizeMappingEntry(entry = {}) {
  const source = asObject(entry);
  return {
    entry_id: String(source.entry_id || "").trim(),
    rule_kind: String(source.rule_kind || "").trim(),
    rule_title: String(source.rule_title || "").trim(),
    source_name: String(source.source_name || "").trim(),
    target_value: String(source.target_value || "").trim(),
    match_field: String(source.match_field || "").trim(),
    target_field: String(source.target_field || "").trim(),
    notes: String(source.notes || "").trim(),
    updated_at: String(source.updated_at || "").trim(),
  };
}

function normalizePendingMapping(item = {}, unsafeValues = new Set()) {
  const source = asObject(item);
  return {
    record_id: String(source.record_id || "").trim(),
    revision_id: Number.parseInt(source.revision_id, 10) || 0,
    project_code: String(source.project_code || "").trim(),
    project_name: String(source.project_name || "").trim(),
    project_type_code: String(source.project_type_code || "").trim(),
    project_type_label: String(source.project_type_label || "").trim(),
    exchange_code: String(source.exchange_code || "").trim(),
    exchange_label: String(source.exchange_label || "").trim(),
    created_at: String(source.created_at || "").trim(),
    state: String(source.state || "").trim(),
    status_label: String(source.status_label || "").trim(),
    status_detail: String(source.status_detail || "").trim(),
    source_name: safeDisplayText(source.source_name, "", unsafeValues),
    current_group: safeDisplayText(source.current_group, "", unsafeValues),
    current_type: safeDisplayText(source.current_type, "", unsafeValues),
    resolved_group: safeDisplayText(source.resolved_group, "", unsafeValues),
    resolved_type: safeDisplayText(source.resolved_type, "", unsafeValues),
    gap_codes: asArray(source.gap_codes).map((code) => String(code || "").trim()).filter(Boolean),
    blocking_reason_code: String(source.blocking_reason_code || "").trim(),
    recommended_rule: normalizeRecommendedRule(source.recommended_rule, unsafeValues),
    available_rule_kinds: asArray(source.available_rule_kinds).map((kind) => String(kind || "").trim()).filter(Boolean),
    candidate_resolutions: requireArrayField(
      source,
      "candidate_resolutions",
      "Invalid mappings resource",
    ).map((resolution) => normalizeCandidateResolution(resolution, unsafeValues)),
    has_conflict: Boolean(source.has_conflict),
  };
}

function normalizeMappingSectionItem(item = {}, sectionId = "") {
  const source = asObject(item);
  const unsafeValues = unsafeBusinessValues(source);
  return {
    ...normalizePendingMapping(source, unsafeValues),
    record_id: String(source.record_id || "").trim(),
    revision_id: asInt(source.revision_id),
    business_id: String(source.business_id || "").trim(),
    business_label: safeBusinessLabel(source),
    raw_business_label: "",
    blocker_kind: String(source.blocker_kind || "").trim(),
    blocker_subtype: String(source.blocker_subtype || "").trim(),
    queue_section: String(source.queue_section || sectionId || "").trim(),
    record_family: String(source.record_family || "").trim(),
    exchange_code: String(source.exchange_code || "").trim(),
    exchange_label: String(source.exchange_label || "").trim(),
    state: String(source.state || "").trim(),
    status_label: String(source.status_label || "").trim(),
    status_detail: String(source.status_detail || "").trim(),
    actionable: Boolean(source.actionable),
    audit_only: Boolean(source.audit_only),
    evidence_codes: asArray(source.evidence_codes).map((code) => String(code || "").trim()).filter(Boolean),
  };
}

function normalizeMappingSection(section = {}) {
  const source = asObject(section);
  const sectionId = String(source.section_id || "").trim();
  return {
    section_id: sectionId,
    title: String(source.title || "").trim(),
    count: asInt(source.count),
    cta_kind: String(source.cta_kind || "").trim(),
    items: requireArrayField(source, "items", "Invalid mappings resource").map((item) =>
      normalizeMappingSectionItem(item, sectionId),
    ),
  };
}

const LEGACY_BUSINESS_SECTION_ID = ["business", "resolution"].join("_");
const LEGACY_REVIEW_CTA_KIND = ["re", "evaluate", "business"].join("_");

function normalizeMappingsSummary(summary = {}) {
  const source = asObject(summary);
  return {
    actionable_count: asInt(source.actionable_count),
    mapping_gap_count: asInt(source.mapping_gap_count),
    mapping_conflict_count: asInt(source.mapping_conflict_count),
    audit_count: asInt(source.audit_count),
  };
}

function normalizeMappingUndoState(undo = {}) {
  const source = asObject(undo);
  const startupSessionId = String(source.startup_session_id || "").trim();
  return {
    available: source.available === true && Boolean(startupSessionId),
    startup_session_id: startupSessionId,
    operation_kind: String(source.operation_kind || "").trim(),
  };
}

export function normalizeMappingsResource(resource = {}) {
  const source = asObject(resource);
  const sections = requireArrayField(source, "sections", "Invalid mappings resource")
    .map((section) => normalizeMappingSection(section))
    .filter((section) => section.section_id !== LEGACY_BUSINESS_SECTION_ID && section.cta_kind !== LEGACY_REVIEW_CTA_KIND);
  const summary = normalizeMappingsSummary(source.summary);
  return {
    entries: asArray(source.entries).map((entry) => normalizeMappingEntry(entry)),
    sections,
    summary,
    undo: normalizeMappingUndoState(source.undo),
    returned_count: asInt(source.returned_count),
    total_count: asInt(source.total_count),
    truncated: Boolean(source.truncated),
  };
}
