import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

/**
 * E2E（运行说明见 docs/TESTING.md）：真实 backend + frontend + fake LLM。
 *
 * webServer 启动顺序：fake LLM -> backend（BM25-only，LLM_BASE_URL 指向
 * fake）-> Next（CI 使用生产构建）。supervisor 理解/规划走确定性 rule 路径
 * （SUPERVISOR_LLM_PLAN=0），LLM 只服务答案合成与出题三段——E2E 测产品
 * 编排与数据流，不测供应商网络。
 */
const FAKE_LLM_PORT = 8199;
// 8123 is the deployed production service (deploy/edu-backend.service);
// E2E always runs its isolated backend copy on a scratch port.
const BACKEND_PORT = Number(process.env.E2E_BACKEND_PORT || 8124);
const FRONT_PORT = Number(process.env.E2E_FRONTEND_PORT || 3030);
const production = process.env.E2E_PRODUCTION === "1";

export default defineConfig({
  testDir: "./e2e",
  forbidOnly: !!process.env.CI,
  globalTimeout: 25 * 60_000,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["line"], ["html", { open: "never" }]],
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
      reuseExistingServer: false,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      // 隔离副本：全部存储根由 NEXT_TUTOR_DATA_DIR 指向 scratch 目录，
      // 业务数据（会话/画像/知识）绝不落仓库。
      command:
        "node e2e/prepare-backend.mjs && " +
        'cd "${E2E_BACKEND_HOME:-/tmp/edu-agent-e2e}/services/api" && ' +
        '"${E2E_PYTHON:-python3}" -m uvicorn app.main:app ' +
        `--host 127.0.0.1 --port ${BACKEND_PORT} --workers 1`,
      // A listening socket is not enough: wait for bootstrap/recovery to finish
      // and only start browser flows when the real readiness contract is 200.
      url: `http://127.0.0.1:${BACKEND_PORT}/api/v1/ready`,
      reuseExistingServer: false,
      timeout: 90_000,
      stdout: "ignore",
      stderr: "pipe",
      env: {
        EDU_TEST_KEYLESS: "1",
        AUTH_MODE: "1",
        NEXT_TUTOR_DATA_DIR: resolve(process.env.E2E_BACKEND_HOME || "/tmp/edu-agent-e2e", "data"),
        ADMIN_EMAIL: "material-admin@e2e.example.com",
        ADMIN_PASSWORD: "e2e-pass-123",
        OPENAI_API_KEY: "",
        AZURE_SPEECH_KEY: "",
        AZURE_SPEECH_REGION: "",
        TAVILY_API_KEY: "",
        PEXELS_API_KEY: "",
        PIXABAY_API_KEY: "",
        LLM_BASE_URL: `http://127.0.0.1:${FAKE_LLM_PORT}/v1`,
        LLM_API_KEY: "fake-e2e-key",
        LLM_MODEL: "fake-llm",
        // 站内助手（A05+）：E2e 打开总开关，验证 P1 全链路。
        SITE_ASSISTANT_ENABLED: "1",
        // Exercise the account switch and structured SVG question-card path
        // in the isolated browser backend. Tests that do not request a diagram
        // still receive the normal auto policy and may legitimately have no
        // illustration.
        QUIZ_SVG_ENABLED: "1",
        SUPERVISOR_LLM_PLAN: "0",
        TEXTBOOK_GRAPH_ENABLED: "0",
        AUTH_JWT_SECRET: "e2e-test-secret-e2e-test-secret-e2e",
        // E2E 从单 IP 高频注册测试账号（产品限流不受测）
        RATE_LIMIT_DISABLE: "1",
        // Voice smoke：TTS 走 stub provider（plan.md §26 Flow 8）
        VOICE_TTS_PROVIDER: "stub",
        CORS_ORIGINS: `http://127.0.0.1:${FRONT_PORT},http://localhost:${FRONT_PORT}`,
      },
    },
    {
      command:
        (production ? "pnpm exec next start --port " : "pnpm exec next dev --webpack --port ") + FRONT_PORT +
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
