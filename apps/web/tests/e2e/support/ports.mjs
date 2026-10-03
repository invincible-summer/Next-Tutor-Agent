/** Shared port selection for the e2e chain (start.sh-style auto fallback).
 *
 * Playwright loads configs (and whatever they import) as CommonJS without
 * top-level await, so this module stays fully synchronous: pickPortSync
 * shells out to ports-cli.mjs (real ESM, runs the async probe) and returns
 * the chosen port.
 *
 * Rules:
 * - An explicit env override (e.g. E2E_BACKEND_PORT) is honored as-is — the
 *   caller pins the port and gets a hard failure when it is occupied.
 * - Otherwise the preferred port is used when free; when occupied the next
 *   free port is picked (like start.sh's 8000 -> 8765 -> 8123 chain), so a
 *   crashed previous run or a parallel session never wedges the suite.
 */
import { execFileSync } from "node:child_process";

const CLI = "tests/e2e/support/ports-cli.mjs";

export function pickPortSync(preferred, envName) {
  if (envName && process.env[envName]) return Number(process.env[envName]);
  const out = execFileSync(process.execPath, [CLI, String(preferred), envName || ""],
                           { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
  return Number(out.trim().split(/\s+/).pop());
}
