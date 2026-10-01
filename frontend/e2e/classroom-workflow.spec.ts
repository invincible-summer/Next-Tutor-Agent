import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { loginViaStorage } from "./helpers";
import { JOB_ID, LESSON_ID, RUN_ID, SLIDES_2, WS_ID, jobPublic, lessonDetail, routeBase, type MutableLessonState } from "./classroom-helpers";

const lessonUrl = `/workspaces/${WS_ID}/classroom/${LESSON_ID}`;
const shotDir = "../acceptance-reports/screenshots";
test.use({ contextOptions: { reducedMotion: "reduce" } });
// Render real courseware from synthetic data, so screenshots exercise the frame
// sizing and theme instead of a simplified HTML stand-in.
const frameHtml = execFileSync(process.env.E2E_PYTHON || "python3", ["-c", `
from tests.classroom_fixtures import make_revision, make_brief, make_slide, make_para_block, make_segment
from app.classroom.render.compiler import compile_html
from app.schemas import classroom as sc
slides = []
for n, title, text in [(1, "系统与内力", "把两个碰撞的小车看作一个系统，先区分内力和外力。"), (2, "守恒条件", "当系统所受合外力的冲量可以忽略时，总动量保持不变。")]:
    block = make_para_block(n, text)
    slide = make_slide(n, title=title, layout=sc.SlideLayout.key_points,
        blocks=[block, sc.BulletsBlock(id=f"blk_{n+10:024x}", items=[[sc.SpanText(text="明确系统边界")], [sc.SpanText(text="分析外力及其冲量")], [sc.SpanText(text="判断动量是否守恒")]])],
        segments=[make_segment(n, block_ids=[block.id])])
    slides.append(slide)
brief = make_brief().model_copy(update={"theme_id": "academic_clear@2"})
revision = make_revision(slides=slides, brief=brief).model_copy(update={"renderer_version": "2.0.0"})
html = compile_html(revision)
for n in (1, 2):
    html = html.replace(f"blk_{n:024x}", f"blk-e2e-{n}1")
print(html)
`], { cwd: "../backend", encoding: "utf8", maxBuffer: 8 * 1024 * 1024 });

async function setup(page: Page, dark = false) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginViaStorage(page, "course-workflow-fixture");
  await page.addInitScript((dark) => localStorage.setItem("edu-agent-theme", dark ? "dark" : "light"), dark);
  // Fully mocked: no real account, storage or paid provider is used.
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith("/auth/me") ? { user: { id: "fixture", email: "fixture@example.com", profile: { name: "课程预览", prefs: {} }, role: "user" } }
      : path.endsWith("/auth/status") ? { auth_required: false }
      : path.endsWith("/sidebar") ? { workspaces: [], sessions: [] }
      : { enabled: false, items: [], notes: [], annotations: [] };
    await route.fulfill({ json: body });
  });
  const state: MutableLessonState = { slides: structuredClone(SLIDES_2), revision: 1,
    themeId: "academic_clear@2", lifecycle: "active", latestJobState: "succeeded", runState: "pending" };
  await routeBase(page, state, { frameHtml });
  return state;
}

for (const dark of [false, true]) test(`课程介绍不加载课件，编辑入口明确（${dark ? "深" : "浅"}色）`, async ({ page }) => {
  await setup(page, dark);
  let frames = 0;
  page.on("request", (req) => { if (req.url().includes("/frame")) frames++; });
  await page.goto(lessonUrl);
  await expect(page.getByText("课程内容预览")).toBeVisible();
  await expect(page.getByRole("button", { name: "编辑课程", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "继续上课", exact: true })).toBeVisible();
  expect(frames).toBe(0);
  await expect(page.locator("iframe")).toHaveCount(0);
  await page.screenshot({ animations: "disabled", path: `${shotDir}/course-overview-${dark ? "dark" : "light"}.png` });
  await page.getByRole("button", { name: "编辑课程", exact: true }).click();
  await expect(page.getByText("编辑课件组件")).toBeVisible();
  await expect(page.locator("iframe[title='动量守恒入门']")).toBeVisible();
  expect(frames).toBe(1);
  await page.screenshot({ animations: "disabled", path: `${shotDir}/course-editor-${dark ? "dark" : "light"}.png` });
});

test("手动保存只发组件，AI 优化只发目标与指令", async ({ page }) => {
  const state = await setup(page);
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/revisions") && route.request().method() === "POST") {
      requests.push(route.request().postDataJSON().operation);
      state.revision++;
      return route.fulfill({ json: { job_id: JOB_ID, revision: state.revision } });
    }
    if (path.endsWith(`/jobs/${JOB_ID}`)) return route.fulfill({ json: jobPublic({ state: "succeeded" }) });
    return route.fallback();
  });
  await page.goto(`${lessonUrl}?edit=1`);
  await page.getByRole("textbox", { name: "文字 1", exact: true }).fill("先确定系统边界，再判断外力。");
  await expect(page.getByRole("button", { name: "优化当前组件", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "保存组件", exact: true }).click();
  await expect(page.locator("header").getByText("版本 2", { exact: true })).toBeVisible();
  expect(requests[0]).toMatchObject({ op: "edit_content", changes: [{ op: "replace_block", block: { kind: "paragraph", spans: [{ text: "先确定系统边界，再判断外力。" }] } }] });
  await page.getByRole("textbox", { name: "优化要求", exact: true }).fill("解释更清楚");
  await page.getByRole("button", { name: "优化当前组件", exact: true }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toEqual({ op: "regenerate_block", slide_id: SLIDES_2[0].id, block_id: "blk-e2e-11", instruction: "解释更清楚" });
});

test("选页后进入全屏，浮层字幕和对话栏持续可用", async ({ page }) => {
  const state = await setup(page);
  let revision = 3;
  const run = (lessonDetail(state) as { recent_run: Record<string, unknown> }).recent_run;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith(`/lessons/${LESSON_ID}`)) {
      const detail = lessonDetail(state) as { revision: { slides: { segments: { spoken_text: string }[] }[] } };
      detail.revision.slides[0].segments[0].spoken_text += " 完整讲稿补充：系统之外的作用力才属于外力。";
      return route.fulfill({ json: detail });
    }
    if (path.endsWith(`/runs/${RUN_ID}`)) return route.fulfill({ json: { ...run, state_revision: revision,
      audio_profile: { policy: "text_only", provider: "", voice_id: "", language: "zh-CN", playback_speed: 1, version: 1 } } });
    if (path.endsWith("/lease")) return route.fulfill({ json: { lease_epoch: 2, expires_at: "2099-01-01T00:00:00Z" } });
    if (path.endsWith("/progress")) return route.fulfill({ json: { state_revision: ++revision } });
    if (path.endsWith("/chat/stream")) return route.fulfill({ contentType: "text/event-stream", body: 'event: answer\ndata: {"type":"answer","content":"可以把两辆小车视为一个系统。","is_delta":true}\n\nevent: done\ndata: {"type":"done","answer":"可以把两辆小车视为一个系统。","session_id":"fixture-qa"}\n\n' });
    return route.fallback();
  });
  await page.goto(`${lessonUrl}/learn/${RUN_ID}`);
  const shell = page.locator(".classroom-player");
  const rail = page.getByRole("navigation", { name: "课件列表", exact: true });
  const sidebar = page.getByRole("complementary", { name: "课堂侧栏" });
  await expect(rail).toBeVisible();
  await expect(shell).toHaveAttribute("data-fullscreen", "false");
  await expect(page.getByRole("region", { name: "字幕", exact: true })).toHaveCount(0);
  await expect(sidebar.getByRole("button", { name: "没听懂", exact: true })).toBeVisible();
  await rail.getByRole("button").nth(1).click();
  await expect(rail.getByRole("button").nth(1)).toHaveAttribute("aria-current", "true");
  await page.screenshot({ animations: "disabled", path: `${shotDir}/course-player-preview-light.png` });
  await page.getByRole("button", { name: "从本页开始上课", exact: true }).click();
  await expect(shell).toHaveAttribute("data-fullscreen", "true");
  await expect(rail).toHaveCount(0);
  await expect(sidebar).toBeVisible();
  await page.getByRole("button", { name: "字幕", exact: true }).click();
  const caption = page.getByRole("region", { name: "字幕", exact: true });
  await expect(caption).toBeVisible();
  const stageBox = await page.locator(".classroom-stage").boundingBox();
  const captionBox = await caption.boundingBox();
  expect(captionBox!.y).toBeGreaterThan(stageBox!.y);
  expect(captionBox!.y + captionBox!.height).toBeLessThanOrEqual(stageBox!.y + stageBox!.height);
  const slideBox = await page.frameLocator("iframe[title='动量守恒入门']").locator(".slide.current .stage").boundingBox();
  expect(captionBox!.y + captionBox!.height).toBeLessThanOrEqual(slideBox!.y + slideBox!.height);
  expect(captionBox!.y).toBeGreaterThan(slideBox!.y);
  await sidebar.getByRole("button", { name: "举个例子", exact: true }).click();
  await expect(sidebar.getByText("可以把两辆小车视为一个系统。", { exact: true })).toBeVisible();
  await sidebar.getByRole("tab", { name: "课程讲稿", exact: true }).click();
  const scriptPages = sidebar.locator("details[data-script-page]");
  await expect(scriptPages).toHaveCount(2);
  await expect(sidebar.locator("details[open]")).toHaveCount(0);
  await scriptPages.nth(0).locator("summary").click();
  await expect(scriptPages.nth(0)).toContainText("第一段：把两个碰撞的小车看成一个系统。");
  await expect(scriptPages.nth(0)).toContainText("第二段：内力成对出现，总动量不变。");
  await expect(scriptPages.nth(0)).toContainText("完整讲稿补充：系统之外的作用力才属于外力。");
  await expect(scriptPages.nth(0).locator(".script-segment")).toHaveCount(0);
  await scriptPages.nth(1).locator("summary").click();
  await expect(sidebar.getByText("第三段：只要合外力冲量可以忽略。", { exact: false })).toBeVisible();
  await page.screenshot({ animations: "disabled", path: `${shotDir}/course-player-script-light.png` });
  await sidebar.getByRole("tab", { name: "对话", exact: true }).click();
  await sidebar.getByRole("tab", { name: "课程讲稿", exact: true }).click();
  await expect(sidebar.locator("details[open]")).toHaveCount(2);
  await sidebar.getByRole("tab", { name: "对话", exact: true }).click();
  await page.screenshot({ animations: "disabled", path: `${shotDir}/course-player-fullscreen-light.png` });
  await page.getByRole("button", { name: "退出全屏", exact: true }).click();
  await expect(rail).toBeVisible();
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await expect(sidebar).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ animations: "disabled", path: `${shotDir}/course-player-narrow-dark.png` });
  await sidebar.getByRole("tab", { name: "课程讲稿", exact: true }).click();
  await expect(sidebar.locator("details[open]")).toHaveCount(2);
  await page.screenshot({ animations: "disabled", path: `${shotDir}/course-player-script-narrow-dark.png` });
});
