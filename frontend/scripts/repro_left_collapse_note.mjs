// 笔记打开态（NoteToolbar）左栏折叠实测。
import { chromium } from "playwright";

const BASE = process.env.BASE_URL || "http://localhost:3001";
const NOTE_URL = `${BASE}/notes/note_20260818_113214_%E6%8A%98%E5%8F%A0%E5%AE%9E%E6%B5%8B`;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

const state = async () => {
  const loc = page.locator("div.hidden.md\\:block").first();
  if ((await loc.count()) === 0) return { visible: false };
  return { visible: true };
};
const toggle = () => page.getByRole("button", { name: "收起/展开笔记栏" }).first();

await page.goto(NOTE_URL, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
console.log("N1 note open, toolbar visible:",
  await page.getByRole("button", { name: "收起/展开笔记栏" }).count(), "个折叠按钮");
console.log("N2 sidebar:", JSON.stringify(await state()));
await toggle().click();
await page.waitForTimeout(400);
console.log("N3 after NoteToolbar toggle:", JSON.stringify(await state()));
await toggle().click();
await page.waitForTimeout(400);
console.log("N4 restored:", JSON.stringify(await state()));
await browser.close();
console.log("NOTE-PATH DONE");
