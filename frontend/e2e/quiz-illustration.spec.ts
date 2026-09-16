/** SVG question diagrams stay inside the shared structured question card. */
import { test, expect, request as pwRequest } from "@playwright/test";
import { loginViaStorage, registerAndLogin } from "./helpers";

test("聊天明确要求带插图：题图在结构化题卡内并可放大", async ({ page }) => {
  const api = await pwRequest.newContext();
  try {
    const learner = await registerAndLogin(api);
    await loginViaStorage(page, learner.token);
    await page.goto("/chat");

    const input = page.locator("textarea").first();
    await expect(input).toBeVisible({ timeout: 20_000 });
    await input.fill("请出一道带插图的物理题");
    await input.press("Enter");

    const card = page.getByTestId("quiz-card").first();
    await expect(card).toBeVisible({ timeout: 90_000 });
    const illustration = card.getByTestId("question-illustration").first();
    await expect(illustration).toBeVisible({ timeout: 30_000 });
    await expect(illustration.getByTestId("question-illustration-image")).toHaveJSProperty("naturalWidth", 640);
    const expand = illustration.getByTestId("question-illustration-expand");
    await expect(expand).toBeVisible();
    await expand.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
  } finally {
    await api.dispose();
  }
});

test("聊天模糊表达考我一下：仍进入结构化题卡", async ({ page }) => {
  const api = await pwRequest.newContext();
  try {
    const learner = await registerAndLogin(api);
    await loginViaStorage(page, learner.token);
    await page.goto("/chat");

    const input = page.locator("textarea").first();
    await expect(input).toBeVisible({ timeout: 20_000 });
    await input.fill("考我一下关于 ZX-17 定理的题");
    await input.press("Enter");

    const card = page.getByTestId("quiz-card").first();
    await expect(card).toBeVisible({ timeout: 90_000 });
    await expect(card).toContainText("ZX-17 定理");
    await expect(card.getByRole("button", { name: /314159/ })).toBeEnabled();
  } finally {
    await api.dispose();
  }
});

test("测评配置：插图选项并入习题生成卡且按钮可切换", async ({ page }) => {
  const api = await pwRequest.newContext();
  try {
    const learner = await registerAndLogin(api);
    await loginViaStorage(page, learner.token);
    await page.goto("/assessment");

    const options = page.getByTestId("assessment-illustration-options");
    await expect(options).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("assessment-illustration-options")).toHaveCount(1);
    const toggle = options.getByRole("button", { name: /已开启|已关闭|On|Off/ });
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
  } finally {
    await api.dispose();
  }
});
