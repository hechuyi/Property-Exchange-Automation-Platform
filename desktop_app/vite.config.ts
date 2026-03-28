import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: path.resolve(__dirname, "build", "renderer"),
    emptyOutDir: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          const normalizedId = id.split("\\").join("/");
          if (!normalizedId.includes("/node_modules/")) {
            return undefined;
          }
          if (
            normalizedId.includes("/node_modules/react/")
            || normalizedId.includes("/node_modules/react-dom/")
            || normalizedId.includes("/node_modules/scheduler/")
          ) {
            return "vendor-react";
          }
          if (normalizedId.includes("/node_modules/@refinedev/")) {
            return "vendor-refine";
          }
          return undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/testing/vitest.setup.ts"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
