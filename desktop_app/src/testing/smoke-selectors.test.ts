import { describe, expect, test } from "vitest";
import { DESKTOP_SELECTOR_SCHEMA, DESKTOP_SMOKE_SELECTOR_CONTRACT } from "../desktop/contracts";
import { SMOKE_SELECTOR_BRIDGE } from "./smoke-bridge";

describe("SMOKE_SELECTOR_BRIDGE", () => {
  test("maps nav selectors from desktop selector contract", () => {
    expect(SMOKE_SELECTOR_BRIDGE.nav.workbench[0]).toBe(
      `[data-testid="${DESKTOP_SELECTOR_SCHEMA.nav.primary.workbench}"]`,
    );
    expect(SMOKE_SELECTOR_BRIDGE.nav.mappings[0]).toBe(
      `[data-testid="${DESKTOP_SELECTOR_SCHEMA.nav.primary.mappings}"]`,
    );
    expect(SMOKE_SELECTOR_BRIDGE.nav.records[0]).toBe(
      `[data-testid="${DESKTOP_SELECTOR_SCHEMA.nav.primary.records}"]`,
    );
  });

  test("maps page roots from desktop selector contract", () => {
    expect(SMOKE_SELECTOR_BRIDGE.pages.workbench[0]).toBe(
      `[data-testid="${DESKTOP_SELECTOR_SCHEMA.workbench.page}"]`,
    );
    expect(SMOKE_SELECTOR_BRIDGE.pages.mappings[0]).toBe(
      `[data-testid="${DESKTOP_SELECTOR_SCHEMA.mappings.page}"]`,
    );
    expect(SMOKE_SELECTOR_BRIDGE.pages.records[0]).toBe(
      `[data-testid="${DESKTOP_SELECTOR_SCHEMA.records.page}"]`,
    );
  });

  test("maps action and filter selectors from selector contract", () => {
    expect(SMOKE_SELECTOR_BRIDGE.actions.triggerManualImport).toEqual(
      DESKTOP_SMOKE_SELECTOR_CONTRACT.actions.triggerManualImport,
    );
    expect(SMOKE_SELECTOR_BRIDGE.actions.triggerExport).toEqual(DESKTOP_SMOKE_SELECTOR_CONTRACT.actions.triggerExport);
    expect(SMOKE_SELECTOR_BRIDGE.actions.importPendingMappings).toEqual(
      DESKTOP_SMOKE_SELECTOR_CONTRACT.actions.importPendingMappings,
    );
    expect(SMOKE_SELECTOR_BRIDGE.actions.saveDraftMappings).toEqual(
      DESKTOP_SMOKE_SELECTOR_CONTRACT.actions.saveDraftMappings,
    );
    expect(SMOKE_SELECTOR_BRIDGE.actions.forceStopCurrentJob).toEqual(
      DESKTOP_SMOKE_SELECTOR_CONTRACT.actions.forceStopCurrentJob,
    );
    expect(SMOKE_SELECTOR_BRIDGE.records.stateFilter).toEqual(DESKTOP_SMOKE_SELECTOR_CONTRACT.records.stateFilter);
    expect(SMOKE_SELECTOR_BRIDGE.records.projectTypeFilter).toEqual(
      DESKTOP_SMOKE_SELECTOR_CONTRACT.records.projectTypeFilter,
    );
    expect(SMOKE_SELECTOR_BRIDGE.records.dateFromInput).toEqual(DESKTOP_SMOKE_SELECTOR_CONTRACT.records.dateFromInput);
    expect(SMOKE_SELECTOR_BRIDGE.records.dateToInput).toEqual(DESKTOP_SMOKE_SELECTOR_CONTRACT.records.dateToInput);
    expect(SMOKE_SELECTOR_BRIDGE.records.keywordInput).toEqual(DESKTOP_SMOKE_SELECTOR_CONTRACT.records.keywordInput);
  });

  test("maps mapping draft selectors from selector contract", () => {
    expect(SMOKE_SELECTOR_BRIDGE.mappings.draftItems).toEqual(DESKTOP_SMOKE_SELECTOR_CONTRACT.mappings.draftItems);
    expect(SMOKE_SELECTOR_BRIDGE.mappings.draftRuleKindField).toEqual(
      DESKTOP_SMOKE_SELECTOR_CONTRACT.mappings.draftRuleKindField,
    );
    expect(SMOKE_SELECTOR_BRIDGE.mappings.draftTargetValueField).toEqual(
      DESKTOP_SMOKE_SELECTOR_CONTRACT.mappings.draftTargetValueField,
    );
  });

  test("keeps smoke selectors limited to the three workflow destinations", () => {
    expect(Object.keys(SMOKE_SELECTOR_BRIDGE.nav)).toEqual(["workbench", "records", "mappings"]);
    expect(Object.keys(SMOKE_SELECTOR_BRIDGE.pages)).toEqual(["workbench", "records", "mappings"]);
    expect(SMOKE_SELECTOR_BRIDGE).toEqual(DESKTOP_SMOKE_SELECTOR_CONTRACT);
  });
});
