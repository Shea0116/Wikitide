import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

export default defineConfig({
  base: "/static/admin/",
  build: {
    outDir: "../static/admin",
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL("./src/admin/main.tsx", import.meta.url)),
      output: { entryFileNames: "app.js", assetFileNames: "app.[ext]" },
    },
  },
});
