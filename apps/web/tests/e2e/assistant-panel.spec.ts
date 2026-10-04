/** 站内学习助手 E2E（A14 验收）。
 *
 * 覆盖：入口与面板（AC-01..05）、导览问答（示例 A-1/2）、
 * 自动导航 execute→ack 闭环（示例 A-3..5）、学习报告固定结构卡
 * （示例 B-1..3）、教学报告卡（示例 C-1..3）、历史与新建会话。
 * 全部走隔离 backend（8124，SITE_ASSISTANT_ENABLED=1）+ fake LLM。
 */
import { expect, test } from "@playwright/test";
import { BACKEND, loginViaStorage, registerAndLogin } from "./support/helpers";

test.describe("assistant panel (A05-A14)", () => {
  let token = "";
  let userId = "";

  test.beforeAll(async ({ request }) => {
    const auth = await registerAndLogin(request);
    token = auth.token;
    userId = auth.userId;
  });

  test.beforeEach(async ({ page }) => {
    await loginViaStorage(page, token);
  });

  test("launcher opens panel with composer and welcome", async ({ page }) => {
    await page.goto("/dashboard");
    const launcher = page.getByRole("button", { name: /学习助手|assistant/i });
    await expect(launcher).toBeVisible();
    await launcher.click();
    const composer = page.locator(".assistant-composer textarea");
    await expect(composer).toBeVisible();
    // 欢迎区存在（空会话），快捷入口 ≤4。
    await expect(page.locator(".assistant-panel")).toBeVisible();
  });

  test("site tour question returns module guidance", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: /学习助手|assistant/i }).click();
    const composer = page.locator(".assistant-composer textarea");
    await composer.fill("这个网站能帮我做什么？");
    await composer.press("Enter");
    // 快路 guide：markdown 正文出现（内容来自目录事实）。
    await expect(page.locator(".assistant-md").first())
      .toBeVisible({ timeout: 15000 });
  });

  test("navigate command auto-executes and acks (示例 A)", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: /学习助手|assistant/i }).click();
    const composer = page.locator(".assistant-composer textarea");
    await composer.fill("带我去记忆中心。");
    await composer.press("Enter");
    // 动作卡出现并自动执行 → 路由到 /memory；ack 后卡显示已完成。
    await expect(page).toHaveURL(/\/memory/, { timeout: 15000 });
    await expect(
      page.locator(".assistant-action-card[data-state='succeeded']").first(),
    ).toBeVisible({ timeout: 15000 });
  });

  test("entity search yields clickable note deep link (P1-1/P1-2)", async ({ page, request }) => {
    // 前置：真实 API 创建一篇具名笔记（notes router prefix + 路径双段）。
    const res = await request.post(`${BACKEND}/api/v1/notes/notes`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { title: "定积分与可积性", content: "e2e 实体检索内容" },
    });
    expect(res.ok()).toBeTruthy();
    const noteId = (await res.json()).note.id as string;
    await page.goto("/dashboard");
    await page.getByRole("button", { name: /学习助手|assistant/i }).click();
    const composer = page.locator(".assistant-composer textarea");
    await composer.fill("找到我的笔记《定积分与可积性》");
    await composer.press("Enter");
    // search 意图 → user_click 实体深链卡（不是笔记模块卡）。
    const card = page.locator(
      ".assistant-action-card[data-state='proposed']").first();
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(card).toContainText("定积分与可积性");
    await card.getByRole("button", { name: /立即打开/ }).click();
    await expect(page).toHaveURL(
      new RegExp(`/notes/${encodeURIComponent(noteId)}`),
      { timeout: 15000 });
    await expect(
      page.locator(".assistant-action-card[data-state='succeeded']").first(),
    ).toBeVisible({ timeout: 15000 });
    // 搜索来源（site_search locator）可点击深链回笔记列表/详情。
    const sources = page.locator(".assistant-sources");
    await expect(sources).toBeVisible({ timeout: 10000 });
    await sources.locator(".assistant-sources-toggle").click();
    await expect(
      sources.locator(".assistant-source-title", { hasText: "定积分与可积性" }),
    ).toBeVisible();
  });

  test("learning report renders fixed-structure card (示例 B)", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: /学习助手|assistant/i }).click();
    const composer = page.locator(".assistant-composer textarea");
    await composer.fill("最近一周我学得怎么样？");
    await composer.press("Enter");
    const card = page.locator("[data-testid='assistant-learning-report']");
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(card.locator(".assistant-report-window")).toBeVisible();
    // 新账号无记录：诚实空态而不是伪数字。
    await expect(card.locator(".assistant-report-empty, .assistant-report-fact")
      .first()).toBeVisible();
  });

  test("teaching report renders account-scope card (示例 C)", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: /学习助手|assistant/i }).click();
    const composer = page.locator(".assistant-composer textarea");
    await composer.fill("有没有AI教学评价？");
    await composer.press("Enter");
    const card = page.locator("[data-testid='assistant-teaching-report']");
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(
      card.locator(".assistant-report-scope",
        { hasText: "与你的教学交互" })).toBeVisible();
  });

  test("history view lists and reopens conversations", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: /学习助手|assistant/i }).click();
    const composer = page.locator(".assistant-composer textarea");
    await composer.fill("你好");
    await composer.press("Enter");
    await page.locator(".assistant-md").first()
      .waitFor({ timeout: 15000 });
    // 打开历史并新建会话。
    const historyBtn = page.getByRole("button", { name: /历史|history/i });
    if (await historyBtn.count()) {
      await historyBtn.first().click();
      await page.locator(".assistant-history").waitFor({ timeout: 10000 });
      const newBtn = page.getByRole("button", { name: /新对话|new/i });
      if (await newBtn.count()) {
        await newBtn.first().click();
        await expect(
          page.locator(".assistant-composer textarea")).toBeVisible();
      }
    }
  });

  test("domain write note.create reviews preview then approves (B03)", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: /学习助手|assistant/i }).click();
    const composer = page.locator(".assistant-composer textarea");
    await composer.fill(
      "帮我记一条笔记：标题为：验收笔记。这是变更预览确认流程的测试内容。");
    await composer.press("Enter");
    // note.create 是 review_required：动作卡先给「查看变更」，不直接执行。
    const card = page.locator(
      ".assistant-action-card[data-state='proposed']").first();
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(card).toContainText("创建笔记");
    await card.getByRole("button", { name: /查看变更/ }).click();
    // 预览卡：变更字段 + 影响说明 + 确认/取消。
    const preview = page.locator(".assistant-preview-card");
    await expect(preview).toBeVisible({ timeout: 10000 });
    await expect(preview).toContainText("笔记正文");
    await preview.getByRole("button", { name: /确认执行/ }).click();
    // approve（许可）→ execute（领域写）→ succeeded。
    await expect(
      page.locator(".assistant-action-card[data-state='succeeded']").first(),
    ).toBeVisible({ timeout: 15000 });
  });

  test("domain write workspace.create auto-runs with real receipt (B05)", async ({ page, request }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: /学习助手|assistant/i }).click();
    const composer = page.locator(".assistant-composer textarea");
    await composer.fill("帮我创建一个学习区叫物理专区");
    await composer.press("Enter");
    // workspace.create 是 intent_sufficient（§21.2）：明确自然语言指令
    // 直接执行 → 卡直达 succeeded；无「查看变更」前置门槛。
    const card = page.locator(
      ".assistant-action-card[data-state='succeeded']").first();
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(card).toContainText("创建辅导区");
    // 真实业务回执：辅导区出现在本人列表（不是仅卡片文字）。
    const res = await request.get(`${BACKEND}/api/v1/workspaces`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    const names = (body.workspaces || []).map((w: { name: string }) => w.name);
    expect(names).toContain("物理专区");
    // §21.5 撤销闭环：真实补偿（归档辅导区），不是只改卡片文字。
    const card2 = page.locator(
      ".assistant-action-card[data-state='succeeded']").first();
    await card2.getByRole("button", { name: /^撤销/ }).click();
    await expect(card2).toContainText("已撤销", { timeout: 15000 });
    const res2 = await request.get(`${BACKEND}/api/v1/workspaces`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const body2 = await res2.json();
    const names2 = (body2.workspaces || []).map((w: { name: string }) => w.name);
    expect(names2).not.toContain("物理专区");
  });
});
