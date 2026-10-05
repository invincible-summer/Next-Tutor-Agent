/** Assessment domain: CAT payloads, evaluation_pending re-poll, quiz practice. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, createApiClient } from "../src/index.ts";
import { jsonResponse, noSleep, scriptedFetch } from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

function client(steps: Parameters<typeof scriptedFetch>[0]) {
  const script = scriptedFetch(steps);
  return {
    api: createApiClient({ baseUrl: BASE, fetchImpl: script.fetch, sleepImpl: noSleep }),
    calls: script.calls,
  };
}

test("start posts the CAT payload and returns the first question", async () => {
  const { api, calls } = client([
    jsonResponse(200, {
      status: "ok",
      assessment_id: "as_1",
      illustration_mode: "v3",
      difficulty: 5,
      question: { question_id: "q_1", question_revision: 2, q_type: "short_answer", stem: "…", options: {}, input_spec: { kind: "text", max_bytes: 2000, requires_explanation: false }, concept_refs: [], source_badge: "tb", hints_available: true, illustration: null },
    }),
  ]);
  const result = await api.assessment.start({
    workspace_id: "ws_1",
    concept_keys: ["k1"],
    illustration_mode: "v3",
  });
  assert.equal(result.assessment_id, "as_1");
  assert.equal(calls[0]!.url, `${BASE}/assessment/start`);
  assert.deepEqual(JSON.parse(calls[0]!.init.body as string), {
    workspace_id: "ws_1",
    concept_keys: ["k1"],
    illustration_mode: "v3",
  });
});

test("next re-polls while the server answers 409 evaluation_pending", async () => {
  const { api, calls } = client([
    jsonResponse(409, { detail: { error: { code: "evaluation_pending", message: "still grading" } } }),
    jsonResponse(409, { detail: { error: { code: "evaluation_pending", message: "still grading" } } }),
    jsonResponse(200, { status: "ok", assessment_id: "as_1", stop_reason: "", question: null, summary: { assessment_id: "as_1", items: [] } }),
  ]);
  const result = await api.assessment.next("as_1", { expectedRevision: 2 });
  assert.equal(result.summary?.items.length, 0);
  assert.equal(calls.length, 3, "one original + two conflict re-polls");
  assert.equal(calls[0]!.url, `${BASE}/assessment/next`);
  assert.deepEqual(JSON.parse(calls[0]!.init.body as string), {
    assessment_id: "as_1",
    expected_revision: 2,
  });
});

test("next surfaces unrelated 409 codes instead of re-polling", async () => {
  const { api, calls } = client([
    jsonResponse(409, { detail: { error: { code: "assessment_not_active", message: "closed" } } }),
  ]);
  await assert.rejects(
    api.assessment.next("as_1"),
    (error: unknown) => error instanceof ApiError && error.code === "assessment_not_active",
  );
  assert.equal(calls.length, 1);
});

test("submit carries the idempotency key; quiz record posts the practice loop", async () => {
  const { api, calls } = client([
    jsonResponse(202, { attempt_id: "at_1", source_id: "s_1", job_id: "j_1", question_id: "q_1", question_revision: 1, task_result: null, evaluation: { status: "pending" } }),
    jsonResponse(202, { status: "ok", attempt_id: "at_2", duplicate: false }),
  ]);
  await api.assessment.submit(
    { question_id: "q_1", question_revision: 1, student_answer: "42" },
    { idempotencyKey: "idem-key-0001" },
  );
  assert.equal(calls[0]!.init.headers?.["Idempotency-Key"], "idem-key-0001");
  assert.equal(calls[0]!.url, `${BASE}/assessment/submissions`);
  await api.assessment.quizRecord({
    question_id: "q_1",
    question_revision: 1,
    student_answer: "A",
    session_id: "s_3",
  });
  assert.equal(calls[1]!.url, `${BASE}/quiz/record`);
  assert.deepEqual(JSON.parse(calls[1]!.init.body as string), {
    question_id: "q_1",
    question_revision: 1,
    student_answer: "A",
    session_id: "s_3",
  });
});
