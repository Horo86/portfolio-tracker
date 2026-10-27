import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    // During development the backend runs separately on port 8000.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    outDir: "dist",
    rolldownOptions: {
      output: {
        // The charts' library in one chunk of its own, with its own size budget (D-138). Only
        // the library and its registration: a React module here would pull React's shared code
        // into the chunk, and the first screen would then load it. tslib stays here while only
        // ECharts uses it; `npm run budget` fails if index.html ever preloads this chunk.
        manualChunks: (id) =>
          /node_modules\/(echarts|zrender|tslib)\/|src\/charts\/echarts\./.test(id)
            ? "echarts"
            : undefined,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // Room for several waits of up to 5 s each (src/test/setup.ts) in one test.
    testTimeout: 20_000,
    include: ["src/**/*.test.{ts,tsx}"],
    // CSS is ignored in tests except the theme tokens, read by the contrast test.
    css: { include: [/themes\.css/] },
  },
});
