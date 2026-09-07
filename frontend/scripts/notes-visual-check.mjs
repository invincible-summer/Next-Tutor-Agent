// M-Notes 页面视觉验证：明/暗主题 + 窄屏 + 编辑器交互。
// 运行于 frontend/ 目录内（复用其 node_modules 的 playwright + 浏览器缓存）。
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const BASE = "http://localhost:3000";
const SHOTS = "/tmp/notes_shots";
mkdirSync(SHOTS, { recursive: true });

const SAMPLE = `# 力学笔记

**牛顿第二定律**：$F = ma$，其中 *m* 是质量。

## 核心公式

$$E_k = \\frac{1}{2}mv^2$$

## 关联

参见 [[牛顿第二定律]] 与 [[动能定理|动能]]，标签 #物理 #力学。

## 表格

| 量 | 符号 | 单位 |
| --- | --- | --- |
| 力 | F | N |
| 质量 | m | kg |

## 代码

\`\`\`python
F = m * a
\`\`\`

> 引用：一切物体总保持匀速直线运动状态。
`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => console.log("[pageerror]", e.message));

// 1) 首页（浅色）
await page.goto(`${BASE}/notes`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await page.screenshot({ path: `${SHOTS}/01_home_light.png` });

// 2) 新建空白笔记并输入内容
await page.getByRole("button", { name: /新建笔记/ }).click();
await page.waitForTimeout(400);
await page.getByRole("button", { name: /空白笔记/ }).click();
await page.waitForTimeout(1800);
await page.screenshot({ path: `${SHOTS}/02_new_note.png` });

const editor = page.getByPlaceholder(/开始书写笔记/);
await editor.fill(SAMPLE);
await page.waitForTimeout(1600); // 等自动保存 + 预览渲染
await page.screenshot({ path: `${SHOTS}/03_editor_split_light.png` });

// 3) 纯预览模式
await page.getByRole("button", { name: /预览/ }).first().click();
await page.waitForTimeout(800);
await page.screenshot({ path: `${SHOTS}/04_preview_light.png` });

// 4) 关系图
await page.getByRole("button", { name: /笔记关系图/ }).click();
await page.waitForTimeout(1200);
await page.screenshot({ path: `${SHOTS}/05_graph.png` });
await page.getByRole("button", { name: /笔记关系图/ }).click();
await page.waitForTimeout(400);

// 5) 暗色主题（localStorage + 重载，no-flash 脚本会在启动时应用）
await page.evaluate(() => localStorage.setItem("edu-agent-theme", "dark"));
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await page.screenshot({ path: `${SHOTS}/06_editor_dark.png` });

// 6) 暗色首页
await page.goto(`${BASE}/notes`, { waitUntil: "domcontentloaded" });
await page.evaluate(() => localStorage.setItem("edu-agent-theme", "dark"));
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
await page.screenshot({ path: `${SHOTS}/07_home_dark.png` });

// 7) 窄屏（390×844）
const mob = await browser.newPage({ viewport: { width: 390, height: 844 } });
await mob.goto(`${BASE}/notes`, { waitUntil: "domcontentloaded" });
await mob.waitForTimeout(3000);
await mob.screenshot({ path: `${SHOTS}/08_narrow_light.png` });

await browser.close();
console.log("DONE");
