/* 课堂创建 E2E（plan.md §19.3 classroom-create）。
 *
 * 场景 1：工作区进入 → 默认设置 → 一次生成 → 关闭页面 → 重进，
 *         仍是同一 lesson/job（幂等不重复建课）。
 * 场景 2：空教材 + 无外部服务（research/tts/images 不可用）也有明确
 *         路径：提示通识备课、联网勾选禁用，仍可提交且不带 research。
 */
import { expect, test, type Route } from "@playwright/test";
import { loginViaStorage } from "./helpers";
import {
  CAPABILITIES_FULL, JOB_ID, LESSON_ID, WS_ID, jobPublic,
} from "./classroom-helpers";

test.beforeEach(async ({ page }) => {
  await loginViaStorage(page, "e2e-fake-token");
});

interface CreateCapture {
  body: Record<string, unknown> | null;
  idemKey: string;
  createCalls: number;
}

/** 创建流路由：POST 返回固定 lesson/job；列表/详情反映 generating 态。 */
async function routeCreateFlow(
  page: Route extends never ? never : import("@playwright/test").Page,
): Promise<CreateCapture> {
  const cap: CreateCapture = { body: null, idemKey: "", createCalls: 0 };
  const page_ = page as import("@playwright/test").Page;
  await page_.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json",
                      body: JSON.stringify(body) });

    if (path === "/api/v1/classroom/capabilities") {
      return json(CAPABILITIES_FULL);
    }
    if (path === "/api/v1/classroom/templates") {
      return json({
        pedagogy: [{ pedagogy_id: "concept_deep@1", name_zh: "概念精讲",
          name_en: "", description_zh: "", description_en: "" }],
        themes: [{ theme_id: "academic_clear@1", name_zh: "学术清晰",
          name_en: "", description_zh: "", description_en: "",
          accent: "#2563eb", preview_kind: "swatch" }],
        defaults: {},
      });
    }
    if (path === "/api/v1/workspaces" && method === "GET") {
      return json({ workspaces: [] });
    }
    if (path === `/api/v1/workspaces/${WS_ID}` && method === "GET") {
      return json({ workspace_id: WS_ID, name: "大学物理", session_ids: [],
        knowledge_files: [], public_memory: "",
        public_memory_updated_at: 0, created_at: 0, updated_at: 0 });
    }
    if (path === "/api/v1/chat/sessions") {
      return json({ sessions: [] });
    }
    if (path === "/api/v1/textbooks") {
      return json({ textbooks: [] });
    }
    if (path === `/api/v1/workspaces/${WS_ID}/classroom/lessons`
        && method === "POST") {
      cap.createCalls += 1;
      cap.body = route.request().postDataJSON() as Record<string, unknown>;
      cap.idemKey = route.request().headers()["idempotency-key"] ?? "";
      return json({
        lesson_id: LESSON_ID, job_id: JOB_ID, target_revision: 1,
        status_url: "/s", events_url: "/e",
      }, 201);
    }
    if (path === `/api/v1/workspaces/${WS_ID}/classroom/lessons`
        && method === "GET") {
      return json({
        items: cap.createCalls === 0 ? [] : [{
          lesson_id: LESSON_ID, workspace_id: WS_ID, title: "动量守恒入门",
          status: "generating", latest_ready_revision: null,
          latest_job: jobPublic({ phase: "outline",
            progress: { completed_slides: 0, total_slides: 0 } }),
          brief: null, extra: null, updated_at: "2026-09-26T00:00:00Z",
        }],
        total: cap.createCalls === 0 ? 0 : 1, page: 1, page_size: 5,
        resume: null,
      });
    }
    if (path === `/api/v1/workspaces/${WS_ID}/classroom/lessons/${LESSON_ID}`
        && method === "GET") {
      return json({
        lesson_id: LESSON_ID, workspace_id: WS_ID, title: "动量守恒入门",
        lifecycle: "active", latest_ready_revision: null,
        published_revisions: [], revision: null, pending: null,
        latest_job: jobPublic({ phase: "outline",
          progress: { completed_slides: 0, total_slides: 0 } }),
        recent_run: null,
      });
    }
    const jobMatch = path.match(/\/jobs\/([\w-]+)$/);
    if (jobMatch && method === "GET") {
      return json(jobPublic({ phase: "outline",
        progress: { completed_slides: 0, total_slides: 0 } }));
    }
    const sseMatch = path.match(/\/jobs\/([\w-]+)\/events$/);
    if (sseMatch && method === "GET") {
      return route.fulfill({
        status: 200, contentType: "text/event-stream",
        body: 'event: snapshot\ndata: ' + JSON.stringify({
          job_id: JOB_ID, state_revision: 4, state: "running",
          phase: "outline", completed_slides: 0, total_slides: 0,
          warnings: [],
        }) + "\n\n",
      });
    }
    return route.fallback();
  });
  return cap;
}

test("一次生成后关闭重进仍是同一课程与任务", async ({ page }) => {
  const cap = await routeCreateFlow(page);

  await page.goto(`/workspaces/${WS_ID}/classroom`);
  await expect(page.getByText("还没有课程")).toBeVisible();

  // 打开一键备课，默认值可见，填主题后一次生成
  await page.getByRole("button", { name: "一键备课" }).click();
  const dialog = page.locator('.motion-modal');
  await expect(dialog).toBeVisible();
  await dialog.locator("input").first()
    .fill("动量守恒与系统边界");
  await page.getByRole("button", { name: "生成课程" }).click();

  expect(cap.createCalls).toBe(1);
  expect(cap.idemKey.length).toBeGreaterThanOrEqual(16);
  expect((cap.body?.brief as Record<string, unknown>)?.topic)
    .toBe("动量守恒与系统边界");
  expect(cap.body?.start_mode).toBe("automatic");

  // 跳转详情页：生成阶段可见（关闭后进度不丢——worker 在服务端）
  await expect(page.getByText("组织课程")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("课程尚未生成完成")).toBeVisible();

  // 「关闭」回列表：同一课程卡（generating）+ 同一 job
  await page.goBack();
  await expect(page.getByText("动量守恒入门")).toBeVisible({
    timeout: 10_000 });
  await expect(page.locator("span").filter({ hasText: "生成中" })
    .first()).toBeVisible();

  // 重进详情：仍是同一 job（不重复建课）
  await page.getByRole("link", { name: "动量守恒入门" }).first().click();
  await expect(page.getByText("组织课程")).toBeVisible({ timeout: 10_000 });
  expect(cap.createCalls).toBe(1);
});

test("空教材且无外部服务时仍有明确路径", async ({ page }) => {
  const cap = await routeCreateFlow(page);
  // 覆盖能力：外部服务全部不可用
  await page.unroute("**/api/v1/classroom/capabilities");
  await page.route("**/api/v1/classroom/capabilities", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({
        ...CAPABILITIES_FULL,
        tts: { ...CAPABILITIES_FULL.tts, configured: false, available: false,
               reason: "no_key" },
        research: { configured: false, available: false, reason: "no_key" },
        images: { configured: false, available: false, reason: "no_key" },
      }) });
  });

  await page.goto(`/workspaces/${WS_ID}/classroom`);
  await page.getByRole("button", { name: "一键备课" }).click();
  const dialog = page.locator('.motion-modal');
  await expect(dialog).toBeVisible();

  // 空教材提示 + 通识路径说明
  await expect(dialog.getByText("本学习区还没有可选教材")).toBeVisible();
  await expect(
    dialog.getByText("未选教材时将按「通识资料」生成主题课程")).toBeVisible();

  // 展开更多设置：联网开关在无 key 时禁用 + 明确提示；仍可提交
  await dialog.getByRole("button", { name: "更多设置" }).click();
  const researchBox = dialog.locator("input[type='checkbox']").first();
  await expect(researchBox).toBeDisabled();
  await expect(dialog.getByText("联网检索暂不可用：no_key")).toBeVisible();
  await dialog.locator("input").first().fill("斜抛运动");
  await page.getByRole("button", { name: "生成课程" }).click();

  expect(cap.createCalls).toBe(1);
  const brief = cap.body?.brief as Record<string, unknown>;
  expect(brief?.source_policy).toBe("web_topic");
  expect((brief?.research as Record<string, unknown>)?.enabled)
    .toBe(false);
});
