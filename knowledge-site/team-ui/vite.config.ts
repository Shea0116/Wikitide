import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  base: "/static/teams/",
  build: {
    outDir: "../static/teams",
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL("./src/main.tsx", import.meta.url)),
      output: { entryFileNames: "app.js", assetFileNames: "app.[ext]" },
    },
  },
  test: { environment: "jsdom", clearMocks: true, setupFiles: ['./src/test-i18n-setup.ts'] },
});
