/**
 * 化学模拟实验台（3D）：验证同一套文档内核、AUTO 基线、就地气泡和器材架取用。
 * 这组用例不验证“答对/答错”，因为工作台是自由探索玩具；只验证可操作的空间反馈。
 */
import { test, expect } from "@playwright/test";
import { loginViaStorage } from "./support/helpers";

type ChemPoint = { x: number; y: number };

async function enterBench(page: import("@playwright/test").Page, stage = "silver-condenser"): Promise<void> {
  await page.goto(`/tools/lab/chemistry?stage=${stage}`);
  await page.waitForFunction(() => Boolean((window as any).__chemLabDebug?.doc), null, { timeout: 20_000 });
}

async function projectEquipment(page: import("@playwright/test").Page, id: string, y = 1): Promise<ChemPoint> {
  return page.evaluate(({ instanceId, localY }) => {
    const debug = (window as any).__chemLabDebug;
    const group = debug.sync.equipmentGroup(instanceId);
    const v = group.localToWorld(new (group.position.constructor as any)(0, localY, 0));
    v.project(debug.controller.camera);
    const rect = debug.controller.renderer.domElement.getBoundingClientRect();
    return { x: (v.x + 1) * rect.width / 2 + rect.left, y: (1 - v.y) * rect.height / 2 + rect.top };
  }, { instanceId: id, localY: y });
}

test.describe("化学模拟实验台 3D", () => {
  test.beforeEach(async ({ page }) => {
    await loginViaStorage(page, "e2e-fake-token");
  });

  test("目录提供六个主题，并进入自由探索台", async ({ page }) => {
    await page.goto("/tools/lab");
    await expect(page.locator("[data-testid=chem-lab-catalog]")).toBeVisible();
    await expect(page.locator("[data-testid^=chem-lab-stage-]")).toHaveCount(6);
    await page.locator("[data-testid=chem-lab-stage-free-explore]").click();
    await expect(page).toHaveURL(/stage=free-explore/);
    await expect(page.locator(".chem-hud")).toContainText(/自由探索|Free Explore/);
  });

  test("AUTO 载入装置，点击器材出现就地气泡", async ({ page }) => {
    await enterBench(page);
    await page.getByRole("button", { name: /AUTO/i }).click();
    await page.waitForTimeout(250);

    const before = await page.evaluate(() => (window as any).__chemLabDebug.doc().equipment.length);
    expect(before).toBeGreaterThan(5);
    // Horizontal condenser uses its geometric center as the local origin.
    const point = await projectEquipment(page, "cond-1", 0);
    await page.mouse.click(point.x, point.y);
    await expect(page.locator(".chem-popover")).toContainText(/蛇形冷凝器|Coil condenser/);
    await expect(page.locator(".chem-popover")).toContainText(/转动方向|Rotate/);
    await expect(page.locator("[class*=assistant]")).toHaveCount(0);
  });

  test("点击后方器材架会把缩小模型取到台面", async ({ page }) => {
    await enterBench(page);
    const before = await page.evaluate(() => (window as any).__chemLabDebug.doc().equipment.length);
    const point = await page.evaluate(() => {
      const debug = (window as any).__chemLabDebug;
      const mesh = debug.sync.rackPickMeshes()[0];
      const v = mesh.getWorldPosition(new (mesh.position.constructor as any)());
      v.project(debug.controller.camera);
      const rect = debug.controller.renderer.domElement.getBoundingClientRect();
      return { x: (v.x + 1) * rect.width / 2 + rect.left, y: (1 - v.y) * rect.height / 2 + rect.top };
    });
    await page.mouse.click(point.x, point.y);
    await expect.poll(() => page.evaluate(() => (window as any).__chemLabDebug.doc().equipment.length)).toBe(before + 1);
  });
});
