import { Refine } from "@refinedev/core";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DesktopProvider } from "../desktop/provider";
import { createDefaultScope, setSharedRecordsScope } from "../features/records/scope";
import OverviewPage from "./OverviewPage";
import { PAGE_TEST_IDS } from "../testing/selectors";

type JsonPayload = Record<string, unknown>;

function createJsonResponse(payload: JsonPayload) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  } as Response;
}

function createJsonErrorResponse(payload: JsonPayload, status = 400) {
  return {
    ok: false,
    status,
    json: async () => payload,
  } as Response;
}

function renderPage() {
  return render(
    <DesktopProvider config={{ backendUrl: "http://127.0.0.1:42679", apiToken: "token" }}>
      <Refine resources={[]}>
        <OverviewPage />
      </Refine>
    </DesktopProvider>,
  );
}

describe("OverviewPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setSharedRecordsScope(createDefaultScope());
    const peapDesktop = {
      pickDirectory: vi.fn().mockResolvedValue("/tmp/manual"),
      restartBackend: vi.fn().mockResolvedValue({ ok: true, backendUrl: "http://127.0.0.1:42679" }),
      getBackendConfig: vi.fn().mockResolvedValue({ backendUrl: "http://127.0.0.1:42679", apiToken: "token" }),
    };
    Object.defineProperty(window, "peapDesktop", {
      value: peapDesktop,
      configurable: true,
      writable: true,
    });
  });

  it("renders selectors and overview payload semantics", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/overview")) {
        return Promise.resolve(
          createJsonResponse({
            latest_progress: {
              phase_code: "save_pages",
              phase_label: "正在保存网页",
              phase_percent: 56,
              downloaded_count: 8,
              persisted_count: 6,
              pending_mapping_count: 1,
            },
            latest_job: {
              job_id: "job-running",
              job_type: "one_click",
              status: "running",
              downloaded_count: 8,
              persisted_count: 6,
              exception_count: 0,
              summary: {},
            },
            browser_runtime: { installed: true, launch_ready: true },
            browser_install: { status: "succeeded", message: "浏览器已安装" },
            product_readiness: { download_ready: true, issues: [] },
            record_state_counts: {},
            pending_mapping_count: 0,
          }),
        );
      }
      return Promise.resolve(createJsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(await screen.findByTestId(PAGE_TEST_IDS.overview.page)).toBeInTheDocument();
    expect(screen.getByTestId(PAGE_TEST_IDS.overview.primaryActions)).toBeInTheDocument();
    expect(screen.getByTestId(PAGE_TEST_IDS.overview.progressCard)).toBeInTheDocument();
    expect(screen.getByTestId(PAGE_TEST_IDS.overview.runtimeCard)).toBeInTheDocument();

    expect(await screen.findByText("正在保存网页")).toBeInTheDocument();
    expect(screen.getByText("56%")).toBeInTheDocument();
    expect(screen.getByText(/已保存网页 8 条/)).toBeInTheDocument();
    expect(screen.getByText(/系统正在保存已扫描到的页面/)).toBeInTheDocument();
    expect(screen.getByText("运行环境已就绪")).toBeInTheDocument();
  });

  it("runs one-click action", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url.endsWith("/api/overview") && method === "GET") {
        return Promise.resolve(
          createJsonResponse({
            latest_progress: { phase_code: "", phase_label: "暂无任务", phase_percent: 0 },
            latest_job: null,
            browser_runtime: { installed: true, launch_ready: true },
            browser_install: { status: "succeeded" },
            product_readiness: { download_ready: true, issues: [] },
            record_state_counts: {},
            pending_mapping_count: 0,
          }),
        );
      }
      if (url.endsWith("/api/jobs/one-click") && method === "POST") {
        return Promise.resolve(createJsonResponse({ job_id: "job-new" }));
      }
      return Promise.resolve(createJsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    await screen.findByTestId(PAGE_TEST_IDS.overview.primaryActions);
    fireEvent.click(screen.getByRole("button", { name: "一键执行" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/jobs/one-click"),
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("surfaces manual import failure detail instead of silent fallback", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url.endsWith("/api/overview") && method === "GET") {
        return Promise.resolve(
          createJsonResponse({
            latest_progress: { phase_code: "", phase_label: "暂无任务", phase_percent: 0 },
            latest_job: null,
            browser_runtime: { installed: true, launch_ready: true },
            browser_install: { status: "succeeded" },
            product_readiness: { download_ready: true, issues: [] },
            record_state_counts: {},
            pending_mapping_count: 0,
          }),
        );
      }
      if (url.endsWith("/api/jobs/manual-import") && method === "POST") {
        return Promise.resolve(
          createJsonErrorResponse({
            error: "手动导入目录不存在：/tmp/manual",
          }),
        );
      }
      return Promise.resolve(createJsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByTestId(PAGE_TEST_IDS.overview.primaryActions);

    fireEvent.click(screen.getByRole("button", { name: "手动导入" }));

    expect(await screen.findByText(/手动导入目录不存在/)).toBeInTheDocument();
  });

  it("publishes manual import mutation phases into smoke interaction trace", async () => {
    const smokeTrace = {
      manualImport: {},
      windowErrors: [],
    };
    Object.defineProperty(window, "__PEAP_DESKTOP_SMOKE_INTERACTION_TRACE", {
      value: smokeTrace,
      configurable: true,
      writable: true,
    });

    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url.endsWith("/api/overview") && method === "GET") {
        return Promise.resolve(
          createJsonResponse({
            latest_progress: { phase_code: "", phase_label: "暂无任务", phase_percent: 0 },
            latest_job: null,
            browser_runtime: { installed: true, launch_ready: true },
            browser_install: { status: "succeeded" },
            product_readiness: { download_ready: true, issues: [] },
            record_state_counts: {},
            pending_mapping_count: 0,
          }),
        );
      }
      if (url.endsWith("/api/jobs/manual-import") && method === "POST") {
        return Promise.resolve(
          createJsonErrorResponse({
            error: "目录冲突",
          }, 409),
        );
      }
      return Promise.resolve(createJsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByTestId(PAGE_TEST_IDS.overview.primaryActions);

    fireEvent.click(screen.getByRole("button", { name: "手动导入" }));

    expect(await screen.findByText("目录冲突")).toBeInTheDocument();
    await waitFor(() => {
      expect(smokeTrace.manualImport).toMatchObject({
        mutationEvents: [
          expect.objectContaining({ phase: "pick_directory_resolved", inputDir: "/tmp/manual" }),
          expect.objectContaining({ phase: "request_started", inputDir: "/tmp/manual" }),
          expect.objectContaining({ phase: "request_failed", message: "目录冲突" }),
        ],
      });
    });
  });

  it("fails explicitly when manual import response misses job_id and appends trace evidence", async () => {
    const smokeTrace = {
      manualImport: {},
      windowErrors: [],
    };
    Object.defineProperty(window, "__PEAP_DESKTOP_SMOKE_INTERACTION_TRACE", {
      value: smokeTrace,
      configurable: true,
      writable: true,
    });

    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url.endsWith("/api/overview") && method === "GET") {
        return Promise.resolve(
          createJsonResponse({
            latest_progress: { phase_code: "", phase_label: "暂无任务", phase_percent: 0 },
            latest_job: null,
            browser_runtime: { installed: true, launch_ready: true },
            browser_install: { status: "succeeded" },
            product_readiness: { download_ready: true, issues: [] },
            record_state_counts: {},
            pending_mapping_count: 0,
          }),
        );
      }
      if (url.endsWith("/api/jobs/manual-import") && method === "POST") {
        return Promise.resolve(
          createJsonResponse({
            status: "accepted",
            message: "manual import accepted",
          }),
        );
      }
      return Promise.resolve(createJsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByTestId(PAGE_TEST_IDS.overview.primaryActions);

    fireEvent.click(screen.getByRole("button", { name: "手动导入" }));

    expect(await screen.findByText(/缺少 job_id/)).toBeInTheDocument();
    await waitFor(() => {
      expect(smokeTrace.manualImport).toMatchObject({
        mutationEvents: expect.arrayContaining([
          expect.objectContaining({ phase: "request_invalid_response", inputDir: "/tmp/manual" }),
        ]),
      });
    });
  });

  it("runs export with live records scope instead of desktop defaults and renders empty export state", async () => {
    setSharedRecordsScope({
      recordFamily: "listing",
      state: "pending_mapping",
      projectType: "equity_transfer",
      keyword: "国资",
      dateFrom: "2026-03-01",
      dateTo: "2026-03-02",
      page: 3,
      pageSize: 100,
    });

    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url.endsWith("/api/overview") && method === "GET") {
        return Promise.resolve(
          createJsonResponse({
            latest_progress: { phase_code: "", phase_label: "暂无任务", phase_percent: 0 },
            latest_job: null,
            browser_runtime: { installed: true, launch_ready: true },
            browser_install: { status: "succeeded" },
            product_readiness: { download_ready: true, issues: [] },
            record_state_counts: {},
            pending_mapping_count: 0,
          }),
        );
      }
      if (url.endsWith("/api/exports") && method === "POST") {
        return Promise.resolve(
          createJsonResponse({
            job_id: "job-export",
            status: "empty",
            message: "当前条件下没有可导出的记录",
            empty_reason_code: "no_matching_records",
            scope_state_counts: {
              pending_mapping: 0,
              skipped: 0,
            },
          }),
        );
      }
      return Promise.resolve(createJsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByTestId(PAGE_TEST_IDS.overview.primaryActions);

    fireEvent.click(screen.getByRole("button", { name: "导出 Excel" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/exports"),
        expect.objectContaining({ method: "POST" }),
      );
    });

    const exportCall = fetchMock.mock.calls.find(([input, init]) => {
      return String(input).includes("/api/exports") && String(init?.method || "GET").toUpperCase() === "POST";
    });
    const exportBody = JSON.parse(String(exportCall?.[1]?.body || "{}"));

    expect(exportBody).toMatchObject({
      scope: {
        record_family: "listing",
        state: "pending_mapping",
        project_type: "equity_transfer",
        keyword: "国资",
        date_from: "2026-03-01",
        date_to: "2026-03-02",
        page: 3,
        page_size: 100,
      },
      mode: "rebuild",
    });

    expect(await screen.findByText(/导出结果为空/)).toBeInTheDocument();
    expect(screen.getByText(/当前条件下没有可导出的记录/)).toBeInTheDocument();
  });

  it("shows export failed terminal state explicitly", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url.endsWith("/api/overview") && method === "GET") {
        return Promise.resolve(
          createJsonResponse({
            latest_progress: { phase_code: "", phase_label: "暂无任务", phase_percent: 0 },
            latest_job: null,
            browser_runtime: { installed: true, launch_ready: true },
            browser_install: { status: "succeeded" },
            product_readiness: { download_ready: true, issues: [] },
            record_state_counts: {},
            pending_mapping_count: 0,
          }),
        );
      }
      if (url.endsWith("/api/exports") && method === "POST") {
        return Promise.resolve(
          createJsonResponse({
            status: "failed",
            message: "导出失败：磁盘不可写",
            job_id: "job-failed",
          }),
        );
      }
      return Promise.resolve(createJsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByTestId(PAGE_TEST_IDS.overview.primaryActions);

    fireEvent.click(screen.getByRole("button", { name: "导出 Excel" }));

    expect(await screen.findByText(/导出失败/)).toBeInTheDocument();
    expect(screen.getByText(/磁盘不可写/)).toBeInTheDocument();
  });

  it("publishes force stop mutation phases into smoke interaction trace", async () => {
    const smokeTrace = {
      manualImport: {},
      forceStop: {},
      windowErrors: [],
    };
    Object.defineProperty(window, "__PEAP_DESKTOP_SMOKE_INTERACTION_TRACE", {
      value: smokeTrace,
      configurable: true,
      writable: true,
    });

    const restartBackend = vi.fn().mockResolvedValue({ ok: true, backendUrl: "http://127.0.0.1:42679" });
    Object.defineProperty(window, "peapDesktop", {
      value: {
        pickDirectory: vi.fn().mockResolvedValue("/tmp/manual"),
        restartBackend,
        getBackendConfig: vi.fn().mockResolvedValue({ backendUrl: "http://127.0.0.1:42679", apiToken: "token" }),
      },
      configurable: true,
      writable: true,
    });

    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url.endsWith("/api/overview") && method === "GET") {
        return Promise.resolve(
          createJsonResponse({
            latest_progress: { phase_code: "", phase_label: "暂无任务", phase_percent: 0 },
            latest_job: {
              job_id: "job-running",
              job_type: "manual_import",
              status: "running",
            },
            browser_runtime: { installed: true, launch_ready: true },
            browser_install: { status: "succeeded" },
            product_readiness: { download_ready: true, issues: [] },
            record_state_counts: {},
            pending_mapping_count: 0,
          }),
        );
      }
      return Promise.resolve(createJsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByTestId(PAGE_TEST_IDS.overview.primaryActions);
    const forceStopButton = screen.getByRole("button", { name: "强制停止" });
    await waitFor(() => {
      expect(forceStopButton).not.toBeDisabled();
    });
    fireEvent.click(forceStopButton);

    await waitFor(() => {
      expect(restartBackend).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(smokeTrace.forceStop).toMatchObject({
        mutationEvents: [
          expect.objectContaining({ phase: "request_started" }),
          expect.objectContaining({ phase: "request_succeeded" }),
        ],
      });
    });
  });

  it("enables force stop when a recent job is still running even if latest_job is terminal", async () => {
    const restartBackend = vi.fn().mockResolvedValue({ ok: true, backendUrl: "http://127.0.0.1:42679" });
    Object.defineProperty(window, "peapDesktop", {
      value: {
        pickDirectory: vi.fn().mockResolvedValue("/tmp/manual"),
        restartBackend,
        getBackendConfig: vi.fn().mockResolvedValue({ backendUrl: "http://127.0.0.1:42679", apiToken: "token" }),
      },
      configurable: true,
      writable: true,
    });

    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = String(init?.method || "GET").toUpperCase();
      if (url.endsWith("/api/overview") && method === "GET") {
        return Promise.resolve(
          createJsonResponse({
            latest_progress: { phase_code: "", phase_label: "暂无任务", phase_percent: 0 },
            latest_job: {
              job_id: "job-export",
              job_type: "export_excel",
              status: "success",
            },
            recent_jobs: [
              { job_id: "job-export", job_type: "export_excel", status: "success" },
              { job_id: "job-running", job_type: "manual_import", status: "running" },
            ],
            browser_runtime: { installed: true, launch_ready: true },
            browser_install: { status: "succeeded" },
            product_readiness: { download_ready: true, issues: [] },
            record_state_counts: {},
            pending_mapping_count: 0,
          }),
        );
      }
      return Promise.resolve(createJsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    await screen.findByTestId(PAGE_TEST_IDS.overview.primaryActions);

    const forceStopButton = screen.getByRole("button", { name: "强制停止" });
    await waitFor(() => {
      expect(forceStopButton).not.toBeDisabled();
    });

    fireEvent.click(forceStopButton);

    await waitFor(() => {
      expect(restartBackend).toHaveBeenCalledTimes(1);
    });
  });
});
