"use client";

import { useEffect } from "react";

type Guard = { message: string; url: string; historyState: unknown };
let activeGuard: Guard | null = null;

/** Installed before App Router's history listener so a cancelled traversal keeps the form mounted. */
export function installUnsavedChangesGuard() {
  const navigation = (window as Window & { navigation?: EventTarget }).navigation;
  const unload = (event: BeforeUnloadEvent) => {
    if (!activeGuard) return;
    event.preventDefault();
    event.returnValue = "";
  };
  const click = (event: MouseEvent) => {
    if (!activeGuard || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = (event.target as Element)?.closest<HTMLAnchorElement>("a[href]");
    if (!link || link.target === "_blank" || link.hasAttribute("download") || link.href === window.location.href) return;
    if (!window.confirm(activeGuard.message)) { event.preventDefault(); event.stopImmediatePropagation(); }
  };
  const navigate = (event: Event) => {
    if (!activeGuard || (event as Event & { navigationType?: string }).navigationType !== "traverse") return;
    if (event.cancelable && !window.confirm(activeGuard.message)) event.preventDefault();
  };
  const history = (event: PopStateEvent) => {
    if (navigation || !activeGuard || window.location.href === activeGuard.url) return;
    if (window.confirm(activeGuard.message)) return;
    event.stopImmediatePropagation();
    window.history.pushState(activeGuard.historyState, "", activeGuard.url);
  };
  navigation?.addEventListener("navigate", navigate);
  window.addEventListener("beforeunload", unload);
  document.addEventListener("click", click, true);
  window.addEventListener("popstate", history);
  return () => {
    navigation?.removeEventListener("navigate", navigate);
    window.removeEventListener("beforeunload", unload);
    document.removeEventListener("click", click, true);
    window.removeEventListener("popstate", history);
  };
}

/** Register the visible explicit-save form; hidden or unmounted pages cannot block navigation. */
export function useUnsavedChanges(dirty: boolean, message: string) {
  useEffect(() => {
    if (!dirty) return;
    const guard = { message, url: window.location.href, historyState: window.history.state };
    activeGuard = guard;
    return () => { if (activeGuard === guard) activeGuard = null; };
  }, [dirty, message]);
}
