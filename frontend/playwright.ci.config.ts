import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// Deliberate allowlist: new browser specs join the extended suite by default.
// Each file protects a different critical journey. Keep this list small.
export default defineConfig({
  ...base,
  globalTimeout: 8 * 60_000,
  testMatch: [
    "auth-isolation.spec.ts",
    "textbook-bm25.spec.ts",
    "strict-qa.spec.ts",
    "grounded-quiz.spec.ts",
    "notes.spec.ts",
    "classroom-workflow.spec.ts",
  ],
});
