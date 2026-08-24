import test from "node:test";
import assert from "node:assert/strict";

import { MAPPING_SOURCE_TYPES } from "../src/constants/index.js";
import {
  buildMappingDraftFromEntry,
  buildMappingDraftFromSuggestion,
  createInitialMappingDraft,
} from "../src/state/mappingDraftState.js";

test("buildMappingDraftFromSuggestion clears notes and entry identity", () => {
  const draft = buildMappingDraftFromSuggestion({
    entry_id: "entry-1",
    rule_kind: "group_type",
    source_name: "光明食品（集团）有限公司",
    target_value: "市属",
    notes: "推荐备注不应带入",
  });

  assert.deepEqual(draft, {
    ...createInitialMappingDraft(),
    rule_kind: "group_type",
    source_name: "光明食品（集团）有限公司",
    target_value: "市属",
  });
});

test("buildMappingDraftFromEntry preserves stored notes for edit mode", () => {
  const draft = buildMappingDraftFromEntry({
    entry_id: "entry-1",
    match_field: "group",
    target_field: "source_type",
    source_name: "光明食品（集团）有限公司",
    target_value: "市属",
    notes: "已有备注",
  });

  assert.deepEqual(draft, {
    ...createInitialMappingDraft(),
    entry_id: "entry-1",
    rule_kind: "group_type",
    source_name: "光明食品（集团）有限公司",
    target_value: "市属",
    notes: "已有备注",
  });
});

test("mapping source types omit scientific institute pseudo-category", () => {
  assert.deepEqual(MAPPING_SOURCE_TYPES, ["央企", "部委", "市属", "民营"]);
});
