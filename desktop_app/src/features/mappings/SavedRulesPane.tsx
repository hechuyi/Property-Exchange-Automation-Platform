import { Button, Card, Input, Select, Space, Typography } from "antd";
import type { SavedMappingEntry } from "./entries";

type RuleOption = {
  value: string;
  label: string;
};

type SavedRulesPaneProps = {
  entriesRuleKind: string;
  entriesKeyword: string;
  ruleOptions: RuleOption[];
  onEntriesRuleKindChange: (value: string) => void;
  onEntriesKeywordChange: (value: string) => void;
  savedEntriesSummary: string;
  editableEntries: SavedMappingEntry[];
  filteredEntries: SavedMappingEntry[];
  abnormalEntries: SavedMappingEntry[];
  editingEntryKey: string;
  onLoadEntryToSingleDraft: (entry: SavedMappingEntry) => void;
};

export function SavedRulesPane({
  entriesRuleKind,
  entriesKeyword,
  ruleOptions,
  onEntriesRuleKindChange,
  onEntriesKeywordChange,
  savedEntriesSummary,
  editableEntries,
  filteredEntries,
  abnormalEntries,
  editingEntryKey,
  onLoadEntryToSingleDraft,
}: SavedRulesPaneProps) {
  return (
    <Card title="已保存规则">
      <div id="mappingEntriesTableWrap" className="records-table-wrap compact-list">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Space wrap>
            <Select
              value={entriesRuleKind}
              style={{ width: 220 }}
              aria-label="已保存规则类型筛选"
              onChange={onEntriesRuleKindChange}
              options={[
                { value: "all", label: "全部规则类型" },
                ...ruleOptions,
              ]}
            />
            <Input
              aria-label="已保存规则关键字筛选"
              placeholder="按来源/目标值/备注筛选"
              value={entriesKeyword}
              onChange={(event) => onEntriesKeywordChange(event.target.value)}
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
                    <Button onClick={() => onLoadEntryToSingleDraft(entry)}>加载到单条编辑</Button>
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
  );
}
