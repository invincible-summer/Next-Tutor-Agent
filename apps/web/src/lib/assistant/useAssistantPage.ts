"use client";

// 页面接入 hook（plan.md §13.1 useAssistantPage，A08）。
// 简单页面可只传 context（无操作能力）；需要 dirty 保护或页面命令的
// 页面传完整 adapter。适配器随页面卸载自动注销。
import { usePathname } from "next/navigation";
import { useEffect, useId, useMemo, useRef } from "react";
import {
  AssistantPageAdapter,
  UseAssistantPageOptions,
  defaultClientState,
  registerPageAdapter,
} from "./page-context";

export type { UseAssistantPageOptions };

export function useAssistantPage(options: UseAssistantPageOptions): void {
  const pathname = usePathname();
  const optionsRef = useRef(options);
  const adapterId = `adapter_${useId()}`;

  useEffect(() => {
    optionsRef.current = options;
  }, [options]);

  const adapter = useMemo<AssistantPageAdapter>(() => ({
    adapter_id: adapterId,
    getContext: () => optionsRef.current.context(),
    getClientState: () => optionsRef.current.clientState?.()
      ?? defaultClientState(),
    beforeNavigate: () =>
      optionsRef.current.beforeNavigate?.() ?? Promise.resolve("allow"),
    navigationStatus: (target) => optionsRef.current.navigationStatus
      ? optionsRef.current.navigationStatus(target)
      : (target.kind === "module" ? { status: "succeeded" } : null),
    perform: (command) =>
      optionsRef.current.perform?.(command) ?? Promise.resolve({
        status: "failed", code: "page_not_ready",
      }),
  }), [adapterId]);

  useEffect(() => registerPageAdapter(adapter), [adapter, pathname]);
}
