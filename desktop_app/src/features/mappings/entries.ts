import type { MappingRuleConfig } from "./flows";

function normalizeText(value: unknown) {
  return String(value ?? "").trim();
}

function stableSerialize(value: unknown): string {
  if (value === null || value === undefined) {
    return String(value);
  }
  if (typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableSerialize(item)).join(",")}]`;
  }
  const target = value as Record<string, unknown>;
  const keys = Object.keys(target).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableSerialize(target[key])}`).join(",")}}`;
}

function hashFingerprint(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
  }
  return `f${(hash >>> 0).toString(16)}`;
}

function buildRuleKindLookup(mappingRuleConfig: MappingRuleConfig) {
  const lookup = new Map<string, string>();
  for (const [ruleKind, config] of Object.entries(mappingRuleConfig || {})) {
    const matchField = normalizeText(config.matchField).toLowerCase();
    const targetField = normalizeText(config.targetField).toLowerCase();
    if (matchField && targetField) {
      lookup.set(`${matchField}|${targetField}`, ruleKind);
    }
  }
  return lookup;
}

function resolveIdentity(entry: Record<string, any>) {
  const entryId = normalizeText(entry?.entry_id);
  if (entryId) {
    return {
      value: entryId,
      source: "entry_id",
    };
  }
  const mappingId = normalizeText(entry?.mapping_id);
  if (mappingId) {
    return {
      value: mappingId,
      source: "mapping_id",
    };
  }
  const id = normalizeText(entry?.id);
  if (id) {
    return {
      value: id,
      source: "id",
    };
  }
  return null;
}

function appendIssue(issueCodes: SavedMappingIssueCode[], issueCode: SavedMappingIssueCode) {
  if (!issueCodes.includes(issueCode)) {
    issueCodes.push(issueCode);
  }
}

function buildIssueText(issueCode: SavedMappingIssueCode) {
  if (issueCode === "missing_identity") {
    return "缺少稳定标识（entry_id / mapping_id / id），当前条目不会进入可编辑列表";
  }
  if (issueCode === "duplicate_identity") {
    return "条目标识重复，无法确认唯一规则";
  }
  if (issueCode === "missing_rule_fields") {
    return "缺少 match_field / target_field 规则字段";
  }
  if (issueCode === "unsupported_rule_fields") {
    return "规则字段组合不受支持";
  }
  if (issueCode === "missing_source_name") {
    return "缺少来源名称";
  }
  if (issueCode === "missing_target_value") {
    return "缺少目标值";
  }
  return "条目存在异常";
}

export type SavedMappingIssueCode =
  | "missing_identity"
  | "duplicate_identity"
  | "missing_rule_fields"
  | "unsupported_rule_fields"
  | "missing_source_name"
  | "missing_target_value";

export type SavedMappingEntry = {
  key: string;
  status: "valid" | "abnormal";
  isEditable: boolean;
  issueCodes: SavedMappingIssueCode[];
  issueText: string[];
  identitySource: "entry_id" | "mapping_id" | "id" | "content_fingerprint";
  ruleKind: string;
  ruleTitle: string;
  matchField: string;
  targetField: string;
  sourceName: string;
  targetValue: string;
  notes: string;
  updatedAt: string;
  rawEntry: Record<string, any>;
};

export function normalizeSavedMappingEntries(entries: Array<Record<string, any>>, mappingRuleConfig: MappingRuleConfig) {
  const ruleKindLookup = buildRuleKindLookup(mappingRuleConfig);
  const keyDedup = new Map<string, number>();
  const usedIdentityKeys = new Set<string>();
  return (entries || []).map((entry): SavedMappingEntry => {
    const metadata = (entry?.metadata || {}) as Record<string, any>;
    const issueCodes: SavedMappingIssueCode[] = [];
    const identity = resolveIdentity(entry || {});
    if (!identity) {
      appendIssue(issueCodes, "missing_identity");
    } else if (usedIdentityKeys.has(identity.value)) {
      appendIssue(issueCodes, "duplicate_identity");
    } else {
      usedIdentityKeys.add(identity.value);
    }

    const matchField = normalizeText(metadata.match_field || entry?.match_field);
    const targetField = normalizeText(metadata.target_field || entry?.target_field);
    if (!matchField || !targetField) {
      appendIssue(issueCodes, "missing_rule_fields");
    }
    const ruleKindKey = `${matchField.toLowerCase()}|${targetField.toLowerCase()}`;
    const ruleKind = ruleKindLookup.get(ruleKindKey) || "";
    if (matchField && targetField && !ruleKind) {
      appendIssue(issueCodes, "unsupported_rule_fields");
    }

    const sourceName = normalizeText(entry?.company_name || entry?.source_name || entry?.match_value);
    const targetValue = targetField.toLowerCase() === "group_name"
      ? normalizeText(entry?.group_name || entry?.target_value)
      : targetField.toLowerCase() === "source_type"
        ? normalizeText(entry?.source_type || entry?.target_value)
        : normalizeText(entry?.target_value);
    if (!sourceName) {
      appendIssue(issueCodes, "missing_source_name");
    }
    if (!targetValue) {
      appendIssue(issueCodes, "missing_target_value");
    }

    const fallbackFingerprint = hashFingerprint(stableSerialize(entry || {}));
    const baseKey = identity?.value
      ? (issueCodes.length ? `abnormal:${identity.value}` : identity.value)
      : `abnormal:${fallbackFingerprint}`;
    const dedupeCount = (keyDedup.get(baseKey) || 0) + 1;
    keyDedup.set(baseKey, dedupeCount);
    const key = dedupeCount > 1 ? `${baseKey}#${dedupeCount}` : baseKey;

    const status = issueCodes.length > 0 ? "abnormal" : "valid";
    const notes = normalizeText(metadata.notes || entry?.notes);
    return {
      key,
      status,
      isEditable: status === "valid",
      issueCodes,
      issueText: issueCodes.map(buildIssueText),
      identitySource: identity?.source || "content_fingerprint",
      ruleKind: ruleKind || "unsupported",
      ruleTitle: mappingRuleConfig[ruleKind || ""]?.title || `${matchField || "未知"} → ${targetField || "未知"}`,
      matchField,
      targetField,
      sourceName,
      targetValue,
      notes,
      updatedAt: normalizeText(entry?.updated_at),
      rawEntry: entry || {},
    };
  });
}

export function filterSavedMappingEntries({
  entries,
  ruleKind,
  keyword,
}: {
  entries: SavedMappingEntry[];
  ruleKind: string;
  keyword: string;
}) {
  const normalizedRuleKind = normalizeText(ruleKind || "all") || "all";
  const normalizedKeyword = normalizeText(keyword).toLowerCase();
  return entries.filter((entry) => {
    if (normalizedRuleKind !== "all" && entry.ruleKind !== normalizedRuleKind) {
      return false;
    }
    if (!normalizedKeyword) {
      return true;
    }
    const haystack = [
      entry.sourceName,
      entry.targetValue,
      entry.notes,
      entry.ruleTitle,
      entry.matchField,
      entry.targetField,
    ].join(" ").toLowerCase();
    return haystack.includes(normalizedKeyword);
  });
}

export function formatSavedEntriesSummary({
  totalCount,
  filteredCount,
  keyword,
  ruleKind,
}: {
  totalCount: number;
  filteredCount: number;
  keyword: string;
  ruleKind: string;
}) {
  const hasFilter = normalizeText(keyword) || normalizeText(ruleKind || "all") !== "all";
  if (!totalCount) {
    return "当前没有单独维护的映射规则；已录入记录也可能是网页本身已提供完整类型和集团信息";
  }
  if (hasFilter) {
    return `共 ${totalCount} 条规则，当前命中 ${filteredCount} 条`;
  }
  return `共 ${totalCount} 条规则，支持按规则类型和关键字筛选`;
}
