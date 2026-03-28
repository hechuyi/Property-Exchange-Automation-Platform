import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MappingsPage from "./MappingsPage";
import { PAGE_TEST_IDS } from "../testing/selectors";

const listMappings = vi.fn();
const previewMapping = vi.fn();
const saveMapping = vi.fn();
const reprocessPendingMappings = vi.fn();
const runtime = {
  config: {
    backendUrl: "http://127.0.0.1:42679",
    apiToken: "token",
  },
  commands: {
    listMappings,
    previewMapping,
    saveMapping,
    reprocessPendingMappings,
  },
};

vi.mock("../desktop/provider", () => ({
  useDesktopRuntime: () => runtime,
}));

describe("MappingsPage", () => {
  beforeEach(() => {
    listMappings.mockReset();
    previewMapping.mockReset();
    saveMapping.mockReset();
    reprocessPendingMappings.mockReset();
  });

  it("renders selector contract zones", async () => {
    listMappings.mockResolvedValue({ pending: [], entries: [] });

    render(<MappingsPage />);

    await waitFor(() => {
      expect(listMappings).toHaveBeenCalledTimes(1);
    });

    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.page)).toBeInTheDocument();
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.pendingList)).toBeInTheDocument();
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.editor)).toBeInTheDocument();
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.preview)).toBeInTheDocument();
  });

  it("imports pending draft then handles conflict-confirmed save", async () => {
    listMappings.mockResolvedValue({
      pending: [
        {
          record_id: "rec-1",
          project_code: "P-001",
          payload: {
            项目名称: "测试项目",
            转让方: "华润置地",
            隶属集团: "华润",
          },
        },
      ],
      entries: [],
    });
    previewMapping.mockResolvedValue({
      conflict: true,
      mode: "overwrite",
      source_name: "华润",
      target_value: "央企",
      match_field: "group",
      target_field: "source_type",
      existing_entry: { source_type: "地方国企" },
      affected_count: 5,
      affected_pending_count: 2,
    });
    saveMapping.mockResolvedValue({
      job_id: "job-1",
      affected_count: 5,
    });

    render(<MappingsPage />);

    await waitFor(() => {
      expect(listMappings).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(screen.getByRole("button", { name: "导入到草稿" }));

    const targetInput = document.querySelector('input[data-draft-field="targetValue"]') as HTMLInputElement;
    expect(targetInput).toBeTruthy();
    fireEvent.change(targetInput, { target: { value: "央企" } });

    fireEvent.click(screen.getByRole("button", { name: "保存已填写规则" }));

    await waitFor(() => {
      expect(screen.getByTestId(PAGE_TEST_IDS.mappings.preview)).toHaveTextContent("已存在同来源规则");
    });

    fireEvent.click(screen.getByRole("button", { name: "确认覆盖" }));

    await waitFor(() => {
      expect(saveMapping).toHaveBeenCalledWith(
        expect.objectContaining({
          source_name: "华润",
          target_value: "央企",
          confirm_overwrite: true,
        }),
      );
    });
  });

  it("filters saved entries and loads one rule into single editor with explicit lock boundary", async () => {
    listMappings.mockResolvedValue({
      pending: [],
      entries: [
        {
          entry_id: "entry-1",
          company_name: "华润集团",
          source_type: "央企",
          metadata: {
            match_field: "group",
            target_field: "source_type",
            notes: "旧备注",
          },
          updated_at: "2026-03-28 10:00:00",
        },
        {
          entry_id: "entry-2",
          company_name: "越秀集团",
          group_name: "地方平台",
          metadata: {
            match_field: "transferor",
            target_field: "group_name",
            notes: "广州",
          },
          updated_at: "2026-03-28 11:00:00",
        },
      ],
    });

    render(<MappingsPage />);

    await waitFor(() => {
      expect(listMappings).toHaveBeenCalledTimes(1);
    });

    expect(screen.getByText("共 2 条规则，支持按规则类型和关键字筛选")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("按来源/目标值/备注筛选"), { target: { value: "华润" } });
    expect(screen.getByText("共 2 条规则，当前命中 1 条")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "加载到单条编辑" }));

    const sourceInput = screen.getByLabelText("来源名称") as HTMLInputElement;
    const targetInput = screen.getByLabelText("目标值") as HTMLInputElement;
    expect(sourceInput.value).toBe("华润集团");
    expect(sourceInput).toBeDisabled();
    expect(targetInput.value).toBe("央企");
    expect(screen.getByText("正在编辑已保存规则；来源名称与规则类型已锁定，如需新建请点击“新建规则”")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "新建规则" }));
    expect(sourceInput).not.toBeDisabled();
    expect(sourceInput.value).toBe("");
  });

  it("shows capacity notice after truncated single-rule save result", async () => {
    listMappings.mockResolvedValue({ pending: [], entries: [] });
    previewMapping.mockResolvedValue({
      conflict: false,
      mode: "update",
      source_name: "华润集团",
      target_value: "央企",
    });
    saveMapping.mockResolvedValue({
      job_id: "job-2",
      affected_count: 200,
      affected_returned_count: 200,
      affected_total_count: 350,
      truncated: true,
    });

    render(<MappingsPage />);

    await waitFor(() => {
      expect(listMappings).toHaveBeenCalledTimes(1);
    });

    fireEvent.change(screen.getByLabelText("来源名称"), { target: { value: "华润集团" } });
    fireEvent.change(screen.getByLabelText("目标值"), { target: { value: "央企" } });
    fireEvent.click(screen.getByRole("button", { name: "保存单条规则" }));

    await waitFor(() => {
      expect(saveMapping).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.preview)).toHaveTextContent("只显示前 200 条记录，仍有剩余 150 条");
  });

  it("locks batch save while conflict confirmation is pending", async () => {
    listMappings.mockResolvedValue({
      pending: [
        {
          record_id: "rec-lock-1",
          project_code: "P-LOCK-001",
          payload: {
            项目名称: "冲突项目",
            转让方: "华润置地",
          },
        },
      ],
      entries: [],
    });
    previewMapping.mockResolvedValue({
      conflict: true,
      mode: "overwrite",
      source_name: "华润置地",
      target_value: "央企",
      match_field: "transferor",
      target_field: "source_type",
      existing_entry: { source_type: "地方国企" },
      affected_count: 2,
      affected_pending_count: 1,
    });
    saveMapping.mockResolvedValue({
      job_id: "job-lock-1",
      affected_count: 2,
    });

    render(<MappingsPage />);

    await waitFor(() => {
      expect(listMappings).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(screen.getByRole("button", { name: "导入到草稿" }));
    const targetInput = document.querySelector('input[data-draft-field="targetValue"]') as HTMLInputElement;
    fireEvent.change(targetInput, { target: { value: "央企" } });

    const batchSaveButton = screen.getByRole("button", { name: "保存已填写规则" });
    fireEvent.click(batchSaveButton);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "确认覆盖" })).toBeInTheDocument();
    });
    expect(batchSaveButton).toBeDisabled();

    fireEvent.click(batchSaveButton);
    expect(previewMapping).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "确认覆盖" }));
    await waitFor(() => {
      expect(saveMapping).toHaveBeenCalledTimes(1);
    });
  });

  it("shows capacity notice after truncated pending reprocess result", async () => {
    listMappings.mockResolvedValue({ pending: [], entries: [] });
    reprocessPendingMappings.mockResolvedValue({
      job_id: "job-reprocess-1",
      affected_count: 80,
      affected_returned_count: 80,
      affected_total_count: 130,
      truncated: true,
    });

    render(<MappingsPage />);

    await waitFor(() => {
      expect(listMappings).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(screen.getByRole("button", { name: "一键重处理当前所有待补项" }));

    await waitFor(() => {
      expect(reprocessPendingMappings).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.preview)).toHaveTextContent("只显示前 80 条记录，仍有剩余 50 条");
  });
});
