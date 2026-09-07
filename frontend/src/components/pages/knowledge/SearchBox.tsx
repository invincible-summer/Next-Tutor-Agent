"use client";
// 知识谱系搜索框：输入即浮出结果面板（键盘 ↑↓/Enter/Esc），面板关闭后用
// ‹ i/N › 循环定位各命中项。匹配逻辑统一在 ./search.ts，本组件只管交互。
import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { stateColor } from "@/lib/labels";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";
import type { KnowledgeNode, KnowledgeTaxonomyGroup } from "@/lib/types-modules";
import { SEARCH_RESULT_LIMIT } from "./search";

type Tr = (key: string, fallback?: string) => string;

export function SearchBox({
  value,
  onChange,
  matches,
  groupOf,
  activeMatchId,
  onPick,
  onStep,
  onClear,
  tr,
}: {
  value: string;
  onChange: (v: string) => void;
  /** 当前学段内的全部命中（已按命中质量排序，见 search.ts） */
  matches: KnowledgeNode[];
  groupOf: (n: KnowledgeNode) => KnowledgeTaxonomyGroup | null;
  activeMatchId: string | null;
  onPick: (n: KnowledgeNode) => void;
  onStep: (dir: 1 | -1) => void;
  onClear: () => void;
  tr: Tr;
}) {
  const [open, setOpen] = useState(false);
  const [cursorRaw, setCursorRaw] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const q = value.trim();
  const visible = matches.slice(0, SEARCH_RESULT_LIMIT);
  // 结果集收窄时游标在渲染期收敛，避免 effect 级联 setState
  const cursor = Math.min(cursorRaw, Math.max(0, visible.length - 1));
  const activeIdx = activeMatchId ? matches.findIndex((n) => n.id === activeMatchId) : -1;

  // 键盘游标行滚动跟随
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>('[data-cursor="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [cursor, open]);

  const pick = (n: KnowledgeNode) => {
    setOpen(false);
    onPick(n);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!q) return;
      setOpen(true);
      setCursorRaw((c) => Math.min(c + 1, visible.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursorRaw((c) => Math.max(c - 1, 0));
    } else if (e.key === "Enter") {
      if (open && visible[cursor]) pick(visible[cursor]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div ref={rootRef} className="relative ml-auto shrink-0">
      <div className="flex items-center gap-1.5">
        <div className="relative">
          <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <input
            ref={inputRef}
            value={value}
            onChange={(e) => {
              const v = e.target.value;
              onChange(v);
              setOpen(v.trim().length > 0);
              setCursorRaw(0);
            }}
            onKeyDown={onKeyDown}
            onFocus={() => { if (q) setOpen(true); }}
            placeholder={tr("searchPh")}
            className="h-7 w-52 rounded-full border border-border bg-surface pl-7 pr-3 text-xs text-fg outline-none transition-colors placeholder:text-muted focus:border-accent"
          />
        </div>
        {/* 命中导航：面板关闭且有搜索词时显示，未定位前只显示总数 */}
        {q && matches.length > 0 && (
          <div className="flex items-center gap-0.5 rounded-full border border-border bg-surface px-1 py-0.5">
            <button
              onClick={() => onStep(-1)}
              title={tr("searchPrev")}
              className="flex h-5 w-5 cursor-pointer items-center justify-center rounded-full text-fg-secondary transition-colors hover:bg-surface-hover hover:text-accent"
            >
              <ChevronLeft size={13} />
            </button>
            <span className="tnum min-w-9 text-center text-[10px] text-muted">
              {activeIdx >= 0 ? `${activeIdx + 1}/${matches.length}` : matches.length}
            </span>
            <button
              onClick={() => onStep(1)}
              title={tr("searchNext")}
              className="flex h-5 w-5 cursor-pointer items-center justify-center rounded-full text-fg-secondary transition-colors hover:bg-surface-hover hover:text-accent"
            >
              <ChevronRight size={13} />
            </button>
          </div>
        )}
        {q && (
          <button
            onClick={() => { onClear(); inputRef.current?.focus(); }}
            title={tr("searchClear")}
            className="flex h-5 w-5 cursor-pointer items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-hover hover:text-fg"
          >
            <X size={13} />
          </button>
        )}
      </div>

      {/* 结果面板：portal 锚定（页面在 overflow 容器内滚动时跟随不被裁剪） */}
      {open && q && (
        <AnchoredPopover
          anchorRef={rootRef}
          open
          onClose={() => setOpen(false)}
          placement="bottom-end"
          className="z-30 w-[400px] max-w-[calc(100vw-2.5rem)] overflow-hidden rounded-[10px] border border-border bg-surface shadow-lg"
        >
          <div className="border-b border-border-light px-3 py-1.5 text-[10px] text-muted">
            {matches.length > 0
              ? tr("searchResultCount").replace("%n", String(matches.length))
                + (matches.length > SEARCH_RESULT_LIMIT
                  ? ` · ${tr("searchTruncated").replace("%n", String(SEARCH_RESULT_LIMIT))}`
                  : "")
              : tr("searchEmpty").replace("%q", q)}
          </div>
          {visible.length > 0 && (
            <div ref={listRef} className="max-h-[320px] overflow-y-auto p-1">
              {visible.map((n, i) => {
                const group = groupOf(n);
                const hit = n.id === activeMatchId;
                return (
                  <button
                    key={n.id}
                    data-cursor={i === cursor}
                    onMouseEnter={() => setCursorRaw(i)}
                    onClick={() => pick(n)}
                    className={cn(
                      "flex w-full cursor-pointer items-center gap-2 rounded-[7px] px-2 py-1.5 text-left transition-colors",
                      i === cursor ? "bg-accent-soft" : "hover:bg-surface-hover",
                    )}
                  >
                    <span
                      className="size-2 shrink-0 rounded-full"
                      style={{ background: stateColor(n.mastery?.state ?? "") }}
                      aria-hidden
                    />
                    <span className={cn("truncate text-xs", hit ? "font-semibold text-accent-strong" : "text-fg")}>
                      {n.name}
                    </span>
                    <span className="ml-auto flex shrink-0 items-center gap-1.5 text-[10px] text-muted">
                      {n.subject && <span>{n.subject}</span>}
                      {group && <span className="max-w-32 truncate">{group.name}</span>}
                      <span className="tnum">{Math.round((n.mastery?.p_known ?? 0) * 100)}%</span>
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          <div className="border-t border-border-light px-3 py-1 text-[10px] text-muted">
            {tr("searchScopeHint", "")}
          </div>
        </AnchoredPopover>
      )}
    </div>
  );
}
