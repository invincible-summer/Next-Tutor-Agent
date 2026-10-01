import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
const require = createRequire(import.meta.url);
const cache = new Map();
function load(name) {
  if (cache.has(name)) return cache.get(name);
  if (name === "api") return {};
  const code = ts.transpileModule(readFileSync(`src/lib/assistant/${name}.ts`, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  // 局部变量名避开 `module`（@next/next/no-assign-module-variable）。
  const mod = { exports: {} };
  cache.set(name, mod.exports);
  new Function("require", "module", "exports", code)((path) => path.startsWith("./") ? load(path.slice(2)) : require(path), mod, mod.exports);
  return mod.exports;
}
const { urlMatchesTarget, resolveTargetUrl } = load("routes");
assert.equal(urlMatchesTarget("/", "/"), true);
for (const actual of ["/chat", "/memory", "/home"]) assert.equal(urlMatchesTarget(actual, "/"), false);
assert.equal(urlMatchesTarget("/notes/wrong", "/notes/right"), false);
assert.equal(urlMatchesTarget("/notes/right/extra", "/notes/right"), false);
assert.equal(urlMatchesTarget("/memory", "/memory?concept=c"), false);
assert.equal(urlMatchesTarget("/memory?concept=wrong", "/memory?concept=c"), false);
assert.equal(urlMatchesTarget("/memory?concept=c&ws=w", "/memory?ws=w&concept=c"), true);
assert.equal(urlMatchesTarget("/memory?concept=c&concept=wrong", "/memory?concept=c"), false);
assert.equal(urlMatchesTarget("/docs#wrong", "/docs#right"), false);
assert.equal(urlMatchesTarget("https://wrong.test/", "/"), false);
assert.equal(resolveTargetUrl({ kind: "file", file_id: "a/b", page: 0 }), "/resources/files?file=a%2Fb&page=0");

assert.equal(resolveTargetUrl({ kind: "module", route_id: "account" }), "/account");
assert.equal(resolveTargetUrl({ kind: "module", route_id: "settings" }), "/settings");
assert.equal(resolveTargetUrl({ kind: "profile_section", section: "account" }), "/account");
assert.equal(resolveTargetUrl({ kind: "profile_section", section: "learning" }), "/profile?section=learning");
for (const section of ["voice", "assistant"]) {
  assert.equal(resolveTargetUrl({ kind: "profile_section", section }), `/settings?section=${section}`);
}
assert.equal(resolveTargetUrl({ kind: "settings_section", section: "processing" }), "/settings?section=processing");

const context = load("page-context");
const { runPageCommand } = load("actions");
global.window = { location: new URL("http://assistant.local/") };
const realTimeout = global.setTimeout;
let ticks = 0;
let onTick = () => {};
global.setTimeout = (callback) => realTimeout(() => { ticks++; onTick(); callback(); }, 0);
const ok = { status: "succeeded" };
let readiness = () => ok;
let guard = async () => "allow";
function register() {
  return context.registerPageAdapter({ adapter_id: "test", beforeNavigate: () => guard(), navigationStatus: (target) => readiness(target) });
}
const router = { push: (url) => { window.location = new URL(url, window.location); register(); context.bumpRouteEpoch(); } };
const command = (target) => ({ kind: "navigate", target });
const home = command({ kind: "module", route_id: "home" });
try {
  register();
  assert.deepEqual(await runPageCommand(home, router), ok);
  window.location = new URL("http://assistant.local/memory"); register();
  assert.deepEqual(await runPageCommand(home, router), ok);
  // URL alone never confirms an entity; wait past the previous two second limit.
  let readyAt = ticks + 60;
  readiness = () => ticks >= readyAt ? ok : null;
  const target = command({ kind: "concept", concept_id: "loaded", workspace_id: "w" });
  assert.deepEqual(await runPageCommand(target, router), ok);
  assert.ok(ticks >= readyAt);
  readiness = () => ({ status: "failed", code: "entity_not_found" });
  assert.equal((await runPageCommand(target, router)).code, "entity_not_found");
  readiness = () => null;
  assert.equal((await runPageCommand(target, router)).code, "page_not_ready");
  // A new command invalidates an old command, even when its target later becomes ready.
  readiness = () => null;
  const old = runPageCommand(target, router);
  await new Promise((r) => realTimeout(r, 5));
  readiness = () => ok;
  const newer = runPageCommand(home, router);
  assert.equal((await old).status, "cancelled");
  assert.deepEqual(await newer, ok);
  // Back/forward or manual departure during a slow load cancels the pending receipt.
  readiness = () => null;
  readyAt = ticks + 3;
  onTick = () => { if (ticks === readyAt) window.location = new URL("http://assistant.local/profile"); };
  assert.equal((await runPageCommand(target, router)).status, "cancelled");
  onTick = () => {};
  assert.equal(context.activeAdapter(), null);
  register(); guard = async () => "stay";
  assert.equal((await runPageCommand(home, router)).status, "cancelled");
  guard = async () => { throw new Error("guard failed"); };
  assert.equal((await runPageCommand(home, router)).status, "failed");
  console.log("assistant navigation: URL, readiness, epochs, slow loading, missing targets, cancellation and stale commands passed");
} finally { global.setTimeout = realTimeout; }
