import { RecordStatusTag } from "./RecordStatusTag";
import {
  projectTypeText,
  resolveLocateTarget,
  resolveOpenFileTarget,
  resolveRecordStatus,
  type RecordsRow,
} from "./table";

type RecordDetailPanelProps = {
  row: RecordsRow | null;
  onOpenFile: () => void | Promise<void>;
  onRevealInFolder: () => void | Promise<void>;
};

function DetailRow({ label, value }: { label: string; value: string }) {
  if (!value) {
    return null;
  }

  return (
    <div style={{ display: "grid", gap: 4 }}>
      <strong style={{ fontSize: 12, color: "#4b5563" }}>{label}</strong>
      <span>{value}</span>
    </div>
  );
}

export function RecordDetailPanel({ row, onOpenFile, onRevealInFolder }: RecordDetailPanelProps) {
  if (!row) {
    return (
      <aside
        style={{
          border: "1px solid #d1d5db",
          borderRadius: 12,
          padding: 16,
          background: "#f8fafc",
          display: "grid",
          gap: 12,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 18 }}>记录详情</h2>
        <p style={{ margin: 0 }}>选择一条记录后，可在这里查看详情和文件操作。</p>
      </aside>
    );
  }

  const status = resolveRecordStatus(row);
  const openFileTarget = resolveOpenFileTarget(row);
  const locateTarget = resolveLocateTarget(row);

  return (
    <aside
      style={{
        border: "1px solid #d1d5db",
        borderRadius: 12,
        padding: 16,
        background: "#ffffff",
        display: "grid",
        gap: 16,
        alignContent: "start",
      }}
    >
      <div style={{ display: "grid", gap: 8 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>记录详情</h2>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start", flexWrap: "wrap" }}>
          <div style={{ display: "grid", gap: 4 }}>
            <strong style={{ fontSize: 18 }}>{row.project_name || row.project_code || "未命名记录"}</strong>
            <span style={{ color: "#4b5563" }}>{row.project_code || "未提供项目编号"}</span>
          </div>
          <RecordStatusTag row={row} />
        </div>
      </div>

      <div style={{ display: "grid", gap: 12 }}>
        <DetailRow label="项目类型" value={projectTypeText(row.project_type)} />
        <DetailRow label="交易场所" value={String(row.exchange || "").trim()} />
        <DetailRow label="挂牌日期" value={String(row.listing_date || "").trim()} />
        <DetailRow label="最近更新" value={String(row.updated_at || "").trim()} />
        <DetailRow label="处理说明" value={status.detail} />
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button type="button" disabled={!openFileTarget} onClick={() => void onOpenFile()}>
          打开文件
        </button>
        <button type="button" disabled={!locateTarget} onClick={() => void onRevealInFolder()}>
          在文件夹中显示
        </button>
      </div>
    </aside>
  );
}
