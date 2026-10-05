/** Classroom domain: idempotent creates, lease DELETE-with-body, bytes, job SSE. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, createApiClient } from "../src/index.ts";
import {
  binaryResponse,
  bytes,
  jsonResponse,
  noSleep,
  scriptedFetch,
  sseResponse,
  textResponse,
} from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

function client(steps: Parameters<typeof scriptedFetch>[0]) {
  const script = scriptedFetch(steps);
  return {
    api: createApiClient({ baseUrl: BASE, fetchImpl: script.fetch, sleepImpl: noSleep }),
    calls: script.calls,
  };
}

const WS = "ws_1";
const LESSON = "les_000000000000000000000001";

test("ensureQaSession uses the stable draft key to recover the same server chat", async () => {
  const { api, calls } = client([jsonResponse(200, { session_id: "qa_1" })]);
  assert.deepEqual(await api.classroom.ensureQaSession(WS, LESSON, "run_1", "draft-qa-01"), { session_id: "qa_1" });
  assert.equal(calls[0]!.url, `${BASE}/workspaces/${WS}/classroom/lessons/${LESSON}/runs/run_1/qa-session`);
  assert.equal(calls[0]!.init.method, "POST");
  assert.equal(calls[0]!.init.headers?.["Idempotency-Key"], "draft-qa-01");
  assert.deepEqual(JSON.parse(calls[0]!.init.body as string), {});
});

test("createLesson posts with the Idempotency-Key header and accepts 202", async () => {
  const { api, calls } = client([
    jsonResponse(202, { lesson_id: LESSON, job_id: "job_1", target_revision: 1, status_url: "/x", events_url: "/y" }),
  ]);
  const result = await api.classroom.createLesson(
    WS,
    { brief: { topic: "导数概念" }, start_mode: "automatic" } as never,
    "idem-key-lesson-01",
  );
  assert.equal(result.lesson_id, LESSON);
  const call = calls[0]!;
  assert.equal(call.url, `${BASE}/workspaces/${WS}/classroom/lessons`);
  assert.equal(call.init.method, "POST");
  assert.equal(call.init.headers?.["Idempotency-Key"], "idem-key-lesson-01");
});

test("releaseLease sends DELETE with a JSON body", async () => {
  const { api, calls } = client([jsonResponse(200, { status: "released" })]);
  await api.classroom.releaseLease(WS, LESSON, "run_1", {
    client_id: "client-ab",
    lease_epoch: 2,
  });
  const call = calls[0]!;
  assert.equal(call.init.method, "DELETE");
  assert.equal(call.url, `${BASE}/workspaces/${WS}/classroom/lessons/${LESSON}/runs/run_1/lease`);
  assert.deepEqual(JSON.parse(call.init.body as string), { client_id: "client-ab", lease_epoch: 2 });
});

test("getRevisionFrame returns HTML text; clipContent resolves WAV bytes", async () => {
  const { api, calls } = client([
    textResponse(200, "<html><body>slides</body></html>", { "content-type": "text/html; charset=utf-8" }),
    binaryResponse(200, "RIFF-clips"),
  ]);
  const frame = await api.classroom.getRevisionFrame(WS, LESSON, 3, "print");
  assert.equal(frame, "<html><body>slides</body></html>");
  assert.ok(calls[0]!.url.endsWith(`/lessons/${LESSON}/revisions/3/frame?mode=print`));
  const audio = await api.classroom.clipContent(WS, LESSON, "run_1", "ck_1");
  assert.ok(audio instanceof ArrayBuffer);
  assert.equal(Buffer.from(audio).toString("utf8"), "RIFF-clips");
  assert.ok(calls[1]!.url.endsWith(`/runs/run_1/audio/ck_1/content`));
});

test("jobEvents yields snapshots, skips heartbeats, ends on terminal state", async () => {
  const wire = [
    "id: 3\nevent: snapshot\ndata: {\"job_id\":\"job_1\",\"state_revision\":3,\"state\":\"running\",\"phase\":\"outline\",\"completed_slides\":1,\"total_slides\":8,\"warnings\":[]}\n\n",
    ": heartbeat\n\n",
    "event: snapshot\ndata: {\"job_id\":\"job_1\",\"state_revision\":4,\"state\":\"succeeded\",\"phase\":null,\"completed_slides\":8,\"total_slides\":8,\"warnings\":[]}\n\n",
  ].join("");
  const { api, calls } = client([sseResponse([bytes(wire)])]);
  const events = [];
  for await (const event of api.classroom.jobEvents(WS, LESSON, "job_1", { afterRevision: 2 })) {
    events.push(event);
  }
  assert.equal(events.length, 2);
  assert.equal(events[0]!.type, "snapshot");
  assert.equal(events[0]!.snapshot.state, "running");
  assert.equal(events[1]!.type, "snapshot");
  assert.equal(events[1]!.snapshot.state, "succeeded");
  assert.ok(calls[0]!.url.endsWith(`/jobs/job_1/events?after_revision=2`));
  assert.equal(calls[0]!.init.headers?.Accept, "text/event-stream");
});

test("jobEvents terminal frame with empty data reports a null snapshot", async () => {
  const wire = 'event: terminal\ndata: {}\n\n';
  const { api } = client([sseResponse([bytes(wire)])]);
  const events = [];
  for await (const event of api.classroom.jobEvents(WS, LESSON, "job_1")) {
    events.push(event);
  }
  assert.equal(events.length, 1);
  assert.equal(events[0]!.type, "terminal");
  assert.equal(events[0]!.type === "terminal" ? events[0]!.snapshot : null, null);
});

test("classroom error envelopes surface as ApiError codes", async () => {
  const { api } = client([
    jsonResponse(403, { detail: { error: { code: "classroom_disabled", message: "disabled" } } }),
  ]);
  await assert.rejects(
    api.classroom.templates(),
    (error: unknown) =>
      error instanceof ApiError && error.code === "classroom_disabled" && error.status === 403,
  );
});

test("checkpoint submit keeps its idempotency key in the body, not the header", async () => {
  const { api, calls } = client([
    jsonResponse(202, { attempt_id: "at_1", source_id: "s_1", job_id: "j_1", question_id: "q_1", question_revision: 1, task_result: null, evaluation_status: "pending", duplicate: false }),
  ]);
  await api.classroom.submitCheckpoint(WS, LESSON, "run_1", "ck_1", {
    question_ref: "q_1",
    student_answer: "ans",
    idempotency_key: "idem-in-body-01",
  });
  const call = calls[0]!;
  assert.equal(call.url, `${BASE}/workspaces/${WS}/classroom/lessons/${LESSON}/runs/run_1/checkpoints/ck_1/submit`);
  assert.equal(call.init.headers?.["Idempotency-Key"], undefined);
  assert.deepEqual(JSON.parse(call.init.body as string), {
    question_ref: "q_1",
    student_answer: "ans",
    idempotency_key: "idem-in-body-01",
  });
});
