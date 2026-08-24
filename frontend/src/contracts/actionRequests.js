import { MAPPING_RULES } from "../constants/index.js";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asText(value) {
  return String(value ?? "").trim();
}

function asPositiveInt(value) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function asRequestBoolean(value, fieldName, defaultValue = false) {
  if (typeof value === "boolean") return value;
  if (value == null) return defaultValue;
  throw new TypeError(`${fieldName} must be a boolean`);
}

function assertActionableOneClickScope(payload = {}) {
  const source = asObject(payload);
  const familyScopes = Array.isArray(source.family_scopes) ? source.family_scopes : [];
  if (familyScopes.length) return;
  const recordFamily = asText(source.record_family);
  const businessId = asText(source.business_id);
  const exchange = asText(source.exchange);
  if (!recordFamily || !businessId || !exchange) {
    throw new Error("missing actionable default scope for one-click request");
  }
}

function normalizeOneClickFamilyScopes(payload = {}) {
  const source = asObject(payload);
  if (!("family_scopes" in source)) return [];
  if (!Array.isArray(source.family_scopes)) {
    throw new Error("family_scopes must be an array");
  }
  return source.family_scopes.map((rawScope, index) => {
    const scope = asObject(rawScope);
    const recordFamily = asText(scope.record_family);
    const businessId = asText(scope.business_id);
    const exchange = asText(scope.exchange);
    if (!recordFamily || !businessId || !exchange) {
      throw new Error(`family_scopes[${index}] requires record_family, business_id, and exchange`);
    }
    const normalized = {
      record_family: recordFamily,
      business_id: businessId,
      exchange,
    };
    const businessLabel = asText(scope.business_label);
    if (businessLabel) normalized.business_label = businessLabel;
    return normalized;
  });
}

function resolveRuleKind(ruleKind, matchField, targetField) {
  const normalizedRuleKind = asText(ruleKind);
  if (normalizedRuleKind && MAPPING_RULES[normalizedRuleKind]) {
    return normalizedRuleKind;
  }
  const normalizedMatchField = asText(matchField);
  const normalizedTargetField = asText(targetField);
  return Object.entries(MAPPING_RULES).find(([, spec]) => spec.matchField === normalizedMatchField && spec.targetField === normalizedTargetField)?.[0] || normalizedRuleKind;
}

function assertCompleteManualImportScope(source = {}) {
  const recordFamily = asText(source.record_family);
  const businessId = asText(source.business_id);
  const businessLabel = asText(source.business_label);
  const exchange = asText(source.exchange);
  const hasAnyExplicitScope = Boolean(recordFamily || businessId || businessLabel || exchange);
  if (!hasAnyExplicitScope) {
    return {
      recordFamily: "",
      businessId: "",
      businessLabel: "",
      exchange: "",
    };
  }
  if (!recordFamily || !businessId) {
    throw new Error("manual-import explicit scope requires record_family and business_id together");
  }
  return {
    recordFamily,
    businessId,
    businessLabel,
    exchange,
  };
}

export function buildOneClickRequest(payload = {}) {
  const source = asObject(payload);
  assertActionableOneClickScope(source);
  const request = {};
  const startDate = asText(source.start_date);
  const endDate = asText(source.end_date);
  const recordFamily = asText(source.record_family);
  const businessId = asText(source.business_id);
  const exchange = asText(source.exchange);
  const familyScopes = normalizeOneClickFamilyScopes(source);
  const maxPages = asPositiveInt(source.max_pages);
  const concurrency = asPositiveInt(source.concurrency);
  const postprocessConfig = asText(source.postprocess_config);
  if (startDate) request.start_date = startDate;
  if (endDate) request.end_date = endDate;
  if (familyScopes.length) {
    request.family_scopes = familyScopes;
  } else {
    request.record_family = recordFamily;
    request.business_id = businessId;
    if (exchange) request.exchange = exchange;
  }
  if (maxPages != null) request.max_pages = maxPages;
  if (concurrency != null) request.concurrency = concurrency;
  if (postprocessConfig) request.postprocess_config = postprocessConfig;
  if ("include_public_resource" in source) {
    request.include_public_resource = asRequestBoolean(
      source.include_public_resource,
      "include_public_resource",
    );
  }
  if ("no_resume" in source) request.no_resume = asRequestBoolean(source.no_resume, "no_resume");
  if ("save_json" in source) request.save_json = asRequestBoolean(source.save_json, "save_json");
  if ("verbose" in source) request.verbose = asRequestBoolean(source.verbose, "verbose");
  return request;
}

export function buildManualImportRequest(payload = {}) {
  const source = typeof payload === "string" ? { input_dir: payload } : asObject(payload);
  const request = {
    input_dir: asText(source.input_dir),
  };
  const {
    recordFamily,
    businessId,
    businessLabel,
    exchange,
  } = assertCompleteManualImportScope(source);
  if (businessId) {
    if (recordFamily) request.record_family = recordFamily;
    request.business_id = businessId;
    if (businessLabel) request.business_label = businessLabel;
    if (exchange) request.exchange = exchange;
  }
  return request;
}

export function buildMappingRequest(payload = {}) {
  const source = asObject(payload);
  const ruleKind = resolveRuleKind(source.rule_kind, source.match_field, source.target_field);
  const request = {
    rule_kind: ruleKind,
    source_name: asText(source.source_name),
    target_value: asText(source.target_value),
    notes: asText(source.notes),
    confirm_overwrite: asRequestBoolean(source.confirm_overwrite, "confirm_overwrite"),
  };
  const entryId = asText(source.entry_id);
  if (entryId) request.entry_id = entryId;
  return request;
}

export function buildMappingConflictResolutionRequest(payload = {}) {
  const source = asObject(payload);
  const resolution = asObject(source.selected_resolution);
  const normalizedResolution = buildMappingRequest(resolution);
  return {
    record_id: asText(source.record_id),
    selected_resolution: {
      field: asText(resolution.field),
      label: asText(resolution.label),
      title: asText(resolution.title),
      notes: asText(resolution.notes),
      ...normalizedResolution,
    },
    notes: asText(source.notes),
    confirm_overwrite: asRequestBoolean(source.confirm_overwrite, "confirm_overwrite"),
  };
}

export function buildPathSelectionRequest(payload = {}) {
  const source = asObject(payload);
  const request = {};
  const selectionKind = asText(source.selection_kind);
  const prompt = asText(source.prompt);
  const currentPath = asText(source.current_path);
  if (selectionKind) request.selection_kind = selectionKind;
  if (prompt) request.prompt = prompt;
  if (currentPath) request.current_path = currentPath;
  return request;
}

export function buildPathOpenRequest(payload = {}) {
  const source = asObject(payload);
  return {
    path: asText(source.path),
    reveal: asRequestBoolean(source.reveal, "reveal"),
  };
}

export function buildRuntimeInstallRequest(payload = {}) {
  const source = asObject(payload);
  const request = {};
  const browserName = asText(source.browser_name);
  const trigger = asText(source.trigger);
  if (browserName) request.browser_name = browserName;
  if (trigger) request.trigger = trigger;
  return request;
}
