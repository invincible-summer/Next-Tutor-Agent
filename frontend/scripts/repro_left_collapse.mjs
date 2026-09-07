// 左栏收起实测（非等待式断言）：home 与 note-open 两处入口 + 持久化。
import { chromium } from "playwright";

const BASE = process.env.BASE_URL || "http://localhost:3001";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => console.log("[pageerror]", e.message));

const state = async () => {
  const loc = page.locator("div.hidden.md\\:block").first();
  const n = await loc.count();
  if (n === 0) return { visible: false, width: null };
  const box = await loc.boundingBox({ timeout: 2000 }).catch(() => null);
  return { visible: true, width: box ? Math.round(box.width) : null };
};
const toggle = () => page.getByRole("button", { name: "收起/展开笔记栏" }).first();

// --- A. notes 首页（chromeHeader 入口）---
await page.goto(`${BASE}/notes`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
console.log("A1 home:", JSON.stringify(await state()));
await toggle().click();
await page.waitForTimeout(400);
console.log("A2 after click:", JSON.stringify(await state()));
console.log("A3 persisted:", await page.evaluate(() => localStorage.getItem("edu-agent-notes-layout")));
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(2000);
console.log("A4 after reload:", JSON.stringify(await state()), "(null=折叠持久化生效)");
await toggle().click();
await page.waitForTimeout(400);
console.log("A5 re-expanded:", JSON.stringify(await state()));

// --- B. 打开一篇笔记（NoteToolbar 入口）---
const anyNote = page.locator("div.hidden.md\\:block button").filter({ hasText: /\S/ });
const cnt = await anyNote.count();
if (cnt > 0) {
  await anyNote.nth(Math.min(3, cnt - 1)).click();
  await page.waitForTimeout(1500);
  console.log("B1 note open url:", page.url());
  console.log("B2 sidebar:", JSON.stringify(await state()));
  await toggle().click();
  await page.waitForTimeout(400);
  console.log("B3 after toolbar toggle:", JSON.stringify(await state()));
  await toggle().click();
  await page.waitForTimeout(400);
  console.log("B4 restored:", JSON.stringify(await state()));
} else {
  console.log("B skipped: 没有笔记可打开");
}

await browser.close();
console.log("REPRO DONE");
