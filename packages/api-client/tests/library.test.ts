/** Library domain: folders/upload, textbook registry fields, byte downloads. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/index.ts";
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

test("upload posts the form with the folder_id query", async () => {
  const { api, calls } = client([
    jsonResponse(200, { results: [{ id: "f_1", filename: "a.pdf", folder_id: "d_2", char_count: 1, chunk_count: 1 }] }),
  ]);
  const form = new RecordingForm();
  form.append("files", { uri: "file://a.pdf" });
  await api.library.upload(form, "d_2");
  const call = calls[0]!;
  assert.equal(call.url, `${BASE}/library/upload?folder_id=d_2`);
  assert.equal(call.init.method, "POST");
  assert.equal(call.init.body, form);
});

test("file downloads resolve bytes via the shared transport", async () => {
  const { api, calls } = client([binaryResponse(200, "%PDF-1.7")]);
  const data = await api.library.downloadFile("f_1");
  assert.ok(data instanceof ArrayBuffer);
  assert.equal(Buffer.from(data).toString("utf8"), "%PDF-1.7");
  assert.equal(calls[0]!.url, `${BASE}/library/files/f_1/download`);
});

test("textbook upload injects scalar form fields, not just files", async () => {
  const { api, calls } = client([
    jsonResponse(200, { results: [{ filename: "math.pdf", status: "building", group_id: "tb_9", id: "tb_9" }] }),
  ]);
  const form = new RecordingForm();
  form.append("files", { uri: "file://math.pdf" });
  await api.library.textbooks.upload(form, {
    level: "高中",
    subject: "数学",
    defaultMaxChapters: 30,
    volumeOverrides: { f_1: { max_concepts: 200 } },
  });
  const byName = Object.fromEntries(form.entries.map((entry) => [entry.name, entry.value]));
  assert.equal(byName.level, "高中");
  assert.equal(byName.scope, "private");
  assert.equal(byName.subject, "数学");
  assert.equal(byName.default_max_chapters, "30");
  assert.equal(byName.volume_overrides, '{"f_1":{"max_concepts":200}}');
  assert.equal(calls[0]!.url, `${BASE}/textbooks/upload`);
});

test("rebuild/archive hit the registry mutation endpoints", async () => {
  const { api, calls } = client([
    jsonResponse(200, { textbook_id: "tb_1", status: "building", mode: "rag_graph" }),
    jsonResponse(200, { status: "archived", textbook_id: "tb_1" }),
  ]);
  await api.library.textbooks.rebuildGraph("tb_1", "full_ocr");
  await api.library.textbooks.archive("tb_1");
  assert.deepEqual(JSON.parse(calls[0]!.init.body as string), { mode: "full_ocr" });
  assert.equal(calls[0]!.url, `${BASE}/textbooks/tb_1/rebuild_graph`);
  assert.equal(calls[1]!.init.method, "DELETE");
  assert.equal(calls[1]!.url, `${BASE}/textbooks/tb_1`);
});
