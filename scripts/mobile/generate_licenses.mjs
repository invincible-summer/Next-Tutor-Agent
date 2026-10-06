// Generates the mobile third-party license screen data from
// licenses/inventory.json (owned by scripts/compliance/generate_notices.py).
//
// Scope: the apps/mobile production dependency subgraph — seeded from the
// inventory's "apps/mobile:dependencies" anchors and walked over component
// dependency edges. This is a superset of what the JS bundle actually ships
// (it includes expo CLI tool-chain transitives): over-inclusion is the safe
// direction for notices, and it keeps the screen in lockstep with the
// repository-wide inventory instead of a second resolution.
//
// Per package we ship name/version/license expression/homepage plus the
// copyright lines extracted from its own license text; complete license
// terms are deduplicated per expression (MIT bodies are identical modulo
// the copyright line, which the per-package entry carries).
//
// Usage:
//   node scripts/mobile/generate_licenses.mjs          # regenerate
//   node scripts/mobile/generate_licenses.mjs --check  # drift gate (CI)
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../..");
const inventoryPath = resolve(root, "licenses/inventory.json");
const outPath = resolve(root, "apps/mobile/src/features/me/licenses.data.json");
const MOBILE_ANCHOR = "apps/mobile:dependencies";
const COPYRIGHT_RE = /^\s*(?:\/\/\s*)?(.*[Cc]opyright.*)$/;

const check = process.argv.includes("--check");
const inventory = JSON.parse(readFileSync(inventoryPath, "utf8"));
const components = inventory.components;
if (!Array.isArray(components) || components.length === 0) {
  console.error("generate_licenses: licenses/inventory.json has no components");
  process.exit(1);
}

// Evidence exceptions: packages whose registry metadata lacks a license
// field, resolved to an expression by an explicit decision record
// (kind "license-evidence-exception" in licenses/decisions.json).
const decisions = JSON.parse(
  readFileSync(resolve(root, "licenses/decisions.json"), "utf8"),
);
const exceptionExpression = new Map();
for (const decision of decisions.decisions ?? []) {
  if (decision.kind === "license-evidence-exception" && decision.expression) {
    exceptionExpression.set(decision.component, decision.expression);
  }
}

const npmByKey = new Map();
for (const c of components) {
  if (c.ecosystem === "npm") npmByKey.set(`${c.name}@${c.version}`, c);
}
const seeds = components.filter(
  (c) => Array.isArray(c.direct_in) && c.direct_in.includes(MOBILE_ANCHOR),
);
if (seeds.length === 0) {
  console.error(`generate_licenses: no components anchored at ${MOBILE_ANCHOR}`);
  process.exit(1);
}

const subgraph = new Map();
const queue = [...seeds];
while (queue.length > 0) {
  const current = queue.pop();
  if (subgraph.has(current.name)) continue;
  subgraph.set(current.name, current);
  for (const edge of current.dependencies ?? []) {
    const target = npmByKey.get(edge);
    if (target && !subgraph.has(target.name)) queue.push(target);
  }
}

// Canonical full license text per expression, from the alphabetically first
// component that actually carries a license file for it (deterministic).
const textByExpression = new Map();
const packages = [...subgraph.values()]
  .sort((a, b) => a.name.localeCompare(b.name))
  .map((c) => {
    const expression = c.license_expression
      ?? exceptionExpression.get(c.purl)
      ?? "";
    if (!expression) {
      console.error(`generate_licenses: ${c.name}@${c.version} has no license expression`);
      process.exit(1);
    }
    let copyrights = [];
    const file = (c.license_files ?? [])[0];
    if (file) {
      const text = readFileSync(resolve(root, file.path), "utf8");
      copyrights = text
        .split(/\r?\n/)
        .map((line) => (COPYRIGHT_RE.exec(line) ?? [])[1]?.trim())
        .filter((line) => line && line.length <= 120)
        .slice(0, 3);
      if (!textByExpression.has(expression)) textByExpression.set(expression, text);
    }
    return {
      name: c.name,
      version: c.version,
      license: expression,
      homepage: typeof c.homepage === "string" ? c.homepage : "",
      copyrights,
    };
  });

const data = {
  schema: 1,
  source: "licenses/inventory.json",
  packages,
  license_texts: Object.fromEntries(
    [...textByExpression.entries()].sort((a, b) => a[0].localeCompare(b[0])),
  ),
};
const serialized = JSON.stringify(data, null, 2) + "\n";

if (check) {
  let committed = "";
  try {
    committed = readFileSync(outPath, "utf8");
  } catch {
    console.error(`generate_licenses: ${resolve(root, "apps/mobile/src/features/me/licenses.data.json")} is missing; run node scripts/mobile/generate_licenses.mjs`);
    process.exit(1);
  }
  if (committed !== serialized) {
    console.error(
      "generate_licenses: drift — apps/mobile/src/features/me/licenses.data.json " +
        "does not match licenses/inventory.json. Regenerate with " +
        "node scripts/mobile/generate_licenses.mjs and commit both together.",
    );
    process.exit(1);
  }
  console.log(`licenses data: ${packages.length} packages in sync with inventory`);
  process.exit(0);
}

writeFileSync(outPath, serialized);
console.log(
  `licenses data: wrote ${packages.length} packages, ` +
    `${Object.keys(data.license_texts).length} license texts -> apps/mobile/src/features/me/licenses.data.json`,
);
