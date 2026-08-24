import test from "node:test";
import assert from "node:assert/strict";

import { normalizeMappingsResource } from "../src/contracts/mappings.js";

test("normalizeMappingsResource rejects explicitly malformed backlog collection fields", () => {
  for (const [resource, message] of [
    [{ sections: {} }, "sections"],
    [{ sections: [{ section_id: "mapping_gap_resolution", items: {} }] }, "items"],
    [
      {
        sections: [
          {
            section_id: "mapping_gap_resolution",
            items: [{ record_id: "rec-1", candidate_resolutions: {} }],
          },
        ],
      },
      "candidate_resolutions",
    ],
  ]) {
    assert.throws(
      () => normalizeMappingsResource(resource),
      /Invalid mappings resource/,
      `${message} should not be silently normalized`,
    );
  }
});

test("normalizeMappingsResource preserves split backlog sections and gap-conflict vocabulary", () => {
  const normalized = normalizeMappingsResource({
    entries: [
      {
        entry_id: "entry-1",
        rule_kind: "transferor_group",
        rule_title: "转让方 -> 集团",
        source_name: "中铁",
        target_value: "中铁集团",
        match_field: "transferor",
        target_field: "group_name",
        notes: "来源于人工校验",
        updated_at: "2026-04-12T10:00:00",
      },
    ],
    sections: [
      {
        section_id: "mapping_gap_resolution",
        title: "待映射补全",
        count: "1",
        cta_kind: "reprocess_pending",
        items: [
          {
            record_id: "rec-gap-1",
            revision_id: "11",
            project_code: "XM-001",
            source_name: "华润置地",
            business_id: "asset_transfer",
            raw_business_label: "实物资产转让",
            blocker_kind: "mapping_gap",
            blocker_subtype: "rule_gap",
            queue_section: "mapping_gap_resolution",
            record_family: "listing",
            exchange_code: "cbex",
            exchange_label: "北京产权交易所",
            state: "pending_mapping",
            status_label: "待补映射",
            status_detail: "缺少类型映射，需补规则后回刷",
            actionable: true,
            audit_only: false,
            evidence_codes: ["missing_type"],
            gap_codes: ["missing_type"],
            recommended_rule: {
              rule_kind: "transferor_group",
              title: "转让方 -> 集团",
              source_name: "华润置地",
              target_value: "华润集团",
            },
            candidate_resolutions: [
              {
                field: "group_name",
                rule_kind: "transferor_group",
                match_field: "source_name",
                target_field: "group_name",
                source_name: "华润置地",
                target_value: "华润集团",
                label: "华润集团",
                title: "候选集团",
                evidence_chain: ["catalog_match"],
              },
            ],
          },
        ],
      },
      {
        section_id: "mapping_conflict_resolution",
        title: "待映射冲突",
        count: "1",
        cta_kind: "read_only",
        items: [
          {
            record_id: "rec-mapping-1",
            revision_id: "12",
            business_id: "equity_transfer",
            raw_business_label: "股权转让",
            blocker_kind: "mapping_conflict",
            blocker_subtype: "mapping_conflict",
            queue_section: "mapping_conflict_resolution",
            record_family: "listing",
            exchange_code: "cbex",
            exchange_label: "北京产权交易所",
            state: "pending_mapping",
            status_label: "待补映射",
            status_detail: "集团已识别，但缺少类型映射",
            actionable: true,
            audit_only: false,
            evidence_codes: ["missing_type"],
          },
        ],
      },
      {
        section_id: "audit",
        title: "审计只读",
        count: "1",
        cta_kind: "read_only",
        items: [
          {
            record_id: "rec-audit-1",
            revision_id: "13",
            business_id: "",
            raw_business_label: "隐藏审计项",
            blocker_kind: "audit",
            blocker_subtype: "hidden_family_blocker",
            queue_section: "audit",
            record_family: "agreement",
            exchange_code: "cbex",
            exchange_label: "北京产权交易所",
            state: "pending_mapping",
            status_label: "审计只读",
            status_detail: "隐藏 family blocker 不进入可执行队列",
            actionable: false,
            audit_only: true,
            evidence_codes: ["hidden_family"],
          },
        ],
      },
    ],
    summary: {
      actionable_count: "2",
      mapping_gap_count: "1",
      mapping_conflict_count: "1",
      audit_count: "1",
    },
    undo: {
      available: true,
      startup_session_id: "startup-session-a",
      operation_kind: "update",
    },
    returned_count: "3",
    total_count: "3",
    truncated: false,
  });

  assert.deepEqual(normalized.entries, [
    {
      entry_id: "entry-1",
      rule_kind: "transferor_group",
      rule_title: "转让方 -> 集团",
      source_name: "中铁",
      target_value: "中铁集团",
      match_field: "transferor",
      target_field: "group_name",
      notes: "来源于人工校验",
      updated_at: "2026-04-12T10:00:00",
    },
  ]);
  assert.equal(normalized.sections.length, 3);
  assert.deepEqual(normalized.summary, {
    actionable_count: 2,
    mapping_gap_count: 1,
    mapping_conflict_count: 1,
    audit_count: 1,
  });
  assert.deepEqual(normalized.undo, {
    available: true,
    startup_session_id: "startup-session-a",
    operation_kind: "update",
  });
  assert.equal(normalized.returned_count, 3);
  assert.equal(normalized.total_count, 3);
  assert.equal(normalized.truncated, false);

  assert.deepEqual(normalized.sections.map((section) => section.section_id), [
    "mapping_gap_resolution",
    "mapping_conflict_resolution",
    "audit",
  ]);
  assert.equal(normalized.sections[0].cta_kind, "reprocess_pending");
  assert.equal(normalized.sections[1].cta_kind, "read_only");
  assert.equal(normalized.sections[2].cta_kind, "read_only");
  assert.deepEqual(normalized.sections[0].items[0].gap_codes, ["missing_type"]);
  assert.equal(normalized.sections[0].items[0].project_code, "XM-001");
  assert.equal(normalized.sections[0].items[0].source_name, "华润置地");
  assert.equal(normalized.sections[0].items[0].recommended_rule.rule_kind, "transferor_group");
  assert.equal(normalized.sections[0].items[0].candidate_resolutions[0].title, "候选集团");
  assert.equal(normalized.sections[1].items[0].business_id, "equity_transfer");
  assert.equal(normalized.sections[2].items[0].audit_only, true);
});

test("normalizeMappingsResource preserves rich section item fields used by the live mappings panel", () => {
  const normalized = normalizeMappingsResource({
    sections: [
      {
        section_id: "mapping_gap_resolution",
        title: "待映射补全",
        count: 1,
        cta_kind: "reprocess_pending",
        items: [
          {
            record_id: "rec-rich-1",
            revision_id: 7,
            project_code: "XM-777",
            source_name: "招商蛇口",
            gap_codes: ["missing_group", "missing_type"],
            recommended_rule: {
              rule_kind: "transferor_group",
              title: "转让方 -> 集团",
              source_name: "招商蛇口",
              target_value: "招商局集团",
            },
            candidate_resolutions: [
              {
                field: "group_name",
                rule_kind: "transferor_group",
                match_field: "source_name",
                target_field: "group_name",
                source_name: "招商蛇口",
                target_value: "招商局集团",
                title: "招商局集团",
                evidence_chain: ["catalog_match"],
              },
            ],
          },
        ],
      },
    ],
  });

  assert.deepEqual(normalized.sections[0].items[0], {
    record_id: "rec-rich-1",
    revision_id: 7,
    project_code: "XM-777",
    project_name: "",
    project_type_code: "",
    project_type_label: "",
    created_at: "",
    source_name: "招商蛇口",
    current_group: "",
    current_type: "",
    resolved_group: "",
    resolved_type: "",
    gap_codes: ["missing_group", "missing_type"],
    blocking_reason_code: "",
    recommended_rule: {
      rule_kind: "transferor_group",
      title: "转让方 -> 集团",
      source_name: "招商蛇口",
      target_value: "招商局集团",
    },
    available_rule_kinds: [],
    candidate_resolutions: [
      {
        field: "group_name",
        rule_kind: "transferor_group",
        match_field: "source_name",
        target_field: "group_name",
        source_name: "招商蛇口",
        target_value: "招商局集团",
        label: "",
        title: "招商局集团",
        evidence_chain: ["catalog_match"],
      },
    ],
    has_conflict: false,
    business_id: "",
    business_label: "",
    raw_business_label: "",
    blocker_kind: "",
    blocker_subtype: "",
    queue_section: "mapping_gap_resolution",
    record_family: "",
    exchange_code: "",
    exchange_label: "",
    state: "",
    status_label: "",
    status_detail: "",
    actionable: false,
    audit_only: false,
    evidence_codes: [],
  });
});

test("normalizeMappingsResource ignores legacy top-level pending backlog residue", () => {
  const normalized = normalizeMappingsResource({
    pending: [
      {
        record_id: "rec-legacy",
        state: "pending_mapping",
      },
    ],
  });

  assert.deepEqual(normalized, {
    entries: [],
    sections: [],
    summary: {
      actionable_count: 0,
      mapping_gap_count: 0,
      mapping_conflict_count: 0,
      audit_count: 0,
    },
    undo: {
      available: false,
      startup_session_id: "",
      operation_kind: "",
    },
    returned_count: 0,
    total_count: 0,
    truncated: false,
  });
});

test("normalizeMappingsResource sanitizes raw business labels from section DTO fields", () => {
  const normalized = normalizeMappingsResource({
    sections: [
      {
        section_id: "mapping_gap_resolution",
        title: "待映射补全",
        count: 1,
        items: [
          {
            record_id: "rec-sentinel",
            revision_id: 1,
            raw_business_label: "UNTRUSTED_EXTERNAL_TEXT",
            business_label: "UNTRUSTED_EXTERNAL_TEXT",
            source_name: "UNTRUSTED_EXTERNAL_TEXT",
            state: "pending_mapping",
            status_label: "待补映射",
            candidate_resolutions: [
              {
                title: "UNTRUSTED_EXTERNAL_TEXT",
                source_name: "UNTRUSTED_EXTERNAL_TEXT",
                target_value: "安全集团",
                evidence_chain: [
                  "catalog_match",
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
  });

  const encoded = JSON.stringify(normalized);
  assert.doesNotMatch(encoded, /UNTRUSTED_EXTERNAL_TEXT/);
  const item = normalized.sections[0].items[0];
  assert.equal(item.business_label, "未识别项目类型");
  assert.equal(item.raw_business_label, "");
  assert.equal(item.status_label, "待补映射");
});

test("normalizeMappingsResource converts real backend evidence_chain objects into deterministic readable text", () => {
  const normalized = normalizeMappingsResource({
    sections: [
      {
        section_id: "mapping_conflict_resolution",
        title: "待映射冲突",
        count: 1,
        items: [
          {
            record_id: "rec-conflict-1",
            candidate_resolutions: [
              {
                title: "华润集团",
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
  });

  assert.deepEqual(
    normalized.sections[0].items[0].candidate_resolutions[0].evidence_chain,
    [
      "catalog_match",
      "label: 目录候选; match_field: transferor; target_field: group_name; source_name: 华润置地; target_value: 华润集团",
    ],
  );
});
