/** Memory domain: prompt-memory profile + legacy audit reads. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError } from "../src/errors.ts";
import { createMemoryClient } from "../src/memory.ts";
import { createTransport } from "../src/transport.ts";
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
  return { api: createMemoryClient(transport), calls: script.calls };
}

test("episodes maps paging params and branches on the status envelope", async () => {
  const { api, calls } = client([
    jsonResponse(200, {
      status: "ok",
      episodes: [{ id: "ep_1", ts: 1700.5, summary: "复习导数" }],
      has_more: true,
    }),
    jsonResponse(200, { status: "disabled" }),
  ]);
  const page = await api.episodes({ limit: 100, before: 1800 });
  assert.equal(page.status, "ok");
  assert.equal(page.episodes?.[0]?.id, "ep_1");
  assert.equal(page.has_more, true);
  assert.equal(calls[0]!.url, `${BASE}/memory/episodes?limit=100&before=1800`);
  assert.equal(calls[0]!.init.method, "GET");
  assert.equal(calls[0]!.init.headers?.Authorization, "Bearer tok");
  const disabled = await api.episodes();
  assert.equal(disabled.status, "disabled");
  assert.equal(calls[1]!.url, `${BASE}/memory/episodes`, "no params means no query string");
  assert.ok(!calls[0]!.url.includes("student_id="), "legacy web param must not leak");
});

test("semantic and procedural read the audit/projection endpoints", async () => {
  const { api, calls } = client([
    jsonResponse(200, { status: "ok", facts: [{ id: "f_1", category: "preference", fact: "偏好简洁讲解" }] }),
    jsonResponse(200, { status: "ok", strategies: [{ strategy: "worked_example", success_rate: 0.8, trials: 5 }] }),
  ]);
  const semantic = await api.semantic();
  assert.equal(semantic.facts?.[0]?.fact, "偏好简洁讲解");
  assert.equal(calls[0]!.url, `${BASE}/memory/semantic`);
  const procedural = await api.procedural();
  assert.equal(procedural.strategies?.[0]?.strategy, "worked_example");
  assert.equal(calls[1]!.url, `${BASE}/memory/procedural`);
});

test("prompt profile read and window update round-trip", async () => {
  const { api, calls } = client([
    jsonResponse(200, {
      status: "ok",
      window_size: 15,
      max_window: 30,
      core_profile: { learning_summary: "正在学导数" },
      recent_sessions: [{ session_id: "s_1", has_contribution: true }],
      compacted_session_count: 2,
      directive_chars: 120,
    }),
    jsonResponse(200, { status: "ok", window_size: 20, max_window: 30 }),
  ]);
  const profile = await api.promptProfile();
  assert.equal(profile.window_size, 15);
  assert.equal(profile.core_profile?.["learning_summary"], "正在学导数");
  assert.equal(profile.recent_sessions?.[0]?.session_id, "s_1");
  assert.equal(calls[0]!.url, `${BASE}/memory/prompt-profile`);
  const updated = await api.setPromptWindow(20);
  assert.equal(updated.window_size, 20);
  assert.equal(calls[1]!.init.method, "PUT");
  assert.equal(calls[1]!.url, `${BASE}/memory/prompt-profile/window`);
  assert.deepEqual(JSON.parse(calls[1]!.init.body as string), { window_size: 20 });
});

test("promptSessionStatus encodes the session id and returns the attribution", async () => {
  const { api, calls } = client([jsonResponse(200, { status: "compacted" })]);
  const result = await api.promptSessionStatus("sess a/1");
  assert.equal(result.status, "compacted");
  assert.equal(calls[0]!.url, `${BASE}/memory/prompt-profile/sessions/${encodeURIComponent("sess a/1")}`);
});

test("HTTP errors reject instead of degrading to a silent status", async () => {
  const { api } = client([
    jsonResponse(404, { detail: { error: { code: "session_not_found", message: "会话不存在" } } }),
  ]);
  await assert.rejects(
    api.promptSessionStatus("missing"),
    (error: unknown) => error instanceof ApiError && error.code === "session_not_found" && error.status === 404,
  );
});

test("window validation errors surface the server envelope", async () => {
  const { api } = client([
    jsonResponse(422, { detail: { error: { code: "validation_error", message: "window_size must be 5-30" } } }),
  ]);
  await assert.rejects(
    api.setPromptWindow(99),
    (error: unknown) => error instanceof ApiError && error.status === 422 && error.retryable === false,
  );
});
