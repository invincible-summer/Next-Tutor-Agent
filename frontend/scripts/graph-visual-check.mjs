// TextForceGraph 首页封面视觉验证：浅色/深色、聊天/教材开关关掉再恢复（回归：
// 空态覆盖层曾遮挡按钮）、hover 信息卡。
// 运行前需 ./start.sh 已启动（前端 :3001）。用法：node scripts/graph-visual-check.mjs
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const BASE = "http://localhost:3001";
const SHOTS = "/tmp/graph_shots";
mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => console.log("[pageerror]", e.message));
page.on("console", (m) => m.type() === "error" && console.log("[console]", m.text()));

await page.goto(`${BASE}/notes`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(4500); // 等图谱加载 + 预收敛 + 漂浮
await page.screenshot({ path: `${SHOTS}/01_home_light.png` });

// 开关回归：关掉聊天 → 截屏 → 再点开恢复 → 截屏（按钮不能被覆盖层挡住）
const chats = page.getByRole("button", { name: /^聊天$/ });
const books = page.getByRole("button", { name: /^教材$/ });
await chats.click();
await page.waitForTimeout(900);
await page.screenshot({ path: `${SHOTS}/02_chats_off.png` });
await books.click();
await page.waitForTimeout(900);
await page.screenshot({ path: `${SHOTS}/03_both_off.png` });
await chats.click(); // 恢复（上一轮 bug：空态覆盖层挡住后点不动）
await books.click();
await page.waitForTimeout(900);
await page.screenshot({ path: `${SHOTS}/04_restored.png` });

// hover 第一个文本节点看信息卡
const canvas = page.locator("canvas");
await canvas.hover({ position: { x: 500, y: 400 } });
await page.waitForTimeout(400);
await page.screenshot({ path: `${SHOTS}/05_hover.png` });

// 暗色主题
await page.evaluate(() => localStorage.setItem("edu-agent-theme", "dark"));
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(4500);
await page.screenshot({ path: `${SHOTS}/06_home_dark.png` });

await browser.close();
console.log("DONE");
