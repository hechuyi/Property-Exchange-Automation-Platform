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
import { resolveLocateTarget, statusText, type RecordsRow } from "../features/records/table";

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
  const requestSequenceRef = useRef(0);

  const rows = useMemo(() => toRecordsRows(payload.rows), [payload.rows]);
  const currentPage = Number(payload.page || activeScope.page || 1);
  const pageCount = Number(payload.page_count || payload.summary?.page_count || 0);
  const hasMore = Boolean(payload.has_more);

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
      <section data-testid={PAGE_TEST_IDS.records.filters}>
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

        <label htmlFor="recordsKeywordInput">关键词</label>
        <input
          id="recordsKeywordInput"
          value={draftScope.keyword}
          onChange={(event) => {
            setDraftScope((prev) => ({ ...prev, keyword: event.target.value }));
          }}
        />

        <label htmlFor="recordsDateFromInput">开始日期</label>
        <input
          id="recordsDateFromInput"
          type="date"
          value={draftScope.dateFrom}
          onChange={(event) => {
            setDraftScope((prev) => ({ ...prev, dateFrom: event.target.value }));
          }}
        />

        <label htmlFor="recordsDateToInput">结束日期</label>
        <input
          id="recordsDateToInput"
          type="date"
          value={draftScope.dateTo}
          onChange={(event) => {
            setDraftScope((prev) => ({ ...prev, dateTo: event.target.value }));
          }}
        />

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

        <button type="button" onClick={applyFilters}>查询</button>
        <button type="button" disabled={exportState.kind === "loading"} onClick={() => { void exportRecords(); }}>
          导出 Excel
        </button>
      </section>

      <section data-testid={PAGE_TEST_IDS.records.summary}>
        <p>{formatRecordsSummary(payload) || "暂无记录摘要"}</p>
        {exportState.kind !== "idle" && exportState.kind !== "failed" ? <p role="status">{exportStateText}</p> : null}
        {exportState.kind === "failed" ? <p role="alert">{exportStateText}</p> : null}
      </section>

      {loadError ? <p role="alert">记录加载失败：{loadError}</p> : null}

      <section data-testid={PAGE_TEST_IDS.records.table}>
        {loading ? <p>加载中…</p> : null}
        {hasRows ? (
          <table>
            <thead>
              <tr>
                <th>录入状态</th>
                <th>项目编号</th>
                <th>项目名称</th>
                <th>挂牌日期</th>
                <th>最近更新</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((record) => {
                const key = String(record.record_id || `${record.project_code || ""}-${record.updated_at || ""}`);
                const archivePath = String(record.archive_path || "").trim();
                const locateTarget = resolveLocateTarget(record);
                return (
                  <tr key={key}>
                    <td>
                      {statusText(record)}
                      {record.status_detail ? <div>{record.status_detail}</div> : null}
                    </td>
                    <td>{record.project_code || ""}</td>
                    <td>{record.project_name || ""}</td>
                    <td>{record.listing_date || ""}</td>
                    <td>{record.updated_at || ""}</td>
                    <td>
                      <button
                        type="button"
                        disabled={!archivePath}
                        onClick={() => {
                          void window.peapDesktop?.openPath?.(archivePath);
                        }}
                      >
                        打开归档
                      </button>
                      <button
                        type="button"
                        disabled={!locateTarget}
                        onClick={() => {
                          void window.peapDesktop?.showItemInFolder?.(locateTarget);
                        }}
                      >
                        定位文件
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          isEmpty ? <p>当前筛选条件下没有记录</p> : null
        )}

        <button type="button" onClick={() => changePage(currentPage - 1)} disabled={loading || currentPage <= 1}>
          上一页
        </button>
        <span>
          第 {currentPage} 页{pageCount > 0 ? ` / ${pageCount} 页` : ""}
        </span>
        <button type="button" onClick={() => changePage(currentPage + 1)} disabled={loading || !hasMore}>
          下一页
        </button>
      </section>
    </div>
  );
}
