import { defineConfig, devices } from "@playwright/test";

// Every API request in these tests is intercepted. No runtime data is written.
export default defineConfig({
  testDir: "./e2e",
  testMatch: "i18n.spec.ts",
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://127.0.0.1:3042",
    viewport: { width: 1440, height: 900 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "pnpm exec next dev --webpack --port 3042 --hostname 127.0.0.1",
    port: 3042,
    reuseExistingServer: false,
    timeout: 120_000,
    env: { NEXT_PUBLIC_BACKEND_URL: "http://127.0.0.1:3042" },
  },
});
