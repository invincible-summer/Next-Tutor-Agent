"use client";

// 提示块（A06 视觉）。
import { AlertCircle, AlertTriangle, Info } from "lucide-react";
import { cn } from "@/lib/cn";
import { useAssistantStore } from "@/lib/assistant/store";
import { stringsFor } from "../strings";

export function NoticeCard({
  tone, code, text,
}: { tone: "info" | "warning" | "error"; code: string; text: string }) {
  const lang = useAssistantStore((s) => s.lang);
  const t = stringsFor(lang);
  const title = tone === "error" ? t.noticeError
    : tone === "warning" ? t.noticeWarning : t.noticeInfo;
  const Icon = tone === "error" ? AlertCircle
    : tone === "warning" ? AlertTriangle : Info;
  return (
    <div className={cn("assistant-card", "assistant-notice", `assistant-notice-${tone}`)}
      role={tone === "error" ? "alert" : "note"}
      data-code={code}>
      <Icon size={15} aria-hidden />
      <div>
        <div className="assistant-notice-title">{title}</div>
        <div className="assistant-notice-text">{text}</div>
      </div>
    </div>
  );
}
