// 页面适配器返回已应用的视图/实体状态；null 只表示还需等待。
import type { PageCommandResult } from "@next-tutor/contracts/assistant";

export const navigationSucceeded: PageCommandResult = { status: "succeeded" };
export const navigationMissing: PageCommandResult = { status: "failed", code: "entity_not_found" };
export const navigationUnavailable: PageCommandResult = { status: "failed", code: "capability_disabled" };
export const navigationFailed: PageCommandResult = { status: "failed", code: "page_not_ready" };

/** 只确认已渲染、可见的内容，并在确认时完成滚动定位。 */
export function navigationAnchor(attr: string, value: string, root: ParentNode = document): PageCommandResult | null {
  const element = root.querySelector<HTMLElement>(`[data-${attr}="${CSS.escape(value)}"]`);
  if (!element || !element.getClientRects().length) return null;
  element.scrollIntoView({ block: "center", behavior: "instant" });
  return navigationSucceeded;
}
