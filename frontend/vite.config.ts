import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// staticwebapp.config.json lives in public/ — Vite copies it to dist/ automatically.
// No manual copy plugin needed.

export default defineConfig({
  plugins: [
    react(),
  ],
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    rollupOptions: {
      output: {
        manualChunks: undefined
      }
    }
  }, 
});
