import test from "node:test";
import assert from "node:assert/strict";

import { createMappingsPanel } from "../src/panels/mappings.js";

function createFakeElement({ value = "" } = {}) {
  const listeners = new Map();
  return {
    disabled: false,
    innerHTML: "",
    value,
    dataset: {},
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    async click() {
      const handler = listeners.get("click");
      if (handler) {
        await handler({ currentTarget: this, target: this });
      }
    },
  };
}

test("mappings panel consumes sections and summary even when legacy pending is absent", () => {
  const panelEl = createFakeElement();
  const elementMap = new Map([
    ["#panel-mappings", panelEl],
  ]);

  const panel = createMappingsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    MAPPING_RULES: {
      transferor_group: {
        sourceLabel: "转让方",
        targetLabel: "集团",
        title: "转让方 -> 集团",
      },
    },
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    getMappings() {
      return {
        entries: [],
        sections: [
          {
            section_id: "mapping_gap_resolution",
            title: "待映射补全",
            count: 1,
            cta_kind: "reprocess_pending",
            items: [
              {
                record_id: "rec-1",
                revision_id: 11,
                raw_business_label: "实物资产转让",
                status_label: "待补映射",
                status_detail: "补齐映射规则后回刷",
                queue_section: "mapping_gap_resolution",
                evidence_codes: ["missing_type"],
              },
            ],
          },
        ],
        summary: {
          actionable_count: 1,
          mapping_gap_count: 1,
          mapping_conflict_count: 0,
          audit_count: 0,
        },
      };
    },
    getMappingDraft() {
      return {
        rule_kind: "transferor_group",
        source_name: "",
        target_value: "",
        notes: "",
        confirm_overwrite: false,
      };
    },
    getMappingPreview() {
      return null;
    },
    getMappingMessage() {
      return "";
    },
    buildMappingTargetControl() {
      return "<input id=\"mapping-target-value\">";
    },
    onRuleKindChange() {},
    onDraftInput() {},
    onDraftPreview() {},
    onDraftSave() {},
    onDraftReset() {},
    onUseSuggestion() {},
    onResolveConflict() {},
    onSectionAction() {},
  });

  panel.render();

  assert.match(panelEl.innerHTML, /待映射补全/);
  assert.match(panelEl.innerHTML, /全部回刷/);
  assert.match(panelEl.innerHTML, /补齐映射规则后回刷/);
});

test("mappings panel suppresses mapping CTA when pending review leaves mapping backlog empty", () => {
  const panelEl = createFakeElement();
  const elementMap = new Map([
    ["#panel-mappings", panelEl],
  ]);

  const panel = createMappingsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    MAPPING_RULES: {
      transferor_group: {
        sourceLabel: "转让方",
        targetLabel: "集团",
        title: "转让方 -> 集团",
      },
    },
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    getMappings() {
      return {
        entries: [],
        sections: [
          {
            section_id: "mapping_gap_resolution",
            title: "待映射补全",
            count: 0,
            cta_kind: "reprocess_pending",
            items: [],
          },
          {
            section_id: "audit",
            title: "审计只读",
            count: 0,
            cta_kind: "read_only",
            items: [],
          },
        ],
        summary: {
          actionable_count: 0,
          mapping_gap_count: 0,
          mapping_conflict_count: 0,
          audit_count: 0,
        },
      };
    },
    getMappingDraft() {
      return {
        rule_kind: "transferor_group",
        source_name: "",
        target_value: "",
        notes: "",
        confirm_overwrite: false,
      };
    },
    getMappingPreview() {
      return null;
    },
    getMappingMessage() {
      return "";
    },
    buildMappingTargetControl() {
      return "<input id=\"mapping-target-value\">";
    },
    onRuleKindChange() {},
    onDraftInput() {},
    onDraftPreview() {},
    onDraftSave() {},
    onDraftReset() {},
    onUseSuggestion() {},
    onResolveConflict() {},
    onSectionAction() {},
  });

  panel.render();

  assert.match(panelEl.innerHTML, /待映射补全/);
  assert.doesNotMatch(panelEl.innerHTML, /全部回刷/);
  assert.doesNotMatch(panelEl.innerHTML, /btn-mappings-section-mapping_gap_resolution/);
});

test("mappings panel renders rich section-item fields preserved by the adapter", () => {
  const panelEl = createFakeElement();
  const elementMap = new Map([
    ["#panel-mappings", panelEl],
  ]);

  const panel = createMappingsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    MAPPING_RULES: {
      transferor_group: {
        sourceLabel: "转让方",
        targetLabel: "集团",
        title: "转让方 -> 集团",
      },
    },
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    getMappings() {
      return {
        entries: [],
        sections: [
          {
            section_id: "mapping_gap_resolution",
            title: "待映射补全",
            count: 1,
            cta_kind: "reprocess_pending",
            items: [
              {
                record_id: "rec-1",
                revision_id: 11,
                project_code: "XM-001",
                source_name: "华润置地",
                raw_business_label: "实物资产转让",
                status_label: "待补映射",
                status_detail: "补齐映射规则后回刷",
                queue_section: "mapping_gap_resolution",
                gap_codes: ["missing_type"],
                recommended_rule: {
                  rule_kind: "transferor_group",
                  title: "转让方 -> 集团",
                  source_name: "华润置地",
                  target_value: "华润集团",
                },
                candidate_resolutions: [
                  {
                    title: "华润集团",
                    rule_kind: "transferor_group",
                    source_name: "华润置地",
                    target_value: "华润集团",
                    evidence_chain: ["catalog_match"],
                  },
                ],
                evidence_codes: ["missing_type"],
              },
            ],
          },
        ],
        summary: {
          actionable_count: 1,
          mapping_gap_count: 1,
          mapping_conflict_count: 0,
          audit_count: 0,
        },
      };
    },
    getMappingDraft() {
      return {
        rule_kind: "transferor_group",
        source_name: "",
        target_value: "",
        notes: "",
        confirm_overwrite: false,
      };
    },
    getMappingPreview() {
      return null;
    },
    getMappingMessage() {
      return "";
    },
    buildMappingTargetControl() {
      return "<input id=\"mapping-target-value\">";
    },
    onRuleKindChange() {},
    onDraftInput() {},
    onDraftPreview() {},
    onDraftSave() {},
    onDraftReset() {},
    onUseSuggestion() {},
    onResolveConflict() {},
    onSectionAction() {},
  });

  panel.render();

  assert.match(panelEl.innerHTML, /XM-001/);
  assert.match(panelEl.innerHTML, /华润置地/);
  assert.match(panelEl.innerHTML, /缺口：missing_type/);
  assert.match(panelEl.innerHTML, /推荐：转让方 -> 集团 · 华润置地 → 华润集团/);
  assert.match(panelEl.innerHTML, /华润置地 → 华润集团/);
  assert.match(panelEl.innerHTML, /依据：catalog_match/);
});

test("mappings panel renders readable evidence text for object evidence_chain entries", () => {
  const panelEl = createFakeElement();
  const elementMap = new Map([
    ["#panel-mappings", panelEl],
  ]);

  const panel = createMappingsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    MAPPING_RULES: {
      transferor_group: {
        sourceLabel: "转让方",
        targetLabel: "集团",
        title: "转让方 -> 集团",
      },
    },
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    getMappings() {
      return {
        entries: [],
        sections: [
          {
            section_id: "mapping_conflict_resolution",
            title: "待映射冲突",
            count: 1,
            cta_kind: "read_only",
            items: [
              {
                record_id: "rec-1",
                revision_id: 11,
                project_code: "XM-001",
                source_name: "华润置地",
                state: "mapping_conflict",
                status_label: "待映射冲突",
                status_detail: "存在多个候选映射",
                queue_section: "mapping_conflict_resolution",
                candidate_resolutions: [
                  {
                    title: "华润集团",
                    rule_kind: "transferor_group",
                    source_name: "华润置地",
                    target_value: "华润集团",
                    evidence_chain: [
                      "catalog_match",
                      {
                        label: "目录候选",
                        match_field: "transferor",
                        target_field: "group_name",
                        source_name: "华润置地",
                        target_value: "华润集团",
                      },
                    ],
                  },
                ],
              },
            ],
          },
        ],
        summary: {
          actionable_count: 1,
          mapping_gap_count: 0,
          mapping_conflict_count: 1,
          audit_count: 0,
        },
      };
    },
    getMappingDraft() {
      return {
        rule_kind: "transferor_group",
        source_name: "",
        target_value: "",
        notes: "",
        confirm_overwrite: false,
      };
    },
    getMappingPreview() {
      return null;
    },
    getMappingMessage() {
      return "";
    },
    buildMappingTargetControl() {
      return "<input id=\"mapping-target-value\">";
    },
    onRuleKindChange() {},
    onDraftInput() {},
    onDraftPreview() {},
    onDraftSave() {},
    onDraftReset() {},
    onUseSuggestion() {},
    onResolveConflict() {},
    onSectionAction() {},
  });

  panel.render();

  assert.match(panelEl.innerHTML, /label: 目录候选; match_field: transferor; target_field: group_name; source_name: 华润置地; target_value: 华润集团/);
  assert.doesNotMatch(panelEl.innerHTML, /\[object Object\]/);
});

test("mappings panel renders mapping conflicts as decisions rather than errors or empty gaps", () => {
  const panelEl = createFakeElement();
  const elementMap = new Map([
    ["#panel-mappings", panelEl],
  ]);

  const panel = createMappingsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    MAPPING_RULES: {
      transferor_group: {
        sourceLabel: "转让方",
        targetLabel: "集团",
        title: "转让方 -> 集团",
      },
    },
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    getMappings() {
      return {
        entries: [],
        sections: [
          {
            section_id: "mapping_conflict_resolution",
            title: "待映射冲突",
            count: 1,
            cta_kind: "read_only",
            items: [
              {
                record_id: "rec-conflict",
                project_code: "XM-CONFLICT",
                source_name: "上海桥盛拆迁有限公司",
                state: "mapping_conflict",
                status_label: "映射冲突",
                status_detail: "存在多个映射候选结果，需要人工裁决",
                gap_codes: [],
                candidate_resolutions: [
                  {
                    title: "转让方 -> 集团",
                    rule_kind: "transferor_group",
                    source_name: "上海桥盛拆迁有限公司",
                    target_value: "上海市杨浦区国有资产监督管理委员会",
                  },
                ],
              },
            ],
          },
        ],
        summary: {
          actionable_count: 1,
          mapping_gap_count: 0,
          mapping_conflict_count: 1,
          audit_count: 0,
        },
      };
    },
    getMappingDraft() {
      return {
        rule_kind: "transferor_group",
        source_name: "",
        target_value: "",
        notes: "",
        confirm_overwrite: false,
      };
    },
    getMappingPreview() {
      return null;
    },
    getMappingMessage() {
      return "";
    },
    buildMappingTargetControl() {
      return "<input id=\"mapping-target-value\">";
    },
    onRuleKindChange() {},
    onDraftInput() {},
    onDraftPreview() {},
    onDraftSave() {},
    onDraftReset() {},
    onUseSuggestion() {},
    onResolveConflict() {},
    onSectionAction() {},
  });

  panel.render();

  assert.match(panelEl.innerHTML, /候选：1 个/);
  assert.doesNotMatch(panelEl.innerHTML, /缺口：—/);
  assert.doesNotMatch(panelEl.innerHTML, /class="badge failed"/);
  assert.doesNotMatch(panelEl.innerHTML, /btn-danger/);
});

test("mappings panel does not render raw business label sentinel from item or evidence fields", () => {
  const panelEl = createFakeElement();
  const elementMap = new Map([
    ["#panel-mappings", panelEl],
  ]);

  const panel = createMappingsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    MAPPING_RULES: {
      transferor_group: {
        sourceLabel: "转让方",
        targetLabel: "集团",
        title: "转让方 -> 集团",
      },
    },
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    getMappings() {
      return {
        entries: [],
        sections: [
          {
            section_id: "mapping_gap_resolution",
            title: "待映射补全",
            count: 1,
            cta_kind: "reprocess_pending",
            items: [
              {
                record_id: "rec-sentinel",
                revision_id: 11,
                raw_business_label: "UNTRUSTED_EXTERNAL_TEXT",
                business_label: "UNTRUSTED_EXTERNAL_TEXT",
                source_name: "UNTRUSTED_EXTERNAL_TEXT",
                state: "pending_mapping",
                status_label: "待补映射",
                status_detail: "补齐映射规则后回刷",
                queue_section: "mapping_gap_resolution",
                gap_codes: ["missing_type"],
                candidate_resolutions: [
                  {
                    title: "UNTRUSTED_EXTERNAL_TEXT",
                    rule_kind: "transferor_group",
                    source_name: "UNTRUSTED_EXTERNAL_TEXT",
                    target_value: "安全集团",
                    evidence_chain: [
                      {
                        label: "UNTRUSTED_EXTERNAL_TEXT",
                        source_name: "UNTRUSTED_EXTERNAL_TEXT",
                        target_value: "安全集团",
                      },
                    ],
                  },
                ],
              },
            ],
          },
        ],
        summary: {
          actionable_count: 1,
          mapping_gap_count: 1,
          mapping_conflict_count: 0,
          audit_count: 0,
        },
      };
    },
    getMappingDraft() {
      return {
        rule_kind: "transferor_group",
        source_name: "",
        target_value: "",
        notes: "",
        confirm_overwrite: false,
      };
    },
    getMappingPreview() {
      return null;
    },
    getMappingMessage() {
      return "";
    },
    buildMappingTargetControl() {
      return "<input id=\"mapping-target-value\">";
    },
    onRuleKindChange() {},
    onDraftInput() {},
    onDraftPreview() {},
    onDraftSave() {},
    onDraftReset() {},
    onUseSuggestion() {},
    onResolveConflict() {},
    onSectionAction() {},
  });

  panel.render();

  assert.doesNotMatch(panelEl.innerHTML, /UNTRUSTED_EXTERNAL_TEXT/);
  assert.match(panelEl.innerHTML, /未识别项目类型|待补映射/);
});

test("mappings panel dispatches mapping-gap CTA by cta_kind", async () => {
  const panelEl = createFakeElement();
  const actionButton = createFakeElement();
  const sectionActions = [];
  const elementMap = new Map([
    ["#panel-mappings", panelEl],
    ["#btn-mappings-section-mapping_gap_resolution", actionButton],
  ]);

  const panel = createMappingsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    MAPPING_RULES: {
      transferor_group: {
        sourceLabel: "转让方",
        targetLabel: "集团",
        title: "转让方 -> 集团",
      },
    },
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    getMappings() {
      return {
        entries: [],
        sections: [
          {
            section_id: "mapping_gap_resolution",
            title: "待映射补全",
            count: 1,
            cta_kind: "reprocess_pending",
            items: [],
          },
        ],
        summary: {
          actionable_count: 1,
          mapping_gap_count: 1,
          mapping_conflict_count: 0,
          audit_count: 0,
        },
      };
    },
    getMappingDraft() {
      return {
        rule_kind: "transferor_group",
        source_name: "",
        target_value: "",
        notes: "",
        confirm_overwrite: false,
      };
    },
    getMappingPreview() {
      return null;
    },
    getMappingMessage() {
      return "";
    },
    buildMappingTargetControl() {
      return "<input id=\"mapping-target-value\">";
    },
    onRuleKindChange() {},
    onDraftInput() {},
    onDraftPreview() {},
    onDraftSave() {},
    onDraftReset() {},
    onUseSuggestion() {},
    onResolveConflict() {},
    async onSectionAction(section) {
      sectionActions.push(section);
    },
  });

  panel.render();
  await actionButton.click();

  assert.deepEqual(sectionActions, [
    {
      section_id: "mapping_gap_resolution",
      title: "待映射补全",
      count: 1,
      cta_kind: "reprocess_pending",
      items: [],
    },
  ]);
});

test("mappings panel dispatches edit and delete handlers for saved entries", async () => {
  const panelEl = createFakeElement();
  const editButton = createFakeElement();
  editButton.dataset.entryId = "entry-1";
  const deleteButton = createFakeElement();
  deleteButton.dataset.entryId = "entry-1";
  const edited = [];
  const deleted = [];
  const entries = [
    {
      entry_id: "entry-1",
      rule_kind: "transferor_group",
      rule_title: "转让方 -> 集团",
      source_name: "中铁",
      target_value: "中铁集团",
      match_field: "transferor",
      target_field: "group_name",
      notes: "来自人工校验",
      updated_at: "2026-04-12T10:00:00",
    },
  ];
  const elementMap = new Map([
    ["#panel-mappings", panelEl],
  ]);

  const panel = createMappingsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$(selector) {
      if (selector === ".btn-edit-mapping-entry") return [editButton];
      if (selector === ".btn-delete-mapping-entry") return [deleteButton];
      return [];
    },
    MAPPING_RULES: {
      transferor_group: {
        sourceLabel: "转让方",
        targetLabel: "集团",
        title: "转让方 -> 集团",
      },
    },
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    getMappings() {
      return {
        entries,
        sections: [],
        summary: {
          actionable_count: 0,
          mapping_gap_count: 0,
          mapping_conflict_count: 0,
          audit_count: 0,
        },
      };
    },
    getMappingDraft() {
      return {
        entry_id: "",
        rule_kind: "transferor_group",
        source_name: "",
        target_value: "",
        notes: "",
        confirm_overwrite: false,
      };
    },
    getMappingPreview() {
      return null;
    },
    getMappingMessage() {
      return "";
    },
    buildMappingTargetControl() {
      return "<input id=\"mapping-target-value\">";
    },
    onRuleKindChange() {},
    onDraftInput() {},
    onDraftPreview() {},
    onDraftSave() {},
    onDraftReset() {},
    onUseSuggestion() {},
    onResolveConflict() {},
    onSectionAction() {},
    onEditEntry(entry) {
      edited.push(entry);
    },
    async onDeleteEntry(entry) {
      deleted.push(entry);
    },
  });

  panel.render();
  await editButton.click();
  await deleteButton.click();

  assert.deepEqual(edited, [entries[0]]);
  assert.deepEqual(deleted, [entries[0]]);
  assert.match(panelEl.innerHTML, /编辑/);
  assert.match(panelEl.innerHTML, /删除/);
});

test("mappings panel shows dedicated copy for conflict section instead of gap copy", () => {
  const panelEl = createFakeElement();
  const elementMap = new Map([
    ["#panel-mappings", panelEl],
  ]);

  const panel = createMappingsPanel({
    $(selector) {
      return elementMap.get(selector) || null;
    },
    $$() {
      return [];
    },
    MAPPING_RULES: {
      transferor_group: {
        sourceLabel: "转让方",
        targetLabel: "集团",
        title: "转让方 -> 集团",
      },
    },
    escapeHtml(value) {
      return String(value || "");
    },
    display(value) {
      return String(value || "");
    },
    formatJobTime(value) {
      return String(value || "");
    },
    num(value) {
      return Number.parseInt(value, 10) || 0;
    },
    getMappings() {
      return {
        entries: [],
        sections: [
          {
            section_id: "mapping_conflict_resolution",
            title: "待映射冲突",
            count: 1,
            cta_kind: "read_only",
            items: [],
          },
        ],
        summary: {
          actionable_count: 1,
          mapping_gap_count: 0,
          mapping_conflict_count: 1,
          audit_count: 0,
        },
      };
    },
    getMappingDraft() {
      return {
        rule_kind: "transferor_group",
        source_name: "",
        target_value: "",
        notes: "",
        confirm_overwrite: false,
      };
    },
    getMappingPreview() {
      return null;
    },
    getMappingMessage() {
      return "";
    },
    buildMappingTargetControl() {
      return "<input id=\"mapping-target-value\">";
    },
    onRuleKindChange() {},
    onDraftInput() {},
    onDraftPreview() {},
    onDraftSave() {},
    onDraftReset() {},
    onUseSuggestion() {},
    onResolveConflict() {},
    onSectionAction() {},
  });

  panel.render();
  assert.match(panelEl.innerHTML, /需要人工裁决的候选冲突/);
});

test("mappings panel exposes only the server-published current-session undo action", async () => {
  const panelEl = createFakeElement();
  const undoButton = createFakeElement();
  const elementMap = new Map([
    ["#panel-mappings", panelEl],
    ["#btn-mapping-undo", undoButton],
  ]);
  const received = [];
  const undo = {
    available: true,
    startup_session_id: "startup-session-a",
    operation_kind: "delete",
  };
  const panel = createMappingsPanel({
    $: (selector) => elementMap.get(selector) || null,
    $$: () => [],
    MAPPING_RULES: {
      transferor_group: {
        sourceLabel: "转让方",
        targetLabel: "集团",
        title: "转让方 -> 集团",
      },
    },
    escapeHtml: (value) => String(value || ""),
    display: (value) => String(value || ""),
    formatJobTime: (value) => String(value || ""),
    num: (value) => Number.parseInt(value, 10) || 0,
    getMappings: () => ({ entries: [], sections: [], summary: {}, undo }),
    getMappingDraft: () => ({ rule_kind: "transferor_group" }),
    getMappingPreview: () => null,
    getMappingMessage: () => "",
    buildMappingTargetControl: () => '<input id="mapping-target-value">',
    async onUndo(value) {
      received.push(value);
    },
  });

  panel.render();
  await undoButton.click();

  assert.match(panelEl.innerHTML, /撤销上次规则变更/);
  assert.doesNotMatch(panelEl.innerHTML, /id="btn-mapping-undo"[^>]*disabled/);
  assert.deepEqual(received, [undo]);
});
