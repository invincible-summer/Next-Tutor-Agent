/** E2E helpers: API-level auth + fixture upload, UI login shortcut. */
import { request, expect, type APIRequestContext, type Page } from "@playwright/test";

// 8123 是部署的生产服务；E2E 的隔离 backend 固定用 8124（与
// playwright.config.ts 的 E2E_BACKEND_PORT 保持一致）。
export const BACKEND = process.env.E2E_BACKEND_URL || "http://127.0.0.1:8124";
export const BACKEND_WS = process.env.E2E_BACKEND_WS || "ws://127.0.0.1:8124";

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

/** Inject the bearer token so UI pages are authenticated without the form. */
export async function loginViaStorage(page: Page, token: string) {
  await page.addInitScript((t) => {
    localStorage.setItem("edu-agent-token", t);
    localStorage.setItem("edu-agent-user", JSON.stringify({ id: "", email: "e2e@example.com", name: "E2E" }));
  }, token);
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
