import { defineConfig, devices } from '@playwright/test'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const API = 'http://127.0.0.1:8100', WEB = 'http://127.0.0.1:4173'
const DB = join(tmpdir(), `learnai-e2e-${process.pid}.db`)
const py = '../backend/.venv/bin/python'

export default defineConfig({
  testDir: './e2e', timeout: 45_000, expect: { timeout: 10_000 }, fullyParallel: false, workers: 1, retries: 0,
  reporter: [['list']],
  use: { baseURL: WEB, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [{ name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } } }],
  webServer: [
    { command: 'node e2e/fake-ollama.mjs', env: { PORT: '11999' }, url: 'http://127.0.0.1:11999/api/tags', reuseExistingServer: false },
    { // the real FastAPI backend on a throwaway SQLite file; only the model is faked
      command: `${py} -m uvicorn main:app --host 127.0.0.1 --port 8100`, cwd: '../backend', url: `${API}/health`, reuseExistingServer: false,
      env: { ENVIRONMENT: 'test', SECRET_KEY: 'e2e-secret-key-e2e-secret-key-0123456789', DATABASE_PATH: DB, DB_BACKEND: 'sqlite', LLM_PROVIDERS: 'ollama',
        OLLAMA_BASE_URL: 'http://127.0.0.1:11999', OLLAMA_API_KEY: '', OLLAMA_MODEL: 'fake', GEMINI_API_KEY: '', RATE_LIMIT_PER_MINUTE: '600', LLM_TIMEOUT_S: '30' } },
    { // production build served by vite preview, pointed at the e2e backend
      command: `VITE_API_BASE_URL=${API} npx vite build --outDir dist-e2e && npx vite preview --outDir dist-e2e --host 127.0.0.1 --port 4173 --strictPort`, url: WEB, reuseExistingServer: false, timeout: 180_000 },
  ],
})
