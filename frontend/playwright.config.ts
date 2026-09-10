import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 60000,
  use: { baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:18080', trace: 'retain-on-failure' },
})
