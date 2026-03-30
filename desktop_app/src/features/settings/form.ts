export type EditableSettingsSnapshot = {
  default_exchange: string;
  default_project_type: string;
  default_concurrency: number;
  workspace_root: string;
  archive_root: string;
  export_root: string;
  postprocess_config: string;
  save_json: boolean;
};

export function buildEditableSettingsSnapshot(state: Record<string, any>): EditableSettingsSnapshot {
  const parsedConcurrency = Number(state.default_concurrency);
  const defaultConcurrency = Number.isFinite(parsedConcurrency) && parsedConcurrency > 0
    ? Math.max(1, Math.trunc(parsedConcurrency))
    : 1;
  return {
    default_exchange: String(state.default_exchange || "").trim(),
    default_project_type: String(state.default_project_type || "").trim(),
    default_concurrency: defaultConcurrency,
    workspace_root: String(state.workspace_root || "").trim(),
    archive_root: String(state.archive_root || "").trim(),
    export_root: String(state.export_root || "").trim(),
    postprocess_config: String(state.postprocess_config || "").trim(),
    save_json: Boolean(state.save_json),
  };
}

export function editableSettingsChanged(
  current: EditableSettingsSnapshot,
  base: EditableSettingsSnapshot | null,
) {
  if (!base) {
    return false;
  }
  return (
    current.default_exchange !== base.default_exchange
    || current.default_project_type !== base.default_project_type
    || current.default_concurrency !== base.default_concurrency
    || current.workspace_root !== base.workspace_root
    || current.archive_root !== base.archive_root
    || current.export_root !== base.export_root
    || current.postprocess_config !== base.postprocess_config
    || current.save_json !== base.save_json
  );
}
