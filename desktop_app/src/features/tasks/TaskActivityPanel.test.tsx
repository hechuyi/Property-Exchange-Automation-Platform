import { fireEvent, render, screen, within } from "@testing-library/react";
import { TaskActivityPanel } from "./TaskActivityPanel";

describe("TaskActivityPanel", () => {
  it("renders recent jobs and selected events inline", () => {
    const onSelectJob = vi.fn();

    render(
      <TaskActivityPanel
        caption="日常监控已内联到工作台，任务页只保留为次级历史视图。"
        jobs={[
          {
            job_id: "job-1",
            job_type: "one_click",
            status: "running",
            downloaded_count: 5,
            persisted_count: 4,
            exception_count: 0,
            summary: {},
          },
          {
            job_id: "job-2",
            job_type: "manual_import",
            status: "failed",
            downloaded_count: 1,
            persisted_count: 0,
            exception_count: 1,
            summary: {},
          },
        ]}
        selectedJobId="job-1"
        onSelectJob={onSelectJob}
        events={[
          { stage: "save_pages", status: "running", project_code: "P-1", payload: { label: "正在保存" } },
          { stage: "save_pages", status: "failed", project_code: "P-2", error_type: "manual_import_failed" },
        ]}
        capacityNotice="只显示前 2 条事件，仍有剩余 1 条"
      />,
    );

    const panel = screen.getByTestId("task-activity-panel");
    expect(within(panel).getByText(/日常监控已内联到工作台/)).toBeInTheDocument();
    expect(within(panel).getByText(/一键执行 · 进行中/)).toBeInTheDocument();
    expect(within(panel).getByText(/进行中 · P-1/)).toBeInTheDocument();
    expect(within(panel).getByText(/只显示前 2 条事件/)).toBeInTheDocument();
    expect(within(panel).getByText(/手动导入需人工处理，请在工作台查看任务活动。/)).toBeInTheDocument();
    expect(within(panel).getByTestId("task-activity-job-scroll")).toHaveStyle({
      maxHeight: "240px",
      overflowY: "auto",
    });
    expect(within(panel).getByTestId("task-activity-event-scroll")).toHaveStyle({
      maxHeight: "320px",
      overflowY: "auto",
    });

    fireEvent.click(screen.getByRole("button", { name: /手动导入解析 · 需人工处理/ }));
    expect(onSelectJob).toHaveBeenCalledWith("job-2");
  });
});
