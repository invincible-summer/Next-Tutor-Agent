/**
 * E2E backend isolation: rsync the repo into a scratch dir
 * so the real uvicorn process never writes business data (chat_history/,
 * students/, knowledge/) into the working tree. The backend derives its
 * storage roots from module constants, so an isolated copy is the clean way
 * to keep E2E state out of the versioned repo.
 *
 * Reuses the copy across runs unless E2E_FRESH=1 (full resync of backend/
 * sources only — fixtures under the copy are per-run state).
 */
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, rmSync, mkdirSync, copyFileSync, symlinkSync } from "node:fs";
import { parse, resolve, sep } from "node:path";

const REPO = resolve(import.meta.dirname, "../../../../..");
const DEST = resolve(process.env.E2E_BACKEND_HOME || "/tmp/edu-agent-e2e");
if (DEST === parse(DEST).root || DEST === REPO ||
    REPO.startsWith(DEST + sep) || DEST.startsWith(REPO + sep)) {
  throw new Error("E2E_BACKEND_HOME must be a separate scratch directory outside the repository");
}
const backendSrc = resolve(DEST, "services", "api");

if (process.env.E2E_FRESH === "1" && existsSync(DEST)) {
  rmSync(DEST, { recursive: true, force: true });
}
mkdirSync(DEST, { recursive: true });
// rsync's receiver only mkdirs the final path component, so the nested
// services/api destination must exist beforehand.
mkdirSync(backendSrc, { recursive: true });

// Backend sources only; runtime data goes to <DEST>/data via
// NEXT_TUTOR_DATA_DIR (single runtime root owned by app/core/paths.py).
execFileSync("rsync", ["-a", "--delete", "--exclude=__pycache__",
  "--exclude=.venv*", "--exclude=.env*", "--exclude=traces",
  "--exclude=uploads", "services/api/", `${backendSrc}/`],
  { stdio: "inherit", cwd: REPO });
// .env must NOT leak into the E2E copy
mkdirSync(resolve(DEST, "data"), { recursive: true });
// Offline SVG preview is part of the backend contract. Keep its browser
// script in the same architecture-relative location in the scratch checkout.
const webScratch = resolve(DEST, "apps/web");
mkdirSync(resolve(webScratch, "scripts"), { recursive: true });
copyFileSync(resolve(REPO, "apps/web/scripts/render-illustration.mjs"),
  resolve(webScratch, "scripts/render-illustration.mjs"));
if (!existsSync(resolve(webScratch, "node_modules"))) {
  symlinkSync(resolve(REPO, "apps/web/node_modules"), resolve(webScratch, "node_modules"), "dir");
}

// sanity: the copy has the app package
if (!existsSync(resolve(backendSrc, "app/main.py"))) {
  console.error("[prepare-backend] rsync failed: app/main.py missing");
  process.exit(1);
}
const py = process.env.E2E_PYTHON || "python3";
const probe = spawnSync(py, ["-c", "import fastapi, uvicorn, openai"],
                        { encoding: "utf8" });
if (probe.status !== 0) {
  console.error("[prepare-backend] python deps missing:\n" + probe.stderr);
  process.exit(1);
}
console.log(`[prepare-backend] ready at ${backendSrc}`);
