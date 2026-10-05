/** Capabilities probe: dotted-key product capability aggregation. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { UnauthorizedError, createApiClient } from "../src/index.ts";
import { jsonResponse, noSleep, scriptedFetch } from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

test("get returns the dotted-key capability map verbatim", async () => {
  const body = {
    chat: { available: true, reason: "" },
    upload: { available: true, reason: "" },
    classroom: { available: false, reason: "classroom_disabled" },
    cloud_stt: { available: true, reason: "" },
    cloud_tts: { available: false, reason: "cloud_tts_not_configured" },
    assistant: { available: true, reason: "" },
    "illustration.quiz": { available: true, reason: "" },
    "illustration.scenario": { available: true, reason: "" },
    "illustration.v3": { available: false, reason: "model_not_configured" },
    "diagram.materials": { available: true, reason: "" },
  };
  const script = scriptedFetch([jsonResponse(200, body)]);
  const api = createApiClient({ baseUrl: BASE, fetchImpl: script.fetch, sleepImpl: noSleep });
  const capabilities = await api.capabilities.get();
  assert.equal(capabilities.chat.available, true);
  assert.equal(capabilities.classroom.reason, "classroom_disabled");
  assert.equal(capabilities["illustration.v3"].available, false);
  assert.equal(capabilities["diagram.materials"].available, true);
  assert.equal(script.calls[0]!.url, `${BASE}/capabilities`);
});

test("errors surface as typed 401 after the (absent) refresh attempt", async () => {
  const script = scriptedFetch([
    jsonResponse(401, { detail: { error: { code: "authentication_required", message: "login required" } } }),
  ]);
  const api = createApiClient({ baseUrl: BASE, fetchImpl: script.fetch, sleepImpl: noSleep });
  await assert.rejects(
    api.capabilities.get(),
    (error: unknown) => error instanceof UnauthorizedError && error.message === "login required",
  );
});
