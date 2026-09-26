/* 课堂播放器 E2E：真实 <audio> 管线 + 伪造 API（plan.md §18 G05、§19.3）。
 *
 * page.route 拦截课堂 API（无需后端数据），音频内容返回真实的小 WAV
 * （8kHz PCM16，~300ms），验证整课推进只由媒体 ended 驱动：
 * - 播放后字幕随段推进，翻页指示 1/2 → 2/2；
 * - 暂停立即停声（字幕停在当前段，不因时间流逝推进）；
 * - 末段结束后出现完成层，最后一次 progress 上报 action=complete；
 * - 无 TTS（provider 空）时进入文字课堂仍可读讲稿完成。
 */
import { expect, test, type Page, type Route } from "@playwright/test";
import { loginViaStorage } from "./helpers";

const WS = "ws-e2e-player";
const LESSON = "les-e2e-player";
const RUN = "run-e2e-player";
const SEGS = [
  { id: "seg-e2e-1", text: "第一段讲稿：把两个碰撞的小车看成一个系统。" },
  { id: "seg-e2e-2", text: "第二段讲稿：内力成对出现，总动量不变。" },
  { id: "seg-e2e-3", text: "第三段讲稿：只要合外力冲量可以忽略。" },
  { id: "seg-e2e-4", text: "第四段讲稿：这就是动量守恒的适用条件。" },
];
const SLIDES = [
  { id: "s-e2e-slide01", order: 1, title: "系统与内力", segs: [0, 1] },
  { id: "s-e2e-slide02", order: 2, title: "守恒条件", segs: [2, 3] },
];

/** 真实可解码 WAV：8kHz 单声道 PCM16 静音。
 * 700ms/段：段推进间隔必须大于 Playwright 轮询间隔，字幕窗口才稳定
 * 可观察（300ms 在 dev 模式下会被跳过）。 */
function tinyWav(ms = 700): Buffer {
  const rate = 8000;
  const samples = Math.floor((rate * ms) / 1000);
  const data = Buffer.alloc(samples * 2);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);       // PCM
  header.writeUInt16LE(1, 22);       // mono
  header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

interface ProgressCall {
  action: string;
  cursorSegment: string;
  played: string[];
}

async function routeClassroomApi(
  page: Page, opts: { withVoice: boolean },
): Promise<ProgressCall[]> {
  const progressCalls: ProgressCall[] = [];
  const wav = tinyWav();
  let stateRevision = 3;
  const clipState = (segId: string) => ({
    clip_id: `clip-${segId}`, state: "ready",
    status_url: `/status/${segId}`, content_url: `/content/${segId}`,
  });

  await page.route("**/api/v1/workspaces/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json",
                      body: JSON.stringify(body) });

    if (path.endsWith(`/classroom/lessons/${LESSON}`)
        && method === "GET") {
      return json({
        lesson_id: LESSON, workspace_id: WS, title: "动量守恒入门",
        lifecycle: "active", latest_ready_revision: 1,
        published_revisions: [1], latest_job: null,
        revision: {
          revision: 1, schema_version: 1,
          slides: SLIDES.map((s) => ({
            slide_id: s.id, order: s.order, title: s.title,
            layout: "key_points",
            learning_objective_ids: [],
            blocks: [
              { id: `blk-e2e-${s.order}1`, kind: "paragraph",
                spans: [{ kind: "text", text: s.title }] },
            ],
            segments: s.segs.map((i) => ({
              segment_id: SEGS[i].id, role: "explain",
              display_text: SEGS[i].text, spoken_text: SEGS[i].text,
              show_block_ids: [`blk-e2e-${s.order}1`],
              focus_block_ids: [], pause_after_ms: 0,
              source_ids: [], estimated_ms: 700,
            })),
            claims: [], source_ids: [], transition: "auto",
            estimated_seconds: 1,
          })),
          checkpoint_templates: [], objectives: [], glossary: [],
          renderer_version: "test", content_hash: "0".repeat(64),
          created_at: "2026-09-26T00:00:00Z",
        },
        pending: null, recent_run: null,
      });
    }
    if (path.endsWith(`/classroom/lessons/${LESSON}/revisions/1/frame`)
        && method === "GET") {
      return route.fulfill({
        status: 200, contentType: "text/html; charset=utf-8",
        body: "<!doctype html><html><head><title>frame</title></head>" +
              "<body><div id='viewport'></div></body></html>",
      });
    }
    if (path.endsWith(`/classroom/lessons/${LESSON}/runs/${RUN}`)
        && method === "GET") {
      return json({
        run_id: RUN, lesson_id: LESSON, lesson_revision: 1,
        status: "paused", state_revision: stateRevision,
        cursor: { slide_id: SLIDES[0].id, segment_id: SEGS[0].id,
                  chunk_index: 0, offset_ms: 0,
                  last_completed_segment_id: null },
        cursor_slide_order: 1,
        resume_anchor: null,
        audio_profile: { policy: "auto",
          provider: opts.withVoice ? "azure" : "",
          voice_id: "zh-CN-XiaoxiaoNeural", language: "zh-CN",
          allow_local_fallback: true, playback_speed: 0.9, version: 1 },
        qa_session_id: null, visited_slide_count: 1, completed_kind: "",
        created_at: "2026-09-26T00:00:00Z",
        updated_at: "2026-09-26T00:00:00Z",
        lease: { held: false, expired: true, client_id: null,
                 lease_epoch: 0, expires_at: null },
        cursor_index: 0, segment_total: SEGS.length,
        tts_local_locked: false, tts_fallback_notified: false,
      });
    }
    if (path.endsWith(`/runs/${RUN}/lease`) && method === "POST") {
      return json({ lease_epoch: 5,
                    expires_at: "2026-09-26T12:00:00Z" });
    }
    if (path.endsWith(`/runs/${RUN}/lease`) && method === "PUT") {
      return json({ lease_epoch: 5,
                    expires_at: "2026-09-26T12:00:00Z" });
    }
    if (path.endsWith(`/runs/${RUN}/audio`) && method === "POST") {
      const body = route.request().postDataJSON() as {
        segment_ids?: string[] };
      const ids = (body.segment_ids ?? []).slice(0, 3);
      return json({ clips: ids.map(clipState) }, 202);
    }
    const clipMatch = path.match(/\/audio\/(clip-[\w-]+)$/);
    if (clipMatch && method === "GET") {
      return json({ state: "ready", duration_ms: 700,
                    provider: opts.withVoice ? "azure" : "melo",
                    voice_id: "zh-CN-XiaoxiaoNeural" });
    }
    const contentMatch = path.match(/\/audio\/(clip-[\w-]+)\/content$/);
    if (contentMatch && method === "GET") {
      return route.fulfill({ status: 200,
                             contentType: "audio/wav", body: wav });
    }
    if (path.endsWith(`/runs/${RUN}/progress`) && method === "PUT") {
      const body = route.request().postDataJSON() as {
        action?: string;
        cursor?: { segment_id?: string };
        played_segment_ids?: string[] };
      stateRevision += 1;
      progressCalls.push({
        action: String(body.action ?? ""),
        cursorSegment: String(body.cursor?.segment_id ?? ""),
        played: body.played_segment_ids ?? [],
      });
      return json({ state_revision: stateRevision });
    }
    // 未匹配的课堂请求：透传（WorkspaceModeBar 等静态渲染）
    return route.fallback();
  });
  return progressCalls;
}

test.beforeEach(async ({ page }) => {
  // 登录态只需存在 token；API 全部被 route 拦截，后端不参与
  await loginViaStorage(page, "e2e-fake-token");
});

test("整课经真实音频 ended 推进到完成", async ({ page }) => {
  const progress = await routeClassroomApi(page, { withVoice: true });
  await page.goto(
    `/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);

  const caption = page.locator("section[aria-label='字幕'] p[aria-live='polite']");
  // 初始（恢复）位置：第一段
  await expect(caption).toContainText(SEGS[0].text);
  await expect(page.locator("footer span", { hasText: "1 / 2" }))
    .toBeVisible();

  // 用户手势开始播放（真实 WAV 300ms/段）
  await page.click("button[aria-label='播放']");
  await expect(page.locator("button[aria-label='暂停']")).toBeVisible();

  // 段推进只由 ended 驱动：第二段出现（约 300ms 后），仍在第 1 页
  await expect(caption).toContainText(SEGS[1].text, { timeout: 8_000 });
  await expect(page.locator("footer span", { hasText: "1 / 2" }))
    .toBeVisible();

  // 跨页：第三段出现后翻页指示变 2 / 2
  await expect(caption).toContainText(SEGS[2].text, { timeout: 8_000 });
  await expect(page.locator("footer span", { hasText: "2 / 2" }))
    .toBeVisible();

  // 末段结束后出现完成层；progress 最终上报 complete
  await expect(page.getByText("本节课已完成")).toBeVisible({
    timeout: 10_000 });
  expect(progress.length).toBeGreaterThan(0);
  const complete = progress.filter((p) => p.action === "complete");
  expect(complete.length, "exactly one complete event").toBe(1);
  expect(complete[0].played).toContain(SEGS[3].id);
});

test("暂停立即停声，恢复后从当前段继续", async ({ page }) => {
  await routeClassroomApi(page, { withVoice: true });
  await page.goto(
    `/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);
  const caption = page.locator("section[aria-label='字幕'] p[aria-live='polite']");

  await page.click("button[aria-label='播放']");
  // 等到第二段（第一段已 ended）
  await expect(caption).toContainText(SEGS[1].text, { timeout: 8_000 });

  // 暂停：静音 WAV 本应在 ~300ms 内放完并推进；停住即证明停声
  await page.click("button[aria-label='暂停']");
  await page.waitForTimeout(900);
  await expect(caption).toContainText(SEGS[1].text);

  // 恢复：继续第二段并最终走完
  await page.click("button[aria-label='播放']");
  await expect(caption).toContainText(SEGS[2].text, { timeout: 8_000 });
});

test("插问暂停讲授，回答后继续原课", async ({ page }) => {
  await routeClassroomApi(page, { withVoice: true });
  let chatBody: Record<string, unknown> | null = null;
  await page.route("**/api/v1/chat/stream", async (route) => {
    chatBody = route.request().postDataJSON() as Record<string, unknown>;
    const sse = [
      'event: answer\ndata: {"type":"answer","content":"内力成对出现，相互抵消。","is_delta":true}\n\n',
      'event: done\ndata: {"type":"done","answer":"内力成对出现，相互抵消。","thinking":"","session_id":"sess-qa-e2e","trace_id":"tr1"}\n\n',
      'event: history_saved\ndata: {"type":"history_saved","session_id":"sess-qa-e2e"}\n\n',
    ].join("");
    await route.fulfill({ status: 200,
                          contentType: "text/event-stream", body: sse });
  });
  await page.route("**/api/v1/chat/sessions/**", async (route) => {
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ session_id: "sess-qa-e2e", messages: [
        { message_id: "m_e2e_reply_01", role: "user" },
        { message_id: "m_e2e_reply_02", role: "assistant",
          content: "内力成对出现，相互抵消。" },
      ] }),
    });
  });
  await page.route("**/qa-audio", async (route) => {
    await route.fulfill({
      status: 202, contentType: "application/json",
      body: JSON.stringify({ clips: [{
        clip_id: "clip-qa-e2e-01", state: "ready",
        status_url: "/s", content_url: "/c" }] }),
    });
  });

  await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);
  const caption = page.locator(
    "section[aria-label='字幕'] p[aria-live='polite']");
  await expect(caption).toContainText(SEGS[0].text);
  await page.click("button[aria-label='播放']");

  // 快捷补讲：暂停讲授 + 打开问答抽屉
  await page.getByRole("button", { name: "没听懂" }).click();
  await expect(page.getByRole("heading", { name: "课堂提问" }))
    .toBeVisible();
  await page.waitForTimeout(900);
  await expect(caption).toContainText(SEGS[0].text);   // 未推进（暂停）

  // 回答流式显示，随后出现「继续原课」
  await expect(page.locator("section[aria-label='课堂提问']")
    .getByText("内力成对出现，相互抵消。")).toBeVisible({ timeout: 8_000 });
  await expect(page.getByRole("button", { name: "继续原课" }))
    .toBeVisible({ timeout: 8_000 });
  expect(chatBody).not.toBeNull();
  const ref = (chatBody as unknown as {
    classroom_ref?: Record<string, unknown>;
  }).classroom_ref;
  expect(ref?.run_id).toBe(RUN);
  expect(ref?.slide_id).toBe(SLIDES[0].id);

  // 继续原课：从被打断段恢复，随后正常推进
  await page.getByRole("button", { name: "继续原课" }).click();
  await expect(caption).toContainText(SEGS[1].text, { timeout: 8_000 });
});

test("无语音时进入文字课堂，讲稿仍可完整阅读", async ({ page }) => {
  await routeClassroomApi(page, { withVoice: false });
  await page.goto(
    `/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);

  // provider 为空 → 文字课堂徽标 + 讲稿侧栏可见，播放不发声不报错
  await expect(page.getByText("文字课堂")).toBeVisible({ timeout: 8_000 });
  await expect(page.locator("section[aria-label='字幕'] p[aria-live='polite']"))
    .toContainText(SEGS[0].text);
  // 讲稿侧栏包含全部段（默认展开，可滚动阅读）
  for (const seg of SEGS) {
    await expect(page.locator("aside").getByText(seg.text, { exact: false }))
      .toBeVisible();
  }
  // 手动翻页（目录第 2 页）不依赖音频
  await page.click(
    "nav[aria-label='章节目录'] li:nth-child(2) button");
  await expect(page.locator("footer span", { hasText: "2 / 2" }))
    .toBeVisible();
});
