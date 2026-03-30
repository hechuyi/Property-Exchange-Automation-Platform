import { describe, expect, test } from "vitest";
import { DESKTOP_SELECTOR_SCHEMA, DESKTOP_SMOKE_SELECTOR_CONTRACT } from "../desktop/contracts";
import { SMOKE_SELECTOR_BRIDGE } from "./smoke-bridge";

describe("SMOKE_SELECTOR_BRIDGE", () => {
  test("maps nav selectors from desktop selector contract", () => {
    expect(SMOKE_SELECTOR_BRIDGE.nav.overview[0]).toBe(
      `[data-testid="${DESKTOP_SELECTOR_SCHEMA.nav.overview}"]`,
    );
    expect(SMOKE_SELECTOR_BRIDGE.nav.mappings[0]).toBe(
      `[data-testid="${DESKTOP_SELECTOR_SCHEMA.nav.mappings}"]`,
    );
    expect(SMOKE_SELECTOR_BRIDGE.nav.records[0]).toBe(
      `[data-testid="${DESKTOP_SELECTOR_SCHEMA.nav.records}"]`,
    );
  });

  test("maps page roots from desktop selector contract", () => {
    expect(SMOKE_SELECTOR_BRIDGE.pages.overview[0]).toBe(
      `[data-testid="${DESKTOP_SELECTOR_SCHEMA.overview.page}"]`,
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

  test("keeps runtime js embedded contract equivalent to ts bridge contract", async () => {
    const runtimeModule = await import("../../smoke_driver.js");
    const runtimeExports = runtimeModule.default ?? runtimeModule;
    expect(runtimeExports.__internal.EMBEDDED_SMOKE_SELECTOR_BRIDGE).toEqual(DESKTOP_SMOKE_SELECTOR_CONTRACT);
    expect(runtimeExports.__internal.EMBEDDED_SMOKE_SELECTOR_BRIDGE).toEqual(SMOKE_SELECTOR_BRIDGE);
  });
});
