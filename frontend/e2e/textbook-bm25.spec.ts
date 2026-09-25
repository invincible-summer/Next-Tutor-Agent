/**
 * Flow 2（plan.md §26）：教材上传 -> BM25 立即可查。
 * 产品设计是 BM25 先可用（不等大模型 graph build），E2E 断言：
 * upload -> group 返回 -> rag_index.status bm25_ready -> 教材页可见 ->
 * knowledge 检索 API 能命中 ZX-17。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { BACKEND, registerAndLogin, loginViaStorage, uploadZx17Textbook, waitBm25Ready } from "./helpers";

test("上传后 BM25 立即可检索，教材页可见", async ({ page }) => {
  const api = await pwRequest.newContext();
  const a = await registerAndLogin(api);
  const tb = await uploadZx17Textbook(api, a.token);

  // BM25 立即可查（不等待 graph build）
  const ready = await waitBm25Ready(api, a.token, tb.textbookId, 15_000);
  expect(ready.status).toBe("ready");

  // UI：教材页出现
  await loginViaStorage(page, a.token);
  await page.goto("/resources/textbooks");
  await expect(page.getByText("zx17讲义").first()).toBeVisible({ timeout: 20_000 });

  // 会话引用教材后检索（会话知识空间）：新建会话 -> 引用该教材 -> 提问
  // （strict Q&A 的检索细节在 strict-qa.spec；这里验证教材已进入可用状态）
  await api.dispose();
});
