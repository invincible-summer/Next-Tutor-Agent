import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

process.env.NEXT_PUBLIC_DEMO_MODE = "1";
process.env.NEXT_PUBLIC_BASE_PATH = "/Next-Tutor-Agent";
const cache = new Map();
function load(name) {
  if (cache.has(name)) return cache.get(name);
  const code = ts.transpileModule(readFileSync(`src/lib/${name}.ts`, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const mod = { exports: {} };
  cache.set(name, mod.exports);
  new Function("require", "module", "exports", code)((path) => load(path.slice(2)), mod, mod.exports);
  return mod.exports;
}
let token = null;
let reads = 0;
global.localStorage = { getItem: () => token };
global.window = { location: new URL("https://demo.test/Next-Tutor-Agent/"), dispatchEvent: () => {} };
const payloads = {
  "/auth/me": { user: { id: "usr_12e410b4e2", email: "example@example.com" } },
  "/chat/sessions/demo": { messages: [{ role: "user", content: "first" }, { role: "assistant", content: "last" }], message_total: 2 },
  "/learner-evaluation/workspaces": { items: [{ workspace_id: "math" }, { workspace_id: "physics" }], total: 2 },
  "/knowledge/graph": { nodes: [{ id: "chapter", kind: "chapter", level: "本科" }, { id: "section", kind: "section", level: "本科" }, { id: "concept", kind: "concept", level: "本科", name: "定积分" }], edges: [{ from: "concept", to: "section", type: "PART_OF" }, { from: "section", to: "chapter", type: "PART_OF" }] },
};
const responses = Object.fromEntries(Object.keys(payloads).map((path, index) => [path, `responses/${index}.json`]));
const fixtures = Object.fromEntries(Object.entries(responses).map(([path, file]) => [file, payloads[path]]));
global.fetch = async (url) => {
  reads++;
  assert.ok(url.startsWith("/Next-Tutor-Agent/demo/"));
  const name = url.slice("/Next-Tutor-Agent/demo/".length);
  return new Response(JSON.stringify(name === "manifest.json" ? { responses, assets: {}, routes: { runs: [
    { workspaceId: "math", lessonId: "integrals", runId: "old-run" },
    { workspaceId: "math", lessonId: "integrals", runId: "new-run" },
    { workspaceId: "physics", lessonId: "integrals", runId: "other-workspace" },
    { workspaceId: "math", lessonId: "derivatives", runId: "other-lesson" },
  ] } } : fixtures[name]));
};
const { demoFetch, demoLessonRuns } = load("demo-fetch");
const request = (path, init) => demoFetch("/Next-Tutor-Agent/api/v1" + path, init);
assert.equal((await request("/auth/status")).status, 200);
assert.equal((await request("/auth/me")).status, 401);
assert.equal((await request("/auth/login", { method: "POST", body: JSON.stringify({ email: "private@example.com", password: "example" }) })).status, 401);
const login = await (await request("/auth/login", { method: "POST", body: JSON.stringify({ email: "example@example.com", password: "example" }) })).json();
assert.equal(login.user.id, "usr_12e410b4e2");
token = login.token;
const before = reads;
for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
  for (const path of ["/chat/stream", "/notes/notes", "/user/profile", "/workspaces", "/assessment/start"]) {
    await assert.rejects(request(path, { method, body: "{}" }), /Read-only demo/);
  }
}
assert.equal(reads, before, "Writes must not fetch any network resource");
assert.equal((await request("/auth/me?student_id=another-account")).status, 200);
const tail = await (await request("/chat/sessions/demo?tail=1")).json();
assert.deepEqual(tail.messages.map((message) => message.content), ["last"]);
assert.equal(tail.message_total, 2);
const list = await (await request("/learner-evaluation/workspaces?offset=1&limit=1")).json();
assert.equal(list.total, 2);
assert.deepEqual(list.items, [{ workspace_id: "physics" }]);
const overview = await (await request("/knowledge/graph?view=overview&level=本科")).json();
assert.equal(overview.nodes[0].metadata.concept_count, 1);
const chapter = await (await request("/knowledge/graph?view=chapter&chapter_id=chapter")).json();
assert.equal(chapter.nodes.length, 3);
assert.equal((await request("/chat/sessions/not-exported")).status, 404);
assert.deepEqual(await demoLessonRuns("math", "integrals"), ["old-run", "new-run"]);
assert.deepEqual(await demoLessonRuns("missing", "integrals"), []);
let notices = 0, mutations = 0, cancelled = 0;
global.window.dispatchEvent = () => { notices++; };
const event = { preventDefault: () => { cancelled++; }, stopPropagation: () => { cancelled++; } };
const { guardDemoAction } = load("demo");
guardDemoAction(() => { mutations++; })(event);
assert.equal(mutations, 0, "Blocked UI actions must not change local state");
assert.equal(notices, 1);
assert.equal(cancelled, 2);
guardDemoAction(() => { mutations++; }, false)(event);
assert.equal(mutations, 1, "Read actions must remain available");
process.env.NEXT_PUBLIC_DEMO_MODE = "0";
cache.delete("demo");
load("demo").guardDemoAction(() => { mutations++; })(event);
assert.equal(mutations, 2, "Normal deployments retain the original handler");
assert.equal(notices, 1);
console.log("Pages demo: example identity, read queries and mutation/network blocking passed");
