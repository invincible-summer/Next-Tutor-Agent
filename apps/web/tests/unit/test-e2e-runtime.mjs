import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { createServer } from "node:net";
import { test } from "node:test";
import { buildFingerprint, buildFront } from "../e2e/support/build-front.mjs";
import { pickPortSync } from "../e2e/support/ports.mjs";

function sandbox(t) {
  const root = mkdtempSync(resolve(tmpdir(), "e2e-cache-test-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const put = (path, contents) => {
    mkdirSync(resolve(root, path, ".."), { recursive: true });
    writeFileSync(resolve(root, path), contents);
  };
  return { root, put };
}

test("build cache invalidates changed source/assets/config/dependencies/environment, even at the same URL", t => {
  const { root, put } = sandbox(t);
  const env = { BACKEND_URL: "http://127.0.0.1:8124", NEXT_PUBLIC_DEMO_MODE: "0" };
  for (const path of ["src/app.ts", "public/icon.svg", "next.config.ts", "tsconfig.json",
    "scripts/build.mjs", "package.json", "pnpm-lock.yaml", ".env.local"]) put(path, "original");
  const initial = buildFingerprint(root, env);
  assert.equal(buildFingerprint(root, { ...env, E2E_RUN_ROOT: "/tmp/a-new-run" }), initial);
  put("tests/e2e/example.spec.ts", "changed test scope");
  assert.equal(buildFingerprint(root, env), initial);
  for (const path of ["src/app.ts", "public/icon.svg", "next.config.ts", "tsconfig.json",
    "scripts/build.mjs", "package.json", "pnpm-lock.yaml", ".env.local"]) {
    put(path, "changed"); assert.notEqual(buildFingerprint(root, env), initial, path);
    put(path, "original");
  }
  assert.notEqual(buildFingerprint(root, { ...env, BACKEND_URL: "http://127.0.0.1:9000" }), initial);
});

test("unchanged builds hit cache; failed/corrupt builds never reuse a successful marker", async t => {
  const { root, put } = sandbox(t);
  const env = { BACKEND_URL: "http://127.0.0.1:8124" };
  put("src/app.ts", "first");
  let builds = 0;
  const run = async (_command, args) => {
    if (args.includes("next")) { builds++; put(".next-e2e/BUILD_ID", "synthetic-build"); }
  };
  await buildFront(run, env, root); await buildFront(run, env, root);
  assert.equal(builds, 1);
  put("src/app.ts", "second");
  await assert.rejects(buildFront(async (_command, args) => {
    if (args.includes("next")) throw new Error("compile failed");
  }, env, root), /compile failed/);
  assert.throws(() => readFileSync(resolve(root, ".next-e2e/e2e-build.json")), { code: "ENOENT" });
  await buildFront(run, env, root); assert.equal(builds, 2);
  put(".next-e2e/e2e-build.json", "invalid JSON");
  await buildFront(run, env, root); assert.equal(builds, 3);
});

test("ports reject invalid/occupied overrides and automatically fall back without changing pinned ports", async t => {
  const server = createServer();
  await new Promise(resolveListen => server.listen(0, "127.0.0.1", resolveListen));
  t.after(() => server.close());
  const port = server.address().port;
  const previous = process.env.E2E_TEST_PORT;
  t.after(() => {
    if (previous === undefined) delete process.env.E2E_TEST_PORT;
    else process.env.E2E_TEST_PORT = previous;
  });
  process.env.E2E_TEST_PORT = "NaN";
  assert.throws(() => pickPortSync(port, "E2E_TEST_PORT"), /must be a port/);
  process.env.E2E_TEST_PORT = String(port);
  assert.equal(pickPortSync(port, "E2E_TEST_PORT"), port);
  assert.throws(() => pickPortSync(port, "E2E_TEST_PORT", true));
  delete process.env.E2E_TEST_PORT;
  assert.ok(pickPortSync(port, "E2E_TEST_PORT") > port);
});
