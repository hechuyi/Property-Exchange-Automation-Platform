import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("settings panel does not render default-scope scope policy metadata from the editor", async () => {
  const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
  const settingsPanelStart = source.indexOf("function renderSettingsForm() {");
  assert.notEqual(settingsPanelStart, -1);
  const settingsPanelSource = source.slice(settingsPanelStart, source.indexOf("$(\"#settings-default-family\")", settingsPanelStart));

  assert.doesNotMatch(settingsPanelSource, /defaultScopeEditor\.scope_policies/);
  assert.doesNotMatch(settingsPanelSource, /policy\.label/);
  assert.doesNotMatch(settingsPanelSource, /policy\.summary/);
  assert.doesNotMatch(settingsPanelSource, /范围策略/);
});
