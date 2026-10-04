/** Transport pipeline: metadata headers, retry policy, 401 single-flight, 409 semantics. */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ApiError,
  ConflictError,
  NetworkError,
  UnauthorizedError,
  createTransport,
} from "../src/index.ts";
import { hangingFetch, jsonResponse, noSleep, scriptedFetch } from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

function headerOf(call: { init: { headers?: Record<string, string> } }, name: string) {
  return call.init.headers?.[name] ?? null;
}

test("attaches client metadata, request id, bearer token and json content type", async () => {
  const { fetch, calls } = scriptedFetch([jsonResponse(200, { ok: true })]);
  const transport = createTransport({
    baseUrl: BASE,
    fetchImpl: fetch,
    tokenProvider: () => "token-1",
    clientMetadataProvider: () => ({ platform: "web", version: "2.2.2", build: "42" }),
    requestIdFactory: () => "req-fixed",
    sleepImpl: noSleep,
  });
  const result = await transport.request<{ ok: boolean }>("/ping", { json: { a: 1 } });
  assert.equal(result.body.ok, true);
  const call = calls[0]!;
  assert.equal(call.url, `${BASE}/ping`);
  assert.equal(headerOf(call, "X-Client-Platform"), "web");
  assert.equal(headerOf(call, "X-Client-Version"), "2.2.2");
  assert.equal(headerOf(call, "X-Client-Build"), "42");
  assert.equal(headerOf(call, "X-Request-ID"), "req-fixed");
  assert.equal(headerOf(call, "Authorization"), "Bearer token-1");
  assert.equal(headerOf(call, "Content-Type"), "application/json");
  assert.equal(call.init.method, "GET"); // default method
  assert.equal(call.init.body, JSON.stringify({ a: 1 }));
});

test("falls back to guest token header when no bearer token", async () => {
  const { fetch, calls } = scriptedFetch([jsonResponse(200, {})]);
  const transport = createTransport({
    baseUrl: BASE,
    fetchImpl: fetch,
    guestTokenProvider: () => "guest-7",
    sleepImpl: noSleep,
  });
  await transport.request("/anything");
  assert.equal(headerOf(calls[0]!, "X-Guest-Token"), "guest-7");
  assert.equal(headerOf(calls[0]!, "Authorization"), null);
});

test("GET retries 503 and network errors, then succeeds", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(503, {}),
    new Error("socket hang up"),
    jsonResponse(200, { value: 3 }),
  ]);
  const transport = createTransport({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  const result = await transport.request<{ value: number }>("/jobs/1");
  assert.equal(result.body.value, 3);
  assert.equal(calls.length, 3);
  // X-Request-ID stays stable across retries so the server can correlate them.
  assert.equal(headerOf(calls[0]!, "X-Request-ID"), headerOf(calls[2]!, "X-Request-ID"));
});

test("GET gives up after two retries and surfaces the last status", async () => {
  const { fetch, calls } = scriptedFetch([jsonResponse(503, {})]);
  const transport = createTransport({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  await assert.rejects(
    transport.request("/jobs/1"),
    (error: unknown) => error instanceof ApiError && error.status === 503 && error.retryable,
  );
  assert.equal(calls.length, 3);
});

test("POST never auto-retries without an idempotency key", async () => {
  const { fetch, calls } = scriptedFetch([jsonResponse(502, {})]);
  const transport = createTransport({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  await assert.rejects(transport.request("/turns", { method: "POST", json: {} }));
  assert.equal(calls.length, 1);
});

test("POST retries when the endpoint opts in with an Idempotency-Key", async () => {
  const { fetch, calls } = scriptedFetch([jsonResponse(502, {}), jsonResponse(201, { id: "j1" })]);
  const transport = createTransport({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  const result = await transport.request<{ id: string }>("/turns", {
    method: "POST",
    json: {},
    idempotencyKey: "idem-1",
    retryWrites: true,
  });
  assert.equal(result.body.id, "j1");
  assert.equal(calls.length, 2);
  assert.equal(headerOf(calls[0]!, "Idempotency-Key"), "idem-1");
});

test("401 without an onUnauthorized hook surfaces immediately", async () => {
  const { fetch, calls } = scriptedFetch([jsonResponse(401, { detail: "nope" })]);
  const transport = createTransport({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  await assert.rejects(transport.request("/me"), UnauthorizedError);
  assert.equal(calls.length, 1);
});

test("401 coalesces into one refresh and retries only with a new token", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(401, {}),
    jsonResponse(401, {}),
    jsonResponse(200, { ok: 1 }),
    jsonResponse(200, { ok: 2 }),
  ]);
  let token = "old";
  let hookCalls = 0;
  const transport = createTransport({
    baseUrl: BASE,
    fetchImpl: fetch,
    tokenProvider: () => token,
    onUnauthorized: () => {
      hookCalls += 1;
      token = "new";
    },
    sleepImpl: noSleep,
  });
  const [a, b] = await Promise.all([
    transport.request<{ ok: number }>("/me"),
    transport.request<{ ok: number }>("/profile"),
  ]);
  assert.equal(hookCalls, 1);
  assert.equal(a.body.ok + b.body.ok, 3);
  assert.equal(calls.length, 4);
  assert.equal(headerOf(calls[0]!, "Authorization"), "Bearer old");
  assert.equal(headerOf(calls[2]!, "Authorization"), "Bearer new");
});

test("401 after refresh without a token change is not retried", async () => {
  const { fetch, calls } = scriptedFetch([jsonResponse(401, {})]);
  let hookCalls = 0;
  const transport = createTransport({
    baseUrl: BASE,
    fetchImpl: fetch,
    tokenProvider: () => "same",
    onUnauthorized: () => {
      hookCalls += 1;
    },
    sleepImpl: noSleep,
  });
  await assert.rejects(transport.request("/me"), UnauthorizedError);
  assert.equal(hookCalls, 1);
  assert.equal(calls.length, 1);
});

test("409 with a different code is surfaced as ConflictError, never retried", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(409, { detail: { error: { code: "revision_conflict" } } }),
  ]);
  const transport = createTransport({
    baseUrl: BASE,
    fetchImpl: fetch,
    sleepImpl: noSleep,
  });
  await assert.rejects(
    transport.request("/turns", {
      method: "POST",
      json: {},
      waitForConflict: { code: "evaluation_pending" },
    }),
    (error: unknown) => error instanceof ConflictError && error.code === "revision_conflict",
  );
  assert.equal(calls.length, 1);
});

test("waitForConflict re-polls a matching 409 code until it clears", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(409, { detail: { error: { code: "evaluation_pending" } } }),
    jsonResponse(409, { detail: { error: { code: "evaluation_pending" } } }),
    jsonResponse(200, { status: "started" }),
  ]);
  const transport = createTransport({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  const result = await transport.request<{ status: string }>("/assessment/start", {
    method: "POST",
    json: {},
    waitForConflict: { code: "evaluation_pending", deadlineMs: 60_000 },
  });
  assert.equal(result.body.status, "started");
  assert.equal(calls.length, 3);
});

test("per-attempt timeout aborts the fetch as a non-retryable NetworkError", async () => {
  const transport = createTransport({
    baseUrl: BASE,
    fetchImpl: hangingFetch(),
    sleepImpl: noSleep,
  });
  await assert.rejects(
    transport.request("/slow", { timeoutMs: 15 }),
    (error: unknown) => error instanceof NetworkError && !error.retryable,
  );
});

test("query params are encoded and undefined values dropped", async () => {
  const { fetch, calls } = scriptedFetch([jsonResponse(200, {})]);
  const transport = createTransport({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  await transport.request("/search", {
    query: { q: "a b/c", page: 2, flag: true, skip: undefined },
  });
  assert.equal(
    calls[0]!.url,
    `${BASE}/search?q=${encodeURIComponent("a b/c")}&page=2&flag=true`,
  );
});
