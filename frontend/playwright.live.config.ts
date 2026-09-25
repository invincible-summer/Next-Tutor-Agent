import { defineConfig, devices } from "@playwright/test";

/**
 * Explicit local-only real-LLM acceptance lane.
 *
 * This config is intentionally separate from playwright.config.ts, so GitHub
 * Actions never discovers or runs it. It starts an isolated local backend and
 * local Next frontend, but inherits the developer shell's real LLM provider
 * variables (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / QUIZ_MODEL as configured).
 * No deployment URL or CI secret is involved.
 */
if (process.env.LIVE_LLM_TEST !== "1") {
  throw new Error("Set LIVE_LLM_TEST=1 explicitly before running real-LLM acceptance tests.");
}
if (!process.env.LLM_API_KEY) {
  throw new Error("LLM_API_KEY is required for the local real-LLM acceptance lane.");
}

const BACKEND_PORT = Number(process.env.LIVE_E2E_BACKEND_PORT || 8125);
const FRONT_PORT = Number(process.env.LIVE_E2E_FRONTEND_PORT || 3031);
const BACKEND_HOME = process.env.LIVE_E2E_BACKEND_HOME || "/tmp/edu-agent-live-e2e";

export default defineConfig({
  testDir: "./e2e-live",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${FRONT_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command:
        `E2E_BACKEND_HOME=${JSON.stringify(BACKEND_HOME)} E2E_FRESH=1 node e2e/prepare-backend.mjs && ` +
        `cd ${JSON.stringify(`${BACKEND_HOME}/backend`)} && ` +
        "QUIZ_SVG_ENABLED=1 SUPERVISOR_LLM_PLAN=0 TEXTBOOK_GRAPH_ENABLED=0 " +
        "RATE_LIMIT_DISABLE=1 JWT_SECRET=live-e2e-test-secret-live-e2e-test-secret " +
        `CORS_ORIGINS=http://127.0.0.1:${FRONT_PORT},http://localhost:${FRONT_PORT} ` +
        "${E2E_PYTHON:-python3} -m uvicorn app.main:app " +
        `--host 127.0.0.1 --port ${BACKEND_PORT} --workers 1`,
      url: `http://127.0.0.1:${BACKEND_PORT}/api/v1/ready`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command:
        `NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:${BACKEND_PORT} ` +
        `npx next dev --port ${FRONT_PORT} --hostname 127.0.0.1`,
      port: FRONT_PORT,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
