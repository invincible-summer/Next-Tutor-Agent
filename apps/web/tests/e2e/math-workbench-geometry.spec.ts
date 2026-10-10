/**
 * 平面几何构造链路（ADR-0023）：线段两击构造、已有点复用（不重复建点）、
 * Esc 取消进行中的构造、Delete 级联删除、单条撤销恢复。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { registerAndLogin, loginViaStorage } from "./support/helpers";

test("线段构造复用已有点；Esc 取消；Delete 级联删除且单条撤销", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await page.goto("/tools/geometry");
  await expect(page.locator("[data-testid=geometry-workbench]")).toBeVisible({ timeout: 20_000 });

  // 切到平面几何：空白新文档（pristine）不弹保存提示（ADR-0023）。
  await page.locator("[data-testid=geometry-mode-toggle]").click();
  await page.locator("[data-testid=geometry-mode-geometry2d]").click();
  await expect(page.locator("[data-testid=geometry-mode-panel]")).toBeHidden();
  await expect(page.locator("[data-testid=geometry-unsaved-dialog]")).toHaveCount(0);
  await expect(page.locator("[data-testid=geometry-right-panel]")).toContainText(/对象|Objects/);

  const stage = page.locator("[data-testid=geometry-stage-2d]");
  const box = await stage.boundingBox();
  expect(box).toBeTruthy();
  const rows = page.locator("[data-testid=geometry-object-row]");
  await expect(rows).toHaveCount(0);

  // 线段工具两击构造：2 个点 + 1 条线段 = 3 个对象（单个 batch）。
  await page.locator("[data-testid=geometry-tool-line]").click();
  await page.mouse.click(box!.x + box!.width * 0.35, box!.y + box!.height * 0.4);
  await page.mouse.click(box!.x + box!.width * 0.65, box!.y + box!.height * 0.4);
  await expect(rows).toHaveCount(3);
  await expect(page.locator("[data-testid=geometry-stage-2d] line.stage2d-shape")).toHaveCount(1);

  // 第二条线段从已有点出发：复用端点、不产生重复点（+1 点 +1 线段 = 5）。
  await page.mouse.click(box!.x + box!.width * 0.65, box!.y + box!.height * 0.4); // 命中已有点
  await page.mouse.click(box!.x + box!.width * 0.5, box!.y + box!.height * 0.7);
  await expect(rows).toHaveCount(5);

  // Esc 取消进行中的构造（起点点击不产生对象）。
  await page.mouse.click(box!.x + box!.width * 0.3, box!.y + box!.height * 0.6);
  await page.keyboard.press("Escape");
  await page.mouse.click(box!.x + box!.width * 0.8, box!.y + box!.height * 0.6);
  await page.keyboard.press("Escape");
  await expect(rows).toHaveCount(5);

  // 选择工具点选端点 → Delete 级联删除（该点 + 依赖它的线段）。
  await page.locator("[data-testid=geometry-tool-select]").click();
  await page.mouse.click(box!.x + box!.width * 0.35, box!.y + box!.height * 0.4);
  await page.keyboard.press("Delete");
  await expect(rows).toHaveCount(3);
  // 撤销一次即恢复（batch 是单条历史记录）。
  await page.keyboard.press("Control+z");
  await expect(rows).toHaveCount(5);
  await api.dispose();
});
