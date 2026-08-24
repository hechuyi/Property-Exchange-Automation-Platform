import {
  normalizeCatalogResource,
  resolveActionableDefaultScope,
} from "../contracts/catalog.js";
import { normalizeBasicSettingsResource } from "../contracts/settings.js";
import { businessTypeLabel, recordFamilyLabel } from "../constants/index.js";

function asText(value) {
  return String(value ?? "").trim();
}

function normalizeFamilyOption(family = {}) {
  const familyId = asText(family.family_id);
  const rawLabel = asText(family.family_label || familyId);
  return {
    family_id: familyId,
    family_label: rawLabel,
    family_display_label: recordFamilyLabel(familyId, rawLabel),
  };
}

function normalizeBusinessOption(business = {}) {
  const businessId = asText(business.business_id);
  const rawLabel = asText(business.business_label || businessId);
  return {
    business_id: businessId,
    business_label: rawLabel,
    business_display_label: businessTypeLabel(businessId, rawLabel),
  };
}

function hasFamilyOption(familyOptions = [], familyId = "") {
  return familyOptions.some((family) => family.family_id === familyId);
}

function resolveCatalogFamily(catalog = {}, familyId = "") {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const visibleFamilies = Array.isArray(normalizedCatalog.visible_families)
    ? normalizedCatalog.visible_families
    : [];
  return visibleFamilies.find((family) => family.family_id === familyId) || null;
}

export function resolveCatalogBusinessScopeSelection({
  catalog = {},
  basicSettings = {},
  selectedFamilyId = "",
  selectedBusinessId = "",
  selectedExchange = "",
  allowImplicitExchangeSelection = true,
} = {}) {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const basic = normalizeBasicSettingsResource(basicSettings);
  const familyOptions = (Array.isArray(normalizedCatalog.visible_families) ? normalizedCatalog.visible_families : [])
    .map((family) => normalizeFamilyOption(family))
    .filter((family) => family.family_id);
  const implicitFamilyId = familyOptions.length === 1 ? familyOptions[0].family_id : "";
  const effectiveScope = basic.effective_default_scope || {};
  const storedPreference = basic.stored_preference || {};
  const resolvedFamilyId = (
    (asText(selectedFamilyId) && hasFamilyOption(familyOptions, asText(selectedFamilyId)) && asText(selectedFamilyId))
    || (asText(effectiveScope.record_family) && hasFamilyOption(familyOptions, asText(effectiveScope.record_family)) && asText(effectiveScope.record_family))
    || (asText(storedPreference.record_family) && hasFamilyOption(familyOptions, asText(storedPreference.record_family)) && asText(storedPreference.record_family))
    || implicitFamilyId
  );
  const family = resolveCatalogFamily(normalizedCatalog, resolvedFamilyId);
  const businessOptions = (Array.isArray(family?.businesses) ? family.businesses : [])
    .map((business) => normalizeBusinessOption(business))
    .filter((business) => business.business_id);
  const normalizedSelectedBusinessId = asText(selectedBusinessId);
  const selectedBusinessValue = businessOptions.some((business) => business.business_id === normalizedSelectedBusinessId)
    ? normalizedSelectedBusinessId
    : "";
  const normalizedSelectedExchange = asText(selectedExchange) || (
    allowImplicitExchangeSelection
      ? asText(
          effectiveScope.exchange
            || storedPreference.exchange
            || basic.default_exchange,
        )
      : ""
  );
  return {
    family_options: familyOptions,
    selected_family_id: resolvedFamilyId,
    business_options: businessOptions,
    selected_business_id: selectedBusinessValue,
    selected_exchange: normalizedSelectedExchange,
    effective_default_scope: basic.effective_default_scope,
    stored_preference: basic.stored_preference,
    stale_default_metadata: basic.stale_default_metadata,
  };
}

export function resolveCatalogFamilyScopePlan({
  catalog = {},
  surface = "one_click",
  businessId = "all",
  exchange = "all",
} = {}) {
  const normalizedCatalog = normalizeCatalogResource(catalog);
  const familyOptions = (Array.isArray(normalizedCatalog.visible_families) ? normalizedCatalog.visible_families : [])
    .map((family) => normalizeFamilyOption(family))
    .filter((family) => family.family_id);
  const familyScopes = familyOptions
    .map((family) => resolveActionableDefaultScope(
      normalizedCatalog,
      {
        record_family: family.family_id,
        business_id: asText(businessId) || "all",
        exchange: asText(exchange) || "all",
      },
      { surface },
    ))
    .filter(Boolean)
    .map((scope) => ({
      record_family: scope.record_family,
      business_id: scope.business_id,
      business_label: "",
      exchange: scope.exchange,
    }));
  return {
    family_options: familyOptions,
    family_scopes: familyScopes,
  };
}
