// M-Notes UI 打磨轮视觉验证：可折叠/可拖宽侧栏、单行工具栏+齿轮菜单、
// 专注模式、AI 面板模式选择器；明/暗 + 窄屏。运行于 frontend/ 目录内。
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const BASE = "http://localhost:3000";
const SHOTS = "/tmp/notes_polish_shots";
mkdirSync(SHOTS, { recursive: true });
const log = (...a) => console.log("[check]", ...a);

const SAMPLE = `# 力学笔记

**牛顿第二定律**：$F = ma$，其中 *m* 是质量。

## 核心公式

$$E_k = \\frac{1}{2}mv^2$$

## 关联

参见 [[牛顿第二定律]] 与 [[动能定理|动能]]，标签 #物理 #力学。

> 引用：一切物体总保持匀速直线运动状态。
`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => { errors.push(e.message); log("pageerror:", e.message); });

// 1) 首页（浅色）：微型头部（左右折叠 + 图谱图标）、三张统计卡、无浮动按钮
await page.goto(`${BASE}/notes`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await page.screenshot({ path: `${SHOTS}/01_home_light.png` });
log("home floating pill:", await page.locator("button.absolute.bottom-4").count()); // 期望 0
log("home toggles:", await page.getByTitle("收起/展开笔记栏").count()
  + await page.getByTitle("收起/展开笔记助手").count()); // 期望 2

// 2) 新建空白笔记并输入内容 → 单行工具栏
await page.getByRole("button", { name: /新建笔记/ }).click();
await page.waitForTimeout(400);
await page.getByRole("button", { name: /空白笔记/ }).click();
await page.waitForTimeout(1800);
const editor = page.getByPlaceholder(/开始书写笔记/);
await editor.fill(SAMPLE);
await page.waitForTimeout(1600);
await page.screenshot({ path: `${SHOTS}/02_note_toolbar_light.png` });

// 3) 齿轮菜单
await page.getByTitle("笔记设置").click();
await page.waitForTimeout(400);
await page.screenshot({ path: `${SHOTS}/03_gear_menu_light.png` });
log("gear popover selects:", await page.locator("select").count()); // 期望 ≥1（文件夹）
await page.keyboard.press("Escape");
await page.getByTitle("笔记设置").click(); // 点外关闭（同按钮切换）
await page.waitForTimeout(200);

// 4) 专注模式（全屏覆盖层）→ Esc 退出
await page.getByTitle("专注模式").click();
await page.waitForTimeout(600);
await page.screenshot({ path: `${SHOTS}/04_focus_light.png` });
log("focus overlay:", await page.locator("div.fixed.inset-0.z-50").count()); // 期望 1
await page.keyboard.press("Escape");
await page.waitForTimeout(400);
log("focus overlay after Esc:", await page.locator("div.fixed.inset-0.z-50").count()); // 期望 0

// 5) 左栏折叠 / 展开
await page.getByTitle("收起/展开笔记栏").click();
await page.waitForTimeout(500);
await page.screenshot({ path: `${SHOTS}/05_left_collapsed.png` });
log("sidebar after collapse:", await page.getByRole("button", { name: /新建笔记/ }).count()); // 期望 0
await page.getByTitle("收起/展开笔记栏").click();
await page.waitForTimeout(500);
log("sidebar after expand:", await page.getByRole("button", { name: /新建笔记/ }).count()); // 期望 1

// 6) 左栏拖宽 + 双击复位（对左侧手柄）
const handle = page.getByRole("separator").first();
const hb = await handle.boundingBox();
const sideBefore = await page.evaluate(() =>
  document.querySelector("main div[style*='width']")?.getBoundingClientRect().width ?? 0);
await page.mouse.move(hb.x + hb.width / 2, hb.y + 300);
await page.mouse.down();
await page.mouse.move(hb.x + 130, hb.y + 300, { steps: 10 });
await page.mouse.up();
await page.waitForTimeout(300);
const sideAfter = await page.evaluate(() =>
  document.querySelector("main div[style*='width']")?.getBoundingClientRect().width ?? 0);
log(`left width ${sideBefore} -> ${sideAfter}`); // 期望 240 -> ~370
await page.getByTitle("收起/展开笔记栏").click(); // 折叠后手柄消失，先折叠
await page.waitForTimeout(300);
await page.getByTitle("收起/展开笔记栏").click(); // 再展开（宽度保留）
await page.waitForTimeout(300);
const sideKept = await page.evaluate(() =>
  document.querySelector("main div[style*='width']")?.getBoundingClientRect().width ?? 0);
log("left width kept after collapse roundtrip:", sideKept);

// 7) AI 面板：模式选择器（左下角）
await page.getByTitle("助手模式").click();
await page.waitForTimeout(400);
await page.screenshot({ path: `${SHOTS}/06_mode_picker_light.png` });
log("mode menu items:", await page.getByTitle("助手模式")
  .locator("..").locator("..").getByRole("button").count()); // 菜单内按钮数
for (const label of ["计划", "完全授权", "问答", "协作"]) {
  const hit = await page.getByRole("button", { name: new RegExp(label), exact: false })
    .filter({ hasText: label }).count();
  if (hit > 0) { log("menu has", label); }
}
await page.getByRole("button", { name: /^协作$/ }).click(); // 选回协作
await page.waitForTimeout(300);
log("mode after pick:", await page.getByTitle("助手模式").innerText()); // 期望含 协作

// 8) 右栏折叠（AI 面板头部 chevron = 第二个同 title 按钮）→ 工具栏按钮恢复
await page.getByTitle("收起/展开笔记助手").nth(1).click();
await page.waitForTimeout(500);
await page.screenshot({ path: `${SHOTS}/07_right_collapsed.png` });
log("ai panel after collapse:", await page.getByTitle("助手模式").count()); // 期望 0
await page.getByTitle("收起/展开笔记助手").click();
await page.waitForTimeout(500);
log("ai panel after expand:", await page.getByTitle("助手模式").count()); // 期望 1

// 9) 暗色：编辑器 + 工具栏
await page.evaluate(() => localStorage.setItem("edu-agent-theme", "dark"));
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await page.screenshot({ path: `${SHOTS}/08_note_dark.png` });

// 10) 窄屏 390×844
const mob = await browser.newPage({ viewport: { width: 390, height: 844 } });
await mob.goto(`${BASE}/notes`, { waitUntil: "domcontentloaded" });
await mob.waitForTimeout(3000);
await mob.screenshot({ path: `${SHOTS}/09_narrow_light.png` });
const overflow = await mob.evaluate(() =>
  document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
log("narrow horizontal overflow:", overflow); // 期望 false

await browser.close();
console.log("PAGEERRORS:", errors.length);
console.log("DONE");
