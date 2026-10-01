"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { CheckCircle2, CircleAlert, X } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { cn } from "@/lib/cn";

type Tone = "success" | "error";
type Notify = (message: string, tone?: Tone) => void;
const ToastContext = createContext<Notify | null>(null);

/** One floating notification at a time, outside page layout and scroll containers. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const lang = useUIStore((s) => s.lang);
  const [notice, setNotice] = useState<{ message: string; tone: Tone } | null>(null);
  const notify = useCallback<Notify>((message, tone = "success") => {
    setNotice({ message, tone });
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), notice.tone === "error" ? 6000 : 3000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const Icon = notice?.tone === "error" ? CircleAlert : CheckCircle2;
  return <ToastContext.Provider value={notify}>
    {children}
    {notice && createPortal(
      <div className="motion-pop fixed right-6 top-20 z-[60] flex max-w-[min(24rem,calc(100vw-3rem))] items-start gap-2.5 rounded-xl border border-border bg-surface px-4 py-3 shadow-lg">
        <div role={notice.tone === "error" ? "alert" : "status"} aria-atomic="true" className="flex min-w-0 items-start gap-2.5">
          <Icon size={16} aria-hidden="true" className={cn("mt-0.5 shrink-0", notice.tone === "error" ? "text-danger" : "text-success")} />
          <p className="break-words text-xs leading-5 text-fg">{notice.message}</p>
        </div>
        <button type="button" onClick={() => setNotice(null)} aria-label={lang === "zh" ? "关闭提示" : "Dismiss notification"}
          className="shrink-0 rounded p-0.5 text-muted hover:bg-surface-hover hover:text-fg focus-visible:outline-2 focus-visible:outline-accent">
          <X size={14} aria-hidden="true" />
        </button>
      </div>, document.body,
    )}
  </ToastContext.Provider>;
}

export function useToast(): Notify {
  const notify = useContext(ToastContext);
  if (!notify) throw new Error("useToast requires ToastProvider");
  return notify;
}
