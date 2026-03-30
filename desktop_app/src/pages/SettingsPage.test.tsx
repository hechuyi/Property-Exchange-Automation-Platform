import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "./SettingsPage";
import { PAGE_TEST_IDS } from "../testing/selectors";

vi.mock("../desktop/provider", () => ({
  useDesktopRuntime: () => runtime,
}));

const runtime = {
  config: {
    backendUrl: "http://127.0.0.1:42679",
    apiToken: "token",
  },
  commands: {},
};

const fetchMock = vi.fn();
const openPath = vi.fn();
const showItemInFolder = vi.fn();
const restartBackend = vi.fn();
const pickDirectory = vi.fn();
const pickFile = vi.fn();

describe("SettingsPage", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    openPath.mockReset();
    showItemInFolder.mockReset();
    restartBackend.mockReset();
    pickDirectory.mockReset();
    pickFile.mockReset();

    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: false,
        media: "",
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    window.peapDesktop = {
      getBackendConfig: () => ({ backendUrl: "http://127.0.0.1:42679", apiToken: "token" }),
      openPath,
      showItemInFolder,
      pickDirectory,
      pickFile,
      restartBackend,
    };
  });

  it("renders grouped settings sections and picker-driven path rows", async () => {
    fetchMock.mockImplementation(async () => ({
      ok: true,
      json: async () => ({
        default_exchange: "all",
        default_project_type: "all",
        default_concurrency: 1,
        workspace_root: "/tmp/workspace",
        archive_root: "/tmp/archive",
        export_root: "/tmp/export",
        postprocess_config: "/tmp/postprocess.json",
        save_json: false,
        app_home: "/tmp/app_home",
        streaming_db: "/tmp/app.db",
        log_dir: "/tmp/logs",
        cache_dir: "/tmp/cache",
      }),
    }));

    render(<SettingsPage />);

    expect(screen.getByTestId(PAGE_TEST_IDS.settings.page)).toBeInTheDocument();
    expect(screen.getByTestId(PAGE_TEST_IDS.settings.form)).toBeInTheDocument();
    expect(screen.getByTestId(PAGE_TEST_IDS.settings.runtimeActions)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "默认值" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "位置" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "运行环境" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "维护" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "工作目录 选择…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "后处理配置 选择文件…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "后处理配置 在系统中显示" })).toBeInTheDocument();
    expect(screen.getByLabelText("后处理配置")).toHaveAttribute("readonly");
  });

  it("saves settings after native picking and supports runtime restart action", async () => {
    pickDirectory
      .mockResolvedValueOnce("/picked/workspace")
      .mockResolvedValueOnce("/picked/archive")
      .mockResolvedValueOnce("/picked/export");
    pickFile.mockResolvedValueOnce("/picked/postprocess.json");
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/settings/basic") && (!init || !init.method || init.method === "GET")) {
        return {
          ok: true,
          json: async () => ({
            default_exchange: "all",
            default_project_type: "all",
            default_concurrency: 1,
            workspace_root: "/tmp/workspace",
            archive_root: "/tmp/archive",
            export_root: "/tmp/export",
          }),
        };
      }
      if (url.endsWith("/api/settings/advanced") && (!init || !init.method || init.method === "GET")) {
        return {
          ok: true,
          json: async () => ({
            postprocess_config: "",
            save_json: false,
            streaming_db: "/tmp/app.db",
          }),
        };
      }
      if (url.endsWith("/api/runtime/dependencies") && (!init || !init.method || init.method === "GET")) {
        return {
          ok: true,
          json: async () => ({
            browser: { installed: false },
            product_readiness: { download_ready: false },
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({ ok: true }),
      };
    });

    render(<SettingsPage />);

    const saveButton = await screen.findByRole("button", { name: "保存设置" });
    expect(saveButton).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "工作目录 选择…" }));
    fireEvent.click(screen.getByRole("button", { name: "归档目录 选择…" }));
    fireEvent.click(screen.getByRole("button", { name: "导出目录 选择…" }));
    fireEvent.click(screen.getByRole("button", { name: "后处理配置 选择文件…" }));
    const concurrencyInput = await screen.findByLabelText("默认并发");
    fireEvent.change(concurrencyInput, { target: { value: "3" } });
    expect(saveButton).not.toBeDisabled();
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "http://127.0.0.1:42679/api/settings/basic",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            default_exchange: "all",
            default_project_type: "all",
            default_concurrency: 3,
            workspace_root: "/picked/workspace",
            archive_root: "/picked/archive",
            export_root: "/picked/export",
          }),
        }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        "http://127.0.0.1:42679/api/settings/advanced",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            postprocess_config: "/picked/postprocess.json",
            save_json: false,
          }),
        }),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "重启后端" }));
    await waitFor(() => {
      expect(restartBackend).toHaveBeenCalledTimes(1);
    });
  });

  it("renders runtime details and keeps derived paths read-only with reveal actions", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/settings/basic") && (!init || !init.method || init.method === "GET")) {
        return {
          ok: true,
          json: async () => ({
            default_exchange: "all",
            default_project_type: "all",
            default_concurrency: 1,
            workspace_root: "/tmp/workspace",
            archive_root: "/tmp/archive",
            export_root: "/tmp/export",
          }),
        };
      }
      if (url.endsWith("/api/settings/advanced") && (!init || !init.method || init.method === "GET")) {
        return {
          ok: true,
          json: async () => ({
            postprocess_config: "/tmp/postprocess.yaml",
            save_json: false,
            streaming_db: "/tmp/app.db",
          }),
        };
      }
      if (url.endsWith("/api/runtime/dependencies") && (!init || !init.method || init.method === "GET")) {
        return {
          ok: true,
          json: async () => ({
            browser: {
              installed: false,
              error: "Chromium runtime is not installed",
              browser_cache_dir: "/tmp/pw-cache",
              executable_path: "/tmp/pw-cache/chromium",
              driver_executable: "/tmp/pw-cache/driver",
            },
            browser_install: {
              status: "running",
              message: "Installing chromium",
              attempt_count: 3,
              trigger: "auto",
            },
            product_readiness: {
              download_ready: false,
              issues: [{ message: "Chromium runtime is not installed" }],
            },
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({ ok: true }),
      };
    });

    render(<SettingsPage />);

    await screen.findByText("正在准备浏览器运行环境");
    expect(screen.getByText("浏览器正在安装")).toBeInTheDocument();
    expect(screen.getByText("安装状态：Installing chromium")).toBeInTheDocument();
    expect(screen.getByText("缓存目录：/tmp/pw-cache")).toBeInTheDocument();
    expect(screen.getByText("可执行文件：/tmp/pw-cache/chromium")).toBeInTheDocument();
    expect(screen.getByText("Playwright Driver：/tmp/pw-cache/driver")).toBeInTheDocument();
    expect(screen.getByText("浏览器详情：Chromium runtime is not installed")).toBeInTheDocument();
    expect(screen.getByText("就绪检查：Chromium runtime is not installed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "安装浏览器" })).toBeDisabled();

    const workspaceInput = screen.getByLabelText("工作目录");
    const archiveInput = screen.getByLabelText("归档目录");
    const exportInput = screen.getByLabelText("导出目录");
    const databaseInput = screen.getByLabelText("数据库");
    expect(workspaceInput).toHaveAttribute("readonly");
    expect(archiveInput).toHaveAttribute("readonly");
    expect(exportInput).toHaveAttribute("readonly");
    expect(databaseInput).toHaveAttribute("readonly");

    fireEvent.click(screen.getByRole("button", { name: "工作目录 在系统中显示" }));
    await waitFor(() => {
      expect(showItemInFolder).toHaveBeenCalledWith("/tmp/workspace");
    });

    fireEvent.click(screen.getByRole("button", { name: "归档目录 在系统中显示" }));
    await waitFor(() => {
      expect(showItemInFolder).toHaveBeenCalledWith("/tmp/archive");
    });

    fireEvent.click(screen.getByRole("button", { name: "导出目录 在系统中显示" }));
    await waitFor(() => {
      expect(showItemInFolder).toHaveBeenCalledWith("/tmp/export");
    });

    fireEvent.click(screen.getByRole("button", { name: "后处理配置 在系统中显示" }));
    await waitFor(() => {
      expect(showItemInFolder).toHaveBeenCalledWith("/tmp/postprocess.yaml");
    });

    fireEvent.click(screen.getByRole("button", { name: "数据库 在系统中显示" }));
    await waitFor(() => {
      expect(showItemInFolder).toHaveBeenCalledWith("/tmp/app.db");
    });
  });

  it("keeps save success state visible after snapshot reload", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/settings/basic") && (!init || !init.method || init.method === "GET")) {
        return {
          ok: true,
          json: async () => ({
            default_exchange: "all",
            default_project_type: "all",
            default_concurrency: 1,
            workspace_root: "/tmp/workspace",
            archive_root: "/tmp/archive",
            export_root: "/tmp/export",
          }),
        };
      }
      if (url.endsWith("/api/settings/advanced") && (!init || !init.method || init.method === "GET")) {
        return {
          ok: true,
          json: async () => ({
            postprocess_config: "/tmp/postprocess.yaml",
            save_json: false,
            streaming_db: "/tmp/app.db",
          }),
        };
      }
      if (url.endsWith("/api/runtime/dependencies") && (!init || !init.method || init.method === "GET")) {
        return {
          ok: true,
          json: async () => ({
            browser: { installed: true },
            product_readiness: { download_ready: true },
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({ ok: true }),
      };
    });

    render(<SettingsPage />);

    const saveButton = await screen.findByRole("button", { name: "保存设置" });
    fireEvent.change(await screen.findByLabelText("默认并发"), { target: { value: "4" } });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "http://127.0.0.1:42679/api/settings/basic",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(screen.getByText("设置已保存")).toBeInTheDocument();
  });

  it("enforces mutual exclusion between save/check/install runtime actions", async () => {
    let runtimeLoadCount = 0;
    let resolveRuntimeCheck: ((payload: any) => void) | null = null;
    const runtimeCheckPromise = new Promise((resolve) => {
      resolveRuntimeCheck = resolve;
    });
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/settings/basic") && (!init || !init.method || init.method === "GET")) {
        return {
          ok: true,
          json: async () => ({
            default_exchange: "all",
            default_project_type: "all",
            default_concurrency: 1,
            workspace_root: "/tmp/workspace",
            archive_root: "/tmp/archive",
            export_root: "/tmp/export",
          }),
        };
      }
      if (url.endsWith("/api/settings/advanced") && (!init || !init.method || init.method === "GET")) {
        return {
          ok: true,
          json: async () => ({
            postprocess_config: "",
            save_json: false,
            streaming_db: "/tmp/app.db",
          }),
        };
      }
      if (url.endsWith("/api/runtime/dependencies") && (!init || !init.method || init.method === "GET")) {
        runtimeLoadCount += 1;
        if (runtimeLoadCount > 1) {
          const payload = await runtimeCheckPromise;
          return {
            ok: true,
            json: async () => payload,
          };
        }
        return {
          ok: true,
          json: async () => ({
            browser: { installed: false },
            product_readiness: { download_ready: false },
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({ ok: true }),
      };
    });

    render(<SettingsPage />);

    const saveButton = await screen.findByRole("button", { name: "保存设置" });
    fireEvent.change(await screen.findByLabelText("默认并发"), { target: { value: "2" } });
    expect(saveButton).not.toBeDisabled();

    const checkButton = screen.getByRole("button", { name: "检测运行环境" });
    const installButton = screen.getByRole("button", { name: "安装浏览器" });
    fireEvent.click(checkButton);

    expect(saveButton).toBeDisabled();
    expect(checkButton).toBeDisabled();
    expect(installButton).toBeDisabled();

    resolveRuntimeCheck?.({
      browser: { installed: false },
      product_readiness: { download_ready: false },
    });

    await waitFor(() => {
      expect(checkButton).not.toBeDisabled();
    });
  });
});
