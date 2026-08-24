import { MAPPING_RULES } from "../constants/index.js";

function asText(value) {
  return String(value ?? "").trim();
}

function deriveRuleKind(matchField, targetField) {
  return (
    Object.entries(MAPPING_RULES).find(
      ([, spec]) => spec.matchField === asText(matchField) && spec.targetField === asText(targetField),
    )?.[0] || "transferor_group"
  );
}

export function createInitialMappingDraft() {
  return {
    entry_id: "",
    rule_kind: "transferor_group",
    source_name: "",
    target_value: "",
    notes: "",
    confirm_overwrite: false,
  };
}

function buildDraftBase(ruleLike = {}) {
  return {
    entry_id: asText(ruleLike.entry_id),
    rule_kind:
      asText(ruleLike.rule_kind) || deriveRuleKind(ruleLike.match_field, ruleLike.target_field),
    source_name: asText(ruleLike.source_name),
    target_value: asText(ruleLike.target_value),
    confirm_overwrite: false,
  };
}

export function buildMappingDraftFromSuggestion(ruleLike = {}) {
  return {
    ...createInitialMappingDraft(),
    ...buildDraftBase(ruleLike),
    entry_id: "",
    notes: "",
  };
}

export function buildMappingDraftFromEntry(entry = {}) {
  return {
    ...createInitialMappingDraft(),
    ...buildDraftBase(entry),
    notes: asText(entry.notes),
  };
}
