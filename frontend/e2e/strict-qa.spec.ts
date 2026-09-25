/**
 * Flow 3（plan.md §26）：严格教材问答。
 * fixture 含自造事实「ZX-17 定理的右端常数为 314159」——不可能来自模型
 * 常识，只能来自检索。断言：knowledge_search 发生、回答含 314159、
 * 来源引用可见。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { BACKEND, registerAndLogin, loginViaStorage } from "./helpers";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

test("根据教材提问，回答引用教材事实", async ({ page }) => {
  const api = await pwRequest.newContext();
  const a = await registerAndLogin(api);

  // 建会话并把教材快照进会话（chat upload：session knowledge 空间）
  const fixture = await readFile(join(process.cwd(), "e2e/fixtures/zx17.txt"));
  const up = await api.post(`${BACKEND}/api/v1/chat/upload`, {
    headers: { Authorization: `Bearer ${a.token}` },
    multipart: {
      files: { name: "zx17讲义.txt", mimeType: "text/plain", buffer: fixture },
    },
  });
  expect(up.status()).toBe(200);
  const upBody = await up.json();
  const sessionId: string = upBody.session_id ?? upBody.sessionId;
  expect(sessionId).toBeTruthy();

  // UI：打开该会话发问
  await loginViaStorage(page, a.token);
  await page.goto(`/chat/${sessionId}`);
  const input = page.locator("textarea").first();
  await expect(input).toBeVisible({ timeout: 20_000 });
  await input.fill("根据刚上传的教材，ZX-17 定理的右端常数是什么？");
  await input.press("Enter");

  // 回答流完成：assistant 消息含自造事实 314159（fake LLM 只会回显检索
  // 驱动的合成文本；断言真实链路把教材证据送达了合成层与 UI）
  await expect(
    page.getByText("314159").first(),
  ).toBeVisible({ timeout: 60_000 });

  // 检索发生过：资料检索卡片（knowledge_search tool 卡片）出现
  const searchCard = page.getByText("资料检索", { exact: false }).or(
    page.getByText("知识检索", { exact: false }),
  ).first();
  await expect(searchCard).toBeVisible({ timeout: 30_000 });
  await api.dispose();
});
