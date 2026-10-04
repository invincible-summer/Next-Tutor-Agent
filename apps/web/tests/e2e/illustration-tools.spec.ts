import { test, expect, request as pwRequest } from "@playwright/test";
import { BACKEND, loginViaStorage, registerAndLogin } from "./support/helpers";
import type { IllustrationSession, ToolIllustrationJob } from "../../src/lib/api-illustration-tools";
import type { QuestionIllustrationData } from "../../src/lib/types";

test("工具助手真实链路：自动检索、手选内置和私有素材、多轮及历史基础、刷新恢复", async ({ page }) => {
  const api = await pwRequest.newContext();
  try {
    const learner = await registerAndLogin(api);
    const headers = { Authorization: `Bearer ${learner.token}` };
    const saved = await api.post(`${BACKEND}/api/v1/diagram-materials`, { headers, data: {
      title: "E2E 情景烧杯", description: "项目原创的测试烧杯素材", subject: "physics", aliases: ["E2E"],
      scope: "private", source: "manual", enabled: true,
      svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><path d="M220 90 L220 300 L420 300 L420 90" fill="none" stroke="#26364a" stroke-width="3"/></svg>',
    } });
    expect(saved.ok()).toBe(true);
    const material = await saved.json();
    await loginViaStorage(page, learner.token);
    await page.goto("/tools?theme=light");
    await expect(page.getByRole("link", { name: "工具助手", exact: true })).toBeVisible();
    await page.getByTestId("illustration-tool-entry").click();
    const composer = page.getByTestId("illustration-composer");
    const mode = page.getByTestId("illustration-tool-mode");
    await mode.selectOption("v3");
    await expect(page.getByTestId("automatic-material-search")).toBeVisible();
    await composer.fill("画一个烧杯示意图，不添加文字。");
    await page.getByTestId("send-illustration-request").click();
    await expect(page.getByTestId("illustration-revision").filter({ hasText: "版本 1" })).toBeVisible({ timeout: 20_000 });
    await expect(page).toHaveURL(/tools\/illustration\?session=/);
    const sessionId = new URL(page.url()).searchParams.get("session")!;
    const result = page.getByTestId("illustration-result");
    const image = result.getByTestId("question-illustration-image");
    await expect(image).toHaveJSProperty("naturalWidth", 960);
    const firstImage = await image.getAttribute("src");

    await page.getByTestId("choose-illustration-materials").click();
    const picker = page.getByTestId("illustration-material-picker");
    await picker.getByRole("combobox", { name: "学科", exact: true }).selectOption("physics");
    await picker.getByRole("combobox", { name: "素材类型", exact: true }).selectOption("vessel");
    await picker.getByRole("textbox", { name: "搜索素材", exact: true }).fill("beakr");
    const beaker = picker.locator('[data-asset-id="vessel.beaker"]');
    await expect(beaker).toBeVisible();
    await beaker.locator('input[type="checkbox"]').check();
    await picker.getByRole("button", { name: "我的素材", exact: true }).click();
    await picker.getByRole("textbox", { name: "搜索素材", exact: true }).fill("E2E");
    const privateMaterial = picker.locator(`[data-asset-id="material.${material.id}"]`);
    await expect(privateMaterial).toBeVisible();
    await privateMaterial.locator('input[type="checkbox"]').check();
    await page.getByTestId("apply-illustration-materials").click();
    await composer.fill("保留烧杯，在右侧加上标签“水”。");
    const secondRequest = page.waitForRequest(request => request.method() === "POST" && request.url().endsWith(`/sessions/${sessionId}/turns`));
    await page.getByTestId("send-illustration-request").click();
    const selected = (await secondRequest).postDataJSON().selected_materials;
    expect(selected).toEqual(expect.arrayContaining([{ asset_id: "vessel.beaker", version: 1 }, { asset_id: `material.${material.id}`, version: material.revision }]));
    await expect(page.locator('[data-testid="illustration-revision"][data-revision="2"]')).toBeVisible({ timeout: 20_000 });
    await expect(image).not.toHaveAttribute("src", firstImage!);
    await expect(page.getByTestId("illustration-turn")).toHaveCount(2);
    await page.screenshot({ path: "test-results/illustration-tools-light.png", animations: "disabled" });

    await page.locator('[data-testid="illustration-revision"][data-revision="1"]').click();
    await page.getByTestId("use-illustration-revision").click();
    await composer.fill("保留烧杯，在左侧加上标签“烧杯”。");
    const thirdRequest = page.waitForRequest(request => request.method() === "POST" && request.url().endsWith(`/sessions/${sessionId}/turns`));
    await page.getByTestId("send-illustration-request").click();
    const third = (await thirdRequest).postDataJSON();
    expect(third.base_revision).toBe(2); expect(third.source_revision).toBe(1);
    await expect(page.locator('[data-testid="illustration-revision"][data-revision="3"]')).toBeVisible({ timeout: 20_000 });
    await page.reload();
    await expect(page.getByTestId("illustration-turn")).toHaveCount(3);
    await expect(image).toHaveJSProperty("naturalWidth", 960);
    const restored = await api.get(`${BACKEND}/api/v1/tools/illustration/sessions/${sessionId}`, { headers });
    const data = await restored.json();
    expect(data.revision).toBe(3); expect(data.revisions).toHaveLength(3); expect(data.turns[2].source_revision).toBe(1);
    expect(data.turns[1].selected_materials).toHaveLength(2);

    await page.goto(`/tools/illustration?session=${sessionId}&theme=dark`);
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(image).toHaveJSProperty("naturalWidth", 960);
    await page.screenshot({ path: "test-results/illustration-tools-dark.png", animations: "disabled" });
    await page.setViewportSize({ width: 1024, height: 900 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByTestId("choose-illustration-materials").click();
    await expect(picker.getByTestId("illustration-material").first()).toBeVisible();
    await expect.poll(() => page.getByRole("dialog").evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({ path: "test-results/illustration-tools-narrow.png", animations: "disabled" });
  } finally { await api.dispose(); }
});

const image: QuestionIllustrationData = {
  kind: "svg", schema_version: 3, sanitizer_version: 3, width: 640, height: 400,
  svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><rect x="150" y="100" width="200" height="200" fill="none" stroke="#000"/></svg>',
  alt: "项目原创测试图", caption: "测试图", content_hash: `sha256:${"c".repeat(64)}`,
};

test("配图提交响应丢失：读取会话恢复同一轮，V2自动检索和V1模式保持独立", async ({ page }) => {
  await loginViaStorage(page, "e2e-illustration-mock");
  let creates = 0;
  let submits = 0;
  const session: IllustrationSession = { session_id: "scene_mock", title: "恢复测试", revision: 0, active_job_id: null, turns: [], revisions: [], created_at: 1, updated_at: 1 };
  let job: ToolIllustrationJob;
  await page.route("**/api/v1/tools/illustration/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/sessions") && route.request().method() === "GET") return route.fulfill({ json: { items: creates ? [session] : [], total: creates } });
    if (path.endsWith("/sessions") && route.request().method() === "POST") { creates++; return route.fulfill({ json: session }); }
    if (path.endsWith("/sessions/scene_mock")) return route.fulfill({ json: session });
    if (path.endsWith("/turns")) {
      const body = route.request().postDataJSON();
      submits++;
      expect(body.selected_materials).toEqual([]);
      expect(body.mode).toBe(submits === 1 ? "v2" : "v1");
      const rev = ++session.revision;
      session.turns.push({ turn_id: `turn_${rev}`, job_id: `job_${rev}`, message: body.message, mode: body.mode, selected_materials: [], status: "ready", revision: rev, created_at: 1, request_id: body.request_id });
      session.revisions.push({ revision: rev, artifact_id: `artifact_${rev}`, mode: body.mode, illustration: image, created_at: 1 });
      job = { job_id: `job_${rev}`, session_id: session.session_id, turn_id: `turn_${rev}`, mode: body.mode, status: "ready", stage: "ready", progress: 100, base_revision: rev - 1, revision: rev, artifact_id: `artifact_${rev}`, illustration: image, selected_materials: [], failure: null };
      if (submits === 1) return route.abort("connectionreset");
      return route.fulfill({ json: job });
    }
    if (path.includes("/jobs/")) return route.fulfill({ json: job });
    return route.fallback();
  });
  await page.goto("/tools/illustration");
  await page.getByTestId("illustration-tool-mode").selectOption("v2");
  await expect(page.getByTestId("automatic-material-search")).toBeVisible();
  await page.getByTestId("illustration-composer").fill("画一个测试情景");
  await page.getByTestId("send-illustration-request").click();
  await expect(page.getByTestId("illustration-turn")).toHaveCount(1);
  await expect(page.getByTestId("illustration-result").getByTestId("question-illustration-image")).toBeVisible();
  await expect(page).toHaveURL(/session=scene_mock/);
  expect(creates).toBe(1); expect(submits).toBe(1);
  await page.getByTestId("illustration-tool-mode").selectOption("v1");
  await expect(page.getByTestId("choose-illustration-materials")).toHaveCount(0);
  await page.getByTestId("illustration-composer").fill("把对象向右移动");
  await page.getByTestId("send-illustration-request").click();
  await expect(page.getByTestId("illustration-turn")).toHaveCount(2);
  expect(creates).toBe(1); expect(submits).toBe(2);
});
