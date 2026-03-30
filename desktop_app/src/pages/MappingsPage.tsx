import { Button, Card, Space, Typography } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useDesktopRuntime } from "../desktop/provider";
import { PAGE_TEST_IDS } from "../testing/selectors";
import {
  formatBatchMappingSaveSummary,
  formatMappingConflictSummary,
  formatPendingReprocessSummary,
  formatSingleMappingSaveSummary,
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
  toDraft,
  type MappingDraftItem,
  type PendingMapping,
} from "../features/mappings/model";
import { PendingMappingsPane } from "../features/mappings/PendingMappingsPane";
import { RuleEditorPane } from "../features/mappings/RuleEditorPane";
import { SavedRulesPane } from "../features/mappings/SavedRulesPane";

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
  const ruleOptions = useMemo(
    () => Object.entries(MAPPING_RULE_CONFIG).map(([value, config]) => ({ value, label: config.title })),
    [],
  );
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
      setPreviewText(formatBatchMappingSaveSummary(result));
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
      const capacityNotice = formatCapacityNotice(payload);
      setPreviewText(formatSingleMappingSaveSummary(payload, result.preview, capacityNotice));
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
      setPreviewText(formatPendingReprocessSummary(payload || {}, capacityNotice));
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
        <div
          data-layout="remediation-workspace"
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(320px, 0.95fr) minmax(0, 1.25fr)",
            gap: 16,
            alignItems: "start",
          }}
        >
          <PendingMappingsPane
            pendingPayload={pendingPayload}
            pending={pending}
            disabled={batchSavePhase !== "idle" || singleSaving || Boolean(conflictPreview)}
            onImportAll={importAllPending}
            onImportItem={importPendingItem}
            onTriggerReprocess={() => {
              void triggerPendingReprocess();
            }}
          />

          <RuleEditorPane
            editingExistingEntry={editingExistingEntry}
            singleDraft={singleDraft}
            singleSourceLabel={singleSourceLabel}
            singleTargetLabel={singleTargetLabel}
            ruleOptions={ruleOptions}
            singleSaving={singleSaving}
            singleSaveDisabled={singleSaveDisabled}
            onSingleDraftChange={(field, value) => {
              setSingleDraft((draft) => ({ ...draft, [field]: value }));
            }}
            onSaveSingle={() => {
              void saveSingleMapping();
            }}
            onStartNewSingleDraft={startNewSingleDraft}
            drafts={drafts}
            onUpdateDraft={updateDraft}
            batchSaving={batchSaving}
            batchWaitingConflict={batchWaitingConflict}
            saveDraftDisabled={saveDraftDisabled}
            onSaveDraftMappings={() => {
              void saveDraftMappings();
            }}
            previewText={previewText}
            previewError={previewError}
          />
        </div>

        <SavedRulesPane
          entriesRuleKind={entriesRuleKind}
          entriesKeyword={entriesKeyword}
          ruleOptions={ruleOptions}
          onEntriesRuleKindChange={setEntriesRuleKind}
          onEntriesKeywordChange={setEntriesKeyword}
          savedEntriesSummary={savedEntriesSummary}
          editableEntries={editableEntries}
          filteredEntries={filteredEntries}
          abnormalEntries={abnormalEntries}
          editingEntryKey={editingEntryKey}
          onLoadEntryToSingleDraft={loadEntryToSingleDraft}
        />
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
