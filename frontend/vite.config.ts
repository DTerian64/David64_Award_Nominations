import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { copyFileSync } from 'fs'

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'copy-static-config',
      closeBundle() {
        try {
          copyFileSync('staticwebapp.config.json', 'dist/staticwebapp.config.json')
          console.log('✅ Copied staticwebapp.config.json to dist/')
        } catch (e) {
          console.warn('⚠️ Could not copy staticwebapp.config.json:', e)
        }
      }
    }
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
