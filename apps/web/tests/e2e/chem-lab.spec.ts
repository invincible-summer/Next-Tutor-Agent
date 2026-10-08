/**
 * 模拟实验室（化学实验台）：目录 → 准备页 → 开始实验 → 实验台全链路。
 *
 * 回归锚点：准备页曾因两个加载 effect 共享一个 generation 计数器而永久
 * 停在"正在创建会话…"加载态（detail 结果被 session effect 的计数顶掉，
 * finally 里的复位被守卫跳过）——?experiment= 深链必现。此 spec 钉死：
 * 准备页必须在超时内真实渲染，且实验台可进入。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { BACKEND, registerAndLogin, loginViaStorage } from "./support/helpers";

test("实验目录可见且可进入任意实验的准备页与实验台", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);

  // 目录： shipped 内容包全部上架（6 个原创合成实验）。
  await page.goto("/tools/lab");
  const cards = page.locator("[data-testid^=chem-lab-experiment-]");
  await expect(cards.first()).toBeVisible({ timeout: 20_000 });
  expect(await cards.count()).toBeGreaterThanOrEqual(6);

  // 准备页：不再卡在“正在创建会话…”整屏加载态。
  await cards.first().click();
  await expect(page.locator("[data-testid=chem-lab-preparation]"))
    .toBeVisible({ timeout: 15_000 });
  await expect(page.locator("[data-testid=chem-lab-start]")).toBeVisible();

  // 开始实验：会话创建成功并进入实验台（时间轴/同步状态为台面标记）。
  await page.locator("[data-testid=chem-lab-start]").click();
  await page.waitForURL(/\/tools\/lab\/chemistry\?session=/, { timeout: 30_000 });
  await expect(page.locator("[data-testid=chem-lab-sync-status]"))
    .toBeVisible({ timeout: 20_000 });
  await expect(page.locator("main svg").first()).toBeVisible();
  await api.dispose();
});
