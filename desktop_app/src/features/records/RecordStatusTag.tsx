import { resolveRecordStatus, type RecordsRow } from "./table";

type RecordStatusTagProps = {
  row: RecordsRow;
};

const TAG_STYLES = {
  ready: { background: "#e8f6e8", color: "#1f6b24", border: "1px solid #b7e0b9" },
  blocked: { background: "#fff4d6", color: "#8a5a00", border: "1px solid #f0cf7b" },
  attention: { background: "#fdecea", color: "#9f2d20", border: "1px solid #f5b8af" },
  muted: { background: "#f3f4f6", color: "#4b5563", border: "1px solid #d1d5db" },
} as const;

export function RecordStatusTag({ row }: RecordStatusTagProps) {
  const status = resolveRecordStatus(row);
  const style = TAG_STYLES[status.tone];

  return (
    <span
      title={status.detail}
      style={{
        display: "inline-flex",
        alignItems: "center",
        borderRadius: 999,
        padding: "2px 10px",
        fontSize: 12,
        fontWeight: 600,
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      {status.label}
    </span>
  );
}
