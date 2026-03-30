import { Button, Card, Input, Select, Space, Typography } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useDesktopRuntime } from "../desktop/provider";
import { PAGE_TEST_IDS } from "../testing/selectors";
import {
  formatMappingConflictSummary,
  runBatchMappingUpsertFlow,
  runMappingUpsertFlow,
} from "../features/mappings/flows";
import {
  filterSavedMappingEntries,
  formatSavedEntriesSummary,
  normalizeSavedMappingEntries,
} from "../features/mappings/entries";
import {
  buildDraftSourceValue,
  MAPPING_RULE_CONFIG,
  pendingRecordCompany,
  pendingSummary,
  toDraft,
  type MappingDraftItem,
  type PendingMapping,
} from "../features/mappings/model";

const EMPTY_SINGLE_DRAFT = {
  ruleKind: "transferor_group",
  sourceName: "",
  targetValue: "",
  notes: "",
};

type ConflictFlowType = "single" | "batch";

type BatchSavePhase = "idle" | "in-flight" | "waiting-conflict";

function formatCapacityNotice(payload: Record<string, any>) {
  const returnedCount = Number(payload.affected_returned_count ?? payload.affected_count ?? 0);
  const totalCount = Number(payload.affected_total_count ?? returnedCount);
  if (!payload.truncated || totalCount <= returnedCount) {
    return "";
  }
  return `只显示前 ${returnedCount} 条记录，仍有剩余 ${totalCount - returnedCount} 条`;
}

export default function MappingsPage() {
  const { commands } = useDesktopRuntime();
  const [pendingPayload, setPendingPayload] = useState<Record<string, any>>({ pending: [], entries: [] });
  const [drafts, setDrafts] = useState<MappingDraftItem[]>([]);
  const [singleDraft, setSingleDraft] = useState(EMPTY_SINGLE_DRAFT);
  const [singleSaving, setSingleSaving] = useState(false);
  const [batchSavePhase, setBatchSavePhase] = useState<BatchSavePhase>("idle");
  const [editingEntryKey, setEditingEntryKey] = useState("");
  const [entriesKeyword, setEntriesKeyword] = useState("");
  const [entriesRuleKind, setEntriesRuleKind] = useState("all");
  const [previewText, setPreviewText] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [conflictPreview, setConflictPreview] = useState<Record<string, any> | null>(null);
  const conflictResolverRef = useRef<{ flowType: ConflictFlowType; resolve: (value: boolean) => void } | null>(null);

  const pending = useMemo(() => (Array.isArray(pendingPayload.pending) ? pendingPayload.pending : []) as PendingMapping[], [pendingPayload]);
  const entries = useMemo(
    () => (Array.isArray(pendingPayload.entries) ? pendingPayload.entries : []) as Array<Record<string, any>>,
    [pendingPayload],
  );
  const normalizedEntries = useMemo(
    () => normalizeSavedMappingEntries(entries, MAPPING_RULE_CONFIG),
    [entries],
  );
  const editableEntries = useMemo(
    () => normalizedEntries.filter((entry) => entry.isEditable),
    [normalizedEntries],
  );
  const abnormalEntries = useMemo(
    () => normalizedEntries.filter((entry) => entry.status === "abnormal"),
    [normalizedEntries],
  );
  const filteredEntries = useMemo(
    () => filterSavedMappingEntries({ entries: editableEntries, ruleKind: entriesRuleKind, keyword: entriesKeyword }),
    [editableEntries, entriesKeyword, entriesRuleKind],
  );
  const savedEntriesSummary = useMemo(
    () => {
      const baseSummary = formatSavedEntriesSummary({
        totalCount: editableEntries.length,
        filteredCount: filteredEntries.length,
        keyword: entriesKeyword,
        ruleKind: entriesRuleKind,
      });
      if (!abnormalEntries.length) {
        return baseSummary;
      }
      return `${baseSummary}；另有 ${abnormalEntries.length} 条异常/不支持条目（仅只读展示）`;
    },
    [abnormalEntries.length, editableEntries.length, entriesKeyword, entriesRuleKind, filteredEntries.length],
  );
  const singleRuleConfig = MAPPING_RULE_CONFIG[singleDraft.ruleKind] || MAPPING_RULE_CONFIG.transferor_group;
  const singleSourceLabel = singleRuleConfig?.sourceLabel || "来源名称";
  const singleTargetLabel = singleRuleConfig?.targetLabel || "目标值";
  const editingExistingEntry = Boolean(editingEntryKey);
  const batchSaving = batchSavePhase === "in-flight";
  const batchWaitingConflict = batchSavePhase === "waiting-conflict";
  const saveDraftDisabled = batchSavePhase !== "idle" || singleSaving || Boolean(conflictPreview);
  const singleSaveDisabled = singleSaving || batchSavePhase !== "idle" || Boolean(conflictPreview);

  const loadMappings = useCallback(async () => {
    try {
      const payload = await commands.listMappings();
      setPendingPayload(payload || { pending: [], entries: [] });
      setPreviewError("");
    } catch (error) {
      setPreviewError(String((error as Error)?.message || error || "加载映射失败"));
    }
  }, [commands]);

  useEffect(() => {
    void loadMappings();
  }, [loadMappings]);

  useEffect(() => {
    if (!editingEntryKey) {
      return;
    }
    if (!editableEntries.some((entry) => entry.key === editingEntryKey)) {
      setEditingEntryKey("");
      setSingleDraft(EMPTY_SINGLE_DRAFT);
    }
  }, [editableEntries, editingEntryKey]);

  useEffect(() => () => {
    if (conflictResolverRef.current) {
      conflictResolverRef.current.resolve(false);
      conflictResolverRef.current = null;
    }
  }, []);

  const requestOverwriteConfirm = useCallback((preview: Record<string, any>, flowType: ConflictFlowType) => {
    if (conflictResolverRef.current) {
      setPreviewError("存在待确认冲突，请先完成确认后再继续保存。");
      return Promise.resolve(false);
    }
    if (flowType === "batch") {
      setBatchSavePhase("waiting-conflict");
      setPreviewText("批量保存已暂停，等待冲突确认...");
    }
    setConflictPreview(preview);
    setPreviewText(formatMappingConflictSummary(preview));
    setPreviewError("");
    return new Promise<boolean>((resolve) => {
      conflictResolverRef.current = { flowType, resolve };
    });
  }, []);

  const closeConflict = (confirmed: boolean) => {
    const resolver = conflictResolverRef.current;
    conflictResolverRef.current = null;
    if (resolver) {
      if (resolver.flowType === "batch") {
        setBatchSavePhase("in-flight");
      }
      resolver.resolve(confirmed);
    }
    if (!confirmed) {
      setPreviewText("已取消覆盖保存；原规则保持不变");
    }
    setConflictPreview(null);
  };

  const importPendingItem = (item: PendingMapping) => {
    const existing = new Set(drafts.map((draft) => draft.recordId));
    if (existing.has(item.record_id)) {
      return;
    }
    setDrafts((current) => [...current, toDraft(item)]);
    setPreviewText("已导入 1 条待补项，请直接在列表里填写");
    setPreviewError("");
  };

  const importAllPending = () => {
    if (!pending.length) {
      setPreviewError("当前没有待补映射可导入");
      return;
    }
    const existing = new Set(drafts.map((draft) => draft.recordId));
    const additions = pending.filter((item) => !existing.has(item.record_id)).map(toDraft);
    setDrafts((current) => [...current, ...additions]);
    setPreviewText(`已导入 ${additions.length} 条待补项，请直接在列表里填写`);
    setPreviewError("");
  };

  const updateDraft = (index: number, field: keyof MappingDraftItem, value: string) => {
    setDrafts((current) => {
      const next = [...current];
      const target = next[index];
      if (!target) {
        return current;
      }
      if (field === "ruleKind") {
        const previousRuleKind = target.previousRuleKind;
        target.ruleKind = value;
        if (!target.sourceName || target.sourceName === buildDraftSourceValue(previousRuleKind, target.rawRecord)) {
          target.sourceName = buildDraftSourceValue(value, target.rawRecord);
        }
        target.previousRuleKind = value;
        return next;
      }
      (target as any)[field] = value;
      return next;
    });
  };

  const saveDraftMappings = async () => {
    if (batchSavePhase !== "idle" || singleSaving || Boolean(conflictPreview)) {
      return;
    }
    const filledDrafts = drafts
      .map((draft) => ({
        ...draft,
        sourceName: String(draft.sourceName || "").trim(),
        targetValue: String(draft.targetValue || "").trim(),
        notes: String(draft.notes || "").trim(),
      }))
      .filter((draft) => draft.sourceName && draft.targetValue);

    if (!filledDrafts.length) {
      setPreviewError("请先在导入列表中至少填写一条完整规则");
      return;
    }

    setPreviewError("");
    setPreviewText(`正在逐条检查并保存 ${filledDrafts.length} 条已填写规则...`);
    setBatchSavePhase("in-flight");
    try {
      const result = await runBatchMappingUpsertFlow({
        drafts: filledDrafts,
        mappingRuleConfig: MAPPING_RULE_CONFIG,
        previewMapping: commands.previewMapping,
        saveMapping: commands.saveMapping,
        confirmOverwrite: (preview) => requestOverwriteConfirm(preview, "batch"),
      });

      setDrafts((current) => current.filter((item) => !result.savedRecordIds.has(item.recordId)));
      const affectedCount = result.refreshJobs.reduce((sum, item) => sum + Number(item.affected_count || 0), 0);
      const parts = [`已保存 ${result.savedCount} 条规则`];
      if (result.refreshJobs.length) {
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
      setPreviewText(parts.join("，"));
      setPreviewError(result.savedCount === 0 && result.failedCount > 0 ? "映射规则保存失败，请到任务页查看明细。" : "");
      await loadMappings();
    } catch {
      setPreviewError("映射规则保存失败，请到任务页查看明细。");
    } finally {
      setBatchSavePhase("idle");
    }
  };

  const saveSingleMapping = async () => {
    if (singleSaving || batchSavePhase !== "idle" || Boolean(conflictPreview)) {
      return;
    }
    const sourceName = String(singleDraft.sourceName || "").trim();
    const targetValue = String(singleDraft.targetValue || "").trim();
    if (!sourceName || !targetValue) {
      setPreviewError("请先填写完整的来源名称和目标值");
      return;
    }
    setSingleSaving(true);
    setPreviewError("");
    setPreviewText("正在检查并保存单条规则...");
    try {
      const result = await runMappingUpsertFlow({
        draft: {
          ruleKind: singleDraft.ruleKind,
          sourceName,
          targetValue,
          notes: singleDraft.notes,
        },
        mappingRuleConfig: MAPPING_RULE_CONFIG,
        previewMapping: commands.previewMapping,
        saveMapping: commands.saveMapping,
        confirmOverwrite: (preview) => requestOverwriteConfirm(preview, "single"),
      });
      if (result.cancelled) {
        return;
      }
      const payload = result.response || {};
      const actionLabel = result.preview?.mode === "overwrite"
        ? "映射规则已覆盖"
        : result.preview?.mode === "update"
          ? "映射规则已更新"
          : "映射规则已保存";
      const capacityNotice = formatCapacityNotice(payload);
      setPreviewText(
        payload.job_id
          ? `${actionLabel}，已启动映射回刷任务：${payload.job_id}，影响 ${Number(payload.affected_count || 0)} 条记录${capacityNotice ? `。${capacityNotice}` : ""}`
          : `${actionLabel}，当前没有匹配到需要回刷的记录`,
      );
      setPreviewError("");
      await loadMappings();
    } catch {
      setPreviewError("映射规则保存失败，请到任务页查看明细。");
    } finally {
      setSingleSaving(false);
    }
  };

  const triggerPendingReprocess = async () => {
    if (batchSavePhase !== "idle" || singleSaving || Boolean(conflictPreview)) {
      return;
    }
    try {
      const payload = await commands.reprocessPendingMappings({});
      const capacityNotice = formatCapacityNotice(payload || {});
      if (payload?.job_id) {
        setPreviewText(
          capacityNotice
            ? `已启动待补映射批量重处理：${payload.job_id}，共 ${Number(payload.affected_count || 0)} 条记录。${capacityNotice}`
            : `已启动待补映射批量重处理：${payload.job_id}，共 ${Number(payload.affected_count || 0)} 条记录`,
        );
      } else {
        setPreviewText("当前没有待补映射需要重处理");
      }
      setPreviewError("");
      await loadMappings();
    } catch {
      setPreviewError("待补映射批量重处理失败，请到任务页查看明细。");
    }
  };

  const startNewSingleDraft = () => {
    setSingleDraft(EMPTY_SINGLE_DRAFT);
    setEditingEntryKey("");
    setPreviewError("");
    setPreviewText("已切换到新建规则模式");
  };

  const loadEntryToSingleDraft = (entry: (typeof filteredEntries)[number]) => {
    setSingleDraft({
      ruleKind: entry.ruleKind,
      sourceName: entry.sourceName,
      targetValue: entry.targetValue,
      notes: entry.notes,
    });
    setEditingEntryKey(entry.key);
    setPreviewError("");
    setPreviewText(`已加载规则：${entry.sourceName || "未命名来源"}（${entry.ruleTitle}）`);
  };

  return (
    <div data-testid={PAGE_TEST_IDS.mappings.page}>
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <Card title="待补映射" data-testid={PAGE_TEST_IDS.mappings.pendingList}>
          <Space direction="vertical" style={{ width: "100%" }}>
            <Typography.Text>{pendingSummary(pendingPayload, pending.length)}</Typography.Text>
            <Space>
              <Button onClick={importAllPending}>导入全部待补项</Button>
              <Button
                id="runPendingMappingRefreshBtn"
                onClick={triggerPendingReprocess}
                disabled={batchSavePhase !== "idle" || singleSaving || Boolean(conflictPreview)}
              >
                一键重处理当前所有待补项
              </Button>
            </Space>
            {pending.length === 0 ? (
              <Typography.Text type="secondary">当前没有待补映射</Typography.Text>
            ) : (
              pending.map((item) => {
                const record = item.payload || {};
                return (
                  <Card key={item.record_id} size="small">
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Typography.Text>{`${item.project_code || "无编号"} · ${record["项目名称"] || "未命名项目"}`}</Typography.Text>
                      <Typography.Text type="secondary">{`公司：${pendingRecordCompany(record) || "未识别"}`}</Typography.Text>
                      <Space>
                        <Button id="importPendingMappingBtn" onClick={() => importPendingItem(item)}>导入到草稿</Button>
                      </Space>
                    </Space>
                  </Card>
                );
              })
            )}
          </Space>
        </Card>

        <Card title="规则编辑" data-testid={PAGE_TEST_IDS.mappings.editor}>
          <Space direction="vertical" style={{ width: "100%" }}>
            {editingExistingEntry ? (
              <Typography.Text type="warning">
                正在编辑已保存规则；来源名称与规则类型已锁定，如需新建请点击“新建规则”
              </Typography.Text>
            ) : (
              <Typography.Text type="secondary">新建规则模式：可编辑规则类型、来源名称、目标值与备注</Typography.Text>
            )}
            <Space wrap>
              <Select
                value={singleDraft.ruleKind}
                style={{ width: 220 }}
                disabled={editingExistingEntry}
                aria-label="规则类型"
                onChange={(value) => setSingleDraft((draft) => ({ ...draft, ruleKind: value }))}
                options={Object.entries(MAPPING_RULE_CONFIG).map(([value, config]) => ({ value, label: config.title }))}
              />
              <Input
                aria-label="来源名称"
                placeholder={singleSourceLabel}
                value={singleDraft.sourceName}
                disabled={editingExistingEntry}
                onChange={(event) => setSingleDraft((draft) => ({ ...draft, sourceName: event.target.value }))}
              />
              <Input
                aria-label="目标值"
                placeholder={singleTargetLabel}
                value={singleDraft.targetValue}
                onChange={(event) => setSingleDraft((draft) => ({ ...draft, targetValue: event.target.value }))}
              />
              <Input
                aria-label="备注"
                placeholder="备注"
                value={singleDraft.notes}
                onChange={(event) => setSingleDraft((draft) => ({ ...draft, notes: event.target.value }))}
              />
              <Button type="primary" loading={singleSaving} disabled={singleSaveDisabled} onClick={saveSingleMapping}>保存单条规则</Button>
              {editingExistingEntry ? <Button onClick={startNewSingleDraft}>新建规则</Button> : null}
            </Space>

            {drafts.map((draft, index) => (
              <div className="mapping-draft-item" data-draft-index={index} key={draft.recordId}>
                <Space direction="vertical" style={{ width: "100%" }}>
                  <Typography.Text>{`${draft.project_code || "无编号"} · ${draft.project_name || "未命名项目"}`}</Typography.Text>
                  <Space wrap>
                    <Select
                      value={draft.ruleKind}
                      data-draft-field="ruleKind"
                      onChange={(value) => updateDraft(index, "ruleKind", value)}
                      options={Object.entries(MAPPING_RULE_CONFIG).map(([value, config]) => ({ value, label: config.title }))}
                      style={{ width: 200 }}
                    />
                    <Input
                      value={draft.sourceName}
                      data-draft-field="sourceName"
                      onChange={(event) => updateDraft(index, "sourceName", event.target.value)}
                    />
                    <Input
                      value={draft.targetValue}
                      data-draft-field="targetValue"
                      onChange={(event) => updateDraft(index, "targetValue", event.target.value)}
                    />
                    <Input
                      value={draft.notes}
                      data-draft-field="notes"
                      onChange={(event) => updateDraft(index, "notes", event.target.value)}
                    />
                  </Space>
                </Space>
              </div>
            ))}

            <Button id="saveDraftMappingsBtn" type="primary" loading={batchSaving} disabled={saveDraftDisabled} onClick={saveDraftMappings}>
              {batchWaitingConflict ? "等待冲突确认..." : batchSaving ? "批量保存中..." : "保存已填写规则"}
            </Button>
          </Space>
        </Card>

        <Card title="预览 / 结果" data-testid={PAGE_TEST_IDS.mappings.preview}>
          <Space direction="vertical" style={{ width: "100%" }}>
            <Typography.Text>{previewText || "等待预览或保存结果"}</Typography.Text>
            {previewError ? <Typography.Text type="danger">{previewError}</Typography.Text> : null}
          </Space>
        </Card>

        <Card title="已保存规则">
          <div id="mappingEntriesTableWrap" className="records-table-wrap compact-list">
            <Space direction="vertical" style={{ width: "100%" }}>
              <Space wrap>
                <Select
                  value={entriesRuleKind}
                  style={{ width: 220 }}
                  aria-label="已保存规则类型筛选"
                  onChange={setEntriesRuleKind}
                  options={[
                    { value: "all", label: "全部规则类型" },
                    ...Object.entries(MAPPING_RULE_CONFIG).map(([value, config]) => ({ value, label: config.title })),
                  ]}
                />
                <Input
                  aria-label="已保存规则关键字筛选"
                  placeholder="按来源/目标值/备注筛选"
                  value={entriesKeyword}
                  onChange={(event) => setEntriesKeyword(event.target.value)}
                />
              </Space>
              <Typography.Text>{savedEntriesSummary}</Typography.Text>
              {filteredEntries.length === 0 ? (
                <Typography.Text type="secondary">
                  {editableEntries.length === 0
                    ? "当前没有单独维护的映射规则；已录入记录也可能是网页本身已提供完整类型和集团信息"
                    : "当前筛选条件没有命中规则，请调整规则类型或关键字"}
                </Typography.Text>
              ) : (
                filteredEntries.map((entry) => (
                  <Card key={entry.key} size="small">
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Typography.Text strong>{entry.sourceName || "未命名来源"}</Typography.Text>
                      <Typography.Text type="secondary">
                        {`${entry.ruleTitle} · ${entry.targetValue || "空值"}`}
                      </Typography.Text>
                      <Typography.Text type="secondary">
                        {`备注：${entry.notes || "无"}${entry.updatedAt ? ` · 最近更新：${entry.updatedAt}` : ""}`}
                      </Typography.Text>
                      <Space>
                        <Button onClick={() => loadEntryToSingleDraft(entry)}>加载到单条编辑</Button>
                        {editingEntryKey === entry.key ? <Typography.Text type="warning">当前正在编辑该规则</Typography.Text> : null}
                      </Space>
                    </Space>
                  </Card>
                ))
              )}
              {abnormalEntries.length > 0 ? (
                <Card size="small">
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <Typography.Text type="warning">异常/不支持条目（只读）：{abnormalEntries.length} 条</Typography.Text>
                    {abnormalEntries.map((entry) => (
                      <div key={entry.key}>
                        <Typography.Text>{entry.sourceName || "未命名来源"}</Typography.Text>
                        <Typography.Text type="secondary">{` · ${entry.ruleTitle}`}</Typography.Text>
                        <Typography.Text type="secondary">{` · ${entry.issueText.join("；")}`}</Typography.Text>
                      </div>
                    ))}
                  </Space>
                </Card>
              ) : null}
            </Space>
          </div>
        </Card>
      </Space>

      {conflictPreview ? (
        <Card title="冲突确认" role="dialog">
          <Space direction="vertical">
            <Typography.Paragraph>{formatMappingConflictSummary(conflictPreview)}</Typography.Paragraph>
            <Space>
              <Button onClick={() => closeConflict(false)}>取消覆盖</Button>
              <Button type="primary" onClick={() => closeConflict(true)}>确认覆盖</Button>
            </Space>
          </Space>
        </Card>
      ) : null}
    </div>
  );
}
