import { defineConfig, devices } from "@playwright/test";

/**
 * E2E（plan.md §24-§27）：真实 backend + frontend + fake LLM。
 *
 * webServer 启动顺序：fake LLM -> backend（BM25-only，LLM_BASE_URL 指向
 * fake）-> Next dev。supervisor 理解/规划走确定性 rule 路径
 * （SUPERVISOR_LLM_PLAN=0），LLM 只服务答案合成与出题三段——E2E 测产品
 * 编排与数据流，不测供应商网络。
 */
const FAKE_LLM_PORT = 8199;
// 8123 is the deployed production service (deploy/edu-backend.service);
// E2E always runs its isolated backend copy on a scratch port.
const BACKEND_PORT = Number(process.env.E2E_BACKEND_PORT || 8124);
const FRONT_PORT = 3030;

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: `http://127.0.0.1:${FRONT_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "node e2e/fake-llm-server.mjs",
      port: FAKE_LLM_PORT,
      reuseExistingServer: true,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      // 隔离副本：backend 的存储根是模块常量，E2E 进程必须在 scratch
      // 目录跑，业务数据（chat_history/students/knowledge）绝不落仓库。
      command:
        "node e2e/prepare-backend.mjs && " +
        "cd ${E2E_BACKEND_HOME:-/tmp/edu-agent-e2e}/backend && " +
        "${E2E_PYTHON:-python3} -m uvicorn app.main:app " +
        `--host 127.0.0.1 --port ${BACKEND_PORT} --workers 1`,
      // A listening socket is not enough: wait for bootstrap/recovery to finish
      // and only start browser flows when the real readiness contract is 200.
      url: `http://127.0.0.1:${BACKEND_PORT}/api/v1/ready`,
      reuseExistingServer: false,
      timeout: 90_000,
      stdout: "ignore",
      stderr: "pipe",
      env: {
        LLM_BASE_URL: `http://127.0.0.1:${FAKE_LLM_PORT}/v1`,
        LLM_API_KEY: "fake-e2e-key",
        LLM_MODEL: "fake-llm",
        // Exercise the account switch and structured SVG question-card path
        // in the isolated browser backend. Tests that do not request a diagram
        // still receive the normal auto policy and may legitimately have no
        // illustration.
        QUIZ_SVG_ENABLED: "1",
        SUPERVISOR_LLM_PLAN: "0",
        TEXTBOOK_GRAPH_ENABLED: "0",
        JWT_SECRET: "e2e-test-secret-e2e-test-secret-e2e",
        // E2E 从单 IP 高频注册测试账号（产品限流不受测）
        RATE_LIMIT_DISABLE: "1",
        // Voice smoke：TTS 走 stub provider（plan.md §26 Flow 8）
        VOICE_TTS_PROVIDER: "stub",
        CORS_ORIGINS: `http://127.0.0.1:${FRONT_PORT},http://localhost:${FRONT_PORT}`,
      },
    },
    {
      command:
        "npx next dev --port " + FRONT_PORT +
        ` --hostname 127.0.0.1`,
      port: FRONT_PORT,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "pipe",
      env: {
        NEXT_PUBLIC_BACKEND_URL: `http://127.0.0.1:${BACKEND_PORT}`,
      },
    },
  ],
});
