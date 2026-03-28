import { describe, expect, it, vi } from "vitest";
import { readBackendConfig } from "./config";
import {
  DEFAULT_RECORD_SCOPE,
  DESKTOP_COMMAND_NAMES,
  DESKTOP_PANEL_KEYS,
  DESKTOP_SELECTOR_SCHEMA,
} from "./contracts";

describe("desktop contract scaffolding", () => {
  it("loads backend config from the preload bridge", async () => {
    const bridge = {
      getBackendConfig: vi.fn().mockResolvedValue({
        backendUrl: "http://127.0.0.1:42679",
        apiToken: "secret-token",
      }),
    };

    await expect(readBackendConfig(bridge)).resolves.toEqual({
      backendUrl: "http://127.0.0.1:42679",
      apiToken: "secret-token",
    });
    expect(bridge.getBackendConfig).toHaveBeenCalledTimes(1);
  });

  it("freezes panel keys, default record scope, command names, and selector schema for later workers", () => {
    expect(DESKTOP_PANEL_KEYS).toEqual(["overview", "tasks", "records", "mappings", "settings"]);
    expect(DEFAULT_RECORD_SCOPE).toEqual({
      recordFamily: "listing",
      state: "all",
      projectType: "all",
      keyword: "",
      dateFrom: "",
      dateTo: "",
      page: 1,
      pageSize: 50,
    });
    expect(DESKTOP_COMMAND_NAMES).toEqual([
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
    ]);
    expect(DESKTOP_SELECTOR_SCHEMA).toEqual({
      shell: {
        app: "desktop-app-shell",
        content: "desktop-app-content",
      },
      nav: {
        overview: "desktop-nav-overview",
        tasks: "desktop-nav-tasks",
        records: "desktop-nav-records",
        mappings: "desktop-nav-mappings",
        settings: "desktop-nav-settings",
      },
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
    });
  });
});
