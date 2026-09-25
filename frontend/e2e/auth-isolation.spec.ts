/**
 * Flow 1（plan.md §26）：身份隔离。
 * A 注册登录 -> 建 session -> 上传私有教材 -> 登出；
 * B 登录 -> 不能看到 A 的 session/教材（API 404/不可见 + UI 不出现）。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { BACKEND, registerAndLogin, loginViaStorage, uploadZx17Textbook, unique } from "./helpers";

test("A 的私有 session/教材对 B 不可见", async ({ page }) => {
  const api = await pwRequest.newContext();
  const a = await registerAndLogin(api);
  const b = await registerAndLogin(api);

  // A 上传私有教材（进入 building/bm25 索引）
  const tb = await uploadZx17Textbook(api, a.token);
  expect(tb.textbookId).toBeTruthy();

  // A 建一个会话（走真实 chat API 发一句话）
  const chat = await api.post(`${BACKEND}/api/v1/chat/stream`, {
    headers: { Authorization: `Bearer ${a.token}`,
               "Content-Type": "application/json" },
    data: { message: "你好，我是 A", session_id: "" },
    maxRedirects: 0,
  });
  expect(chat.status()).toBe(200);

  // B 的教材列表不含 A 的私有教材
  const bList = await api.get(`${BACKEND}/api/v1/textbooks`, {
    headers: { Authorization: `Bearer ${b.token}` },
  });
  expect(bList.status()).toBe(200);
  const bItems = (await bList.json()).textbooks ?? [];
  expect(bItems.find((t: any) => t.id === tb.textbookId)).toBeUndefined();

  // A 的教材在自己的列表中（scope=private）
  const aList = await api.get(`${BACKEND}/api/v1/textbooks`, {
    headers: { Authorization: `Bearer ${a.token}` },
  });
  const aItems = (await aList.json()).textbooks ?? [];
  expect(aItems.find((t: any) => t.id === tb.textbookId)).toBeTruthy();

  // B 直接请求 A 的教材详情 -> 404（不暴露存在性）
  const bDetail = await api.get(`${BACKEND}/api/v1/textbooks/${tb.textbookId}`, {
    headers: { Authorization: `Bearer ${b.token}` },
  });
  expect(bDetail.status()).toBe(404);

  // UI：B 打开教材页，看不到 A 的教材标题
  await loginViaStorage(page, b.token);
  await page.goto("/resources/textbooks");
  await expect(page.getByText("zx17讲义").first()).toBeHidden();
  await api.dispose();
});

test("UI 登录后可以看到自己的教材", async ({ page }) => {
  const api = await pwRequest.newContext();
  const a = await registerAndLogin(api);
  await uploadZx17Textbook(api, a.token);
  await loginViaStorage(page, a.token);
  await page.goto("/resources/textbooks");
  await expect(page.getByText("zx17讲义").first()).toBeVisible({ timeout: 20_000 });
  await api.dispose();
});
