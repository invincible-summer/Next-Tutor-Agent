/** Evaluation domain: learner-evaluation archive + M7 insights panel. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ConflictError } from "../src/errors.ts";
import { createEvaluationClient } from "../src/evaluation.ts";
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
  return { api: createEvaluationClient(transport), calls: script.calls };
}

test("workspaces and workspace summary hit the archive reads", async () => {
  const { api, calls } = client([
    jsonResponse(200, { items: [{ workspace_id: "ws_1", workspace_name: "数学" }], total: 1, offset: 0, limit: 20 }),
    jsonResponse(200, { workspace_id: "ws_1", evaluation_status: "ready", coverage: { observed_concepts: 3 } }),
  ]);
  const list = await api.workspaces({ offset: 0, limit: 20 });
  assert.equal(list.items[0]!.workspace_id, "ws_1");
  assert.equal(calls[0]!.url, `${BASE}/learner-evaluation/workspaces?offset=0&limit=20`);
  assert.equal(calls[0]!.init.method, "GET");
  assert.equal(calls[0]!.init.headers?.Authorization, "Bearer tok");
  const summary = await api.workspace("ws_1");
  assert.equal(summary.evaluation_status, "ready");
  assert.equal(calls[1]!.url, `${BASE}/learner-evaluation/workspaces/ws_1`);
});

test("concepts maps camelCase filters to snake_case and drops undefined", async () => {
  const { api, calls } = client([
    jsonResponse(200, { items: [], total: 0, offset: 0, limit: 50, revision: "rev_7" }),
  ]);
  const result = await api.concepts("ws_1", { state: "fragile", textbookId: "tb_1", q: "导数", limit: 50 });
  assert.equal(result.revision, "rev_7");
  const url = calls[0]!.url;
  assert.ok(url.startsWith(`${BASE}/learner-evaluation/workspaces/ws_1/concepts?`), url);
  assert.ok(url.includes("state=fragile"));
  assert.ok(url.includes("textbook_id=tb_1"));
  assert.ok(url.includes(`q=${encodeURIComponent("导数")}`));
  assert.ok(url.includes("limit=50"));
  assert.ok(!url.includes("offset="), "undefined paging params are dropped");
  assert.ok(!url.includes("student_id="), "legacy web param must not leak");
});

test("sessions, session evidence and the evidence timeline use encoded paths", async () => {
  const { api, calls } = client([
    jsonResponse(200, { items: [{ source_session_ref: "sess a/1", has_evidence: true }], total: 1, offset: 0, limit: 20 }),
    jsonResponse(200, { items: [{ source_id: "src_1", kind: "dialogue" }], total: 1 }),
    jsonResponse(200, { items: [{ source_id: "src_1" }], total: 1, offset: 0, limit: 30 }),
  ]);
  const sessions = await api.sessions("ws_1");
  assert.equal(sessions.items[0]!.source_session_ref, "sess a/1");
  assert.equal(calls[0]!.url, `${BASE}/learner-evaluation/workspaces/ws_1/sessions`);
  const items = await api.sessionEvidence("ws_1", "sess a/1");
  assert.equal(items.items[0]!.source_id, "src_1");
  assert.equal(calls[1]!.url, `${BASE}/learner-evaluation/workspaces/ws_1/sessions/${encodeURIComponent("sess a/1")}`);
  await api.evidence("ws_1", { conceptKey: "ck_9", sourceKind: "assessment", sourceSessionRef: "sess a/1", offset: 30, limit: 30 });
  const url = calls[2]!.url;
  assert.ok(url.startsWith(`${BASE}/learner-evaluation/workspaces/ws_1/evidence?`), url);
  assert.ok(url.includes("concept_key=ck_9"));
  assert.ok(url.includes("source_kind=assessment"));
  assert.ok(url.includes(`source_session_ref=${encodeURIComponent("sess a/1")}`));
  assert.ok(url.includes("offset=30"));
  assert.ok(url.includes("limit=30"));
});

test("evidence detail, job status and deleteEvidence carry the If-Match guard", async () => {
  const { api, calls } = client([
    jsonResponse(200, { source_id: "src_1", source_revision: 3, reviews: [] }),
    jsonResponse(200, { job_id: "job_1", state: "failed", retryable: true }),
    jsonResponse(200, { status: "accepted", deleted: "src_1", affected_concepts: ["c_1"], source_session_ref: "s_1" }),
  ]);
  const detail = await api.evidenceDetail("src_1");
  assert.equal(detail.source_revision, 3);
  assert.equal(calls[0]!.url, `${BASE}/learner-evaluation/evidence/src_1`);
  const job = await api.job("job_1");
  assert.equal(job.state, "failed");
  assert.equal(calls[1]!.url, `${BASE}/learner-evaluation/jobs/job_1`);
  const deleted = await api.deleteEvidence("src_1", { expectedRevision: 3 });
  assert.equal(deleted.status, "accepted");
  assert.equal(calls[2]!.init.method, "DELETE");
  assert.equal(calls[2]!.init.headers?.["If-Match"], "3");
});

test("review, retry and synthesis post their contract bodies", async () => {
  const { api, calls } = client([
    jsonResponse(200, { review_id: "rev_1", job_id: "job_2" }),
    jsonResponse(200, { job_id: "job_3", parent_job_id: "job_1" }),
    jsonResponse(200, { job_id: "job_4", duplicate: true }),
  ]);
  const review = await api.createReview("src_1", {
    interpretation_id: "interp_1",
    reason: "判断与作答不符",
    issue_kind: "wrong_verdict",
    expected_revision: 3,
  });
  assert.equal(review.review_id, "rev_1");
  assert.equal(calls[0]!.init.method, "POST");
  assert.deepEqual(JSON.parse(calls[0]!.init.body as string), {
    interpretation_id: "interp_1",
    reason: "判断与作答不符",
    issue_kind: "wrong_verdict",
    expected_revision: 3,
  });
  const retry = await api.retryJob("job_1", 3);
  assert.equal(retry.parent_job_id, "job_1");
  assert.equal(calls[1]!.url, `${BASE}/learner-evaluation/jobs/job_1/retry`);
  assert.deepEqual(JSON.parse(calls[1]!.init.body as string), { expected_revision: 3 });
  const synthesis = await api.requestSynthesis("ws_1", "rev_7");
  assert.equal(synthesis.duplicate, true);
  assert.equal(calls[2]!.url, `${BASE}/learner-evaluation/workspaces/ws_1/synthesis`);
  assert.deepEqual(JSON.parse(calls[2]!.init.body as string), { expected_scope_revision: "rev_7" });
});

test("insights reads hit /evaluation/* and patchProposal transitions status", async () => {
  const { api, calls } = client([
    jsonResponse(200, { ts: 1, total_turns: 10, pending_proposals: 1 }),
    jsonResponse(200, [{ id: "tr_1", outcome: "success" }]),
    jsonResponse(200, [{ id: "p_1", status: "proposed", target: "t", change: "c" }]),
    jsonResponse(200, { status: "ok", llm_calls: 4 }),
    jsonResponse(200, [{ id: "g_1", active: true }]),
    jsonResponse(200, { entry_id: "g_1", active: false }),
    jsonResponse(200, { proposal_id: "p_1", status: "approved" }),
  ]);
  const report = await api.report();
  assert.equal(report.total_turns, 10);
  const traces = await api.traces(50);
  assert.equal(traces[0]!.id, "tr_1");
  assert.equal(calls[1]!.url, `${BASE}/evaluation/traces?limit=50`);
  const proposals = await api.proposals();
  assert.equal(proposals[0]!.status, "proposed");
  const budget = await api.contextBudget(200);
  assert.equal(budget.llm_calls, 4);
  assert.equal(calls[3]!.url, `${BASE}/evaluation/context-budget?limit=200`);
  const guidance = await api.guidance();
  assert.equal(guidance[0]!.active, true);
  const revoked = await api.revokeGuidance("g_1");
  assert.equal(revoked.active, false);
  assert.equal(calls[5]!.init.method, "DELETE");
  assert.equal(calls[5]!.url, `${BASE}/evaluation/guidance/g_1`);
  const patched = await api.patchProposal("p_1", "approved");
  assert.equal(patched.status, "approved");
  assert.equal(calls[6]!.init.method, "PATCH");
  assert.equal(calls[6]!.url, `${BASE}/evaluation/proposals/p_1`);
  assert.deepEqual(JSON.parse(calls[6]!.init.body as string), { status: "approved" });
  for (const call of calls) {
    assert.ok(!call.url.includes("student_id="), "legacy web param must not leak");
  }
});

test("409 revision_conflict surfaces as ConflictError instead of a body", async () => {
  const { api, calls } = client([
    jsonResponse(409, { detail: { error: { code: "revision_conflict", message: "证据版本已变化，请刷新" } } }),
  ]);
  await assert.rejects(
    api.createReview("src_1", { interpretation_id: "interp_1", reason: "不够准确", expected_revision: 2 }),
    (error: unknown) => error instanceof ConflictError && error.code === "revision_conflict" && error.status === 409,
  );
  assert.equal(calls.length, 1, "domain conflicts are not retried");
});

test("unknown workspace surfaces the server error code", async () => {
  const { api } = client([
    jsonResponse(404, { detail: { error: { code: "workspace_not_found", message: "工作区不存在" } } }),
  ]);
  await assert.rejects(
    api.workspace("ws_missing"),
    (error: unknown) => {
      return error instanceof Error && "code" in error && (error as { code: unknown }).code === "workspace_not_found";
    },
  );
});
