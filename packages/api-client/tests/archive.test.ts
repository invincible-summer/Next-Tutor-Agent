/** Archive domain: trash list/detail/restore/purge/empty + retention policy. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, ConflictError } from "../src/errors.ts";
import { createTransport } from "../src/transport.ts";
import { createArchiveClient } from "../src/archive.ts";
import { jsonResponse, noSleep, scriptedFetch } from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

function client(steps: Parameters<typeof scriptedFetch>[0]) {
  const script = scriptedFetch(steps);
  const transport = createTransport({
    baseUrl: BASE,
    fetchImpl: script.fetch,
    tokenProvider: () => "tok",
    sleepImpl: noSleep,
  });
  return { archive: createArchiveClient(transport), calls: script.calls };
}

const ITEM = {
  id: "tr_1",
  resource_type: "workspace",
  original_id: "ws_1",
  title: "数学工作区",
  deleted_at: 1700000000,
  deleted_at_iso: "2023-11-14T22:13:20Z",
  expires_at: null,
  retention_days: 7,
  version: 1,
  metadata: { session_count: 2, has_public_memory: true },
};

test("list sends the resource_type filter only when set", async () => {
  const { archive, calls } = client([
    jsonResponse(200, { status: "ok", items: [ITEM] }),
    jsonResponse(200, { status: "ok", items: [] }),
  ]);
  const listed = await archive.list();
  assert.equal(calls[0]!.url, `${BASE}/trash`);
  assert.equal(calls[0]!.init.method, "GET");
  assert.equal(calls[0]!.init.headers?.Authorization, "Bearer tok");
  assert.equal(listed.status, "ok");
  assert.equal(listed.items[0]!.id, "tr_1");
  assert.equal(listed.items[0]!.metadata.has_public_memory, true);

  const filtered = await archive.list("workspace");
  assert.equal(calls[1]!.url, `${BASE}/trash?resource_type=workspace`);
  assert.equal(filtered.items.length, 0);
});

test("get unwraps the item envelope", async () => {
  const { archive, calls } = client([jsonResponse(200, { status: "ok", item: ITEM })]);
  const item = await archive.get("tr_1");
  assert.equal(calls[0]!.url, `${BASE}/trash/tr_1`);
  assert.equal(item.title, "数学工作区");
  assert.equal(item.resource_type, "workspace");
});

test("restore posts the chosen workspace ids", async () => {
  const { archive, calls } = client([
    jsonResponse(200, {
      status: "restored",
      item_id: "tr_1",
      resource_type: "session",
      original_id: "s_1",
      textbook_id: null,
    }),
  ]);
  const result = await archive.restore("tr_1", ["ws_1", "ws_2"]);
  assert.equal(calls[0]!.url, `${BASE}/trash/tr_1/restore`);
  assert.equal(calls[0]!.init.method, "POST");
  assert.deepEqual(JSON.parse(calls[0]!.init.body as string), {
    workspace_ids: ["ws_1", "ws_2"],
  });
  assert.equal(result.status, "restored");
  assert.equal(result.original_id, "s_1");
});

test("purge and empty issue DELETEs and return the purge report", async () => {
  const { archive, calls } = client([
    jsonResponse(200, {
      status: "purged",
      item_id: "tr_1",
      memory_forget: { sessions: 1 },
      source_attribution_detached: { journal_sessions: 0 },
    }),
    jsonResponse(200, { status: "ok", purged: 2, failed: ["tr_9"] }),
  ]);
  const purged = await archive.purge("tr_1");
  assert.equal(calls[0]!.url, `${BASE}/trash/tr_1`);
  assert.equal(calls[0]!.init.method, "DELETE");
  assert.equal(purged.status, "purged");

  const emptied = await archive.empty();
  assert.equal(calls[1]!.url, `${BASE}/trash`);
  assert.equal(calls[1]!.init.method, "DELETE");
  assert.equal(emptied.purged, 2);
  assert.deepEqual(emptied.failed, ["tr_9"]);
});

test("policy round-trips through GET and PUT", async () => {
  const policy = {
    default_days: 7,
    user_max_days: 30,
    forced_max_days: 30,
    mode: "auto",
    cleanup_interval_seconds: 3600,
    retention_days: 7,
    can_keep_manually: false,
  };
  const { archive, calls } = client([
    jsonResponse(200, policy),
    jsonResponse(200, { ...policy, retention_days: 14 }),
  ]);
  const current = await archive.getPolicy();
  assert.equal(calls[0]!.url, `${BASE}/trash/policy`);
  assert.equal(current.retention_days, 7);

  const updated = await archive.setPolicy(14);
  assert.equal(calls[1]!.url, `${BASE}/trash/policy`);
  assert.equal(calls[1]!.init.method, "PUT");
  assert.deepEqual(JSON.parse(calls[1]!.init.body as string), { retention_days: 14 });
  assert.equal(updated.retention_days, 14);
});

test("missing items surface a 404 ApiError; restore collisions a ConflictError", async () => {
  const { archive } = client([
    jsonResponse(404, { detail: "归档不存在" }),
    jsonResponse(409, { detail: "同名会话已存在于目标工作区" }),
  ]);
  await assert.rejects(
    archive.get("nope"),
    (error: unknown) => error instanceof ApiError && error.status === 404,
  );
  await assert.rejects(
    archive.restore("tr_1"),
    (error: unknown) => error instanceof ConflictError && error.status === 409,
  );
});
