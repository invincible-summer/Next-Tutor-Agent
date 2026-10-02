import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
let tokenCounter = 0;

async function mockSite(page: Page, options: { admin?: boolean; allowed?: boolean; theme?: string } = {}) {
  let allowed = options.allowed ?? true;
  let active = 2;
  let legacy = 3;
  const requests: { path: string; headers: Record<string, string> }[] = [];
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(({ admin, theme }) => {
    localStorage.setItem("edu-agent-lang", "zh");
    localStorage.setItem("edu-agent-theme", theme);
    if (admin) localStorage.setItem("edu-agent-token", "admin-test-token");
  }, { admin: options.admin ?? false, theme: options.theme ?? "light" });
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/v1", "");
    requests.push({ path, headers: req.headers() });
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/auth/status") return json({ guest_allowed: allowed, auth_required: !allowed });
    if (path === "/auth/me") return json({ user: { id: "usr_admin_test", username: "Admin", email: "admin@example.com", role: "admin", profile: { name: "管理员", grade: "本科", subjects: [] } } });
    if (path === "/guest/session") return req.method() === "DELETE" ? route.fulfill({ status: 204 }) : json({ token: `temporary-${++tokenCounter}`, expires_in: 1800 });
    if (path === "/guest/textbooks") return json({ items: [{ id: "tb_public", name: "公共数学教材", grade: "本科" }] });
    if (path === "/chat/stream") return route.fulfill({ contentType: "text/event-stream", body: 'event: answer\ndata: {"type":"answer","content":"临时聊天测试回答"}\n\nevent: done\ndata: {"type":"done","answer":"临时聊天测试回答","session_id":"gst_test","temporary":true}\n\n' });
    if (path === "/guest/quiz/generate") return json({ questions: [{ question_id: "gq_test", question_revision: 1, type: "multiple_choice", stem: "1+1=?", options: { A: "2", B: "3" }, answer: "A", explanation: "相加得到2", difficulty: "easy", knowledge_point: "加法" }] });
    if (path === "/quiz/submission") return json({ submission: null });
    if (path === "/quiz/record") return json({ status: "ok", attempt_id: "gatt_test", question_id: "gq_test", question_revision: 1, student_answer: "A", task_result: { verdict: "correct", criterion_results: [] }, verdict: "correct", feedback: "正确", evaluation: { status: "unavailable", reason_code: "guest_temporary" }, pending: false, revealed: { answer: "A", explanation: "相加得到2" } });
    if (path === "/admin/guest-policy") { if (req.method() === "PUT") allowed = req.postDataJSON().allow_guests; return json({ allow_guests: allowed, updated_at: 0 }); }
    if (path === "/admin/guest-data") return json({ active: { visitors: active, sessions: active, questions: active ? 1 : 0, tasks: 0 }, total_items: legacy, total_bytes: legacy ? 120 : 0, categories: {} });
    if (path === "/admin/guest-data/purge") { active = 0; legacy = 0; return json({ status: "purged", active: { visitors: 2, cancelled_tasks: 0 }, total_deleted: 3, total_bytes: 120, failed: 0 }); }
    if (path === "/admin/users") return json({ users: [], summary: { count: 0, total_bytes: 0 } });
    if (path === "/admin/orphan-data") return json({ report: { categories: {}, total_items: 0, total_bytes: 0, protected_ids: [] } });
    if (path === "/assistant/capabilities") return json({ enabled: false });
    if (path === "/classroom/capabilities") return json({ enabled: false });
    if (path === "/ux/motivation") return json({ streak_days: 0 });
    return json({ status: "disabled", sessions: [], workspaces: [] });
  });
  return { requests, errors, disable: () => { allowed = false; } };
}

test("guest chat uses temporary token, has no private navigation or persistent learning data", async ({ page }) => {
  const { requests, errors } = await mockSite(page);
  await page.goto("/chat");
  await expect(page.getByTestId("guest-chat")).toBeVisible();
  await page.getByRole("textbox", { name: "输入学习问题" }).fill("请解释加法");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("临时聊天测试回答", { exact: true })).toBeVisible();
  expect(requests.find((r) => r.path === "/chat/stream")?.headers["x-guest-token"]).toMatch(/^temporary-\d+$/);
  expect(requests.some((r) => /^\/(assistant|workspaces|student|library|ux|voice)\//.test(r.path))).toBe(false);
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  await expect(page.locator('a[href="/dashboard"]')).toHaveCount(0);
  const stored = await page.evaluate(() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]));
  expect(stored).not.toContain("请解释加法"); expect(stored).not.toContain("temporary-1");
  await page.reload();
  await expect(page.getByTestId("guest-chat")).toBeVisible();
  await expect(page.getByText("临时聊天测试回答", { exact: true })).toHaveCount(0);
  expect(requests.filter((r) => r.path === "/guest/session").length).toBeGreaterThanOrEqual(2);
  expect(errors).toEqual([]);
});

test("guest practice generates cards and submits without an evaluation link", async ({ page }) => {
  const { requests, errors } = await mockSite(page);
  await page.goto("/assessment");
  await page.getByRole("textbox", { name: "主题或知识点" }).fill("加法");
  await page.getByRole("button", { name: "生成临时练习" }).click();
  await expect(page.getByText("1+1=?", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /A.*2/ }).click();
  await page.getByRole("button", { name: "提交批改", exact: true }).click();
  await expect.poll(() => requests.filter((r) => r.path === "/quiz/record").length).toBe(1);
  await expect(page.getByText("本题反馈", { exact: true })).toBeVisible();
  await expect(page.getByText("学习反馈", { exact: true })).toHaveCount(0);
  await expect(page.locator('a[href*="evaluation"]')).toHaveCount(0);
  await page.getByRole("button", { name: "清空并重新开始" }).click();
  await expect(page.getByText("1+1=?", { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("disabled guest access and private routes redirect to login", async ({ page }) => {
  const { requests } = await mockSite(page, { allowed: false });
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login\?redirect=/);
  expect(requests.some((r) => r.path === "/guest/session")).toBe(false);
});

test("enabled guests still cannot open personal pages", async ({ page }) => {
  await mockSite(page);
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login\?redirect=/);
});

test("live policy closure clears the page and redirects", async ({ page }) => {
  const site = await mockSite(page);
  await page.goto("/chat");
  await expect(page.getByTestId("guest-chat")).toBeVisible();
  site.disable();
  await page.evaluate(() => window.dispatchEvent(new Event("edu-access-changed")));
  await expect(page).toHaveURL(/\/login\?redirect=/);
});

test("guest tabs have separate tokens and login in another tab clears temporary data", async ({ page, context }) => {
  const first = await mockSite(page);
  await page.goto("/chat");
  await page.getByRole("textbox", { name: "输入学习问题" }).fill("第一标签页私有临时文字");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("临时聊天测试回答", { exact: true })).toBeVisible();
  const other = await context.newPage();
  const second = await mockSite(other);
  await other.goto("/chat");
  await expect(other.getByText("第一标签页私有临时文字", { exact: true })).toHaveCount(0);
  await other.getByRole("textbox", { name: "输入学习问题" }).fill("第二页的问题");
  await other.getByRole("button", { name: "发送", exact: true }).click();
  await expect(other.getByText("临时聊天测试回答", { exact: true })).toBeVisible();
  const token = (site: typeof first) => site.requests.find((r) => r.path === "/chat/stream")?.headers["x-guest-token"];
  expect(token(first)).not.toBe(token(second));
  await other.evaluate(() => localStorage.setItem("edu-agent-token", "new-login-token"));
  await expect(page.getByTestId("guest-chat")).toHaveCount(0);
  await expect(page.getByText("第一标签页私有临时文字", { exact: true })).toHaveCount(0);
  await expect.poll(() => first.requests.some((r) => r.path === "/guest/session" && r.headers["x-guest-token"] === token(first))).toBe(true);
  expect(first.errors).toEqual([]);
  await other.close();
});

for (const theme of ["light", "dark"]) {
  test(`admin policy and cleanup controls in ${theme}`, async ({ page }) => {
    const { errors } = await mockSite(page, { admin: true, theme });
    await page.goto("/admin");
    await expect(page.getByText("游客访问", { exact: true })).toBeVisible();
    await page.getByRole("checkbox", { name: "允许未登录用户体验" }).uncheck();
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("游客访问设置已生效。", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "数据清理", exact: true }).click();
    await expect(page.getByText("游客数据清理", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "清理全部游客数据", exact: true }).click();
    await page.getByRole("button", { name: "清理全部游客数据", exact: true }).last().click();
    await expect(page.getByText(/已结束 2 位游客体验/)).toBeVisible();
    await mkdir("../../acceptance-reports/screenshots", { recursive: true });
    await page.screenshot({ path: `../../acceptance-reports/screenshots/guest-admin-${theme}-1440.png`, fullPage: true });
    expect(errors).toEqual([]);
  });
  for (const width of [1440, 1024]) {
    test(`guest pages fit ${theme} at ${width}`, async ({ page }) => {
      const { errors } = await mockSite(page, { theme });
      await page.setViewportSize({ width, height: 900 });
      await mkdir("../../acceptance-reports/screenshots", { recursive: true });
      for (const [path, mode] of [["/chat", "chat"], ["/assessment", "practice"]]) {
        await page.goto(path);
        await expect(page.getByTestId(`guest-${mode}`)).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: `../../acceptance-reports/screenshots/guest-${mode}-${theme}-${width}.png`, fullPage: true });
      }
      expect(errors).toEqual([]);
    });
  }
}
