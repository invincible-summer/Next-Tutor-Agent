/**
 * Cross-client API contracts: generated DTOs, protocol event unions and
 * stable identifier types. Generated files under `./generated` are never
 * hand-edited; the source of truth is the server Pydantic schema layer.
 *
 * classroom/assistant generated modules export a few identically named
 * models (ErrorBody/ErrorResponse), so they are exposed as subpath entry
 * points (`@next-tutor/contracts/classroom`, `.../assistant`) instead of
 * being re-exported from this root — consumers keep the same per-file scope
 * they had before the packages migration.
 *
 * This package must stay platform-neutral: no React, no fetch, no DOM APIs.
 */
export * from "./ids.ts";
export * from "./protocols/chat.ts";
export * from "./generated/illustration.ts";
export * from "./generated/worksheet.ts";
export * from "./generated/chem_lab.ts";
