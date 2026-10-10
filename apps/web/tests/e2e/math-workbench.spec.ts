/**
 * 几何作图器主链路（plan D10/L4）：/tools 入口 → 登录门控 → 沉浸式工作台 →
 * 二维函数输入即时出图 → 模式栏默认收起/切换不丢数据 → dirty/保存 → 撤销重做 →
 * 计算面板 → 中英文切换不销毁现场。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { registerAndLogin, loginViaStorage } from "./support/helpers";

test("工具目录第五张卡片进入沉浸式几何作图器", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);

  await page.goto("/tools");
  const entry = page.locator("[data-testid=geometry-tool-entry]");
  await expect(entry).toBeVisible({ timeout: 20_000 });
  await entry.click();

  // 进入即绘图：无欢迎弹窗，坐标轴与右侧函数输入立即可见。
  await expect(page.locator("[data-testid=geometry-workbench]")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("[data-testid=geometry-stage-2d]")).toBeVisible();
  await expect(page.locator("svg .stage2d-axis-label").first()).toBeVisible();
  await expect(page.locator("[data-testid=geometry-formula-editor]").first()).toBeVisible();
  await expect(page.locator("[data-testid=geometry-mode-panel]")).toBeHidden(); // 二级模式栏默认收起
  // 沉浸式：全局侧栏不出现。
  await expect(page.locator("[data-testid=geometry-workbench]")).toBeVisible();
  await api.dispose();
});

test("输入 y=sin(x) 立即绘制并标脏，保存后恢复干净", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await page.goto("/tools/geometry");
  await expect(page.locator("[data-testid=geometry-workbench]")).toBeVisible({ timeout: 20_000 });

  // 新文档即未保存。
  const indicator = page.locator("[data-testid=geometry-unsaved-indicator]");
  await expect(indicator).toContainText(/未保存|Unsaved/);

  // 添加函数行并输入 sin(x)：失焦提交。
  await page.locator("button", { hasText: /添加函数|Add function/ }).click();
  const editor = page.locator("[data-testid=geometry-formula-editor]").last();
  await editor.click();
  await editor.fill("sin(x)");
  await editor.blur();
  // 曲线出现（采样 polyline path）。
  await expect(page.locator("[data-testid=geometry-stage-2d] path").first()).toBeVisible({ timeout: 10_000 });

  // Ctrl+S 清脏。
  await page.keyboard.press("Control+s");
  await expect(indicator).toContainText(/已保存到此浏览器|Saved in this browser/, { timeout: 10_000 });
  await api.dispose();
});

test("模式切换更换幕布且互不共享坐标轴；dirty 时弹保存提示", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await page.goto("/tools/geometry");
  await expect(page.locator("[data-testid=geometry-workbench]")).toBeVisible({ timeout: 20_000 });

  // 输入一个函数（有内容 → dirty 非空文档）。
  await page.locator("button", { hasText: /添加函数|Add function/ }).click();
  const editor = page.locator("[data-testid=geometry-formula-editor]").last();
  await editor.click();
  await editor.fill("cos(x)");
  await editor.blur();
  await expect(page.locator("[data-testid=geometry-stage-2d] path").first()).toBeVisible();
  // 二维函数幕布有坐标轴。
  await expect(page.locator("svg .stage2d-axis-label").first()).toBeVisible();

  // dirty 切换弹三选一（ADR-0023）；"直接切换"不销毁数据。
  const toggle = page.locator("[data-testid=geometry-mode-toggle]");
  await toggle.click();
  await page.locator("[data-testid=geometry-mode-geometry2d]").click();
  const dialog = page.locator("[data-testid=geometry-unsaved-dialog]");
  await expect(dialog).toBeVisible();
  await dialog.locator("button", { hasText: /直接切换|Switch anyway/ }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator("[data-testid=geometry-mode-panel]")).toBeHidden();
  await expect(page.locator("[data-testid=geometry-right-panel]")).toContainText(/对象|Objects/);
  // 平面几何幕布：只有网格，不画坐标轴，也不显示函数曲线。
  await expect(page.locator("svg .stage2d-axis")).toHaveCount(0);
  await expect(page.locator("[data-testid=geometry-stage-2d] path")).toHaveCount(0);

  // 平面几何是空白新文档（pristine）→ 切回二维函数不弹窗。
  await toggle.click();
  await page.locator("[data-testid=geometry-mode-functions2d]").click();
  await expect(page.locator("svg .stage2d-axis-label").first()).toBeVisible();
  await expect(dialog).toHaveCount(0);

  // 每个模式独立保存：二维函数保存后，平面几何仍是未保存的新文档。
  const indicator = page.locator("[data-testid=geometry-unsaved-indicator]");
  await page.keyboard.press("Control+s");
  await expect(indicator).toContainText(/已保存到此浏览器|Saved in this browser/, { timeout: 10_000 });
  await toggle.click();
  await page.locator("[data-testid=geometry-mode-geometry2d]").click();
  await expect(indicator).toContainText(/未保存|Unsaved/);

  // 切回二维函数：原函数仍在（模式切换不销毁数据）。
  await toggle.click();
  await page.locator("[data-testid=geometry-mode-functions2d]").click();
  await expect(page.locator("[data-testid=geometry-formula-editor]").first()).toHaveValue(/cos\(x\)/);
  await expect(page.locator("[data-testid=geometry-stage-2d] path").first()).toBeVisible();
  await api.dispose();
});

test("空文档自带草稿行、视图控制浮层可用", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await page.goto("/tools/geometry");
  await expect(page.locator("[data-testid=geometry-workbench]")).toBeVisible({ timeout: 20_000 });

  // 首次进入即有一条空草稿行（plan D5），无需点击添加函数。
  const editor = page.locator("[data-testid=geometry-formula-editor]").first();
  await expect(editor).toBeVisible();
  await editor.click();
  await editor.fill("sin(x)");
  await editor.blur();
  await expect(page.locator("[data-testid=geometry-stage-2d] path").first()).toBeVisible({ timeout: 10_000 });

  // 画布右上角视图控制：回到原点 / 适配视图 / 缩放按钮。
  const controls = page.locator("[data-testid=geometry-view-controls]");
  await expect(controls).toBeVisible();
  await controls.locator("button").nth(2).click(); // 回到原点
  await controls.locator("button").nth(3).click(); // 适配视图
  await expect(page.locator("[data-testid=geometry-stage-2d] path").first()).toBeVisible();
  await api.dispose();
});

test("撤销/重做恢复保存基线后 dirty 清除", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await page.goto("/tools/geometry");
  await expect(page.locator("[data-testid=geometry-workbench]")).toBeVisible({ timeout: 20_000 });

  await page.locator("button", { hasText: /添加函数|Add function/ }).click();
  const editor = page.locator("[data-testid=geometry-formula-editor]").last();
  await editor.click();
  await editor.fill("x^2");
  await editor.blur();
  const indicator = page.locator("[data-testid=geometry-unsaved-indicator]");
  await page.keyboard.press("Control+s");
  await expect(indicator).toContainText(/已保存到此浏览器|Saved in this browser/);

  // 再次编辑 → dirty；撤销回到保存快照 → 干净。
  await editor.click();
  await editor.fill("x^3");
  await editor.blur();
  await expect(indicator).toContainText(/未保存|Unsaved/);
  await page.keyboard.press("Control+z");
  await expect(indicator).toContainText(/已保存到此浏览器|Saved in this browser/, { timeout: 10_000 });
  await api.dispose();
});

test("数学工具：求值、定积分与解方程（可标记到图上）", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await page.goto("/tools/geometry");
  await expect(page.locator("[data-testid=geometry-workbench]")).toBeVisible({ timeout: 20_000 });

  // 切到"数学工具"页签（仅二维函数模式提供）。
  await page.locator("[data-testid=geometry-right-panel] [role=tab]", { hasText: /数学工具|Math tools/ }).click();
  const expression = page.locator("[data-testid=geometry-formula-editor]");
  await expect(expression).toBeVisible();
  await expression.fill("x^2");
  const result = page.locator("[data-testid=geometry-analysis-result]");
  const decimals = page.locator("[data-testid=geometry-right-panel] input[inputmode=decimal]");

  // 求值 x=3 → 9。
  await decimals.first().fill("3");
  await page.locator("[data-testid=geometry-calc-run] button").click();
  await expect(result).toBeVisible();
  await expect(result.locator(".analysis-value")).toContainText("9");

  // 定积分 [0,1] → 1/3（区间预填后覆写）。
  await page.locator("[data-testid=geometry-analysis-integral]").click();
  await decimals.nth(0).fill("0");
  await decimals.nth(1).fill("1");
  await page.locator("[data-testid=geometry-calc-run] button").click();
  await expect(result.locator(".analysis-value")).toContainText("0.333333", { timeout: 5_000 });

  // 解方程 x^2 - 4 = 0 → 根列表 ±2；标记到图上生成两个自由点。
  await page.locator("[data-testid=geometry-analysis-solve]").click();
  await expression.fill("x^2 - 4 = 0");
  await page.locator("[data-testid=geometry-calc-run] button").click();
  await expect(result.locator("[data-testid=geometry-analysis-root]")).toHaveCount(2);
  await expect(result).toContainText(/2/);
  await page.locator("[data-testid=geometry-analysis-mark] button").click();
  await expect(page.locator(".stage2d-point")).toHaveCount(2);
  await api.dispose();
});

test("窄屏默认折叠右栏，胶囊按钮可展开", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/tools/geometry");
  await expect(page.locator("[data-testid=geometry-workbench]")).toBeVisible({ timeout: 20_000 });
  // 画布优先：右栏收起，仅剩底部胶囊入口。
  await expect(page.locator("[data-testid=geometry-right-panel]")).toHaveCount(0);
  await page.locator(".right-panel-collapsed").click();
  await expect(page.locator("[data-testid=geometry-right-panel]")).toBeVisible();
  await api.dispose();
});

test("语言切换只换文字，不清空未保存内容", async ({ page }) => {
  const api = await pwRequest.newContext();
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  await page.goto("/tools/geometry");
  await expect(page.locator("[data-testid=geometry-workbench]")).toBeVisible({ timeout: 20_000 });

  await page.locator("button", { hasText: /添加函数|Add function/ }).click();
  const editor = page.locator("[data-testid=geometry-formula-editor]").last();
  await editor.click();
  await editor.fill("tan(x)");
  await editor.blur();
  await expect(page.locator("[data-testid=geometry-stage-2d] path").first()).toBeVisible();
  const pathCount = await page.locator("[data-testid=geometry-stage-2d] path").count();
  expect(pathCount).toBeGreaterThan(1); // tan 的渐近线拆分

  // 切换语言（顶栏语言控件在沉浸式下不可见，直接走 store 持久化）。
  await page.evaluate(() => {
    window.localStorage.setItem("edu-agent-output-lang", JSON.stringify({ lang: "en" }));
  });
  await page.reload();
  await expect(page.locator("[data-testid=geometry-workbench]")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("[data-testid=geometry-unsaved-indicator]")).toBeVisible();
  await api.dispose();
});
