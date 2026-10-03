/** Child process for ports.mjs — never imported by a Playwright config. */
import { createServer } from "node:net";

const [preferredRaw, envName] = process.argv.slice(2);
const preferred = Number(preferredRaw);

function isFree(port) {
  return new Promise((resolve) => {
    const probe = createServer();
    probe.once("error", () => resolve(false));
    probe.once("listening", () => probe.close(() => resolve(true)));
    probe.listen({ port, host: "127.0.0.1" });
  });
}

if (envName && process.env[envName]) {
  console.log(Number(process.env[envName]));
} else {
  let chosen = 0;
  for (let port = preferred; port < preferred + 50; port += 1) {
    // eslint-disable-next-line no-await-in-loop -- sequential fallback is the point
    if (await isFree(port)) { chosen = port; break; }
  }
  if (!chosen) {
    process.stderr.write(`[e2e-ports] no free port in ${preferred}..${preferred + 49}\n`);
    process.exit(1);
  }
  if (chosen !== preferred) {
    console.error(`[e2e-ports] ${preferred} busy, using ${chosen} instead`);
  }
  console.log(chosen);
}
