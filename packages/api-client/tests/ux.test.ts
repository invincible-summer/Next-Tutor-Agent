/** UX domain: dashboard greeting/motivation/activity, teaching log, recent quiz, model info. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError } from "../src/errors.ts";
import { createTransport } from "../src/transport.ts";
import { createUxClient } from "../src/ux.ts";
import { jsonResponse, noSleep, scriptedFetch } from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

function client(steps: Parameters<typeof scriptedFetch>[0]) {
  const script = scriptedFetch(steps);
  const transport = createTransport({
    baseUrl: BASE,
    fetchImpl: script.fetch,
    tokenProvider: () => "tok",
    sleepImpl: noSleep,
  });
  return { ux: createUxClient(transport), calls: script.calls };
}

test("greeting maps lang/grade to the wire query", async () => {
  const { ux, calls } = client([jsonResponse(200, { greeting: "早上好", lang: "zh" })]);
  const result = await ux.greeting({ lang: "zh", grade: "本科" });
  assert.equal(result.greeting, "早上好");
  const call = calls[0]!;
  assert.ok(call.url.startsWith(`${BASE}/ux/greeting?`), call.url);
  assert.ok(call.url.includes("lang=zh"));
  assert.ok(call.url.includes(`grade=${encodeURIComponent("本科")}`));
  assert.equal(call.init.headers?.Authorization, "Bearer tok");
});

test("activity sends the days window and defaults to the web client's 14", async () => {
  const day = { date: "2026-10-03", answers: 3, teachings: 1, reviews: 2 };
  const { ux, calls } = client([
    jsonResponse(200, {
      days: [day],
      source: "aggregated",
      streak_days: 4,
      longest_streak: 9,
      last_active_day: "2026-10-03",
      active_days: 12,
    }),
    jsonResponse(200, { days: [], source: "none", streak_days: 0, longest_streak: 0, last_active_day: "", active_days: 0 }),
  ]);
  const activity = await ux.activity(30);
  assert.equal(activity.days[0]!.answers, 3);
  assert.equal(calls[0]!.url, `${BASE}/ux/activity?days=30`);
  await ux.activity();
  assert.equal(calls[1]!.url, `${BASE}/ux/activity?days=14`);
});

test("motivation and profile read their envelopes", async () => {
  const { ux, calls } = client([
    jsonResponse(200, { streak_days: 4, next_milestone: 7, milestones: [3, 7, 30], active_days: 12 }),
    jsonResponse(200, {
      student_id: "s1",
      style: { tone: "encouraging", detail_level: "medium", visual_preference: true, pacing: "steady", patience: "high" },
      motivation: { last_nudge_ts: 0, last_milestone_surfaced: 0 },
      recent_feedback_counts: { like: 2 },
      avg_response_length: 120,
      abandon_signals: 0,
      event_count: 40,
      updated_at: 1,
    }),
  ]);
  const motivation = await ux.motivation();
  assert.equal(motivation.next_milestone, 7);
  assert.equal(calls[0]!.url, `${BASE}/ux/motivation`);
  const profile = await ux.profile();
  assert.equal(profile.style.tone, "encouraging");
  assert.equal(calls[1]!.url, `${BASE}/ux/profile`);
});

test("teachingLog maps limitPerConcept and never sends the legacy student_id", async () => {
  const { ux, calls } = client([
    jsonResponse(200, {
      status: "ok",
      concepts: {
        导数: { current_mode: "socratic", current_outcome: "understood", last_ts: 1, entries: [] },
      },
    }),
  ]);
  const result = await ux.teachingLog({ limitPerConcept: 10 });
  assert.equal(result.status, "ok");
  assert.equal(result.concepts?.["导数"]?.current_mode, "socratic");
  const url = calls[0]!.url;
  assert.ok(url.startsWith(`${BASE}/student/teaching-log?`), url);
  assert.ok(url.includes("limit_per_concept=10"));
  assert.ok(!url.includes("student_id="), "identity comes from the token");
});

test("teachingLog surfaces the disabled projection without throwing", async () => {
  const { ux, calls } = client([jsonResponse(200, { status: "disabled" })]);
  const result = await ux.teachingLog();
  assert.equal(result.status, "disabled");
  assert.equal(result.concepts, undefined);
  assert.equal(calls[0]!.url, `${BASE}/student/teaching-log`);
});

test("recentQuizQuestions reads the cross-session quiz history", async () => {
  const { ux, calls } = client([
    jsonResponse(200, {
      status: "ok",
      questions: [{
        id: "att_1",
        ts: "2026-10-03T10:00:00",
        session_id: "s1",
        question_id: "q1",
        question_revision: 2,
        topic: "algebra",
        knowledge_point: "导数",
        type: "choice",
        stem: "…",
        verdict: "correct",
        evaluation_status: "ready",
        availability: "available",
      }],
    }),
    jsonResponse(200, { status: "ok", questions: [] }),
  ]);
  const result = await ux.recentQuizQuestions({ limit: 20 });
  assert.equal(result.questions[0]!.question_id, "q1");
  assert.equal(calls[0]!.url, `${BASE}/quiz/recent?limit=20`);
  const empty = await ux.recentQuizQuestions();
  assert.equal(empty.questions.length, 0);
  assert.equal(calls[1]!.url, `${BASE}/quiz/recent`);
});

test("modelInfo reads the public model configuration", async () => {
  const { ux, calls } = client([
    jsonResponse(200, {
      llm_model: "gpt-x",
      multimodal_configured: true,
      multimodal_model: "gpt-x",
      voice_models: {
        cloud: { model: "Azure Speech", configured: false, voices: [] },
        local: { model: "MeloTTS-Chinese", enabled: true, voice: "melo-zh", languages: ["zh-CN"] },
        phone_provider: "local",
        automatic_priority: "local",
      },
    }),
  ]);
  const info = await ux.modelInfo();
  assert.equal(info.llm_model, "gpt-x");
  assert.equal(info.voice_models?.local.enabled, true);
  assert.equal(calls[0]!.url, `${BASE}/model-info`);
});

test("server errors surface as ApiError with the nested envelope code", async () => {
  const { ux } = client([
    jsonResponse(500, { detail: { error: { code: "ux_store_offline", message: "down" } } }),
  ]);
  await assert.rejects(
    ux.motivation(),
    (error: unknown) =>
      error instanceof ApiError && error.status === 500 && error.code === "ux_store_offline",
  );
});
