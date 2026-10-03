import { defineConfig } from "@playwright/test";
import { pickPortSync as pickPort } from "./tests/e2e/support/ports.mjs";

// The static demo server falls back to the next free port when 3040 is
// occupied (start.sh-style); pin via E2E_PAGES_PORT.
const PAGES_PORT = pickPort(3040, "E2E_PAGES_PORT");
// Worker re-loads must resolve the same port (see playwright.config.ts).
process.env.E2E_PAGES_PORT ??= String(PAGES_PORT);

export default defineConfig({
  testDir: "./tests/pages",
  timeout: 180_000,
  workers: 1,
  use: { baseURL: `http://127.0.0.1:${PAGES_PORT}/Next-Tutor-Agent/`, viewport: { width: 1440, height: 900 }, trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: {
    command: `python3 ../../scripts/demo/serve_pages_demo.py --port ${PAGES_PORT}`,
    url: `http://127.0.0.1:${PAGES_PORT}/Next-Tutor-Agent/`,
    reuseExistingServer: !process.env.CI,
  },
});
