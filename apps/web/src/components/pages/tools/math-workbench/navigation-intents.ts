"use client";

import type { DrawingMode } from "./workbench-types.ts";

/**
 * Structured file/navigation intents for the workbench's destructive
 * transition coordinator (plan D7). Intents are a closed enum — never raw
 * callbacks or unconstrained URLs from storage. `switch-mode` (ADR-0023)
 * routes mode changes through the same dirty check: the target bundle is
 * never destroyed, but the user is still offered a save first.
 */
export type FileIntent =
  | { kind: "leave"; href: string }
  | { kind: "new" }
  | { kind: "open"; documentId: string }
  | { kind: "replace-import"; file: File }
  | { kind: "replace-preset"; presetId: string }
  | { kind: "switch-mode"; mode: DrawingMode };

export function intentLabel(intent: FileIntent): string {
  return intent.kind;
}
