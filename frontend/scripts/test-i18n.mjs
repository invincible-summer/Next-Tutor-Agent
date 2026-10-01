import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";
import ts from "typescript";

const require = createRequire(import.meta.url);
const root = path.resolve("src");
const cache = new Map();
const values = new Map();
const storage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value),
};
const browser = {
  URLSearchParams,
  window: { location: { search: "" }, matchMedia: () => ({ matches: false }) }, localStorage: storage,
  document: { documentElement: { classList: { contains: () => false }, style: { setProperty() {} } } },
};
function load(file) {
  if (cache.has(file)) return cache.get(file).exports;
  const context = { ...browser, exports: {}, require: (specifier) => {
    if (specifier.startsWith("@/")) return load(path.join(root, specifier.slice(2)) + ".ts");
    if (specifier.startsWith(".")) return load(path.resolve(path.dirname(file), specifier) + ".ts");
    return require(specifier);
  } };
  cache.set(file, context);
  const compiled = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(compiled, context, { filename: file });
  return context.exports;
}
function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(file) : [file];
  });
}
const files = walk(root).filter((file) => /\.tsx?$/.test(file));
const dictionaries = new Map();
for (const file of files.filter((file) => /strings\.ts$/.test(file))) {
  for (const value of Object.values(load(file))) {
    if (value?.zh && value?.en) dictionaries.set(file, value);
  }
}
const i18n = load(path.join(root, "lib/i18n.ts"));
const global = { zh: i18n.getDict("zh"), en: i18n.getDict("en") };
dictionaries.set(path.join(root, "lib/i18n.ts"), global);
let entries = 0;
function checkPair(zh, en, location) {
  assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort(), `Language keys differ: ${location}`);
  for (const key of Object.keys(zh)) {
    if (typeof zh[key] === "object" && !Array.isArray(zh[key])) {
      checkPair(zh[key], en[key], `${location}.${key}`);
    } else if (typeof zh[key] === "string") {
      entries++;
      const placeholders = (text) => Array.from(text.match(/%[a-z]|\{\w+\}/g) ?? []).sort().join(",");
      assert.equal(placeholders(zh[key]), placeholders(en[key]), `Interpolation differs: ${location}.${key}`);
    }
  }
}
for (const [file, dictionary] of dictionaries) checkPair(dictionary.zh, dictionary.en, path.relative(root, file));

// Check literal translation calls in their page/component namespaces, including
// shared components whose translators are supplied by their parent pages.
function scopes(file, source) {
  const result = [global];
  for (const [dictionaryFile, dictionary] of dictionaries) {
    const relative = path.relative(root, dictionaryFile);
    const section = relative.match(/^app\/\(workspace\)\/([^/]+)/)?.[1];
    if (path.dirname(dictionaryFile) === path.dirname(file)
        || (section && file.includes(`/components/pages/${section}/`))
        || (file.includes("/components/classroom/") && (relative.startsWith("components/classroom/") || relative.startsWith("app/(workspace)/workspaces/")))
        || (file.includes("/components/landing/") && relative.endsWith("landing-strings.ts"))
        || (file.includes("/components/workspace/") && relative.startsWith("components/workspace/"))) result.push(dictionary);
  }
  for (const statement of source.statements) {
    if (!ts.isImportDeclaration(statement)) continue;
    const specifier = statement.moduleSpecifier.text;
    const target = specifier.startsWith("@/") ? path.join(root, specifier.slice(2)) + ".ts"
      : path.resolve(path.dirname(file), specifier) + ".ts";
    if (dictionaries.has(target)) result.push(dictionaries.get(target));
  }
  return result;
}
let calls = 0;
for (const file of files.filter((file) => !/strings\.ts$/.test(file))) {
  const source = ts.createSourceFile(file, fs.readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
  const available = scopes(file, source);
  const globalTranslators = new Set();
  for (const statement of source.statements) {
    if (!ts.isImportDeclaration(statement) || !/(?:^|\/)i18n$/.test(statement.moduleSpecifier.text)) continue;
    const bindings = statement.importClause?.namedBindings;
    if (!bindings || !ts.isNamedImports(bindings)) continue;
    for (const binding of bindings.elements) {
      if ((binding.propertyName ?? binding.name).text === "t") globalTranslators.add(binding.name.text);
    }
  }
  function visit(node) {
    if (ts.isCallExpression(node)) {
      const name = ts.isIdentifier(node.expression) ? node.expression.text
        : ts.isPropertyAccessExpression(node.expression) ? node.expression.name.text : "";
      const isGlobal = ts.isIdentifier(node.expression) && globalTranslators.has(name);
      const key = isGlobal ? node.arguments[1] : ["tr", "ps"].includes(name) ? node.arguments[0] : null;
      if (key && ts.isStringLiteral(key)) {
        calls++;
        assert.ok((isGlobal ? [global] : available).some((dict) => typeof dict.zh[key.text] === "string" && typeof dict.en[key.text] === "string"),
          `Missing translation ${key.text} in ${path.relative(root, file)}:${source.getLineAndCharacterOfPosition(node.pos).line + 1}`);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
}

const { makePageT } = load(path.join(root, "lib/i18n-page.ts"));
const example = { zh: { title: "页面标题" }, en: { title: "Page title" } };
const zh = makePageT("zh", example);
const en = makePageT("en", example);
assert.equal(makePageT("zh", example), zh, "Repeated renders must preserve the translation function reference");
assert.equal(makePageT("en", example), en);
assert.notEqual(zh, en);
assert.equal(en("title"), "Page title");
assert.equal(en("common.retry"), "Retry");
assert.equal(en("unknown", "Explicit fallback"), "Explicit fallback");
assert.equal(en("unknown"), "unknown");
assert.equal(i18n.gradeLabel("en", "本科"), "Undergrad");
assert.equal(i18n.GRADE_LABELS.en.find((entry) => entry.label === "Undergrad").token, "本科");

values.set("edu-agent-lang", "en");
assert.equal(i18n.loadLang(), "en");
values.set("edu-agent-lang", "invalid");
assert.equal(i18n.loadLang(), "zh");
values.set("edu-agent-lang", "en");
const { useUIStore } = load(path.join(root, "lib/store.ts"));
assert.equal(useUIStore.getState().lang, "zh", "Initial state must match SSR before hydration");
useUIStore.getState().hydrateClient();
assert.equal(useUIStore.getState().lang, "en");
assert.equal(useUIStore.getState().mounted, true);
storage.getItem = () => { throw new Error("Storage disabled"); };
storage.setItem = () => { throw new Error("Storage disabled"); };
assert.equal(i18n.loadLang(), "zh");
assert.doesNotThrow(() => useUIStore.getState().setLang("zh"));
assert.doesNotThrow(() => useUIStore.getState().setLang("en"));
assert.equal(useUIStore.getState().lang, "en");
useUIStore.getState().hydrateClient();
assert.equal(useUIStore.getState().lang, "en", "Navigation must retain in-memory language when storage is blocked");
useUIStore.setState({ mounted: false });
assert.doesNotThrow(() => useUIStore.getState().hydrateClient());
assert.equal(useUIStore.getState().mounted, true);

const { applyAction } = load(path.join(root, "components/pages/notes/editorActions.ts"));
const blank = { value: "", start: 0, end: 0 };
assert.equal(applyAction(blank, "bold", "en").value, "**bold text**");
assert.match(applyAction(blank, "table", "en").value, /Column 1/);
assert.equal(applyAction({ value: "原文", start: 0, end: 2 }, "bold", "en").value, "**原文**");
console.log(`i18n: ${dictionaries.size} dictionaries, ${entries} bilingual entries and ${calls} call sites passed; stable translators, storage fallback and editor actions passed.`);
