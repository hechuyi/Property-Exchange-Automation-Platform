export type EditableSettingsSnapshot = {
  default_exchange: string;
  default_project_type: string;
  default_concurrency: number;
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
    || current.postprocess_config !== base.postprocess_config
    || current.save_json !== base.save_json
  );
}
