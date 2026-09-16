import { expect, request as pwRequest, test } from "@playwright/test";
import { loginViaStorage } from "../e2e/helpers";

const BACKEND_PORT = Number(process.env.LIVE_E2E_BACKEND_PORT || 8125);
const BACKEND = `http://127.0.0.1:${BACKEND_PORT}`;
const RUNS = Number(process.env.LIVE_ILLUSTRATION_RUNS || 3);

async function register(api: Awaited<ReturnType<typeof pwRequest.newContext>>) {
  const email = `live_svg_${Date.now()}_${Math.random().toString(16).slice(2)}@example.com`;
  const password = "live-e2e-pass-123";
  const reg = await api.post(`${BACKEND}/api/v1/auth/register`, {
    data: { email, password, name: "Live SVG E2E" },
  });
  expect([200, 201]).toContain(reg.status());
  const login = await api.post(`${BACKEND}/api/v1/auth/login`, {
    data: { email, password },
  });
  expect(login.status()).toBe(200);
  const body = await login.json();
  const token = String(body.token ?? body.access_token ?? "");
  expect(token).not.toBe("");
  return token;
}

test("local real LLM: required CAT is text-first and repeatedly reaches reviewed SVG", async ({ page }) => {
  test.skip(process.env.LIVE_LLM_TEST !== "1", "explicit local real-LLM lane only");
  expect(RUNS).toBeGreaterThanOrEqual(1);

  const api = await pwRequest.newContext();
  try {
    const token = await register(api);
    const workspaceResponse = await api.post(`${BACKEND}/api/v1/workspaces`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: "Live SVG acceptance", folder_ids: [], file_ids: [] },
    });
    expect(workspaceResponse.status()).toBe(200);
    const workspace = await workspaceResponse.json();
    const workspaceId = String(workspace.workspace_id || "");
    expect(workspaceId).not.toBe("");

    await loginViaStorage(page, token);
    await page.route("**/api/v1/learner-evaluation/workspaces?*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [{
            workspace_id: workspaceId,
            workspace_name: "Live SVG acceptance",
            evaluation_status: "ready",
          }],
          total: 1,
          offset: 0,
          limit: 100,
        }),
      });
    });
    await page.route(`**/api/v1/learner-evaluation/workspaces/${workspaceId}/concepts?*`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [{
            concept_ref: {
              graph_owner_namespace: "live",
              textbook_id: "",
              file_ids: [],
              concept_id: "velocity",
              concept_revision: "1",
              display_name: "速度",
              key: "速度",
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

    await page.goto("/assessment");
    const records: Array<Record<string, unknown>> = [];

    for (let run = 1; run <= RUNS; run += 1) {
      const concept = page.getByRole("button", { name: "速度" });
      await expect(concept).toBeVisible({ timeout: 30_000 });
      await concept.click();

      const selects = page.locator("select");
      await expect(selects).toHaveCount(2);
      await selects.nth(1).selectOption("practice");

      const options = page.getByTestId("assessment-illustration-options");
      const required = options.locator('input[type="checkbox"]');
      await expect(required).toBeEnabled();
      await required.check();

      const startResponsePromise = page.waitForResponse((response) =>
        response.url().endsWith("/api/v1/assessment/start")
        && response.request().method() === "POST");
      const illustrationResponsePromise = page.waitForResponse((response) =>
        /\/api\/v1\/assessment\/questions\/[^/]+\/illustration$/.test(response.url())
        && response.request().method() === "POST");

      const startedAt = Date.now();
      await page.getByRole("button", { name: /开始测评|Start/ }).click();
      const startResponse = await startResponsePromise;
      const textElapsedMs = Date.now() - startedAt;
      expect(startResponse.status()).toBe(200);
      expect(textElapsedMs).toBeLessThanOrEqual(30_000);
      const startBody = await startResponse.json();
      const question = startBody.question ?? {};
      const questionId = String(question.question_id || "");
      expect(questionId).not.toBe("");
      expect(questionId.startsWith("q_draft_")).toBe(false);
      expect(question.illustration ?? null).toBeNull();
      expect(String(question.stem || "")).not.toContain("配图未完成");

      await expect(page.getByText(String(question.stem))).toBeVisible();
      await expect(page.getByTestId("assessment-illustration-generating")).toBeVisible();
      const answer = page.locator("textarea").first();
      await expect(answer).toBeEnabled();
      await answer.fill(`本地真实模型验收 ${run}`);
      await expect(page.getByRole("button", { name: /提交答案|Submit/ })).toBeEnabled();

      const illustrationResponse = await illustrationResponsePromise;
      const illustrationElapsedMs = Date.now() - startedAt - textElapsedMs;
      expect(illustrationResponse.status()).toBe(200);
      const illustrationBody = await illustrationResponse.json();
      expect(illustrationBody.status).toBe("ready");
      expect(illustrationBody.question_id).toBe(questionId);
      expect(illustrationBody.question_revision).toBe(question.question_revision ?? 1);
      expect(illustrationBody.illustration?.kind).toBe("svg");
      expect(illustrationBody.illustration?.sanitizer_version).toBe(2);
      expect(String(illustrationBody.illustration?.svg || "")).toContain("<svg");
      expect(Number(illustrationBody.metrics?.generation_calls || 0)).toBeLessThanOrEqual(3);
      expect(Number(illustrationBody.metrics?.generation_elapsed_ms || 0)).toBeLessThanOrEqual(18_500);

      const illustration = page.getByTestId("question-illustration");
      await expect(illustration).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId("assessment-illustration-generating")).toHaveCount(0);
      await expect(answer).toHaveValue(`本地真实模型验收 ${run}`);
      const naturalWidth = await illustration.getByTestId("question-illustration-image")
        .evaluate((image) => (image as HTMLImageElement).naturalWidth);
      expect(naturalWidth).toBeGreaterThan(0);

      records.push({
        run,
        question_id: questionId,
        text_elapsed_ms: textElapsedMs,
        illustration_elapsed_ms: illustrationElapsedMs,
        generation_calls: illustrationBody.metrics?.generation_calls,
        illustration_repairs: illustrationBody.metrics?.illustration_repairs,
        sanitizer_version: illustrationBody.illustration?.sanitizer_version,
      });

      await page.getByRole("button", { name: /放弃测评|Abandon/ }).first().click();
      await expect(page.getByRole("button", { name: /开始测评|Start/ })).toBeVisible({ timeout: 30_000 });
    }

    console.log("LIVE_ILLUSTRATION_ACCEPTANCE=" + JSON.stringify(records));
    expect(records).toHaveLength(RUNS);
  } finally {
    await api.dispose();
  }
});
