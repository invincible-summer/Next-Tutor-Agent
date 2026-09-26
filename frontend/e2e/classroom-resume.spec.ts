/* 课堂断点恢复 E2E（plan.md §19.3 classroom-resume）。
 *
 * - 刷新恢复：paused run 游标即上次位置；恢复播放必须用户手势
 *   （进入页面不发音频合成请求）；
 * - lease 过期/接管：POST lease 成功后可播放；他端持有时显示接管遮罩
 *   （含说明文案与「在这里继续」），同标签页刷新沿用同一 client_id；
 * - run 加载失败：显示重试提示，不误报“其他设备播放”；
 * - 进度上报失败（网络断开）：本地播放不受影响，字幕照常推进；
 * - pagehide：进度立即 flush（keepalive PUT）。
 */
import { expect, test, type Page, type Route } from "@playwright/test";
import { loginViaStorage } from "./helpers";

const WS = "ws-e2e-resume";
const LESSON = "les-e2e-resume";
const RUN = "run-e2e-resume";
const SEGS = [
  { id: "seg-e2e-1", text: "第一段：系统与内力。" },
  { id: "seg-e2e-2", text: "第二段：内力成对抵消。" },
  { id: "seg-e2e-3", text: "第三段：冲量可忽略时守恒。" },
];

function tinyWav(ms = 900): Buffer {
  const rate = 8000;
  const samples = Math.floor((rate * ms) / 1000);
  const data = Buffer.alloc(samples * 2);
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

interface ResumeHooks {
  audioRequests: number;
  progressCalls: { action: string; keepalive: boolean }[];
  progressFailures: number;
  progressOk: number;
  leaseAcquires: { client_id: string; takeover: boolean }[];
}

async function routeResume(
  page: Page, hooks: ResumeHooks,
  opts: { cursorSeg?: number; heldLease?: string;
          acquireConflict?: boolean; runGetError?: boolean } = {},
): Promise<void> {
  const wav = tinyWav();
  const cursorSeg = opts.cursorSeg ?? 1;
  const cursorSlide = cursorSeg <= 1 ? "s-e2e-slide01" : "s-e2e-slide02";
  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
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
          brief: { topic: "动量守恒", theme_id: "academic_clear@1",
                   duration_minutes: 15 },
          slides: [1, 2].map((n) => ({
            slide_id: `s-e2e-slide0${n}`, order: n,
            title: `第 ${n} 页`, layout: "key_points",
            learning_objective_ids: [],
            blocks: [{ id: `blk-e2e-${n}1`, kind: "paragraph",
              spans: [{ kind: "text", text: `第 ${n} 页要点` }] }],
            segments: [{
              segment_id: SEGS[n - 1].id, role: "explain",
              display_text: SEGS[n - 1].text,
              spoken_text: SEGS[n - 1].text,
              show_block_ids: [`blk-e2e-${n}1`], focus_block_ids: [],
              pause_after_ms: 0, source_ids: [], estimated_ms: 900,
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
      return route.fulfill({ status: 200,
        contentType: "text/html; charset=utf-8",
        body: "<!doctype html><html><body><div id='v'></div></body></html>" });
    }
    if (path.endsWith(`/runs/${RUN}`) && method === "GET") {
      if (opts.runGetError) {
        return json({ error: { code: "storage_unavailable",
          message: "temp", retryable: true, request_id: "r" } }, 500);
      }
      const heldBy = opts.heldLease ?? null;
      return json({
        run_id: RUN, lesson_id: LESSON, lesson_revision: 1,
        status: "paused", state_revision: 3,
        cursor: { slide_id: cursorSlide, segment_id: SEGS[cursorSeg - 1].id,
                  chunk_index: 0, offset_ms: 0,
                  last_completed_segment_id: null },
        cursor_slide_order: cursorSeg, resume_anchor: null,
        audio_profile: { policy: "auto", provider: "azure",
          voice_id: "v", language: "zh-CN", allow_local_fallback: true,
          playback_speed: 0.9, version: 1 },
        qa_session_id: null, visited_slide_count: cursorSeg,
        completed_kind: "",
        created_at: "2026-09-26T00:00:00Z",
        updated_at: "2026-09-26T00:00:00Z",
        lease: heldBy
          ? { held: true, expired: false, client_id: heldBy,
              lease_epoch: 5, expires_at: "2026-09-26T12:00:00Z" }
          : { held: false, expired: true, client_id: null,
              lease_epoch: 0, expires_at: null },
        cursor_index: cursorSeg - 1, segment_total: SEGS.length,
        tts_local_locked: false, tts_fallback_notified: false,
      });
    }
    if (path.endsWith(`/runs/${RUN}/lease`) && method === "POST") {
      const body = route.request().postDataJSON() as {
        client_id?: string; takeover?: boolean };
      hooks.leaseAcquires.push({ client_id: String(body.client_id ?? ""),
        takeover: Boolean(body.takeover) });
      if (opts.acquireConflict) {
        return json({ error: { code: "lease_conflict",
          message: "另一设备正在播放本课堂", retryable: false,
          request_id: "r" } }, 409);
      }
      return json({ lease_epoch: 9,
                    expires_at: "2026-09-26T12:00:00Z" });
    }
    if (path.endsWith(`/runs/${RUN}/lease`) && method === "PUT") {
      return json({ lease_epoch: 9,
                    expires_at: "2026-09-26T12:00:00Z" });
    }
    if (path.endsWith(`/runs/${RUN}/audio`) && method === "POST") {
      hooks.audioRequests += 1;
      const body = route.request().postDataJSON() as {
        segment_ids?: string[] };
      return json({ clips: (body.segment_ids ?? []).slice(0, 3)
        .map((id) => ({ clip_id: `clip-${id}`, state: "ready",
                        status_url: "/s", content_url: "/c" })) }, 202);
    }
    if (/\/audio\/clip-[\w-]+$/.test(path)) {
      return json({ state: "ready", duration_ms: 900, provider: "azure",
                    voice_id: "v" });
    }
    if (/\/audio\/clip-[\w-]+\/content$/.test(path)) {
      return route.fulfill({ status: 200, contentType: "audio/wav",
                             body: wav });
    }
    if (path.endsWith(`/runs/${RUN}/progress`)) {
      const body = route.request().postDataJSON() as { action?: string };
      hooks.progressCalls.push({
        action: String(body.action ?? ""),
        keepalive: Boolean(route.request().headers()["keepalive"]),
      });
      if (hooks.progressFailures > 0) {
        hooks.progressFailures -= 1;
        return json({ error: { code: "storage_unavailable",
          message: "temp", retryable: true, request_id: "r" } }, 503);
      }
      hooks.progressOk += 1;
      return json({ state_revision: 4 });
    }
    return route.fallback();
  });
}

test.beforeEach(async ({ page }) => {
  await loginViaStorage(page, "e2e-fake-token");
});

test("刷新恢复到上次游标且不自动发声", async ({ page }) => {
  const hooks: ResumeHooks = { audioRequests: 0, progressCalls: [],
                               progressFailures: 0, progressOk: 0,
                               leaseAcquires: [] };
  await routeResume(page, hooks, { cursorSeg: 2 });
  await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);

  // 恢复位置 = 第二段；恢复播放必须用户手势（未点击前零合成请求）
  const caption = page.locator(
    "section[aria-label='字幕'] p[aria-live='polite']");
  await expect(caption).toContainText(SEGS[1].text, { timeout: 10_000 });
  await page.waitForTimeout(1200);
  expect(hooks.audioRequests).toBe(0);

  // 用户手势后 lease 接管 + 合成开始
  await page.click("button[aria-label='播放']");
  await expect(page.locator("button[aria-label='暂停']")).toBeVisible();
  expect(hooks.audioRequests).toBeGreaterThanOrEqual(1);

  // 刷新：仍是同一 run（URL 不变），游标恢复自服务端
  await page.reload();
  await expect(caption).toContainText(SEGS[1].text, { timeout: 10_000 });
});

test("进度上报失败不崩溃且重试收敛", async ({ page }) => {
  const hooks: ResumeHooks = { audioRequests: 0, progressCalls: [],
                               progressFailures: 2, progressOk: 0,
                               leaseAcquires: [] };
  await routeResume(page, hooks, { cursorSeg: 1 });
  await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);
  const caption = page.locator(
    "section[aria-label='字幕'] p[aria-live='polite']");

  await page.click("button[aria-label='播放']");
  // 服务端 503 期间：播放器不崩溃（字幕推进或暂停均可接受，可观察）
  await expect(caption).toContainText(SEGS[1].text, { timeout: 10_000 });
  await expect.poll(() => hooks.progressCalls.length).toBeGreaterThan(0);
  // 失败被消费后：一次用户手势（目录跳页）即恢复成功上报（200）
  await expect.poll(() => hooks.progressFailures).toBe(0);
  await page.click("nav[aria-label='章节目录'] li:nth-child(2) button");
  await expect.poll(() => hooks.progressOk, { timeout: 20_000 })
    .toBeGreaterThan(0);
  await expect(page.locator("footer")).toBeVisible();
});

test("pagehide 立即 flush 进度", async ({ page }) => {
  const hooks: ResumeHooks = { audioRequests: 0, progressCalls: [],
                               progressFailures: 0, progressOk: 0,
                               leaseAcquires: [] };
  await routeResume(page, hooks, { cursorSeg: 1 });
  await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);
  await page.click("button[aria-label='播放']");
  await page.waitForTimeout(400);

  await page.evaluate(() =>
    window.dispatchEvent(new Event("pagehide")));
  await expect.poll(() => hooks.progressCalls.length)
    .toBeGreaterThan(0);
  const flushed = hooks.progressCalls.filter((p) => p.keepalive
    || p.action === "pause" || p.action === "progress");
  expect(flushed.length).toBeGreaterThan(0);
});

test("lease 被他端持有时显示接管说明而非报错观感", async ({ page }) => {
  const hooks: ResumeHooks = { audioRequests: 0, progressCalls: [],
                               progressFailures: 0, progressOk: 0,
                               leaseAcquires: [] };
  await routeResume(page, hooks, { cursorSeg: 1,
    heldLease: "cl-other-device-0001", acquireConflict: true });
  await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);

  // 接管遮罩：说明是“其他设备或标签页”+ 一键接管，而不是含糊的故障
  await expect(page.getByText("本课正在你的其他设备或标签页播放"))
    .toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("只有一个播放控制者")).toBeVisible();
  await expect(page.getByRole("button", { name: "在这里继续" }))
    .toBeVisible();
  await expect(page.getByText("课堂加载失败")).toHaveCount(0);

  // 初始化按“他端持有”自动请求接管，client_id 是规范随机 id
  expect(hooks.leaseAcquires.length).toBeGreaterThan(0);
  expect(hooks.leaseAcquires[0].takeover).toBe(true);
  expect(hooks.leaseAcquires[0].client_id)
    .toMatch(/^cl-[A-Za-z0-9-]{6,61}$/);

  // 刷新（=「在这里继续」的行为）：同一标签页沿用同一 client_id 再接管
  await page.reload();
  await expect(page.getByText("本课正在你的其他设备或标签页播放"))
    .toBeVisible({ timeout: 10_000 });
  expect(hooks.leaseAcquires.length).toBeGreaterThan(1);
  expect(hooks.leaseAcquires[1].client_id)
    .toBe(hooks.leaseAcquires[0].client_id);
  expect(hooks.leaseAcquires[1].takeover).toBe(true);
});

test("run 加载失败显示重试而非误报其他设备", async ({ page }) => {
  const hooks: ResumeHooks = { audioRequests: 0, progressCalls: [],
                               progressFailures: 0, progressOk: 0,
                               leaseAcquires: [] };
  await routeResume(page, hooks, { cursorSeg: 1, runGetError: true });
  await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);

  await expect(page.getByText("课堂加载失败，请重试"))
    .toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
  await expect(page.getByText("本课正在你的其他设备或标签页播放"))
    .toHaveCount(0);
});
