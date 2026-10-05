/** SSE decoder: chunk boundaries, UTF-8 splits, multi-line data, heartbeat, tail flush. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseFrame, readSseFrames, readSseJson } from "../src/index.ts";
import { chunked, sseResponse } from "./helpers.ts";

async function collect(chunks: Uint8Array[]) {
  const frames = [];
  for await (const frame of readSseFrames(sseResponse(chunks).body!)) frames.push(frame);
  return frames;
}

test("parses event/data frames in one chunk", async () => {
  const frames = await collect([
    new TextEncoder().encode('event: snapshot\ndata: {"state":"running"}\n\n'),
  ]);
  assert.equal(frames.length, 1);
  assert.equal(frames[0]!.event, "snapshot");
  assert.deepEqual(JSON.parse(frames[0]!.data), { state: "running" });
});

test("handles a frame split across chunks mid-line", async () => {
  const frames = await collect(
    chunked("event: ans" + "wer\ndata: " + JSON.stringify({ delta: "好的" }) + "\n\n", [8, 7, 5, 4]),
  );
  assert.equal(frames.length, 1);
  assert.equal(frames[0]!.event, "answer");
  assert.deepEqual(JSON.parse(frames[0]!.data), { delta: "好的" });
});

test("reassembles a UTF-8 multibyte character split across chunks", async () => {
  const payload = 'data: {"t":"图"}\n\n';
  const all = new TextEncoder().encode(payload);
  // "图" is 3 bytes (E5 9B BE); split inside the sequence.
  const cut = payload.indexOf("{") + 6;
  const chunks = [all.slice(0, cut), all.slice(cut)];
  const frames = await collect(chunks);
  assert.equal(frames.length, 1);
  assert.deepEqual(JSON.parse(frames[0]!.data), { t: "图" });
});

test("joins multi-line data with newlines and keeps id fields", async () => {
  const frames = await collect([
    new TextEncoder().encode("id: 41\nevent: patch\ndata: line one\ndata: line two\n\n"),
  ]);
  assert.equal(frames[0]!.id, "41");
  assert.equal(frames[0]!.data, "line one\nline two");
});

test("skips heartbeat comment lines and frames without data", async () => {
  const frames = await collect([
    new TextEncoder().encode(": keep-alive\n\nevent: noop\n\nid: 1\ndata: {}\n\n"),
  ]);
  assert.equal(frames.length, 1);
  assert.equal(frames[0]!.id, "1");
});

test("flushes a trailing frame not terminated by a blank line", async () => {
  const frames = await collect([new TextEncoder().encode('event: done\ndata: {"type":"done"}')]);
  assert.equal(frames.length, 1);
  assert.equal(frames[0]!.event, "done");
});

test("readSseJson skips malformed JSON payloads", async () => {
  const events = [];
  for await (const event of readSseJson(
    sseResponse([new TextEncoder().encode('data: {broken\n\ndata: {"ok":1}\n\n')]).body!,
  )) {
    events.push(event);
  }
  assert.equal(events.length, 1);
  assert.deepEqual(events[0]!.data, { ok: 1 });
});

test("an aborted signal ends iteration without yielding", async () => {
  const controller = new AbortController();
  controller.abort();
  const frames = [];
  for await (const frame of readSseFrames(sseResponse([new TextEncoder().encode("data: {}\n\n")]).body!, {
    signal: controller.signal,
  })) {
    frames.push(frame);
  }
  assert.equal(frames.length, 0);
});

test("parseFrame trims id/event and trimStarts data", () => {
  const frame = parseFrame("id:  7 \nevent:  step \ndata:   spaced");
  assert.deepEqual(frame, { id: "7", event: "step", data: "spaced" });
  assert.equal(parseFrame(": only a comment"), null);
});
