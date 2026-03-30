import { Button, Card, Input, Select, Space, Typography } from "antd";
import { PAGE_TEST_IDS } from "../../testing/selectors";
import type { MappingDraftItem } from "./model";

type RuleOption = {
  value: string;
  label: string;
};

type SingleDraft = {
  ruleKind: string;
  sourceName: string;
  targetValue: string;
  notes: string;
};

type RuleEditorPaneProps = {
  editingExistingEntry: boolean;
  singleDraft: SingleDraft;
  singleSourceLabel: string;
  singleTargetLabel: string;
  ruleOptions: RuleOption[];
  singleSaving: boolean;
  singleSaveDisabled: boolean;
  onSingleDraftChange: (field: keyof SingleDraft, value: string) => void;
  onSaveSingle: () => void;
  onStartNewSingleDraft: () => void;
  drafts: MappingDraftItem[];
  onUpdateDraft: (index: number, field: keyof MappingDraftItem, value: string) => void;
  batchSaving: boolean;
  batchWaitingConflict: boolean;
  saveDraftDisabled: boolean;
  onSaveDraftMappings: () => void;
  previewText: string;
  previewError: string;
};

export function RuleEditorPane({
  editingExistingEntry,
  singleDraft,
  singleSourceLabel,
  singleTargetLabel,
  ruleOptions,
  singleSaving,
  singleSaveDisabled,
  onSingleDraftChange,
  onSaveSingle,
  onStartNewSingleDraft,
  drafts,
  onUpdateDraft,
  batchSaving,
  batchWaitingConflict,
  saveDraftDisabled,
  onSaveDraftMappings,
  previewText,
  previewError,
}: RuleEditorPaneProps) {
  return (
    <Card title="当前编辑器" data-testid={PAGE_TEST_IDS.mappings.editor} data-layout-region="editor">
      <Space direction="vertical" style={{ width: "100%" }}>
        {editingExistingEntry ? (
          <Typography.Text type="warning">
            正在编辑已保存规则；来源名称与规则类型已锁定，如需新建请点击“新建规则”
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary">当前编辑器始终保持可见，导入待补项后可直接在此完成补录。</Typography.Text>
        )}
        <Space wrap>
          <Select
            value={singleDraft.ruleKind}
            style={{ width: 220 }}
            disabled={editingExistingEntry}
            aria-label="规则类型"
            onChange={(value) => onSingleDraftChange("ruleKind", value)}
            options={ruleOptions}
          />
          <Input
            aria-label="来源名称"
            placeholder={singleSourceLabel}
            value={singleDraft.sourceName}
            disabled={editingExistingEntry}
            onChange={(event) => onSingleDraftChange("sourceName", event.target.value)}
          />
          <Input
            aria-label="目标值"
            placeholder={singleTargetLabel}
            value={singleDraft.targetValue}
            onChange={(event) => onSingleDraftChange("targetValue", event.target.value)}
          />
          <Input
            aria-label="备注"
            placeholder="备注"
            value={singleDraft.notes}
            onChange={(event) => onSingleDraftChange("notes", event.target.value)}
          />
          <Button type="primary" loading={singleSaving} disabled={singleSaveDisabled} onClick={onSaveSingle}>保存单条规则</Button>
          {editingExistingEntry ? <Button onClick={onStartNewSingleDraft}>新建规则</Button> : null}
        </Space>

        <div data-testid={PAGE_TEST_IDS.mappings.preview}>
          <Space direction="vertical" style={{ width: "100%" }}>
            <Typography.Text>{previewText || "等待预览或保存结果"}</Typography.Text>
            {previewError ? <Typography.Text type="danger">{previewError}</Typography.Text> : null}
          </Space>
        </div>

        {drafts.length === 0 ? (
          <Typography.Text type="secondary">从左侧队列导入待补项后，当前编辑器会在这里保持上下文。</Typography.Text>
        ) : (
          drafts.map((draft, index) => (
            <Card key={draft.recordId} size="small" className="mapping-draft-item" data-draft-index={index}>
              <Space direction="vertical" style={{ width: "100%" }}>
                <Typography.Text strong>{draft.project_name || "未命名项目"}</Typography.Text>
                <Typography.Text type="secondary">{draft.project_code || "无编号"}</Typography.Text>
                <Space wrap>
                  <Select
                    value={draft.ruleKind}
                    data-draft-field="ruleKind"
                    onChange={(value) => onUpdateDraft(index, "ruleKind", value)}
                    options={ruleOptions}
                    style={{ width: 200 }}
                  />
                  <Input
                    value={draft.sourceName}
                    data-draft-field="sourceName"
                    onChange={(event) => onUpdateDraft(index, "sourceName", event.target.value)}
                  />
                  <Input
                    value={draft.targetValue}
                    data-draft-field="targetValue"
                    onChange={(event) => onUpdateDraft(index, "targetValue", event.target.value)}
                  />
                  <Input
                    value={draft.notes}
                    data-draft-field="notes"
                    onChange={(event) => onUpdateDraft(index, "notes", event.target.value)}
                  />
                </Space>
              </Space>
            </Card>
          ))
        )}

        <Button id="saveDraftMappingsBtn" type="primary" loading={batchSaving} disabled={saveDraftDisabled} onClick={onSaveDraftMappings}>
          {batchWaitingConflict ? "等待冲突确认..." : batchSaving ? "批量保存中..." : "保存已填写规则"}
        </Button>
      </Space>
    </Card>
  );
}
