/** Chat domain: stream decoding through the transport and event classification. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { chatEventFromFrame, createApiClient, isTerminalChatEvent } from "../src/index.ts";
import { jsonResponse, noSleep, sseResponse, scriptedFetch } from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

test("stream() POSTs /chat/stream and yields typed events until done", async () => {
  const wire =
    'event: answer\ndata: {"delta":"你"}\n\n' +
    "event: heartbeat\n" + 'data: {"type":"heartbeat"}\n\n' +
    'event: done\ndata: {"type":"done","session_id":"s1"}\n\n';
  const { fetch, calls } = scriptedFetch([sseResponse([new TextEncoder().encode(wire)])]);
  const client = createApiClient({
    baseUrl: BASE,
    fetchImpl: fetch,
    tokenProvider: () => "tok",
    sleepImpl: noSleep,
  });
  const events = [];
  for await (const event of client.chat.stream({ message: "hi" })) {
    events.push(event);
  }
  assert.deepEqual(
    events.map((event) => event.type),
    ["answer", "heartbeat", "done"],
  );
  assert.equal(events[0]!.type === "answer" && events[0].delta, "你");
  const call = calls[0]!;
  assert.equal(call.init.method, "POST");
  assert.equal(call.url, `${BASE}/chat/stream`);
  assert.equal(call.init.headers?.Accept, "text/event-stream");
  assert.deepEqual(JSON.parse(String(call.init.body)), { message: "hi" });
  assert.equal(call.init.headers?.Authorization, "Bearer tok");
});

test("stream() surfaces HTTP failures as ApiError", async () => {
  const { fetch } = scriptedFetch([jsonResponse(429, { detail: { error: { code: "rate_limited" } } })]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  await assert.rejects(async () => {
    for await (const _event of client.chat.stream({ message: "hi" })) {
      // no events expected
    }
  }, (error: unknown) => (error as { code?: string }).code === "rate_limited");
});

test("chatEventFromFrame: event name wins, bare data defaults to message, malformed skipped", () => {
  const override = chatEventFromFrame({ event: "answer", data: '{"type":"other","delta":"x"}' });
  assert.equal(override?.type, "answer");
  const bare = chatEventFromFrame({ data: '{"delta":"y"}' });
  assert.equal(bare?.type, "message");
  assert.equal(chatEventFromFrame({ data: "not json" }), null);
  assert.equal(chatEventFromFrame({ data: "[1,2]" }), null);
});

test("isTerminalChatEvent marks done and error", () => {
  assert.ok(isTerminalChatEvent({ type: "done" }));
  assert.ok(isTerminalChatEvent({ type: "error" }));
  assert.ok(!isTerminalChatEvent({ type: "answer", delta: "x" }));
});
