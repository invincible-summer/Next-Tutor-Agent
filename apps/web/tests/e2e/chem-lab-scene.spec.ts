/**
 * 化学实验台场景（scene/）：几何锚点、材质真值与响应式/主题可用性。
 *
 * - 测试锚点并存：chem-lab-stage（外层）+ chem-lab-scene（SVG 视口）；
 * - 对象热区唯一且不重叠，可读名称锚定在对象上缘；
 * - 液体真值来自 RenderFrame：原液瓶有桃红液柱、空烧杯无残影；
 * - 窄屏（390×844）舞台高度 ≥320px 且底部面板可达；1024px 单列保主宽。
 */
import { test, expect } from "@playwright/test";
import { BACKEND, registerAndLogin, loginViaStorage } from "./support/helpers";

test("场景锚点并存且对象热区不重叠、名称贴对象上缘", async ({ page, request }) => {
  const account = await registerAndLogin(request);
  await loginViaStorage(page, account.token);
  const created = await request.post(`${BACKEND}/api/v1/tools/lab/chemistry/sessions`, {
    data: { experiment_id: "chem.dilution", mode: "guided", language: "zh", session_seed: 0 },
  });
  const snap = await created.json();
  await page.goto(`/tools/lab/chemistry?session=${snap.session_id}`);
  await expect(page.locator("[data-testid=chem-lab-stage]")).toBeVisible({ timeout: 20000 });
  await expect(page.locator("[data-testid=chem-lab-scene]")).toBeVisible();

  const report = await page.evaluate(() => {
    const objects = [...document.querySelectorAll<HTMLElement>('[data-testid^="chem-lab-object-"]')];
    const visible = objects.filter((el) => el.offsetWidth > 0 && el.offsetHeight > 0);
    const boxes = visible.map((el) => ({ id: el.dataset.testid!, rect: el.getBoundingClientRect() }));
    const overlaps: string[] = [];
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i].rect, b = boxes[j].rect;
        const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
        const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        if (x > 8 && y > 8) overlaps.push(`${boxes[i].id}×${boxes[j].id}`);
      }
    }
    const stock = document.querySelector<HTMLElement>('[data-testid="chem-lab-object-stock"]');
    const label = stock?.querySelector("span");
    const labelBox = label?.getBoundingClientRect();
    const anchored =
      stock && labelBox
        ? Math.abs(labelBox.top + labelBox.height / 2 - stock.getBoundingClientRect().top) < 16
        : false;
    return { count: visible.length, overlaps, anchored, tooSmall: visible.filter((el) => Math.min(el.offsetWidth, el.offsetHeight) < 40).length };
  });
  expect(report.count).toBeGreaterThanOrEqual(7);
  expect(report.overlaps).toHaveLength(0);
  expect(report.tooSmall).toBe(0);
  expect(report.anchored).toBe(true);
});

test("液体真值：原液瓶为桃红液柱，空烧杯不画液体", async ({ page, request }) => {
  const account = await registerAndLogin(request);
  await loginViaStorage(page, account.token);
  const created = await request.post(`${BACKEND}/api/v1/tools/lab/chemistry/sessions`, {
    data: { experiment_id: "chem.dilution", mode: "guided", language: "zh", session_seed: 0 },
  });
  const snap = await created.json();
  await page.goto(`/tools/lab/chemistry?session=${snap.session_id}`);
  await expect(page.locator("[data-testid=chem-lab-sync-status]")).toBeVisible({ timeout: 20000 });

  const liquids = await page.evaluate(() => {
    const read = (id: string) => {
      const g = document.querySelector(`[data-object-id="${id}"]`);
      if (!g) return null;
      const paths = [...g.querySelectorAll("path")];
      const liquid = paths.find((p) => {
        const fill = p.getAttribute("fill") ?? "none";
        return fill !== "none" && fill.includes("#") && parseFloat(p.getAttribute("opacity") ?? "1") > 0.3;
      });
      if (!liquid) return null;
      const box = g.getBoundingClientRect();
      const bb = liquid.getBBox();
      return { fill: liquid.getAttribute("fill"), pctOfNode: (bb.height / (box.height || 1)) * 100 };
    };
    return { stock: read("stock"), empty: read("beaker-b") };
  });
  // 桃红示踪原液：#fb7185，初始 40% 液位（帧真值 400‰）
  expect(liquids.stock?.fill?.toLowerCase()).toBe("#fb7185");
  expect(liquids.stock!.pctOfNode).toBeGreaterThan(25);
  // 空烧杯：任何液体路径都不应存在
  expect(liquids.empty).toBeNull();
});

test("响应式：390px 舞台高度≥320 且底部面板可达；1024px 单列保主宽", async ({ page, request }) => {
  const account = await registerAndLogin(request);
  await loginViaStorage(page, account.token);
  const created = await request.post(`${BACKEND}/api/v1/tools/lab/chemistry/sessions`, {
    data: { experiment_id: "chem.dilution", mode: "guided", language: "zh", session_seed: 0 },
  });
  const snap = await created.json();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/tools/lab/chemistry?session=${snap.session_id}`);
  await expect(page.locator("[data-testid=chem-lab-sync-status]")).toBeVisible({ timeout: 20000 });
  const phoneScene = await page.locator("[data-testid=chem-lab-scene]").boundingBox();
  expect(phoneScene?.height ?? 0).toBeGreaterThanOrEqual(320);
  await expect(page.locator("[data-testid=chem-lab-mobile-equipment]")).toBeVisible();
  await expect(page.locator("[data-testid=chem-lab-mobile-guidance]")).toBeVisible();

  await page.setViewportSize({ width: 1024, height: 768 });
  await page.waitForTimeout(400);
  const stageWidth = await page.evaluate(() => {
    const scene = document.querySelector('[data-testid="chem-lab-scene"]');
    const grid = scene?.closest(".grid");
    return grid ? getComputedStyle(grid).gridTemplateColumns.split(" ").length : 0;
  });
  expect(stageWidth).toBe(1); // 768–1279：侧栏折叠，舞台独占主宽
});

test("暗色主题：场景背景切换且对象仍可读", async ({ page, request }) => {
  const account = await registerAndLogin(request);
  await loginViaStorage(page, account.token);
  const created = await request.post(`${BACKEND}/api/v1/tools/lab/chemistry/sessions`, {
    data: { experiment_id: "chem.dilution", mode: "guided", language: "zh", session_seed: 0 },
  });
  const snap = await created.json();
  await page.goto(`/tools/lab/chemistry?session=${snap.session_id}`);
  await expect(page.locator("[data-testid=chem-lab-sync-status]")).toBeVisible({ timeout: 20000 });
  const lightBg = await page.evaluate(() =>
    getComputedStyle(document.querySelector('[data-testid="chem-lab-scene"]')!).backgroundColor);
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await page.waitForTimeout(200);
  const darkBg = await page.evaluate(() =>
    getComputedStyle(document.querySelector('[data-testid="chem-lab-scene"]')!).backgroundColor);
  expect(lightBg).not.toBe(darkBg);
  await expect(page.locator('[data-testid="chem-lab-object-stock"]')).toBeVisible();
});
