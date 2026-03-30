import { fireEvent, render, screen, within } from "@testing-library/react";
import { PAGE_TEST_IDS } from "../../testing/selectors";
import { RuleEditorPane } from "./RuleEditorPane";

describe("RuleEditorPane", () => {
  it("keeps active drafts and preview feedback in the same remediation pane", () => {
    const onSingleDraftChange = vi.fn();
    const onUpdateDraft = vi.fn();
    const onSaveSingle = vi.fn();
    const onSaveDraftMappings = vi.fn();
    const onStartNewSingleDraft = vi.fn();

    render(
      <RuleEditorPane
        editingExistingEntry={false}
        singleDraft={{
          ruleKind: "transferor_group",
          sourceName: "",
          targetValue: "",
          notes: "",
        }}
        singleSourceLabel="转让方名称"
        singleTargetLabel="集团名称"
        ruleOptions={[{ value: "transferor_group", label: "转让方 -> 集团" }]}
        singleSaving={false}
        singleSaveDisabled={false}
        onSingleDraftChange={onSingleDraftChange}
        onSaveSingle={onSaveSingle}
        onStartNewSingleDraft={onStartNewSingleDraft}
        drafts={[
          {
            recordId: "rec-1",
            project_code: "P-001",
            project_name: "测试项目",
            company_name: "华润置地",
            group_name: "",
            rawRecord: {},
            ruleKind: "transferor_group",
            previousRuleKind: "transferor_group",
            sourceName: "华润置地",
            targetValue: "央企",
            notes: "P-001",
          },
        ]}
        onUpdateDraft={onUpdateDraft}
        batchSaving={false}
        batchWaitingConflict={false}
        saveDraftDisabled={false}
        onSaveDraftMappings={onSaveDraftMappings}
        previewText="已导入 1 条待补项，请直接在列表里填写"
        previewError=""
      />
    );

    const editor = screen.getByTestId(PAGE_TEST_IDS.mappings.editor);
    expect(within(editor).getByText("测试项目")).toBeInTheDocument();
    expect(within(editor).getByTestId(PAGE_TEST_IDS.mappings.preview)).toHaveTextContent("已导入 1 条待补项");

    fireEvent.change(screen.getByLabelText("来源名称"), { target: { value: "华润置地" } });
    fireEvent.click(screen.getByRole("button", { name: "保存单条规则" }));
    fireEvent.click(screen.getByRole("button", { name: "保存已填写规则" }));

    expect(onSingleDraftChange).toHaveBeenCalledWith("sourceName", "华润置地");
    expect(onSaveSingle).toHaveBeenCalledTimes(1);
    expect(onSaveDraftMappings).toHaveBeenCalledTimes(1);
  });
});
