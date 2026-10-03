import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/pages",
  timeout: 180_000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:3040/Next-Tutor-Agent/", viewport: { width: 1440, height: 900 }, trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: { command: "python3 ../../scripts/demo/serve_pages_demo.py", url: "http://127.0.0.1:3040/Next-Tutor-Agent/", reuseExistingServer: !process.env.CI },
});
