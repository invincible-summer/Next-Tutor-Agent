/** Chat uploads + library attachment wiring (multipart FormDataLike). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createChatClient } from "../src/chat.ts";
import { createTransport } from "../src/transport.ts";
import { jsonResponse, noSleep, scriptedFetch, type RecordedCall } from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

class FakeFormData {
  readonly parts: [string, unknown][] = [];
  append(name: string, value: unknown): void {
    this.parts.push([name, value]);
  }
}

function makeChat(steps: Parameters<typeof scriptedFetch>[0]) {
  const { fetch, calls } = scriptedFetch(steps);
  const transport = createTransport({
    baseUrl: BASE,
    fetchImpl: fetch,
    tokenProvider: () => "tok",
    sleepImpl: noSleep,
  });
  return { chat: createChatClient(transport), calls };
}

function lastCall(calls: RecordedCall[]): RecordedCall {
  const call = calls[calls.length - 1];
  assert.ok(call, "expected at least one recorded call");
  return call;
}

test("upload() POSTs multipart form to /chat/upload with session/grade/workspace query", async () => {
  const { chat, calls } = makeChat([
    jsonResponse(200, { results: [{ id: "f1", filename: "a.pdf" }], session_id: "s1" }),
  ]);
  const form = new FakeFormData();
  form.append("files", { uri: "file:///a.pdf", name: "a.pdf", type: "application/pdf" });
  const res = await chat.upload<{ session_id: string }>(form, {
    sessionId: "s0",
    grade: "初中",
    workspaceId: "w1",
  });
  assert.equal(res.session_id, "s1");
  const call = lastCall(calls);
  assert.equal(call.init.method, "POST");
  assert.equal(
    call.url,
    `${BASE}/chat/upload?session_id=s0&grade=${encodeURIComponent("初中")}&workspace_id=w1`,
  );
  // multipart body passes through untouched; no JSON content-type override.
  assert.equal(call.init.body, form);
  assert.equal(call.init.headers?.["Content-Type"], undefined);
  assert.equal(call.init.headers?.Authorization, "Bearer tok");
});

test("upload() without options hits the bare endpoint", async () => {
  const { chat, calls } = makeChat([jsonResponse(200, { results: [], session_id: "s2" })]);
  const form = new FakeFormData();
  await chat.upload(form);
  assert.equal(lastCall(calls).url, `${BASE}/chat/upload`);
});

test("attachLibraryFiles() POSTs file_ids; workspace rides the query", async () => {
  const { chat, calls } = makeChat([
    jsonResponse(200, { results: [], errors: [], session_id: "s3" }),
  ]);
  await chat.attachLibraryFiles("new", ["f1", "f2"], "w9");
  const call = lastCall(calls);
  assert.equal(
    call.url,
    `${BASE}/chat/sessions/new/attach_library?workspace_id=w9`,
  );
  assert.equal(call.init.method, "POST");
  assert.deepEqual(JSON.parse(String(call.init.body)), { file_ids: ["f1", "f2"] });
});

test("attachLibraryFiles() surfaces server error codes", async () => {
  const { chat } = makeChat([
    jsonResponse(404, { detail: { error: { code: "session_not_found" } } }),
  ]);
  await assert.rejects(
    chat.attachLibraryFiles("ghost", ["f1"]),
    (error: unknown) => (error as { code?: string }).code === "session_not_found",
  );
});
