import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e', testMatch: 'classroom-workflow.spec.ts', workers: 1,
  timeout: 30000, expect: { timeout: 12000 }, reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:3041', viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure' },
  webServer: { command: 'pnpm exec next dev --webpack --port 3041 --hostname 127.0.0.1', port: 3041, reuseExistingServer: false, timeout: 120000,
    env: { NEXT_PUBLIC_BACKEND_URL: 'http://127.0.0.1:3041' } }
});
