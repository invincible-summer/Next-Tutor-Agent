#!/usr/bin/env node
/* 课堂渲染资产打包（plan.md §9.4）。
 *
 * 读取 tsc 编译出的 frame-runtime 与仓库固定的 KaTeX dist，把 CSS 字体
 * URL 转为 data URI，输出带 manifest/hash 的后端可读包：
 *   backend/app/classroom/static/generated/
 *     runtime.js / katex.min.js / katex.min.css / manifest.json
 * 缺任一输入即退出非零；不访问网络。
 */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");
const katexDir = resolve(frontendRoot, "node_modules", "katex", "dist");
const runtimePath = resolve(
  frontendRoot, "dist", "classroom", "frame-runtime.js");
const outDir = resolve(
  frontendRoot, "..", "backend", "app", "classroom", "static", "generated");

const RUNTIME_VERSION = "1.0.0";
const REQUIRED_FONT_WEIGHTS = ["Regular", "Bold", "Italic", "BoldItalic"];

function fail(message) {
  console.error(`[build-classroom-assets] ${message}`);
  process.exit(1);
}

function sha256(buffer) {
  return createHash("sha256").update(buffer).digest("hex");
}

function readKatexCss() {
  const css = readFileSync(resolve(katexDir, "katex.min.css"), "utf8");
  // 把 url(fonts/KaTeX_Main-Regular.woff2) 等替换为 data URI
  return css.replace(/url\((?:'|")?fonts\/([^'"?)]+)(?:'|")?\)/g,
    (match, fontFile) => {
      const data = readFileSync(resolve(katexDir, "fonts", fontFile));
      const ext = fontFile.split(".").pop().toLowerCase();
      const mime = ext === "woff2" ? "font/woff2"
        : ext === "woff" ? "font/woff"
          : ext === "ttf" ? "font/ttf" : "application/octet-stream";
      return `url(data:${mime};base64,${data.toString("base64")})`;
    });
}

function main() {
  const runtime = readFileSync(runtimePath, "utf8");
  const katexJs = readFileSync(resolve(katexDir, "katex.min.js"), "utf8");
  const katexCss = readKatexCss();
  for (const w of REQUIRED_FONT_WEIGHTS) {
    try {
      readFileSync(resolve(katexDir, "fonts", `KaTeX_Main-${w}.woff2`));
    } catch {
      fail(`KaTeX 字体缺失: KaTeX_Main-${w}.woff2`);
    }
  }

  mkdirSync(outDir, { recursive: true });
  writeFileSync(resolve(outDir, "runtime.js"), runtime);
  writeFileSync(resolve(outDir, "katex.min.js"), katexJs);
  writeFileSync(resolve(outDir, "katex.min.css"), katexCss);

  const files = {
    "runtime.js": {
      sha256: sha256(Buffer.from(runtime, "utf8")),
      bytes: Buffer.byteLength(runtime),
    },
    "katex.min.js": {
      sha256: sha256(Buffer.from(katexJs, "utf8")),
      bytes: Buffer.byteLength(katexJs),
    },
    "katex.min.css": {
      sha256: sha256(Buffer.from(katexCss, "utf8")),
      bytes: Buffer.byteLength(katexCss),
    },
  };
  const manifest = {
    runtime_version: RUNTIME_VERSION,
    katex_version: "0.16.47",
    generated_at: new Date().toISOString(),
    files,
  };
  writeFileSync(
    resolve(outDir, "manifest.json"),
    JSON.stringify(manifest, null, 2));

  // KaTeX 许可证随包说明（导出 ZIP 打包 licenses/ 用）
  try {
    copyFileSync(
      resolve(frontendRoot, "node_modules", "katex", "LICENSE"),
      resolve(outDir, "KATEX_LICENSE"));
  } catch {
    fail("KaTeX LICENSE 缺失");
  }
  console.log(
    `[build-classroom-assets] 已生成 ${outDir} (runtime ${files["runtime.js"].sha256.slice(0, 12)}…)`);
}

main();
