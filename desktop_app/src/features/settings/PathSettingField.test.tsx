import { fireEvent, render, screen } from "@testing-library/react";
import { PathSettingField } from "./PathSettingField";

describe("PathSettingField", () => {
  it("renders picker-first controls for editable directory rows", () => {
    const onPick = vi.fn();
    const onReveal = vi.fn();

    render(
      <PathSettingField
        label="工作目录"
        value="/tmp/workspace"
        onPick={onPick}
        onReveal={onReveal}
      />,
    );

    expect(screen.getByLabelText("工作目录")).toHaveAttribute("readonly");
    fireEvent.click(screen.getByRole("button", { name: "工作目录 选择…" }));
    fireEvent.click(screen.getByRole("button", { name: "工作目录 在系统中显示" }));

    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onReveal).toHaveBeenCalledTimes(1);
  });

  it("supports file picking for postprocess config", () => {
    const onPick = vi.fn();

    render(
      <PathSettingField
        label="后处理配置"
        value="/tmp/postprocess.json"
        pickerLabel="选择文件…"
        onPick={onPick}
      />,
    );

    expect(screen.getByRole("button", { name: "后处理配置 选择文件…" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "后处理配置 选择文件…" }));
    expect(onPick).toHaveBeenCalledTimes(1);
  });
});
