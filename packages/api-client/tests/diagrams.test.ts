/** Diagrams domain: material list/save/delete wire shapes and the assets catalog. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { ConflictError, createApiClient } from "../src/index.ts";
import { jsonResponse, noSleep, scriptedFetch } from "./helpers.ts";

const BASE = "https://api.example.test/api/v1";

function client(steps: Parameters<typeof scriptedFetch>[0]) {
  const script = scriptedFetch(steps);
  return {
    api: createApiClient({ baseUrl: BASE, fetchImpl: script.fetch, sleepImpl: noSleep }),
    calls: script.calls,
  };
}

test("listMaterials encodes scope/filters and skips disabled defaults", async () => {
  const { api, calls } = client([jsonResponse(200, { items: [], total: 0 })]);
  await api.diagrams.listMaterials("private", "力", 2, undefined, { subject: "physics", enabledOnly: true });
  const url = calls[0]!.url;
  assert.ok(url.startsWith(`${BASE}/diagram-materials?`), url);
  assert.ok(url.includes("scope=private"));
  assert.ok(url.includes("q=%E5%8A%9B"));
  assert.ok(url.includes("page=2"));
  assert.ok(url.includes("per=12"));
  assert.ok(url.includes("subject=physics"));
  assert.ok(url.includes("enabled_only=true"));
});

test("saveMaterial PUTs with the id path; revision conflicts surface typed", async () => {
  const { api, calls } = client([
    jsonResponse(200, { id: "m_ab", revision: 3 }),
    jsonResponse(409, { detail: { error: { code: "material_revision_conflict", message: "stale" } } }),
  ]);
  await api.diagrams.saveMaterial(
    {
      title: "斜面",
      description: "",
      subject: "physics",
      aliases: [],
      svg: "<svg/>",
      scope: "private",
      source: "manual",
      enabled: true,
      base_revision: 3,
    },
    "m_ab",
  );
  assert.equal(calls[0]!.init.method, "PUT");
  assert.equal(calls[0]!.url, `${BASE}/diagram-materials/m_ab`);
  await assert.rejects(
    api.diagrams.saveMaterial(
      {
        title: "斜面",
        description: "",
        subject: "physics",
        aliases: [],
        svg: "<svg/>",
        scope: "private",
        source: "manual",
        enabled: true,
        base_revision: 1,
      },
      "m_ab",
    ),
    (error: unknown) =>
      error instanceof ConflictError && error.code === "material_revision_conflict",
  );
});

test("deleteMaterial carries the CAS revision query", async () => {
  const { api, calls } = client([jsonResponse(200, { deleted: true })]);
  await api.diagrams.deleteMaterial("m_ab", 7);
  assert.equal(calls[0]!.init.method, "DELETE");
  assert.equal(calls[0]!.url, `${BASE}/diagram-materials/m_ab?base_revision=7`);
});

test("assets catalog maps the snake_case query and posts typed previews", async () => {
  const { api, calls } = client([
    jsonResponse(200, { items: [{ id: "da_1" }], total: 1, page: 0, per: 12 }),
    jsonResponse(200, { illustration: { kind: "svg", svg: "<svg/>" } }),
  ]);
  await api.diagrams.listAssets({ q: "spring", educationLevel: "high" });
  const listUrl = calls[0]!.url;
  assert.ok(listUrl.includes("q=spring"));
  assert.ok(listUrl.includes("education_level=high"));
  await api.diagrams.previewAsset("da_1", { k: "3" }, "monochrome");
  assert.equal(calls[1]!.init.method, "POST");
  assert.deepEqual(JSON.parse(calls[1]!.init.body as string), {
    params: { k: "3" },
    profile: "monochrome",
  });
});
