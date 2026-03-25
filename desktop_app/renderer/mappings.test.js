const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

async function loadMappingsModule() {
  const moduleUrl = pathToFileURL(path.join(__dirname, "mappings.mjs")).href;
  return import(moduleUrl);
}

const MAPPING_RULE_CONFIG = {
  transferor_group: {
    matchField: "transferor",
    targetField: "group_name",
  },
  transferor_type: {
    matchField: "transferor",
    targetField: "source_type",
  },
  group_group: {
    matchField: "group",
    targetField: "group_name",
  },
  group_type: {
    matchField: "group",
    targetField: "source_type",
  },
};

test("buildMappingPayload trims draft values and resolves rule configuration", async () => {
  const { buildMappingPayload } = await loadMappingsModule();
  const payload = buildMappingPayload(
    {
      ruleKind: "group_type",
      sourceName: " 华润 ",
      targetValue: " 央企 ",
      notes: " 手工确认 ",
    },
    MAPPING_RULE_CONFIG,
  );

  assert.deepEqual(payload, {
    source_name: "华润",
    target_value: "央企",
    match_field: "group",
    target_field: "source_type",
    notes: "手工确认",
  });
});

test("runMappingUpsertFlow previews before saving when there is no conflict", async () => {
  const { runMappingUpsertFlow } = await loadMappingsModule();
  const calls = [];
  const result = await runMappingUpsertFlow({
    draft: {
      ruleKind: "transferor_group",
      sourceName: "华润置地",
      targetValue: "华润集团",
      notes: "批量导入",
    },
    mappingRuleConfig: MAPPING_RULE_CONFIG,
    previewMapping: async (payload) => {
      calls.push(["preview", { ...payload }]);
      return { conflict: false, mode: "create", affected_count: 4 };
    },
    saveMapping: async (payload) => {
      calls.push(["save", { ...payload }]);
      return { job_id: "job-1", affected_count: 4 };
    },
    confirmOverwrite: async () => {
      calls.push(["confirm"]);
      return true;
    },
  });

  assert.equal(result.cancelled, false);
  assert.deepEqual(calls, [
    [
      "preview",
      {
        source_name: "华润置地",
        target_value: "华润集团",
        match_field: "transferor",
        target_field: "group_name",
        notes: "批量导入",
      },
    ],
    [
      "save",
      {
        source_name: "华润置地",
        target_value: "华润集团",
        match_field: "transferor",
        target_field: "group_name",
        notes: "批量导入",
      },
    ],
  ]);
});

test("runMappingUpsertFlow aborts save when overwrite preview is rejected", async () => {
  const { runMappingUpsertFlow } = await loadMappingsModule();
  const calls = [];
  const result = await runMappingUpsertFlow({
    draft: {
      ruleKind: "group_type",
      sourceName: "华润",
      targetValue: "央企",
      notes: "覆盖旧规则",
    },
    mappingRuleConfig: MAPPING_RULE_CONFIG,
    previewMapping: async (payload) => {
      calls.push(["preview", { ...payload }]);
      return {
        conflict: true,
        mode: "overwrite",
        target_field: "source_type",
        source_name: "华润",
        target_value: "央企",
        existing_entry: { source_type: "地方国企" },
        affected_count: 12,
        affected_pending_count: 7,
      };
    },
    saveMapping: async (payload) => {
      calls.push(["save", { ...payload }]);
      return { ok: true };
    },
    confirmOverwrite: async (preview) => {
      calls.push(["confirm", preview.mode]);
      return false;
    },
  });

  assert.equal(result.cancelled, true);
  assert.deepEqual(calls, [
    [
      "preview",
      {
        source_name: "华润",
        target_value: "央企",
        match_field: "group",
        target_field: "source_type",
        notes: "覆盖旧规则",
      },
    ],
    ["confirm", "overwrite"],
  ]);
});

test("runMappingUpsertFlow adds confirm_overwrite when overwrite is accepted", async () => {
  const { runMappingUpsertFlow } = await loadMappingsModule();
  const calls = [];
  const result = await runMappingUpsertFlow({
    draft: {
      ruleKind: "group_type",
      sourceName: "华润",
      targetValue: "央企",
      notes: "",
    },
    mappingRuleConfig: MAPPING_RULE_CONFIG,
    previewMapping: async (payload) => {
      calls.push(["preview", { ...payload }]);
      return {
        conflict: true,
        mode: "overwrite",
        target_field: "source_type",
        source_name: "华润",
        target_value: "央企",
        existing_entry: { source_type: "地方国企" },
        affected_count: 12,
        affected_pending_count: 7,
      };
    },
    saveMapping: async (payload) => {
      calls.push(["save", { ...payload }]);
      return { job_id: "job-2", affected_count: 12 };
    },
    confirmOverwrite: async (preview) => {
      calls.push(["confirm", preview.existing_entry.source_type]);
      return true;
    },
  });

  assert.equal(result.cancelled, false);
  assert.deepEqual(calls, [
    [
      "preview",
      {
        source_name: "华润",
        target_value: "央企",
        match_field: "group",
        target_field: "source_type",
        notes: "",
      },
    ],
    ["confirm", "地方国企"],
    [
      "save",
      {
        source_name: "华润",
        target_value: "央企",
        match_field: "group",
        target_field: "source_type",
        notes: "",
        confirm_overwrite: true,
      },
    ],
  ]);
});

test("runBatchMappingUpsertFlow summarizes saves conflicts and surfaced failures", async () => {
  const { runBatchMappingUpsertFlow } = await loadMappingsModule();
  const calls = [];
  const result = await runBatchMappingUpsertFlow({
    drafts: [
      {
        recordId: "rec-1",
        ruleKind: "group_type",
        sourceName: "华润",
        targetValue: "央企",
        notes: "覆盖旧规则",
      },
      {
        recordId: "rec-2",
        ruleKind: "transferor_group",
        sourceName: "华润置地",
        targetValue: "华润集团",
        notes: "",
      },
      {
        recordId: "rec-3",
        ruleKind: "transferor_type",
        sourceName: "失败公司",
        targetValue: "地方国企",
        notes: "",
      },
    ],
    mappingRuleConfig: MAPPING_RULE_CONFIG,
    previewMapping: async (payload) => {
      calls.push(["preview", { ...payload }]);
      if (payload.source_name === "华润") {
        return {
          conflict: true,
          mode: "overwrite",
          target_field: "source_type",
          source_name: "华润",
          target_value: "央企",
          existing_entry: { source_type: "地方国企" },
          affected_count: 8,
          affected_pending_count: 3,
        };
      }
      return {
        conflict: false,
        mode: "create",
        target_field: payload.target_field,
        source_name: payload.source_name,
        target_value: payload.target_value,
        affected_count: 2,
        affected_pending_count: 1,
      };
    },
    saveMapping: async (payload) => {
      calls.push(["save", { ...payload }]);
      if (payload.source_name === "失败公司") {
        throw new Error("preview endpoint failed");
      }
      return {
        job_id: `job-${payload.source_name}`,
        affected_count: payload.source_name === "华润" ? 8 : 2,
      };
    },
    confirmOverwrite: async (preview) => {
      calls.push(["confirm", preview.source_name]);
      return true;
    },
  });

  assert.equal(result.savedCount, 2);
  assert.equal(result.skippedOverwriteCount, 0);
  assert.equal(result.failedCount, 1);
  assert.equal(result.refreshJobs.length, 2);
  assert.deepEqual(Array.from(result.savedRecordIds), ["rec-1", "rec-2"]);
  assert.match(result.failureMessages[0], /失败公司/);
  assert.match(result.failureMessages[0], /preview endpoint failed/);
  assert.deepEqual(calls, [
    [
      "preview",
      {
        source_name: "华润",
        target_value: "央企",
        match_field: "group",
        target_field: "source_type",
        notes: "覆盖旧规则",
      },
    ],
    ["confirm", "华润"],
    [
      "save",
      {
        source_name: "华润",
        target_value: "央企",
        match_field: "group",
        target_field: "source_type",
        notes: "覆盖旧规则",
        confirm_overwrite: true,
      },
    ],
    [
      "preview",
      {
        source_name: "华润置地",
        target_value: "华润集团",
        match_field: "transferor",
        target_field: "group_name",
        notes: "",
      },
    ],
    [
      "save",
      {
        source_name: "华润置地",
        target_value: "华润集团",
        match_field: "transferor",
        target_field: "group_name",
        notes: "",
      },
    ],
    [
      "preview",
      {
        source_name: "失败公司",
        target_value: "地方国企",
        match_field: "transferor",
        target_field: "source_type",
        notes: "",
      },
    ],
    [
      "save",
      {
        source_name: "失败公司",
        target_value: "地方国企",
        match_field: "transferor",
        target_field: "source_type",
        notes: "",
      },
    ],
  ]);
});

test("runBatchMappingUpsertFlow marks duplicate drafts with the same rule as resolved together", async () => {
  const { runBatchMappingUpsertFlow } = await loadMappingsModule();
  const result = await runBatchMappingUpsertFlow({
    drafts: [
      {
        recordId: "rec-1",
        ruleKind: "group_type",
        sourceName: "华润",
        targetValue: "央企",
        notes: "",
      },
      {
        recordId: "rec-2",
        ruleKind: "group_type",
        sourceName: "华润",
        targetValue: "央企",
        notes: "同一规则对应另一条记录",
      },
    ],
    mappingRuleConfig: MAPPING_RULE_CONFIG,
    previewMapping: async () => ({
      conflict: false,
      mode: "create",
      affected_count: 6,
      affected_pending_count: 2,
    }),
    saveMapping: async () => ({
      job_id: "job-huarun",
      affected_count: 6,
    }),
  });

  assert.equal(result.savedCount, 1);
  assert.deepEqual(Array.from(result.savedRecordIds).sort(), ["rec-1", "rec-2"]);
  assert.equal(result.refreshJobs.length, 1);
});

test("isMappingInteractionActive stays true while conflict dialog is open", async () => {
  const { isMappingInteractionActive } = await loadMappingsModule();

  assert.equal(
    isMappingInteractionActive({
      currentPanel: "mappings",
      activeElement: null,
      conflictDialogOpen: true,
    }),
    true,
  );
});

test("isMappingInteractionActive only treats mapping form and drafts as active edit zones", async () => {
  const { isMappingInteractionActive } = await loadMappingsModule();

  assert.equal(
    isMappingInteractionActive({
      currentPanel: "mappings",
      activeElement: {
        closest(selector) {
          return selector === "#mappingForm, #mappingDrafts" ? {} : null;
        },
      },
      conflictDialogOpen: false,
    }),
    true,
  );
  assert.equal(
    isMappingInteractionActive({
      currentPanel: "mappings",
      activeElement: {
        closest() {
          return null;
        },
      },
      conflictDialogOpen: false,
    }),
    false,
  );
  assert.equal(
    isMappingInteractionActive({
      currentPanel: "overview",
      activeElement: {
        closest() {
          return {};
        },
      },
      conflictDialogOpen: true,
    }),
    false,
  );
});
