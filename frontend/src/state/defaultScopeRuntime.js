import {
  getCatalogDefaultScope,
  listSurfaceSourceIds,
  listSurfaceBusinesses,
  normalizeCatalogResource,
  resolveActionableDefaultScope,
} from "../contracts/catalog.js";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}
function asText(value) {
  return String(value ?? "").trim();
}

function asPositiveInt(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function normalizeScope(scope = {}) {
  const source = asObject(scope);
  return {
    record_family: asText(source.record_family),
    business_id: asText(source.business_id),
    business_label: asText(source.business_label),
    exchange: asText(source.exchange),
  };
}

function requirementPayload({ normalizedScope, businessId, sourceId, requirement = {} }) {
  const scopePolicy = asText(requirement.scope_policy);
  if (!scopePolicy) return null;
  return {
    record_family: normalizedScope.record_family,
    business_id: businessId,
    source_id: sourceId,
    scope_policy: scopePolicy,
    scope_policy_label: asText(requirement.scope_policy_label),
    scope_policy_summary: asText(requirement.scope_policy_summary),
  };
}

function sourceBusinessRequirementsForScope(catalog = {}, scope = {}, surface = "") {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const normalizedScope = normalizeScope(scope);
  if (!isExecutableScope(normalizedScope)) return [];
  const familyRequirements = asObject(normalizedCatalog.source_business_requirements?.[normalizedScope.record_family]);
  const businessIds = normalizedScope.business_id === "all"
    ? listSurfaceBusinesses(normalizedCatalog, {
        record_family: normalizedScope.record_family,
        surface,
      }).filter((business) => business.supported).map((business) => business.business_id)
    : [normalizedScope.business_id];
  const requirements = [];
  businessIds.forEach((businessId) => {
    const sourceIds = normalizedScope.exchange === "all"
      ? listSurfaceSourceIds(normalizedCatalog, {
          record_family: normalizedScope.record_family,
          business_id: businessId,
          surface,
        })
      : [normalizedScope.exchange];
    const businessRequirements = asObject(familyRequirements[businessId]);
    sourceIds.forEach((sourceId) => {
      const payload = requirementPayload({
        normalizedScope,
        businessId,
        sourceId,
        requirement: asObject(businessRequirements[sourceId]),
      });
      if (payload) requirements.push(payload);
    });
  });
  return requirements;
}

function normalizeStaleMetadata(metadata = {}) {
  const source = asObject(metadata);
  return {
    is_stale: Boolean(source.is_stale),
    reason: asText(source.reason),
    hint: asText(source.hint),
  };
}

function isExecutableScope(scope = {}) {
  const normalized = normalizeScope(scope);
  return Boolean(
    normalized.record_family
      && normalized.business_id
      && normalized.exchange
  );
}

function normalizeBrowseScope(scope = {}) {
  const normalized = normalizeScope(scope);
  return {
    record_family: normalized.record_family,
    business_id: normalized.business_id || "all",
    business_label: normalized.business_label,
    exchange: normalized.exchange || "all",
  };
}

function isBrowsableScope(scope = {}) {
  return Boolean(normalizeBrowseScope(scope).record_family);
}

function buildRuntime({
  state,
  surface,
  catalog,
  basicSettings,
  scope = null,
  error = null,
  businesses = [],
  supportedBusinesses = [],
}) {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const basic = asObject(basicSettings);
  return {
    state: asText(state),
    surface: asText(surface),
    scope: scope && isExecutableScope(scope) ? normalizeScope(scope) : null,
    error: asText(error),
    businesses,
    supportedBusinesses,
    source_business_requirements: sourceBusinessRequirementsForScope(normalizedCatalog, scope || {}, surface),
    catalog: normalizedCatalog,
    effective_default_scope: normalizeScope(basic.effective_default_scope),
    stored_preference: normalizeScope(basic.stored_preference),
    stale_default_metadata: normalizeStaleMetadata(basic.stale_default_metadata),
  };
}

export function describeDefaultScopeRuntime(runtime = {}) {
  const source = asObject(runtime);
  const stale = normalizeStaleMetadata(source.stale_default_metadata);
  switch (asText(source.state)) {
    case "load_failed":
      return asText(source.error) || "默认范围加载失败，请稍后重试。";
    case "missing_catalog":
      return "业务目录不可用，无法确定默认执行范围。";
    case "missing_default_scope":
      return "默认执行范围缺失，请先到设置中选择支持的业务和交易所。";
    case "stale_default_scope":
      return stale.hint || "默认执行范围已失效，请先重新选择。";
    case "unsupported":
      return "当前默认执行范围不支持此操作，请先重新选择。";
    case "no_surface_businesses":
      return "当前目录中没有支持此操作的业务。";
    default:
      return "";
  }
}

export function resolveDefaultScopeRuntime({
  catalog = {},
  basicSettings = {},
  surface = "",
  error = null,
} = {}) {
  if (error) {
    return buildRuntime({
      state: "load_failed",
      surface,
      catalog,
      basicSettings,
      error,
    });
  }

  const normalizedCatalog = normalizeCatalogResource(catalog);
  const visibleFamilies = Array.isArray(normalizedCatalog.visible_families)
    ? normalizedCatalog.visible_families
    : [];
  if (!visibleFamilies.length) {
    return buildRuntime({
      state: "missing_catalog",
      surface,
      catalog: normalizedCatalog,
      basicSettings,
    });
  }

  const basic = asObject(basicSettings);
  const stale = normalizeStaleMetadata(basic.stale_default_metadata);
  const effectiveScope = normalizeScope(basic.effective_default_scope);
  const declaredScope = isExecutableScope(effectiveScope)
    ? effectiveScope
    : getCatalogDefaultScope(normalizedCatalog);
  const familyId = asText(declaredScope?.record_family || basic.stored_preference?.record_family || effectiveScope.record_family);
  const businesses = familyId && surface
    ? listSurfaceBusinesses(normalizedCatalog, { record_family: familyId, surface })
    : [];
  const supportedBusinesses = businesses.filter((item) => item.supported);

  if (stale.is_stale) {
    return buildRuntime({
      state: "stale_default_scope",
      surface,
      catalog: normalizedCatalog,
      basicSettings,
      businesses,
      supportedBusinesses,
    });
  }

  if (!declaredScope) {
    return buildRuntime({
      state: "missing_default_scope",
      surface,
      catalog: normalizedCatalog,
      basicSettings,
      businesses,
      supportedBusinesses,
    });
  }

  if (surface && businesses.length > 0 && supportedBusinesses.length === 0) {
    return buildRuntime({
      state: "no_surface_businesses",
      surface,
      catalog: normalizedCatalog,
      basicSettings,
      businesses,
      supportedBusinesses,
    });
  }

  const actionableScope = surface
    ? resolveActionableDefaultScope(normalizedCatalog, declaredScope, { surface })
    : normalizeScope(declaredScope);
  if (!actionableScope) {
    return buildRuntime({
      state: "unsupported",
      surface,
      catalog: normalizedCatalog,
      basicSettings,
      businesses,
      supportedBusinesses,
    });
  }

  return buildRuntime({
    state: "ready",
    surface,
    catalog: normalizedCatalog,
    basicSettings,
    scope: actionableScope,
    businesses,
    supportedBusinesses,
  });
}

export function buildRecordsScopeFromRuntime(runtime = {}, filters = {}, options = {}) {
  const source = asObject(runtime);
  if (asText(source.state) !== "ready" || !isExecutableScope(source.scope)) {
    return null;
  }
  const normalizedScope = normalizeScope(source.scope);
  const sourceFilters = asObject(filters);
  const businessId = asText(sourceFilters.business_id || normalizedScope.business_id);
  const exchange = asText(sourceFilters.exchange || normalizedScope.exchange);
  return {
    record_family: normalizedScope.record_family,
    state: asText(sourceFilters.state || "all") || "all",
    business_id: businessId,
    business_label: businessId === normalizedScope.business_id ? normalizedScope.business_label : "",
    exchange,
    keyword: asText(sourceFilters.keyword),
    date_from: asText(sourceFilters.date_from),
    date_to: asText(sourceFilters.date_to),
    page: asPositiveInt(options.page, 1),
    page_size: asPositiveInt(options.page_size, 50),
  };
}

function browseFamilyFromCatalog(catalog = {}) {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const declaredScope = getCatalogDefaultScope(normalizedCatalog);
  if (declaredScope?.record_family) {
    return declaredScope.record_family;
  }
  const visibleFamilies = Array.isArray(normalizedCatalog.visible_families)
    ? normalizedCatalog.visible_families
    : [];
  return asText(visibleFamilies[0]?.family_id);
}

function browseFamilyFromSettings(basicSettings = {}) {
  const basic = asObject(basicSettings);
  return asText(
    basic.effective_default_scope?.record_family
      || basic.stored_preference?.record_family,
  );
}

function visibleFamilyIds(catalog = {}) {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  return new Set((normalizedCatalog.visible_families || []).map((item) => asText(item.family_id)).filter(Boolean));
}

function visibleBrowseFamily(catalog = {}, candidate = "") {
  const candidateText = asText(candidate);
  return candidateText && visibleFamilyIds(catalog).has(candidateText) ? candidateText : "";
}

function buildBrowseRuntime({
  state,
  catalog,
  basicSettings,
  scope = null,
  error = null,
  businesses = [],
}) {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const basic = asObject(basicSettings);
  return {
    state: asText(state),
    surface: "records",
    scope: scope && isBrowsableScope(scope) ? normalizeBrowseScope(scope) : null,
    error: asText(error),
    businesses,
    supportedBusinesses: businesses.filter((item) => item.supported),
    catalog: normalizedCatalog,
    effective_default_scope: normalizeScope(basic.effective_default_scope),
    stored_preference: normalizeScope(basic.stored_preference),
    stale_default_metadata: normalizeStaleMetadata(basic.stale_default_metadata),
  };
}

export function describeRecordsBrowseRuntime(runtime = {}) {
  const source = asObject(runtime);
  switch (asText(source.state)) {
    case "load_failed":
      return asText(source.error) || "记录范围加载失败，请稍后重试。";
    case "missing_catalog":
      return "业务目录不可用，无法构建记录浏览范围。";
    default:
      return "";
  }
}

export function resolveRecordsBrowseRuntime({
  catalog = {},
  basicSettings = {},
  error = null,
} = {}) {
  const familyId = visibleBrowseFamily(catalog, browseFamilyFromSettings(basicSettings))
    || visibleBrowseFamily(catalog, browseFamilyFromCatalog(catalog));
  if (!familyId) {
    return buildBrowseRuntime({
      state: error ? "load_failed" : "missing_catalog",
      catalog,
      basicSettings,
      error,
    });
  }
  const businesses = listSurfaceBusinesses(catalog, { record_family: familyId, surface: "records" });
  return buildBrowseRuntime({
    state: "ready",
    catalog,
    basicSettings,
    businesses,
    scope: {
      record_family: familyId,
      business_id: "all",
      business_label: "",
      exchange: "all",
    },
  });
}

export function buildRecordsScopeFromBrowseRuntime(runtime = {}, filters = {}, options = {}) {
  const source = asObject(runtime);
  if (asText(source.state) !== "ready" || !isBrowsableScope(source.scope)) {
    return null;
  }
  const normalizedScope = normalizeBrowseScope(source.scope);
  const sourceFilters = asObject(filters);
  const catalog = normalizeCatalogResource(source.catalog || {});
  const requestedFamily = asText(sourceFilters.record_family);
  const recordFamily = visibleBrowseFamily(catalog, requestedFamily)
    || visibleBrowseFamily(catalog, normalizedScope.record_family);
  if (!recordFamily) {
    return null;
  }
  const businessId = asText(sourceFilters.business_id, normalizedScope.business_id || "all");
  const exchange = asText(sourceFilters.exchange, normalizedScope.exchange || "all");
  return {
    record_family: recordFamily,
    state: asText(sourceFilters.state || "all") || "all",
    business_id: businessId || "all",
    business_label: businessId === normalizedScope.business_id ? normalizedScope.business_label : "",
    exchange: exchange || "all",
    keyword: asText(sourceFilters.keyword),
    date_from: asText(sourceFilters.date_from),
    date_to: asText(sourceFilters.date_to),
    page: asPositiveInt(options.page, 1),
    page_size: asPositiveInt(options.page_size, 50),
  };
}

export function buildActionableScopeFromRuntime(runtime = {}, overrides = {}) {
  const source = asObject(runtime);
  if (asText(source.state) !== "ready" || !isExecutableScope(source.scope)) {
    return null;
  }
  const baseScope = normalizeScope(source.scope);
  const sourceOverrides = asObject(overrides);
  return {
    record_family: baseScope.record_family,
    business_id: asText(sourceOverrides.business_id || baseScope.business_id),
    business_label: asText(sourceOverrides.business_label || baseScope.business_label),
    exchange: asText(sourceOverrides.exchange || baseScope.exchange),
  };
}
