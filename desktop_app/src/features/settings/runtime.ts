function normalizeText(value: unknown) {
  return String(value ?? "").trim();
}

function installStateLine(installState: Record<string, any>) {
  const message = normalizeText(installState.message);
  if (message) {
    return `安装状态：${message}`;
  }
  const status = normalizeText(installState.status || "idle");
  if (status === "running") {
    return "安装状态：浏览器正在后台安装";
  }
  if (status === "succeeded") {
    return "安装状态：浏览器已安装";
  }
  if (status === "failed") {
    return "安装状态：浏览器安装失败";
  }
  return "";
}

export type SettingsRuntimeSummary = {
  headline: string;
  browserState: string;
  detailLines: string[];
  installStatus: string;
};

export function summarizeSettingsRuntimeState(runtimePayload: Record<string, any>) {
  if (!runtimePayload || !Object.keys(runtimePayload).length) {
    return {
      headline: "等待运行环境检测",
      browserState: "浏览器未检测",
      detailLines: [],
      installStatus: "idle",
    } satisfies SettingsRuntimeSummary;
  }

  const browser = (runtimePayload.browser || {}) as Record<string, any>;
  const productReadiness = (runtimePayload.product_readiness || {}) as Record<string, any>;
  const browserInstall = (runtimePayload.browser_install || {}) as Record<string, any>;
  const installStatus = normalizeText(browserInstall.status || "idle") || "idle";
  const isInstalling = installStatus === "running";
  const isReady = Boolean(productReadiness.download_ready);
  const hasBrowserError = Boolean(normalizeText(browser.error));
  const issues = Array.isArray(productReadiness.issues)
    ? productReadiness.issues
        .map((issue) => {
          if (issue && typeof issue === "object") {
            return normalizeText((issue as Record<string, any>).message);
          }
          return normalizeText(issue);
        })
        .filter(Boolean)
    : [];

  let headline = "运行环境未完成";
  if (isInstalling) {
    headline = "正在准备浏览器运行环境";
  } else if (isReady) {
    headline = "运行环境已就绪";
  } else if (hasBrowserError || issues.length > 0) {
    headline = "运行环境缺失或异常";
  }

  let browserState = "浏览器未检测";
  if (isInstalling) {
    browserState = "浏览器正在安装";
  } else if (Boolean(browser.installed)) {
    browserState = "浏览器已就绪";
  } else if (hasBrowserError) {
    browserState = "浏览器状态异常";
  } else if (Object.keys(browser).length) {
    browserState = "浏览器未安装";
  }

  const detailLines: string[] = [];
  const installLine = installStateLine(browserInstall);
  if (installLine) {
    detailLines.push(installLine);
  }
  if (normalizeText(browser.browser_cache_dir)) {
    detailLines.push(`缓存目录：${normalizeText(browser.browser_cache_dir)}`);
  }
  if (normalizeText(browser.executable_path)) {
    detailLines.push(`可执行文件：${normalizeText(browser.executable_path)}`);
  }
  if (normalizeText(browser.driver_executable)) {
    detailLines.push(`Playwright Driver：${normalizeText(browser.driver_executable)}`);
  }
  if (normalizeText(browser.error)) {
    detailLines.push(`浏览器详情：${normalizeText(browser.error)}`);
  }
  if (issues.length > 0) {
    detailLines.push(`就绪检查：${issues.join("；")}`);
  }

  return {
    headline,
    browserState,
    detailLines,
    installStatus,
  } satisfies SettingsRuntimeSummary;
}
