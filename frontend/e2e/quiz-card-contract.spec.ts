/**
 * 用户可见题卡合同：普通聊天明确要题必须生成可交互题卡；正式作答写回
 * 会话并进入跨会话习题历史；测评中心默认题量为 1。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { BACKEND, loginViaStorage, registerAndLogin, unique } from "./helpers";

test("聊天出题可作答、刷新恢复并在习题中心加载", async ({ page }) => {
  const api = await pwRequest.newContext();
  const learner = await registerAndLogin(api);
  const auth = { Authorization: `Bearer ${learner.token}` };

  // 空学习区已经足够渲染测评配置；概念为空不会影响本用例核对默认题量。
  const workspace = await api.post(`${BACKEND}/api/v1/workspaces`, {
    headers: auth,
    data: { name: unique("题卡验收区"), folder_ids: [], file_ids: [] },
  });
  expect(workspace.status()).toBe(200);

  await loginViaStorage(page, learner.token);
  await page.goto("/chat");
  const input = page.locator("textarea").first();
  await expect(input).toBeVisible({ timeout: 20_000 });
  await input.fill("请出一道 ZX-17 定理选择题考我");
  await input.press("Enter");

  const card = page.getByTestId("quiz-card").first();
  await expect(card).toBeVisible({ timeout: 90_000 });
  await expect(card).toContainText("ZX-17 定理");
  await expect(card).not.toContainText("旧题目仅供回看");
  const option = card.getByRole("button", { name: /314159/ });
  await expect(option).toBeEnabled();

  const before = await api.get(`${BACKEND}/api/v1/quiz/recent`, {
    headers: auth,
  });
  expect(before.status()).toBe(200);
  const beforeRows = (await before.json()).questions as Array<Record<string, unknown>>;
  expect(beforeRows.length).toBeGreaterThan(0);
  expect(beforeRows.some((row) => row.evaluation_status === "unanswered")).toBeTruthy();

  await option.click();
  await card.getByRole("button", { name: /提交批改|Submit/ }).click();
  await expect(card.getByText(/回答正确|答对|正确/).first())
    .toBeVisible({ timeout: 30_000 });

  await expect(page).toHaveURL(/\/chat\/[^/]+$/);
  await page.reload();
  const restored = page.getByTestId("quiz-card").first();
  await expect(restored).toBeVisible({ timeout: 30_000 });
  await expect(restored.getByText(/回答正确|答对|正确/).first()).toBeVisible();

  const after = await api.get(`${BACKEND}/api/v1/quiz/recent`, {
    headers: auth,
  });
  expect(after.status()).toBe(200);
  const afterRows = (await after.json()).questions as Array<Record<string, unknown>>;
  expect(afterRows.some((row) => row.verdict === "correct")).toBeTruthy();

  await page.goto("/assessment");
  await expect(page.getByText("根据教材，ZX-17 定理的右端常数是多少？", { exact: true }).first())
    .toBeVisible({ timeout: 30_000 });
  await expect(page.locator('input[type="number"]').first()).toHaveValue("1");
  await api.dispose();
});
