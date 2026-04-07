import { defineConfig } from "vite";
import { resolve } from "path";

export default defineConfig({
  root: __dirname,
  server: {
    port: 5173,
    proxy: {
      "/api/": {
        target: "http://127.0.0.1:42679",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
});
