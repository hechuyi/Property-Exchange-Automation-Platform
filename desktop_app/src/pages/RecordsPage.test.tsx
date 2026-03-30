import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createDefaultScope, getSharedRecordsScope, setSharedRecordsScope } from "../features/records/scope";
import RecordsPage from "./RecordsPage";
import { PAGE_TEST_IDS } from "../testing/selectors";

const listRecords = vi.fn();
const runExport = vi.fn();

const runtime = {
  config: {
    backendUrl: "http://127.0.0.1:42679",
    apiToken: "secret-token",
  },
  commands: {
    listRecords,
    runExport,
  },
};

vi.mock("../desktop/provider", () => ({
  useDesktopRuntime: () => runtime,
}));

function buildRecordsPayload(overrides: Record<string, unknown> = {}) {
  return {
    page: 1,
    page_count: 2,
    has_more: true,
    summary: {
      total_count: 2,
      visible_count: 1,
      filtered_state_counts: {
        ready: 1,
      },
    },
    rows: [
      {
        record_id: "rec-1",
        state: "ready",
        status_label: "已录入",
        project_code: "P-001",
        project_name: "测试项目",
        project_type: "equity_transfer",
        exchange: "北交所",
        listing_date: "2026-03-01",
        archive_path: "/tmp/archive/P-001.xlsx",
        source_file: "/tmp/source/P-001.xlsx",
        updated_at: "2026-03-01 10:00:00",
      },
    ],
    ...overrides,
  };
}

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolveFn, rejectFn) => {
    resolve = resolveFn;
    reject = rejectFn;
  });
  return { promise, resolve, reject };
}

describe("RecordsPage", () => {
  beforeEach(() => {
    listRecords.mockReset();
    runExport.mockReset();
    listRecords.mockResolvedValue(buildRecordsPayload());
    runExport.mockResolvedValue({
      job_id: "job-1",
      status: "completed",
      message: "导出完成，共生成 1 个文件",
      artifacts: ["/tmp/archive/P-001.xlsx"],
    });
    setSharedRecordsScope(createDefaultScope());

    window.peapDesktop = {
      getBackendConfig: () => ({ backendUrl: "http://127.0.0.1:42679", apiToken: "secret-token" }),
      openPath: vi.fn(),
      showItemInFolder: vi.fn(),
    };
  });

  function getRecordsDetailPanel() {
    return screen.getByRole("heading", { name: "记录详情" }).closest("aside") as HTMLElement;
  }

  it("renders records root selector, filters, summary and table", async () => {
    render(<RecordsPage />);

    expect(screen.getByTestId(PAGE_TEST_IDS.records.page)).toBeInTheDocument();
    expect(screen.getByTestId(PAGE_TEST_IDS.records.filters)).toBeInTheDocument();
    expect(screen.getByTestId(PAGE_TEST_IDS.records.summary)).toBeInTheDocument();
    expect(screen.getByTestId(PAGE_TEST_IDS.records.table)).toBeInTheDocument();

    expect(document.getElementById("recordsStateFilter")).toBeInTheDocument();
    expect(document.getElementById("recordsProjectTypeFilter")).toBeInTheDocument();
    expect(document.getElementById("recordsDateFromInput")).toBeInTheDocument();
    expect(document.getElementById("recordsDateToInput")).toBeInTheDocument();
    expect(document.getElementById("recordsKeywordInput")).toBeInTheDocument();

    await waitFor(() => {
      expect(listRecords).toHaveBeenCalledWith(
        expect.objectContaining({
          recordFamily: "listing",
          page: 1,
          pageSize: 50,
        }),
      );
    });

    expect(await within(screen.getByTestId(PAGE_TEST_IDS.records.table)).findByText("测试项目")).toBeInTheDocument();
    expect(screen.getAllByText("已就绪").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "记录详情" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "打开文件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "在文件夹中显示" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "实物资产" })).toHaveValue("physical_asset");
    expect(screen.getByRole("option", { name: "预披露" })).toHaveValue("pre_disclosure");
    expect(screen.queryByRole("option", { name: "资产转让" })).not.toBeInTheDocument();
  });

  it("submits filter scope via listRecords", async () => {
    render(<RecordsPage />);
    await waitFor(() => expect(listRecords).toHaveBeenCalledTimes(1));

    listRecords.mockClear();

    fireEvent.change(document.getElementById("recordsStateFilter") as HTMLSelectElement, {
      target: { value: "pending_mapping" },
    });
    fireEvent.change(document.getElementById("recordsProjectTypeFilter") as HTMLSelectElement, {
      target: { value: "physical_asset" },
    });
    fireEvent.change(document.getElementById("recordsKeywordInput") as HTMLInputElement, {
      target: { value: "国资" },
    });
    fireEvent.change(document.getElementById("recordsDateFromInput") as HTMLInputElement, {
      target: { value: "2026-03-01" },
    });
    fireEvent.change(document.getElementById("recordsDateToInput") as HTMLInputElement, {
      target: { value: "2026-03-02" },
    });
    fireEvent.change(screen.getByLabelText("每页"), {
      target: { value: "100" },
    });

    fireEvent.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() => {
      expect(listRecords).toHaveBeenCalledWith(
        expect.objectContaining({
          state: "pending_mapping",
          projectType: "physical_asset",
          keyword: "国资",
          dateFrom: "2026-03-01",
          dateTo: "2026-03-02",
          page: 1,
          pageSize: 100,
        }),
      );
    });
  });

  it("supports pagination and loads next page when has_more is true", async () => {
    listRecords.mockResolvedValueOnce(buildRecordsPayload({ page: 1, has_more: true }));
    listRecords.mockResolvedValueOnce(
      buildRecordsPayload({
        page: 2,
        has_more: false,
        rows: [
          {
            record_id: "rec-2",
            state: "parse_failed",
            status_label: "解析失败",
            project_code: "P-002",
            project_name: "第二个项目",
            project_type: "physical_asset",
            exchange: "北交所",
            listing_date: "2026-03-03",
            source_file: "/tmp/source/P-002.xlsx",
            updated_at: "2026-03-03 10:00:00",
          },
        ],
      }),
    );

    render(<RecordsPage />);

    await screen.findByText("测试项目");
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    await waitFor(() => {
      expect(listRecords).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }));
    });
    expect(await within(screen.getByTestId(PAGE_TEST_IDS.records.table)).findByText("第二个项目")).toBeInTheDocument();
    expect(within(screen.getByTestId(PAGE_TEST_IDS.records.table)).getByText("需人工处理")).toBeInTheDocument();
    expect(screen.queryByText("解析失败")).not.toBeInTheDocument();
  });

  it("uses row selection to drive the detail panel", async () => {
    listRecords.mockResolvedValueOnce(
      buildRecordsPayload({
        rows: [
          {
            record_id: "rec-1",
            state: "ready",
            status_label: "已录入",
            project_code: "P-001",
            project_name: "第一个项目",
            project_type: "equity_transfer",
            exchange: "北交所",
            archive_path: "/tmp/archive/P-001.xlsx",
            updated_at: "2026-03-01 10:00:00",
          },
          {
            record_id: "rec-2",
            state: "mapping_conflict",
            status_label: "规则冲突",
            project_code: "P-002",
            project_name: "第二个项目",
            project_type: "pre_disclosure",
            exchange: "上交所",
            source_file: "/tmp/source/P-002.xlsx",
            updated_at: "2026-03-02 10:00:00",
          },
        ],
      }),
    );

    render(<RecordsPage />);

    const detailPanel = getRecordsDetailPanel();

    expect(await within(screen.getByTestId(PAGE_TEST_IDS.records.table)).findByText("第一个项目")).toBeInTheDocument();
    expect(within(detailPanel).getByText("第一个项目")).toBeInTheDocument();
    expect(within(detailPanel).getByText("P-001")).toBeInTheDocument();

    fireEvent.click(screen.getByText("第二个项目").closest("tr") as HTMLTableRowElement);

    expect(within(detailPanel).getByText("P-002")).toBeInTheDocument();
    expect(within(detailPanel).getByText("预披露")).toBeInTheDocument();
    expect(within(detailPanel).getByText("待补映射")).toBeInTheDocument();
    expect(within(detailPanel).getByText("存在待确认的映射口径，请先统一后再继续处理。")).toBeInTheDocument();
  });

  it("runs export as a first-class action using active records scope", async () => {
    render(<RecordsPage />);
    await waitFor(() => expect(listRecords).toHaveBeenCalledTimes(1));

    fireEvent.change(document.getElementById("recordsStateFilter") as HTMLSelectElement, {
      target: { value: "pending_mapping" },
    });
    fireEvent.change(document.getElementById("recordsKeywordInput") as HTMLInputElement, {
      target: { value: "国资" },
    });
    fireEvent.change(screen.getByLabelText("每页"), {
      target: { value: "100" },
    });
    fireEvent.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() => {
      expect(listRecords).toHaveBeenLastCalledWith(expect.objectContaining({ state: "pending_mapping", pageSize: 100 }));
    });

    fireEvent.click(screen.getByRole("button", { name: "导出 Excel" }));

    await waitFor(() => {
      expect(runExport).toHaveBeenCalledWith(
        expect.objectContaining({
          scope: expect.objectContaining({
            state: "pending_mapping",
            keyword: "国资",
            page: 1,
            pageSize: 100,
          }),
          mode: "rebuild",
        }),
      );
    });

    expect(await screen.findByText(/导出已完成/)).toBeInTheDocument();
  });

  it("renders loading then queued export state explicitly", async () => {
    const deferred = createDeferred<Record<string, unknown>>();
    runExport.mockImplementationOnce(() => deferred.promise);
    render(<RecordsPage />);
    await waitFor(() => expect(listRecords).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "导出 Excel" }));

    expect(await screen.findByText(/导出请求提交中/)).toBeInTheDocument();

    deferred.resolve({
      job_id: "job-queued",
      status: "queued",
      message: "导出任务已排队",
    });

    expect(await screen.findByText(/导出任务进行中/)).toBeInTheDocument();
  });

  it("renders empty export terminal state with reason details", async () => {
    runExport.mockResolvedValueOnce({
      job_id: "job-empty",
      status: "empty",
      message: "当前条件下没有可导出的记录；待补映射 2 条",
      empty_reason_code: "pending_mapping_blocked",
      scope_state_counts: {
        pending_mapping: 2,
        skipped: 0,
      },
    });

    render(<RecordsPage />);
    await waitFor(() => expect(listRecords).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "导出 Excel" }));

    expect(await screen.findByText(/导出结果为空/)).toBeInTheDocument();
    expect(screen.getByText(/pending_mapping_blocked/)).toBeInTheDocument();
    expect(screen.getByText(/待补映射 2 条/)).toBeInTheDocument();
  });

  it("renders failed export terminal state from payload status", async () => {
    runExport.mockResolvedValueOnce({
      job_id: "job-failed",
      status: "failed",
      message: "导出失败：磁盘不可写",
    });

    render(<RecordsPage />);
    await waitFor(() => expect(listRecords).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "导出 Excel" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("导出失败");
    expect(screen.getByRole("alert")).toHaveTextContent("磁盘不可写");
  });

  it("distinguishes records load failure from empty result", async () => {
    listRecords.mockRejectedValueOnce(new Error("数据库连接失败"));

    render(<RecordsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("记录加载失败：数据库连接失败");
    expect(screen.queryByText("当前筛选条件下没有记录")).not.toBeInTheDocument();
  });

  it("suppresses stale responses when newer records request has returned", async () => {
    const first = createDeferred<Record<string, unknown>>();
    const second = createDeferred<Record<string, unknown>>();
    listRecords.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);

    render(<RecordsPage />);

    fireEvent.change(document.getElementById("recordsStateFilter") as HTMLSelectElement, {
      target: { value: "pending_mapping" },
    });
    fireEvent.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() => expect(listRecords).toHaveBeenCalledTimes(2));

    second.resolve(
      buildRecordsPayload({
        rows: [
          {
            record_id: "new-rec",
            state: "pending_mapping",
            status_label: "待补映射",
            project_code: "P-NEW",
            project_name: "新 scope 结果",
            updated_at: "2026-03-05 10:00:00",
          },
        ],
      }),
    );
    expect(await within(screen.getByTestId(PAGE_TEST_IDS.records.table)).findByText("新 scope 结果")).toBeInTheDocument();

    first.resolve(
      buildRecordsPayload({
        rows: [
          {
            record_id: "old-rec",
            state: "ready",
            status_label: "已录入",
            project_code: "P-OLD",
            project_name: "旧 scope 结果",
            updated_at: "2026-03-01 10:00:00",
          },
        ],
      }),
    );

    await waitFor(() => {
      expect(screen.queryByText("旧 scope 结果")).not.toBeInTheDocument();
      expect(within(screen.getByTestId(PAGE_TEST_IDS.records.table)).getByText("新 scope 结果")).toBeInTheDocument();
    });
  });

  it("keeps shared scope immutable and updates only through setter API", () => {
    const initial = createDefaultScope();
    setSharedRecordsScope(initial);
    const snapshot = getSharedRecordsScope();
    snapshot.state = "pending_mapping";
    snapshot.keyword = "外部突变";

    const untouched = getSharedRecordsScope();
    expect(untouched.state).toBe(initial.state);
    expect(untouched.keyword).toBe(initial.keyword);

    const nextScope = { ...initial, state: "pending_mapping", keyword: "仅通过 API" };
    setSharedRecordsScope(nextScope);
    nextScope.state = "all";
    nextScope.keyword = "被篡改";

    const stored = getSharedRecordsScope();
    expect(stored.state).toBe("pending_mapping");
    expect(stored.keyword).toBe("仅通过 API");
  });
});
