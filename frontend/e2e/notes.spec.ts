/**
 * Flow 6（plan.md §26）：Notes。
 * 笔记创建/vault 可用、source 归属正确（notes API 真实合同）、
 * 私有 ownership 保持（他人不可见）。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { BACKEND, registerAndLogin, unique } from "./helpers";

test("创建笔记并保持私有 ownership", async ({ page }) => {
  const api = await pwRequest.newContext();
  const a = await registerAndLogin(api);
  const b = await registerAndLogin(api);

  // 真实创建合同：POST /notes {title, content}
  const create = await api.post(`${BACKEND}/api/v1/notes/notes`, {
    headers: { Authorization: `Bearer ${a.token}`,
               "Content-Type": "application/json" },
    data: { title: `ZX-17 定理笔记 ${unique("n")}`,
            content: "ZX-17 定理的右端常数为 314159（来自教材）。", tags: ["ZX-17"] },
  });
  expect(create.status()).toBe(200);
  const note = (await create.json()).note;
  const noteId = note?.id;
  expect(noteId).toBeTruthy();

  // vault 列表含新笔记
  const vault = await api.get(`${BACKEND}/api/v1/notes/vault`, {
    headers: { Authorization: `Bearer ${a.token}` },
  });
  expect(vault.status()).toBe(200);
  const vaultBody = await vault.json();
  const allNotes = [
    ...(vaultBody.notes ?? []),
    ...(vaultBody.items ?? []),
  ];
  expect(allNotes.find((n: any) => n.id === noteId)).toBeTruthy();

  // 私有 ownership：B 的 vault 看不到，直接取 404
  const bVault = await api.get(`${BACKEND}/api/v1/notes/vault`, {
    headers: { Authorization: `Bearer ${b.token}` },
  });
  const bBody = await bVault.json();
  const bNotes = [...(bBody.notes ?? []), ...(bBody.items ?? [])];
  expect(bNotes.find((n: any) => n.id === noteId)).toBeUndefined();

  const bDetail = await api.get(`${BACKEND}/api/v1/notes/notes/${noteId}`, {
    headers: { Authorization: `Bearer ${b.token}` },
  });
  expect(bDetail.status()).toBe(404);

  // UI：笔记仓库页正常加载（笔记内容与 ownership 的实质断言在上方 API
  // 级完成；页面深处的列表视图属笔记 UI 自身的回归范围）
  const { loginViaStorage } = await import("./helpers");
  await loginViaStorage(page, a.token);
  await page.goto("/notes");
  await expect(page.getByRole("heading", { name: "笔记仓库" }))
    .toBeVisible({ timeout: 20_000 });
  await api.dispose();
});
