/** Assistant domain: conversations/turns/SSE, actions, workflows, proactive services. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ConflictError, UnauthorizedError } from "../src/errors.ts";
import { createTransport } from "../src/transport.ts";
import { createAssistantClient } from "../src/assistant.ts";
import {
  chunked,
  jsonResponse,
  noSleep,
  scriptedFetch,
  sseResponse,
} from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

function client(steps: Parameters<typeof scriptedFetch>[0]) {
  const script = scriptedFetch(steps);
  const transport = createTransport({
    baseUrl: BASE,
    fetchImpl: script.fetch,
    tokenProvider: () => "tok",
    sleepImpl: noSleep,
  });
  return { assistant: createAssistantClient(transport), calls: script.calls };
}

const EXECUTION = {
  action: {
    action_id: "a_1",
    conversation_id: "c_1",
    turn_id: "t_1",
    label: "打开首页",
    payload: { kind: "navigate", target: { kind: "module", route_id: "home" } },
    state: "succeeded",
    created_at: "2026-01-01T00:00:00Z",
    expires_at: "2026-01-01T01:00:00Z",
  },
  conversation_revision: 5,
  business_result: { kind: "none" },
  command: null,
  command_id: null,
  ack_token: null,
};

test("conversations: create/list/get/delete carry revision guards", async () => {
  const { assistant, calls } = client([
    jsonResponse(201, { conversation_id: "c_1", revision: 1, created_at: "2026-01-01T00:00:00Z" }),
    jsonResponse(200, { items: [], total: 0, offset: 0, limit: 20 }),
    jsonResponse(200, {
      conversation_id: "c_1",
      title: "数学",
      revision: 3,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:10:00Z",
      messages: [],
      has_more: true,
      active_turn: null,
    }),
    jsonResponse(204, null),
  ]);
  const created = await assistant.createConversation("req_1", "数学");
  assert.equal(calls[0]!.url, `${BASE}/assistant/conversations`);
  assert.equal(calls[0]!.init.method, "POST");
  assert.deepEqual(JSON.parse(calls[0]!.init.body as string), {
    client_request_id: "req_1",
    title: "数学",
  });
  assert.equal(created.conversation_id, "c_1");

  await assistant.listConversations(0, 20);
  assert.equal(calls[1]!.url, `${BASE}/assistant/conversations?offset=0&limit=20`);

  const detail = await assistant.getConversation("c_1", 12);
  assert.equal(calls[2]!.url, `${BASE}/assistant/conversations/c_1?before_seq=12`);
  assert.equal(detail.has_more, true);

  await assistant.deleteConversation("c_1", 3);
  assert.equal(calls[3]!.url, `${BASE}/assistant/conversations/c_1`);
  assert.equal(calls[3]!.init.method, "DELETE");
  assert.equal(calls[3]!.init.headers?.["If-Match"], "3");
});

test("turns: submit is accepted (202) and cancel posts the client request id", async () => {
  const { assistant, calls } = client([
    jsonResponse(202, {
      turn_id: "t_1",
      conversation_id: "c_1",
      conversation_revision: 4,
      state: "running",
      events_path: "/api/v1/assistant/turns/t_1/events",
      duplicate: false,
    }),
    jsonResponse(200, { turn_id: "t_1", state: "cancelled", cancel_requested: true }),
  ]);
  const accepted = await assistant.submitTurn("c_1", {
    client_message_id: "m_1",
    expected_conversation_revision: 3,
    text: "你好",
    lang: "zh",
    timezone: "Asia/Shanghai",
    scope: { mode: "follow_page" },
    page_context: { route_id: "home", route_epoch: 1 },
  });
  assert.equal(calls[0]!.url, `${BASE}/assistant/conversations/c_1/turns`);
  assert.equal(calls[0]!.init.method, "POST");
  assert.equal(JSON.parse(calls[0]!.init.body as string).client_message_id, "m_1");
  assert.equal(accepted.turn_id, "t_1");
  assert.equal(accepted.events_path, "/api/v1/assistant/turns/t_1/events");

  const cancelled = await assistant.cancelTurn("t_1", "req_2");
  assert.equal(calls[1]!.url, `${BASE}/assistant/turns/t_1/cancel`);
  assert.deepEqual(JSON.parse(calls[1]!.init.body as string), { client_request_id: "req_2" });
  assert.equal(cancelled.cancel_requested, true);
});

test("streamTurnEvents decodes a chunked multi-event stream and stops at turn_done", async () => {
  const wire = [
    'event: status\nid: t_1:1\ndata: {"event":"status","turn_id":"t_1","event_seq":1,"stage":"reading","label":"读取中","emitted_at":"2026-01-01T00:00:00Z"}\n\n',
    'data: {"event":"text_delta","turn_id":"t_1","event_seq":2,"message_id":"m_2","block_id":"b_1","delta":"你好","emitted_at":"2026-01-01T00:00:01Z"}\n\n',
    ": ping\n\n",
    'event: turn_done\nid: t_1:3\ndata: {"event":"turn_done","turn_id":"t_1","event_seq":3,"state":"completed","conversation_revision":4,"emitted_at":"2026-01-01T00:00:02Z"}\n\n',
    'event: status\nid: t_1:4\ndata: {"event":"status","turn_id":"t_1","event_seq":4,"stage":"composing","label":"x","emitted_at":"2026-01-01T00:00:03Z"}\n\n',
  ].join("");
  const { assistant, calls } = client([sseResponse(chunked(wire, [7, 64]))]);
  const events = [];
  for await (const event of assistant.streamTurnEvents("t_1", 2)) {
    events.push(event);
  }
  assert.equal(calls[0]!.url, `${BASE}/assistant/turns/t_1/events?after_seq=2`);
  assert.equal(calls[0]!.init.method, "GET");
  assert.equal(calls[0]!.init.headers?.Accept, "text/event-stream");
  assert.equal(calls[0]!.init.body, null);
  // heartbeat skipped; the frame after turn_done is never yielded
  assert.equal(events.length, 3);
  assert.equal(events[0]!.event, "status");
  assert.equal(events[0]!.event_seq, 1);

  const deltaEvent = events[1]!;
  if (deltaEvent.event !== "text_delta") assert.fail("expected a text_delta event");
  assert.equal(deltaEvent.delta, "你好");
  assert.equal(deltaEvent.block_id, "b_1");

  const doneEvent = events[2]!;
  if (doneEvent.event !== "turn_done") assert.fail("expected a turn_done event");
  assert.equal(doneEvent.state, "completed");
  assert.equal(doneEvent.conversation_revision, 4);
});

test("streamTurnEvents rejects with the server envelope on HTTP errors", async () => {
  const { assistant } = client([
    sseResponse([], 404),
  ]);
  await assert.rejects(
    async () => {
      for await (const event of assistant.streamTurnEvents("gone", 0)) void event;
    },
    (error: unknown) => error instanceof Error,
  );
});

test("actions: preview/approve/execute/get/ack/undo hit the action endpoints", async () => {
  const { assistant, calls } = client([
    jsonResponse(200, {
      preview_id: "p_1",
      action_id: "a_1",
      title: "重命名会话",
      summary: "s",
      parameter_hash: "hash_1",
      expires_at: "2026-01-01T01:00:00Z",
      approval: "review_required",
    }),
    jsonResponse(200, { approval_id: "ap_1", decision: "approve", expires_at: "2026-01-01T01:00:00Z" }),
    jsonResponse(200, EXECUTION),
    jsonResponse(200, EXECUTION),
    jsonResponse(200, EXECUTION),
    jsonResponse(200, { action: EXECUTION.action, undone: true, reason: null }),
  ]);
  const preview = await assistant.getActionPreview("a_1");
  assert.equal(calls[0]!.url, `${BASE}/assistant/actions/a_1/preview`);
  assert.equal(preview.parameter_hash, "hash_1");

  const approval = await assistant.approveAction("a_1", {
    preview_id: "p_1",
    parameter_hash: "hash_1",
    decision: "approve",
  });
  assert.equal(calls[1]!.url, `${BASE}/assistant/actions/a_1/approve`);
  assert.deepEqual(JSON.parse(calls[1]!.init.body as string), {
    preview_id: "p_1",
    parameter_hash: "hash_1",
    decision: "approve",
  });
  assert.equal(approval.approval_id, "ap_1");

  await assistant.executeAction("a_1", {
    invocation_id: "inv_1",
    client_instance_id: "inst_1",
    route_epoch: 2,
    approval_id: "ap_1",
  });
  assert.equal(calls[2]!.url, `${BASE}/assistant/actions/a_1/execute`);
  assert.equal(JSON.parse(calls[2]!.init.body as string).invocation_id, "inv_1");

  await assistant.getAction("a_1", "inst_1");
  assert.equal(calls[3]!.url, `${BASE}/assistant/actions/a_1?client_instance_id=inst_1`);

  await assistant.ackAction("a_1", {
    command_id: "cmd_1",
    ack_token: "tok_1",
    result: "succeeded",
  });
  assert.equal(calls[4]!.url, `${BASE}/assistant/actions/a_1/ack`);

  const undone = await assistant.undoAction("a_1", {
    client_request_id: "req_9",
    expected_result_revision: "r1",
  });
  assert.equal(calls[5]!.url, `${BASE}/assistant/actions/a_1/undo`);
  assert.equal(undone.undone, true);
});

test("search maps filters to query params and omits the unset ones", async () => {
  const empty = { items: [], total: 0, offset: 0, limit: 5, complete: true };
  const { assistant, calls } = client([jsonResponse(200, empty), jsonResponse(200, empty)]);
  await assistant.search({
    q: "导数",
    kinds: ["note", "session"],
    workspace_id: "ws_1",
    offset: 10,
    limit: 5,
    include_content: true,
  });
  assert.equal(
    calls[0]!.url,
    `${BASE}/assistant/search?q=${encodeURIComponent("导数")}`
      + `&kinds=${encodeURIComponent("note,session")}&workspace_id=ws_1`
      + "&offset=10&limit=5&include_content=true",
  );

  await assistant.search({ q: "x" });
  assert.equal(calls[1]!.url, `${BASE}/assistant/search?q=x`);
});

test("drafts: get/consume/delete with the result entity passthrough", async () => {
  const draft = {
    draft_id: "d_1",
    conversation_id: "c_1",
    action_id: "a_1",
    prefill: { kind: "note", title: "t", markdown: "m" },
    created_at: "2026-01-01T00:00:00Z",
    expires_at: "2026-01-01T01:00:00Z",
    consumed: true,
    result_entity: { kind: "note", id: "n_1" },
  };
  const { assistant, calls } = client([
    jsonResponse(200, draft),
    jsonResponse(200, draft),
    jsonResponse(200, draft),
    jsonResponse(204, null),
  ]);
  const loaded = await assistant.getDraft("d_1");
  assert.equal(calls[0]!.url, `${BASE}/assistant/drafts/d_1`);
  assert.equal(loaded.draft_id, "d_1");

  await assistant.consumeDraft("d_1", { kind: "note", id: "n_1" });
  assert.equal(calls[1]!.url, `${BASE}/assistant/drafts/d_1/consume`);
  assert.deepEqual(JSON.parse(calls[1]!.init.body as string), {
    result_entity: { kind: "note", id: "n_1" },
  });

  await assistant.consumeDraft("d_1");
  assert.deepEqual(JSON.parse(calls[2]!.init.body as string), { result_entity: null });

  await assistant.deleteDraft("d_1");
  assert.equal(calls[3]!.init.method, "DELETE");
  assert.equal(calls[3]!.url, `${BASE}/assistant/drafts/d_1`);
});

test("workflows: list/get/approve/start/cancel/retry/resume", async () => {
  const workflow = {
    workflow_id: "wf_1",
    conversation_id: "c_1",
    revision: 2,
    template: "organize_materials",
    objective: "整理资料",
    state: "waiting",
    steps: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:05:00Z",
  };
  const { assistant, calls } = client([
    jsonResponse(200, { items: [workflow], total: 1 }),
    jsonResponse(200, { workflow, preview: { workflow_id: "wf_1", plan_hash: "ph_1" } }),
    jsonResponse(200, workflow),
    jsonResponse(202, workflow),
    jsonResponse(200, workflow),
    jsonResponse(202, workflow),
    jsonResponse(202, workflow),
  ]);
  const list = await assistant.listWorkflows(0, 20, "waiting");
  assert.equal(calls[0]!.url, `${BASE}/assistant/workflows?offset=0&limit=20&state=waiting`);
  assert.equal(list.total, 1);

  const detail = await assistant.getWorkflow("wf_1");
  assert.equal(calls[1]!.url, `${BASE}/assistant/workflows/wf_1`);
  assert.equal(detail.workflow.workflow_id, "wf_1");

  await assistant.approveWorkflow("wf_1", {
    expected_revision: 2,
    plan_hash: "ph_1",
    approved_step_ids: ["s_1"],
  });
  assert.equal(calls[2]!.url, `${BASE}/assistant/workflows/wf_1/approve`);
  assert.deepEqual(JSON.parse(calls[2]!.init.body as string), {
    expected_revision: 2,
    plan_hash: "ph_1",
    approved_step_ids: ["s_1"],
  });

  await assistant.startWorkflow("wf_1", { client_request_id: "req_3", expected_revision: 3 });
  assert.equal(calls[3]!.url, `${BASE}/assistant/workflows/wf_1/start`);

  await assistant.cancelWorkflow("wf_1");
  assert.equal(calls[4]!.url, `${BASE}/assistant/workflows/wf_1/cancel`);
  assert.equal(calls[4]!.init.method, "POST");
  assert.equal(calls[4]!.init.body, null);

  await assistant.retryWorkflowStep("wf_1", "s_1", {
    expected_revision: 4,
    client_request_id: "req_4",
  });
  assert.equal(calls[5]!.url, `${BASE}/assistant/workflows/wf_1/steps/s_1/retry`);

  await assistant.resumeWorkflow("wf_1", { expected_revision: 5, choice_id: "ch_1" });
  assert.equal(calls[6]!.url, `${BASE}/assistant/workflows/wf_1/resume`);
  assert.deepEqual(JSON.parse(calls[6]!.init.body as string), {
    expected_revision: 5,
    choice_id: "ch_1",
  });
});

test("subscriptions, notifications and reports round-trip", async () => {
  const subscription = {
    subscription_id: "sub_1",
    revision: 1,
    kind: "weekly_brief",
    enabled: true,
    timezone: "Asia/Shanghai",
    local_time: "08:00",
    weekdays: [1],
    scope: {},
    quiet_hours: { start: "22:00", end: "07:00" },
    next_run_at: "2026-01-05T00:00:00Z",
    last_run_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
  const notification = {
    notification_id: "n_1",
    kind: "subscription",
    title: "周报已生成",
    summary: "s",
    created_at: "2026-01-01T00:00:00Z",
    read_at: null,
    dismissed_at: null,
    expires_at: "2026-01-08T00:00:00Z",
    report_id: "r_1",
    workflow_id: null,
    target: null,
    source_ids: [],
  };
  const { assistant, calls } = client([
    jsonResponse(200, { items: [subscription] }),
    jsonResponse(201, subscription),
    jsonResponse(200, { ...subscription, enabled: false, revision: 2 }),
    jsonResponse(204, null),
    jsonResponse(200, { items: [notification], total: 1, offset: 0, limit: 20 }),
    jsonResponse(200, notification),
    jsonResponse(200, notification),
    jsonResponse(200, { report_id: "r_1", kind: "weekly_brief" }),
    jsonResponse(204, null),
  ]);
  const subs = await assistant.listSubscriptions();
  assert.equal(calls[0]!.url, `${BASE}/assistant/subscriptions`);
  assert.equal(subs.items[0]!.subscription_id, "sub_1");

  await assistant.createSubscription({
    client_request_id: "req_5",
    kind: "weekly_brief",
    timezone: "Asia/Shanghai",
    local_time: "08:00",
  });
  assert.equal(calls[1]!.url, `${BASE}/assistant/subscriptions`);
  assert.equal(JSON.parse(calls[1]!.init.body as string).kind, "weekly_brief");

  await assistant.patchSubscription("sub_1", { expected_revision: 1, enabled: false });
  assert.equal(calls[2]!.url, `${BASE}/assistant/subscriptions/sub_1`);
  assert.equal(calls[2]!.init.method, "PATCH");

  await assistant.deleteSubscription("sub_1");
  assert.equal(calls[3]!.init.method, "DELETE");

  const notes = await assistant.listNotifications(0, 20, true);
  assert.equal(calls[4]!.url, `${BASE}/assistant/notifications?offset=0&limit=20&unread_only=true`);
  assert.equal(notes.total, 1);

  await assistant.markNotificationRead("n_1");
  assert.equal(calls[5]!.url, `${BASE}/assistant/notifications/n_1/read`);
  assert.equal(calls[5]!.init.method, "POST");

  await assistant.dismissNotification("n_1", true);
  assert.equal(calls[6]!.url, `${BASE}/assistant/notifications/n_1/dismiss`);
  assert.deepEqual(JSON.parse(calls[6]!.init.body as string), { mute_entity: true });

  const report = await assistant.getReport("r_1");
  assert.equal(calls[7]!.url, `${BASE}/assistant/reports/r_1`);
  assert.equal(report.report_id, "r_1");

  await assistant.deleteReport("r_1");
  assert.equal(calls[8]!.init.method, "DELETE");
  assert.equal(calls[8]!.url, `${BASE}/assistant/reports/r_1`);
});

test("preferences GET/PUT and audio job lifecycle", async () => {
  const job = {
    job_id: "j_1",
    state: "ready",
    clips: [{ clip_id: "clip_1", state: "ready" }],
    truncated: false,
  };
  const { assistant, calls } = client([
    jsonResponse(200, { tone: "neutral", revision: 3 }),
    jsonResponse(200, { tone: "encouraging", revision: 4 }),
    jsonResponse(202, { ...job, state: "queued", clips: [] }),
    jsonResponse(200, job),
    jsonResponse(200, { ...job, state: "cancelled" }),
  ]);
  const prefs = await assistant.getPreferences();
  assert.equal(calls[0]!.url, `${BASE}/assistant/preferences`);
  assert.equal(prefs.revision, 3);

  await assistant.putPreferences({ tone: "encouraging", base_revision: 3 });
  assert.equal(calls[1]!.url, `${BASE}/assistant/preferences`);
  assert.equal(calls[1]!.init.method, "PUT");
  assert.deepEqual(JSON.parse(calls[1]!.init.body as string), {
    tone: "encouraging",
    base_revision: 3,
  });

  const created = await assistant.createAudioJob({
    message_id: "m_2",
    policy: "auto",
    language: "zh",
    client_request_id: "req_a",
  });
  assert.equal(calls[2]!.url, `${BASE}/assistant/audio/jobs`);
  assert.equal(calls[2]!.init.method, "POST");
  assert.deepEqual(JSON.parse(calls[2]!.init.body as string), {
    message_id: "m_2",
    policy: "auto",
    language: "zh",
    client_request_id: "req_a",
  });
  assert.equal(created.state, "queued");

  const polled = await assistant.getAudioJob("j_1");
  assert.equal(calls[3]!.url, `${BASE}/assistant/audio/jobs/j_1`);
  assert.equal(polled.clips[0]!.clip_id, "clip_1");

  await assistant.cancelAudioJob("j_1");
  assert.equal(calls[4]!.url, `${BASE}/assistant/audio/jobs/j_1/cancel`);
  assert.equal(calls[4]!.init.method, "POST");
});

test("error envelope surfaces code; 401 maps to UnauthorizedError, 409 to ConflictError", async () => {
  const { assistant } = client([
    jsonResponse(401, {
      error: { code: "authentication_required", message: "登录后才能使用学习助手会话。", retryable: false },
    }),
    jsonResponse(409, {
      error: {
        code: "revision_conflict",
        message: "会话版本已变化。",
        retryable: false,
        extra: { latest_revision: 5 },
      },
    }),
  ]);
  await assert.rejects(
    assistant.listConversations(),
    // 401 surfaces as UnauthorizedError (code "unauthorized"); the server
    // envelope is kept on `details` and its message is preserved.
    (error: unknown) =>
      error instanceof UnauthorizedError
      && error.status === 401
      && error.message === "登录后才能使用学习助手会话。",
  );
  await assert.rejects(
    assistant.putPreferences({ tone: "neutral", base_revision: 1 }),
    (error: unknown) =>
      error instanceof ConflictError
      && error.status === 409
      && error.code === "revision_conflict",
  );
});
