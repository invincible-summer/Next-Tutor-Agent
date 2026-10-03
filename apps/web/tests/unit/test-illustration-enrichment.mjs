import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

function load(file, dependencies, globals = {}) {
  const code = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const mod = { exports: {} };
  new Function("require", "module", "exports", ...Object.keys(globals), code)(
    (name) => {
      assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
      return dependencies[name];
    }, mod, mod.exports, ...Object.values(globals),
  );
  return mod.exports;
}

const timeouts = [];
const fakeAbort = { timeout: (milliseconds) => {
  timeouts.push(milliseconds);
  return new AbortController().signal;
} };
let fetchResponse;
const api = load("src/lib/api-illustrations.ts", {
  "./api": { API_BASE: "http://illustration.test/api/v1" },
  "./api-fetch": { apiFetch: (...args) => fetchResponse(...args) },
}, { AbortSignal: fakeAbort });

for (const [status, payload, code, retryable] of [
  [404, { detail: { error: { code: "assessment_question_not_current", retryable: false } } }, "assessment_question_not_current", false],
  [503, { error: { code: "policy_disabled", retryable: false } }, "policy_disabled", false],
  [429, {}, "status_429", true],
]) {
  fetchResponse = async () => new Response(JSON.stringify(payload), { status });
  await assert.rejects(api.startIllustration("q_api", 1), error => {
    assert.ok(error instanceof api.IllustrationRequestError);
    assert.equal(error.message, code);
    assert.equal(error.retryable, retryable);
    return true;
  });
}
fetchResponse = async () => { throw new DOMException("Timed out", "TimeoutError"); };
await assert.rejects(api.startIllustration("q_api", 1), error => {
  assert.equal(error.message, "run_interrupted");
  assert.equal(error.retryable, true);
  return true;
});
assert.ok(timeouts.every(milliseconds => milliseconds === 120_000));

const picture = { kind: "svg", svg: "<svg></svg>", alt: "合成测试图", width: 640, height: 400 };
const question = (id = "q_test", illustration) => ({ question_id: id, question_revision: 1, illustration });
const result = (id, status, extra = {}) => ({ question_id: id, question_revision: 1, status, ...extra });
const ready = (id, jobId = "job_ready") => result(id, "ready", { job_id: jobId, illustration: picture });
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function settle() {
  for (let tick = 0; tick < 12; tick++) await Promise.resolve();
}

function harness(handlers) {
  let owner = "owner_a";
  let now = 0;
  const effects = [];
  const timers = [];
  const calls = { start: [], read: [], retry: [], frozen: [], timeouts: [] };
  const auth = selector => selector(auth.getState());
  auth.getState = () => ({ user: { id: owner } });
  const dependencies = {
    react: { useEffect: effect => effects.push(effect), useSyncExternalStore: (_subscribe, snapshot) => snapshot() },
    "@/lib/demo": { DEMO_MODE: false },
    "@/lib/auth-store": { useAuthStore: auth },
    "@/lib/api-illustrations": { IllustrationRequestError: api.IllustrationRequestError },
  };
  for (const [method, exportName] of [
    ["start", "startIllustration"], ["read", "getIllustrationJob"],
    ["retry", "retryIllustrationJob"], ["frozen", "getFrozenIllustration"],
  ]) {
    dependencies["@/lib/api-illustrations"][exportName] = (...args) => {
      calls[method].push(args);
      assert.ok(handlers[method], `Unexpected ${method} request`);
      return handlers[method](...args);
    };
  }
  const { useIllustrationEnrichment: hook } = load("src/components/pages/assessment/useIllustrationEnrichment.ts", dependencies, {
    setTimeout: callback => timers.push(callback), Date: { now: () => now },
    AbortSignal: { timeout: milliseconds => { calls.timeouts.push(milliseconds); return new AbortController().signal; } },
  });
  return {
    calls,
    owner: value => { owner = value; },
    mount: input => {
      const snapshot = hook(input);
      for (const effect of effects.splice(0)) effect();
      return snapshot;
    },
    advance: async milliseconds => {
      now += milliseconds;
      for (const callback of timers.splice(0)) callback();
      await settle();
    },
  };
}

// Card/feedback remounts share one failure; a server denial cannot be retried.
{
  const flight = deferred();
  const h = harness({ start: () => flight.promise });
  assert.equal(h.mount(question()).state, "generating");
  assert.equal(h.mount(question()).state, "generating");
  assert.equal(h.calls.start.length, 1);
  flight.reject(new api.IllustrationRequestError("assessment_question_not_current", false));
  await settle();
  const failed = h.mount(question());
  assert.equal(failed.state, "failed");
  assert.equal(failed.retryable, false);
  failed.retry();
  h.mount(question());
  assert.equal(h.calls.start.length, 1);
}

// A plain provider/transport failure remains explicit and retryable.
{
  let attempts = 0;
  const h = harness({ start: async id => {
    if (++attempts === 1) throw new Error("private transport diagnostics");
    return ready(id);
  } });
  h.mount(question());
  await settle();
  const failed = h.mount(question());
  assert.equal(failed.failureCode, "provider_unavailable");
  assert.equal(failed.retryable, true);
  assert.equal(h.calls.start.length, 1);
  failed.retry();
  failed.retry();
  await settle();
  assert.equal(h.mount(question()).state, "ready");
  assert.equal(h.calls.start.length, 2);
}

// An interrupted poll may already have a frozen artifact when retried.
{
  let reads = 0;
  const h = harness({
    start: async id => result(id, "queued", { job_id: "job_recover" }),
    read: async () => {
      if (++reads === 1) throw new Error("connection lost");
      return ready("q_test", "job_recover");
    },
  });
  h.mount(question());
  await settle();
  await h.advance(1000);
  const failed = h.mount(question());
  assert.equal(failed.state, "failed");
  failed.retry();
  await settle();
  assert.equal(h.mount(question()).state, "ready");
  assert.equal(h.calls.start.length, 1);
  assert.equal(h.calls.retry.length, 0);
}

// Only a retryable server failure creates another job, then polling continues.
{
  let reads = 0;
  const h = harness({
    start: async id => result(id, "queued", { job_id: "job_failed" }),
    read: async () => {
      if (++reads === 1) throw new Error("connection lost");
      if (reads === 2) return result("q_test", "failed", {
        job_id: "job_failed", failure: { code: "provider_unavailable", retryable: true },
      });
      return ready("q_test", "job_new");
    },
    retry: async () => result("q_test", "running", { job_id: "job_new" }),
  });
  h.mount(question());
  await settle();
  await h.advance(1000);
  h.mount(question()).retry();
  await settle();
  await h.advance(1000);
  assert.equal(h.mount(question()).state, "ready");
  assert.deepEqual(h.calls.retry.map(([jobId]) => jobId), ["job_failed"]);
}

// A late server denial is respected even when the preceding network error allowed retry.
{
  let reads = 0;
  const h = harness({
    start: async id => result(id, "queued", { job_id: "job_denied" }),
    read: async () => {
      if (++reads === 1) throw new Error("connection lost");
      return result("q_test", "failed", {
        job_id: "job_denied", failure: { code: "question_material_incomplete", retryable: false },
      });
    },
  });
  h.mount(question());
  await settle();
  await h.advance(1000);
  h.mount(question()).retry();
  await settle();
  assert.equal(h.mount(question()).retryable, false);
  assert.equal(h.calls.retry.length, 0);
}

// A concurrent successful job wins over a retry of an older failed job.
{
  let reads = 0;
  const h = harness({
    start: async id => result(id, "queued", { job_id: "job_old" }),
    read: async () => {
      if (++reads === 1) throw new Error("connection lost");
      return result("q_test", "failed", { job_id: "job_old", retryable: true });
    },
    retry: async () => { throw new api.IllustrationRequestError("illustration_frozen", false); },
    frozen: async id => ready(id, "job_new"),
  });
  h.mount(question());
  await settle();
  await h.advance(1000);
  h.mount(question()).retry();
  await settle();
  assert.equal(h.mount(question()).state, "ready");
  assert.equal(h.calls.frozen.length, 1);
}

// A superseded flight cannot overwrite a newer ready state with a late failure.
{
  const oldFlight = deferred();
  const newFlight = deferred();
  let attempts = 0;
  const h = harness({ start: () => ++attempts === 1 ? oldFlight.promise : newFlight.promise });
  h.mount(question());
  assert.equal(h.mount(question("q_test", picture)).state, "ready");
  h.mount(question());
  newFlight.resolve(ready("q_test", "job_new"));
  await settle();
  oldFlight.reject(new api.IllustrationRequestError("provider_unavailable", true));
  await settle();
  assert.equal(h.mount(question()).state, "ready");
  assert.equal(h.calls.start.length, 2);
}

// Switching accounts invalidates the old flight and permits a fresh read on return.
{
  const oldFlight = deferred();
  let attempts = 0;
  const h = harness({ start: async id => ++attempts === 1 ? oldFlight.promise : ready(id) });
  h.mount(question());
  h.owner("owner_b");
  h.mount(question());
  oldFlight.resolve(ready("q_test", "job_old_owner"));
  await settle();
  assert.equal(h.mount(question()).state, "ready");
  h.owner("owner_a");
  h.mount(question());
  await settle();
  assert.equal(h.mount(question()).state, "ready");
  assert.equal(h.calls.start.length, 3);
}

// Cache pressure cannot evict an active request and start a duplicate flight.
{
  const pending = deferred();
  const h = harness({ start: id => id === "q_pending" ? pending.promise : Promise.resolve(ready(id)) });
  h.mount(question("q_pending"));
  for (let index = 0; index < 105; index++) {
    h.mount(question(`q_completed_${index}`));
    await settle();
  }
  h.mount(question("q_pending"));
  assert.equal(h.calls.start.filter(([id]) => id === "q_pending").length, 1);
  pending.resolve(ready("q_pending"));
  await settle();
  assert.equal(h.mount(question("q_pending")).state, "ready");
}

// V2 remains observable beyond 90 seconds and stops at the 150-second boundary.
{
  const h = harness({
    start: async id => result(id, "queued", { job_id: "job_slow" }),
    read: async () => result("q_test", "running", { job_id: "job_slow" }),
  });
  h.mount(question());
  await settle();
  await h.advance(91_000);
  assert.equal(h.mount(question()).state, "generating");
  assert.deepEqual(h.calls.timeouts, [120_000, 30_000]);
  await h.advance(59_000);
  const timedOut = h.mount(question());
  assert.equal(timedOut.state, "failed");
  assert.equal(timedOut.failureCode, "run_interrupted");
  assert.equal(timedOut.retryable, true);
  assert.equal(h.calls.read.length, 1);
}

console.log("illustration enrichment: request deadlines, retry permissions, shared flights, frozen recovery, account changes and stale results passed");
