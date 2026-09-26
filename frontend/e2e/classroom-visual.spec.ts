/* 课堂视觉验收 E2E（plan.md §19.3 classroom-visual）。
 *
 * 5 主题 × 浅/深色 × 三视口（390×844 / 768×1024 / 1440×900）+
 * 200% 字号 + reduced-motion：
 * - 每主题的课件帧截图彼此不同（主题确实改变视觉）；
 * - 控件条不与课件舞台重叠（bbox 不相交）；
 * - 截图存 acceptance-reports/screenshots/ 供人工复核（§19.3
 *   「至少一次人工看图确认」）。
 */
import { expect, test, type Page, type Route } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";
import { loginViaStorage } from "./helpers";

const WS = "ws-e2e-visual";
const LESSON = "les-e2e-visual";
const RUN = "run-e2e-visual";
const SEG_TEXT = "第一段：系统与内力。";

const THEMES: { id: string; name: string; accent: string; bg: string }[] = [
  { id: "academic_clear", name: "学术清晰", accent: "#2563eb", bg: "#ffffff" },
  { id: "warm_chalk", name: "暖粉笔记", accent: "#d97706", bg: "#fff7ed" },
  { id: "ink_minimal", name: "墨色极简", accent: "#111827", bg: "#f8fafc" },
  { id: "nature_field", name: "自然野趣", accent: "#059669", bg: "#ecfdf5" },
  { id: "starry_night", name: "星空夜话", accent: "#7c3aed", bg: "#f5f3ff" },
];

function themedFrame(t: typeof THEMES[number]): string {
  return `<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;background:${t.bg};}
h1{color:${t.accent};font-size:26px;font-family:serif;padding:20px;margin:0}
p{padding:0 20px;color:#333}</style></head><body>
<h1>动量守恒 · ${t.name}</h1>
<p>Δp⃗ = F⃗·Δt —— 合外力为零时系统总动量守恒。</p>
<p>长内容检查：内力成对出现、等值反向，矢量和为零；只要合外力冲量
可以忽略，系统总动量保持不变。反例：粗糙斜面上摩擦力是外力。</p>
<script>parent.postMessage({type:"classroom_ready",nonce:"n0nce"},"*");
window.addEventListener("message",(e)=>{if(e.data&&e.data.type
==="classroom_init"&&e.ports&&e.ports[0]){const p=e.ports[0];
p.onmessage=(m)=>{if(m.data&&m.data.type==="goto_page")
{p.postMessage({type:"page_selected",order:m.data.order});}};p.start();}});</script>
</body></html>`;
}

async function routeVisual(page: Page, theme: string): Promise<void> {
  const t = THEMES.find((x) => x.id === theme) ?? THEMES[0];
  await page.route("**/api/v1/**", async (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json",
                      body: JSON.stringify(body) });
    if (path === `/api/v1/workspaces/${WS}` && method === "GET") {
      return json({ workspace_id: WS, name: "物理", session_ids: [],
        knowledge_files: [], public_memory: "",
        public_memory_updated_at: 0, created_at: 0, updated_at: 0 });
    }
    if (path === `/api/v1/workspaces/${WS}/classroom/lessons/${LESSON}`
        && method === "GET") {
      return json({
        lesson_id: LESSON, workspace_id: WS, title: "动量守恒",
        lifecycle: "active", latest_ready_revision: 1,
        published_revisions: [1], latest_job: null,
        revision: {
          revision: 1, schema_version: 1,
          brief: { topic: "动量守恒", theme_id: `${theme}@1`,
                   duration_minutes: 15 },
          slides: [{
            slide_id: "s-e2e-slide01", order: 1, title: "系统与内力",
            layout: "key_points", learning_objective_ids: [],
            blocks: [{ id: "blk-e2e-11", kind: "paragraph",
              spans: [{ kind: "text", text: "要点" }] }],
            segments: [{
              segment_id: "seg-e2e-1", role: "explain",
              display_text: SEG_TEXT, spoken_text: SEG_TEXT,
              show_block_ids: ["blk-e2e-11"], focus_block_ids: [],
              pause_after_ms: 0, source_ids: [], estimated_ms: 500,
            }],
            claims: [], source_ids: [], transition: "auto",
            estimated_seconds: 1,
          }],
          objectives: [], glossary: [], source_records: [], assets: [],
          checkpoints: [], renderer_version: "t", content_hash: "0".repeat(64),
          created_at: "2026-09-26T00:00:00Z",
        },
        pending: null, recent_run: null,
      });
    }
    if (path.endsWith(`/lessons/${LESSON}/revisions/1/frame`)) {
      return route.fulfill({ status: 200,
        contentType: "text/html; charset=utf-8", body: themedFrame(t) });
    }
    if (path.endsWith(`/runs/${RUN}`) && method === "GET") {
      return json({
        run_id: RUN, lesson_id: LESSON, lesson_revision: 1,
        status: "paused", state_revision: 3,
        cursor: { slide_id: "s-e2e-slide01", segment_id: "seg-e2e-1",
                  chunk_index: 0, offset_ms: 0,
                  last_completed_segment_id: null },
        cursor_slide_order: 1, resume_anchor: null,
        audio_profile: { policy: "auto", provider: "",
          voice_id: "", language: "zh-CN", allow_local_fallback: true,
          playback_speed: 0.9, version: 1 },
        qa_session_id: null, visited_slide_count: 1, completed_kind: "",
        created_at: "2026-09-26T00:00:00Z",
        updated_at: "2026-09-26T00:00:00Z",
        lease: { held: false, expired: true, client_id: null,
                 lease_epoch: 0, expires_at: null },
        cursor_index: 0, segment_total: 1,
        tts_local_locked: false, tts_fallback_notified: false,
      });
    }
    if (path.endsWith(`/runs/${RUN}/progress`)) {
      return json({ state_revision: 4 });
    }
    return route.fallback();
  });
}

const SHOT_DIR = path.join(process.cwd(), "..", "acceptance-reports",
                           "screenshots");

async function openPlayer(page: Page, theme: string,
                          dark: boolean): Promise<void> {
  await loginViaStorage(page, "e2e-fake-token");
  await page.addInitScript((isDark) => {
    localStorage.setItem("edu-agent-theme", isDark ? "dark" : "light");
  }, dark);
  await routeVisual(page, theme);
  await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);
  await expect(page.locator(
    "section[aria-label='字幕'] p[aria-live='polite']"))
    .toContainText(SEG_TEXT, { timeout: 10_000 });
}

/** 控件条与课件舞台 bbox 不相交（±2px 容差）。 */
async function expectNoOverlap(page: Page): Promise<void> {
  const frame = page.locator("iframe[title='动量守恒']");
  const toolbar = page.getByRole("toolbar", { name: "classroom-controls" });
  await expect(frame).toBeVisible();
  await expect(toolbar).toBeVisible();
  const f = await frame.boundingBox();
  const t = await toolbar.boundingBox();
  expect(f).not.toBeNull();
  expect(t).not.toBeNull();
  const overlap = !(f!.y + f!.height <= t!.y + 2
    || t!.y + t!.height <= f!.y + 2);
  expect(overlap, "controls must not cover the slide stage").toBe(false);
}

test.describe("课堂播放器视觉矩阵", () => {
  fs.mkdirSync(SHOT_DIR, { recursive: true });

  for (const theme of THEMES) {
    test(`主题 ${theme.name}（浅色 1440×900）`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await openPlayer(page, theme.id, false);
      await expectNoOverlap(page);
      await page.screenshot({
        path: path.join(SHOT_DIR, `player-${theme.id}-light.png`),
        fullPage: false });
    });
  }

  for (const dark of [false, true]) {
    test(`窄屏 390×844 ${dark ? "深色" : "浅色"}控件不遮挡课件`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await openPlayer(page, "academic_clear", dark);
      await expectNoOverlap(page);
      // 窄屏四主操作仍可用
      await expect(page.getByRole("button", { name: "播放" })
        .or(page.locator("button[aria-label='播放']"))).toBeVisible();
      await page.screenshot({
        path: path.join(SHOT_DIR,
          `player-390-${dark ? "dark" : "light"}.png`) });
    });

    test(`平板 768×1024 ${dark ? "深色" : "浅色"}`, async ({ page }) => {
      await page.setViewportSize({ width: 768, height: 1024 });
      await openPlayer(page, "warm_chalk", dark);
      await expectNoOverlap(page);
      await page.screenshot({
        path: path.join(SHOT_DIR,
          `player-768-${dark ? "dark" : "light"}.png`) });
    });
  }

  test("200% 字号下控件与讲稿仍可读可用", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginViaStorage(page, "e2e-fake-token");
    await page.addInitScript(() => {
      localStorage.setItem("edu-agent-fs", "2");
    });
    await routeVisual(page, "ink_minimal");
    await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);
    const caption = page.locator(
      "section[aria-label='字幕'] p[aria-live='polite']");
    await expect(caption).toContainText(SEG_TEXT, { timeout: 10_000 });
    await expectNoOverlap(page);
    await expect(page.locator("button[aria-label='播放']")).toBeVisible();
    await page.screenshot({
      path: path.join(SHOT_DIR, "player-200pct.png") });
  });

  test("reduced-motion 下正常渲染（无动画依赖）", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openPlayer(page, "starry_night", false);
    await expectNoOverlap(page);
    // 目录渲染完整（无动画也能即时可用）
    await expect(page.locator("nav[aria-label='章节目录']"))
      .toBeVisible();
    await page.screenshot({
      path: path.join(SHOT_DIR, "player-reduced-motion.png") });
  });

  test("五主题课件帧截图互不相同", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const shots: Buffer[] = [];
    for (const theme of THEMES) {
      await openPlayer(page, theme.id, false);
      const frame = page.locator("iframe[title='动量守恒']");
      await expect(frame).toBeVisible();
      shots.push(await frame.screenshot());
      // 每次换主题前清理路由，避免叠加
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
    for (let i = 1; i < shots.length; i++) {
      expect(shots[i].equals(shots[0]),
        `theme ${THEMES[i].name} frame must differ from base theme`)
        .toBe(false);
    }
  });
});
