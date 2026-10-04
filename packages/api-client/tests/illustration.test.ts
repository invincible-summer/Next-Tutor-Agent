/** Illustration domains: error mapping, quiz read models, §5.2.1 recovery & poll semantics. */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  IllustrationApiError,
  NetworkError,
  createApiClient,
} from "../src/index.ts";
import { hangingFetch, jsonResponse, noSleep, scriptedFetch } from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

// --- quiz illustration (V1/V2/V3 read model) ----------------------------------

test("quiz start maps server failure envelopes to IllustrationApiError", async () => {
  const { fetch } = scriptedFetch([
    jsonResponse(500, { detail: { error: { code: "budget_exhausted", retryable: false } } }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  await assert.rejects(
    client.illustration.start("q1", 3),
    (error: unknown) =>
      error instanceof IllustrationApiError &&
      error.code === "budget_exhausted" &&
      error.retryable === false,
  );
});

test("quiz retry aborted mid-wait maps to retryable run_interrupted", async () => {
  const client = createApiClient({
    baseUrl: BASE,
    fetchImpl: hangingFetch("TimeoutError"),
    sleepImpl: noSleep,
  });
  await assert.rejects(
    client.illustration.retry("job-1", { timeoutMs: 10 }),
    (error: unknown) =>
      error instanceof IllustrationApiError &&
      error.code === "run_interrupted" &&
      error.retryable === true,
  );
});

test("quiz frozen artifact GET returns the union view", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(200, { status: "ready", question_id: "q1", question_revision: 2 }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  const view = await client.illustration.frozen("q1", 2);
  assert.equal(view.status, "ready");
  assert.match(calls[0]!.url, /\/questions\/q1\/illustration\?question_revision=2$/);
});

// --- scenario tool illustration (§5.2.1) --------------------------------------

const SESSION_WITH_TURN = {
  session_id: "s1",
  title: "",
  revision: 4,
  active_job_id: "job-9",
  created_at: 0,
  updated_at: 0,
  turns: [
    {
      turn_id: "t1",
      message: "画个受力图",
      mode: "v3",
      selected_materials: [{ asset_id: "a1", version: 2 }],
      job_id: "job-9",
      status: "running",
      revision: null,
      created_at: 0,
      request_id: "req-stable",
    },
  ],
  revisions: [],
};

test("submitTurnWithRecovery recovers a lost POST via session request_id", async () => {
  const { fetch, calls } = scriptedFetch([
    new Error("connection reset"), // POST /turns lost
    jsonResponse(200, SESSION_WITH_TURN), // GET session
    jsonResponse(200, { job_id: "job-9", status: "running", stage: "rendering", progress: 65 }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  const job = await client.tools.illustration.submitTurnWithRecovery("s1", {
    message: "画个受力图",
    mode: "v3",
    selected_materials: [{ asset_id: "a1", version: 2 }],
    base_revision: 4,
    request_id: "req-stable",
  });
  assert.equal(job.job_id, "job-9");
  assert.equal(calls.length, 3);
  assert.ok(calls[0]!.url.endsWith("/tools/illustration/sessions/s1/turns"));
  // The recovery POST never repeats generation: it only reads projections.
  assert.ok(calls[1]!.url.endsWith("/tools/illustration/sessions/s1"));
  assert.ok(calls[2]!.url.endsWith("/tools/illustration/jobs/job-9"));
  assert.equal(JSON.parse(String(calls[0]!.init.body)).request_id, "req-stable");
});

test("submitTurnWithRecovery rethrows when the turn never landed", async () => {
  const emptySession = { ...SESSION_WITH_TURN, turns: [] };
  const { fetch } = scriptedFetch([
    new Error("connection reset"),
    jsonResponse(200, emptySession),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  await assert.rejects(
    client.tools.illustration.submitTurnWithRecovery("s1", {
      message: "x",
      mode: "v1",
      selected_materials: [],
      base_revision: 1,
      request_id: "req-stable",
    }),
    NetworkError,
  );
});

test("submitTurn surfaces 409 domain conflicts as typed errors without retry", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(409, { detail: { error: { code: "illustration_session_busy" } } }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  await assert.rejects(
    client.tools.illustration.submitTurn("s1", {
      message: "x",
      mode: "v2",
      selected_materials: [],
      base_revision: 1,
      request_id: "r1",
    }),
    (error: unknown) =>
      error instanceof IllustrationApiError && error.code === "illustration_session_busy",
  );
  assert.equal(calls.length, 1);
});

test("findTurnByRequestId matches only the stable request id", () => {
  const client = createApiClient({
    baseUrl: BASE,
    fetchImpl: scriptedFetch([jsonResponse(200, {})]).fetch,
    sleepImpl: noSleep,
  });
  const session = SESSION_WITH_TURN as never as Parameters<
    typeof client.tools.illustration.findTurnByRequestId
  >[0];
  const turn = client.tools.illustration.findTurnByRequestId(session, "req-stable");
  assert.ok(turn);
  assert.equal(turn.turn_id, "t1");
  assert.equal(client.tools.illustration.findTurnByRequestId(session, "other"), null);
});

test("pollJob stops at the server terminal state", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(200, { job_id: "j", status: "running", stage: "composing", progress: 45 }),
    jsonResponse(200, { job_id: "j", status: "ready", stage: "ready", progress: 100 }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  const events = [];
  for await (const event of client.tools.illustration.pollJob("j", { sleepImpl: noSleep })) {
    events.push(event);
  }
  assert.equal(events.length, 2);
  assert.equal(events[0]!.kind, "job");
  const terminal = events[1];
  assert.ok(terminal && terminal.kind === "job");
  assert.equal(terminal.job.status, "ready");
  assert.equal(calls.length, 2); // no fetch after the terminal snapshot
});

test("pollJob timeout only stops observation — it never fabricates a failed job", async () => {
  const client = createApiClient({
    baseUrl: BASE,
    fetchImpl: scriptedFetch([jsonResponse(200, {})]).fetch,
    sleepImpl: noSleep,
  });
  const events = [];
  for await (const event of client.tools.illustration.pollJob("j", {
    timeoutMs: 0,
    sleepImpl: noSleep,
  })) {
    events.push(event);
  }
  assert.deepEqual(events, [{ kind: "observation_stopped", reason: "timeout" }]);
});

test("pollJob suspends network calls while shouldPause is true, then re-syncs", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(200, { job_id: "j", status: "ready", stage: "ready", progress: 100 }),
  ]);
  let paused = true;
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  let pauses = 0;
  const events = [];
  for await (const event of client.tools.illustration.pollJob("j", {
    intervalMs: 5,
    sleepImpl: async () => {
      pauses += 1;
      if (pauses >= 2) paused = false; // foreground after two suspended beats
    },
    shouldPause: () => paused,
  })) {
    events.push(event);
  }
  assert.ok(pauses >= 2, "poller must idle while paused");
  assert.equal(calls.length, 1); // exactly one re-sync fetch after foreground
  assert.equal(events[0]!.kind, "job");
});
