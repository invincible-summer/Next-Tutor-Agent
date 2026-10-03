import { expect, test, type Page } from "@playwright/test";

// Compile the shared settings route before opening stateful editors in dev.
// Compiling a previously unseen route can otherwise trigger Fast Refresh.
test.beforeAll(async ({ request }) => { await request.get("/settings"); });

const user = { id: "i18n-user", email: "i18n@example.com", username: "Demo", role: "admin",
  profile: { name: "Demo", grade: "本科", subjects: [] } };
const textbook = {
  id: "tb_i18n", title: "教材原文标题", filename: "textbook.pdf", file_id: "file_i18n",
  kind: "group", scope: "private", subject: "Physics", level: "本科", status: "ready",
  chapter_count: 2, concept_count: 4, warnings: [], updated_at: 1_790_000_000,
  volumes: [{ file_id: "file_i18n", filename: "textbook.pdf", has_original: true }],
  file_ids: ["file_i18n"], graph_policy: { default_max_chapters: null, default_max_concepts: null, volume_overrides: {} },
};
const note = {
  id: "note_i18n", title: "笔记原文标题", folder_id: "", tags: [], template_id: "", status: "active",
  revision: 1, source: {}, review: { enabled: false, next_review_at: 0, easiness: 2.5, interval: 0, repetitions: 0 },
  created_at: 1_790_000_000, updated_at: 1_790_000_000, created_by: "user", word_count: 10,
};

async function mockSite(page: Page, options: { lang?: "zh" | "en"; theme?: "light" | "dark"; beforeEnglishDocs?: () => Promise<void> } = {}) {
  const counts = new Map<string, number>();
  const saves: { lang: string; markdown: string }[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && /hydration|didn't match|does not match/i.test(message.text())) errors.push(message.text());
  });
  await page.addInitScript(({ lang, theme }) => {
    if (!localStorage.getItem("edu-agent-lang")) localStorage.setItem("edu-agent-lang", lang);
    if (!localStorage.getItem("edu-agent-theme")) localStorage.setItem("edu-agent-theme", theme);
    localStorage.setItem("edu-agent-token", "mock-token");
  }, { lang: options.lang ?? "en", theme: options.theme ?? "light" });
  await page.context().route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    counts.set(path, (counts.get(path) ?? 0) + 1);
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/auth/status") return json({ auth_required: false });
    if (path === "/auth/me") return json({ user });
    if (path === "/assistant/capabilities") return json({ enabled: false });
    if (path === "/classroom/capabilities") return json({ enabled: false });
    if (path === "/model-info") return json({ llm_model: "Mock", multimodal_configured: false });
    if (path === "/ux/motivation") return json({ streak_days: 0, active_days: 0 });
    if (path === "/ux/greeting") return json({ greeting: "Welcome back" });
    if (path === "/workspaces") return json({ workspaces: [] });
    if (path === "/sidebar") return json({ sessions: [], workspaces: [], details: {}, classroom_summaries: {} });
    if (path === "/chat/sessions") return json({ sessions: [] });
    if (path === "/knowledge/taxonomy") return json({ status: "ok", levels: [] });
    if (path === "/library") return json({ folders: [], files: [] });
    if (path === "/textbooks") return json({ textbooks: [textbook] });
    if (path === "/textbooks/tb_i18n") return json({ textbook, outline: [], concepts: [] });
    if (path === "/trash") return json({ status: "ok", items: Array.from({ length: 6 }, (_, index) => ({
      id: `archive_${index}`, title: `Archived chat ${index + 1}`, resource_type: "session",
      deleted_at: 1_790_000_000, expires_at: 0, size_bytes: 10,
      metadata: { memory_forget_status: index === 0 ? "legacy_unknown" : "recent" },
    })) });
    if (path === "/trash/policy") return json({ mode: "manual", retention_days: 30, user_max_days: 90, forced_max_days: 90 });
    if (path === "/docs/content") {
      if (route.request().method() === "PUT") {
        const body = route.request().postDataJSON();
        saves.push(body);
        return json({ ...body, updated_at: 1_790_000_000, updated_by: "Demo" });
      }
      const lang = url.searchParams.get("lang") ?? "zh";
      if (lang === "en") await options.beforeEnglishDocs?.();
      return json({ markdown: lang === "en" ? "# English user guide\n\n## Quick start\n\nChoose your materials." : "# 中文使用手册\n\n## 快速上手\n\n准备学习材料。",
        updated_at: 0, updated_by: "", show_manual: false });
    }
    if (path === "/notes/vault") return json({ folders: [], notes: [note], tags: {}, custom_templates: [],
      stats: { note_count: 1, folder_count: 0, link_count: 1, unresolved_links: [], due_review_count: 0, due_review_ids: [] } });
    if (path === "/notes/templates") return json({ templates: [] });
    if (path === "/notes/graph") return json({ nodes: [], edges: [] });
    if (path === "/notes/notes/note_i18n") return json({ note, content: "# 原始笔记内容\n\nconversation://session/chat_source",
      backlinks: [], links: { resolved: [], unresolved: [], resources: [{ type: "session", resource_id: "chat_source", url: "conversation://session/chat_source",
        title: "Source chat", status: "deleted", resolved: false }] }, inline_tags: [] });
    if (path.includes("/notes/agent")) return json({ messages: [], mode: "ask", pending_plan: null });
    return json({ status: "disabled" }, 503);
  });
  return { counts, saves, errors };
}

async function switchLanguage(page: Page, lang: "zh" | "en") {
  // Use the real settings page in a second tab so an editor's draft stays open.
  // UIProvider consumes the cross-tab storage event on the original page.
  const settings = await page.context().newPage();
  await settings.goto("/settings");
  await settings.getByRole("group", { name: /^(Language|语言)$/ }).getByRole("button", { name: lang === "en" ? "English" : "中文", exact: true }).click();
  await settings.close();
  await expect(page.locator("html")).toHaveAttribute("lang", lang === "en" ? "en" : "zh-CN");
}

test("language follows navigation, reload, auth forms and grade display", async ({ page }) => {
  const { errors } = await mockSite(page, { lang: "zh" });
  await page.goto("/");
  await page.getByRole("button", { name: "Switch to English" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await page.getByRole("link", { name: "Create one" }).click();
  await expect(page.getByRole("heading", { name: "Create account", exact: true })).toBeVisible();
  await page.locator('input[type="email"]').fill("example@example.com");
  await page.locator('input[type="password"]').fill("example-password");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByRole("button", { name: "Undergrad", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "本科", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "中文", exact: true }).click();
  await expect(page.getByRole("button", { name: "本科", exact: true })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  expect(errors).toEqual([]);
});

test("archive translations and pager update without repeated data loading", async ({ page }) => {
  const { counts, errors } = await mockSite(page);
  await page.goto("/archive");
  await expect(page.getByRole("main").getByRole("heading", { name: "Archive Center" })).toBeVisible();
  await expect(page.getByText("The legacy generation kept only an aggregate count", { exact: false })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Page number" })).toBeVisible();
  const initial = counts.get("/trash") ?? 0;
  await page.getByRole("button", { name: "Manage", exact: true }).click();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.getByText("Archived chat 6", { exact: true })).toBeVisible();
  await page.waitForTimeout(400);
  expect(counts.get("/trash")).toBe(initial);
  await switchLanguage(page, "zh");
  await expect(page.getByRole("main").getByRole("heading", { name: "归档中心" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "页码" })).toBeVisible();
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await expect(page.getByText("旧版只保留汇总计数", { exact: false })).toBeVisible();
  expect(errors).toEqual([]);
});

test("textbooks translate forms and grade labels while preserving source names", async ({ page }) => {
  const { counts, errors } = await mockSite(page);
  await page.goto("/resources/textbooks");
  await expect(page.getByRole("button", { name: /教材原文标题/ }).first()).toBeVisible();
  await expect(page.getByText("Undergrad", { exact: false }).first()).toBeVisible();
  await page.getByRole("button", { name: "Upload textbook", exact: true }).click();
  await expect(page.getByText("Textbook group defaults", { exact: true })).toBeVisible();
  await expect(page.getByPlaceholder("Chapters: no limit")).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({ name: "sample.txt", mimeType: "text/plain", buffer: Buffer.from("example") });
  await expect(page.getByRole("button", { name: "Upload and build", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).last().click();
  await page.keyboard.press("Escape");
  const initial = counts.get("/textbooks") ?? 0;
  await page.getByRole("button", { name: /教材原文标题/ }).first().click();
  await expect(page.getByText("Knowledge map limits", { exact: true })).toBeVisible();
  await expect(page.getByText("Default chapter limit", { exact: true })).toBeVisible();
  await page.waitForTimeout(400);
  expect(counts.get("/textbooks")).toBe(initial);
  expect(counts.get("/textbooks/tb_i18n")).toBeLessThanOrEqual(2);
  expect(errors).toEqual([]);
});

test("notes translate resource controls and preview labels while preserving content", async ({ page }) => {
  const { errors } = await mockSite(page);
  await page.goto("/notes/note_i18n");
  await expect(page.getByRole("button", { name: "Insert resource link", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Insert resource link", exact: true }).click();
  await expect(page.getByPlaceholder("Search notes or conversations")).toBeVisible();
  await page.getByPlaceholder("Search notes or conversations").fill("no-match");
  await expect(page.getByText("No matching resources", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Insert resource link", exact: true }).click();
  await expect(page.getByText("Chat history · chat_source", { exact: true })).toBeVisible();
  await expect(page.getByText("Resource links", { exact: true })).toBeVisible();
  await expect(page.getByText("This resource was deleted", { exact: false })).toBeVisible();
  await expect(page.getByText("原始笔记内容", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("docs reload in the selected language and retain the editing locale", async ({ page }) => {
  const { saves, errors } = await mockSite(page);
  await page.goto("/docs");
  await expect(page.getByRole("heading", { name: "English user guide" })).toBeVisible();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await switchLanguage(page, "zh");
  await expect(page.getByText("编辑 English 版本", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("heading", { name: "中文使用手册" })).toBeVisible();
  expect(saves).toEqual([{ markdown: "# English user guide\n\n## Quick start\n\nChoose your materials.", lang: "en" }]);
  expect(errors).toEqual([]);
});

test("a late docs response cannot replace the current language", async ({ page }) => {
  let releaseEnglish!: () => void;
  const pendingEnglish = new Promise<void>((resolve) => { releaseEnglish = resolve; });
  const { errors } = await mockSite(page, { lang: "zh", beforeEnglishDocs: () => pendingEnglish });
  await page.goto("/docs");
  await expect(page.getByRole("heading", { name: "中文使用手册" })).toBeVisible();
  const englishResponse = page.waitForResponse((response) => response.url().includes("/docs/content?lang=en"));
  await switchLanguage(page, "en");
  await switchLanguage(page, "zh");
  releaseEnglish();
  await englishResponse;
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "中文使用手册" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "English user guide" })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("saving a draft completes while a locale refresh is pending", async ({ page }) => {
  let holdEnglish = false;
  let releaseEnglish!: () => void;
  const pendingEnglish = new Promise<void>((resolve) => { releaseEnglish = resolve; });
  const { saves, errors } = await mockSite(page, { beforeEnglishDocs: () => holdEnglish ? pendingEnglish : Promise.resolve() });
  await page.goto("/docs");
  await expect(page.getByRole("heading", { name: "English user guide" })).toBeVisible();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByRole("textbox").fill("# Updated English guide");
  await switchLanguage(page, "zh");
  holdEnglish = true;
  const englishResponse = page.waitForResponse((response) => response.url().includes("/docs/content?lang=en"));
  await switchLanguage(page, "en");
  await expect(page.getByRole("textbox")).toHaveValue("# Updated English guide");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeEnabled();
  await expect(page.getByRole("heading", { name: "Updated English guide" })).toBeVisible();
  releaseEnglish();
  await englishResponse;
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Updated English guide" })).toBeVisible();
  expect(saves).toEqual([{ markdown: "# Updated English guide", lang: "en" }]);
  expect(errors).toEqual([]);
});

test("language changes propagate across tabs", async ({ page, context }) => {
  await mockSite(page);
  await page.goto("/login");
  const other = await context.newPage();
  await mockSite(other);
  await other.goto("/login");
  await page.getByRole("button", { name: "中文", exact: true }).click();
  await expect(other.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
});

for (const theme of ["light", "dark"] as const) {
  for (const width of [1440, 1024]) {
    test(`English docs render in ${theme} at ${width}px`, async ({ page }, testInfo) => {
      const { errors } = await mockSite(page, { theme });
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/docs");
      await expect(page.getByRole("heading", { name: "English user guide" })).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("lang", "en");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await page.locator("html").evaluate((node) => node.classList.contains("dark"))).toBe(theme === "dark");
      await page.screenshot({ path: testInfo.outputPath(`i18n-en-${theme}-${width}.png`), fullPage: true, animations: "disabled" });
      expect(errors).toEqual([]);
    });
  }
}
