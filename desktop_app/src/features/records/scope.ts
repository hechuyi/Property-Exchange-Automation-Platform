import { useSyncExternalStore } from "react";
import { DEFAULT_RECORD_SCOPE, type RecordScope } from "../../desktop/contracts";

export type RecordsScope = Required<RecordScope>;

export const RECORD_STATE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "all", label: "全部状态" },
  { value: "ready", label: "已就绪" },
  { value: "pending_mapping", label: "待补映射" },
  { value: "skipped", label: "已跳过" },
  { value: "parse_failed", label: "需人工处理（内容解析）" },
  { value: "postprocess_failed", label: "需人工处理（后处理）" },
  { value: "conflict", label: "需人工处理（归档重名）" },
];

export const PROJECT_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "all", label: "全部类型" },
  { value: "equity_transfer", label: "股权转让" },
  { value: "physical_asset", label: "实物资产" },
  { value: "capital_increase", label: "增资扩股" },
  { value: "pre_disclosure", label: "预披露" },
];

export const PAGE_SIZE_OPTIONS = [20, 50, 100, 200] as const;

export function createDefaultScope(): RecordsScope {
  return {
    recordFamily: DEFAULT_RECORD_SCOPE.recordFamily,
    state: DEFAULT_RECORD_SCOPE.state,
    projectType: DEFAULT_RECORD_SCOPE.projectType,
    keyword: DEFAULT_RECORD_SCOPE.keyword,
    dateFrom: DEFAULT_RECORD_SCOPE.dateFrom,
    dateTo: DEFAULT_RECORD_SCOPE.dateTo,
    page: DEFAULT_RECORD_SCOPE.page,
    pageSize: DEFAULT_RECORD_SCOPE.pageSize,
  };
}

export function withFirstPage(scope: RecordsScope): RecordsScope {
  return {
    ...scope,
    page: 1,
  };
}

function cloneScope(scope: RecordsScope): RecordsScope {
  return {
    ...scope,
  };
}

function freezeScope(scope: RecordsScope): RecordsScope {
  return Object.freeze(cloneScope(scope)) as RecordsScope;
}

let sharedRecordsScope: RecordsScope = freezeScope(createDefaultScope());
const sharedScopeListeners = new Set<() => void>();

function getSharedRecordsScopeSnapshot(): RecordsScope {
  return sharedRecordsScope;
}

export function getSharedRecordsScope(): RecordsScope {
  return cloneScope(sharedRecordsScope);
}

export function setSharedRecordsScope(scope: RecordsScope): void {
  sharedRecordsScope = freezeScope(scope);
  sharedScopeListeners.forEach((listener) => {
    listener();
  });
}

export function subscribeSharedRecordsScope(listener: () => void): () => void {
  sharedScopeListeners.add(listener);
  return () => {
    sharedScopeListeners.delete(listener);
  };
}

export function useSharedRecordsScope(): RecordsScope {
  return useSyncExternalStore(subscribeSharedRecordsScope, getSharedRecordsScopeSnapshot, getSharedRecordsScopeSnapshot);
}
