import { expect, test, type Page } from "@playwright/test";
import { loginViaStorage } from "./support/helpers";

const question = {
  question_id: "q_v3_feedback", question_revision: 1, q_type: "short_answer",
  stem: "小车向右做匀速运动，请说明运动方向。", visual_role: "supplemental",
};

async function mockAssessment(page: Page, mode: "v1" | "v2" | "v3" = "v1") {
  await loginViaStorage(page, "e2e-fake-token");
  let prefs: Record<string, unknown> = { quiz_svg_enabled: true, quiz_illustration_mode: mode };
  const profile = () => ({ name: "E2E", grade: "本科", school: "", subjects: [], avatar: "", prefs });
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/auth/me") return route.fulfill({ json: { status: "ok", user: {
      id: "usr_e2e_mock", role: "student", email: "e2e@example.com", username: "E2E", profile: profile(),
    } } });
    if (path === "/api/v1/user/profile") {
      if (route.request().method() === "PUT") prefs = { ...prefs, ...route.request().postDataJSON().prefs };
      return route.fulfill({ json: { status: "ok", quiz_svg_available: true, profile: profile() } });
    }
    if (path === "/api/v1/assessment/active") return route.fulfill({ json: { status: "none" } });
    if (path === "/api/v1/learner-evaluation/workspaces") return route.fulfill({ json: { items: [], total: 0 } });
    if (path === "/api/v1/quiz/recent") return route.fulfill({ json: { questions: [] } });
    if (path === "/api/v1/student/error-notebook") return route.fulfill({ json: { items: [] } });
    if (path === "/api/v1/chat/sessions") return route.fulfill({ json: { sessions: [] } });
    if (path === "/api/v1/sidebar") return route.fulfill({ json: { sessions: [], workspaces: [], details: {}, classroom_summaries: {} } });
    return route.fallback();
  });
}

test("V3 单次选择与自动审图：修改偏好开关不重置版本", async ({ page }, testInfo) => {
  await mockAssessment(page);
  await page.route("**/api/v1/assessment/start", async route => {
    expect(route.request().postDataJSON().illustration_mode).toBe("v3");
    return route.fulfill({ json: { status: "ok", assessment_id: "asmt_v3", question } });
  });
  await page.route("**/api/v1/assessment/questions/q_v3_feedback/illustration", route => route.fulfill({
    json: { status: "not_required", question_id: question.question_id, question_revision: 1 },
  }));
  await page.goto("/assessment");
  const options = page.getByTestId("assessment-illustration-options");
  const select = options.getByRole("combobox");
  await expect(select).toHaveValue("v1");
  const review = page.getByTestId("assessment-review-options");
  await expect(review.getByText("生成后审查题图", { exact: true })).toBeVisible();
  await select.selectOption("v3");
  await expect(page.getByTestId("assessment-v3-description")).toContainText("补画");
  await expect(page.getByTestId("assessment-automatic-diagram-review")).toBeVisible();
  await expect(review.getByText("生成后审查题图", { exact: true })).toHaveCount(0);
  await options.getByRole("button", { name: "已开启", exact: true }).click();
  await expect(select).toHaveValue("v3");
  await options.getByRole("button", { name: "已关闭", exact: true }).click();
  await expect(select).toHaveValue("v3");
  for (const theme of ["light", "dark"]) {
    await page.evaluate(value => {
      localStorage.setItem("edu-agent-theme", value);
      window.dispatchEvent(new StorageEvent("storage", { key: "edu-agent-theme", newValue: value }));
    }, theme);
    await expect(page.locator("html")).toHaveClass(theme === "dark" ? /dark/ : /^(?!.*dark).*$/);
    await testInfo.attach(`V3 illustration options ${theme}`, {
      body: await options.screenshot(), contentType: "image/png",
    });
    await testInfo.attach(`V3 automatic review ${theme}`, {
      body: await review.screenshot(), contentType: "image/png",
    });
  }
  await page.setViewportSize({ width: 960, height: 900 });
  await expect(select).toBeVisible();
  const bounds = await options.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(960);
  await testInfo.attach("V3 illustration options narrow desktop", {
    body: await options.screenshot(), contentType: "image/png",
  });
  await page.getByRole("button", { name: "开始测评", exact: true }).click();
  await expect(page.getByText(question.stem)).toBeVisible();
});

test("V3 账户默认可保存并用于测评", async ({ page }) => {
  await mockAssessment(page);
  await page.goto("/settings?section=processing");
  const select = page.getByRole("combobox", { name: "默认配图方式" });
  await expect(select).toHaveValue("v1");
  await select.selectOption("v3");
  await expect(select).toHaveValue("v3");
  await expect(select).toBeEnabled();
  await page.goto("/assessment");
  await expect(page.getByTestId("assessment-illustration-options").getByRole("combobox")).toHaveValue("v3");
});

for (const stopped of [false, true]) {
  test(`作答后配图失败可见：${stopped ? "已结束测评无重试入口" : "当前题保留重试"}`, async ({ page }) => {
    await mockAssessment(page, "v3");
    await page.route("**/api/v1/assessment/start", route => route.fulfill({ json: {
      status: "ok", assessment_id: "asmt_v3", question,
    } }));
    await page.route("**/api/v1/assessment/questions/q_v3_feedback/illustration", route => route.fulfill({ json: {
      status: "failed", job_id: "job_v3_failed", question_id: question.question_id, question_revision: 1,
      failure: { code: "visual_review_failed", retryable: true },
    } }));
    await page.route("**/api/v1/assessment/answer", route => route.fulfill({ json: {
      status: "ok", task_result: { verdict: "correct", grading_status: "graded" },
      evaluation: { status: "ready" }, stop_reason: stopped ? "count_limit" : null,
    } }));
    await page.goto("/assessment");
    await page.getByRole("button", { name: "开始测评", exact: true }).click();
    await expect(page.getByTestId("assessment-illustration-failed")).toContainText("题图未通过审查");
    await page.locator("textarea").first().fill("向右");
    await page.getByRole("button", { name: "提交答案", exact: true }).click();
    const feedback = page.getByTestId("assessment-feedback-question");
    await expect(feedback.getByTestId("assessment-illustration-failed")).toContainText("作答反馈已保留");
    if (stopped) await expect(feedback.getByRole("button", { name: "重试配图" })).toHaveCount(0);
    else await expect(feedback.getByRole("button", { name: "重试配图" })).toBeEnabled();
  });
}
