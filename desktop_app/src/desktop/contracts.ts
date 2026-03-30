import {
  MAPPINGS_ACTION_NODE_IDS,
  MAPPINGS_DRAFT_SELECTORS,
  OVERVIEW_ACTION_NODE_IDS,
  PAGE_TEST_IDS,
  RECORDS_FILTER_NODE_IDS,
  SHELL_TEST_IDS,
} from "../testing/selectors";
export {
  DESKTOP_PANEL_KEYS,
  DESKTOP_PRIMARY_PANEL_KEYS,
  DESKTOP_SECONDARY_PANEL_KEYS,
  type DesktopPanelKey,
} from "../features/shell/navigation";

export const DEFAULT_RECORD_SCOPE = {
  recordFamily: "listing",
  state: "all",
  projectType: "all",
  keyword: "",
  dateFrom: "",
  dateTo: "",
  page: 1,
  pageSize: 50,
} as const;

export const DESKTOP_COMMAND_NAMES = [
  "getOverview",
  "listJobs",
  "getJob",
  "listJobEvents",
  "listRecords",
  "listMappings",
  "runOneClick",
  "runManualImport",
  "runExport",
  "saveMapping",
  "previewMapping",
  "reprocessPendingMappings",
  "reprocessRecord",
] as const;

export const DESKTOP_SELECTOR_SCHEMA = {
  shell: {
    app: SHELL_TEST_IDS.app,
    content: SHELL_TEST_IDS.content,
  },
  nav: {
    primary: {
      workbench: SHELL_TEST_IDS.navWorkbench,
      records: SHELL_TEST_IDS.navRecords,
      mappings: SHELL_TEST_IDS.navMappings,
    },
    secondary: {
      settings: SHELL_TEST_IDS.navSettings,
    },
  },
  workbench: PAGE_TEST_IDS.overview,
  records: PAGE_TEST_IDS.records,
  mappings: PAGE_TEST_IDS.mappings,
  settings: PAGE_TEST_IDS.settings,
} as const;

const byTestId = (testId: string) => `[data-testid="${testId}"]`;
const byId = (id: string) => `#${id}`;

export const DESKTOP_SMOKE_SELECTOR_CONTRACT = {
  nav: {
    workbench: [byTestId(SHELL_TEST_IDS.navWorkbench)],
    records: [byTestId(SHELL_TEST_IDS.navRecords)],
    mappings: [byTestId(SHELL_TEST_IDS.navMappings)],
  },
  pages: {
    workbench: [byTestId(PAGE_TEST_IDS.overview.page)],
    records: [byTestId(PAGE_TEST_IDS.records.page)],
    mappings: [byTestId(PAGE_TEST_IDS.mappings.page)],
  },
  actions: {
    triggerManualImport: [byId(OVERVIEW_ACTION_NODE_IDS.runManualImportBtn)],
    triggerExport: [byId(OVERVIEW_ACTION_NODE_IDS.runExportBtn)],
    importPendingMappings: [byId(MAPPINGS_ACTION_NODE_IDS.importPendingMappingBtn)],
    saveDraftMappings: [byId(MAPPINGS_ACTION_NODE_IDS.saveDraftMappingsBtn)],
    forceStopCurrentJob: [byId(OVERVIEW_ACTION_NODE_IDS.forceStopBtn)],
  },
  mappings: {
    draftItems: [MAPPINGS_DRAFT_SELECTORS.draftItems],
    draftRuleKindField: [MAPPINGS_DRAFT_SELECTORS.draftRuleKindField],
    draftTargetValueField: [MAPPINGS_DRAFT_SELECTORS.draftTargetValueField],
  },
  records: {
    stateFilter: [byId(RECORDS_FILTER_NODE_IDS.recordsStateFilter)],
    projectTypeFilter: [byId(RECORDS_FILTER_NODE_IDS.recordsProjectTypeFilter)],
    dateFromInput: [byId(RECORDS_FILTER_NODE_IDS.recordsDateFromInput)],
    dateToInput: [byId(RECORDS_FILTER_NODE_IDS.recordsDateToInput)],
    keywordInput: [byId(RECORDS_FILTER_NODE_IDS.recordsKeywordInput)],
  },
} as const;
export type DesktopCommandName = (typeof DESKTOP_COMMAND_NAMES)[number];

export type BackendConfig = {
  backendUrl: string;
  apiToken: string;
};

export type RecordScope = {
  recordFamily?: string;
  state?: string;
  projectType?: string;
  keyword?: string;
  dateFrom?: string;
  dateTo?: string;
  page?: number;
  pageSize?: number;
};

export function normalizeBackendConfig(config: Partial<BackendConfig> | null | undefined): BackendConfig {
  const backendUrl = String(config?.backendUrl || "").trim();
  const apiToken = String(config?.apiToken || "").trim();
  if (!backendUrl) {
    throw new Error("Desktop backend URL is missing");
  }
  return {
    backendUrl,
    apiToken,
  };
}
