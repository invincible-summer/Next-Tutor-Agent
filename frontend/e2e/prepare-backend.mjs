/**
 * E2E backend isolation (plan.md §26-§27): rsync the repo into a scratch dir
 * so the real uvicorn process never writes business data (chat_history/,
 * students/, knowledge/) into the working tree. The backend derives its
 * storage roots from module constants, so an isolated copy is the clean way
 * to keep E2E state out of the versioned repo.
 *
 * Reuses the copy across runs unless E2E_FRESH=1 (full resync of backend/
 * sources only — fixtures under the copy are per-run state).
 */
import { execSync, spawnSync } from "node:child_process";
import { existsSync, rmSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const REPO = resolve(import.meta.dirname, "../..");
const DEST = process.env.E2E_BACKEND_HOME || "/tmp/edu-agent-e2e";
const backendSrc = resolve(DEST, "backend");

function run(cmd, opts = {}) {
  execSync(cmd, { stdio: "inherit", cwd: REPO, ...opts });
}

if (process.env.E2E_FRESH === "1" && existsSync(DEST)) {
  rmSync(DEST, { recursive: true, force: true });
}
mkdirSync(DEST, { recursive: true });

// backend sources + app config roots the backend needs at runtime
run(
  `rsync -a --delete --exclude '__pycache__' --exclude '.venv*' ` +
  `backend/ ${JSON.stringify(backendSrc)}/`,
);
// scripts/deploy docs not needed; .env must NOT leak into the E2E copy
for (const runtime of ["chat_history", "knowledge", "students", "notes",
                       "users", "backend/traces"]) {
  // runtime dirs live under the copy root; create the standard layout
}
mkdirSync(resolve(DEST, "chat_history"), { recursive: true });
mkdirSync(resolve(DEST, "knowledge"), { recursive: true });

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
