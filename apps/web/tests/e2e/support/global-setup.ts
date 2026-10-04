import type { FullConfig } from "@playwright/test";

export default function setup(config: FullConfig) {
  if (config.workers !== 1 || config.projects.some(project => project.retries !== 0)) {
    throw new Error("Product E2E requires one worker and zero retries.");
  }
}
