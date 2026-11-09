import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

export default defineConfig({
  base: "/static/editor/",
  build: {
    outDir: "../static/editor",
    emptyOutDir: true,
    rollupOptions: {
      input: fileURLToPath(new URL("./src/editor.ts", import.meta.url)),
      output: {
        entryFileNames: "editor.js",
        assetFileNames: "editor.[ext]",
        // Language chunks must import a stable shared module, not editor.js:
        // the entry has a cache query and must execute exactly once per page.
        manualChunks: {
          "editor-core": [
            "@codemirror/state", "@codemirror/view", "@codemirror/commands",
            "@codemirror/language", "@codemirror/search", "@codemirror/lang-markdown",
            "@codemirror/language-data", "@lezer/highlight",
          ],
        },
      },
    },
  },
});
