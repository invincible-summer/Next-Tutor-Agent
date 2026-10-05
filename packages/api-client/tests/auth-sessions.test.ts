import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, createApiClient } from "../src/index.ts";
import { jsonResponse, noSleep, scriptedFetch } from "./helpers.ts";

function client(steps: Parameters<typeof scriptedFetch>[0]) {
  const script = scriptedFetch(steps);
  return {
    api: createApiClient({ baseUrl: "https://api.example.test/api/v1", fetchImpl: script.fetch, sleepImpl: noSleep }),
    calls: script.calls,
  };
}

test("refresh preserves the rotated credential response and sends the explicit token", async () => {
  const expected = { access_token: "access-new", refresh_token: "refresh-new", expires_in: 900 };
  const { api, calls } = client([jsonResponse(200, expected)]);
  assert.deepEqual(await api.auth.refresh("refresh-old"), expected);
  assert.equal(calls[0]!.url, "https://api.example.test/api/v1/auth/refresh");
  assert.equal(calls[0]!.init.method, "POST");
  assert.deepEqual(JSON.parse(calls[0]!.init.body as string), { refresh_token: "refresh-old" });
});

test("principal and sessions preserve the server tenant and session envelopes", async () => {
  const principal = { user_id: "u", tenant_id: "t", membership_id: "m", tenant_role: "owner", platform_role: "user", auth_session_id: "s" };
  const sessions = [{ id: "s", created_at: "2026-10-05", expires_at: "2026-10-06", client: { platform: "ios" } }];
  const { api, calls } = client([jsonResponse(200, { status: "ok", principal }), jsonResponse(200, { status: "ok", sessions })]);
  assert.deepEqual((await api.auth.principal()).principal, principal);
  assert.deepEqual((await api.auth.sessions()).sessions, sessions);
  assert.ok(calls[0]!.url.endsWith("/auth/principal"));
  assert.ok(calls[1]!.url.endsWith("/auth/sessions"));
});

test("session revocation encodes the identifier as one path component", async () => {
  const { api, calls } = client([jsonResponse(200, {})]);
  await api.auth.revokeSession("s/other?private=1");
  assert.ok(calls[0]!.url.endsWith("/auth/sessions/s%2Fother%3Fprivate%3D1"));
  assert.equal(calls[0]!.init.method, "DELETE");
});

test("invalid refresh surfaces 401 without retrying or submitting other work", async () => {
  const { api, calls } = client([jsonResponse(401, { detail: "refresh_session_revoked" })]);
  await assert.rejects(api.auth.refresh("revoked"), (error: unknown) => error instanceof ApiError && error.status === 401);
  assert.equal(calls.length, 1);
});
