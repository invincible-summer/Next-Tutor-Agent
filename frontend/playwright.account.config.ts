import { defineConfig, devices } from "@playwright/test";

// Requests are mocked; this suite never touches backend storage.
export default defineConfig({
  testDir: "./e2e", testMatch: "account-settings.spec.ts", workers: 1,
  timeout: 60_000, expect: { timeout: 15_000 }, reporter: "list",
  outputDir: "test-results/account-settings",
  use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:3045", viewport: { width: 1440, height: 900 }, screenshot: "only-on-failure", trace: "retain-on-failure" },
  webServer: {
    command: "pnpm exec next dev --webpack --port 3045 --hostname 127.0.0.1",
    port: 3045, reuseExistingServer: false, timeout: 120_000,
    env: { NEXT_PUBLIC_BACKEND_URL: "http://127.0.0.1:3045" },
  },
});
