/**
 * Canonical brand and adaptive design tokens (paper-and-ink identity).
 * The web build consumes these through the generated CSS in
 * `apps/web/src/styles/tokens.generated.css`; native clients import the
 * constants directly. The data source of truth is `data/tokens.json`.
 */
export const DESIGN_TOKENS_VERSION = "0.1.0";

export * from "./color.ts";
export * from "./spacing.ts";
export * from "./radius.ts";
export * from "./typography.ts";
export * from "./elevation.ts";
export * from "./motion.ts";
export * from "./layout.ts";
