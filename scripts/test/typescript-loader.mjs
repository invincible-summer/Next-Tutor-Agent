/**
 * Minimal TypeScript ESM loader for environments whose Node binary was built
 * without the optional `--experimental-strip-types` support. It intentionally
 * transpiles only test imports; type checking remains the responsibility of
 * the normal `tsc --noEmit` checks.
 */
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

// Resolve from the package that owns the test command. Each workspace package
// already declares TypeScript, while the repository root intentionally has no
// runtime dependency on it.
const require = createRequire(path.join(process.cwd(), "package.json"));
const ts = require("typescript");

const TS_EXTENSIONS = new Set([".ts", ".tsx", ".mts", ".cts"]);

export async function load(url, context, nextLoad) {
  const pathname = new URL(url).pathname;
  const extension = pathname.slice(pathname.lastIndexOf("."));
  if (!TS_EXTENSIONS.has(extension)) return nextLoad(url, context);

  const source = await readFile(new URL(url), "utf8");
  const result = ts.transpileModule(source, {
    fileName: pathname,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      jsx: ts.JsxEmit.ReactJSX,
      sourceMap: false,
      inlineSourceMap: false,
      inlineSources: false,
      verbatimModuleSyntax: false,
      esModuleInterop: true,
    },
  });
  return { format: "module", source: result.outputText, shortCircuit: true };
}
