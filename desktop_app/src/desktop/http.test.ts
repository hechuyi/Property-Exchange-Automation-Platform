import { describe, expect, it, vi } from "vitest";
import { createDesktopCommands } from "./commands";
import { buildDesktopHeaders, createDesktopHttpClient, DESKTOP_API_TOKEN_HEADER } from "./http";
import { buildJobEventsPath, buildJobsPath, buildRecordsPath } from "./queries";

describe("desktop http adapter", () => {
  it("injects desktop token headers for every request", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true }),
    });
    const client = createDesktopHttpClient({
      baseUrl: "http://127.0.0.1:42679",
      apiToken: "secret-token",
      fetchFn,
    });

    await expect(client("/api/overview")).resolves.toEqual({ ok: true });
    expect(fetchFn).toHaveBeenCalledWith(
      "http://127.0.0.1:42679/api/overview",
      expect.objectContaining({
        headers: {
          "Content-Type": "application/json",
          [DESKTOP_API_TOKEN_HEADER]: "secret-token",
        },
      }),
    );
    expect(buildDesktopHeaders("secret-token")).toEqual({
      "Content-Type": "application/json",
      [DESKTOP_API_TOKEN_HEADER]: "secret-token",
    });
  });

  it("normalizes backend failures into a stable product-facing error object", async () => {
    const client = createDesktopHttpClient({
      baseUrl: "http://127.0.0.1:42679",
      apiToken: "secret-token",
      fetchFn: vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({ error: "Traceback: internal failure" }),
      }),
    });

    await expect(client("/api/overview")).rejects.toMatchObject({
      code: "HTTP_500",
      detail: "Traceback: internal failure",
      userMessage: "系统请求失败，请稍后重试。",
    });
  });

  it("freezes query builders and command wrappers for records jobs mappings and exports", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true }),
    });
    const client = createDesktopHttpClient({
      baseUrl: "http://127.0.0.1:42679",
      apiToken: "secret-token",
      fetchFn,
    });
    const commands = createDesktopCommands({ client });

    expect(buildRecordsPath({ keyword: "国资" })).toBe(
      "/api/records?record_family=listing&state=all&project_type=all&page=1&page_size=50&keyword=%E5%9B%BD%E8%B5%84",
    );
    expect(buildJobsPath({ limit: 25 })).toBe("/api/jobs?limit=25");
    expect(buildJobEventsPath("job-1", { limit: 100 })).toBe("/api/jobs/job-1/events?limit=100");
    expect(Object.keys(commands)).toEqual([
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

    await commands.listRecords({ keyword: "国资" });
    expect(fetchFn).toHaveBeenLastCalledWith(
      "http://127.0.0.1:42679/api/records?record_family=listing&state=all&project_type=all&page=1&page_size=50&keyword=%E5%9B%BD%E8%B5%84",
      expect.objectContaining({ method: "GET" }),
    );

    await commands.runExport({
      scope: {
        dateFrom: "2026-03-01",
        dateTo: "2026-03-02",
      },
      outputDir: "/tmp/out",
    });
    expect(fetchFn).toHaveBeenLastCalledWith(
      "http://127.0.0.1:42679/api/exports",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          scope: {
            record_family: "listing",
            state: "all",
            project_type: "all",
            keyword: "",
            date_from: "2026-03-01",
            date_to: "2026-03-02",
            page: 1,
            page_size: 50,
          },
          date_from: "2026-03-01",
          date_to: "2026-03-02",
          mode: "rebuild",
          cursor_key: "",
          output_dir: "/tmp/out",
        }),
      }),
    );
  });

  it("keeps snake_case mapping payload fields when called from mapping flow", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true }),
    });
    const client = createDesktopHttpClient({
      baseUrl: "http://127.0.0.1:42679",
      apiToken: "secret-token",
      fetchFn,
    });
    const commands = createDesktopCommands({ client });

    await commands.saveMapping({
      source_name: "华润集团",
      target_value: "央企",
      match_field: "group",
      target_field: "source_type",
      notes: "覆盖",
      confirm_overwrite: true,
    } as any);

    expect(fetchFn).toHaveBeenLastCalledWith(
      "http://127.0.0.1:42679/api/mappings",
      expect.objectContaining({ method: "POST" }),
    );
    const [, requestOptions] = fetchFn.mock.lastCall || [];
    expect(JSON.parse(String(requestOptions?.body || "{}"))).toEqual({
      source_name: "华润集团",
      target_value: "央企",
      match_field: "group",
      target_field: "source_type",
      notes: "覆盖",
      confirm_overwrite: true,
    });
  });

  it("publishes request outcomes into smoke fetch trace when enabled", async () => {
    const smokeTraceKey = "__PEAP_DESKTOP_SMOKE_FETCH_TRACE";
    const smokeTrace = [];
    vi.stubGlobal(smokeTraceKey, smokeTrace);
    const client = createDesktopHttpClient({
      baseUrl: "http://127.0.0.1:42679",
      apiToken: "secret-token",
      fetchFn: vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({ error: "conflict" }),
      }),
    });

    await expect(client("/api/jobs/manual-import", {
      method: "POST",
      body: { input_dir: "/tmp/manual" },
    })).rejects.toMatchObject({
      code: "HTTP_409",
    });

    expect(smokeTrace).toEqual([
      {
        url: "http://127.0.0.1:42679/api/jobs/manual-import",
        method: "POST",
        status: 409,
        ok: false,
        detail: "conflict",
      },
    ]);
  });
});
