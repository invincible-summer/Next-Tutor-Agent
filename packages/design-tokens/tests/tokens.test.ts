/** Token completeness and generation checks (docs/architecture/client-platform.md; Node builtin test runner). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { lightColors, darkColors, rgbTriplet, windowWidthClass, windowHeightClass } from "../src/index.ts";

const here = dirname(fileURLToPath(import.meta.url));
const GENERATED_CSS = resolve(here, "../../../apps/web/src/styles/tokens.generated.css");

test("light/dark palettes share the same key set", () => {
  assert.deepEqual(Object.keys(lightColors).sort(), Object.keys(darkColors).sort());
});

test("every color token is a parseable RGB triplet", () => {
  for (const [key, value] of Object.entries(lightColors)) {
    assert.deepEqual(rgbTriplet(value).length, 3, `light ${key}`);
  }
  for (const [key, value] of Object.entries(darkColors)) {
    assert.deepEqual(rgbTriplet(value).length, 3, `dark ${key}`);
  }
});

test("web tokens.generated.css exists and covers every color token", () => {
  assert.ok(existsSync(GENERATED_CSS), "tokens.generated.css must be generated (`pnpm tokens:generate`)");
  const css = readFileSync(GENERATED_CSS, "utf8");
  for (const key of Object.keys(lightColors)) {
    assert.ok(css.includes(`--${key}:`), `missing --${key} in generated css`);
    assert.ok(css.includes(`--color-${key}: rgb(var(--${key}))`), `missing @theme mapping for ${key}`);
  }
});

test("window size classes follow the window size class contract", () => {
  assert.equal(windowWidthClass(390), "compact");
  assert.equal(windowWidthClass(599), "compact");
  assert.equal(windowWidthClass(600), "medium");
  assert.equal(windowWidthClass(839), "medium");
  assert.equal(windowWidthClass(840), "expanded");
  assert.equal(windowWidthClass(1199), "expanded");
  assert.equal(windowWidthClass(1200), "large");
  assert.equal(windowWidthClass(1599), "large");
  assert.equal(windowWidthClass(1600), "extraLarge");
  assert.equal(windowHeightClass(479), "compact");
  assert.equal(windowHeightClass(480), "medium");
  assert.equal(windowHeightClass(900), "expanded");
});
