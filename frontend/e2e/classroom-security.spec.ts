/* 课堂安全 E2E（plan.md §19.3 classroom-security）。
 *
 * - 课件 iframe：sandbox=allow-scripts（无 allow-same-origin）、srcdoc
 *   注入（URL 不带 token）；伪造 postMessage 无效果（source 校验）；
 * - 鉴权：frame/audio 内容请求都带 Authorization 头（无 query token）；
 * - 资源 404：audio 内容 404 时播放器优雅降级，目录导航仍可用。
 */
import { expect, test, type Page, type Route } from "@playwright/test";
import { loginViaStorage } from "./helpers";

const WS = "ws-e2e-sec";
const LESSON = "les-e2e-sec";
const RUN = "run-e2e-sec";
const SEGS = [
  { id: "seg-e2e-1", text: "第一段：系统与内力。" },
  { id: "seg-e2e-2", text: "第二段：守恒条件。" },
];

function tinyWav(): Buffer {
  const rate = 8000;
  const data = Buffer.alloc(rate * 2);  // 1000ms
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

async function routeSec(
  page: Page, hooks: {
    authHeaders: Record<string, string | undefined>[];
    audioContentStatus: number;
  },
): Promise<void> {
  const wav = tinyWav();
  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const headers = route.request().headers() as Record<string, string>;
    const auth = headers.authorization ?? headers.Authorization;
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
      hooks.authHeaders.push({ frame: auth });
      return json({
        lesson_id: LESSON, workspace_id: WS, title: "动量守恒",
        lifecycle: "active", latest_ready_revision: 1,
        published_revisions: [1], latest_job: null,
        revision: {
          revision: 1, schema_version: 1,
          brief: { topic: "动量守恒", theme_id: "academic_clear@1",
                   duration_minutes: 15 },
          slides: [1, 2].map((n) => ({
            slide_id: `s-e2e-slide0${n}`, order: n, title: `第 ${n} 页`,
            layout: "key_points", learning_objective_ids: [],
            blocks: [{ id: `blk-e2e-${n}1`, kind: "paragraph",
              spans: [{ kind: "text", text: `要点 ${n}` }] }],
            segments: [{
              segment_id: SEGS[n - 1].id, role: "explain",
              display_text: SEGS[n - 1].text,
              spoken_text: SEGS[n - 1].text,
              show_block_ids: [`blk-e2e-${n}1`], focus_block_ids: [],
              pause_after_ms: 0, source_ids: [], estimated_ms: 500,
            }],
            claims: [], source_ids: [], transition: "auto",
            estimated_seconds: 1,
          })),
          objectives: [], glossary: [], source_records: [], assets: [],
          checkpoints: [], renderer_version: "t", content_hash: "0".repeat(64),
          created_at: "2026-09-26T00:00:00Z",
        },
        pending: null, recent_run: null,
      });
    }
    if (path.endsWith(`/lessons/${LESSON}/revisions/1/frame`)) {
      hooks.authHeaders.push({ frameContent: auth });
      // 断言 URL 无 token（鉴权只在头）
      if (url.search.includes("token")) {
        return json({ error: "token in query" }, 400);
      }
      return route.fulfill({ status: 200,
        contentType: "text/html; charset=utf-8",
        body: "<!doctype html><html><body><div id='v'></div></body></html>" });
    }
    if (path.endsWith(`/runs/${RUN}`) && method === "GET") {
      return json({
        run_id: RUN, lesson_id: LESSON, lesson_revision: 1,
        status: "paused", state_revision: 3,
        cursor: { slide_id: "s-e2e-slide01", segment_id: SEGS[0].id,
                  chunk_index: 0, offset_ms: 0,
                  last_completed_segment_id: null },
        cursor_slide_order: 1, resume_anchor: null,
        audio_profile: { policy: "auto", provider: "azure",
          voice_id: "v", language: "zh-CN", allow_local_fallback: true,
          playback_speed: 0.9, version: 1 },
        qa_session_id: null, visited_slide_count: 1, completed_kind: "",
        created_at: "2026-09-26T00:00:00Z",
        updated_at: "2026-09-26T00:00:00Z",
        lease: { held: false, expired: true, client_id: null,
                 lease_epoch: 0, expires_at: null },
        cursor_index: 0, segment_total: SEGS.length,
        tts_local_locked: false, tts_fallback_notified: false,
      });
    }
    if (path.endsWith(`/runs/${RUN}/lease`)) {
      return json({ lease_epoch: 3,
                    expires_at: "2026-09-26T12:00:00Z" });
    }
    if (path.endsWith(`/runs/${RUN}/audio`) && method === "POST") {
      const body = route.request().postDataJSON() as {
        segment_ids?: string[] };
      return json({ clips: (body.segment_ids ?? []).slice(0, 3)
        .map((id) => ({ clip_id: `clip-${id}`, state: "ready",
                        status_url: "/s", content_url: "/c" })) }, 202);
    }
    if (/\/audio\/clip-[\w-]+$/.test(path)) {
      return json({ state: "ready", duration_ms: 500, provider: "azure",
                    voice_id: "v" });
    }
    if (/\/audio\/clip-[\w-]+\/content$/.test(path)) {
      hooks.authHeaders.push({ audioContent: auth });
      if (hooks.audioContentStatus !== 200) {
        return json({ error: { code: "source_not_found",
          message: "另一账户资源", retryable: false, request_id: "r" } },
          hooks.audioContentStatus);
      }
      return route.fulfill({ status: 200, contentType: "audio/wav",
                             body: wav });
    }
    if (path.endsWith(`/runs/${RUN}/progress`)) {
      return json({ state_revision: 4 });
    }
    return route.fallback();
  });
}

test.beforeEach(async ({ page }) => {
  await loginViaStorage(page, "e2e-fake-token");
});

test("课件 iframe 沙箱隔离且伪造 postMessage 无效", async ({ page }) => {
  const hooks = { authHeaders: [] as Record<string, string | undefined>[],
                  audioContentStatus: 200 };
  await routeSec(page, hooks);
  await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);
  await expect(page.locator(
    "section[aria-label='字幕'] p[aria-live='polite']"))
    .toContainText(SEGS[0].text, { timeout: 10_000 });

  // sandbox 只有 allow-scripts；无 allow-same-origin；srcdoc 而非 src
  const sandbox = await page.locator("iframe[title='动量守恒']")
    .getAttribute("sandbox");
  expect(sandbox).toBe("allow-scripts");
  expect(await page.locator("iframe[title='动量守恒']")
    .getAttribute("src")).toBeNull();
  const srcdoc = await page.locator("iframe[title='动量守恒']")
    .getAttribute("srcdoc");
  expect(srcdoc).toContain("<html");

  // 伪造 handshake（source 不是 iframe.contentWindow）：被忽略，不崩溃
  await page.evaluate(() => window.postMessage(
    { type: "classroom_ready", nonce: "evil" }, "*"));
  await page.evaluate(() => window.postMessage(
    { type: "classroom_init", nonce: "evil" }, "*"));
  await page.waitForTimeout(400);
  // 播放器仍可用：目录跳转到第 2 页成功
  await page.click("nav[aria-label='章节目录'] li:nth-child(2) button");
  await expect(page.locator("footer span", { hasText: "2 / 2" }))
    .toBeVisible({ timeout: 10_000 });
});

test("frame 与音频内容请求都经 Authorization 头鉴权", async ({ page }) => {
  const hooks = { authHeaders: [] as Record<string, string | undefined>[],
                  audioContentStatus: 200 };
  await routeSec(page, hooks);
  // 假 token 过 /auth/me 校验，避免被登出逻辑清除（否则后续请求无鉴权头）
  await page.route("**/api/v1/auth/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) {
      return route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ user: { id: "u-e2e",
          email: "e2e@example.com", username: "E2E",
          profile: { name: "E2E" }, role: "student" } }) });
    }
    if (path.endsWith("/auth/status")) {
      return route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ auth_required: true }) });
    }
    return route.fallback();
  });
  await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);
  await expect(page.locator(
    "section[aria-label='字幕'] p[aria-live='polite']"))
    .toContainText(SEGS[0].text, { timeout: 10_000 });
  await page.click("button[aria-label='播放']");
  await expect.poll(() => hooks.authHeaders.filter(
    (h) => h.audioContent !== undefined).length).toBeGreaterThan(0);

  const frameAuth = hooks.authHeaders.find((h) => h.frameContent);
  expect(frameAuth?.frameContent).toBe("Bearer e2e-fake-token");
  const audioAuth = hooks.authHeaders.find((h) => h.audioContent);
  expect(audioAuth?.audioContent).toBe("Bearer e2e-fake-token");
});

test("他人资源 404/410 时播放器优雅降级", async ({ page }) => {
  const hooks = { authHeaders: [] as Record<string, string | undefined>[],
                  audioContentStatus: 404 };
  await routeSec(page, hooks);
  await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);
  await expect(page.locator(
    "section[aria-label='字幕'] p[aria-live='polite']"))
    .toContainText(SEGS[0].text, { timeout: 10_000 });

  // 音频内容 404：无声音但不崩溃；目录导航/讲稿仍可用
  await page.click("button[aria-label='播放']");
  await page.waitForTimeout(800);
  await page.click("nav[aria-label='章节目录'] li:nth-child(2) button");
  await expect(page.locator("footer span", { hasText: "2 / 2" }))
    .toBeVisible({ timeout: 10_000 });
  await expect(page.locator("aside").getByText(SEGS[1].text,
    { exact: false })).toBeVisible();
});
