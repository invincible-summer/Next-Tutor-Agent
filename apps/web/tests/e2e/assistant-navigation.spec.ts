import { expect, test, type Page } from "@playwright/test";
import { loginViaStorage, registerAndLogin } from "./support/helpers";
import type { NavigationTarget } from "@next-tutor/contracts/assistant";

// Keep real conversation/execute/ack transport; replace only the destination in
// the execute response to deterministically exercise the browser adapter contract.
async function navigate(page: Page, target: NavigationTarget) {
  await page.route("**/assistant/actions/*/execute", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.command = { kind: "navigate", target };
    await route.fulfill({ response, json: body });
  }, { times: 1 });
  if (!await page.locator(".assistant-composer textarea").isVisible()) {
    await page.getByRole("button", { name: /学习助手|assistant/i }).click();
  }
  const composer = page.locator(".assistant-composer textarea");
  await composer.fill("带我去记忆中心。");
  await expect(page.locator(".assistant-composer button[aria-label=发送]")).toBeEnabled();
  await composer.press("Enter");
}
const file = { id: "nav_pdf", filename: "Navigation.pdf", original_filename: "Navigation.pdf", has_original: true, folder_id: "", char_count: 50, chunk_count: 1 };
// A decoded image is required; receipt cannot succeed merely on response headers.
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64");

test.describe("assistant navigation receipts", () => {
  let token = "";
  test.beforeAll(async ({ request }) => { token = (await registerAndLogin(request)).token; });
  test.beforeEach(async ({ page }) => { await loginViaStorage(page, token); });

  test("return home and navigate home again acknowledge success", async ({ page }) => {
    await page.goto("/dashboard");
    await navigate(page, { kind: "module", route_id: "home" });
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator(".assistant-action-card[data-state=succeeded]")).toHaveCount(1);
    await navigate(page, { kind: "module", route_id: "home" });
    await expect(page.locator(".assistant-action-card[data-state=succeeded]")).toHaveCount(2);
  });

  test("missing concept never receives a successful ack", async ({ page }) => {
    await page.goto("/memory");
    await expect(page.getByText("还没有学习区", { exact: true })).toBeVisible();
    await page.clock.install();
    await navigate(page, { kind: "learning_archive", workspace_id: "missing-workspace", concept_key: "missing-concept" });
    await expect(page).toHaveURL(/concept=missing-concept/);
    // Fire every 50ms readiness poll in the 12s guard, preserving its real
    // failure/ack behavior without spending that time on an absent target.
    await page.clock.runFor(13_000);
    await expect(page.locator(".assistant-action-card[data-state=failed]")).toHaveCount(1, { timeout: 15000 });
    await expect(page.locator(".assistant-action-card[data-state=succeeded]")).toHaveCount(0);
  });

  test("memory waits for the correct workspace and paginated concept, including back/forward", async ({ page }) => {
    const concepts = Array.from({ length: 21 }, (_, index) => ({
      concept_ref: { key: `concept-${index + 1}`, concept_id: `concept-${index + 1}`, display_name: `Concept ${index + 1}` },
      state: "not_observed",
    }));
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/learner-evaluation/workspaces**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/workspaces")) {
        await route.fulfill({ json: { items: [{ workspace_id: "nav_workspace", workspace_name: "Navigation workspace" }], total: 1 } });
      } else if (url.pathname.endsWith("/concepts")) {
        await gate;
        const offset = Number(url.searchParams.get("offset") || 0);
        await route.fulfill({ json: { items: concepts.slice(offset, offset + 20), total: concepts.length } });
      } else if (url.pathname.endsWith("/nav_workspace")) {
        await route.fulfill({ json: { evaluation_status: "ready", coverage: {} } });
      } else {
        await route.fulfill({ json: { items: [], total: 0 } });
      }
    });
    await page.goto("/profile");
    await navigate(page, { kind: "learning_archive", workspace_id: "nav_workspace", concept_key: "concept-21" });
    await expect(page).toHaveURL(/concept=concept-21/);
    await expect(page.locator(".assistant-action-card[data-state=succeeded]")).toHaveCount(0);
    release();
    await expect(page.locator('[data-concept-id="concept-21"]')).toBeVisible();
    await expect(page.locator('[data-testid="learning-archive"]')).toHaveAttribute("data-workspace", "nav_workspace");
    await expect(page.locator(".assistant-action-card[data-state=succeeded]")).toHaveCount(1);
    await navigate(page, { kind: "learning_archive", workspace_id: "nav_workspace", concept_key: "concept-1" });
    await expect(page.locator('[data-concept-id="concept-1"]')).toBeVisible();
    await expect(page.locator(".assistant-action-card[data-state=succeeded]")).toHaveCount(2);
    await page.goBack();
    await expect(page.locator('[data-concept-id="concept-21"]')).toBeVisible();
    await page.goForward();
    await expect(page.locator('[data-concept-id="concept-1"]')).toBeVisible();
  });

  test("PDF waits for page decode, handles query changes and browser history", async ({ page }) => {
    await page.route("**/api/v1/library", (route) => route.fulfill({ json: { folders: [], files: [file] } }));
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/library/files/nav_pdf/page/*", async (route) => {
      await gate;
      await route.fulfill({ contentType: "image/png", body: png });
    });
    await page.goto("/dashboard");
    await navigate(page, { kind: "file", file_id: file.id, page: 2 });
    await expect(page.locator('[data-file-preview="nav_pdf"][data-page="2"]')).toHaveAttribute("data-preview-state", "loading");
    await expect(page.locator(".assistant-action-card[data-state=succeeded]")).toHaveCount(0);
    release();
    await expect(page.locator('[data-file-preview="nav_pdf"]')).toHaveAttribute("data-preview-state", "ready");
    await expect(page.locator(".assistant-action-card[data-state=succeeded]")).toHaveCount(1);
    // Close modal so the assistant input is accessible, then navigate same page.
    await page.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(page.locator('[data-file-preview="nav_pdf"]')).toHaveCount(0);
    await expect(page).toHaveURL((url) => !url.searchParams.has("page"));
    await navigate(page, { kind: "file", file_id: file.id, page: 3 });
    await expect(page.locator('[data-file-preview="nav_pdf"][data-page="3"]')).toHaveAttribute("data-preview-state", "ready");
    await expect(page.locator(".assistant-action-card[data-state=succeeded]")).toHaveCount(2);
    for (const theme of ["light", "dark"]) {
      await page.evaluate((theme) => document.documentElement.classList.toggle("dark", theme === "dark"), theme);
      await page.setViewportSize({ width: 960, height: 720 });
      const box = await page.locator('[data-file-preview="nav_pdf"]').boundingBox();
      expect(box?.width).toBeLessThan(960);
      await page.screenshot({ path: `test-results/navigation-preview-${theme}.png`, animations: "disabled" });
    }
    await page.goBack();
    await expect(page.locator('[data-file-preview="nav_pdf"]')).toHaveCount(0);
    await page.goForward();
    await expect(page.locator('[data-file-preview="nav_pdf"][data-page="3"]')).toHaveAttribute("data-preview-state", "ready");
  });

  for (const scenario of ["out-of-range", "invalid", "unsupported", "deleted", "forbidden"]) {
    test(`file page ${scenario} fails honestly`, async ({ page }) => {
      const files = scenario === "deleted" ? [] : [{ ...file, ...(scenario === "unsupported" ? { filename: "Notes.txt", original_filename: "Notes.txt" } : {}) }];
      await page.route("**/api/v1/library", (route) => route.fulfill({ json: { folders: [], files } }));
      await page.route("**/library/files/nav_pdf/page/*", (route) => route.fulfill({ status: scenario === "forbidden" ? 403 : 404 }));
      await page.goto("/resources/files");
      await navigate(page, { kind: "file", file_id: file.id, page: scenario === "invalid" ? 0 : 999 });
      await expect(page.locator(".assistant-action-card[data-state=failed]")).toHaveCount(1, { timeout: 15000 });
      await expect(page.locator(".assistant-action-card[data-state=succeeded]")).toHaveCount(0);
    });
  }
});
