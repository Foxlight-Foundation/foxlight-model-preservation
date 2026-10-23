import { defineConfig } from 'vite';

// Fixed asset names: the Library server serves an exact allowlist and never inlines fonts as data: URIs (CSP font-src is 'self').
export default defineConfig({base: './', build: {outDir: '../src/foxlight_preservation_library/static', emptyOutDir: true, assetsInlineLimit: 0, rollupOptions: {output: {entryFileNames: 'assets/library.js', chunkFileNames: 'assets/[name].js', assetFileNames: 'assets/[name][extname]'}}}});
