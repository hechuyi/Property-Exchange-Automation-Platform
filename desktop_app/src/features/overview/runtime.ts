export type RuntimeSummary = {
  headline: string;
  browserState: string;
  detailLines: string[];
};

export function summarizeRuntimeState({
  browserRuntime,
  productReadiness,
  browserInstall,
}: {
  browserRuntime: Record<string, unknown> | null;
  productReadiness: Record<string, unknown> | null;
  browserInstall: Record<string, unknown> | null;
}): RuntimeSummary {
  const installStatus = String(browserInstall?.status || "idle");
  const isInstalling = installStatus === "running";
  const ready = Boolean(productReadiness?.download_ready);
  const hasBrowserError = Boolean(browserRuntime?.error);
  const issues = Array.isArray(productReadiness?.issues)
    ? productReadiness?.issues
        .map((item) => {
          if (item && typeof item === "object") {
            return String((item as Record<string, unknown>).message || "").trim();
          }
          return String(item || "").trim();
        })
        .filter(Boolean)
    : [];
  const hasIssues = issues.length > 0;

  let headline = "运行环境未完成";
  if (isInstalling) {
    headline = "正在准备浏览器运行环境";
  } else if (ready) {
    headline = "运行环境已就绪";
  } else if (hasBrowserError || hasIssues) {
    headline = "运行环境缺失或异常";
  }

  let browserState = "浏览器未检测";
  if (!browserRuntime) {
    browserState = "浏览器未检测";
  } else if (isInstalling) {
    browserState = "浏览器正在安装";
  } else if (Boolean(browserRuntime.installed)) {
    browserState = "浏览器已就绪";
  } else if (hasBrowserError) {
    browserState = "浏览器状态异常";
  } else {
    browserState = "浏览器未安装";
  }

  const detailLines: string[] = [];
  const installMessage = String(browserInstall?.message || "").trim();
  if (installMessage) {
    detailLines.push(`安装状态：${installMessage}`);
  } else if (isInstalling) {
    detailLines.push("安装状态：浏览器正在后台安装");
  }
  if (hasBrowserError) {
    detailLines.push(`浏览器详情：${String(browserRuntime?.error || "")}`);
  }
  if (issues.length > 0) {
    detailLines.push(`就绪检查：${issues.join("；")}`);
  }

  return { headline, browserState, detailLines };
}
