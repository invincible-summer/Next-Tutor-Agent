/** One serial production runner; owns the lock, processes and scratch data. */
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { buildFront } from "./build-front.mjs";
import { pickPortSync } from "./ports.mjs";
import { backendEnv } from "./backend-env.mjs";

const root = fileURLToPath(new URL("../../../", import.meta.url));
process.chdir(root);
const args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
  const [flag, inline] = args[i].split("=");
  if (["--workers", "-j", "--retries"].includes(flag)) {
    const value = inline ?? args[++i];
    if (value !== (flag === "--retries" ? "0" : "1")) {
      throw new Error("E2E uses one worker and zero retries. Select specs or use --grep to limit scope.");
    }
  }
  if (["--config", "-c", "--ui"].includes(flag)) {
    throw new Error("Use pnpm test:e2e with the single production configuration.");
  }
}
if (process.env.E2E_WORKERS && process.env.E2E_WORKERS !== "1") {
  throw new Error("E2E_WORKERS was removed; E2E runs serially.");
}

const lock = resolve(root, ".e2e-lock");
let locked = false;
let scratch;
let child;
let stopping = false;
const servers = [];
function signalChild(signal) {
  if (!child?.pid) return;
  try { process.kill(-child.pid, signal); } catch (error) {
    if (error.code !== "ESRCH") throw error;
  }
}
function stop() { stopping = true; signalChild("SIGTERM"); }
process.on("SIGINT", stop);
process.on("SIGTERM", stop);

async function startServer(name, command, commandArgs, options, url, timeoutMs) {
  const server = spawn(command, commandArgs, { cwd: root, env, detached: true,
    stdio: ["ignore", "pipe", "pipe"], ...options });
  const state = { name, server, log: "", error: null, exited: false };
  servers.push(state);
  for (const stream of [server.stdout, server.stderr]) {
    stream.on("data", chunk => { state.log = (state.log + chunk.toString()).slice(-12_000); });
  }
  server.on("error", error => { state.error = error; });
  server.on("exit", () => { state.exited = true; });
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (stopping) throw new Error("E2E interrupted");
    if (state.error || state.exited) throw new Error(`${name} failed to start: ${state.error?.message || "process exited"}`);
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_000) });
      await response.body?.cancel();
      if (response.ok) { console.log(`[e2e] ${name} ready`); return; }
    } catch { /* retry readiness, never accept a listening socket alone */ }
    await new Promise(resolveWait => setTimeout(resolveWait, 200));
  }
  throw new Error(`${name} readiness exceeded ${timeoutMs / 1000}s`);
}

async function stopServers() {
  for (const { server } of servers.toReversed()) {
    if (!server.pid) continue;
    const kill = signal => {
      try { process.kill(-server.pid, signal); } catch (error) {
        if (error.code !== "ESRCH") throw error;
      }
    };
    const closed = new Promise(resolveClose => {
      if (server.exitCode !== null || server.signalCode !== null) resolveClose();
      else server.once("close", resolveClose);
    });
    kill("SIGTERM");
    const force = setTimeout(() => kill("SIGKILL"), 3_000);
    await closed; clearTimeout(force); kill("SIGKILL");
  }
}

async function run(command, commandArgs, timeoutMs) {
  if (stopping) throw new Error("E2E interrupted");
  await new Promise((resolveRun, reject) => {
    child = spawn(command, commandArgs, { cwd: root, env, stdio: "inherit", detached: true });
    const kill = () => signalChild("SIGKILL");
    const force = setTimeout(kill, timeoutMs + 10_000);
    const timeout = setTimeout(() => {
      console.error(`[e2e] ${command} exceeded ${timeoutMs / 1000}s`); stop();
    }, timeoutMs);
    const onSignal = () => { setTimeout(kill, 5_000).unref(); };
    process.on("SIGINT", onSignal); process.on("SIGTERM", onSignal);
    child.once("error", finish);
    child.once("close", (code, signal) => finish(code === 0 && !stopping ? null :
      new Error(`${command} exited ${code ?? signal}`)));
    function finish(error) {
      clearTimeout(timeout); clearTimeout(force);
      process.off("SIGINT", onSignal); process.off("SIGTERM", onSignal);
      signalChild("SIGKILL"); child = undefined;
      if (error) reject(error); else resolveRun();
    }
  });
}

const env = { ...process.env, NODE_ENV: "production", NEXT_TELEMETRY_DISABLED: "1",
  NEXT_TUTOR_E2E: "1", NEXT_PUBLIC_DEMO_MODE: "0", NEXT_PUBLIC_BASE_PATH: "",
  EDU_TEST_KEYLESS: "1" };
const started = Date.now();
try {
  try { mkdirSync(lock); } catch (error) {
    if (error.code !== "EEXIST") throw error;
    const pid = Number(readFileSync(resolve(lock, "pid"), "utf8"));
    if (!Number.isSafeInteger(pid) || pid <= 0) throw new Error("Invalid E2E lock; inspect .e2e-lock");
    try { process.kill(pid, 0); throw new Error(`E2E already running (pid ${pid})`); }
    catch (probe) { if (probe.code !== "ESRCH") throw probe; }
    rmSync(lock, { recursive: true }); mkdirSync(lock);
  }
  locked = true;
  writeFileSync(resolve(lock, "pid"), String(process.pid));
  scratch = mkdtempSync(resolve(tmpdir(), "next-tutor-e2e-"));
  env.E2E_RUN_ROOT = scratch;
  for (const [name, preferred] of [["E2E_LLM_PORT", 8199], ["E2E_BACKEND_PORT", 8124], ["E2E_FRONTEND_PORT", 3030]]) {
    env[name] = String(pickPortSync(preferred, name, true));
  }
  if (new Set([env.E2E_LLM_PORT, env.E2E_BACKEND_PORT, env.E2E_FRONTEND_PORT]).size !== 3) {
    throw new Error("E2E services require three different ports");
  }
  env.NEXT_PUBLIC_BACKEND_URL = `http://127.0.0.1:${env.E2E_BACKEND_PORT}`;
  env.BACKEND_URL = env.NEXT_PUBLIC_BACKEND_URL;
  if (!args.includes("--list")) {
    await buildFront(run, env);
    await startServer("fake LLM", process.execPath, ["tests/e2e/support/fake-llm-server.mjs"],
      { env: { ...env, FAKE_LLM_PORT: env.E2E_LLM_PORT } }, `http://127.0.0.1:${env.E2E_LLM_PORT}/health`, 10_000);
    await startServer("backend", env.E2E_PYTHON || "python3", ["-m", "uvicorn", "app.main:app",
      "--host", "127.0.0.1", "--port", env.E2E_BACKEND_PORT, "--workers", "1"],
      { cwd: resolve(root, "../../services/api"), env: backendEnv(env) }, `${env.BACKEND_URL}/api/v1/ready`, 30_000);
    await startServer("frontend", "pnpm", ["exec", "next", "start", "--port", env.E2E_FRONTEND_PORT, "--hostname", "127.0.0.1"],
      {}, `http://127.0.0.1:${env.E2E_FRONTEND_PORT}/login`, 30_000);
  }
  await run("pnpm", ["exec", "playwright", "test", ...args], 10 * 60_000 + 15_000);
} catch (error) {
  console.error(`[e2e] ${error.message}`); process.exitCode = 1;
  for (const state of servers) console.error(`[${state.name}]\n${state.log.slice(-4_000)}`);
} finally {
  await stopServers();
  if (scratch) rmSync(scratch, { recursive: true, force: true });
  if (locked) rmSync(lock, { recursive: true, force: true });
  console.log(`[e2e] finished in ${((Date.now() - started) / 1000).toFixed(1)}s${scratch ? "; scratch data cleaned" : ""}`);
}
