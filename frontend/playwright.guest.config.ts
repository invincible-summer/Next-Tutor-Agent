import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e", testMatch: ["guest-access.spec.ts", "account-settings.spec.ts"], workers: 1,
  timeout: 45_000, expect: { timeout: 12_000 }, reporter: "list",
  outputDir: "test-results/guest-access",
  use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:3047", viewport: { width: 1440, height: 900 }, trace: "retain-on-failure" },
  webServer: { command: "pnpm exec next start --port 3047 --hostname 127.0.0.1", port: 3047, reuseExistingServer: false, timeout: 60_000 },
});
