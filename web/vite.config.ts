import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: { port: 5173 },
  build: {
    target: 'es2022',
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // Heavy libraries live in their own lazily-loaded chunks; none is in the entry bundle.
        manualChunks(id) {
          if (id.includes('node_modules/echarts') || id.includes('node_modules/zrender')) return 'echarts'
          if (id.includes('node_modules/monaco-editor')) return 'monaco'
          if (id.includes('node_modules/katex')) return 'katex'
          if (id.includes('node_modules/@vue-flow')) return 'vueflow'
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.ts'],
    css: false,
    setupFiles: ['./src/test-setup.ts'],
  },
})
