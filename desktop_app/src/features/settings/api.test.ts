import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  checkRuntimeDependencies,
  installRuntimeBrowser,
  loadSettingsSnapshot,
  saveSettingsSnapshot,
} from "./api";

const config = {
  backendUrl: "http://127.0.0.1:42679",
  apiToken: "token",
};

const fetchMock = vi.fn();

describe("settings api error normalization", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  it("normalizes backend raw error for settings snapshot load", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({
        error: "Traceback: sqlite3.OperationalError: database is locked",
      }),
    });

    await expect(loadSettingsSnapshot(config)).rejects.toThrow("加载设置失败，请稍后重试。");
  });

  it("keeps business-facing userMessage for save failures", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({
        userMessage: "默认并发必须是正整数",
      }),
    });

    await expect(saveSettingsSnapshot(config, { basic: {}, advanced: {} })).rejects.toThrow("默认并发必须是正整数");
  });

  it("normalizes runtime check and install errors", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({
        error: "runtime probe timeout",
      }),
    });
    await expect(checkRuntimeDependencies(config)).rejects.toThrow("运行环境检测失败，请稍后重试。");

    await expect(installRuntimeBrowser(config)).rejects.toThrow("浏览器安装请求失败，请稍后重试。");
  });
});
