import { BackendConfig, normalizeBackendConfig } from "./contracts";

type DesktopBridge = {
  getBackendConfig: () => Promise<BackendConfig> | BackendConfig;
};

export async function readBackendConfig(bridge: DesktopBridge | null | undefined): Promise<BackendConfig> {
  if (!bridge || typeof bridge.getBackendConfig !== "function") {
    throw new Error("Desktop preload bridge is unavailable");
  }
  return normalizeBackendConfig(await bridge.getBackendConfig());
}
