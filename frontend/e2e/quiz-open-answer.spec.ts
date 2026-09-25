/** Real submission/persistence; controlled read-side grading timing to exercise
 * pending reload and completion without depending on worker/model latency. */
import { test, expect, request as pwRequest } from "@playwright/test";
import { loginViaStorage, registerAndLogin } from "./helpers";

for (const mode of [
  { name: "light", theme: "light", width: 1280, height: 900 },
  { name: "dark-mobile", theme: "dark", width: 390, height: 844 },
]) {
  test(`开放题提交锁定、完整答案和异步反馈可恢复 ${mode.name}`, async ({ page }, testInfo) => {
    const api = await pwRequest.newContext();
    try {
      const learner = await registerAndLogin(api);
      await page.setViewportSize({ width: mode.width, height: mode.height });
      await loginViaStorage(page, learner.token);
      await page.addInitScript((theme) => localStorage.setItem("edu-agent-theme", theme), mode.theme);
      let completed = false;
      await page.route("**/api/v1/quiz/submission?**", async (route) => {
        const response = await route.fetch();
        const body = await response.json();
        if (body.submission) {
          body.submission.pending = !completed;
          body.submission.task_result = completed ? {
            verdict: "partial", grading_status: "graded",
            feedback: { strengths: [], improvement: "请补充端点代入过程。", next_step: "核对新积分限。" },
          } : null;
          body.submission.evaluation = { status: completed ? "ready" : "pending", interpretation_id: completed ? "itp_browser_fixture" : "" };
          body.submission.feedback = completed ? "你的思路正确，还需要说明条件。" : "";
        }
        await route.fulfill({ response, json: body });
      });
      await page.goto("/chat");
      const input = page.locator("textarea").first();
      await expect(input).toBeVisible({ timeout: 20_000 });
      await input.fill("请出两道 ZX-17 定理练习题考我");
      await input.press("Enter");
      const card = page.getByTestId("quiz-card").filter({ has: page.locator("textarea") }).first();
      await expect(card).toBeVisible({ timeout: 90_000 });
      const answer = "由定义出发说明条件。".repeat(30) + "最后一步：保留完整答案的末尾。";
      await expect(card.locator("textarea")).toBeEnabled();
      await card.locator("textarea").fill(answer);
      const accepted = page.waitForResponse((r) => r.url().endsWith("/quiz/record") && r.request().method() === "POST");
      await card.getByRole("button", { name: /提交批改|Submit/ }).click();
      expect((await accepted).status()).toBe(202);
      const submitted = page.getByTestId("quiz-card").filter({ has: page.getByTestId("quiz-submitted-answer") }).first();
      await expect(submitted.getByTestId("quiz-submitted-answer")).toContainText(answer);
      await expect(submitted.getByTestId("quiz-submitted-status")).toHaveText("已提交");
      await expect(submitted.locator("textarea")).toHaveCount(0);
      await expect(submitted.getByRole("button", { name: /提交批改|Submit/ })).toHaveCount(0);

      await page.reload();
      await expect(submitted.getByTestId("quiz-submitted-answer")).toContainText(answer);
      await expect(submitted.getByTestId("submission-outcome")).toContainText("未判定");
      completed = true;
      await expect(submitted.getByTestId("submission-outcome")).toContainText("部分正确");
      await expect(submitted).toContainText("你的思路正确，还需要说明条件。");
      await expect(submitted).toContainText("请补充端点代入过程。");

      await page.reload();
      await expect(submitted).toContainText("部分正确");
      await expect(submitted.getByTestId("quiz-submitted-answer")).toContainText(answer);
      const fits = await submitted.evaluate((el) => el.scrollWidth <= el.clientWidth + 1);
      expect(fits).toBeTruthy();
      await submitted.screenshot({ path: testInfo.outputPath(`submitted-${mode.name}.png`) });
    } finally {
      await api.dispose();
    }
  });
}
