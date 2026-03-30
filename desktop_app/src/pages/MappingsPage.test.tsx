import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.pendingList)).toHaveAttribute("data-layout-region", "queue");
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.editor)).toHaveAttribute("data-layout-region", "editor");
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.pendingList).closest('[data-layout="remediation-workspace"]')).toBeTruthy();
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.editor).closest('[data-layout="remediation-workspace"]')).toBeTruthy();
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.editor)).toContainElement(screen.getByTestId(PAGE_TEST_IDS.mappings.preview));
    expect(screen.getByText("已保存规则").closest('[data-layout="remediation-workspace"]')).toBeFalsy();
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.preview)).toHaveTextContent("等待处理结果");
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

    fireEvent.click(screen.getByRole("button", { name: "导入到编辑器" }));

    const editor = screen.getByTestId(PAGE_TEST_IDS.mappings.editor);
    expect(within(editor).getByText("测试项目")).toBeInTheDocument();
    expect(within(editor).getByTestId(PAGE_TEST_IDS.mappings.preview)).toHaveTextContent("已导入 1 条待补项");

    const targetInput = document.querySelector('input[data-draft-field="targetValue"]') as HTMLInputElement;
    expect(targetInput).toBeTruthy();
    fireEvent.change(targetInput, { target: { value: "央企" } });

    fireEvent.click(screen.getByRole("button", { name: "保存已填写规则" }));

    await waitFor(() => {
      expect(screen.getByTestId(PAGE_TEST_IDS.mappings.preview)).toHaveTextContent("待确认覆盖范围，请先处理冲突确认。");
    });
    expect(screen.getByRole("dialog")).toHaveTextContent("已存在同来源规则");

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

  it("accepts native-setter draft input updates before batch save", async () => {
    listMappings.mockResolvedValue({
      pending: [
        {
          record_id: "rec-script-1",
          project_code: "P-SCRIPT-001",
          payload: {
            项目名称: "脚本项目",
            转让方: "华润置地",
            隶属集团: "华润",
          },
        },
      ],
      entries: [],
    });
    previewMapping.mockResolvedValue({
      conflict: false,
      mode: "update",
      source_name: "华润",
      target_value: "央企",
      match_field: "group",
      target_field: "source_type",
    });
    saveMapping.mockResolvedValue({
      job_id: "job-script-1",
      affected_count: 1,
    });

    render(<MappingsPage />);

    await waitFor(() => {
      expect(listMappings).toHaveBeenCalledTimes(1);
    });

    const importButton = document.getElementById("importPendingMappingBtn") as HTMLButtonElement;
    expect(importButton).toBeTruthy();
    await act(async () => {
      importButton.click();
    });

    await waitFor(() => {
      expect(document.querySelector('input[data-draft-field="targetValue"]')).toBeTruthy();
    });
    const targetInput = document.querySelector('input[data-draft-field="targetValue"]') as HTMLInputElement;
    const setInputValue = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
    expect(setInputValue).toBeTypeOf("function");
    await act(async () => {
      setInputValue?.call(targetInput, "央企");
      targetInput.dispatchEvent(new Event("input", { bubbles: true }));
      targetInput.dispatchEvent(new Event("change", { bubbles: true }));
    });

    const saveButton = document.getElementById("saveDraftMappingsBtn") as HTMLButtonElement;
    expect(saveButton).toBeTruthy();
    await act(async () => {
      saveButton.click();
    });

    await waitFor(() => {
      expect(saveMapping).toHaveBeenCalledWith(
        expect.objectContaining({
          source_name: "华润",
          target_value: "央企",
        }),
      );
    });
  });

  it("keeps distinct batch rules separate and reports both rule count and covered pending-item count", async () => {
    listMappings.mockResolvedValue({
      pending: [
        {
          record_id: "rec-distinct-1",
          project_code: "P-DISTINCT-001",
          payload: {
            项目名称: "项目一",
            转让方: "华润置地",
            隶属集团: "华润",
          },
        },
        {
          record_id: "rec-distinct-2",
          project_code: "P-DISTINCT-002",
          payload: {
            项目名称: "项目二",
            转让方: "越秀地产",
          },
        },
      ],
      entries: [],
    });
    previewMapping.mockResolvedValue({
      conflict: false,
      mode: "update",
    });
    saveMapping
      .mockResolvedValueOnce({ job_id: "job-distinct-1", affected_count: 2 })
      .mockResolvedValueOnce({ job_id: "job-distinct-2", affected_count: 1 });

    render(<MappingsPage />);

    await waitFor(() => {
      expect(listMappings).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(screen.getByRole("button", { name: "导入全部待补项" }));

    const targetInputs = document.querySelectorAll('input[data-draft-field="targetValue"]');
    expect(targetInputs).toHaveLength(2);
    fireEvent.change(targetInputs[0] as HTMLInputElement, { target: { value: "央企" } });
    fireEvent.change(targetInputs[1] as HTMLInputElement, { target: { value: "地方国企" } });

    fireEvent.click(screen.getByRole("button", { name: "保存已填写规则" }));

    await waitFor(() => {
      expect(saveMapping).toHaveBeenCalledTimes(2);
    });
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.preview)).toHaveTextContent("已保存 2 条规则");
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.preview)).toHaveTextContent("覆盖 2 条待补项");
  });

  it("collapses identical batch rules but still reports the full covered pending-item count", async () => {
    listMappings.mockResolvedValue({
      pending: [
        {
          record_id: "rec-same-1",
          project_code: "P-SAME-001",
          payload: {
            项目名称: "项目甲",
            转让方: "华润置地",
            隶属集团: "华润",
          },
        },
        {
          record_id: "rec-same-2",
          project_code: "P-SAME-002",
          payload: {
            项目名称: "项目乙",
            转让方: "华润置地",
            隶属集团: "华润",
          },
        },
      ],
      entries: [],
    });
    previewMapping.mockResolvedValue({
      conflict: false,
      mode: "update",
    });
    saveMapping.mockResolvedValue({
      job_id: "job-same-1",
      affected_count: 4,
    });

    render(<MappingsPage />);

    await waitFor(() => {
      expect(listMappings).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(screen.getByRole("button", { name: "导入全部待补项" }));

    const targetInputs = document.querySelectorAll('input[data-draft-field="targetValue"]');
    expect(targetInputs).toHaveLength(2);
    fireEvent.change(targetInputs[0] as HTMLInputElement, { target: { value: "央企" } });
    fireEvent.change(targetInputs[1] as HTMLInputElement, { target: { value: "央企" } });

    fireEvent.click(screen.getByRole("button", { name: "保存已填写规则" }));

    await waitFor(() => {
      expect(saveMapping).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.preview)).toHaveTextContent("已保存 1 条规则");
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.preview)).toHaveTextContent("覆盖 2 条待补项");
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

  it("surfaces save failure with workbench-oriented attention copy", async () => {
    listMappings.mockResolvedValue({ pending: [], entries: [] });
    previewMapping.mockResolvedValue({
      conflict: false,
      mode: "update",
      source_name: "华润集团",
      target_value: "央企",
    });
    saveMapping.mockRejectedValue(new Error("backend down"));

    render(<MappingsPage />);

    await waitFor(() => {
      expect(listMappings).toHaveBeenCalledTimes(1);
    });

    fireEvent.change(screen.getByLabelText("来源名称"), { target: { value: "华润集团" } });
    fireEvent.change(screen.getByLabelText("目标值"), { target: { value: "央企" } });
    fireEvent.click(screen.getByRole("button", { name: "保存单条规则" }));

    expect(await screen.findByText("映射规则需人工处理，请在工作台查看任务活动。")).toBeInTheDocument();
    expect(screen.queryByText(/任务页查看明细/)).not.toBeInTheDocument();
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

    fireEvent.click(screen.getByRole("button", { name: "导入到编辑器" }));
    const targetInput = document.querySelector('input[data-draft-field="targetValue"]') as HTMLInputElement;
    fireEvent.change(targetInput, { target: { value: "央企" } });

    const batchSaveButton = screen.getByRole("button", { name: "保存已填写规则" });
    fireEvent.click(batchSaveButton);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "确认覆盖" })).toBeInTheDocument();
    });
    expect(batchSaveButton).toBeDisabled();
    expect(screen.getByTestId(PAGE_TEST_IDS.mappings.preview)).toHaveTextContent("待确认覆盖范围，请先处理冲突确认。");

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
