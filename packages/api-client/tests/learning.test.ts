/** Learning (orchestration) domain: plan reads, task lifecycle, launch deep link. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/index.ts";
import { jsonResponse, noSleep, scriptedFetch } from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

function client(steps: Parameters<typeof scriptedFetch>[0]) {
  const script = scriptedFetch(steps);
  return {
    api: createApiClient({ baseUrl: BASE, fetchImpl: script.fetch, sleepImpl: noSleep }),
    calls: script.calls,
  };
}

test("plan/today read the summary and daily projections", async () => {
  const { api, calls } = client([
    jsonResponse(200, { goals: [], weekly_plan: [], daily_tasks: [{ id: "t_1", day: "2026-10-04" }] }),
    jsonResponse(200, [{ id: "t_1", day: "2026-10-04", status: "pending" }]),
  ]);
  const plan = await api.learning.plan();
  assert.ok(Array.isArray(plan.daily_tasks));
  const today = await api.learning.today();
  assert.equal(today[0]!.id, "t_1");
  assert.equal(calls[0]!.url, `${BASE}/orchestration/plan`);
  assert.equal(calls[1]!.url, `${BASE}/orchestration/today`);
});

test("setGoal sends the full normalized payload", async () => {
  const { api, calls } = client([
    jsonResponse(200, { ok: true, goal_id: "g_1", weeks: [], first_task: null }),
  ]);
  const result = await api.learning.setGoal({ title: "掌握线性代数" });
  assert.equal(result.goal_id, "g_1");
  assert.deepEqual(JSON.parse(calls[0]!.init.body as string), {
    title: "掌握线性代数",
    description: "",
    goal_type: "ability",
    subjects: [],
    target_concept_ids: [],
    workspace_id: "",
    deadline: 0,
  });
});

test("complete/launch hit the task action endpoints with empty bodies", async () => {
  const { api, calls } = client([
    jsonResponse(200, { ok: true, emitted_events: 2 }),
    jsonResponse(200, { ok: true, task_id: "t_1", episode_id: "ep_1", session_id: "s_9", launch_url: "/chat/s_9", resumed: false }),
  ]);
  await api.learning.completeTask("t_1");
  const launch = await api.learning.launchTask("t_1");
  assert.equal(launch.launch_url, "/chat/s_9");
  assert.equal(calls[0]!.url, `${BASE}/orchestration/task/t_1/complete`);
  assert.equal(calls[0]!.init.body, "{}");
  assert.equal(calls[1]!.url, `${BASE}/orchestration/task/t_1/launch`);
});
