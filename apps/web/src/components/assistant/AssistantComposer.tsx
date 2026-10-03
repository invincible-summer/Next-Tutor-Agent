"use client";

// 输入区（A06）：Enter 发送、Shift+Enter 换行、
// 输入法 composition 期间 Enter 不发送；2→6 行自增后内部滚动；
// 流式期间发送按钮切「停止」；草稿即时保存。
import { useRef, useState } from "react";
import { Send, Square } from "lucide-react";
import { cn } from "@/lib/cn";
import { useAssistantStore } from "@/lib/assistant/store";
import { Textarea } from "@/components/ui/Input";
import { stringsFor } from "./strings";

export function AssistantComposer() {
  const lang = useAssistantStore((s) => s.lang);
  const draft = useAssistantStore((s) => s.draft);
  const setDraft = useAssistantStore((s) => s.setDraft);
  const send = useAssistantStore((s) => s.send);
  const stop = useAssistantStore((s) => s.stop);
  const turnState = useAssistantStore((s) => s.turnState);
  const t = stringsFor(lang);
  const busy = ["accepting", "running", "reconnecting"].includes(turnState);
  const composingRef = useRef(false);
  const [focused, setFocused] = useState(false);

  const canSend = !busy && draft.trim().length > 0;

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      if (composingRef.current) return; // IME composition
      event.preventDefault();
      if (canSend) void send(draft);
    }
  };

  return (
    <div className={cn("assistant-composer", focused && "assistant-composer-focused")}>
      <Textarea
        aria-label={t.inputPlaceholder}
        placeholder={t.inputPlaceholder}
        value={draft}
        rows={2}
        className="assistant-composer-input"
        onChange={(e) => {
          setDraft(e.target.value);
          const el = e.target as HTMLTextAreaElement;
          el.style.height = "auto";
          const line = Math.min(
            Math.max(el.scrollHeight, 44), 6 * 22 + 16);
          el.style.height = `${line}px`;
        }}
        onCompositionStart={() => {
          composingRef.current = true;
        }}
        onCompositionEnd={() => {
          composingRef.current = false;
        }}
        onKeyDown={handleKeyDown}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      />
      <button
        type="button"
        aria-label={busy ? t.stop : t.send}
        title={busy ? t.stop : t.send}
        disabled={busy ? false : !canSend}
        onClick={() => {
          if (busy) void stop();
          else if (canSend) void send(draft);
        }}
        className={cn(
          "assistant-send-btn",
          busy ? "assistant-send-stop" : !canSend && "opacity-40",
        )}
      >
        {busy
          ? <Square size={14} aria-hidden />
          : <Send size={16} aria-hidden />}
      </button>
    </div>
  );
}
