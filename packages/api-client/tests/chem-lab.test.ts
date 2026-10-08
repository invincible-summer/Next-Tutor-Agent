/** Chem-lab client: typed domain errors, resend-safe command semantics. */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ChemLabApiError,
  NetworkError,
  createApiClient,
} from "../src/index.ts";
import { jsonResponse, noSleep, scriptedFetch, hangingFetch } from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

test("catalog GET hits the chemistry prefix", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(200, { experiments: [], capabilities: {} }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  const catalog = await client.tools.chemLab.listCatalog();
  assert.deepEqual(catalog.experiments, []);
  assert.match(calls[0]!.url, /\/tools\/lab\/chemistry\/catalog$/);
});

test("getEnginePack hits the engine-pack route with optional version", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(200, { pack_hash: "abc", pack: {} }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  const pack = await client.tools.chemLab.getEnginePack("chem.dilution", "v1");
  assert.equal(pack.pack_hash, "abc");
  assert.match(
    calls[0]!.url,
    /\/tools\/lab\/chemistry\/experiments\/chem\.dilution\/engine-pack\?version=v1$/,
  );
});

test("createSession posts the pinned experiment/mode payload", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(200, { session_id: "clab_1", revision: 0 }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  const snap = await client.tools.chemLab.createSession({
    experiment_id: "chem.dilution",
    mode: "guided",
    language: "zh",
    session_seed: 0,
  });
  assert.equal(snap.session_id, "clab_1");
  assert.equal(calls[0]!.init?.method, "POST");
  const body = JSON.parse(String(calls[0]!.init?.body));
  assert.equal(body.experiment_id, "chem.dilution");
  assert.equal(body.owner, undefined);
});

test("postCommand sends command_id/base_revision/pack_hash verbatim", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(200, { accepted: true, command_id: "cmd-1", revision: 1 }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  const ack = await client.tools.chemLab.postCommand("clab_1", {
    command_id: "cmd-1",
    client_seq: 1,
    base_revision: 0,
    pack_hash: "abc123",
    command: { kind: "pour", source_id: "a", target_id: "b", amount_uL: 5000 },
  });
  assert.equal(ack.accepted, true);
  const body = JSON.parse(String(calls[0]!.init?.body));
  assert.equal(body.base_revision, 0);
  assert.equal(body.pack_hash, "abc123");
  assert.equal(body.command.kind, "pour");
});

test("revision conflict surfaces as typed ChemLabApiError", async () => {
  const { fetch } = scriptedFetch([
    jsonResponse(409, { detail: { code: "chem_lab_revision_conflict", message: "stale" } }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  await assert.rejects(
    client.tools.chemLab.postCommand("clab_1", {
      command_id: "cmd-2",
      client_seq: 2,
      base_revision: 0,
      pack_hash: "abc123",
      command: { kind: "wait", duration_ms: 500 },
    }),
    (error: unknown) =>
      error instanceof ChemLabApiError
      && error.code === "chem_lab_revision_conflict"
      && error.status === 409,
  );
});

test("session missing surfaces as typed ChemLabApiError 404", async () => {
  const { fetch } = scriptedFetch([
    jsonResponse(404, { detail: { code: "chem_lab_session_missing", message: "gone" } }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  await assert.rejects(
    client.tools.chemLab.getSession("clab_gone"),
    (error: unknown) =>
      error instanceof ChemLabApiError
      && error.code === "chem_lab_session_missing"
      && error.status === 404,
  );
});

test("hung call surfaces via the baked-in timeout, not an eternal spinner", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const client = createApiClient({ baseUrl: BASE, fetchImpl: hangingFetch(), sleepImpl: noSleep });
  const pending = client.tools.chemLab.createSession({
    experiment_id: "chem.dilution",
    mode: "guided",
    language: "zh",
    session_seed: 0,
  });
  const expects = assert.rejects(
    pending,
    (error: unknown) => error instanceof NetworkError && !error.retryable,
  );
  // 让 fetch 先发出并挂上 abort 监听，再推进到客户端内建超时。
  await new Promise<void>((resolve) => setImmediate(resolve));
  t.mock.timers.tick(30_000);
  await expects;
});

test("getEvents encodes after_seq/limit query", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(200, { items: [], next_after_seq: 41, truncated: false }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  const page = await client.tools.chemLab.getEvents("clab_1", 40, 100);
  assert.equal(page.next_after_seq, 41);
  assert.match(calls[0]!.url, /\/events\?after_seq=40&limit=100$/);
});

test("checkpoint/fork/reset/finish hit their routes", async () => {
  const { fetch, calls } = scriptedFetch([
    jsonResponse(200, { accepted: true }),
    jsonResponse(200, { session: { session_id: "clab_2" }, source_session_id: "clab_1", fork_revision: 3 }),
    jsonResponse(200, { session: { session_id: "clab_3" }, source_session_id: "clab_1", fork_revision: 0 }),
    jsonResponse(200, { session_id: "clab_1", goals: [] }),
  ]);
  const client = createApiClient({ baseUrl: BASE, fetchImpl: fetch, sleepImpl: noSleep });
  await client.tools.chemLab.createCheckpoint("clab_1", "中点");
  await client.tools.chemLab.fork("clab_1", { checkpoint_id: "cp_1" });
  await client.tools.chemLab.reset("clab_1", {});
  await client.tools.chemLab.finish("clab_1");
  assert.match(calls[0]!.url, /\/sessions\/clab_1\/checkpoints$/);
  assert.match(calls[1]!.url, /\/sessions\/clab_1\/fork$/);
  assert.match(calls[2]!.url, /\/sessions\/clab_1\/reset$/);
  assert.match(calls[3]!.url, /\/sessions\/clab_1\/finish$/);
  const cpBody = JSON.parse(String(calls[0]!.init?.body));
  assert.equal(cpBody.label, "中点");
});
