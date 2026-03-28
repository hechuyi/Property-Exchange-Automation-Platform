import type { BackendConfig } from "../desktop/contracts";

declare global {
  interface Window {
    peapDesktop?: {
      getBackendUrl?: () => Promise<string> | string;
      getBackendConfig: () => Promise<BackendConfig> | BackendConfig;
      openPath?: (targetPath: string) => Promise<string> | string;
      showItemInFolder?: (targetPath: string) => Promise<string> | string;
      pickDirectory?: (defaultPath?: string) => Promise<string> | string;
      restartBackend?: () => Promise<{ ok: true; backendUrl: string }> | { ok: true; backendUrl: string };
    };
  }
}

export {};
