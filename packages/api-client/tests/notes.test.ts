/** Notes domain: vault CRUD conflicts, SSE streams, upload passthrough. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ConflictError, createApiClient } from "../src/index.ts";
import { VAULT_AGENT_KEY } from "../src/notes.ts";
import {
  RecordingForm,
  jsonResponse,
  noSleep,
  scriptedFetch,
  sseResponse,
  bytes,
} from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

function client(steps: Parameters<typeof scriptedFetch>[0]) {
  const script = scriptedFetch(steps);
  return {
    api: createApiClient({ baseUrl: BASE, fetchImpl: script.fetch, sleepImpl: noSleep }),
    calls: script.calls,
  };
}

test("vault and agent default to the _vault chat key", async () => {
  const { api, calls } = client([
    jsonResponse(200, { folders: [], notes: [], tags: {}, custom_templates: [], stats: { note_count: 0, folder_count: 0, link_count: 0, unresolved_links: [], due_review_count: 0, due_review_ids: [] } }),
    jsonResponse(200, { note_id: VAULT_AGENT_KEY, mode: "ask", messages: [], modes: ["ask", "plan", "authorize"] }),
  ]);
  await api.notes.vault();
  await api.notes.getAgent();
  assert.equal(calls[0]!.url, `${BASE}/notes/vault`);
  assert.equal(calls[1]!.url, `${BASE}/notes/notes/_vault/agent`);
});

test("saveNote PUTs the optimistic revision; stale answers conflict", async () => {
  const { api, calls } = client([
    jsonResponse(200, { note: { id: "n_1", revision: 4 } }),
    jsonResponse(409, { detail: "内容已更新", note: { id: "n_1", revision: 5 }, content: "server text" }),
  ]);
  await api.notes.saveNote("n_1", { content: "new", base_revision: 4 });
  assert.equal(calls[0]!.init.method, "PUT");
  assert.deepEqual(JSON.parse(calls[0]!.init.body as string), {
    content: "new",
    base_revision: 4,
  });
  await assert.rejects(
    api.notes.saveNote("n_1", { content: "again", base_revision: 4 }),
    (error: unknown) => error instanceof ConflictError,
  );
});

test("generateStream yields typed SSE events (event name wins, bare data uses body type)", async () => {
  const wire = [
    'event: status\ndata: {"type":"started","stage":"collect"}\n\n',
    'data: {"type":"plan","items":[]}\n\n',
    ": heartbeat\n\n",
    'event: done\ndata: {"type":"done","note_id":"n_9"}\n\n',
  ].join("");
  const { api, calls } = client([sseResponse([bytes(wire)])]);
  const events = [];
  for await (const event of api.notes.generateStream({ template_id: "tpl_1" })) {
    events.push(event);
  }
  assert.equal(events[0]!.type, "status");
  assert.equal(events[0]!.stage, "collect");
  assert.equal(events[1]!.type, "plan");
  assert.equal(events[2]!.type, "done");
  assert.equal(events.length, 3);
  const call = calls[0]!;
  assert.equal(call.url, `${BASE}/notes/generate`);
  assert.equal(call.init.headers?.Accept, "text/event-stream");
  assert.deepEqual(JSON.parse(call.init.body as string), { template_id: "tpl_1" });
});

test("upload posts the platform form as-is", async () => {
  const { api, calls } = client([
    jsonResponse(200, { results: [{ id: "n_2", filename: "a.pdf", char_count: 10, chunk_count: 1 }] }),
  ]);
  const form = new RecordingForm();
  form.append("files", { uri: "file://a.pdf", name: "a.pdf", type: "application/pdf" });
  const result = await api.notes.upload(form);
  const first = result.results[0]!;
  assert.ok("id" in first, "expected a successful upload result entry");
  assert.equal(first.id, "n_2");
  assert.equal(calls[0]!.init.body, form);
  assert.equal(calls[0]!.init.method, "POST");
});
