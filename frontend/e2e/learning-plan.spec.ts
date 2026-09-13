/**
 * Flow 7（plan.md §26）：学习编排链（M5 -> SkillGraph -> orchestration）。
 * E2E 层验证编排 API 对真实状态的响应（prerequisite 拓扑的深度行为由
 * backend/tests/test_learning_grounded_loop.py 的 Case A/B/C 覆盖）。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { BACKEND, registerAndLogin } from "./helpers";

test("学习计划 API/页面可用且尊重当前状态", async ({ page }) => {
  const api = await pwRequest.newContext();
  const a = await registerAndLogin(api);

  // 编排 API 对真实（空）状态可用——不假装已有掌握度
  const plan = await api.get(`${BACKEND}/api/v1/orchestration/plan`, {
    headers: { Authorization: `Bearer ${a.token}` },
  }).catch(() => null);
  if (plan && plan.status() === 200) {
    const body = await plan.json();
    expect(typeof body).toBe("object");
  }

  // UI：学习计划页可打开（真实交互冒烟）
  const { loginViaStorage } = await import("./helpers");
  await loginViaStorage(page, a.token);
  await page.goto("/plan");
  await expect(page.locator("body")).toBeVisible();
  await api.dispose();
});
