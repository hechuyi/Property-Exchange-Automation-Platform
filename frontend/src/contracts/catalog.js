function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asText(value, fallback = "") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

const KNOWN_SURFACES = new Set(["records", "one_click", "export"]);

function normalizeScope(scope = {}) {
  const source = asObject(scope);
  return {
    record_family: asText(source.record_family),
    business_id: asText(source.business_id),
    business_label: asText(source.business_label),
    exchange: asText(source.exchange),
  };
}

function normalizeBusiness(business = {}) {
  const source = asObject(business);
  return {
    business_id: asText(source.business_id),
    business_label: asText(source.business_label),
    supported_surfaces: asArray(source.supported_surfaces).map((item) => asText(item)).filter(Boolean),
  };
}

function normalizeVisibleFamily(family = {}) {
  const source = asObject(family);
  return {
    family_id: asText(source.family_id),
    family_label: asText(source.family_label),
    businesses: asArray(source.businesses).map((item) => normalizeBusiness(item)).filter((item) => item.business_id),
  };
}

function normalizeSupportMatrix(matrix = {}) {
  const families = asObject(matrix);
  return Object.fromEntries(
    Object.entries(families).map(([familyId, businesses]) => [
      asText(familyId),
      Object.fromEntries(
        Object.entries(asObject(businesses)).map(([businessId, surfaces]) => [
          asText(businessId),
          Object.fromEntries(
          Object.entries(asObject(surfaces))
              .filter(([surface]) => KNOWN_SURFACES.has(asText(surface)))
              .map(([surface, enabled]) => [asText(surface), Boolean(enabled)]),
          ),
        ]),
      ),
    ]),
  );
}

function normalizeSurfaceSourceMatrix(matrix = {}) {
  const families = asObject(matrix);
  return Object.fromEntries(
    Object.entries(families).map(([familyId, businesses]) => [
      asText(familyId),
      Object.fromEntries(
        Object.entries(asObject(businesses)).map(([businessId, surfaces]) => [
          asText(businessId),
          Object.fromEntries(
            Object.entries(asObject(surfaces))
              .filter(([surface]) => KNOWN_SURFACES.has(asText(surface)))
              .map(([surface, sourceIds]) => [
                asText(surface),
                asArray(sourceIds).map((sourceId) => asText(sourceId)).filter(Boolean),
              ]),
          ),
        ]),
      ),
    ]),
  );
}

function normalizeSourceBusinessRequirements(requirements = {}) {
  const families = asObject(requirements);
  return Object.fromEntries(
    Object.entries(families).map(([familyId, businesses]) => [
      asText(familyId),
      Object.fromEntries(
        Object.entries(asObject(businesses)).map(([businessId, sources]) => [
          asText(businessId),
          Object.fromEntries(
            Object.entries(asObject(sources)).map(([sourceId, requirement]) => {
              const source = asObject(requirement);
              return [
                asText(sourceId),
                {
                  scope_policy: asText(source.scope_policy),
                  scope_policy_label: asText(source.scope_policy_label),
                  scope_policy_summary: asText(source.scope_policy_summary),
                },
              ];
            }),
          ),
        ]),
      ),
    ]),
  );
}

function normalizeVisibility(visibility = {}) {
  const source = asObject(visibility);
  const visibleFamilies = asArray(source.visible_families).map((item) => asText(item)).filter(Boolean);
  return {
    mode: asText(source.mode),
    visible_families: visibleFamilies,
  };
}

function hasCompleteScope(scope = {}) {
  const normalizedScope = normalizeScope(scope);
  return Boolean(
    normalizedScope.record_family
      && normalizedScope.business_id
      && normalizedScope.exchange
  );
}

export function normalizeCatalogResource(resource = {}) {
  const source = asObject(resource);
  return {
    active_profile: {
      profile_id: asText(asObject(source.active_profile).profile_id),
    },
    visible_families: asArray(source.visible_families).map((item) => normalizeVisibleFamily(item)).filter((item) => item.family_id),
    sources: asArray(source.sources).map((item) => {
      const sourceItem = asObject(item);
      return {
        source_id: asText(sourceItem.source_id),
        source_label: asText(sourceItem.source_label),
        record_families: asArray(sourceItem.record_families).map((familyId) => asText(familyId)).filter(Boolean),
      };
    }).filter((item) => item.source_id),
    support_matrix: normalizeSupportMatrix(source.support_matrix),
    surface_source_matrix: normalizeSurfaceSourceMatrix(source.surface_source_matrix),
    source_business_requirements: normalizeSourceBusinessRequirements(source.source_business_requirements),
    default_scope: normalizeScope(source.default_scope),
    visibility: normalizeVisibility(source.visibility),
  };
}

export function findVisibleFamily(catalog = {}, recordFamily = "listing") {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const familyId = asText(recordFamily, "listing");
  return normalizedCatalog.visible_families.find((item) => item.family_id === familyId) || null;
}

function isSurfaceSupported(catalog = {}, recordFamily = "listing", businessId = "", surface = "") {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const familyMatrix = asObject(normalizedCatalog.support_matrix[recordFamily]);
  const surfaceMatrix = asObject(familyMatrix[businessId]);
  if (Object.prototype.hasOwnProperty.call(surfaceMatrix, surface)) {
    return Boolean(surfaceMatrix[surface]);
  }
  const family = findVisibleFamily(normalizedCatalog, recordFamily);
  const business = family?.businesses.find((item) => item.business_id === businessId);
  return Boolean(business && business.supported_surfaces.includes(surface));
}

function surfaceSourceIds(catalog = {}, recordFamily = "listing", businessId = "", surface = "") {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const familyMatrix = asObject(normalizedCatalog.surface_source_matrix[recordFamily]);
  if (!Object.prototype.hasOwnProperty.call(familyMatrix, businessId)) {
    return null;
  }
  const surfaceMatrix = asObject(familyMatrix[businessId]);
  if (!Object.prototype.hasOwnProperty.call(surfaceMatrix, surface)) {
    return null;
  }
  return asArray(surfaceMatrix[surface]).map((item) => asText(item)).filter(Boolean);
}

function uniqueTextList(items = []) {
  const seen = new Set();
  const ordered = [];
  asArray(items).forEach((item) => {
    const normalized = asText(item);
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    ordered.push(normalized);
  });
  return ordered;
}

function intersectOrdered(first = [], second = []) {
  if (!first.length || !second.length) return [];
  const candidate = new Set(second);
  return first.filter((item) => candidate.has(item));
}

function unionOrdered(lists = []) {
  const seen = new Set();
  const ordered = [];
  asArray(lists).forEach((list) => {
    uniqueTextList(list).forEach((item) => {
      if (seen.has(item)) return;
      seen.add(item);
      ordered.push(item);
    });
  });
  return ordered;
}

function surfaceBusinessIds(catalog = {}, recordFamily = "listing", surface = "") {
  return listSurfaceBusinesses(catalog, { record_family: recordFamily, surface })
    .filter((item) => item.supported)
    .map((item) => item.business_id);
}

export function listSurfaceSourceIds(catalog = {}, {
  record_family = "listing",
  business_id = "",
  surface = "",
  all_business_source_mode = "intersection",
} = {}) {
  const recordFamily = asText(record_family);
  const businessId = asText(business_id);
  const surfaceName = asText(surface);
  if (!recordFamily || !surfaceName) return [];

  if (businessId && businessId !== "all") {
    const declaredSourceIds = surfaceSourceIds(catalog, recordFamily, businessId, surfaceName);
    return declaredSourceIds === null
      ? []
      : uniqueTextList(declaredSourceIds);
  }

  const supportedBusinessIds = surfaceBusinessIds(catalog, recordFamily, surfaceName);
  if (!supportedBusinessIds.length) return [];
  const perBusinessSources = supportedBusinessIds
    .map((item) => {
      const declaredSourceIds = surfaceSourceIds(catalog, recordFamily, item, surfaceName);
      return declaredSourceIds === null
        ? []
        : uniqueTextList(declaredSourceIds);
    });
  if (!perBusinessSources.length) return [];
  if (asText(all_business_source_mode) === "union") {
    return unionOrdered(perBusinessSources);
  }
  return perBusinessSources.slice(1).reduce(
    (acc, next) => intersectOrdered(acc, next),
    perBusinessSources[0],
  );
}

export function listSurfaceSourceOptions(catalog = {}, {
  record_family = "listing",
  business_id = "",
  surface = "",
  include_all = false,
  all_label = "全部交易所",
  all_business_source_mode = "intersection",
} = {}) {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const sourceIds = listSurfaceSourceIds(normalizedCatalog, {
    record_family,
    business_id,
    surface,
    all_business_source_mode,
  });
  const catalogLabels = new Map(
    asArray(normalizedCatalog.sources)
      .map((source) => [asText(source.source_id), asText(source.source_label)])
      .filter(([sourceId]) => sourceId),
  );
  const options = sourceIds.map((sourceId) => ({
    source_id: sourceId,
    source_label: catalogLabels.get(sourceId) || sourceId,
  }));
  return include_all
    ? [{ source_id: "all", source_label: asText(all_label, "全部交易所") || "全部交易所" }, ...options]
    : options;
}

export function listSurfaceBusinesses(catalog = {}, { record_family = "listing", surface = "" } = {}) {
  const family = findVisibleFamily(catalog, record_family);
  if (!family) return [];
  return family.businesses.map((business) => ({
    record_family: family.family_id,
    business_id: business.business_id,
    business_label: business.business_label,
    supported: isSurfaceSupported(catalog, family.family_id, business.business_id, surface),
    supported_surfaces: [...business.supported_surfaces],
  }));
}

export function getCatalogDefaultScope(catalog = {}) {
  const scope = normalizeCatalogResource(catalog).default_scope;
  return hasCompleteScope(scope) ? scope : null;
}

export function resolveActionableDefaultScope(catalog = {}, scope = {}, { surface = "" } = {}) {
  const normalizedScope = normalizeScope(scope);
  if (!hasCompleteScope(normalizedScope)) return null;
  if (!surface) {
    return normalizedScope;
  }
  const normalizedCatalog = normalizeCatalogResource(catalog);
  if (normalizedScope.business_id === "all") {
    const familyBusinesses = listSurfaceBusinesses(catalog, {
      record_family: normalizedScope.record_family,
      surface,
    });
    if (!(familyBusinesses.length > 0 && familyBusinesses.every((business) => business.supported))) {
      return null;
    }
    const sourceIdsByBusiness = familyBusinesses.map((business) =>
      surfaceSourceIds(normalizedCatalog, normalizedScope.record_family, business.business_id, surface),
    );
    if (sourceIdsByBusiness.some((sourceIds) => sourceIds === null || !sourceIds.length)) {
      return null;
    }
    if (normalizedScope.exchange === "all") {
      return normalizedScope;
    }
    const supportedForEveryBusiness = familyBusinesses.every((business) => {
      const scopedSourceIds = surfaceSourceIds(normalizedCatalog, normalizedScope.record_family, business.business_id, surface);
      return Array.isArray(scopedSourceIds) && scopedSourceIds.includes(normalizedScope.exchange);
    });
    return supportedForEveryBusiness ? normalizedScope : null;
  }
  if (!isSurfaceSupported(catalog, normalizedScope.record_family, normalizedScope.business_id, surface)) {
    return null;
  }
  const supportedSources = surfaceSourceIds(
    normalizedCatalog,
    normalizedScope.record_family,
    normalizedScope.business_id,
    surface,
  );
  if (supportedSources === null || !supportedSources.length) {
    return null;
  }
  if (normalizedScope.exchange === "all") {
    return normalizedScope;
  }
  return supportedSources.includes(normalizedScope.exchange) ? normalizedScope : null;
}
