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
import { resolve } from "node:path";

// Playwright transpiles config dependencies to CommonJS, so keep import.meta
// out of this shared module. Both runners execute from apps/web.
const CLI = resolve("tests/e2e/support/ports-cli.mjs");

export function pickPortSync(preferred, envName, checkOverride = false) {
  const override = envName && process.env[envName];
  if (override) {
    if (!/^\d+$/.test(override) || Number(override) < 1 || Number(override) > 65535) {
      throw new Error(`${envName} must be a port from 1 to 65535`);
    }
    // Config reloads use the pinned (now listening) port; the runner checks
    // availability once, before starting any build or service.
    if (!checkOverride) return Number(override);
  }
  const out = execFileSync(process.execPath, [CLI, String(preferred), envName || ""],
                           { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
  return Number(out.trim().split(/\s+/).pop());
}
