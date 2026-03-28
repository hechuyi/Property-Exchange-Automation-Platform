import {
  MAPPINGS_ACTION_NODE_IDS,
  MAPPINGS_DRAFT_SELECTORS,
  OVERVIEW_ACTION_NODE_IDS,
  PAGE_TEST_IDS,
  RECORDS_FILTER_NODE_IDS,
  SHELL_TEST_IDS,
} from "../testing/selectors";

export const DESKTOP_PANEL_KEYS = ["overview", "tasks", "records", "mappings", "settings"] as const;

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
    overview: SHELL_TEST_IDS.navOverview,
    tasks: SHELL_TEST_IDS.navTasks,
    records: SHELL_TEST_IDS.navRecords,
    mappings: SHELL_TEST_IDS.navMappings,
    settings: SHELL_TEST_IDS.navSettings,
  },
  overview: PAGE_TEST_IDS.overview,
  tasks: PAGE_TEST_IDS.tasks,
  records: PAGE_TEST_IDS.records,
  mappings: PAGE_TEST_IDS.mappings,
  settings: PAGE_TEST_IDS.settings,
} as const;

const byTestId = (testId: string) => `[data-testid="${testId}"]`;
const byId = (id: string) => `#${id}`;

export const DESKTOP_SMOKE_SELECTOR_CONTRACT = {
  nav: {
    overview: [byTestId(SHELL_TEST_IDS.navOverview)],
    records: [byTestId(SHELL_TEST_IDS.navRecords)],
    mappings: [byTestId(SHELL_TEST_IDS.navMappings)],
  },
  pages: {
    overview: [byTestId(PAGE_TEST_IDS.overview.page)],
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

export type DesktopPanelKey = (typeof DESKTOP_PANEL_KEYS)[number];
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
