import { describe, expect, it } from "vitest";
import { buildEditableSettingsSnapshot, editableSettingsChanged } from "./form";

describe("settings form helpers", () => {
  it("falls back default_concurrency to 1 for non-numeric input", () => {
    const snapshot = buildEditableSettingsSnapshot({
      default_exchange: " all ",
      default_project_type: " listing ",
      default_concurrency: "invalid-number",
      workspace_root: " /tmp/workspace ",
      archive_root: " /tmp/archive ",
      export_root: " /tmp/export ",
      postprocess_config: " /tmp/postprocess.yaml ",
      save_json: 1,
    });

    expect(snapshot.default_exchange).toBe("all");
    expect(snapshot.default_project_type).toBe("listing");
    expect(snapshot.default_concurrency).toBe(1);
    expect(snapshot.workspace_root).toBe("/tmp/workspace");
    expect(snapshot.archive_root).toBe("/tmp/archive");
    expect(snapshot.export_root).toBe("/tmp/export");
    expect(snapshot.postprocess_config).toBe("/tmp/postprocess.yaml");
    expect(snapshot.save_json).toBe(true);
  });

  it("detects form change against saved snapshot", () => {
    const base = {
      default_exchange: "all",
      default_project_type: "all",
      default_concurrency: 1,
      workspace_root: "/tmp/workspace",
      archive_root: "/tmp/archive",
      export_root: "/tmp/export",
      postprocess_config: "",
      save_json: false,
    };

    expect(editableSettingsChanged(base, base)).toBe(false);
    expect(editableSettingsChanged({ ...base, default_concurrency: 2 }, base)).toBe(true);
    expect(editableSettingsChanged({ ...base, archive_root: "/tmp/archive-next" }, base)).toBe(true);
  });
});
