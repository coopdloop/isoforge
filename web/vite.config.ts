import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      // Dev server talks to a running `isoforge chat` gateway.
      '^/(sessions|scenes|projects|themes|render|export|exports|schema|health)': {
        target: 'http://127.0.0.1:4747',
        changeOrigin: true,
      },
      '/ws': { target: 'ws://127.0.0.1:4747', ws: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
