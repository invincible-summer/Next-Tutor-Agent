/** Shared i18n protocol: locale mapping, language catalog, translator chain. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { LANGS, createTranslator, localeFor, type Lang } from "../src/index.ts";

test("localeFor maps langs to BCP-47 locales", () => {
  assert.equal(localeFor("zh"), "zh-CN");
  assert.equal(localeFor("en"), "en-US");
});

test("LANGS covers exactly the supported langs with labels", () => {
  assert.deepEqual(LANGS.map((entry) => entry.code), ["zh", "en"] as Lang[]);
  for (const entry of LANGS) assert.ok(entry.label.length > 0);
});

test("createTranslator resolves dict, then fallback, then the key", () => {
  const t = createTranslator(
    { zh: { "common.retry": "重试" }, en: { "common.retry": "Retry" } },
    "zh",
  );
  assert.equal(t("common.retry"), "重试");
  assert.equal(t("missing.key", "fallback"), "fallback");
  assert.equal(t("missing.key"), "missing.key");
});

test("createTranslator binds the chosen language", () => {
  const dicts = { zh: { k: "中" }, en: { k: "EN" } };
  assert.equal(createTranslator(dicts, "en")("k"), "EN");
  assert.equal(createTranslator(dicts, "zh")("k"), "中");
});
