import { normalizeBasicSettingsResource } from "../contracts/settings.js";
import { resolveCatalogBusinessScopeSelection } from "./businessScopeSelector.js";
import { businessTypeLabel } from "../constants/index.js";
import {
  listSurfaceBusinesses,
  listSurfaceSourceIds,
  listSurfaceSourceOptions,
  normalizeCatalogResource,
} from "../contracts/catalog.js";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asText(value) {
  return String(value ?? "").trim();
}

function normalizeBusinessOption(business = {}) {
  const businessId = asText(business.business_id);
  const rawLabel = asText(business.business_label || businessId);
  return {
    business_id: businessId,
    business_label: rawLabel,
    business_display_label: businessTypeLabel(businessId, rawLabel),
    unavailable: Boolean(business.unavailable),
  };
}

function scopePoliciesForSelection(catalog = {}, {
  record_family = "",
  business_id = "",
  exchange = "",
  surface = "export",
} = {}) {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const familyId = asText(record_family);
  const selectedBusinessId = asText(business_id);
  const selectedExchange = asText(exchange);
  if (!familyId || !selectedBusinessId || !selectedExchange) return [];

  const familyRequirements = asObject(normalizedCatalog.source_business_requirements?.[familyId]);
  const businessIds = selectedBusinessId === "all"
    ? listSurfaceBusinesses(normalizedCatalog, { record_family: familyId, surface })
      .filter((business) => business.supported)
      .map((business) => business.business_id)
    : [selectedBusinessId];
  const policies = [];
  const seen = new Set();
  businessIds.forEach((businessId) => {
    const sourceIds = selectedExchange === "all"
      ? listSurfaceSourceIds(normalizedCatalog, {
          record_family: familyId,
          business_id: businessId,
          surface,
        })
      : [selectedExchange];
    const businessRequirements = asObject(familyRequirements[businessId]);
    sourceIds.forEach((sourceId) => {
      const requirement = asObject(businessRequirements[sourceId]);
      const scopePolicy = asText(requirement.scope_policy);
      if (!scopePolicy || seen.has(scopePolicy)) return;
      seen.add(scopePolicy);
      policies.push({
        policy_id: scopePolicy,
        label: asText(requirement.scope_policy_label),
        summary: asText(requirement.scope_policy_summary),
      });
    });
  });
  return policies;
}

export function resolveSettingsDefaultScopeEditor({
  catalog = {},
  basicSettings = {},
  selectedFamilyId = "",
  selectedBusinessId = "",
  selectedExchange = "",
} = {}) {
  const basic = normalizeBasicSettingsResource(basicSettings);
  const effectiveScope = basic.effective_default_scope || {};
  const storedPreference = basic.stored_preference || {};
  const staleReason = asText((basic.stale_default_metadata || {}).reason);
  const requestedFamilyId = asText(selectedFamilyId);
  const requestedBusinessId = asText(selectedBusinessId);
  const effectiveFamilyId = asText(effectiveScope.record_family);
  const storedFamilyId = asText(storedPreference.record_family);
  const selectedBusinessCandidate = requestedBusinessId || asText(
    requestedFamilyId && requestedFamilyId !== storedFamilyId && requestedFamilyId !== effectiveFamilyId
      ? ""
      : storedPreference.business_id || effectiveScope.business_id,
  );
  const baseSelection = resolveCatalogBusinessScopeSelection({
    catalog,
    basicSettings: basic,
    selectedFamilyId,
    selectedBusinessId: selectedBusinessCandidate,
    selectedExchange,
  });
  const catalogBusinessOptions = Array.isArray(baseSelection.business_options) ? baseSelection.business_options : [];
  const hasSelectedBusiness = catalogBusinessOptions.some((business) => business.business_id === selectedBusinessCandidate);
  const wantsAllBusiness = selectedBusinessCandidate === "all";
  const preserveUnavailableBusiness = Boolean(
    selectedBusinessCandidate
    && !hasSelectedBusiness
    && !wantsAllBusiness
    && staleReason === "unknown_business_id"
    && baseSelection.selected_family_id
    && (!storedFamilyId || baseSelection.selected_family_id === storedFamilyId || baseSelection.selected_family_id === effectiveFamilyId),
  );
  const allBusinessOption = normalizeBusinessOption({
    business_id: "all",
    business_label: "全部",
  });
  const hasAllOption = catalogBusinessOptions.some((business) => business.business_id === "all");
  const baseBusinessOptions = hasAllOption ? catalogBusinessOptions : [allBusinessOption, ...catalogBusinessOptions];
  const businessOptions = !preserveUnavailableBusiness
    ? baseBusinessOptions
    : [
        normalizeBusinessOption({
          business_id: selectedBusinessCandidate,
          business_label: storedPreference.business_label || effectiveScope.business_label || selectedBusinessCandidate,
          unavailable: true,
      }),
        ...baseBusinessOptions,
      ];
  const defaultBusinessId = baseSelection.selected_family_id ? "all" : "";
  const resolvedSelectedBusinessId = wantsAllBusiness || hasSelectedBusiness || preserveUnavailableBusiness
    ? (selectedBusinessCandidate || defaultBusinessId)
    : defaultBusinessId;
  const exchangeOptions = listSurfaceSourceOptions(catalog, {
    record_family: baseSelection.selected_family_id,
    business_id: resolvedSelectedBusinessId || "all",
    surface: "export",
    include_all: true,
    all_label: "全部交易所",
  });
  const unionExchangeOptions = resolvedSelectedBusinessId === "all"
    ? listSurfaceSourceOptions(catalog, {
        record_family: baseSelection.selected_family_id,
        business_id: "all",
        surface: "export",
        include_all: false,
        all_business_source_mode: "union",
      })
    : [];
  const exchangeValues = new Set(exchangeOptions.map((item) => asText(item.source_id)).filter(Boolean));
  const partialExchangeOptions = unionExchangeOptions.filter((option) => {
    const sourceId = asText(option.source_id);
    return sourceId && !exchangeValues.has(sourceId);
  });
  const normalizedSelectedExchange = asText(baseSelection.selected_exchange || selectedExchange || "all", "all");
  const resolvedSelectedExchange = exchangeValues.has(normalizedSelectedExchange)
    ? normalizedSelectedExchange
    : exchangeValues.has("all")
      ? "all"
      : asText(exchangeOptions[0]?.source_id);
  const scopePolicies = scopePoliciesForSelection(catalog, {
    record_family: baseSelection.selected_family_id,
    business_id: resolvedSelectedBusinessId,
    exchange: resolvedSelectedExchange,
    surface: "export",
  });
  return {
    family_options: baseSelection.family_options,
    selected_family_id: baseSelection.selected_family_id,
    family_selection_required: baseSelection.family_options.length > 1 && !baseSelection.selected_family_id,
    business_options: businessOptions,
    selected_business_id: resolvedSelectedBusinessId,
    exchange_options: exchangeOptions,
    partial_exchange_options: partialExchangeOptions,
    selected_exchange: resolvedSelectedExchange,
    scope_policy_ids: scopePolicies.map((policy) => policy.policy_id),
    scope_policies: scopePolicies,
    effective_default_scope: basic.effective_default_scope,
    stored_preference: basic.stored_preference,
    stale_default_metadata: basic.stale_default_metadata,
  };
}
