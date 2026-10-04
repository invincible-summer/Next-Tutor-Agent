/** Guest domain: session lifecycle wire shapes. */
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

test("createSession posts /guest/session and returns the token envelope", async () => {
  const { api, calls } = client([jsonResponse(200, { token: "gst_1", expires_in: 3600 })]);
  const session = await api.guest.createSession();
  assert.equal(session.token, "gst_1");
  assert.equal(session.expires_in, 3600);
  assert.equal(calls[0]!.url, `${BASE}/guest/session`);
  assert.equal(calls[0]!.init.method, "POST");
});

test("deleteSession sends the token as X-Guest-Token and skips the body", async () => {
  const { api, calls } = client([jsonResponse(204, null)]);
  await api.guest.deleteSession("gst_1");
  const call = calls[0]!;
  assert.equal(call.init.method, "DELETE");
  assert.equal(call.init.headers?.["X-Guest-Token"], "gst_1");
});

test("textbooks lists public textbook stubs", async () => {
  const { api, calls } = client([jsonResponse(200, { items: [{ id: "tb_1", title: " demo" }] })]);
  const result = await api.guest.textbooks();
  assert.equal(result.items.length, 1);
  assert.equal(result.items[0]!.id, "tb_1");
  assert.equal(calls[0]!.url, `${BASE}/guest/textbooks`);
});
