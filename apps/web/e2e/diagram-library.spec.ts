import { test, expect, request as pwRequest } from "@playwright/test";
import { loginViaStorage, registerAndLogin } from "./helpers";

test("全部素材可分页观看，筛选搜索与参数预览可用", async ({ page }) => {
  test.setTimeout(300_000);
  const api = await pwRequest.newContext();
  try {
    const user = await registerAndLogin(api);
    await loginViaStorage(page, user.token);
    const firstResponse = page.waitForResponse(response => response.url().includes("/diagram-assets?") && new URL(response.url()).searchParams.get("page") === "0");
    await page.goto("/diagram-library?theme=light");
    const expectedCount = (await (await firstResponse).json()).catalog_total;
    await expect(page.getByTestId("diagram-library").getByRole("heading", { name: "教学素材库", exact: true })).toBeVisible();
    const cards = page.getByTestId("diagram-asset");
    await expect(cards).toHaveCount(12);
    await expect.poll(() => cards.locator("img").evaluateAll(images => images.every(image => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    await expect.poll(() => page.getByTestId("diagram-library").evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
    await page.getByTestId("diagram-library").evaluate(element => { element.scrollTop = 720; });
    await expect.poll(() => page.getByTestId("diagram-library").evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    await page.getByTestId("diagram-library").evaluate(element => { element.scrollTop = 0; });
    await page.screenshot({ path: "test-results/diagram-library-light.png", fullPage: true, animations: "disabled" });
    const seen = new Set<string>();
    for (let index=0; ; index++) {
      await expect.poll(() => cards.locator("img").evaluateAll(images => images.every(image => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
      for (const id of await cards.evaluateAll(elements => elements.map(element => element.getAttribute("data-asset-id")!))) {
        expect(seen.has(id)).toBe(false);
        seen.add(id);
      }
      const next = page.getByRole("button", { name: "下一页", exact: true });
      if (await next.isDisabled()) break;
      const response = page.waitForResponse(response => response.url().includes(`/diagram-assets?`) && new URL(response.url()).searchParams.get("page") === String(index+1));
      const before = await cards.first().getAttribute("data-asset-id");
      await next.click(); await response;
      await expect(cards.first()).not.toHaveAttribute("data-asset-id", before!);
      await expect(page.getByRole("textbox", { name: "页码", exact: true })).toHaveValue(String(index+2));
      await expect(page.locator("section[aria-busy=true]")).toHaveCount(0);
    }
    expect(seen.size).toBe(expectedCount);
    expect(seen.size).toBe(1079);
    const subject = page.getByRole("combobox", { name: "学科", exact: true });
    const subjectValues = await subject.locator("option").evaluateAll(options => options.map(option => (option as HTMLOptionElement).value).filter(Boolean));
    expect(subjectValues).toHaveLength(17);
    for (const value of subjectValues) {
      const response = page.waitForResponse(row => row.url().includes("/diagram-assets?") && new URL(row.url()).searchParams.get("subject") === value);
      await subject.selectOption(value);
      const body = await (await response).json();
      expect(body.total).toBeGreaterThan(0);
      expect(body.items.every((item: {subjects: string[]}) => item.subjects.includes(value))).toBe(true);
      await expect(page.locator("section[aria-busy=true]")).toHaveCount(0);
    }
    const filteredResponse = page.waitForResponse(response => response.url().includes("/diagram-assets?") && new URL(response.url()).searchParams.get("subject") === "mathematics");
    await page.getByRole("combobox", { name: "学科", exact: true }).selectOption("mathematics");
    await filteredResponse;
    await expect(cards).toHaveCount(12);
    const geometryResponse = page.waitForResponse(response => response.url().includes("/diagram-assets?") && new URL(response.url()).searchParams.get("family") === "geometry");
    await page.getByRole("combobox", { name: "素材类型", exact: true }).selectOption("geometry");
    await geometryResponse;
    await expect(cards.first()).toHaveAttribute("data-asset-id", "geometry.prism");
    expect(await cards.evaluateAll(elements => elements.every(element => element.getAttribute("data-asset-id")?.startsWith("geometry.")))).toBe(true);
    await page.getByRole("combobox", { name: "学科", exact: true }).selectOption("");
    await page.getByRole("combobox", { name: "素材类型", exact: true }).selectOption("");
    await page.getByRole("textbox", { name: "搜索素材", exact: true }).fill("beakr");
    await expect(cards.first()).toHaveAttribute("data-asset-id", "vessel.beaker");
    await cards.first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveCSS("opacity", "1");
    await expect(dialog.getByRole("button", { name: "更新预览" })).toBeEnabled();
    const oldSource = await dialog.locator("img").getAttribute("src");
    await dialog.getByRole("spinbutton", { name: "液面高度占比" }).fill("0.7");
    await dialog.getByRole("button", { name: "更新预览" }).click();
    await expect.poll(() => dialog.locator("img").getAttribute("src")).not.toBe(oldSource);
    await expect(dialog.locator("img")).toHaveJSProperty("naturalWidth", 320);
    const coloredSource = await dialog.locator("img").getAttribute("src");
    await dialog.getByRole("combobox", { name: "图示风格", exact: true }).selectOption("monochrome");
    await dialog.getByRole("button", { name: "更新预览" }).click();
    await expect.poll(() => dialog.locator("img").getAttribute("src")).not.toBe(coloredSource);
    await dialog.getByRole("button", { name: "恢复示例" }).click();
    await expect(dialog.getByRole("spinbutton", { name: "液面高度占比" })).toHaveValue("0.45");
    await expect(dialog.getByRole("combobox", { name: "图示风格", exact: true })).toHaveValue("textbook");
    await expect(dialog.getByTestId("asset-origin")).toHaveText("项目原创 SVG");
    await dialog.getByRole("button", { name: "放大图示", exact: true }).click();
    await expect(dialog).toContainText("150%");
    await page.screenshot({ path: "test-results/diagram-library-detail.png", fullPage: true, animations: "disabled" });
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);

    await page.goto("/diagram-library?theme=dark");
    await expect(cards).toHaveCount(12);
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(cards.first().locator("img")).toHaveCSS("filter", "none");
    await expect.poll(() => cards.locator("img").evaluateAll(images => images.every(image => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    await page.screenshot({ path: "test-results/diagram-library-dark.png", fullPage: true, animations: "disabled" });
    await page.setViewportSize({ width: 1024, height: 900 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await cards.first().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCSS("opacity", "1");
    await expect(page.getByRole("dialog")).toHaveCSS("background-color", "rgb(24, 27, 33)");
    await expect.poll(() => page.getByRole("dialog").evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await expect(page.getByRole("dialog").getByRole("button", { name: "更新预览" })).toBeEnabled();
    await page.screenshot({ path: "test-results/diagram-library-narrow.png", fullPage: true, animations: "disabled" });
  } finally { await api.dispose(); }
});
