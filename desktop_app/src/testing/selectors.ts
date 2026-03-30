export const SHELL_TEST_IDS = {
  app: "desktop-app-shell",
  content: "desktop-app-content",
  navOverview: "desktop-nav-overview",
  navTasks: "desktop-nav-tasks",
  navRecords: "desktop-nav-records",
  navMappings: "desktop-nav-mappings",
  navSettings: "desktop-nav-settings",
} as const;

export const PAGE_TEST_IDS = {
  overview: {
    page: "overview-page",
    primaryActions: "overview-primary-actions",
    progressCard: "overview-progress-card",
    runtimeCard: "overview-runtime-card",
  },
  tasks: {
    page: "tasks-page",
    jobList: "tasks-job-list",
    eventList: "tasks-event-list",
  },
  records: {
    page: "records-page",
    filters: "records-filters",
    summary: "records-summary",
    table: "records-table",
  },
  mappings: {
    page: "mappings-page",
    pendingList: "mappings-pending-list",
    editor: "mappings-editor",
    preview: "mappings-preview",
  },
  settings: {
    page: "settings-page",
    form: "settings-form",
    runtimeActions: "settings-runtime-actions",
  },
} as const;

export const OVERVIEW_ACTION_NODE_IDS = {
  runManualImportBtn: "runManualImportBtn",
  runExportBtn: "runExportBtn",
  forceStopBtn: "forceStopBtn",
} as const;

export const MAPPINGS_ACTION_NODE_IDS = {
  importPendingMappingBtn: "importPendingMappingBtn",
  saveDraftMappingsBtn: "saveDraftMappingsBtn",
} as const;

export const RECORDS_FILTER_NODE_IDS = {
  recordsStateFilter: "recordsStateFilter",
  recordsProjectTypeFilter: "recordsProjectTypeFilter",
  recordsDateFromInput: "recordsDateFromInput",
  recordsDateToInput: "recordsDateToInput",
  recordsKeywordInput: "recordsKeywordInput",
} as const;

export const MAPPINGS_DRAFT_SELECTORS = {
  draftItems: ".mapping-draft-item",
  draftRuleKindField: '[data-draft-field="ruleKind"]',
  draftTargetValueField: '[data-draft-field="targetValue"]',
} as const;
