#!/usr/bin/env node
/* 生成 Web 设计令牌样式表（见 docs/architecture/frontend.md）。
 *
 * 事实源：packages/design-tokens/data/tokens.json（与 Mobile 直接消费的
 * TS 常量同源）。输出 apps/web/src/styles/tokens.generated.css：
 *   :root / .dark 自定义属性 + Tailwind 4 `@theme inline` 映射。
 * 输出确定性排序，支持 --check（CI 防漂移，漂移时退出 1）。
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(here, "..", "..");
const TOKENS_PATH = resolve(ROOT, "packages", "design-tokens", "data", "tokens.json");
const OUT_PATH = resolve(ROOT, "apps", "web", "src", "styles", "tokens.generated.css");

function fail(message) {
  console.error(`[generate_design_tokens] ${message}`);
  process.exit(1);
}

function renderColorBlock(selector, colors, shadows, fonts) {
  const lines = [`\n${selector} {`];
  for (const [key, value] of Object.entries(colors)) {
    lines.push(`  --${key}: ${value};`);
  }
  lines.push("");
  for (const [key, value] of Object.entries(shadows)) {
    lines.push(`  --shadow-${key}: ${value};`);
  }
  lines.push("");
  lines.push(`  --font-sans: ${fonts.sans};`);
  lines.push(`  --font-serif: ${fonts.serif};`);
  lines.push(`  --font-mono: ${fonts.mono};`);
  lines.push("}");
  return lines;
}

function renderThemeBlock(colors) {
  const lines = ["\n@theme inline {"];
  for (const key of Object.keys(colors)) {
    lines.push(`  --color-${key}: rgb(var(--${key}));`);
  }
  lines.push("  --font-sans: var(--font-sans);");
  lines.push("  --font-serif: var(--font-serif);");
  lines.push("  --font-mono: var(--font-mono);");
  lines.push("  --shadow-sm: var(--shadow-sm);");
  lines.push("  --shadow-md: var(--shadow-md);");
  lines.push("  --shadow-lg: var(--shadow-lg);");
  lines.push("}");
  return lines;
}

function main() {
  const check = process.argv.includes("--check");
  const tokens = JSON.parse(readFileSync(TOKENS_PATH, "utf8"));
  const { color, shadow, font } = tokens;
  if (!color?.light || !color?.dark || !shadow?.light || !shadow?.dark || !font) {
    fail("tokens.json 缺少 color/shadow/font 段");
  }
  if (Object.keys(color.light).join("\0") !== Object.keys(color.dark).join("\0")) {
    fail("light/dark 颜色键不一致");
  }

  const css = [
    "/* ==========================================================================",
    "   Next Tutor Agent 设计令牌（生成文件，禁止手改）",
    "   事实源：packages/design-tokens/data/tokens.json",
    "   生成：scripts/dev/generate_design_tokens.mjs（根 `pnpm tokens:generate`）",
    "   ========================================================================== */",
    ...renderColorBlock(":root", color.light, shadow.light, font),
    ...renderColorBlock(".dark", color.dark, shadow.dark, font),
    ...renderThemeBlock(color.light),
    "",
  ].join("\n");

  if (check) {
    const current = existsSync(OUT_PATH) ? readFileSync(OUT_PATH, "utf8") : null;
    if (current !== css) {
      console.error("[generate_design_tokens] tokens.generated.css 与 tokens.json 漂移；运行 `pnpm tokens:generate`");
      process.exit(1);
    }
    console.log("[generate_design_tokens] --check 通过（无漂移）");
    return;
  }
  mkdirSync(dirname(OUT_PATH), { recursive: true });
  writeFileSync(OUT_PATH, css);
  console.log(`[generate_design_tokens] 已生成 ${OUT_PATH}`);
}

main();
