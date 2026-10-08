/**
 * 化学实验台拖拽交互：统一 pointer 入口的硬契约（舞台交互模型，见
 * docs/architecture/chem-lab.md）。
 *

 * - 短按 = 选择，不产生 ghost、不开面板；
 * - 键盘等价路径（选择 → 移动 → 目标清单）与指针路径共用同一意图解析；
 * - Escape 取消拖拽/草稿，不残留 ghost。
 */
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { BACKEND, registerAndLogin, loginViaStorage } from "./support/helpers";

interface Bench {
  page: Page;
  api: APIRequestContext;
  sessionId: string;
  packHash: string;
  dispose: () => Promise<void>;
}

/** API 建会话 + UI 直达实验台（目录/准备页链路由 chem-lab.spec.ts 覆盖）。 */
async function openBench(page: Page, api: APIRequestContext): Promise<Bench> {
  const account = await registerAndLogin(api);
  await loginViaStorage(page, account.token);
  const created = await api.post(`${BACKEND}/api/v1/tools/lab/chemistry/sessions`, {
    data: { experiment_id: "chem.dilution", mode: "guided", language: "zh", session_seed: 0 },
  });
  expect(created.status()).toBe(200);
  const snap = await created.json();
  await page.goto(`/tools/lab/chemistry?session=${snap.session_id}`);
  await expect(page.locator("[data-testid=chem-lab-sync-status]")).toBeVisible({ timeout: 20000 });
  await expect(page.locator("[data-testid=chem-lab-scene]")).toBeVisible();
  return {
    page,
    api,
    sessionId: snap.session_id,
    packHash: snap.pack_hash,
    dispose: async () => {
      await api.dispose();
    },
  };
}

test("拖拽期间零提交，松手仅打开操作草稿（不执行化学命令）", async ({ page, request }) => {
  const bench = await openBench(page, request);
  const commandPosts: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().includes("/commands")) commandPosts.push(req.url());
  });

  const stock = page.locator('[data-testid="chem-lab-object-stock"]');
  const beakerA = page.locator('[data-testid="chem-lab-object-beaker-a"]');
  await expect(stock).toBeVisible();
  const from = await stock.boundingBox();
  const to = await beakerA.boundingBox();

  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + from.width / 2 + 24, from.y + from.height / 2 + 24, { steps: 5 });
  await page.waitForTimeout(160);

  // 拖动中：ghost 存在，命令提交为 0
  await expect(page.locator('[data-testid="chem-lab-drag-ghost]')).toHaveCount(1);
  expect(commandPosts).toHaveLength(0);

  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(300);

  // 松手后：只有操作面板（倾倒草稿，预填用量），没有任何 POST
  expect(commandPosts).toHaveLength(0);
  const panel = page.locator("[data-testid=chem-lab-operation-panel]");
  await expect(panel).toBeVisible();
  const amount = await panel.locator('input[type="number"]').first().inputValue();
  expect(Number(amount)).toBeGreaterThan(0);

  // Escape 关闭草稿，舞台无 ghost 残留
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(page.locator("[data-testid=chem-lab-drag-ghost]")).toHaveCount(0);
  await bench.dispose();
});

test("短按是选择：无 ghost、无面板、aria-pressed 生效", async ({ page, request }) => {
  const bench = await openBench(page, request);
  const beakerB = page.locator('[data-testid="chem-lab-object-beaker-b"]');
  await beakerB.click();
  await expect(beakerB).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("[data-testid=chem-lab-drag-ghost]")).toHaveCount(0);
  await expect(page.locator("[data-testid=chem-lab-operation-panel]")).toHaveCount(0);
  await bench.dispose();
});

test("Escape 中断拖拽：ghost 消失且零提交", async ({ page, request }) => {
  const bench = await openBench(page, request);
  const posts: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().includes("/commands")) posts.push(req.url());
  });
  const stock = page.locator('[data-testid="chem-lab-object-stock"]');
  const box = await stock.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 + 40, { steps: 5 });
  await expect(page.locator("[data-testid=chem-lab-drag-ghost]")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await expect(page.locator("[data-testid=chem-lab-drag-ghost]")).toHaveCount(0);
  await expect(page.locator("[data-testid=chem-lab-operation-panel]")).toHaveCount(0);
  expect(posts).toHaveLength(0);
  await bench.dispose();
});

test("键盘等价路径：选择 → 移动 → 目标清单（与指针共用意图解析）", async ({ page, request }) => {
  const bench = await openBench(page, request);
  const beakerA = page.locator('[data-testid="chem-lab-object-beaker-a"]');
  await beakerA.focus();
  await page.keyboard.press("Enter");
  await expect(beakerA).toHaveAttribute("aria-pressed", "true");

  await page.locator("[data-testid=chem-lab-move-button]").click();
  const targets = page.locator('[data-testid^="chem-lab-move-target-"]');
  await expect(targets.first()).toBeVisible();
  // dilution 初始 7 槽全占用：除自身槽外全部禁用，键盘路径不提供非法目标
  const count = await targets.count();
  expect(count).toBeGreaterThanOrEqual(5);
  const disabled = await targets.evaluateAll((els) => els.filter((el) => el.disabled).length);
  expect(disabled).toBe(count - 1);
  await page.keyboard.press("Escape");
  await expect(targets).toHaveCount(0);
  await bench.dispose();
});
