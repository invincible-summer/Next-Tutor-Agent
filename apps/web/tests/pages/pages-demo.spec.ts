import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const manifest = JSON.parse(readFileSync(join(process.cwd(), "public/demo/manifest.json"), "utf8"));
const base = "/Next-Tutor-Agent";

test("complete example showcase works with only a static file server", async ({ page }, testInfo) => {
  const errors: string[] = [];
  const requests: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (request.url().includes("/api/v1") || !request.url().startsWith("http://127.0.0.1:3040/")) requests.push(request.url());
  });
  await page.addInitScript(() => {
    const state = window as unknown as { missingSnapshots: string[] };
    state.missingSnapshots = [];
    window.addEventListener("edu-demo-missing", (event) => state.missingSnapshots.push((event as CustomEvent).detail));
  });
  await page.goto(`${base}/dashboard/`);
  await expect(page).toHaveURL(/\/login\//);
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(page.locator('input[type="email"]')).toHaveValue("example@example.com");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard\//);
  await page.waitForLoadState("networkidle");
  // 合成演示数据：问候语来自 fixtures/demo 的 ux/greeting 快照。
  expect(await page.locator("main").innerText()).toContain("你好，今天想学点什么？");
  // Match the production site's default desktop scale and compact navigation.
  const rail = page.locator("aside").first();
  await expect(page.locator("html")).toHaveCSS("font-size", "16px");
  await expect(rail).toHaveCSS("width", "60px");
  await expect(rail.locator("a").first()).toHaveCSS("height", "56px");
  await expect(rail.locator("nav a").first()).toHaveCSS("width", "36px");
  await expect(rail.locator("nav a").first()).toHaveCSS("height", "36px");
  await expect(rail.locator("nav a").first()).toHaveCSS("font-size", "13px");
  await page.getByRole("button", { name: "展开导航", exact: true }).click();
  await expect(rail).toHaveCSS("width", "224px");
  await page.getByRole("button", { name: "收起导航", exact: true }).click();
  await expect(rail).toHaveCSS("width", "60px");

  const paths = ["/", "/account/", "/admin/", "/archive/", "/assessment/", "/chat/", "/course/", "/dashboard/", "/docs/", "/insights/", "/knowledge/", "/memory/", "/notes/", "/orchestration/", "/plan/", "/profile/", "/resources/files/", "/resources/textbooks/", "/settings/", "/tools/", "/tools/geometry/",
    ...manifest.routes.sessions.map((id: string) => `/chat/${encodeURIComponent(id)}/`),
    ...manifest.routes.notes.map((id: string) => `/notes/${encodeURIComponent(id)}/`),
    ...manifest.routes.workspaces.map((id: string) => `/workspaces/${encodeURIComponent(id)}/classroom/`),
    ...manifest.routes.lessons.map((item: { workspaceId: string; lessonId: string }) => `/workspaces/${encodeURIComponent(item.workspaceId)}/classroom/${item.lessonId}/`),
    ...manifest.routes.runs.map((item: { workspaceId: string; lessonId: string; runId: string }) => `/workspaces/${encodeURIComponent(item.workspaceId)}/classroom/${item.lessonId}/learn/${item.runId}/`),
  ];
  const missing: Record<string, string[]> = {};
  for (const path of paths) {
    const response = await page.goto(base + path);
    expect(response?.status(), path).toBe(200);
    await page.waitForLoadState("networkidle");
    await expect(page.getByText("只读演示 · 浏览 example 的示范数据 · AI 与编辑功能已关闭", { exact: true }), path).toHaveCount(0);
    await expect(page.getByRole("alertdialog"), path).toHaveCount(0);
    await expect(page.locator("main"), path).toContainText(/\S/);
    const values = await page.evaluate(() => (window as unknown as { missingSnapshots: string[] }).missingSnapshots);
    if (values.length) missing[path] = values;
  }
  expect(missing).toEqual({});
  expect(errors).toEqual([]);
  expect(requests).toEqual([]);

  const noteId = manifest.routes.notes[0];
  await page.goto(`${base}/notes/${encodeURIComponent(noteId)}/`);
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toContainText("不能新建、编辑或删除数据");
  await page.getByRole("button", { name: "知道了", exact: true }).click();
  await page.getByRole("button", { name: "笔记总览", exact: true }).click();
  await page.getByRole("button", { name: "新建笔记", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "知道了", exact: true }).click();
  await page.keyboard.press("Escape");
  await page.locator("textarea").first().click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "知道了", exact: true }).click();

  await page.goto(`${base}/chat/${encodeURIComponent(manifest.routes.sessions[0])}/`);
  await page.waitForLoadState("networkidle");
  await expect(page.locator("textarea").first()).toHaveAttribute("readonly", "");
  await page.getByRole("button", { name: /新对话|New chat/ }).first().click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "知道了", exact: true }).click();
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toContainText("不支持 AI 调用");
  await page.screenshot({ path: testInfo.outputPath("read-only-dialog.png") });
  await page.getByRole("button", { name: "知道了", exact: true }).click();
  expect((await page.locator("header.h-14").boundingBox())?.y).toBe(0);
  await page.screenshot({ path: testInfo.outputPath("chat-light.png") });
  await page.evaluate(() => { localStorage.setItem("edu-agent-theme", "dark"); document.documentElement.classList.add("dark"); });
  await page.screenshot({ path: testInfo.outputPath("chat-dark.png") });
  await page.setViewportSize({ width: 1080, height: 800 });
  await page.screenshot({ path: testInfo.outputPath("chat-narrow-desktop.png") });
  const lesson = manifest.routes.lessons[0];
  await page.goto(`${base}/workspaces/${encodeURIComponent(lesson.workspaceId)}/classroom/${lesson.lessonId}/`);
  await page.waitForLoadState("networkidle");
  const savedRuns = page.locator(`a[href*="/classroom/${lesson.lessonId}/learn/"]`);
  await expect(savedRuns).toHaveCount(manifest.routes.runs.filter((run: { workspaceId: string; lessonId: string }) => run.workspaceId === lesson.workspaceId && run.lessonId === lesson.lessonId).length);
  await savedRuns.first().click();
  await expect(page).toHaveURL(/\/learn\//);
  await page.waitForLoadState("networkidle");
  await expect(page.getByText(/已浏览页数/)).toBeVisible();
  await page.getByRole("link", { name: "版本 1", exact: true }).click();
  await expect(page).toHaveURL(/\?revision=1$/);
  await page.waitForLoadState("networkidle");
  await expect(page.locator("header.h-14")).toHaveCount(1);
  await expect(page.locator("iframe")).toBeVisible();
  await expect(page.locator(".classroom-prose").first()).toHaveCSS("font-size", "13px");
  const courseFrame = page.frameLocator("iframe");
  await expect(courseFrame.locator(".slide.current")).toHaveCount(1);
  const slideCount = await courseFrame.locator(".slide").count();
  expect(slideCount).toBeGreaterThan(1);
  await expect(page.getByText(`1 / ${slideCount}`, { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.getByText(`2 / ${slideCount}`, { exact: true }).first()).toBeVisible();
  await expect(courseFrame.locator(".slide.current")).toHaveAttribute("data-slide-id", /.+/);
  await expect(courseFrame.locator(".slide").nth(1)).toHaveClass(/current/);
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await expect(courseFrame.locator(".slide").first()).toHaveClass(/current/);
  await expect(page.getByText(`1 / ${slideCount}`, { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("classroom-dark.png") });
  expect(requests).toEqual([]);
  expect(errors).toEqual([]);
});
