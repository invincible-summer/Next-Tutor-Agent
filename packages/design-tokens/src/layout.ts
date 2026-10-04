/** Layout tokens: content widths and window size classes (window-based, not device-based). */
import raw from "../data/tokens.json" with { type: "json" };

export const CONTENT_MAX_WIDTH = raw.layout.contentMaxWidth;
export const READABLE_MAX_WIDTH = raw.layout.readableMaxWidth;

export const WINDOW = Object.freeze({ ...raw.layout.window }) as Readonly<{
  compactMaxDp: number;
  mediumMinDp: number;
  mediumMaxDp: number;
  expandedMinDp: number;
  expandedMaxDp: number;
  largeMinDp: number;
  largeMaxDp: number;
  extraLargeMinDp: number;
  compactHeightMaxDp: number;
}>;

export type WindowWidthClass = "compact" | "medium" | "expanded" | "large" | "extraLarge";
export type WindowHeightClass = "compact" | "medium" | "expanded";

/** Width classes follow the current window size, never the device model. */
export function windowWidthClass(widthDp: number): WindowWidthClass {
  if (widthDp < WINDOW.mediumMinDp) return "compact";
  if (widthDp <= WINDOW.mediumMaxDp) return "medium";
  if (widthDp <= WINDOW.expandedMaxDp) return "expanded";
  if (widthDp <= WINDOW.largeMaxDp) return "large";
  return "extraLarge";
}

/** Compact height forbids multi-pane layouts regardless of width. */
export function windowHeightClass(heightDp: number): WindowHeightClass {
  if (heightDp <= WINDOW.compactHeightMaxDp) return "compact";
  if (heightDp < 900) return "medium";
  return "expanded";
}
