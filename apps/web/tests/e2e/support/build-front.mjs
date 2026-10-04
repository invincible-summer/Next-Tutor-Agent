/** Source-aware cache, separate from the developer's .next build. */
import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

export function buildFingerprint(root, env) {
  const hash = createHash("sha256");
  function add(path) {
    const absolute = resolve(root, path);
    hash.update(path + "\0");
    if (!existsSync(absolute)) return;
    const entries = readdirOrFile(absolute);
    if (entries) entries.sort().forEach(name => add(`${path}/${name}`));
    else hash.update(readFileSync(absolute));
    hash.update("\0");
  }
  for (const path of ["src", "public", "scripts", "tests/e2e/support/build-front.mjs",
    "next.config.ts", "tsconfig.json", "tsconfig.e2e.json", "tsconfig.classroom.json",
    "postcss.config.mjs", "package.json",
    // Workspace-level build inputs: the shared packages and the single root
    // lockfile are compiled into the web bundle (transpilePackages).
    "../../packages", "../../pnpm-lock.yaml", "../../tsconfig.base.json"]) add(path);
  // Next reads these even when backend dotenv loading is disabled. Hash only;
  // never put their contents or environment values in the diagnostic stamp.
  readdirSync(root).filter(name => /^\.env($|\.)/.test(name)).sort().forEach(add);
  const inputs = Object.fromEntries(Object.entries(env)
    .filter(([key]) => key.startsWith("NEXT_PUBLIC_") || key === "BACKEND_URL")
    .sort(([a], [b]) => a.localeCompare(b)));
  hash.update(JSON.stringify({ inputs, node: process.version, mode: "production-e2e-v1" }));
  return hash.digest("hex");
}

function readdirOrFile(path) {
  try { return readdirSync(path); } catch (error) {
    if (error.code === "ENOTDIR") return null;
    throw error;
  }
}

export async function buildFront(run, env, root = resolve(fileURLToPath(new URL("../../../", import.meta.url)))) {
  const stampPath = resolve(root, ".next-e2e/e2e-build.json");
  const fingerprint = buildFingerprint(root, env);
  let current;
  try { current = JSON.parse(readFileSync(stampPath, "utf8")); } catch { /* rebuild */ }
  if (existsSync(resolve(root, ".next-e2e/BUILD_ID")) && current?.fingerprint === fingerprint) {
    console.log("[e2e-build] cache hit (source and build environment unchanged)");
    await run("pnpm", ["build:classroom"], 60_000);
    return;
  }
  rmSync(stampPath, { force: true });
  await run("pnpm", ["build:classroom"], 60_000);
  await run("pnpm", ["exec", "next", "build", "--webpack"], 240_000);
  // Failed/interrupted builds never publish a valid stamp.
  writeFileSync(stampPath, JSON.stringify({ fingerprint }) + "\n");
}
