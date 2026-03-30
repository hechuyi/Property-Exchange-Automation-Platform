import { fireEvent, render, screen } from "@testing-library/react";
import { RecordDetailPanel } from "./RecordDetailPanel";

describe("RecordDetailPanel", () => {
  it("renders decision-oriented status and platform-native file actions", () => {
    const onOpenFile = vi.fn();
    const onRevealInFolder = vi.fn();

    render(
      <RecordDetailPanel
        row={{
          record_id: "rec-1",
          state: "parse_failed",
          status_label: "解析失败",
          status_detail: "工作表缺少“项目编号”列",
          project_code: "P-001",
          project_name: "测试项目",
          project_type: "physical_asset",
          exchange: "北交所",
          listing_date: "2026-03-01",
          updated_at: "2026-03-02 09:00:00",
          archive_path: "/tmp/archive/P-001.xlsx",
          source_file: "/tmp/source/P-001.xlsx",
        }}
        onOpenFile={onOpenFile}
        onRevealInFolder={onRevealInFolder}
      />,
    );

    expect(screen.getByRole("heading", { name: "记录详情" })).toBeInTheDocument();
    expect(screen.getByText("需人工处理")).toBeInTheDocument();
    expect(screen.getByText("实物资产")).toBeInTheDocument();
    expect(screen.getByText("工作表缺少“项目编号”列")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "打开文件" }));
    fireEvent.click(screen.getByRole("button", { name: "在文件夹中显示" }));

    expect(onOpenFile).toHaveBeenCalledTimes(1);
    expect(onRevealInFolder).toHaveBeenCalledTimes(1);
  });

  it("renders an empty hint when no row is selected", () => {
    render(<RecordDetailPanel row={null} onOpenFile={vi.fn()} onRevealInFolder={vi.fn()} />);

    expect(screen.getByText("选择一条记录后，可在这里查看详情和文件操作。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开文件" })).not.toBeInTheDocument();
  });
});
