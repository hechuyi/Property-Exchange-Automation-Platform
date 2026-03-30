import { DESKTOP_API_TOKEN_HEADER } from "../../desktop/http";

type RuntimeConfig = {
  backendUrl: string;
  apiToken: string;
};

type RequestScene = "load" | "save" | "runtime-check" | "runtime-install";

function defaultSceneErrorMessage(scene: RequestScene) {
  if (scene === "save") {
    return "保存设置失败，请稍后重试。";
  }
  if (scene === "runtime-check") {
    return "运行环境检测失败，请稍后重试。";
  }
  if (scene === "runtime-install") {
    return "浏览器安装请求失败，请稍后重试。";
  }
  return "加载设置失败，请稍后重试。";
}

function normalizeErrorMessage(scene: RequestScene, payload: Record<string, any>) {
  const userMessage = String(payload?.userMessage || "").trim();
  if (userMessage) {
    return userMessage;
  }
  return defaultSceneErrorMessage(scene);
}

async function request(config: RuntimeConfig, path: string, init: RequestInit = {}, scene: RequestScene = "load") {
  let response: Response;
  try {
    response = await fetch(`${config.backendUrl}${path}`, {
      method: init.method || "GET",
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(config.apiToken ? { [DESKTOP_API_TOKEN_HEADER]: config.apiToken } : {}),
        ...((init.headers || {}) as Record<string, string>),
      },
    });
  } catch {
    throw new Error(defaultSceneErrorMessage(scene));
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(normalizeErrorMessage(scene, payload || {}));
  }
  return payload;
}

export function loadSettingsSnapshot(config: RuntimeConfig) {
  return Promise.all([
    request(config, "/api/settings/basic", {}, "load"),
    request(config, "/api/settings/advanced", {}, "load"),
    request(config, "/api/runtime/dependencies", {}, "load"),
  ]).then(([basic, advanced, runtime]) => ({ basic, advanced, runtime }));
}

export function saveSettingsSnapshot(
  config: RuntimeConfig,
  {
    basic,
    advanced,
  }: {
    basic: Record<string, any>;
    advanced: Record<string, any>;
  },
) {
  return Promise.all([
    request(config, "/api/settings/basic", {
      method: "POST",
      body: JSON.stringify(basic),
    }, "save"),
    request(config, "/api/settings/advanced", {
      method: "POST",
      body: JSON.stringify(advanced),
    }, "save"),
  ]);
}

export function checkRuntimeDependencies(config: RuntimeConfig) {
  return request(config, "/api/runtime/dependencies", {}, "runtime-check");
}

export function installRuntimeBrowser(config: RuntimeConfig) {
  return request(config, "/api/runtime/install-browser", {
    method: "POST",
    body: JSON.stringify({ browser_name: "chromium", trigger: "manual" }),
  }, "runtime-install");
}
