/* /course 备课上课 Hub E2E（Phase 2 顶层模块）。
 *
 * 场景 1：Hub 渲染——标题、辅导区分组头（名称 + 课程数）、课程卡标题可见。
 * 场景 2：页头「一键备课」打开 CreateLessonModal——选择器模式含
 *         「所属辅导区」下拉，且选中/可选 mocked 辅导区。
 * 场景 3：分组折叠状态持久化——折叠后 reload 仍折叠（localStorage
 *         edu-agent-course-expanded）。
 * 场景 4：capabilities.enabled=false → 功能未开放空态，不渲染分组。
 *
 * 全部经 page.route mock（/sidebar、capabilities、lessons 列表等），
 * 未拦截的请求落到 webServer 的隔离 backend（auth/ux 等降级接口）。
 */
import { expect, test, type Page, type Route } from "@playwright/test";
import { loginViaStorage } from "./support/helpers";
import {
  CAPABILITIES_FULL, LESSON_ID, WS_ID, WORKSPACE_DETAIL, templatesPayload,
} from "./support/classroom-helpers";

const SIDEBAR_SNAPSHOT = {
  sessions: [],
  workspaces: [
    { workspace_id: WS_ID, name: "大学物理", session_count: 0,
      file_count: 1, has_memory: false },
  ],
  details: {},
  classroom_summaries: {
    [WS_ID]: { lesson_count: 1, active_job_count: 0,
               last_lesson_id: LESSON_ID },
  },
};

/** Hub 分组/继续学习预取消费的 LessonListResponse（resume 为空）。 */
const LESSON_LIST = {
  items: [{
    lesson_id: LESSON_ID, workspace_id: WS_ID, title: "动量守恒入门",
    status: "ready", latest_ready_revision: 1, latest_job: null,
    brief: {
      topic: "动量守恒入门", goals: [], source_policy: "textbook_plus",
      duration_minutes: 15, language: "zh", grade: "",
      pedagogy_id: "concept_deep@1", theme_id: "academic_clear@2",
      image_density: "balanced", checkpoint_density: "standard",
      research_enabled: true, research_timeliness: "basic",
    },
    extra: { chapter_label: "第3章 动量", slide_count: 8 },
    updated_at: "2026-09-26T00:00:00Z",
  }],
  total: 1, page: 1, page_size: 4, resume: null,
};

async function routeHub(page: Page, opts: { disabled?: boolean } = {}) {
  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json",
                      body: JSON.stringify(body) });

    if (path === "/api/v1/classroom/capabilities") {
      return json(opts.disabled
        ? { ...CAPABILITIES_FULL, enabled: false, allowed: false,
            reason: "not_in_rollout" }
        : CAPABILITIES_FULL);
    }
    if (path === "/api/v1/classroom/templates") {
      return json(templatesPayload());
    }
    if (path === "/api/v1/sidebar" && method === "GET") {
      return json(SIDEBAR_SNAPSHOT);
    }
    if (path === `/api/v1/workspaces/${WS_ID}/classroom/lessons`
        && method === "GET") {
      return json(LESSON_LIST);
    }
    if (path === `/api/v1/workspaces/${WS_ID}` && method === "GET") {
      return json(WORKSPACE_DETAIL);
    }
    if (path === "/api/v1/workspaces" && method === "GET") {
      return json({ workspaces: [] });
    }
    if (path === "/api/v1/chat/sessions" && method === "GET") {
      return json({ sessions: [] });
    }
    if (path === "/api/v1/textbooks" && method === "GET") {
      return json({ textbooks: [] });
    }
    return route.fallback();
  });
}

test.beforeEach(async ({ page }) => {
  await loginViaStorage(page, "e2e-fake-token");
});

test("Hub 渲染：标题 + 辅导区分组头 + 课程卡", async ({ page }) => {
  await routeHub(page);
  await page.goto("/course");

  // 顶栏（banner）也会回显当前导航标题，断言限定在内容区 main 内
  await expect(
    page.getByRole("main")
      .getByRole("heading", { name: "备课上课" })).toBeVisible();
  // 分组头：折叠 toggle 的无障碍名包含辅导区名与课程数
  const toggle = page.getByRole("button", { name: /大学物理/ });
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(toggle).toContainText("1 个课程");
  // 分组课程卡（默认展开，预取首页 4 门）
  await expect(
    page.getByRole("link", { name: "动量守恒入门" }).first()).toBeVisible();
  await expect(page.getByText("第3章 动量")).toBeVisible();
});

test("页头一键备课打开 Modal，含所属辅导区选择器", async ({ page }) => {
  await routeHub(page);
  await page.goto("/course");
  await expect(
    page.getByRole("button", { name: /大学物理/ })).toBeVisible();

  await page.locator("header")
    .getByRole("button", { name: "一键备课" }).click();
  const dialog = page.locator(".motion-modal");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("所属辅导区")).toBeVisible();
  // 选择器默认选中第一个（此处唯一）辅导区
  const select = dialog.locator("select").first();
  await expect(select).toHaveValue(WS_ID);
  await expect(select.locator("option")).toHaveText(["大学物理"]);
});

test("分组折叠状态持久化到 localStorage", async ({ page }) => {
  await routeHub(page);
  await page.goto("/course");
  const toggle = page.getByRole("button", { name: /大学物理/ });
  await expect(
    page.getByRole("link", { name: "动量守恒入门" }).first()).toBeVisible();

  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(
    page.getByRole("link", { name: "动量守恒入门" })).toHaveCount(0);
  await expect.poll(() =>
    page.evaluate(() =>
      JSON.parse(localStorage.getItem("edu-agent-course-expanded") ?? "[]")),
  ).toEqual([WS_ID]);

  await page.reload();
  const toggleAfter = page.getByRole("button", { name: /大学物理/ });
  await expect(toggleAfter).toBeVisible();
  await expect(toggleAfter).toHaveAttribute("aria-expanded", "false");
  await expect(
    page.getByRole("link", { name: "动量守恒入门" })).toHaveCount(0);
});

test("课堂功能关闭时渲染未开放空态而非分组", async ({ page }) => {
  await routeHub(page, { disabled: true });
  await page.goto("/course");

  await expect(page.getByText("课堂功能未开放")).toBeVisible();
  await expect(
    page.getByText(/不在课堂功能的开放范围内/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: /大学物理/ })).toHaveCount(0);
});
