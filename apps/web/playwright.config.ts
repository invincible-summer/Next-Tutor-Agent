import { defineConfig, devices } from "@playwright/test";
import { pickPortSync as pickPort } from "./tests/e2e/support/ports.mjs";

/**
 * E2E（运行说明见 docs/development/testing.md）：真实 backend + frontend + fake LLM。
 *
 * runner 启动顺序：fake LLM -> backend（BM25-only，LLM_BASE_URL 指向
 * fake）-> Next（CI 使用生产构建）。supervisor 理解/规划走确定性 rule 路径
 * （SUPERVISOR_LLM_PLAN=0），LLM 只服务答案合成与出题三段——E2E 测产品
 * 编排与数据流，不测供应商网络。
 *
 * Single production mode: pnpm test:e2e. Ports fall back when occupied;
 * every run owns a temporary data root and uses exactly one worker.
 */
const FRONT_PORT = pickPort(3030, "E2E_FRONTEND_PORT");
const scratch = process.env.E2E_RUN_ROOT;
if (!scratch) throw new Error("Run product E2E via pnpm test:e2e");

export default defineConfig({
  testDir: "./tests/e2e",
  forbidOnly: !!process.env.CI,
  globalSetup: "./tests/e2e/support/global-setup.ts",
  globalTimeout: 10 * 60_000,
  timeout: 45_000,
  expect: { timeout: 8_000 },
  maxFailures: 5,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["line"], ["html", { open: "never" }], ["json", { outputFile: "test-results/e2e-results.json" }]],
  use: {
    baseURL: `http://127.0.0.1:${FRONT_PORT}`,
    actionTimeout: 8_000,
    navigationTimeout: 15_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
