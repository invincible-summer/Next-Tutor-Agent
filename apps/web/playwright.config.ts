import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";
import { pickPortSync as pickPort } from "./tests/e2e/support/ports.mjs";

/**
 * E2E（运行说明见 docs/development/testing.md）：真实 backend + frontend + fake LLM。
 *
 * webServer 启动顺序：fake LLM -> backend（BM25-only，LLM_BASE_URL 指向
 * fake）-> Next（CI 使用生产构建）。supervisor 理解/规划走确定性 rule 路径
 * （SUPERVISOR_LLM_PLAN=0），LLM 只服务答案合成与出题三段——E2E 测产品
 * 编排与数据流，不测供应商网络。
 *
 * Ports fall back automatically when the preferred one is occupied
 * (start.sh-style); pin one explicitly via E2E_LLM_PORT / E2E_BACKEND_PORT /
 * E2E_FRONTEND_PORT. Parallel local runs: E2E_WORKERS=4 pnpm test:e2e
 * (default remains 1; CI stays serial).
 */
const FAKE_LLM_PORT = pickPort(8199, "E2E_LLM_PORT");
// 8123 is the deployed production service (deploy/edu-backend.service);
// E2E always runs its isolated backend copy on a scratch port.
const BACKEND_PORT = pickPort(8124, "E2E_BACKEND_PORT");
const FRONT_PORT = pickPort(3030, "E2E_FRONTEND_PORT");
const production = process.env.E2E_PRODUCTION === "1";

// Worker processes re-load this config; pin the picked ports back into the
// environment so every re-load resolves the same values instead of probing
// again (the main process's freshly-bound ports look busy to a re-probe).
process.env.E2E_LLM_PORT ??= String(FAKE_LLM_PORT);
process.env.E2E_BACKEND_PORT ??= String(BACKEND_PORT);
process.env.E2E_FRONTEND_PORT ??= String(FRONT_PORT);

export default defineConfig({
  testDir: "./tests/e2e",
  forbidOnly: !!process.env.CI,
  globalTimeout: 25 * 60_000,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: Number(process.env.E2E_WORKERS || 1),
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
      command: "node tests/e2e/support/fake-llm-server.mjs",
      port: FAKE_LLM_PORT,
      reuseExistingServer: false,
      stdout: "ignore",
      stderr: "pipe",
      env: { FAKE_LLM_PORT: String(FAKE_LLM_PORT) },
    },
    {
      // 隔离副本：全部存储根由 NEXT_TUTOR_DATA_DIR 指向 scratch 目录，
      // 业务数据（会话/画像/知识）绝不落仓库。
      command:
        "node tests/e2e/support/prepare-backend.mjs && " +
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
        // Voice smoke：TTS 走 stub provider，不依赖真实语音服务（voice-smoke.spec.ts）
        VOICE_TTS_PROVIDER: "stub",
        CORS_ORIGINS: `http://127.0.0.1:${FRONT_PORT},http://localhost:${FRONT_PORT}`,
      },
    },
    {
      // Production mode must not reuse an ambient .next built with different
      // env: rewrites (/api proxy via BACKEND_URL) and the client API base are
      // baked at build time. build-front.mjs stamps the inputs and rebuilds
      // only when they change.
      command:
        (production
          ? "node tests/e2e/support/build-front.mjs && pnpm exec next start --port "
          : "pnpm exec next dev --webpack --port ") + FRONT_PORT +
        ` --hostname 127.0.0.1`,
      port: FRONT_PORT,
      reuseExistingServer: false,
      timeout: 240_000,
      stdout: "ignore",
      stderr: "pipe",
      env: {
        NEXT_PUBLIC_BACKEND_URL: `http://127.0.0.1:${BACKEND_PORT}`,
        BACKEND_URL: `http://127.0.0.1:${BACKEND_PORT}`,
      },
    },
  ],
});
