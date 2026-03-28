import type { MappingRuleConfig } from "./flows";

export const MAPPING_RULE_CONFIG: MappingRuleConfig = {
  transferor_group: {
    matchField: "transferor",
    targetField: "group_name",
    sourceLabel: "转让方名称",
    targetLabel: "集团名称",
    title: "转让方 -> 集团",
  },
  transferor_type: {
    matchField: "transferor",
    targetField: "source_type",
    sourceLabel: "转让方名称",
    targetLabel: "类型",
    title: "转让方 -> 类型",
  },
  group_group: {
    matchField: "group",
    targetField: "group_name",
    sourceLabel: "当前集团名称",
    targetLabel: "归并后的集团名称",
    title: "集团 -> 集团",
  },
  group_type: {
    matchField: "group",
    targetField: "source_type",
    sourceLabel: "集团名称",
    targetLabel: "类型",
    title: "集团 -> 类型",
  },
};

export type PendingMapping = {
  record_id: string;
  project_code?: string;
  payload?: Record<string, any>;
};

export type MappingDraftItem = {
  recordId: string;
  project_code: string;
  project_name: string;
  company_name: string;
  group_name: string;
  rawRecord: Record<string, any>;
  ruleKind: string;
  previousRuleKind: string;
  sourceName: string;
  targetValue: string;
  notes: string;
};

export function pendingRecordCompany(record: Record<string, any>) {
  return record["转让方"] || record["融资方"] || record["转让方名称"] || record["融资方名称"] || "";
}

export function buildDraftRuleKind(record: Record<string, any>) {
  return record["隶属集团"] ? "group_type" : "transferor_group";
}

export function buildDraftSourceValue(ruleKind: string, record: Record<string, any>) {
  if (String(ruleKind || "").startsWith("group")) {
    return String(record["隶属集团"] || "").trim();
  }
  return String(pendingRecordCompany(record) || "").trim();
}

export function toDraft(item: PendingMapping): MappingDraftItem {
  const rawRecord = item.payload || {};
  const ruleKind = buildDraftRuleKind(rawRecord);
  return {
    recordId: item.record_id,
    project_code: item.project_code || rawRecord["项目编号"] || "",
    project_name: rawRecord["项目名称"] || "",
    company_name: pendingRecordCompany(rawRecord),
    group_name: rawRecord["隶属集团"] || "",
    rawRecord,
    ruleKind,
    previousRuleKind: ruleKind,
    sourceName: buildDraftSourceValue(ruleKind, rawRecord),
    targetValue: "",
    notes: item.project_code || rawRecord["项目编号"] || "",
  };
}

export function pendingSummary(payload: Record<string, any>, pendingCount: number) {
  const returnedCount = Number(payload.returned_count ?? pendingCount);
  const totalCount = Number(payload.total_count ?? returnedCount);
  const base = `当前 ${returnedCount} 条待补项`;
  if (!payload.truncated || totalCount <= returnedCount) {
    return base;
  }
  return `${base}；只显示前 ${returnedCount} 条待补项，仍有剩余 ${totalCount - returnedCount} 条`;
}
