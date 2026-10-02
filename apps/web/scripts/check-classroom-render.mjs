#!/usr/bin/env node
/* 课堂排版检查 worker（plan.md §9.6）。
 *
 * 用 Playwright headless Chromium 打开编译产物的本地文件（禁止一切外网
 * 请求；内容自包含 data URI）。在 1280×720 / 960×540 两档
 * 测量：每个 block 的 bounds 是否溢出 stage、图片是否解码成功、公式是否
 * 渲染。输出结构化 JSON 报告（只含 block_id/尺寸/错误码）。
 *
 * 用法：node check-classroom-render.mjs --html <path> --json-out <path>
 * 退出码：0 = 通过；2 = 发现问题；3 = 环境错误。
 */
import { chromium } from "playwright";
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 720 },
  { name: "small", width: 960, height: 540 },
];

function parseArgs() {
  const args = { html: "", jsonOut: "", timeoutMs: 40_000 };
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--html") args.html = argv[++i];
    else if (argv[i] === "--json-out") args.jsonOut = argv[++i];
    else if (argv[i] === "--timeout-ms") args.timeoutMs = Number(argv[++i]);
  }
  return args;
}

async function main() {
  const args = parseArgs();
  if (!args.html || !args.jsonOut) {
    console.error("[check-classroom-render] 需要 --html 与 --json-out");
    process.exit(3);
  }
  const report = { ok: true, issues: [], viewports: [] };
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext();
    // 禁止外网：内容必须是自包含的
    await context.route("**/*", (route) =>
      route.request().url().startsWith("file://")
        ? route.continue()
        : route.abort(),
    );
    const page = await context.newPage();
    page.setDefaultTimeout(args.timeoutMs);
    await page.goto(`file://${resolve(args.html)}`, {
      waitUntil: "load",
      timeout: args.timeoutMs,
    });
    await page.evaluate(() => document.fonts.ready);

    for (const vp of VIEWPORTS) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.evaluate(() => window.dispatchEvent(new Event("resize")));
      await page.waitForTimeout(120);

      const slideCount = await page.evaluate(() =>
        document.querySelectorAll(".slide").length);
      for (let i = 1; i <= slideCount; i++) {
        await page.evaluate((order) => {
          document.querySelectorAll(".slide").forEach((slide, j) => slide.classList.toggle("current", j === order - 1));
          window.dispatchEvent(new Event("resize"));
        }, i);
        await page.evaluate(() => document.fonts.ready);
        await page.waitForTimeout(30);
        const issues = await page.evaluate(() => {
          const out = [];
          const slide = document.querySelector(".slide.current");
          const stage = slide.querySelector(".stage");
          const stageRect = stage.getBoundingClientRect();
          const body = slide.querySelector(".body-area");
          const v2 = document.documentElement.getAttribute("data-renderer-version")?.startsWith("2.");
          if (v2 && slide.classList.contains('layout-overflow')) {
            out.push({ block_id: "", code: "layout_overflow" });
          }
          if (v2 && body.scrollHeight > body.clientHeight + 2) {
            out.push({ block_id: "", code: "vertical_overflow" });
          }
          if (body.scrollWidth > body.clientWidth + 2) out.push({ block_id: "", code: "horizontal_overflow" });
          const blocks = Array.from(body.children);
          blocks.forEach((block, index) => {
            const a = block.getBoundingClientRect();
            for (const next of blocks.slice(index + 1)) {
              const b = next.getBoundingClientRect();
              if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 2 &&
                  Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2) {
                out.push({ block_id: block.getAttribute('data-block-id'), code: 'overlap_overflow' });
              }
            }
          });
          slide.querySelectorAll("[data-block-id]").forEach((el) => {
            if (getComputedStyle(el).display === "none") return;
            const id = el.getAttribute("data-block-id");
            const rect = el.getBoundingClientRect();
            if (!rect.width || !rect.height || rect.right > stageRect.right + 2 || rect.left < stageRect.left - 2
                || (rect.bottom > stageRect.bottom + 2 || rect.top < stageRect.top - 2)) {
              out.push({ block_id: id, code: "overflow" });
            }
            if (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2) {
              out.push({ block_id: id, code: "content_overflow" });
            }
            el.querySelectorAll('pre,.flow-card,.step-body,.step-label,.diagram-math-label,td,th').forEach(child => {
              if (child.scrollWidth > child.clientWidth + 2 || child.scrollHeight > child.clientHeight + 2) {
                out.push({ block_id: id, code: "nested_content_overflow" });
              }
            });
            el.querySelectorAll('.flow-node-label').forEach(label => {
              const node = label.previousElementSibling;
              if (!node?.matches('rect.flow-node')) return;
              const textBounds = label.getBoundingClientRect();
              const nodeBounds = node.getBoundingClientRect();
              if (textBounds.left < nodeBounds.left - 2 || textBounds.right > nodeBounds.right + 2
                  || textBounds.top < nodeBounds.top - 2 || textBounds.bottom > nodeBounds.bottom + 2) {
                out.push({ block_id: id, code: "diagram_label_overflow" });
              }
            });
          });
          slide.querySelectorAll("img").forEach((img) => {
            if (!img.complete || !img.naturalWidth) out.push({ block_id: img.closest("[data-block-id]")?.dataset.blockId || "", code: "image_decode_failed" });
          });
          slide.querySelectorAll("[data-katex]").forEach((el) => {
            if (el.getAttribute("data-rendered") !== "1" || !el.querySelector(".katex") || el.querySelector(".katex-error")) {
              out.push({ block_id: el.closest("[data-block-id]")?.dataset.blockId || "", code: "katex_fallback" });
            }
            const minimum = v2 && el.classList.contains('formula-box') ? 20 : 16;
            if (el.getBoundingClientRect().width && parseFloat(getComputedStyle(el).fontSize) < minimum) {
              out.push({ block_id: el.closest("[data-block-id]")?.dataset.blockId || "", code: "formula_too_small" });
            }
          });
          return out;
        });
        for (const issue of issues) report.issues.push({ viewport: vp.name, slide_order: i, ...issue });
      }
      report.viewports.push({
        name: vp.name,
        width: vp.width,
        height: vp.height,
        slides_checked: slideCount,
      });
    }
    report.ok = report.issues.length === 0;
    writeFileSync(args.jsonOut, JSON.stringify(report, null, 2));
    console.log(
      `[check-classroom-render] ${report.ok ? "PASS" : "FAIL"} ` +
      `(${report.issues.length} issues)`,
    );
    process.exit(report.ok ? 0 : 2);
  } catch (err) {
    report.ok = false;
    report.issues.push({
      code: "checker_error",
      detail: String(err).slice(0, 500),
    });
    try {
      writeFileSync(args.jsonOut, JSON.stringify(report, null, 2));
    } catch { /* 尽力输出 */ }
    console.error(`[check-classroom-render] 环境错误: ${err}`);
    process.exit(3);
  } finally {
    await browser?.close().catch(() => {});
  }
}

main();
