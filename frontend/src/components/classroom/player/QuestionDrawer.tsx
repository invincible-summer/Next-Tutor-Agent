"use client";
/* 课堂问答抽屉（plan.md §5.1/§12.5，阶段 H02）。
 *
 * 在当前页旁展开（右栏抽屉，非聊天瀑布）：快捷补讲（没听懂/举个例子）+
 * 自由提问；回答结束后保持暂停，主按钮「继续原课」（回到最初被打断段）
 * 与「从本页开始」由用户显式点击（§5.2.6/§12.5）。思考流不展示
 * （§12.4 课堂 UI 只消费 answer/tool status/done/error）。
 */
import { useEffect, useRef, useState } from "react";
import { CircleHelp, Lightbulb, Loader2, MessageCircle,
         RotateCcw, Send, X } from "lucide-react";
import {
  QUICK_CONFUSED, QUICK_EXAMPLE, type QATurn,
} from "@/lib/classroom/useClassroomQA";

export interface QuestionDrawerStrings {
  title: string;
  confused: string;
  example: string;
  placeholder: string;
  send: string;
  thinking: string;
  resume: string;
  resumeFromPage: string;
  failed: string;
  close: string;
}

export function QuestionDrawer(
  { turns, asking, onAsk, onResume, onResumeFromPage, onClose, s }:
  {
    turns: QATurn[];
    asking: boolean;
    onAsk: (text: string) => void;
    onResume: () => void;
    onResumeFromPage: () => void;
    onClose: () => void;
    s: QuestionDrawerStrings;
  },
) {
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  const lastDone = turns.length > 0 && turns[turns.length - 1].done;

  const submit = () => {
    if (!draft.trim() || asking) return;
    onAsk(draft);
    setDraft("");
  };

  return (
    <section
      aria-label={s.title}
      className="motion-drawer absolute inset-y-0 right-0 z-20 flex w-full max-w-[420px] flex-col border-l border-border bg-surface shadow-xl sm:w-[420px]"
    >
      <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <MessageCircle size={16} className="text-accent" />
        <h2 className="flex-1 text-sm font-semibold text-fg">{s.title}</h2>
        <button type="button" onClick={onClose} aria-label={s.close}
                className="flex h-8 w-8 items-center justify-center rounded-[8px] text-muted hover:bg-surface-hover hover:text-fg">
          <X size={15} />
        </button>
      </header>

      {/* 快捷补讲：真实用户操作表达（§12.6），点击即发送 */}
      <div className="flex flex-wrap gap-2 border-b border-border px-4 py-2.5">
        <button type="button" disabled={asking}
                onClick={() => onAsk(QUICK_CONFUSED)}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs text-fg-secondary transition-colors hover:border-accent/50 hover:text-fg disabled:opacity-50">
          <CircleHelp size={12} /> {s.confused}
        </button>
        <button type="button" disabled={asking}
                onClick={() => onAsk(QUICK_EXAMPLE)}
                className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs text-fg-secondary transition-colors hover:border-accent/50 hover:text-fg disabled:opacity-50">
          <Lightbulb size={12} /> {s.example}
        </button>
      </div>

      <div ref={listRef}
           className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {turns.length === 0 && (
          <p className="pt-6 text-center text-xs text-muted">
            {s.placeholder}
          </p>
        )}
        {turns.map((turn, i) => (
          <div key={i} className="space-y-2">
            <p className="ml-auto w-fit max-w-[85%] rounded-[12px] rounded-br-[4px] bg-accent-soft px-3 py-2 text-[0.82rem] text-fg">
              {turn.question}
            </p>
            {turn.answer && (
              <div className="w-fit max-w-[92%] whitespace-pre-wrap rounded-[12px] rounded-bl-[4px] border border-border bg-bg px-3 py-2 text-[0.82rem] leading-relaxed text-fg">
                {turn.answer}
              </div>
            )}
            {asking && i === turns.length - 1 && !turn.answer && (
              <p className="flex items-center gap-1.5 text-xs text-muted">
                <Loader2 size={12} className="animate-spin" /> {s.thinking}
              </p>
            )}
            {turn.error && (
              <p className="text-xs text-danger">{turn.error}</p>
            )}
          </div>
        ))}
      </div>

      {/* 问答结束保持暂停：恢复由用户点击（§12.5） */}
      {lastDone && !asking && (
        <div className="flex items-center gap-2 border-t border-border px-4 py-2.5">
          <button type="button" onClick={onResume}
                  className="inline-flex h-11 flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-[10px] bg-accent px-4 text-sm font-medium text-on-accent transition-colors hover:bg-accent/85">
            <RotateCcw size={14} /> {s.resume}
          </button>
          <button type="button" onClick={onResumeFromPage}
                  className="inline-flex h-11 cursor-pointer items-center rounded-[10px] border border-border px-3 text-xs text-fg-secondary transition-colors hover:text-fg">
            {s.resumeFromPage}
          </button>
        </div>
      )}

      <div className="flex items-end gap-2 border-t border-border px-4 py-3">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey
                && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          rows={2}
          placeholder={s.placeholder}
          className="min-h-[44px] flex-1 resize-none rounded-[10px] border border-border bg-bg px-3 py-2 text-sm text-fg outline-none transition-colors placeholder:text-muted focus:border-accent"
        />
        <button type="button" onClick={submit} disabled={asking || !draft.trim()}
                aria-label={s.send}
                className="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-[10px] bg-accent text-on-accent transition-colors hover:bg-accent/85 disabled:opacity-40">
          <Send size={16} />
        </button>
      </div>
    </section>
  );
}
