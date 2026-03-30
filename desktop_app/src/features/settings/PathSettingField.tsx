import { useId } from "react";

type PathSettingFieldProps = {
  id?: string;
  label: string;
  value: string;
  pickerLabel?: string;
  onPick?: () => void | Promise<void>;
  onReveal?: () => void | Promise<void>;
  disabled?: boolean;
  description?: string;
};

export function PathSettingField({
  id,
  label,
  value,
  pickerLabel = "选择…",
  onPick,
  onReveal,
  disabled = false,
  description = "",
}: PathSettingFieldProps) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  const revealDisabled = disabled || !String(value || "").trim();

  return (
    <div style={{ display: "grid", gap: 8, marginBottom: 12 }}>
      <label htmlFor={fieldId}>{label}</label>
      <input id={fieldId} readOnly value={value} />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {onPick ? (
          <button type="button" aria-label={`${label} ${pickerLabel}`} onClick={() => void onPick()} disabled={disabled}>
            {pickerLabel}
          </button>
        ) : null}
        {onReveal ? (
          <button
            type="button"
            aria-label={`${label} 在系统中显示`}
            onClick={() => void onReveal()}
            disabled={revealDisabled}
          >
            在系统中显示
          </button>
        ) : null}
      </div>
      {description ? <p style={{ margin: 0 }}>{description}</p> : null}
    </div>
  );
}
