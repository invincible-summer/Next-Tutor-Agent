/* 课堂编辑器 E2E（plan.md §19.3 classroom-editor）。
 *
 * 覆盖：逐页讲稿、换主题、换图、单页重生成、双窗口编辑 409（修订冲突
 * 提示 + 重载）、删除页后页码钳位、最后一页不可删、继续上课深链。
 */
import { expect, test, type Route } from "@playwright/test";
import { loginViaStorage } from "./helpers";
import {
  JOB_ID, LESSON_ID, RUN_ID, SLIDES_2, WS_ID, jobPublic, routeBase,
  themedFrame, type MutableLessonState,
} from "./classroom-helpers";

function freshState(): MutableLessonState {
  return { slides: JSON.parse(JSON.stringify(SLIDES_2)), revision: 1,
           themeId: "academic_clear@1", lifecycle: "active",
           latestJobState: "succeeded", runState: "pending" };
}

interface OpCapture {
  ops: Record<string, unknown>[];
  idemKeys: string[];
  conflictOnce: boolean;
  imageSearches: number;
}

async function routeEditor(
  page: import("@playwright/test").Page,
  state: MutableLessonState,
): Promise<OpCapture> {
  const cap: OpCapture = { ops: [], idemKeys: [], conflictOnce: false,
                           imageSearches: 0 };
  await routeBase(page, state, { frameHtml: themedFrame(
    "academic_clear", "#2563eb") });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json",
                      body: JSON.stringify(body) });

    // POST /revisions：编辑/重生成/换主题/换图 都走这里
    if (path === `/api/v1/workspaces/${WS_ID}/classroom/lessons/${LESSON_ID}`
        + "/revisions" && method === "POST") {
      const op = (route.request().postDataJSON() as {
        operation?: Record<string, unknown>; }).operation ?? {};
      cap.ops.push(op);
      cap.idemKeys.push(route.request().headers()["idempotency-key"] ?? "");
      if (op.op !== "regenerate_slide" && cap.conflictOnce) {
        cap.conflictOnce = false;
        return json({ error: { code: "revision_conflict",
          message: "base_revision 已被更新", retryable: true,
          request_id: "req_e2e" } }, 409);
      }
      return json({ job_id: JOB_ID, lesson_id: LESSON_ID,
                    target_revision: state.revision + 1,
                    status_url: "/s", events_url: "/e" }, 201);
    }
    const jobMatch = path.match(/\/jobs\/([\w-]+)$/);
    if (jobMatch && method === "GET") {
      return json(jobPublic({ state: "succeeded", phase: null,
        progress: { completed_slides: 0, total_slides: 0 } }));
    }
    if (path === `/api/v1/workspaces/${WS_ID}/classroom/image-search`
        && method === "POST") {
      cap.imageSearches += 1;
      return json({ candidates: [
        { candidate_id: "cand_e2e_1", provider: "pexels",
          provider_asset_id: "px_1", creator: "E2E 摄影师",
          thumbnail_url: "", width: 640, height: 360 },
        { candidate_id: "cand_e2e_2", provider: "pixabay",
          provider_asset_id: "pb_2", creator: "E2E 画师",
          thumbnail_url: "", width: 640, height: 360 },
      ] }, 202);
    }
    return route.fallback();
  });
  return cap;
}

test.beforeEach(async ({ page }) => {
  await loginViaStorage(page, "e2e-fake-token");
});

test("逐页讲稿可见并可通过编辑生成新版本", async ({ page }) => {
  const state = freshState();
  const cap = await routeEditor(page, state);
  await page.goto(`/workspaces/${WS_ID}/classroom/${LESSON_ID}`);

  // 讲稿 tab 默认打开：显示当前页讲稿段（role 徽标 + 文本）
  await expect(page.getByText("本页讲稿")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("第一段：把两个碰撞的小车看成一个系统。"))
    .toBeVisible();

  // 编辑讲稿并保存 → edit_content/replace_slide 修订 → 重载到 v2
  await page.getByRole("button", { name: "编辑本页" }).click();
  const segBox = page.locator("textarea").first();
  await segBox.fill("改写后的第一段：先选定系统边界。");
  await page.getByRole("button", { name: "保存并生成新版本" }).click();
  await expect(cap.ops.length).toBeGreaterThan(0);
  const op = cap.ops[0] as { op?: string; changes?: [{
    op?: string; slide?: { segments?: { spoken_text?: string }[] } }] };
  expect(op.op).toBe("edit_content");
  expect(op.changes?.[0].slide?.segments?.[0].spoken_text)
    .toBe("改写后的第一段：先选定系统边界。");

  // 修订成功后自动重载（GET lesson 再次返回 v2 frame）
  state.revision = 2;
  await expect(page.locator("header span").filter({ hasText: "版本 2" }))
    .toBeVisible({ timeout: 15_000 });
});

test("换主题与换图走确定性快速修订", async ({ page }) => {
  const state = freshState();
  state.slides[0].withImage = true; // 换图流程需要 image block
  const cap = await routeEditor(page, state);
  await page.goto(`/workspaces/${WS_ID}/classroom/${LESSON_ID}`);
  await expect(page.getByText("本页讲稿")).toBeVisible({ timeout: 10_000 });

  // 设置 tab：换主题（第二个主题 = 暖粉笔记）
  await page.getByRole("tab", { name: "设置" }).click();
  await page.getByRole("radio", { name: /暖粉笔记/ }).click();
  await expect(cap.ops.some((o) => (o as { op?: string }).op
    === "change_theme")).toBe(true);
  state.revision += 1;
  state.themeId = "warm_chalk@1";
  await expect(page.locator("header span")
    .filter({ hasText: `版本 ${state.revision}` }))
    .toBeVisible({ timeout: 15_000 });

  // 换图：修订重载后回到讲稿 tab，先切回设置
  await page.getByRole("tab", { name: "设置" }).click();
  await page.getByRole("button", { name: "搜索候选图" }).click();
  await expect(page.getByText("E2E 摄影师")).toBeVisible();
  await page.getByRole("button", { name: /E2E 摄影师/ }).click();
  await expect(cap.ops.some((o) => (o as { op?: string }).op
    === "replace_image")).toBe(true);
  expect(cap.imageSearches).toBe(1);
});

test("单页重生成展示生成进度", async ({ page }) => {
  const state = freshState();
  const cap = await routeEditor(page, state);
  // 重生成 job 保持 running（SSE 不可用→轮询 GET J）
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith(`/lessons/${LESSON_ID}/jobs/${JOB_ID}`)
        && !path.endsWith("/events")) {
      return route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify(jobPublic({ state: "running",
          phase: "author_slides" })) });
    }
    return route.fallback();
  });
  await page.goto(`/workspaces/${WS_ID}/classroom/${LESSON_ID}`);
  await expect(page.getByText("本页讲稿")).toBeVisible({ timeout: 10_000 });

  await page.getByRole("tab", { name: "设置" }).click();
  await page.getByRole("button", { name: "更详细" }).click();
  const regenOp = cap.ops.find((o) => (o as { op?: string }).op
    === "regenerate_slide") as { slide_id?: string; instruction?: string };
  expect(regenOp?.slide_id).toBe(SLIDES_2[0].id);
  expect(regenOp?.instruction).toContain("详细");
  // GenerationProgress 嵌入右侧栏（编写课件与讲稿阶段）
  await expect(page.getByText("编写课件与讲稿").first())
    .toBeVisible({ timeout: 10_000 });
});

test("双窗口编辑 409 提示冲突并支持重载", async ({ page }) => {
  const state = freshState();
  const cap = await routeEditor(page, state);
  cap.conflictOnce = true; // 第一个非重生成 op 返回 409
  await page.goto(`/workspaces/${WS_ID}/classroom/${LESSON_ID}`);
  await expect(page.getByText("本页讲稿")).toBeVisible({ timeout: 10_000 });

  await page.getByRole("tab", { name: "设置" }).click();
  await page.getByRole("radio", { name: /墨色极简/ }).click();
  await expect(page.getByText("内容已更新：有人（或另一个标签页）已先生成新版本。"))
    .toBeVisible({ timeout: 10_000 });

  // 重新载入：恢复编辑能力（此时不再 409）；重载后回到讲稿 tab，再切设置
  await page.getByRole("button", { name: "重新载入" }).click();
  await expect(page.getByText("本页讲稿")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("tab", { name: "设置" }).click();
  await page.getByRole("radio", { name: /墨色极简/ }).click();
  await expect(cap.ops.some((o) => (o as { op?: string }).op
    === "change_theme")).toBe(true);
});

test("删除页后页码钳位且最后一页不可删", async ({ page }) => {
  const state = freshState();
  const cap = await routeEditor(page, state);
  // 删除后同步缩减 slides（onReload 重新 GET lesson）
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === `/api/v1/workspaces/${WS_ID}/classroom/lessons`
        + `/${LESSON_ID}` && route.request().method() === "GET"
        && cap.ops.some((o) => (o as { op?: string }).op === "edit_content")) {
      const one = { ...state, slides: [state.slides[0]] };
      return route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify(
          (await import("./classroom-helpers")).lessonDetail(one)) });
    }
    return route.fallback();
  });
  await page.goto(`/workspaces/${WS_ID}/classroom/${LESSON_ID}`);
  await expect(page.getByText("本页讲稿")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("1 / 2")).toBeVisible();

  // 跳到末页（第 2 页）并删除
  await page.getByRole("button", { name: "下一页" }).click();
  await page.getByRole("tab", { name: "设置" }).click();
  await page.getByRole("button", { name: "删除本页" }).click();
  await expect(cap.ops.some((o) => {
    const c = o as { op?: string; changes?: [{ op?: string }] };
    return c.op === "edit_content" && c.changes?.[0].op === "delete_slide";
  })).toBe(true);
  // 重载后只剩 1 页且页码钳位到末页；重载回到讲稿 tab，切回设置断言禁用
  await expect(page.getByText("1 / 1")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("tab", { name: "设置" }).click();
  await expect(page.getByRole("button", { name: "删除本页" })).toBeDisabled();
  await expect(page.getByText("最后一页不能删除。")).toBeVisible();
});

test("继续上课直达既有 run，不换版本", async ({ page }) => {
  const state = freshState();
  await routeEditor(page, state);
  await page.goto(`/workspaces/${WS_ID}/classroom/${LESSON_ID}`);
  await expect(page.getByText("本页讲稿")).toBeVisible({ timeout: 10_000 });

  // recent_run 为 paused → 按钮文案「继续上课」，点击直达 learn 路由
  await page.getByRole("button", { name: "继续上课" }).click();
  await expect(page).toHaveURL(new RegExp(
    `/classroom/${LESSON_ID}/learn/${RUN_ID}$`));
});
