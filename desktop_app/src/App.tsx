import { Refine } from "@refinedev/core";
import { ConfigProvider, Result } from "antd";
import { Component, Suspense, lazy, useEffect, useMemo, useState, type ReactNode } from "react";
import { AppShell } from "./app-shell";
import { readBackendConfig } from "./desktop/config";
import { type BackendConfig } from "./desktop/contracts";
import { DesktopProvider } from "./desktop/provider";

const PANEL_KEYS = ["overview", "tasks", "records", "mappings", "settings"] as const;
type PanelKey = (typeof PANEL_KEYS)[number];

const PAGE_COMPONENTS = {
  overview: lazy(() => import("./pages/OverviewPage")),
  tasks: lazy(() => import("./pages/TasksPage")),
  records: lazy(() => import("./pages/RecordsPage")),
  mappings: lazy(() => import("./pages/MappingsPage")),
  settings: lazy(() => import("./pages/SettingsPage")),
} satisfies Record<PanelKey, ReturnType<typeof lazy>>;

const PANEL_TITLES: Record<PanelKey, string> = {
  overview: "总览页骨架已就绪",
  tasks: "任务页骨架已就绪",
  records: "记录页骨架已就绪",
  mappings: "映射页骨架已就绪",
  settings: "设置页骨架已就绪",
};

function isPanelKey(value: string): value is PanelKey {
  return (PANEL_KEYS as readonly string[]).includes(value);
}

function formatLazyLoadError(error: unknown) {
  const rawMessage = String((error as Error)?.message || error || "页面模块加载失败，请稍后重试。");
  if (
    /ChunkLoadError/i.test(rawMessage)
    || /Loading chunk [\d\w-]+ failed/i.test(rawMessage)
    || /Failed to fetch dynamically imported module/i.test(rawMessage)
  ) {
    return "页面模块加载失败（分块资源不可用），请刷新应用后重试。";
  }
  return rawMessage;
}

function publishDesktopBootstrapState(state: { ready: boolean; error: string }) {
  const appWindow = window as typeof window & {
    __PEAP_DESKTOP_BOOTSTRAP_STATE?: { ready: boolean; error: string };
  };
  appWindow.__PEAP_DESKTOP_BOOTSTRAP_STATE = state;
}

type LazyPageErrorBoundaryProps = {
  activeKey: PanelKey;
  panelTitle: string;
  children: ReactNode;
};

type LazyPageErrorBoundaryState = {
  errorMessage: string;
};

class LazyPageErrorBoundary extends Component<LazyPageErrorBoundaryProps, LazyPageErrorBoundaryState> {
  constructor(props: LazyPageErrorBoundaryProps) {
    super(props);
    this.state = { errorMessage: "" };
  }

  static getDerivedStateFromError(error: unknown): LazyPageErrorBoundaryState {
    return { errorMessage: formatLazyLoadError(error) };
  }

  componentDidUpdate(prevProps: LazyPageErrorBoundaryProps) {
    if (prevProps.activeKey !== this.props.activeKey && this.state.errorMessage) {
      this.setState({ errorMessage: "" });
    }
  }

  render() {
    if (this.state.errorMessage) {
      return <Result status="error" title={`${this.props.panelTitle} 加载失败`} subTitle={this.state.errorMessage} />;
    }
    return this.props.children;
  }
}

export default function App() {
  const [activeKey, setActiveKey] = useState<PanelKey>("overview");
  const [backendConfig, setBackendConfig] = useState<BackendConfig | null>(null);
  const [bootstrapError, setBootstrapError] = useState("");
  const [panelSelectionError, setPanelSelectionError] = useState("");
  const resources = useMemo(
    () => [
      { name: "overview", list: "/overview" },
      { name: "tasks", list: "/tasks" },
      { name: "records", list: "/records" },
      { name: "mappings", list: "/mappings" },
      { name: "settings", list: "/settings" },
    ],
    [],
  );

  useEffect(() => {
    let cancelled = false;
    readBackendConfig(window.peapDesktop)
      .then((config) => {
        if (!cancelled) {
          setBackendConfig(config);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setBootstrapError(String((error && error.message) || error || "Desktop backend config is unavailable"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    publishDesktopBootstrapState({
      ready: Boolean(backendConfig),
      error: bootstrapError,
    });
  }, [backendConfig, bootstrapError]);

  const handlePanelSelect = (nextKey: string) => {
    if (!isPanelKey(nextKey)) {
      setPanelSelectionError(`未知面板 key：${nextKey}`);
      return;
    }
    setPanelSelectionError("");
    setActiveKey(nextKey);
  };

  if (bootstrapError) {
    return (
      <ConfigProvider>
        <Result status="error" title="桌面后端配置不可用" subTitle={bootstrapError} />
      </ConfigProvider>
    );
  }

  if (!backendConfig) {
    return (
      <ConfigProvider>
        <Result status="info" title="正在连接桌面后端" subTitle="等待 preload bridge 返回 backend config。" />
      </ConfigProvider>
    );
  }

  const ActivePage = PAGE_COMPONENTS[activeKey];

  return (
    <ConfigProvider>
      <DesktopProvider config={backendConfig}>
        <Refine resources={resources}>
          <AppShell activeKey={activeKey} onSelect={handlePanelSelect}>
            {panelSelectionError ? (
              <Result status="warning" title="导航面板标识无效" subTitle={panelSelectionError} />
            ) : (
              <LazyPageErrorBoundary activeKey={activeKey} panelTitle={PANEL_TITLES[activeKey]}>
                <Suspense
                  fallback={(
                    <Result
                      status="info"
                      title={PANEL_TITLES[activeKey]}
                      subTitle="页面模块加载中。"
                    />
                  )}
                >
                  <ActivePage />
                </Suspense>
              </LazyPageErrorBoundary>
            )}
          </AppShell>
        </Refine>
      </DesktopProvider>
    </ConfigProvider>
  );
}
