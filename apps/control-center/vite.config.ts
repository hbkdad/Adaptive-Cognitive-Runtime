import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/dashboard': process.env.ACR_API_ORIGIN ?? 'http://127.0.0.1:8011',
      '/memory-inspector': process.env.ACR_API_ORIGIN ?? 'http://127.0.0.1:8011',
      '/skill-lab': process.env.ACR_API_ORIGIN ?? 'http://127.0.0.1:8011',
      '/learning-dashboard': process.env.ACR_API_ORIGIN ?? 'http://127.0.0.1:8011',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/vitest.setup.ts',
  },
})
