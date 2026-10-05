/** Knowledge domain: graph query mapping and read projections. */
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

test("graph maps camelCase query to snake_case wire params and drops undefined", async () => {
  const { api, calls } = client([
    jsonResponse(200, { status: "ok", nodes: [], edges: [], coverage: [] }),
  ]);
  await api.knowledge.graph({
    textbookId: "tb_1",
    view: "chapter",
    chapterId: "ch_2",
    workspaceId: "ws_9",
  });
  const url = calls[0]!.url;
  assert.ok(url.startsWith(`${BASE}/knowledge/graph?`), url);
  assert.ok(url.includes("textbook_id=tb_1"));
  assert.ok(url.includes("view=chapter"));
  assert.ok(url.includes("chapter_id=ch_2"));
  assert.ok(url.includes("workspace_id=ws_9"));
  assert.ok(!url.includes("file_id="));
  assert.ok(!url.includes("student_id="), "legacy web param must not leak");
});

test("concept detail and disabled taxonomy branch on status", async () => {
  const script = scriptedFetch([
    jsonResponse(200, { status: "ok", concept: { id: "c_1", name: "导数" }, edges: {} }),
    jsonResponse(200, { status: "disabled", levels: [] }),
  ]);
  const api = createApiClient({ baseUrl: BASE, fetchImpl: script.fetch, sleepImpl: noSleep });
  const concept = await api.knowledge.concept("c_1", { workspaceId: "ws_1" });
  assert.equal(concept.concept?.name, "导数");
  assert.ok(script.calls[0]!.url.includes("/knowledge/concepts/c_1?workspace_id=ws_1"));
  const taxonomy = await api.knowledge.taxonomy();
  assert.equal(taxonomy.status, "disabled");
});
