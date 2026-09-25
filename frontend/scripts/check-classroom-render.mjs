#!/usr/bin/env node
/* 课堂排版检查 worker（plan.md §9.6）。
 *
 * 用 Playwright headless Chromium 打开编译产物的本地文件（禁止一切外网
 * 请求；内容自包含 data URI）。在 1280×720 / 960×540 / 390 阅读模式三档
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
  { name: "desktop", width: 1280, height: 720, reading: false },
  { name: "small", width: 960, height: 540, reading: false },
  { name: "reading", width: 390, height: 844, reading: true },
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
      await page.evaluate((reading) => {
        document.documentElement.setAttribute(
          "data-reading", reading ? "1" : "0");
        window.dispatchEvent(new Event("resize"));
      }, vp.reading);
      await page.waitForTimeout(120);

      const slideCount = await page.evaluate(() =>
        document.querySelectorAll(".slide").length);
      for (let i = 1; i <= slideCount; i++) {
        const issues = await page.evaluate((order) => {
          const out = [];
          const slides = document.querySelectorAll(".slide");
          const slide = slides[order - 1];
          if (!slide) return out;
          for (const s of slides) s.classList.remove("current");
          slide.classList.add("current");
          window.dispatchEvent(new Event("resize"));
          const stage = slide.querySelector(".stage");
          if (stage) {
            const vw = window.innerWidth || 1280;
            const vh = window.innerHeight || 720;
            const reading = document.documentElement
              .getAttribute("data-reading") === "1";
            const scale = reading ? 1
              : Math.min(vw / 1280, vh / 720, 1.15);
            stage.style.transform = reading ? "" : `scale(${scale})`;
          }
          const stageRect = stage
            ? stage.getBoundingClientRect()
            : null;
          slide.querySelectorAll("[data-block-id]").forEach((el) => {
            const id = el.getAttribute("data-block-id");
            const rect = el.getBoundingClientRect();
            const overflow = stageRect && (
              rect.bottom > stageRect.bottom + 2 ||
              rect.right > stageRect.right + 2 ||
              rect.top < stageRect.top - 2 ||
              rect.left < stageRect.left - 2);
            const visible = rect.height > 0 && rect.width > 0;
            if (!visible || overflow) {
              out.push({
                block_id: id,
                code: !visible ? "invisible" : "overflow",
                top: Math.round(rect.top),
                bottom: Math.round(rect.bottom),
                left: Math.round(rect.left),
                right: Math.round(rect.right),
              });
            }
          });
          slide.querySelectorAll("img").forEach((img) => {
            if (!img.complete || img.naturalWidth === 0) {
              out.push({
                block_id: img.closest("[data-block-id]")
                  ?.getAttribute("data-block-id") || "",
                code: "image_decode_failed",
              });
            }
          });
          slide.querySelectorAll("[data-katex]").forEach((el) => {
            if (el.getAttribute("data-rendered") === "1" &&
                !el.querySelector(".katex") && el.textContent.length > 0 &&
                !el.textContent.match(/[a-zA-Z=^_\\]/)) {
              // 渲染兜底为纯文本且不像公式 → 标记
              out.push({
                block_id: el.closest("[data-block-id]")
                  ?.getAttribute("data-block-id") || "",
                code: "katex_fallback",
              });
            }
          });
          return out;
        }, i);
        for (const issue of issues) {
          report.issues.push({
            viewport: vp.name,
            slide_order: i,
            ...issue,
          });
        }
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
