import { Refine } from "@refinedev/core";
import { render, screen, within } from "@testing-library/react";
import { DesktopProvider } from "../desktop/provider";
import { PAGE_TEST_IDS } from "../testing/selectors";
import WorkbenchPage from "./WorkbenchPage";

type JsonPayload = Record<string, unknown>;

function createJsonResponse(payload: JsonPayload) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  } as Response;
}

function renderPage() {
  return render(
    <DesktopProvider config={{ backendUrl: "http://127.0.0.1:42679", apiToken: "token" }}>
      <Refine resources={[]}>
        <WorkbenchPage />
      </Refine>
    </DesktopProvider>,
  );
}

describe("WorkbenchPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(window, "peapDesktop", {
      value: {
        pickDirectory: vi.fn().mockResolvedValue("/tmp/manual"),
        restartBackend: vi.fn().mockResolvedValue({ ok: true, backendUrl: "http://127.0.0.1:42679" }),
      },
      configurable: true,
      writable: true,
    });
  });

  it("keeps primary actions and task activity on the same workbench surface", async () => {
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
              job_id: "job-1",
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
            pending_mapping_count: 2,
          }),
        );
      }
      if (url.includes("/api/jobs?")) {
        return Promise.resolve(
          createJsonResponse({
            jobs: [
              {
                job_id: "job-1",
                job_type: "one_click",
                status: "running",
                downloaded_count: 8,
                persisted_count: 6,
                exception_count: 0,
                summary: {},
                created_at: "2026-03-28T01:00:00Z",
                updated_at: "2026-03-28T01:02:00Z",
              },
            ],
          }),
        );
      }
      if (url.includes("/api/jobs/job-1/events")) {
        return Promise.resolve(
          createJsonResponse({
            events: [
              { stage: "save_pages", status: "running", project_code: "P-1", payload: { label: "正在保存" } },
            ],
            truncated: false,
            returned_count: 1,
            total_count: 1,
          }),
        );
      }
      return Promise.resolve(createJsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(await screen.findByTestId(PAGE_TEST_IDS.overview.page)).toBeInTheDocument();
    expect(screen.getByTestId(PAGE_TEST_IDS.overview.primaryActions)).toBeInTheDocument();
    const progressCard = screen.getByTestId(PAGE_TEST_IDS.overview.progressCard);
    expect(await within(progressCard).findByText("一键执行 · 进行中")).toBeInTheDocument();
    expect(within(progressCard).queryByText(/^正在保存网页$/)).not.toBeInTheDocument();

    const activityPanel = await screen.findByTestId("task-activity-panel");
    expect(within(activityPanel).getByText("任务活动")).toBeInTheDocument();
    expect(within(activityPanel).getByText(/日常监控已内联到工作台/)).toBeInTheDocument();
    expect(within(activityPanel).getByText(/一键执行 · 进行中/)).toBeInTheDocument();
    expect(await within(activityPanel).findByText(/进行中 · P-1/)).toBeInTheDocument();
  });
});
