import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
export default defineConfig({ base: '/static/imports/', build: { outDir: '../static/imports', emptyOutDir: true,
  rollupOptions: { input: fileURLToPath(new URL('./src/imports-main.tsx', import.meta.url)), output: { entryFileNames: 'app.js', assetFileNames: 'app.[ext]' } } } });
