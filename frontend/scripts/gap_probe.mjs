import { chromium } from "playwright";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto("http://localhost:3000/notes", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
const rows = page.locator("div.hidden.md\\:block button").filter({ hasText: /满宽测试|空白/ });
if (await rows.count() > 0) { await rows.first().click(); await page.waitForTimeout(1500); }
else { console.log("note not found, use first note");
  const any = page.locator("div.hidden.md\\:block button").filter({ hasText: /\S/ });
  if (await any.count() > 0) { await any.last().click(); await page.waitForTimeout(1500); } }
await page.getByRole("button", { name: "编辑" }).first().click();
await page.waitForTimeout(600);
const geo = await page.evaluate(() => {
  const ta = document.querySelector("textarea");
  const middle = ta?.closest(".flex.min-w-0.flex-1.flex-col");
  const aiPanel = document.querySelector("div[class*='border-l']");
  const r1 = ta?.getBoundingClientRect();
  const r2 = middle?.getBoundingClientRect();
  const r3 = aiPanel?.getBoundingClientRect();
  return {
    textareaRight: r1 && Math.round(r1.right),
    middleRight: r2 && Math.round(r2.right),
    aiLeft: r3 && Math.round(r3.left),
    textareaW: r1 && Math.round(r1.width),
    middleW: r2 && Math.round(r2.width),
  };
});
console.log(JSON.stringify(geo));
const gap = geo.aiLeft - geo.textareaRight;
console.log("编辑器右缘→AI面板左缘 gap =", gap, "px", gap <= 8 ? "→ 无空白 PASS" : "→ 有间隙 FAIL");
await browser.close();
