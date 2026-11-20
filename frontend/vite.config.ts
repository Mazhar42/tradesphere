import path from 'node:path'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, 'src') },
  },
  server: {
    port: 5173,
    strictPort: true,
    // Same-origin in development: the refresh-token cookie (path /api/v1/auth) just works.
    proxy: { '/api': { target: 'http://localhost:8000' } },
  },
  build: {
    sourcemap: true,
    // Routes are lazy-loaded; the shell (React, router, query, i18n, zod) is ~190 kB gzipped.
    chunkSizeWarningLimit: 650,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
