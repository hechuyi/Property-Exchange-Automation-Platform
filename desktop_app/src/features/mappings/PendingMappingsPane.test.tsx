import { fireEvent, render, screen } from "@testing-library/react";
import { PendingMappingsPane } from "./PendingMappingsPane";

describe("PendingMappingsPane", () => {
  it("renders pending queue items and exposes import actions", () => {
    const onImportAll = vi.fn();
    const onImportItem = vi.fn();
    const onTriggerReprocess = vi.fn();

    render(
      <PendingMappingsPane
        pendingPayload={{ pending: [], total_count: 2 }}
        pending={[
          {
            record_id: "rec-1",
            project_code: "P-001",
            payload: {
              项目名称: "测试项目",
              转让方: "华润置地",
            },
          },
        ]}
        disabled={false}
        onImportAll={onImportAll}
        onImportItem={onImportItem}
        onTriggerReprocess={onTriggerReprocess}
      />,
    );

    expect(screen.getByText(/当前 1 条待补项/)).toBeInTheDocument();
    expect(screen.getByText("测试项目")).toBeInTheDocument();
    expect(screen.getByText("P-001")).toBeInTheDocument();
    expect(screen.getByText("公司：华润置地")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "导入全部待补项" }));
    fireEvent.click(screen.getByRole("button", { name: "导入到编辑器" }));
    fireEvent.click(screen.getByRole("button", { name: "一键重处理当前所有待补项" }));

    expect(onImportAll).toHaveBeenCalledTimes(1);
    expect(onImportItem).toHaveBeenCalledWith(
      expect.objectContaining({
        record_id: "rec-1",
      }),
    );
    expect(onTriggerReprocess).toHaveBeenCalledTimes(1);
  });
});
