/**
 * 历史只读视图（见 docs/architecture/chem-lab.md 的「历史只读」）：
 * `?session=<sid>&at=<rev>` 是真正的只读重放。
 *
 * - 只读横幅 + 对象禁用 + 回到最新；
 * - 上一版/下一版导航与 URL 同步，越界给出明确错误态；
 * - 历史期间无任何写请求（commands/checkpoint/fork/reset/finish 全为 0）；
 * - 从历史创建分支保持显式确认（按钮存在，但不在只读态自动触发）。
 */
import { test, expect } from "@playwright/test";
import { BACKEND, registerAndLogin, loginViaStorage } from "./support/helpers";

interface Setup {
  sessionId: string;
  packHash: string;
}

/** 建会话并执行两条已接受命令（吸取→排出），得到 rev0/1/2 三段历史。 */
async function setupHistory(request: import("@playwright/test").APIRequestContext): Promise<Setup> {
  const created = await request.post(`${BACKEND}/api/v1/tools/lab/chemistry/sessions`, {
    data: { experiment_id: "chem.dilution", mode: "guided", language: "zh", session_seed: 0 },
  });
  expect(created.status()).toBe(200);
  const snap = await created.json();
  let revision = snap.revision as number;
  let seq = 1;
  for (const command of [
    { kind: "aspirate", source_id: "stock", instrument_id: "pipette-1", amount_uL: 3000 },
    { kind: "dispense", instrument_id: "pipette-1", target_id: "beaker-a", amount_uL: 2000 },
  ]) {
    const ack = await request.post(
      `${BACKEND}/api/v1/tools/lab/chemistry/sessions/${snap.session_id}/commands`,
      {
        data: {
          command_id: `hist-e2e-${seq}`, client_seq: seq, base_revision: revision,
          pack_hash: snap.pack_hash, command,
        },
      },
    );
    expect(ack.status()).toBe(200);
    const body = await ack.json();
    expect(body.accepted, body.error_code).toBe(true);
    revision = body.revision;
    seq += 1;
  }
  return { sessionId: snap.session_id, packHash: snap.pack_hash };
}

test("历史模式：只读横幅、对象禁用、回到最新", async ({ page, request }) => {
  const account = await registerAndLogin(request);
  await loginViaStorage(page, account.token);
  const { sessionId } = await setupHistory(request);

  const writes: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().includes(sessionId)) writes.push(req.url());
  });

  await page.goto(`/tools/lab/chemistry?session=${sessionId}&at=1`);
  await expect(page.locator("[data-testid=chem-lab-readonly-banner]")).toBeVisible({ timeout: 20000 });
  await expect(page.locator("[data-testid=chem-lab-history-revision]")).toContainText("1");
  // 只读：对象热区全部禁用（不提供任何写入口）
  const disabled = await page
    .locator('[data-testid^="chem-lab-object-"]')
    .evaluateAll((els) => els.filter((el) => (el as HTMLButtonElement).disabled).length);
  expect(disabled).toBeGreaterThanOrEqual(7);

  // 回到最新：URL 去掉 at，横幅消失，实验台恢复可操作
  await page.locator("[data-testid=chem-lab-back-to-latest]").click();
  await page.waitForURL((url) => !url.searchParams.has("at"));
  await expect(page.locator("[data-testid=chem-lab-readonly-banner]")).toHaveCount(0);
  await expect(page.locator("[data-testid=chem-lab-object-stock]")).toBeEnabled();
  // 整个只读浏览过程零写请求
  expect(writes).toHaveLength(0);
});

test("上一版/下一版导航与 URL 同步，rev0 上一版禁用", async ({ page, request }) => {
  const account = await registerAndLogin(request);
  await loginViaStorage(page, account.token);
  const { sessionId } = await setupHistory(request);

  await page.goto(`/tools/lab/chemistry?session=${sessionId}&at=1`);
  await expect(page.locator("[data-testid=chem-lab-readonly-banner]")).toBeVisible({ timeout: 20000 });
  await page.locator("[data-testid=chem-lab-history-next]").click();
  await page.waitForURL((url) => url.searchParams.get("at") === "2");
  await expect(page.locator("[data-testid=chem-lab-history-revision]")).toContainText("2");

  await page.locator("[data-testid=chem-lab-history-prev]").click();
  await page.locator("[data-testid=chem-lab-history-prev]").click();
  await page.waitForURL((url) => url.searchParams.get("at") === "0");
  await expect(page.locator("[data-testid=chem-lab-history-prev]")).toBeDisabled();
  // tip=2 时下一版禁用
  await page.locator("[data-testid=chem-lab-history-next]").click();
  await page.locator("[data-testid=chem-lab-history-next]").click();
  await page.waitForURL((url) => url.searchParams.get("at") === "2");
  await expect(page.locator("[data-testid=chem-lab-history-next]")).toBeDisabled();
});

test("越界 revision 显示明确错误态并提供回到最新", async ({ page, request }) => {
  const account = await registerAndLogin(request);
  await loginViaStorage(page, account.token);
  const { sessionId } = await setupHistory(request);
  await page.goto(`/tools/lab/chemistry?session=${sessionId}&at=99`);
  await page.waitForTimeout(800);
  await expect(page.locator("main [role=alert]").first()).toBeVisible({ timeout: 10000 });
  await expect(page.locator("[data-testid=chem-lab-back-to-latest]")).toBeVisible();
  await page.locator("[data-testid=chem-lab-back-to-latest]").click();
  await page.waitForURL((url) => !url.searchParams.has("at"));
  await expect(page.locator("[data-testid=chem-lab-sync-status]")).toBeVisible();
});

test("时间轴 revision 跳转进入历史模式", async ({ page, request }) => {
  const account = await registerAndLogin(request);
  await loginViaStorage(page, account.token);
  const { sessionId } = await setupHistory(request);
  await page.goto(`/tools/lab/chemistry?session=${sessionId}`);
  await expect(page.locator("[data-testid=chem-lab-sync-status]")).toBeVisible({ timeout: 20000 });
  await page.locator('[data-testid="chem-lab-revision-jump"] button').first().click();
  await page.waitForURL((url) => url.searchParams.get("at") === "1");
  await expect(page.locator("[data-testid=chem-lab-readonly-banner]")).toBeVisible();
});
