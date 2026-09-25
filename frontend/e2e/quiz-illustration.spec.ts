/** SVG question diagrams stay inside the shared structured question card. */
import { test, expect, request as pwRequest } from "@playwright/test";
import { loginViaStorage, registerAndLogin } from "./helpers";

test("聊天明确要求带插图：题图在结构化题卡内并可放大", async ({ page }) => {
  const api = await pwRequest.newContext();
  try {
    const learner = await registerAndLogin(api);
    await loginViaStorage(page, learner.token);
    await page.goto("/chat");

    const input = page.locator("textarea").first();
    await expect(input).toBeVisible({ timeout: 20_000 });
    await input.fill("请出一道带插图的物理题");
    await input.press("Enter");

    const card = page.getByTestId("quiz-card").first();
    await expect(card).toBeVisible({ timeout: 90_000 });
    const illustration = card.getByTestId("question-illustration").first();
    await expect(illustration).toBeVisible({ timeout: 30_000 });
    await expect(illustration.getByTestId("question-illustration-image")).toHaveJSProperty("naturalWidth", 640);
    const expand = illustration.getByTestId("question-illustration-expand");
    await expect(expand).toBeVisible();
    await expand.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
  } finally {
    await api.dispose();
  }
});

test("聊天模糊表达考我一下：仍进入结构化题卡", async ({ page }) => {
  const api = await pwRequest.newContext();
  try {
    const learner = await registerAndLogin(api);
    await loginViaStorage(page, learner.token);
    await page.goto("/chat");

    const input = page.locator("textarea").first();
    await expect(input).toBeVisible({ timeout: 20_000 });
    await input.fill("考我一下关于 ZX-17 定理的题");
    await input.press("Enter");

    const card = page.getByTestId("quiz-card").first();
    await expect(card).toBeVisible({ timeout: 90_000 });
    await expect(card).toContainText("ZX-17 定理");
    await expect(card.getByRole("button", { name: /314159/ })).toBeEnabled();
  } finally {
    await api.dispose();
  }
});

test("测评配置：插图选项并入习题生成卡且按钮可切换", async ({ page }) => {
  const api = await pwRequest.newContext();
  try {
    const learner = await registerAndLogin(api);
    await loginViaStorage(page, learner.token);
    await page.goto("/assessment");

    const options = page.getByTestId("assessment-illustration-options");
    await expect(options).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("assessment-illustration-options")).toHaveCount(1);
    const toggle = options.getByRole("button", { name: /已开启|已关闭|On|Off/ });
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
  } finally {
    await api.dispose();
  }
});

test("测评必须配图：文字题先可答，随后同题出现已审核 SVG", async ({ page }) => {
  const api = await pwRequest.newContext();
  try {
    const learner = await registerAndLogin(api);
    await loginViaStorage(page, learner.token);

    await page.route("**/api/v1/assessment/active*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "none" }) });
    });
    await page.route("**/api/v1/learner-evaluation/workspaces?*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [{ workspace_id: "ws_illustration", workspace_name: "配图测试", evaluation_status: "ready" }],
          total: 1,
          offset: 0,
          limit: 100,
        }),
      });
    });
    await page.route("**/api/v1/learner-evaluation/workspaces/ws_illustration/concepts?*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [{
            concept_ref: {
              graph_owner_namespace: "usr_test",
              textbook_id: "tb_test",
              file_ids: ["file_test"],
              concept_id: "velocity",
              concept_revision: "1",
              display_name: "速度",
              key: "velocity-key",
            },
            state: "not_observed",
            evaluation_status: "ready",
            judgment_id: "",
            statement: "",
            claims: [],
            change: null,
            next_probe: null,
            scope_status: "current",
            evidence_count: 0,
            last_observed_at: "",
            updated_at: "",
          }],
          total: 1,
          offset: 0,
          limit: 100,
        }),
      });
    });
    await page.route("**/api/v1/assessment/start", async (route) => {
      const request = route.request().postDataJSON() as { illustration_request?: string };
      expect(request.illustration_request).toBe("required");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "ok",
          assessment_id: "asmt_text_first",
          workspace_id: "ws_illustration",
          difficulty: 2,
          question: {
            question_id: "q_text_first",
            question_revision: 1,
            q_type: "short_answer",
            type: "short_answer",
            stem: "小车沿水平直线向右做匀速运动。说明速度方向。",
            input_spec: { kind: "text", max_bytes: 8192, requires_explanation: true },
            concept_refs: [],
            source_badge: "速度",
            hints_available: true,
          },
        }),
      });
    });
    await page.route("**/api/v1/assessment/questions/q_text_first/illustration", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 700));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "ready",
          question_id: "q_text_first",
          question_revision: 1,
          illustration: {
            kind: "svg",
            schema_version: 1,
            sanitizer_version: 2,
            svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400" width="640" height="400" preserveAspectRatio="xMidYMid meet"><line x1="80" y1="200" x2="520" y2="200" fill="none" stroke="#000" stroke-width="2" /></svg>',
            alt: "一条水平向右的运动方向示意线",
            caption: "运动方向示意",
            width: 640,
            height: 400,
            content_hash: `sha256:${"a".repeat(64)}`,
          },
        }),
      });
    });

    await page.goto("/assessment");
    const concept = page.getByRole("button", { name: "速度" });
    await expect(concept).toBeVisible({ timeout: 30_000 });
    await concept.click();
    const options = page.getByTestId("assessment-illustration-options");
    await options.locator('input[type="checkbox"]').check();
    await page.getByRole("button", { name: /开始测评|Start/ }).click();

    const textQuestion = page.getByText("小车沿水平直线向右做匀速运动。说明速度方向。");
    await expect(textQuestion).toBeVisible();
    await expect(page.getByTestId("assessment-illustration-generating")).toBeVisible();
    const answer = page.locator("textarea").first();
    await expect(answer).toBeEnabled();
    await answer.fill("水平向右");
    await expect(page.getByRole("button", { name: /提交|Submit/ }).last()).toBeEnabled();

    const illustration = page.getByTestId("question-illustration");
    await expect(illustration).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("assessment-illustration-generating")).toHaveCount(0);
    await expect(answer).toHaveValue("水平向右");
  } finally {
    await api.dispose();
  }
});
