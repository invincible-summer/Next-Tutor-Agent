import { expect, test, request as pwRequest } from "@playwright/test";
import { BACKEND, loginViaStorage, registerAndLogin, unique } from "./support/helpers";

test("个人SVG模板、图元编辑、上传、历史和AI草稿在深浅色桌面可用", async ({ page }) => {
  const api = await pwRequest.newContext();
  try {
    const user = await registerAndLogin(api);
    await loginViaStorage(page, user.token);
    await page.goto("/diagram-library?theme=light");
    await page.getByRole("button", { name: "我的素材", exact: true }).click();
    await page.getByTestId("new-material").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("combobox", { name: "范围", exact: true })).toContainText("我的素材");
    await expect(dialog.getByRole("combobox", { name: "范围", exact: true })).not.toContainText("公有素材");
    await dialog.getByRole("textbox", { name: "素材标题", exact: true }).fill("浏览器合成容器");
    await dialog.getByRole("textbox", { name: "素材使用说明", exact: true }).fill("只用于非定量容器构造，不添加读数。");
    await dialog.getByRole("combobox", { name: "设计模板", exact: true }).selectOption("beaker");
    await dialog.getByRole("button", { name: "检查并预览", exact: true }).click();
    await expect(dialog.getByTestId("material-preview").locator("img")).toHaveJSProperty("naturalWidth", 640);
    await dialog.getByRole("button", { name: "圆", exact: true }).click();
    await dialog.getByRole("textbox", { name: "element cx", exact: true }).fill("330");
    await dialog.getByRole("textbox", { name: "element r", exact: true }).fill("30");
    await dialog.getByRole("button", { name: "检查并预览", exact: true }).click();
    await expect(dialog.getByRole("textbox", { name: "SVG 源码", exact: true })).toHaveValue(/cx="330"/);
    await expect(dialog.getByRole("status")).toHaveText("预览已更新");
    await dialog.getByRole("checkbox", { name: "用于出题", exact: true }).check();
    await dialog.getByTestId("material-preview").scrollIntoViewIfNeeded();
    await page.screenshot({ path: "test-results/diagram-material-editor-light.png", animations: "disabled" });
    await dialog.getByRole("button", { name: "保存素材", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId("custom-material")).toHaveCount(1);
    await page.getByTestId("custom-material").click();
    await dialog.getByRole("textbox", { name: "素材标题", exact: true }).fill("修改后的合成容器");
    await dialog.getByRole("button", { name: "保存素材", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await page.getByTestId("custom-material").click();
    await expect(dialog.getByRole("combobox", { name: "历史版本", exact: true })).toContainText("v2");
    await dialog.getByRole("combobox", { name: "历史版本", exact: true }).selectOption("1");
    await expect(dialog.getByRole("textbox", { name: "素材标题", exact: true })).toHaveValue("浏览器合成容器");
    await expect(dialog.getByRole("textbox", { name: "素材使用说明", exact: true })).toHaveValue("只用于非定量容器构造，不添加读数。");
    await page.keyboard.press("Escape");
    await page.goto("/diagram-library?theme=dark");
    await page.getByRole("button", { name: "我的素材", exact: true }).click();
    await page.getByTestId("new-material").click();
    await dialog.getByRole("textbox", { name: "素材标题", exact: true }).fill("上传合成几何");
    await dialog.getByLabel("SVG 文件", { exact: true }).setInputFiles({ name: "circle.svg", mimeType: "image/svg+xml", buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><circle cx="320" cy="200" r="100" fill="none" stroke="#26364a"/></svg>') });
    await expect(dialog.getByTestId("material-preview").locator("img")).toHaveJSProperty("naturalWidth", 640);
    // Browser interaction double; separate live-provider acceptance exercises
    // production generation and question composition without mocks.
    await page.route("**/diagram-materials/generate", route => route.fulfill({ json: { svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><circle cx="330" cy="200" r="90" fill="none" stroke="#26364a"/></svg>', illustration: { kind: "svg", svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><circle cx="330" cy="200" r="90" fill="none" stroke="#26364a"/></svg>', width: 640, height: 400, alt: "合成AI草稿" } } }));
    await dialog.getByRole("textbox", { name: "AI 设计要求", exact: true }).fill("把圆缩小并向右移动");
    await dialog.getByRole("button", { name: "生成 / 修改草稿", exact: true }).click();
    await expect(dialog.getByRole("textbox", { name: "SVG 源码", exact: true })).toHaveValue(/r="90"/);
    await dialog.getByRole("textbox", { name: "element r", exact: true }).fill("80");
    await dialog.getByRole("button", { name: "检查并预览", exact: true }).click();
    await expect(dialog.getByRole("status")).toHaveText("预览已更新");
    await page.setViewportSize({ width: 1024, height: 900 });
    await expect.poll(() => dialog.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    await expect(page.locator("html")).toHaveClass(/dark/);
    await page.screenshot({ path: "test-results/diagram-material-editor-dark-narrow.png", animations: "disabled" });
    await dialog.getByRole("button", { name: "保存素材", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId("custom-material")).toHaveCount(2);
  } finally { await api.dispose(); }
});

test("管理员界面添加公有素材，普通用户可读历史但不能写入", async ({ page, browser }) => {
  const api = await pwRequest.newContext();
  const learnerContext = await browser.newContext();
  try {
    const auth = await api.post(`${BACKEND}/api/v1/auth/login`, {
      data: { email: "material-admin@e2e.example.com", password: "e2e-pass-123" },
    });
    expect(auth.status()).toBe(200);
    const admin = await auth.json();
    expect(admin.user.role).toBe("admin");
    await loginViaStorage(page, admin.token ?? admin.access_token);
    await page.goto("/diagram-library?theme=light");
    await page.getByTestId("new-material").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("combobox", { name: "范围", exact: true }).selectOption("public");
    const title = unique("公有合成几何");
    await dialog.getByRole("textbox", { name: "素材标题", exact: true }).fill(title);
    await dialog.getByRole("combobox", { name: "设计模板", exact: true }).selectOption("geometry");
    await dialog.getByRole("button", { name: "检查并预览", exact: true }).click();
    await dialog.getByRole("button", { name: "保存素材", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    const card = page.getByTestId("custom-material").filter({ hasText: title });
    await card.click();
    await dialog.getByRole("textbox", { name: "素材标题", exact: true }).fill(title + "第二版");
    await dialog.getByRole("button", { name: "保存素材", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    const learner = await registerAndLogin(api);
    const learnerPage = await learnerContext.newPage();
    await loginViaStorage(learnerPage, learner.token);
    await learnerPage.goto("/diagram-library?theme=dark");
    await expect(learnerPage.getByTestId("new-material")).toHaveCount(0);
    await learnerPage.getByTestId("custom-material").filter({ hasText: title + "第二版" }).click();
    const readonly = learnerPage.getByRole("dialog");
    await expect(readonly.getByRole("textbox", { name: "素材标题", exact: true })).toBeDisabled();
    await expect(readonly.getByRole("button", { name: "保存素材", exact: true })).toHaveCount(0);
    await readonly.getByRole("combobox", { name: "历史版本", exact: true }).selectOption("1");
    await expect(readonly.getByRole("textbox", { name: "素材标题", exact: true })).toHaveValue(title);
    await expect(readonly.getByRole("status")).toHaveText("预览已更新");
    await readonly.getByTestId("material-preview").scrollIntoViewIfNeeded();
    await learnerPage.screenshot({ path: "test-results/diagram-material-public-readonly-dark.png", animations: "disabled" });
    const forged = await api.post(`${BACKEND}/api/v1/diagram-materials`, {
      headers: { Authorization: `Bearer ${learner.token}` },
      data: { title: "不能发布", scope: "public", svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><circle cx="320" cy="200" r="80"/></svg>' },
    });
    expect(forged.status()).toBe(403);
    await learnerPage.keyboard.press("Escape");
    await learnerPage.getByRole("button", { name: "我的素材", exact: true }).click();
    await expect(learnerPage.getByTestId("custom-material")).toHaveCount(0);
  } finally { await learnerContext.close(); await api.dispose(); }
});


test("新素材的通用控件可改文字，预览值不改变模板默认值", async ({ page }) => {
  const api = await pwRequest.newContext();
  try {
    const user = await registerAndLogin(api);
    await loginViaStorage(page, user.token);
    await page.goto("/diagram-library?theme=light");
    await page.getByRole("button", { name: "我的素材", exact: true }).click();
    await page.getByTestId("new-material").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("textbox", { name: "素材标题", exact: true }).fill("可调文字流程");
    await dialog.getByRole("combobox", { name: "设计模板", exact: true }).selectOption("flow");
    await dialog.getByRole("textbox", { name: "preview parameter left_text", exact: true }).fill("过滤");
    await dialog.getByRole("textbox", { name: "preview parameter right_text", exact: true }).fill("回收");
    await dialog.getByRole("button", { name: "检查并预览", exact: true }).click();
    const src = await dialog.getByTestId("material-preview").locator("img").getAttribute("src");
    expect(decodeURIComponent(src ?? "")).toContain("过滤");
    await expect(dialog.getByRole("textbox", { name: "参数规范 JSON", exact: true })).toHaveValue(/过程一/);
    await dialog.getByTestId("material-parameters").scrollIntoViewIfNeeded();
    await page.screenshot({ path: "test-results/diagram-material-parameters-light.png", animations: "disabled" });
    await dialog.getByRole("button", { name: "保存素材", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await page.getByTestId("custom-material").click();
    await expect(dialog.getByRole("textbox", { name: "参数规范 JSON", exact: true })).toHaveValue(/left_text/);
    await page.setViewportSize({ width: 1024, height: 900 });
    await page.evaluate(() => document.documentElement.classList.add("dark"));
    await dialog.getByTestId("material-parameters").scrollIntoViewIfNeeded();
    await expect.poll(() => dialog.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    await page.screenshot({ path: "test-results/diagram-material-parameters-dark-narrow.png", animations: "disabled" });
  } finally { await api.dispose(); }
});
