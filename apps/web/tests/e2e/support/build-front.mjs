/** Conditional production build for e2e (runs before `next start`).
 *
 * Next rewrites are baked at BUILD time: the /api proxy target comes from
 * BACKEND_URL (next.config.ts) and the client API base from
 * NEXT_PUBLIC_BACKEND_URL. A production e2e run therefore must not reuse an
 * ambient .next built with different env. This script stamps the e2e build
 * inputs into .next/e2e-build.json and rebuilds only when they change, so a
 * fresh checkout or an env change pays one ~2 min build and every later run
 * starts in seconds.
 */
import { execSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";

const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL;
if (!backendUrl) {
  console.error("[e2e-build] NEXT_PUBLIC_BACKEND_URL is required");
  process.exit(1);
}
const stamp = {
  backendUrl,
  backendProxyUrl: process.env.BACKEND_URL || backendUrl,
  demoMode: process.env.NEXT_PUBLIC_DEMO_MODE || "",
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || "",
};
const stampPath = ".next/e2e-build.json";
const current = existsSync(".next/BUILD_ID") && existsSync(stampPath)
  ? JSON.parse(readFileSync(stampPath, "utf8"))
  : null;

if (current && JSON.stringify(current) === JSON.stringify(stamp)) {
  console.log("[e2e-build] reusing e2e production build for", backendUrl);
} else {
  execSync("pnpm exec next build --webpack", {
    stdio: "inherit",
    env: {
      ...process.env,
      BACKEND_URL: stamp.backendProxyUrl,
      NEXT_PUBLIC_BACKEND_URL: stamp.backendUrl,
    },
  });
  writeFileSync(stampPath, JSON.stringify(stamp, null, 2) + "\n");
}
