/** Voice domain: speech capabilities, multipart transcription, synthesis bytes. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, createApiClient } from "../src/index.ts";
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
  return {
    api: createApiClient({ baseUrl: BASE, fetchImpl: script.fetch, sleepImpl: noSleep }),
    calls: script.calls,
  };
}

test("capabilities projects stt + synthesis without secrets", async () => {
  const { api, calls } = client([
    jsonResponse(200, {
      stt: { available: true, reason: "", formats: ["audio/wav"], languages: ["zh-CN"] },
      synthesis: { available: false, reason: "cloud_tts_not_configured" },
    }),
  ]);
  const capabilities = await api.voice.capabilities();
  assert.equal(capabilities.stt.available, true);
  assert.equal(capabilities.synthesis.reason, "cloud_tts_not_configured");
  assert.equal(calls[0]!.url, `${BASE}/speech/capabilities`);
});

test("transcribe injects the multipart wire fields onto the platform form", async () => {
  const { api, calls } = client([
    jsonResponse(200, { text: "你好", language: "zh-CN", duration_ms: 1500, provider_class: "stub" }),
  ]);
  const form = new RecordingForm();
  const result = await api.voice.transcribe(form, {
    file: { data: { uri: "file://rec.wav", name: "rec.wav", type: "audio/wav" }, filename: "rec.wav" },
    durationMs: 1500,
  });
  assert.deepEqual(
    form.entries.map((entry) => entry.name),
    ["file", "duration_ms", "language"],
  );
  assert.equal(form.entries[1]!.value, "1500");
  assert.equal(form.entries[2]!.value, "zh");
  assert.equal(result.text, "你好");
  const call = calls[0]!;
  assert.equal(call.url, `${BASE}/speech/transcriptions`);
  assert.equal(call.init.method, "POST");
  assert.equal(call.init.body, form);
});

test("synthesis resolves WAV bytes plus the sample-rate/voice headers", async () => {
  const { api, calls } = client([
    binaryResponse(200, "RIFF-audio", {
      "content-type": "audio/wav",
      "x-sample-rate": "24000",
      "x-voice-id": "zh-Xiaoxiao",
    }),
  ]);
  const audio = await api.voice.synthesize({ text: "第一句", speed: 1.2 });
  assert.ok(audio.audio instanceof ArrayBuffer);
  assert.equal(Buffer.from(audio.audio).toString("utf8"), "RIFF-audio");
  assert.equal(audio.sampleRate, 24000);
  assert.equal(audio.voiceId, "zh-Xiaoxiao");
  const call = calls[0]!;
  assert.equal(call.url, `${BASE}/speech/synthesis`);
  assert.deepEqual(JSON.parse(call.init.body as string), {
    text: "第一句",
    language: "zh",
    voice_id: "",
    speed: 1.2,
    policy: "auto",
    allow_local_fallback: true,
  });
});

test("transcription provider errors surface with their stable codes", async () => {
  const { api } = client([jsonResponse(503, { detail: { code: "stt_unavailable" } })]);
  const form = new RecordingForm();
  await assert.rejects(
    api.voice.transcribe(form, { file: { data: "x" }, durationMs: 900 }),
    (error: unknown) =>
      error instanceof ApiError && error.code === "stt_unavailable" && error.status === 503,
  );
});
