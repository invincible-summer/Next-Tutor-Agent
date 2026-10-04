/**
 * Flow 7：学习编排链（M5 -> SkillGraph -> orchestration）。
 * E2E 层验证编排 API 对真实状态的响应（prerequisite 拓扑的深度行为由
 * services/api/tests/agents/learning_orchestration/ 的后端单测覆盖）。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { BACKEND, registerAndLogin, loginViaStorage } from "./support/helpers";

test("学习计划 API/页面可用且尊重当前状态", async ({ page }) => {
  const api = await pwRequest.newContext();
  const a = await registerAndLogin(api);

  try {
    // API failures must fail the test; a fresh account has no invented goals.
    const plan = await api.get(`${BACKEND}/api/v1/orchestration/plan`, {
      timeout: 10_000, headers: { Authorization: `Bearer ${a.token}` },
    });
    expect(plan.status()).toBe(200);
    const body = await plan.json();
    expect(body.student_id).toBe(a.userId);
    expect(body.goals).toEqual([]);
    expect(body.weekly_plan).toEqual([]);
    expect(body.needs_replan).toBe(false);

    await loginViaStorage(page, a.token);
    await page.goto("/plan");
    await expect(page).toHaveURL(/\/knowledge$/);
    await expect(page.getByRole("main").getByRole("heading", { name: "学习计划", exact: true })).toBeVisible();
  } finally {
    await api.dispose();
  }
});
