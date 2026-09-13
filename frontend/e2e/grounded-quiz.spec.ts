/**
 * Flow 4 + Flow 5（plan.md §26）：Grounded Quiz 与 NOT_FOUND。
 * 教材在场时出题 -> QuizCard 出现、教材依据 badge（quiz-source-badge）、
 * source_refs 可展开、作答与 record 成功；
 * 请求教材中没有的 ZX-999 -> strict 语义下不生成伪教材题。
 */
import { test, expect } from "@playwright/test";
import { request as pwRequest } from "@playwright/test";
import { BACKEND, registerAndLogin, loginViaStorage } from "./helpers";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

async function sessionWithTextbook(api: any, token: string): Promise<string> {
  const fixture = await readFile(join(process.cwd(), "e2e/fixtures/zx17.txt"));
  const up = await api.post(`${BACKEND}/api/v1/chat/upload`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      files: { name: "zx17讲义.txt", mimeType: "text/plain", buffer: fixture },
    },
  });
  expect(up.status()).toBe(200);
  const body = await up.json();
  const sid: string = body.session_id ?? body.sessionId;
  expect(sid).toBeTruthy();
  return sid;
}

test("根据教材出题：quiz 卡带教材依据 badge 与来源引用", async ({ page }) => {
  const api = await pwRequest.newContext();
  const a = await registerAndLogin(api);
  const sessionId = await sessionWithTextbook(api, a.token);

  await loginViaStorage(page, a.token);
  await page.goto(`/chat/${sessionId}`);
  const input = page.locator("textarea").first();
  await expect(input).toBeVisible({ timeout: 20_000 });
  await input.fill("根据刚才的教材，给我出 2 道 ZX-17 定理练习");
  await input.press("Enter");

  // 出题卡出现（fake LLM 的 executor 工具循环触发 generate_quiz；
  // grounding provider 命中教材证据 -> grounded）
  const badge = page.getByTestId("quiz-source-badge").first();
  await expect(badge).toBeVisible({ timeout: 90_000 });

  // 作答第一题（MC 选 A）并揭晓 -> record 成功（掌握度提示或判定出现）
  const firstOption = page.getByRole("button", { name: /314159/ }).first();
  await expect(firstOption).toBeVisible({ timeout: 30_000 });
  await firstOption.click();
  const reveal = page.getByText(/揭晓|提交/).first();
  await reveal.click();
  await expect(page.getByText(/回答正确|正确答案/).first())
    .toBeVisible({ timeout: 30_000 });

  // 解析展开后依据可见（filename + excerpt）
  const explanationToggle = page.getByText("解析", { exact: false }).first();
  await explanationToggle.click();
  await expect(page.getByTestId("quiz-source-refs").first())
    .toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("zx17讲义", { exact: false }).first()).toBeVisible();
  await api.dispose();
});

test("strict 请求教材中没有的知识点：不出伪教材题", async ({ page }) => {
  const api = await pwRequest.newContext();
  const a = await registerAndLogin(api);
  const sessionId = await sessionWithTextbook(api, a.token);

  // Assessment API 的 strict 语义（服务端权威合同）：
  // strict_textbook + scope 有效但教材无该内容 -> grounding_not_found
  const resp = await api.post(`${BACKEND}/api/v1/assessment/start`, {
    headers: { Authorization: `Bearer ${a.token}`,
               "Content-Type": "application/json" },
    data: { concept: "ZZZ-999 未定义概念", session_id: sessionId,
            strict_textbook: true },
  });
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.status).toBe("grounding_not_found");
  expect(body.question).toBeNull();
  expect(body.grounding?.tier).toBe("not_found");
  await api.dispose();
});
