/**
 * 文件与离页守卫（plan D7/D8/L3）：三选一弹窗的保存并继续 / 放弃更改 / 取消，
 * 打开另一文档的守卫，存储写入失败时停留原地，刷新触发原生 beforeunload。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { registerAndLogin, loginViaStorage } from "./support/helpers";

async function openWorkbench(page: import("@playwright/test").Page, withEdit: boolean): Promise<void> {
  await page.goto("/tools/geometry");
  await expect(page.locator("[data-testid=geometry-workbench]")).toBeVisible({ timeout: 20_000 });
  if (withEdit) {
    await page.locator("button", { hasText: /添加函数|Add function/ }).click();
    const editor = page.locator("[data-testid=geometry-formula-editor]").last();
    await editor.click();
    await editor.fill("sin(x)");
    await editor.blur();
    await expect(page.locator("[data-testid=geometry-stage-2d] path").first()).toBeVisible();
  }
}

test("取消：弹窗关闭且停留在当前页面", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await openWorkbench(page, true);

  await page.locator("[data-testid=geometry-file-new]").click();
  const dialog = page.locator("[data-testid=geometry-unsaved-dialog]");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: /取消|Cancel/ }).click();
  await expect(dialog).toBeHidden();
  // 仍在作图器，编辑内容未丢。
  await expect(page.locator("[data-testid=geometry-formula-editor]").first()).toHaveValue(/sin\(x\)/);
  await api.dispose();
});

test("放弃更改：直接执行导航目标", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await openWorkbench(page, true);

  await page.locator("[data-testid=geometry-file-new]").click();
  const dialog = page.locator("[data-testid=geometry-unsaved-dialog]");
  await dialog.getByRole("button", { name: /放弃更改|Discard changes/ }).click();
  await expect(dialog).toBeHidden();
  // 新建后是空白未保存文档（函数行被替换）。
  await expect(page.locator("[data-testid=geometry-unsaved-indicator]")).toContainText(/未保存|Unsaved/);
  await api.dispose();
});

test("保存并继续：写入成功后执行目标并清脏", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await openWorkbench(page, true);

  await page.locator("[data-testid=geometry-file-new]").click();
  const dialog = page.locator("[data-testid=geometry-unsaved-dialog]");
  await dialog.getByRole("button", { name: /保存并继续|Save and continue/ }).click();
  // 首存需要名称：命名后保存并继续。
  const nameInput = page.locator(".modal input, [role=dialog] input").last();
  if (await nameInput.isVisible().catch(() => false)) {
    await nameInput.fill("E2E 守卫绘图");
    await page.locator("[role=dialog] button, .modal button").last().click();
  }
  await expect(dialog).toBeHidden({ timeout: 10_000 });
  await expect(page.locator("[data-testid=geometry-unsaved-indicator]")).toContainText(/未保存|Unsaved/); // 新建的目标文档
  await api.dispose();
});

test("存储写入失败：弹窗保持打开且不跳转", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  // 让 localStorage 写入抛 QuotaExceededError。
  await page.addInitScript(() => {
    const original = window.localStorage.setItem.bind(window.localStorage);
    window.localStorage.setItem = (key: string, value: string) => {
      if (key.startsWith("next-tutor.math-workbench")) {
        const error = new Error("quota");
        error.name = "QuotaExceededError";
        throw error;
      }
      return original(key, value);
    };
  });
  await openWorkbench(page, true);

  await page.locator("[data-testid=geometry-file-new]").click();
  const dialog = page.locator("[data-testid=geometry-unsaved-dialog]");
  await dialog.getByRole("button", { name: /保存并继续|Save and continue/ }).click();
  const nameInput = page.locator(".modal input, [role=dialog] input").last();
  if (await nameInput.isVisible().catch(() => false)) {
    await nameInput.fill("配额失败");
    await page.locator("[role=dialog] button, .modal button").last().click();
  }
  // 保存失败：三选一弹窗保持打开并显示失败，绝不执行新建。
  await expect(dialog).toBeVisible({ timeout: 10_000 });
  await expect(dialog).toContainText(/保存失败|Save failed|表达式|Invalid/);
  await api.dispose();
});

test("打开绘图列表可再打开已存档文档", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await openWorkbench(page, true);

  // 先保存一份。
  await page.keyboard.press("Control+s");
  await expect(page.locator("[data-testid=geometry-unsaved-indicator]")).toContainText(/已保存到此浏览器|Saved in this browser/, { timeout: 10_000 });

  // 编辑后再打开列表：浏览列表本身不破坏现场，直接弹出文件管理器。
  const editor = page.locator("[data-testid=geometry-formula-editor]").first();
  await editor.click();
  await editor.fill("cos(x)");
  await editor.blur();
  await page.locator("[data-testid=geometry-file-open]").click();
  const list = page.getByRole("dialog", { name: /打开绘图|Open drawing/ });
  await expect(list).toBeVisible();

  // 从列表打开刚才保存的文档：此时出现三选一守卫。
  await list.locator("[role=listitem] button", { hasText: /^打开$|^Open$/ }).first().click();
  const dialog = page.locator("[data-testid=geometry-unsaved-dialog]");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: /放弃更改|Discard changes/ }).click();
  // 放弃更改后恢复为已存档版本（sin(x)），而不是未保存的 cos(x)。
  await expect(page.locator("[data-testid=geometry-formula-editor]").first()).toHaveValue(/sin\(x\)/);
  await api.dispose();
});

test("dirty 时刷新触发原生 beforeunload 确认", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await openWorkbench(page, true);

  let dialogShown = false;
  page.once("dialog", (dialog) => {
    dialogShown = true;
    void dialog.dismiss();
  });
  await page.reload({ timeout: 15_000 }).catch(() => undefined);
  expect(dialogShown).toBe(true);
  await api.dispose();
});
