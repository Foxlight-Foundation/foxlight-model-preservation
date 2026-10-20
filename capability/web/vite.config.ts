import { defineConfig } from 'vite';

export default defineConfig({base: './', build: {outDir: '../src/foxlight_preservation_library/static', emptyOutDir: true, rollupOptions: {output: {entryFileNames: 'assets/library.js', chunkFileNames: 'assets/[name].js', assetFileNames: 'assets/[name][extname]'}}}});
