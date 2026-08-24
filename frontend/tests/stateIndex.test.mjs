import test from "node:test";
import assert from "node:assert/strict";

test("state index initializes mapping draft while re-exporting the draft factory", async () => {
  const stateModule = await import("../src/state/index.js");

  assert.equal(typeof stateModule.createInitialMappingDraft, "function");
  assert.deepEqual(
    stateModule.getState().mappingDraft,
    stateModule.createInitialMappingDraft(),
  );
});

test("state index formats action errors from real message or status without leaking undefined", async () => {
  const stateModule = await import("../src/state/index.js");

  assert.equal(
    stateModule.formatActionErrorMessage("导出失败", { message: "目录缺失" }),
    "导出失败: 目录缺失",
  );
  assert.equal(
    stateModule.formatActionErrorMessage("导出失败", { status: 409 }),
    "导出失败: HTTP 409",
  );
  assert.equal(
    stateModule.formatActionErrorMessage("导出失败", { localOnly: true, message: "已有执行中的任务：历史区间任务，请等待完成后再导出。" }),
    "已有执行中的任务：历史区间任务，请等待完成后再导出。",
  );
  assert.equal(
    stateModule.formatActionErrorMessage("导出失败", {}),
    "导出失败: 未知错误",
  );
});
