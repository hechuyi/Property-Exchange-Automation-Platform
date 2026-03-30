import { useCallback, useEffect, useMemo, useState } from "react";
import { useDesktopRuntime } from "../desktop/provider";
import { PAGE_TEST_IDS } from "../testing/selectors";
import { checkRuntimeDependencies, installRuntimeBrowser, loadSettingsSnapshot, saveSettingsSnapshot } from "../features/settings/api";
import {
  buildEditableSettingsSnapshot,
  editableSettingsChanged,
  type EditableSettingsSnapshot,
} from "../features/settings/form";
import { summarizeSettingsRuntimeState } from "../features/settings/runtime";

type SettingsState = {
  default_exchange: string;
  default_project_type: string;
  default_concurrency: number;
  app_home: string;
  workspace_root: string;
  archive_root: string;
  export_root: string;
  postprocess_config: string;
  save_json: boolean;
  streaming_db: string;
  log_dir: string;
  cache_dir: string;
  raw_auto_root: string;
  raw_manual_root: string;
  browser_cache_dir: string;
};

const DEFAULT_STATE: SettingsState = {
  default_exchange: "all",
  default_project_type: "all",
  default_concurrency: 1,
  app_home: "",
  workspace_root: "",
  archive_root: "",
  export_root: "",
  postprocess_config: "",
  save_json: false,
  streaming_db: "",
  log_dir: "",
  cache_dir: "",
  raw_auto_root: "",
  raw_manual_root: "",
  browser_cache_dir: "",
};

export default function SettingsPage() {
  const { config } = useDesktopRuntime();
  const [formState, setFormState] = useState<SettingsState>(DEFAULT_STATE);
  const [runtime, setRuntime] = useState<Record<string, any>>({});
  const [savedEditableSnapshot, setSavedEditableSnapshot] = useState<EditableSettingsSnapshot | null>(null);
  const [saving, setSaving] = useState(false);
  const [checkingRuntime, setCheckingRuntime] = useState(false);
  const [installingRuntime, setInstallingRuntime] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [statusError, setStatusError] = useState("");
  const actionInFlight = saving || checkingRuntime || installingRuntime;

  const runtimeSummary = useMemo(() => summarizeSettingsRuntimeState(runtime), [runtime]);
  const editableSnapshot = useMemo(() => buildEditableSettingsSnapshot(formState), [formState]);
  const isFormDirty = useMemo(
    () => editableSettingsChanged(editableSnapshot, savedEditableSnapshot),
    [editableSnapshot, savedEditableSnapshot],
  );
  const installActionDisabled = actionInFlight || runtimeSummary.installStatus === "running";

  const setInfoStatus = useCallback((message: string) => {
    setStatusText(message);
    setStatusError("");
  }, []);

  const setErrorStatus = useCallback((message: string) => {
    setStatusError(message);
    setStatusText("");
  }, []);

  const loadSnapshot = useCallback(async () => {
    try {
      const { basic, advanced, runtime: nextRuntime } = await loadSettingsSnapshot(config);
      const parsedDefaultConcurrency = Number(basic.default_concurrency);
      const normalizedDefaultConcurrency = Number.isFinite(parsedDefaultConcurrency) && parsedDefaultConcurrency > 0
        ? Math.max(1, Math.trunc(parsedDefaultConcurrency))
        : 1;
      const nextFormState = {
        default_exchange: basic.default_exchange || "all",
        default_project_type: basic.default_project_type || "all",
        default_concurrency: normalizedDefaultConcurrency,
        app_home: advanced.app_home || basic.app_home || "",
        workspace_root: basic.workspace_root || basic.app_home || "",
        archive_root: basic.archive_root || "",
        export_root: basic.export_root || "",
        postprocess_config: advanced.postprocess_config || "",
        save_json: Boolean(advanced.save_json),
        streaming_db: advanced.streaming_db || "",
        log_dir: advanced.log_dir || "",
        cache_dir: advanced.cache_dir || "",
        raw_auto_root: advanced.raw_auto_root || "",
        raw_manual_root: advanced.raw_manual_root || "",
        browser_cache_dir: advanced.browser_cache_dir || "",
      };
      setFormState(nextFormState);
      setSavedEditableSnapshot(buildEditableSettingsSnapshot(nextFormState));
      setRuntime(nextRuntime || {});
    } catch (error) {
      setErrorStatus(String((error as Error)?.message || error || "加载设置失败，请稍后重试。"));
    }
  }, [config, setErrorStatus]);

  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  const onSave = async () => {
    if (!isFormDirty || actionInFlight) {
      return;
    }
    setSaving(true);
    setInfoStatus("正在保存设置...");
    try {
      await saveSettingsSnapshot(config, {
        basic: {
          default_exchange: editableSnapshot.default_exchange,
          default_project_type: editableSnapshot.default_project_type,
          default_concurrency: editableSnapshot.default_concurrency,
        },
        advanced: {
          postprocess_config: editableSnapshot.postprocess_config,
          save_json: editableSnapshot.save_json,
        },
      });
      await loadSnapshot();
      setInfoStatus("设置已保存");
    } catch (error) {
      setErrorStatus(String((error as Error)?.message || error || "保存设置失败，请稍后重试。"));
    } finally {
      setSaving(false);
    }
  };

  const runRuntimeCheck = useCallback(async () => {
    if (actionInFlight) {
      return;
    }
    setCheckingRuntime(true);
    setInfoStatus("正在检测浏览器运行环境...");
    try {
      const payload = await checkRuntimeDependencies(config);
      setRuntime(payload || {});
      setInfoStatus(payload?.browser?.installed ? "浏览器已就绪" : "浏览器未安装");
    } catch (error) {
      setErrorStatus(String((error as Error)?.message || error || "运行环境检测失败，请稍后重试。"));
    } finally {
      setCheckingRuntime(false);
    }
  }, [actionInFlight, config, setErrorStatus, setInfoStatus]);

  const runRuntimeInstall = async () => {
    if (actionInFlight) {
      return;
    }
    setInstallingRuntime(true);
    setInfoStatus("正在安装浏览器，首次下载可能较慢...");
    try {
      const payload = await installRuntimeBrowser(config);
      setRuntime((current) => ({ ...current, browser_install: payload || {} }));
      const runtimePayload = await checkRuntimeDependencies(config);
      setRuntime(runtimePayload || {});
      setInfoStatus(runtimePayload?.browser?.installed ? "浏览器已就绪" : "浏览器未安装");
    } catch (error) {
      setErrorStatus(String((error as Error)?.message || error || "浏览器安装失败，请稍后重试。"));
    } finally {
      setInstallingRuntime(false);
    }
  };

  const openPath = async (targetPath: string, locate = false) => {
    if (!targetPath || !window.peapDesktop) {
      return;
    }
    if (locate && window.peapDesktop.showItemInFolder) {
      await window.peapDesktop.showItemInFolder(targetPath);
      return;
    }
    if (window.peapDesktop.openPath) {
      await window.peapDesktop.openPath(targetPath);
    }
  };

  const restartBackend = async () => {
    if (!window.peapDesktop?.restartBackend) {
      return;
    }
    await window.peapDesktop.restartBackend();
    setInfoStatus("后台已重启");
  };

  return (
    <div data-testid={PAGE_TEST_IDS.settings.page}>
      <section data-testid={PAGE_TEST_IDS.settings.form}>
        <label htmlFor="default_exchange">默认交易所</label>
        <input
          id="default_exchange"
          value={formState.default_exchange}
          onChange={(event) => setFormState((state) => ({ ...state, default_exchange: event.target.value }))}
        />

        <label htmlFor="default_project_type">默认项目类型</label>
        <input
          id="default_project_type"
          value={formState.default_project_type}
          onChange={(event) => setFormState((state) => ({ ...state, default_project_type: event.target.value }))}
        />

        <label htmlFor="default_concurrency">默认并发</label>
        <input
          id="default_concurrency"
          type="number"
          min={1}
          value={formState.default_concurrency}
          onChange={(event) => setFormState((state) => ({ ...state, default_concurrency: Number(event.target.value || 1) }))}
        />

        <label htmlFor="postprocess_config">后处理配置</label>
        <input
          id="postprocess_config"
          value={formState.postprocess_config}
          onChange={(event) => setFormState((state) => ({ ...state, postprocess_config: event.target.value }))}
        />

        <label htmlFor="save_json">保存 JSON</label>
        <input
          id="save_json"
          type="checkbox"
          checked={formState.save_json}
          onChange={(event) => setFormState((state) => ({ ...state, save_json: event.target.checked }))}
        />

        <label htmlFor="workspace_root">工作目录</label>
        <input id="workspace_root" readOnly value={formState.workspace_root} />

        <label htmlFor="archive_root">归档目录</label>
        <input id="archive_root" readOnly value={formState.archive_root} />

        <label htmlFor="export_root">导出目录</label>
        <input id="export_root" readOnly value={formState.export_root} />

        <label htmlFor="streaming_db">数据库</label>
        <input id="streaming_db" readOnly value={formState.streaming_db} />

        <label htmlFor="app_home">应用目录</label>
        <input id="app_home" readOnly value={formState.app_home} />

        <label htmlFor="log_dir">日志目录</label>
        <input id="log_dir" readOnly value={formState.log_dir} />

        <label htmlFor="cache_dir">缓存目录</label>
        <input id="cache_dir" readOnly value={formState.cache_dir} />

        <label htmlFor="raw_auto_root">自动导入目录</label>
        <input id="raw_auto_root" readOnly value={formState.raw_auto_root} />

        <label htmlFor="raw_manual_root">手动导入目录</label>
        <input id="raw_manual_root" readOnly value={formState.raw_manual_root} />

        <label htmlFor="browser_cache_dir">浏览器缓存目录</label>
        <input id="browser_cache_dir" readOnly value={formState.browser_cache_dir} />

        <button type="button" onClick={onSave} disabled={actionInFlight || !isFormDirty}>保存设置</button>
      </section>

      <section data-testid={PAGE_TEST_IDS.settings.runtimeActions}>
        <p>{runtimeSummary.headline}</p>
        <p>{runtimeSummary.browserState}</p>
        {runtimeSummary.detailLines.map((line) => (
          <p key={line}>{line}</p>
        ))}
        <button type="button" onClick={runRuntimeCheck} disabled={actionInFlight}>检测运行环境</button>
        <button type="button" onClick={runRuntimeInstall} disabled={installActionDisabled}>安装浏览器</button>
        <button type="button" onClick={() => openPath(formState.workspace_root)} disabled={!formState.workspace_root}>打开工作目录</button>
        <button type="button" onClick={() => openPath(formState.archive_root)} disabled={!formState.archive_root}>打开归档目录</button>
        <button type="button" onClick={() => openPath(formState.export_root)} disabled={!formState.export_root}>打开导出目录</button>
        <button type="button" onClick={() => openPath(formState.postprocess_config)} disabled={!formState.postprocess_config}>打开后处理配置目录</button>
        <button type="button" onClick={() => openPath(formState.app_home)} disabled={!formState.app_home}>打开应用目录</button>
        <button type="button" onClick={() => openPath(formState.log_dir)} disabled={!formState.log_dir}>打开日志目录</button>
        <button type="button" onClick={() => openPath(formState.cache_dir)} disabled={!formState.cache_dir}>打开缓存目录</button>
        <button type="button" onClick={() => openPath(formState.raw_auto_root)} disabled={!formState.raw_auto_root}>打开自动导入目录</button>
        <button type="button" onClick={() => openPath(formState.raw_manual_root)} disabled={!formState.raw_manual_root}>打开手动导入目录</button>
        <button type="button" onClick={() => openPath(formState.browser_cache_dir)} disabled={!formState.browser_cache_dir}>打开浏览器缓存目录</button>
        <button type="button" onClick={() => openPath(formState.streaming_db, true)} disabled={!formState.streaming_db}>定位数据库</button>
        <button type="button" onClick={restartBackend}>重启后端</button>
      </section>

      {statusText ? <p>{statusText}</p> : null}
      {statusError ? <p>{statusError}</p> : null}
    </div>
  );
}
