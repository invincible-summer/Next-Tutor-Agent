/** Profile domain: profile read/update and the avatar multipart lifecycle. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError } from "../src/errors.ts";
import { createProfileClient } from "../src/profile.ts";
import { createTransport } from "../src/transport.ts";
import {
  RecordingForm,
  binaryResponse,
  jsonResponse,
  noSleep,
  scriptedFetch,
} from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

function client(steps: Parameters<typeof scriptedFetch>[0]) {
  const script = scriptedFetch(steps);
  const transport = createTransport({
    baseUrl: BASE,
    fetchImpl: script.fetch,
    tokenProvider: () => "tok",
    sleepImpl: noSleep,
  });
  return { profile: createProfileClient(transport), calls: script.calls };
}

test("get returns the profile envelope with the quiz_svg flag", async () => {
  const { profile, calls } = client([
    jsonResponse(200, {
      status: "ok",
      quiz_svg_available: true,
      profile: { name: "小李", grade: "本科", school: "", subjects: ["数学"], avatar: "avatar:r1" },
    }),
  ]);
  const result = await profile.get();
  assert.equal(result.status, "ok");
  assert.equal(result.quiz_svg_available, true);
  assert.equal(result.profile.avatar, "avatar:r1");
  const call = calls[0]!;
  assert.equal(call.url, `${BASE}/user/profile`);
  assert.equal(call.init.method ?? "GET", "GET");
  assert.equal(call.init.headers?.Authorization, "Bearer tok");
});

test("update sends a PUT with the partial profile JSON and bearer auth", async () => {
  const { profile, calls } = client([
    jsonResponse(200, {
      status: "ok",
      profile: { name: "小李", grade: "本科", school: "", subjects: ["数学"], avatar: "" },
    }),
  ]);
  const result = await profile.update({
    name: "小李",
    grade: "本科",
    prefs: { tts_speed: 0.9 },
  });
  assert.equal(result.profile.name, "小李");
  const call = calls[0]!;
  assert.equal(call.url, `${BASE}/user/profile`);
  assert.equal(call.init.method, "PUT");
  assert.equal(call.init.headers?.["Content-Type"], "application/json");
  assert.equal(call.init.headers?.Authorization, "Bearer tok");
  assert.deepEqual(JSON.parse(call.init.body as string), {
    name: "小李",
    grade: "本科",
    prefs: { tts_speed: 0.9 },
  });
});

test("uploadAvatar passes the caller-built multipart form through untouched", async () => {
  const { profile, calls } = client([
    jsonResponse(200, {
      status: "ok",
      profile: { name: "A", grade: "", school: "", subjects: [], avatar: "avatar:r2" },
    }),
  ]);
  const form = new RecordingForm();
  form.append("file", { uri: "file://crop.png" }, "avatar.png");
  const result = await profile.uploadAvatar(form);
  assert.equal(result.profile.avatar, "avatar:r2");
  const call = calls[0]!;
  assert.equal(call.url, `${BASE}/user/avatar`);
  assert.equal(call.init.method, "PUT");
  assert.equal(call.init.body, form);
  // The multipart boundary belongs to the platform fetch; the client must
  // neither set Content-Type nor inject form fields.
  assert.equal(call.init.headers?.["Content-Type"], undefined);
  assert.equal(form.entries.length, 1);
});

test("deleteAvatar issues DELETE /user/avatar and returns the refreshed profile", async () => {
  const { profile, calls } = client([
    jsonResponse(200, {
      status: "ok",
      profile: { name: "A", grade: "", school: "", subjects: [], avatar: "" },
    }),
  ]);
  const result = await profile.deleteAvatar();
  assert.equal(result.profile.avatar, "");
  assert.equal(calls[0]!.url, `${BASE}/user/avatar`);
  assert.equal(calls[0]!.init.method, "DELETE");
});

test("avatar resolves PNG bytes with the version cache-buster query", async () => {
  const { profile, calls } = client([
    binaryResponse(200, "png-bytes", { "content-type": "image/png" }),
  ]);
  const data = await profile.avatar("avatar:r2");
  assert.equal(Buffer.from(data).toString("utf8"), "png-bytes");
  assert.equal(
    calls[0]!.url,
    `${BASE}/user/avatar?version=${encodeURIComponent("avatar:r2")}`,
  );
});

test("server validation failures surface as ApiError with the server code", async () => {
  const { profile } = client([jsonResponse(422, { detail: "invalid_grade" })]);
  await assert.rejects(
    profile.update({ grade: "学前班" }),
    (error: unknown) =>
      error instanceof ApiError && error.status === 422 && error.code === "invalid_grade",
  );
});

test("avatar upload rejection (too large) surfaces the stable code", async () => {
  const { profile } = client([jsonResponse(413, { detail: "avatar_too_large" })]);
  await assert.rejects(
    profile.uploadAvatar(new RecordingForm()),
    (error: unknown) =>
      error instanceof ApiError && error.status === 413 && error.code === "avatar_too_large",
  );
});
