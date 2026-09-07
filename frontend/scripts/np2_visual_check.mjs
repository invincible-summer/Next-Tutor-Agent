// 打磨 II 复检：编辑模式满宽、向导三形态、图片上传入口、左栏折叠回归。
// 断言全部 DOM 确定性检查；截图存 /tmp/np2_shots 供人工复核。
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const BASE = "http://localhost:3000";
const SHOTS = "/tmp/np2_shots";
mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

// --- 1. 新建笔记并进入编辑模式：编辑器应占满中栏 ---
await page.goto(`${BASE}/notes`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await page.getByRole("button", { name: /新建笔记/ }).click();
await page.waitForTimeout(400);
await page.getByRole("button", { name: /空白笔记/ }).click();
await page.waitForTimeout(1800);
const editor = page.getByPlaceholder(/开始书写笔记/);
await editor.fill("# 满宽测试\n\n编辑模式应当占满整栏宽度而不是窄列。");
await page.waitForTimeout(1200);

const widths = async () => page.evaluate(() => {
  const ta = document.querySelector("textarea");
  const row = ta?.closest(".flex.min-h-0.flex-1");
  return { textarea: ta ? Math.round(ta.getBoundingClientRect().width) : 0,
           row: row ? Math.round(row.getBoundingClientRect().width) : 0,
           vw: window.innerWidth };
});
// 默认分屏
const splitW = await widths();
await page.screenshot({ path: `${SHOTS}/01_split.png` });
// 切到编辑（单栏）——应与分屏行宽一致（满宽）
await page.getByRole("button", { name: "编辑" }).first().click();
await page.waitForTimeout(600);
const editW = await widths();
await page.screenshot({ path: `${SHOTS}/02_edit_fullwidth.png` });
console.log("[1] split:", JSON.stringify(splitW), " edit:", JSON.stringify(editW));
console.log("[1] 编辑模式满宽:", editW.textarea >= splitW.row - 4 ? "PASS" : "FAIL");

// --- 2. 生成向导三形态 ---
await page.getByRole("button", { name: /AI 生成笔记|AI/ }).first().click();
await page.waitForTimeout(1200);
const modal = page.locator("div.fixed.inset-0").last();
await page.screenshot({ path: `${SHOTS}/03_wizard_template.png` });
await modal.getByRole("button", { name: /知识点总结/ }).first().click();
await page.waitForTimeout(300);
await modal.getByRole("button", { name: /下一步/ }).click();
await page.waitForTimeout(800);
await page.screenshot({ path: `${SHOTS}/04_wizard_sources_sessions.png` });

const sessionPickerVisible
  = await modal.getByText("选择要沉淀的辅导对话", { exact: false }).count();
console.log("[2] sessions 模式对话多选:", sessionPickerVisible > 0 ? "PASS" : "FAIL");

await modal.locator("button", { hasText: "从工作区生成" }).click();
await page.waitForTimeout(400);
const wsPick = await modal.locator("select").count();
console.log("[2] workspace 模式工作区下拉:", wsPick > 0 ? "PASS" : "FAIL");
await page.screenshot({ path: `${SHOTS}/05_wizard_sources_workspace.png` });

await modal.locator("button", { hasText: "从教材生成" }).click();
await page.waitForTimeout(400);
const tbPick = await modal.getByText("选择作为知识来源的教材", { exact: false }).count();
console.log("[2] textbooks 模式教材多选:", tbPick > 0 ? "PASS" : "FAIL");
await page.screenshot({ path: `${SHOTS}/06_wizard_sources_textbooks.png` });

await page.keyboard.press("Escape");
await page.waitForTimeout(400);

// --- 3. AI 面板图片上传入口 ---
const imgBtn = page.getByRole("button", { name: /上传图片/ });
console.log("[3] 图片上传按钮:", await imgBtn.count() > 0 ? "PASS" : "FAIL");
const fileInput = page.locator("input[type=file][accept^='image']");
console.log("[3] 隐藏图片 input:", await fileInput.count() > 0 ? "PASS" : "FAIL");
await page.screenshot({ path: `${SHOTS}/07_ai_panel.png` });

// --- 4. 左栏折叠回归 ---
const toggle = page.getByRole("button", { name: "收起/展开笔记栏" }).first();
const sidebar = page.locator("div.hidden.md\\:block").first();
const before = (await sidebar.count()) > 0;
await toggle.click();
await page.waitForTimeout(400);
const collapsed = (await sidebar.count()) === 0;
await toggle.click();
await page.waitForTimeout(400);
const restored = (await sidebar.count()) > 0;
console.log(`[4] 左栏折叠回归: before=${before} collapsed=${collapsed} restored=${restored}`,
  before && collapsed && restored ? "PASS" : "FAIL");
await page.screenshot({ path: `${SHOTS}/08_left_collapse.png` });

console.log("[pageerrors]", errors.length === 0 ? "none" : errors.join(" | "));
await browser.close();
console.log("DONE");
