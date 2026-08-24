import { dirname, resolve } from "path";
import { fileURLToPath } from "url";

import { DESKTOP_API_TOKEN_HEADER } from "./src/backendConfig.js";

const backendTarget = process.env.PEAP_FRONTEND_BACKEND_TARGET || "http://127.0.0.1:42679";
const frontendApiToken = process.env.PEAP_FRONTEND_API_TOKEN || process.env.PEAP_APP_API_TOKEN || "";
const frontendPort = Number(process.env.PEAP_FRONTEND_PORT || "5173");
const currentDir = dirname(fileURLToPath(import.meta.url));

const apiProxy = {
  target: backendTarget,
  changeOrigin: true,
};
if (frontendApiToken) {
  apiProxy.headers = {
    [DESKTOP_API_TOKEN_HEADER]: frontendApiToken,
  };
}

export default {
  root: currentDir,
  server: {
    host: "127.0.0.1",
    port: Number.isFinite(frontendPort) ? frontendPort : 5173,
    strictPort: true,
    proxy: {
      "/api/": apiProxy,
    },
  },
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
};
