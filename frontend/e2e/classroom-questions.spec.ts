/* 课堂插问与检查点 E2E（plan.md §19.3 classroom-questions）。
 *
 * - 检查点 question 型：最后一段结束后弹面板；提示/揭晓经服务端端点；
 *   提交一次受理（已提交的答案可见，按钮不再出现）；
 * - 跳过 ≠ 已答：跳过标记「已跳过」，不显示对错；
 * - 刷新恢复：已答检查点不重开为新题（✓ + 继续上课）；
 * - 提问多轮后回到最初被打断段（resume anchor）；
 * - 答案不提前泄漏：揭晓前讲稿/字幕不包含答案文本。
 */
import { expect, test, type Page, type Route } from "@playwright/test";
import { loginViaStorage } from "./helpers";

const WS = "ws-e2e-qa";
const LESSON = "les-e2e-qa";
const RUN = "run-e2e-qa";
const CKP = "ckp_e2e_0000000000000001";
const SEGS = [
  { id: "seg-e2e-1", text: "第一段：把两个碰撞的小车看成一个系统。" },
  { id: "seg-e2e-2", text: "第二段：内力成对出现，总动量不变。" },
  { id: "seg-e2e-3", text: "第三段：合外力冲量可忽略时动量守恒。" },
];
const ANSWER_TEXT = "光滑水平面上两球对心碰撞";

function tinyWav(ms = 600): Buffer {
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

interface QaState {
  ckpState: "pending" | "answered" | "skipped";
  hintCalls: number;
  revealCalls: number;
  submitBodies: object[];
  skipCalls: number;
}

function slidesBody(withCkp: boolean): object[] {
  const blocks: object[] = [
    { id: "blk-e2e-11", kind: "paragraph",
      spans: [{ kind: "text", text: "系统与内力" }] },
  ];
  if (withCkp) blocks.push({ id: "blk-e2e-12", kind: "checkpoint",
    checkpoint_id: CKP });
  return [{
    slide_id: "s-e2e-slide01", order: 1, title: "系统与内力",
    layout: "key_points", learning_objective_ids: [], blocks,
    segments: [0, 1].map((i) => ({
      segment_id: SEGS[i].id, role: "explain",
      display_text: SEGS[i].text, spoken_text: SEGS[i].text,
      show_block_ids: ["blk-e2e-11"], focus_block_ids: [],
      pause_after_ms: 0, source_ids: [], estimated_ms: 600,
    })),
    claims: [], source_ids: [], transition: "auto", estimated_seconds: 1,
  }, {
    slide_id: "s-e2e-slide02", order: 2, title: "守恒条件",
    layout: "key_points", learning_objective_ids: [],
    blocks: [{ id: "blk-e2e-21", kind: "paragraph",
      spans: [{ kind: "text", text: "守恒条件" }] }],
    segments: [{
      segment_id: SEGS[2].id, role: "explain",
      display_text: SEGS[2].text, spoken_text: SEGS[2].text,
      show_block_ids: ["blk-e2e-21"], focus_block_ids: [],
      pause_after_ms: 0, source_ids: [], estimated_ms: 600,
    }],
    claims: [], source_ids: [], transition: "auto", estimated_seconds: 1,
  }];
}

async function routeQa(
  page: Page, state: QaState, opts: { withCkp: boolean; wavMs?: number },
): Promise<void> {
  const wav = tinyWav(opts.wavMs ?? 800);
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
          slides: slidesBody(opts.withCkp),
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
      return json({
        run_id: RUN, lesson_id: LESSON, lesson_revision: 1,
        status: "paused", state_revision: 3,
        cursor: { slide_id: "s-e2e-slide01", segment_id: SEGS[0].id,
                  chunk_index: 0, offset_ms: 0,
                  last_completed_segment_id: null },
        cursor_slide_order: 1, resume_anchor: null,
        audio_profile: { policy: "auto", provider: "azure",
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
    if (path.endsWith(`/runs/${RUN}/lease`)) {
      return json({ lease_epoch: 2,
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
      return json({ state: "ready", duration_ms: 600, provider: "azure",
                    voice_id: "v" });
    }
    if (/\/audio\/clip-[\w-]+\/content$/.test(path)) {
      return route.fulfill({ status: 200, contentType: "audio/wav",
                             body: wav });
    }
    if (path.endsWith(`/runs/${RUN}/progress`)) {
      return json({ state_revision: 4 });
    }
    // ---- 检查点端点族 ----
    if (path.endsWith(`/checkpoints/${CKP}`) && method === "GET") {
      return json({
        checkpoint_id: CKP, kind: "question",
        prompt: "下列哪种情况系统动量守恒？",
        reflection_seconds: null, optional: false,
        run_state: state.ckpState,
        question: {
          question_id: "q_e2e_ckp_1", question_revision: 1,
          q_type: "multiple_choice",
          stem: "下列哪种情况系统动量守恒？",
          options: { "A": "光滑水平面上两球对心碰撞",
                     "B": "粗糙斜面上滑块加速下滑" },
          source_badge: "教材依据",
        },
      });
    }
    if (path.endsWith(`/checkpoints/${CKP}/hint`) && method === "POST") {
      state.hintCalls += 1;
      return json({ hint: "先看合外力是否为零。" });
    }
    if (path.endsWith(`/checkpoints/${CKP}/reveal`) && method === "POST") {
      state.revealCalls += 1;
      return json({ status: "ok", answer: "A",
                    explanation: ANSWER_TEXT + "，合外力为零。" });
    }
    if (path.endsWith(`/checkpoints/${CKP}/skip`) && method === "POST") {
      state.skipCalls += 1;
      state.ckpState = "skipped";
      return json({ status: "ok", run_state: "skipped" });
    }
    if (path.endsWith(`/checkpoints/${CKP}/submit`)
        && method === "POST") {
      state.submitBodies.push(
        route.request().postDataJSON() as object);
      state.ckpState = "answered";
      return json({ status: "accepted", run_state: "answered",
        evaluation_status: "pending",
        task_result: { verdict: "correct" } });
    }
    if (path.endsWith(`/checkpoints/${CKP}/submission`)) {
      // 已答恢复：返回服务端持久化的提交（pending=false → 卡片锁定只读）
      return json({ submission: state.ckpState === "answered" ? {
        student_answer: "A", pending: false, revealed: null,
        verdict: "correct", feedback: "", points: 1, max_points: 1,
      } : null });
    }
    return route.fallback();
  });
}

test.beforeEach(async ({ page }) => {
  await loginViaStorage(page, "e2e-fake-token");
});

async function openPlayer(page: Page): Promise<void> {
  await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);
  await expect(page.locator(
    "section[aria-label='字幕'] p[aria-live='polite']"))
    .toContainText(SEGS[0].text, { timeout: 10_000 });
}

test("检查点：提示→揭晓→提交一次受理，答案不提前泄漏", async ({ page }) => {
  const state: QaState = { ckpState: "pending", hintCalls: 0,
    revealCalls: 0, submitBodies: [], skipCalls: 0 };
  await routeQa(page, state, { withCkp: true });
  await openPlayer(page);

  // 揭晓前：字幕与讲稿侧栏不含答案文本
  for (const seg of SEGS) {
    await expect(page.locator("aside").getByText(seg.text, { exact: false }))
      .toBeVisible();
  }
  await expect(page.getByText(ANSWER_TEXT)).toHaveCount(0);

  // 播放至本页最后一段结束 → 检查点面板弹出
  await page.click("button[aria-label='播放']");
  await expect(page.locator("section[aria-label='随堂练习']"))
    .toBeVisible({ timeout: 10_000 });

  // 看提示：服务端记录帮助事件
  await page.getByRole("button", { name: "看提示" }).click();
  await expect(page.getByText("先看合外力是否为零。")).toBeVisible();
  expect(state.hintCalls).toBe(1);

  // 揭晓答案
  await page.getByRole("button", { name: "揭晓答案" }).click();
  await expect(page.getByText(/合外力为零/).first()).toBeVisible({
    timeout: 10_000 });
  expect(state.revealCalls).toBe(1);

  // 提交（提示/揭晓后作答 ≠ 独立掌握，服务端已记帮助）
  await page.locator("section[aria-label='随堂练习']")
    .getByRole("button", { name: /两球对心碰撞/ }).click();
  await page.getByRole("button", { name: "提交批改" }).click();
  await expect(page.getByTestId("quiz-submitted-status")).toBeVisible({
    timeout: 10_000 });
  expect(state.submitBodies.length).toBe(1);
  const body = state.submitBodies[0] as {
    student_answer?: string };
  expect(body.student_answer).toBe("A");

  // 已提交的答案可见；操作行（提交/提示/揭晓）不再出现
  await expect(page.getByRole("button", { name: "提交批改" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "揭晓答案" })).toHaveCount(0);

  // 继续上课解除闸门，进入下一页
  await page.locator("section[aria-label='随堂练习']")
    .getByRole("button", { name: "继续上课" }).click();
  await expect(page.locator("footer span", { hasText: "2 / 2" }))
    .toBeVisible({ timeout: 10_000 });
});

test("跳过不算答错：标记已跳过且不显示对错", async ({ page }) => {
  const state: QaState = { ckpState: "pending", hintCalls: 0,
    revealCalls: 0, submitBodies: [], skipCalls: 0 };
  await routeQa(page, state, { withCkp: true });
  await openPlayer(page);

  await page.click("button[aria-label='播放']");
  await expect(page.locator("section[aria-label='随堂练习']"))
    .toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "跳过此题" }).click();
  // 跳过即解除闸门：面板收起、无对错反馈、播放继续到下一页
  await expect(page.locator("section[aria-label='随堂练习']"))
    .toBeHidden({ timeout: 10_000 });
  expect(state.skipCalls).toBe(1);
  expect(state.submitBodies.length).toBe(0);

  await expect(page.locator("footer span", { hasText: "2 / 2" }))
    .toBeVisible({ timeout: 15_000 });
});

test("刷新恢复：已答检查点不重开为新题", async ({ page }) => {
  const state: QaState = { ckpState: "answered", hintCalls: 0,
    revealCalls: 0, submitBodies: [], skipCalls: 0 };
  await routeQa(page, state, { withCkp: true });
  // run 光标已在本页最后一段（刷新场景）
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith(`/runs/${RUN}`) && route.request().method() === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({
          run_id: RUN, lesson_id: LESSON, lesson_revision: 1,
          status: "paused", state_revision: 3,
          cursor: { slide_id: "s-e2e-slide01", segment_id: SEGS[1].id,
                    chunk_index: 0, offset_ms: 0,
                    last_completed_segment_id: SEGS[0].id },
          cursor_slide_order: 1, resume_anchor: null,
          audio_profile: { policy: "auto", provider: "azure",
            voice_id: "v", language: "zh-CN", allow_local_fallback: true,
            playback_speed: 0.9, version: 1 },
          qa_session_id: null, visited_slide_count: 1, completed_kind: "",
          created_at: "2026-09-26T00:00:00Z",
          updated_at: "2026-09-26T00:00:00Z",
          lease: { held: false, expired: true, client_id: null,
                   lease_epoch: 0, expires_at: null },
          cursor_index: 1, segment_total: SEGS.length,
          tts_local_locked: false, tts_fallback_notified: false,
        }) });
    }
    return route.fallback();
  });
  await page.goto(`/workspaces/${WS}/classroom/${LESSON}/learn/${RUN}`);
  // 光标在第二段：字幕即第二段，检查点面板以已答形态出现
  await expect(page.locator(
    "section[aria-label='字幕'] p[aria-live='polite']"))
    .toContainText(SEGS[1].text, { timeout: 10_000 });

  // 面板处于已答形态（✓），不能再选选项、没有提交按钮
  await expect(page.locator("section[aria-label='随堂练习']"))
    .toBeVisible({ timeout: 10_000 });
  await expect(page.locator("section[aria-label='随堂练习']")
    .getByText("✓")).toBeVisible();
  await expect(page.getByRole("button", { name: "提交批改" })).toHaveCount(0);
  expect(state.submitBodies.length).toBe(0);
});

test("插问多轮后回到最初被打断段", async ({ page }) => {
  const state: QaState = { ckpState: "pending", hintCalls: 0,
    revealCalls: 0, submitBodies: [], skipCalls: 0 };
  // 长音频（6s/段）：插问操作稳定发生在第一段播放期间
  await routeQa(page, state, { withCkp: false, wavMs: 6000 });
  let askCount = 0;
  await page.route("**/api/v1/chat/stream", async (route) => {
    askCount += 1;
    const sse = [
      `event: answer\ndata: {"type":"answer","content":"第 ${askCount} 轮回答。","is_delta":true}\n\n`,
      `event: done\ndata: {"type":"done","answer":"第 ${askCount} 轮回答。","thinking":"","session_id":"sess-qa-e2e","trace_id":"tr"}\n\n`,
      'event: history_saved\ndata: {"type":"history_saved","session_id":"sess-qa-e2e"}\n\n',
    ].join("");
    await route.fulfill({ status: 200, contentType: "text/event-stream",
                          body: sse });
  });
  await page.route("**/api/v1/chat/sessions/**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ session_id: "sess-qa-e2e", messages: [] }) });
  });

  await openPlayer(page);
  const caption = page.locator(
    "section[aria-label='字幕'] p[aria-live='polite']");
  await page.click("button[aria-label='播放']");

  // 第一轮插问（第一段播放中）：提问抽屉内输入并发送
  const askBox = page.getByRole("region", { name: "课堂提问" })
    .locator("textarea");
  await page.getByRole("button", { name: "提问", exact: true }).click();
  await askBox.fill("为什么内力抵消？");
  await askBox.press("Enter");
  await expect(page.getByText("第 1 轮回答。")).toBeVisible({
    timeout: 10_000 });
  await page.getByRole("button", { name: "继续原课" }).click();
  await expect(caption).toContainText(SEGS[0].text);

  // 第二轮追问后仍回第一段开头（最初被打断段）
  await page.getByRole("button", { name: "提问", exact: true }).click();
  await askBox.fill("再举个例子？");
  await askBox.press("Enter");
  await expect(page.getByText("第 2 轮回答。")).toBeVisible({
    timeout: 10_000 });
  await page.getByRole("button", { name: "继续原课" }).click();
  await expect(caption).toContainText(SEGS[0].text);
  expect(askCount).toBe(2);
});
