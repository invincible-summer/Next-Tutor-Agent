/* 课堂导出 E2E（plan.md §19.3 classroom-export）。
 *
 * - 下载课件（HTML ZIP）与讲稿（Markdown）：POST exports 带
 *   Idempotency-Key 与 format；content_url 经鉴权 GET；浏览器下载
 *   事件带期望文件名；
 * - 导出内容过期（410 export_expired）：无下载事件，按钮恢复可重试。
 *
 * 离线 ZIP 的自包含性（无外部请求、打印分页）由后端
 * test_classroom_exports 覆盖（真实 ZIP 内容检查）。
 */
import { expect, test, type Page, type Route } from "@playwright/test";
import { loginViaStorage } from "./helpers";
import {
  LESSON_ID, SLIDES_2, WS_ID, routeBase, themedFrame,
  type MutableLessonState,
} from "./classroom-helpers";

interface ExportHooks {
  posts: { format: string; revision: number; idem: string }[];
  contentFetches: number;
  contentStatus: number;
}

async function routeExport(
  page: Page, state: MutableLessonState, hooks: ExportHooks,
): Promise<void> {
  await routeBase(page, state, {
    frameHtml: themedFrame("academic_clear", "#2563eb") });
  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json",
                      body: JSON.stringify(body) });

    if (path === `/api/v1/workspaces/${WS_ID}/classroom/lessons/${LESSON_ID}`
        + "/exports" && method === "POST") {
      const body = route.request().postDataJSON() as {
        format?: string; revision?: number };
      hooks.posts.push({
        format: String(body.format),
        revision: Number(body.revision),
        idem: route.request().headers()["idempotency-key"] ?? "",
      });
      return json({ export_id: "exp_e2e_1",
                    content_url: path + "/exp_e2e_1/content" }, 202);
    }
    if (/\/exports\/[\w-]+\/content$/.test(path) && method === "GET") {
      hooks.contentFetches += 1;
      if (hooks.contentStatus !== 200) {
        return json({ error: { code: "export_expired",
          message: "导出已过期", retryable: true, request_id: "r" } },
          hooks.contentStatus);
      }
      return route.fulfill({ status: 200,
        contentType: "application/zip",
        body: Buffer.from("PK-e2e-zip") });
    }
    return route.fallback();
  });
}

test.beforeEach(async ({ page }) => {
  await loginViaStorage(page, "e2e-fake-token");
});

test("下载课件与讲稿：受理→鉴权内容→浏览器下载", async ({ page }) => {
  const state: MutableLessonState = {
    slides: JSON.parse(JSON.stringify(SLIDES_2)), revision: 3,
    themeId: "academic_clear@1", lifecycle: "active",
    latestJobState: "succeeded", runState: "pending",
  };
  const hooks: ExportHooks = { posts: [], contentFetches: 0,
                               contentStatus: 200 };
  await routeExport(page, state, hooks);
  await page.goto(`/workspaces/${WS_ID}/classroom/${LESSON_ID}`);
  await expect(page.getByText("本页讲稿")).toBeVisible({ timeout: 10_000 });

  // 下载课件（HTML ZIP）
  const zipDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载课件" }).click();
  const zip = await zipDownload;
  expect(zip.suggestedFilename())
    .toBe(`lesson-${LESSON_ID}-r3.zip`);

  // 下载讲稿（Markdown）
  const mdDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载讲稿" }).click();
  const md = await mdDownload;
  expect(md.suggestedFilename())
    .toBe(`lesson-${LESSON_ID}-r3-notes.md`);

  expect(hooks.posts.length).toBe(2);
  expect(hooks.posts[0]).toMatchObject({ format: "html_zip", revision: 3 });
  expect(hooks.posts[1]).toMatchObject({ format: "notes_md", revision: 3 });
  for (const p of hooks.posts) {
    expect(p.idem.length).toBeGreaterThanOrEqual(16);
  }
  expect(hooks.contentFetches).toBe(2);
});

test("导出过期（410）无下载且按钮可重试", async ({ page }) => {
  const state: MutableLessonState = {
    slides: JSON.parse(JSON.stringify(SLIDES_2)), revision: 1,
    themeId: "academic_clear@1", lifecycle: "active",
    latestJobState: "succeeded", runState: "pending",
  };
  const hooks: ExportHooks = { posts: [], contentFetches: 0,
                               contentStatus: 410 };
  await routeExport(page, state, hooks);
  await page.goto(`/workspaces/${WS_ID}/classroom/${LESSON_ID}`);
  const btn = page.getByRole("button", { name: "下载课件" });
  await expect(btn).toBeVisible({ timeout: 10_000 });

  let downloads = 0;
  page.on("download", () => { downloads += 1; });
  await btn.click();
  await page.waitForTimeout(600);
  expect(downloads).toBe(0);
  // 按钮不再处于 busy：可再次点击（重试生成新导出）
  await expect(btn).toBeEnabled();
  await btn.click();
  expect(hooks.posts.length).toBe(2);
});
