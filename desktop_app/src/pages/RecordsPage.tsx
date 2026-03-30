import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useDesktopRuntime } from "../desktop/provider";
import { PAGE_TEST_IDS } from "../testing/selectors";
import {
  createDefaultScope,
  setSharedRecordsScope,
  PAGE_SIZE_OPTIONS,
  PROJECT_TYPE_OPTIONS,
  RECORD_STATE_OPTIONS,
  useSharedRecordsScope,
  withFirstPage,
  type RecordsScope,
} from "../features/records/scope";
import {
  createExportFailureState,
  describeExportState,
  resolveExportTerminalState,
  type ExportViewState,
} from "../features/records/exportState";
import { formatRecordsSummary } from "../features/records/summary";
import { RecordDetailPanel } from "../features/records/RecordDetailPanel";
import { RecordStatusTag } from "../features/records/RecordStatusTag";
import { resolveLocateTarget, resolveOpenFileTarget, type RecordsRow } from "../features/records/table";

type RecordsPayload = {
  page?: number;
  page_count?: number;
  has_more?: boolean;
  summary?: {
    total_count?: number;
    visible_count?: number;
    filtered_state_counts?: Record<string, unknown>;
    state_counts?: Record<string, unknown>;
    page?: number;
    page_count?: number;
  };
  rows?: RecordsRow[];
};

function toRecordsRows(value: unknown): RecordsRow[] {
  return Array.isArray(value) ? (value as RecordsRow[]) : [];
}

function recordKey(record: RecordsRow) {
  return String(record.record_id || `${record.project_code || ""}-${record.updated_at || ""}`);
}

type RecordsLoadState =
  | { kind: "idle" }
  | { kind: "loading"; requestId: number }
  | { kind: "loaded" }
  | { kind: "empty" }
  | { kind: "failed"; message: string };

export default function RecordsPage() {
  const { commands } = useDesktopRuntime();
  const activeScope = useSharedRecordsScope();
  const [draftScope, setDraftScope] = useState<RecordsScope>(() => activeScope || createDefaultScope());
  const [payload, setPayload] = useState<RecordsPayload>({ rows: [] });
  const [loadState, setLoadState] = useState<RecordsLoadState>({ kind: "idle" });
  const [exportState, setExportState] = useState<ExportViewState>({ kind: "idle" });
  const [selectedRecordId, setSelectedRecordId] = useState("");
  const requestSequenceRef = useRef(0);

  const rows = useMemo(() => toRecordsRows(payload.rows), [payload.rows]);
  const currentPage = Number(payload.page || activeScope.page || 1);
  const pageCount = Number(payload.page_count || payload.summary?.page_count || 0);
  const hasMore = Boolean(payload.has_more);
  const selectedRow = useMemo(
    () => rows.find((record) => recordKey(record) === selectedRecordId) || null,
    [rows, selectedRecordId],
  );

  const loadRecords = useCallback(async (scope: RecordsScope) => {
    const requestId = requestSequenceRef.current + 1;
    requestSequenceRef.current = requestId;
    setLoadState({ kind: "loading", requestId });
    try {
      const response = (await commands.listRecords(scope)) as RecordsPayload;
      if (requestId !== requestSequenceRef.current) {
        return;
      }
      const nextPayload = {
        ...response,
        rows: toRecordsRows(response?.rows),
      };
      setPayload(nextPayload);
      setLoadState(nextPayload.rows?.length ? { kind: "loaded" } : { kind: "empty" });
    } catch (loadError) {
      if (requestId !== requestSequenceRef.current) {
        return;
      }
      const message = String((loadError as Error)?.message || loadError || "记录查询失败");
      setLoadState({ kind: "failed", message });
      setPayload({ rows: [] });
    }
  }, [commands]);

  useEffect(() => {
    void loadRecords(activeScope);
  }, [activeScope, loadRecords]);

  useEffect(() => {
    setDraftScope(activeScope);
  }, [activeScope]);

  useEffect(() => {
    if (!rows.length) {
      setSelectedRecordId("");
      return;
    }
    setSelectedRecordId((current) => {
      if (current && rows.some((record) => recordKey(record) === current)) {
        return current;
      }
      return recordKey(rows[0]);
    });
  }, [rows]);

  const applyFilters = () => {
    const nextScope = withFirstPage(draftScope);
    setDraftScope(nextScope);
    setSharedRecordsScope(nextScope);
  };

  const changePage = (nextPage: number) => {
    if (nextPage < 1) {
      return;
    }
    const nextScope = { ...activeScope, page: nextPage };
    setDraftScope((prev) => ({ ...prev, page: nextPage }));
    setSharedRecordsScope(nextScope);
  };

  const exportRecords = useCallback(async () => {
    setExportState({ kind: "loading" });
    try {
      const response = await commands.runExport({
        scope: activeScope,
        mode: "rebuild",
      });
      setExportState(resolveExportTerminalState(response));
    } catch (commandError) {
      setExportState(createExportFailureState((commandError as Error)?.message || String(commandError || "记录导出失败")));
    }
  }, [activeScope, commands]);

  const exportStateText = describeExportState(exportState);
  const loading = loadState.kind === "loading";
  const loadError = loadState.kind === "failed" ? loadState.message : "";
  const hasRows = loadState.kind === "loaded" && rows.length > 0;
  const isEmpty = loadState.kind === "empty";

  return (
    <div data-testid={PAGE_TEST_IDS.records.page}>
      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "minmax(0, 2fr) minmax(320px, 1fr)", alignItems: "start" }}>
        <div style={{ display: "grid", gap: 16 }}>
          <section
            data-testid={PAGE_TEST_IDS.records.filters}
            style={{
              display: "grid",
              gap: 12,
              gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
              alignItems: "end",
              padding: 16,
              border: "1px solid #d1d5db",
              borderRadius: 12,
              background: "#ffffff",
            }}
          >
            <div style={{ display: "grid", gap: 4 }}>
              <label htmlFor="recordsStateFilter">状态</label>
              <select
                id="recordsStateFilter"
                value={draftScope.state}
                onChange={(event) => {
                  setDraftScope((prev) => ({ ...prev, state: event.target.value }));
                }}
              >
                {RECORD_STATE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>

            <div style={{ display: "grid", gap: 4 }}>
              <label htmlFor="recordsProjectTypeFilter">项目类型</label>
              <select
                id="recordsProjectTypeFilter"
                value={draftScope.projectType}
                onChange={(event) => {
                  setDraftScope((prev) => ({ ...prev, projectType: event.target.value }));
                }}
              >
                {PROJECT_TYPE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>

            <div style={{ display: "grid", gap: 4 }}>
              <label htmlFor="recordsKeywordInput">关键词</label>
              <input
                id="recordsKeywordInput"
                value={draftScope.keyword}
                onChange={(event) => {
                  setDraftScope((prev) => ({ ...prev, keyword: event.target.value }));
                }}
              />
            </div>

            <div style={{ display: "grid", gap: 4 }}>
              <label htmlFor="recordsDateFromInput">开始日期</label>
              <input
                id="recordsDateFromInput"
                type="date"
                value={draftScope.dateFrom}
                onChange={(event) => {
                  setDraftScope((prev) => ({ ...prev, dateFrom: event.target.value }));
                }}
              />
            </div>

            <div style={{ display: "grid", gap: 4 }}>
              <label htmlFor="recordsDateToInput">结束日期</label>
              <input
                id="recordsDateToInput"
                type="date"
                value={draftScope.dateTo}
                onChange={(event) => {
                  setDraftScope((prev) => ({ ...prev, dateTo: event.target.value }));
                }}
              />
            </div>

            <div style={{ display: "grid", gap: 4 }}>
              <label htmlFor="recordsPageSizeSelect">每页</label>
              <select
                id="recordsPageSizeSelect"
                aria-label="每页"
                value={String(draftScope.pageSize)}
                onChange={(event) => {
                  setDraftScope((prev) => withFirstPage({ ...prev, pageSize: Number(event.target.value || prev.pageSize) }));
                }}
              >
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>{size}</option>
                ))}
              </select>
            </div>

            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button type="button" onClick={applyFilters}>查询</button>
              <button type="button" disabled={exportState.kind === "loading"} onClick={() => { void exportRecords(); }}>
                导出 Excel
              </button>
            </div>
          </section>

          <section
            data-testid={PAGE_TEST_IDS.records.summary}
            style={{ padding: 16, border: "1px solid #d1d5db", borderRadius: 12, background: "#ffffff" }}
          >
            <p style={{ marginTop: 0 }}>{formatRecordsSummary(payload) || "暂无记录摘要"}</p>
            {exportState.kind !== "idle" && exportState.kind !== "failed" ? <p role="status">{exportStateText}</p> : null}
            {exportState.kind === "failed" ? <p role="alert">{exportStateText}</p> : null}
          </section>

          {loadError ? <p role="alert">记录加载失败：{loadError}</p> : null}

          <section
            data-testid={PAGE_TEST_IDS.records.table}
            style={{ padding: 16, border: "1px solid #d1d5db", borderRadius: 12, background: "#ffffff", display: "grid", gap: 12 }}
          >
            {loading ? <p>加载中…</p> : null}
            {hasRows ? (
              <table>
                <thead>
                  <tr>
                    <th>当前状态</th>
                    <th>项目编号</th>
                    <th>项目名称</th>
                    <th>挂牌日期</th>
                    <th>最近更新</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((record) => {
                    const key = recordKey(record);
                    const selected = key === selectedRecordId;
                    return (
                      <tr
                        key={key}
                        onClick={() => {
                          setSelectedRecordId(key);
                        }}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            setSelectedRecordId(key);
                          }
                        }}
                        aria-selected={selected}
                        tabIndex={0}
                        style={{
                          cursor: "pointer",
                          background: selected ? "#eef6ff" : "transparent",
                          outline: "none",
                        }}
                      >
                        <td><RecordStatusTag row={record} /></td>
                        <td>{record.project_code || ""}</td>
                        <td>{record.project_name || ""}</td>
                        <td>{record.listing_date || ""}</td>
                        <td>{record.updated_at || ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              isEmpty ? <p>当前筛选条件下没有记录</p> : null
            )}

            <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
              <button type="button" onClick={() => changePage(currentPage - 1)} disabled={loading || currentPage <= 1}>
                上一页
              </button>
              <span>
                第 {currentPage} 页{pageCount > 0 ? ` / ${pageCount} 页` : ""}
              </span>
              <button type="button" onClick={() => changePage(currentPage + 1)} disabled={loading || !hasMore}>
                下一页
              </button>
            </div>
          </section>
        </div>

        <RecordDetailPanel
          row={selectedRow}
          onOpenFile={() => {
            const target = selectedRow ? resolveOpenFileTarget(selectedRow) : "";
            if (!target) {
              return;
            }
            return window.peapDesktop?.openPath?.(target);
          }}
          onRevealInFolder={() => {
            const target = selectedRow ? resolveLocateTarget(selectedRow) : "";
            if (!target) {
              return;
            }
            return window.peapDesktop?.showItemInFolder?.(target);
          }}
        />
      </div>
    </div>
  );
}
