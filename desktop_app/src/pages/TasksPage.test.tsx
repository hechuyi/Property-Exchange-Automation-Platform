import { Refine } from "@refinedev/core";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { DesktopProvider } from "../desktop/provider";
import TasksPage from "./TasksPage";
import { PAGE_TEST_IDS } from "../testing/selectors";

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
        <TasksPage />
      </Refine>
    </DesktopProvider>,
  );
}

describe("TasksPage", () => {
  it("renders job list and event list with selector contract", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/jobs?")) {
        return Promise.resolve(
          createJsonResponse({
            jobs: [
              {
                job_id: "job-1",
                job_type: "one_click",
                status: "running",
                downloaded_count: 5,
                persisted_count: 4,
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
              { stage: "save_pages", status: "failed", project_code: "P-2", error_type: "manual_import_failed" },
            ],
            truncated: true,
            returned_count: 2,
            total_count: 3,
          }),
        );
      }
      return Promise.resolve(createJsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(await screen.findByTestId(PAGE_TEST_IDS.tasks.page)).toBeInTheDocument();
    const jobList = screen.getByTestId(PAGE_TEST_IDS.tasks.jobList);
    const eventList = screen.getByTestId(PAGE_TEST_IDS.tasks.eventList);
    expect(jobList).toBeInTheDocument();
    expect(eventList).toBeInTheDocument();
    expect(screen.getByText(/日常监控已移到工作台/)).toBeInTheDocument();

    expect(await within(jobList).findByText(/一键执行 · 进行中/)).toBeInTheDocument();
    expect(await within(eventList).findByText(/进行中 · P-1/)).toBeInTheDocument();
    expect(await within(eventList).findByText(/只显示前 2 条事件/)).toBeInTheDocument();
    expect(within(eventList).getByText(/手动导入需人工处理，请在工作台查看任务活动。/)).toBeInTheDocument();
  });

  it("switches selected job and reloads events", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/jobs?")) {
        return Promise.resolve(
          createJsonResponse({
            jobs: [
              {
                job_id: "job-1",
                job_type: "one_click",
                status: "running",
                downloaded_count: 5,
                persisted_count: 4,
                exception_count: 0,
                summary: {},
                created_at: "2026-03-28T01:00:00Z",
                updated_at: "2026-03-28T01:02:00Z",
              },
              {
                job_id: "job-2",
                job_type: "manual_import",
                status: "failed",
                downloaded_count: 1,
                persisted_count: 0,
                exception_count: 1,
                summary: {},
                created_at: "2026-03-28T01:10:00Z",
                updated_at: "2026-03-28T01:12:00Z",
              },
            ],
          }),
        );
      }
      if (url.includes("/api/jobs/job-1/events")) {
        return Promise.resolve(createJsonResponse({ events: [{ stage: "save_pages", status: "running", project_code: "P-1" }], truncated: false, returned_count: 1, total_count: 1 }));
      }
      if (url.includes("/api/jobs/job-2/events")) {
        return Promise.resolve(createJsonResponse({ events: [{ stage: "save_pages", status: "failed", project_code: "P-2" }], truncated: false, returned_count: 1, total_count: 1 }));
      }
      return Promise.resolve(createJsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    const secondJob = await screen.findByRole("button", { name: /手动导入解析 · 需人工处理/ });
    fireEvent.click(secondJob);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/jobs/job-2/events"), expect.anything());
    });
  });
});
