/** E2E helpers: API-level auth + fixture upload, UI login shortcut. */
import { request, expect, type APIRequestContext, type Page } from "@playwright/test";

// 8123 是部署的生产服务；E2E 的隔离 backend 固定用 8124（与
// playwright.config.ts 的 E2E_BACKEND_PORT 保持一致）。
export const BACKEND = process.env.E2E_BACKEND_URL || `http://127.0.0.1:${process.env.E2E_BACKEND_PORT || 8124}`;
export const BACKEND_WS = process.env.E2E_BACKEND_WS || BACKEND.replace(/^http/, "ws");

let seq = 0;
export function unique(prefix: string): string {
  seq += 1;
  return `${prefix}_${Date.now()}_${seq}`;
}

/** Register + login via the real backend; returns the bearer token. */
export async function registerAndLogin(
  api: APIRequestContext, email?: string,
): Promise<{ token: string; userId: string; email: string }> {
  const mail = email ?? `${unique("e2e")}@example.com`;
  const reg = await api.post(`${BACKEND}/api/v1/auth/register`, {
    data: { email: mail, password: "e2e-pass-123", name: "E2E" },
  });
  if (reg.status() !== 200 && reg.status() !== 201) {
    throw new Error(`register failed: ${reg.status()} ${await reg.text()}`);
  }
  const login = await api.post(`${BACKEND}/api/v1/auth/login`, {
    data: { email: mail, password: "e2e-pass-123" },
  });
  expect(login.status()).toBe(200);
  const body = await login.json();
  const token: string = body.token ?? body.access_token;
  if (!token) throw new Error(`no token in login response: ${JSON.stringify(body)}`);
  return { token, userId: body.user?.id ?? "", email: mail };
}

/** Inject the bearer token so UI pages are authenticated without the form.
 *
 * 纯 mock 场景（非 JWT 的假 token，如 "e2e-fake-token"）：工作区水合会
 * 用该 token 请求 /auth/me，真实后端必然 401 并清空会话、把页面重定向
 * 到登录页——只 mock 业务 API 的 spec 会整体失败。这里对假 token 顺带
 * 拦截两个水合端点，维持"已登录"状态；真实 JWT 则走真实后端。后注册的
 * 宽路由（api/v1 全前缀）handler 以 route.fallback() 兜底时会回落到这里。
 */
export async function loginViaStorage(page: Page, token: string) {
  await page.addInitScript((t) => {
    localStorage.setItem("edu-agent-token", t);
    localStorage.setItem("edu-agent-user", JSON.stringify({ id: "", email: "e2e@example.com", name: "E2E" }));
  }, token);
  if (token.startsWith("ey")) return; // 真实 JWT：交给真实后端校验
  // A mock session has no server identity. Block unmatched API calls rather
  // than sending a fake token to the live backend or hiding logout events.
  // Specific mocks registered below/by the spec take precedence.
  await page.route("**/api/v1/**", (route) => route.fulfill({
    status: 501, json: { detail: `Unmocked API: ${new URL(route.request().url()).pathname}` },
  }));
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ status: "ok", user: {
      id: "usr_e2e_mock", email: "e2e@example.com", username: "E2E",
      role: "student", created_at: 0, last_login_at: 0,
      profile: { name: "E2E", grade: "本科", school: "",
                 subjects: [], avatar: "", prefs: {} } } }),
  }));
  await page.route("**/api/v1/auth/status", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ auth_required: true, guest_allowed: false,
                           using_default_secret: false }),
  }));
}

/** Upload the ZX-17 fixture textbook via the real textbook API. */
export async function uploadZx17Textbook(
  api: APIRequestContext, token: string, title?: string,
): Promise<{ textbookId: string; groupId?: string; fileId: string }> {
  const fs = await import("node:fs/promises");
  const path = await import("node:path");
  const fixture = await fs.readFile(
    path.join(process.cwd(), "e2e/fixtures/zx17.txt"));
  const res = await api.post(`${BACKEND}/api/v1/textbooks/upload`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      files: {
        name: "zx17讲义.txt",
        mimeType: "text/plain",
        buffer: fixture,
      },
    },
  });
  expect(res.status()).toBe(200);
  const body = await res.json();
  const row = (body.results ?? [])[0] ?? null;
  if (!row) throw new Error(`no upload result: ${JSON.stringify(body)}`);
  const textbookId = row.group_id ?? row.id;
  if (!textbookId) throw new Error(`no group id in upload result: ${JSON.stringify(row)}`);
  return { textbookId, groupId: textbookId, fileId: row.file_id ?? row.id ?? "" };
}

/** Wait until the textbook reaches bm25_ready (poll the textbook API). */
export async function waitBm25Ready(
  api: APIRequestContext, token: string, textbookId: string,
  timeoutMs = 30_000,
): Promise<{ status: string; ragIndex: any }> {
  const deadline = Date.now() + timeoutMs;
  let last: any = null;
  while (Date.now() < deadline) {
    const res = await api.get(`${BACKEND}/api/v1/textbooks`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (res.status() === 200) {
      const body = await res.json();
      const items = body.textbooks ?? body.items ?? body ?? [];
      const found = (Array.isArray(items) ? items : [])
        .find((t: any) => t.id === textbookId);
      if (found) {
        last = found;
        const rag = found.rag_index ?? {};
        if (rag.status === "bm25_ready" || rag.bm25_ready || found.status === "ready") {
          return { status: "ready", ragIndex: rag };
        }
      }
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return { status: "timeout", ragIndex: last?.rag_index ?? {} };
}
