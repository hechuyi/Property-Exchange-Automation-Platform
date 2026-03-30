function normalizeText(value: unknown) {
  return String(value ?? "").trim();
}

export type MappingRuleConfig = Record<
  string,
  {
    matchField: string;
    targetField: string;
    sourceLabel: string;
    targetLabel: string;
    title: string;
  }
>;

export type MappingDraft = {
  ruleKind?: string;
  sourceName?: string;
  targetValue?: string;
  notes?: string;
  recordId?: string;
  project_name?: string;
  project_code?: string;
};

export type MappingPayload = {
  source_name: string;
  target_value: string;
  match_field: string;
  target_field: string;
  notes: string;
  confirm_overwrite?: boolean;
};

function resolveRuleConfig(ruleKind: string | undefined, mappingRuleConfig: MappingRuleConfig) {
  return (
    mappingRuleConfig[ruleKind || ""]
    || mappingRuleConfig.transferor_group
    || { matchField: "transferor", targetField: "group_name" }
  );
}

function existingTargetValue(preview: Record<string, any> = {}) {
  const entry = preview.existing_entry || {};
  return normalizeText(
    preview.target_field === "group_name"
      ? entry.group_name
      : entry.source_type,
  );
}

export function buildMappingPayload(draft: MappingDraft, mappingRuleConfig: MappingRuleConfig): MappingPayload {
  const config = resolveRuleConfig(draft?.ruleKind, mappingRuleConfig);
  return {
    source_name: normalizeText(draft?.sourceName),
    target_value: normalizeText(draft?.targetValue),
    match_field: normalizeText(config.matchField || "transferor"),
    target_field: normalizeText(config.targetField || "group_name"),
    notes: normalizeText(draft?.notes),
  };
}

export function formatMappingConflictSummary(preview: Record<string, any> = {}) {
  const affectedCount = Number(preview.affected_count || 0);
  const pendingCount = Number(preview.affected_pending_count || 0);
  const returnedCount = Number(preview.affected_returned_count || affectedCount);
  const totalCount = Number(preview.affected_total_count || returnedCount);
  const currentValue = existingTargetValue(preview) || "空值";
  const nextValue = normalizeText(preview.target_value) || "空值";
  const capacityNotice = preview.truncated && totalCount > returnedCount
    ? `只显示前 ${returnedCount} 条记录，仍有剩余 ${totalCount - returnedCount} 条。`
    : "";
  return [
    `已存在同来源规则，当前值为“${currentValue}”，新值为“${nextValue}”。`,
    `预计回刷 ${affectedCount} 条记录，其中 ${pendingCount} 条仍是待补映射。`,
    capacityNotice,
  ].filter(Boolean).join(" ");
}

export function formatBatchMappingSaveSummary(result: {
  savedCount: number;
  refreshJobs: Record<string, any>[];
  skippedOverwriteCount: number;
  failedCount: number;
  failureMessages: string[];
}) {
  const affectedCount = (result.refreshJobs || []).reduce((sum, item) => sum + Number(item.affected_count || 0), 0);
  const parts = [`已保存 ${result.savedCount} 条规则`];
  if ((result.refreshJobs || []).length) {
    parts.push(`启动 ${result.refreshJobs.length} 个映射回刷任务`);
  }
  if (affectedCount) {
    parts.push(`共影响 ${affectedCount} 条记录`);
  }
  if (result.skippedOverwriteCount) {
    parts.push(`跳过 ${result.skippedOverwriteCount} 条未确认覆盖规则`);
  }
  if (result.failedCount) {
    const failureHint = result.failureMessages[0] ? `首个失败：${result.failureMessages[0]}` : "";
    parts.push(`另有 ${result.failedCount} 条保存失败${failureHint ? `，${failureHint}` : ""}`);
  }
  return parts.join("，");
}

export function formatSingleMappingSaveSummary(payload: Record<string, any>, preview: Record<string, any> | null | undefined, capacityNotice: string) {
  const actionLabel = preview?.mode === "overwrite"
    ? "映射规则已覆盖"
    : preview?.mode === "update"
      ? "映射规则已更新"
      : "映射规则已保存";
  if (payload?.job_id) {
    return `${actionLabel}，已启动映射回刷任务：${payload.job_id}，影响 ${Number(payload.affected_count || 0)} 条记录${capacityNotice ? `。${capacityNotice}` : ""}`;
  }
  return `${actionLabel}，当前没有匹配到需要回刷的记录`;
}

export function formatPendingReprocessSummary(payload: Record<string, any>, capacityNotice: string) {
  if (!payload?.job_id) {
    return "当前没有待补映射需要重处理";
  }
  return capacityNotice
    ? `已启动待补映射批量重处理：${payload.job_id}，共 ${Number(payload.affected_count || 0)} 条记录。${capacityNotice}`
    : `已启动待补映射批量重处理：${payload.job_id}，共 ${Number(payload.affected_count || 0)} 条记录`;
}

export async function runMappingUpsertFlow({
  draft,
  mappingRuleConfig,
  previewMapping,
  saveMapping,
  confirmOverwrite = async () => true,
}: {
  draft: MappingDraft;
  mappingRuleConfig: MappingRuleConfig;
  previewMapping: (payload: MappingPayload) => Promise<Record<string, any>>;
  saveMapping: (payload: MappingPayload) => Promise<Record<string, any>>;
  confirmOverwrite?: (preview: Record<string, any>, payload: MappingPayload) => Promise<boolean>;
}) {
  const payload = buildMappingPayload(draft, mappingRuleConfig);
  const preview = await previewMapping(payload);
  if (preview?.conflict) {
    const confirmed = await confirmOverwrite(preview, payload);
    if (!confirmed) {
      return {
        cancelled: true,
        payload,
        preview,
        response: null,
      };
    }
    payload.confirm_overwrite = true;
  }
  const response = await saveMapping(payload);
  return {
    cancelled: false,
    payload,
    preview,
    response,
  };
}

export async function runBatchMappingUpsertFlow({
  drafts,
  mappingRuleConfig,
  previewMapping,
  saveMapping,
  confirmOverwrite = async () => true,
}: {
  drafts: MappingDraft[];
  mappingRuleConfig: MappingRuleConfig;
  previewMapping: (payload: MappingPayload) => Promise<Record<string, any>>;
  saveMapping: (payload: MappingPayload) => Promise<Record<string, any>>;
  confirmOverwrite?: (preview: Record<string, any>, payload: MappingPayload) => Promise<boolean>;
}) {
  const dedupe = new Set<string>();
  const draftGroups = new Map<string, string[]>();
  const refreshJobs: Record<string, any>[] = [];
  const savedRecordIds = new Set<string>();
  const failureMessages: string[] = [];
  let skippedOverwriteCount = 0;
  let failedCount = 0;
  let savedCount = 0;

  for (const draft of drafts || []) {
    const payload = buildMappingPayload(draft, mappingRuleConfig);
    const dedupeKey = [
      payload.match_field,
      payload.target_field,
      payload.source_name,
      payload.target_value,
    ].join("|").toLowerCase();
    const recordIds = draftGroups.get(dedupeKey) || [];
    if (draft?.recordId) {
      recordIds.push(draft.recordId);
    }
    draftGroups.set(dedupeKey, recordIds);
  }

  for (const draft of drafts || []) {
    const payload = buildMappingPayload(draft, mappingRuleConfig);
    const dedupeKey = [
      payload.match_field,
      payload.target_field,
      payload.source_name,
      payload.target_value,
    ].join("|").toLowerCase();
    if (dedupe.has(dedupeKey)) {
      continue;
    }
    dedupe.add(dedupeKey);

    try {
      const result = await runMappingUpsertFlow({
        draft,
        mappingRuleConfig,
        previewMapping,
        saveMapping,
        confirmOverwrite,
      });
      if (result.cancelled) {
        skippedOverwriteCount += 1;
        continue;
      }
      savedCount += 1;
      for (const recordId of draftGroups.get(dedupeKey) || []) {
        if (recordId) {
          savedRecordIds.add(recordId);
        }
      }
      if (result.response?.job_id) {
        refreshJobs.push(result.response);
      }
    } catch (error) {
      failedCount += 1;
      const label = normalizeText(draft?.sourceName || draft?.project_name || draft?.project_code || "未命名规则");
      failureMessages.push(`${label}：规则保存失败，请到任务页查看明细。`);
    }
  }

  return {
    savedCount,
    skippedOverwriteCount,
    failedCount,
    refreshJobs,
    savedRecordIds,
    failureMessages,
  };
}
