import { SHELL_TEST_IDS } from "../../testing/selectors";

export const DESKTOP_PRIMARY_PANEL_KEYS = ["workbench", "records", "mappings"] as const;
export const DESKTOP_SECONDARY_PANEL_KEYS = ["settings"] as const;
export const DESKTOP_PANEL_KEYS = ["workbench", "records", "mappings", "settings"] as const;

export type DesktopPrimaryPanelKey = (typeof DESKTOP_PRIMARY_PANEL_KEYS)[number];
export type DesktopSecondaryPanelKey = (typeof DESKTOP_SECONDARY_PANEL_KEYS)[number];
export type DesktopPanelKey = (typeof DESKTOP_PANEL_KEYS)[number];

type NavigationItem<Key extends DesktopPanelKey> = {
  key: Key;
  label: string;
  testId: string;
};

export const DESKTOP_PRIMARY_NAVIGATION_ITEMS: ReadonlyArray<NavigationItem<DesktopPrimaryPanelKey>> = [
  { key: "workbench", label: "工作台", testId: SHELL_TEST_IDS.navWorkbench },
  { key: "records", label: "记录", testId: SHELL_TEST_IDS.navRecords },
  { key: "mappings", label: "映射", testId: SHELL_TEST_IDS.navMappings },
];

export const DESKTOP_SECONDARY_NAVIGATION_ITEMS: ReadonlyArray<NavigationItem<DesktopSecondaryPanelKey>> = [
  { key: "settings", label: "设置", testId: SHELL_TEST_IDS.navSettings },
];

export const DESKTOP_PANEL_TITLES: Record<DesktopPanelKey, string> = {
  workbench: "工作台骨架已就绪",
  records: "记录页骨架已就绪",
  mappings: "映射页骨架已就绪",
  settings: "设置页骨架已就绪",
};

export function isDesktopPanelKey(value: string): value is DesktopPanelKey {
  return (DESKTOP_PANEL_KEYS as readonly string[]).includes(value);
}
