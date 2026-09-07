"use client";
// 自研 Markdown 编辑器（用户选型）：textarea + 完整工具栏 + [[wiki 自动补全。
// 关键实现点：
//  - 所有插入走 document.execCommand("insertText")：保留原生 undo 栈
//    （setRangeText 会清空 undo，Ctrl+Z 就救不回来了）。
//  - execCommand 已废弃但所有主流浏览器仍支持；失败时回退 setRangeText。
//  - 键入 "[[" 时弹出已有笔记标题过滤下拉，Enter 选中，无匹配时可新建。
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bold, Code, Heading1, Heading2, Heading3, Image as ImageIcon,
  Italic, Link2, List, ListOrdered, ListTodo, Minus, Pi, Quote, SquareCode,
  SquareRadical, Strikethrough, Table, Braces,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { applyAction, type EditorAction } from "./editorActions";

export interface ToolbarButtonDef {
  action: EditorAction;
  icon: typeof Bold;
  title: string;
}

export function makeToolbar(tr: (k: string) => string): ToolbarButtonDef[] {
  return [
    { action: "bold", icon: Bold, title: tr("ed.bold") },
    { action: "italic", icon: Italic, title: tr("ed.italic") },
    { action: "strike", icon: Strikethrough, title: tr("ed.strike") },
    { action: "code", icon: Code, title: tr("ed.code") },
    { action: "codeblock", icon: SquareCode, title: tr("ed.codeblock") },
    { action: "h1", icon: Heading1, title: tr("ed.h1") },
    { action: "h2", icon: Heading2, title: tr("ed.h2") },
    { action: "h3", icon: Heading3, title: tr("ed.h3") },
    { action: "quote", icon: Quote, title: tr("ed.quote") },
    { action: "ul", icon: List, title: tr("ed.ul") },
    { action: "ol", icon: ListOrdered, title: tr("ed.ol") },
    { action: "task", icon: ListTodo, title: tr("ed.task") },
    { action: "table", icon: Table, title: tr("ed.table") },
    { action: "hr", icon: Minus, title: tr("ed.hr") },
    { action: "link", icon: Link2, title: tr("ed.link") },
    { action: "image", icon: ImageIcon, title: tr("ed.image") },
    { action: "math", icon: Pi, title: tr("ed.math") },
    { action: "mathblock", icon: SquareRadical, title: tr("ed.mathblock") },
    { action: "wikilink", icon: Braces, title: tr("ed.wikilink") },
  ];
}

export interface EditorResourceLink { id: string; title: string; url: string; kind: "note" | "session" | "notes_thread"; }

interface MarkdownEditorProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  noteTitles: string[];
  toolbar: ToolbarButtonDef[];
  createWikiLabel?: string;
  onTriggerCreateWiki?: (title: string) => void;
  onScrollRatioChange?: (ratio: number) => void;
  scrollRatio?: number;
  resourceLinks?: EditorResourceLink[];
}

export function MarkdownEditor({
  value, onChange, placeholder, noteTitles, toolbar, createWikiLabel,
  onTriggerCreateWiki, onScrollRatioChange, scrollRatio, resourceLinks = [],
}: MarkdownEditorProps) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const [wikiQuery, setWikiQuery] = useState<string | null>(null);
  const [wikiStart, setWikiStart] = useState(0);
  const [wikiIndex, setWikiIndex] = useState(0);
  const [resourceOpen, setResourceOpen] = useState(false);
  const [resourceQuery, setResourceQuery] = useState("");

  const matches = useMemo(() => {
    if (wikiQuery === null) return [];
    const q = wikiQuery.toLowerCase();
    return noteTitles.filter((t) => t.toLowerCase().includes(q)).slice(0, 8);
  }, [wikiQuery, noteTitles]);

  /** 通用补丁应用：算出 (from, oldText → newText) 差分区间，先选中旧区间
   *  再 execCommand("insertText")（保留 undo 栈）；不支持时回退受控替换。 */
  const applyEdit = (result: { value: string; start: number; end: number }) => {
    const ta = taRef.current;
    if (!ta) return;
    let from = 0;
    const maxPrefix = Math.min(value.length, result.value.length);
    while (from < maxPrefix && value[from] === result.value[from]) from++;
    let suffix = 0;
    while (
      suffix < maxPrefix - from
      && value[value.length - 1 - suffix] === result.value[result.value.length - 1 - suffix]
    ) suffix++;
    const oldText = value.slice(from, value.length - suffix);
    const newText = result.value.slice(from, result.value.length - suffix);
    ta.focus();
    let applied = false;
    if (typeof document !== "undefined"
        && typeof document.execCommand === "function" && oldText !== newText) {
      ta.setSelectionRange(from, from + oldText.length);
      applied = document.execCommand("insertText", false, newText);
    }
    void applied; // execCommand 失败时受控替换兜底（undo 该轮丢失，可接受）
    onChange(result.value);
    requestAnimationFrame(() => {
      ta.selectionStart = result.start;
      ta.selectionEnd = result.end;
    });
  };

  const runAction = (action: EditorAction) => {
    const ta = taRef.current;
    if (!ta) return;
    const state = { value, start: ta.selectionStart, end: ta.selectionEnd };
    applyEdit(applyAction(state, action));
  };

  const insertResource = (resource: EditorResourceLink) => {
    const ta = taRef.current;
    if (!ta) return;
    const state = { value, start: ta.selectionStart, end: ta.selectionEnd };
    const text = `[${resource.title}](${resource.url})`;
    applyEdit({ value: value.slice(0, state.start) + text + value.slice(state.end), start: state.start + text.length, end: state.start + text.length });
    setResourceOpen(false);
    setResourceQuery("");
  };
  const filteredResources = resourceLinks.filter((resource) => !resourceQuery.trim() || resource.title.toLowerCase().includes(resourceQuery.trim().toLowerCase())).slice(0, 20);

  // --- [[ 自动补全 -----------------------------------------------------------

  const maybeOpenWiki = (ta: HTMLTextAreaElement) => {
    const pos = ta.selectionStart;
    const before = value.slice(Math.max(0, pos - 60), pos);
    const m = /\[\[([^\[\]]*)$/.exec(before);
    if (m) {
      setWikiQuery(m[1]);
      setWikiStart(pos - m[1].length);
      setWikiIndex(0);
    } else if (wikiQuery !== null) {
      setWikiQuery(null);
    }
  };

  const closeWiki = () => setWikiQuery(null);

  const commitWiki = (title: string) => {
    const ta = taRef.current;
    if (!ta || wikiQuery === null) return;
    const pos = ta.selectionStart;
    const after = value.slice(pos);
    const closing = after.startsWith("]]") ? "" : "]]";
    const next = value.slice(0, wikiStart) + title + closing + after;
    const caret = wikiStart + title.length + closing.length;
    onChange(next);
    setWikiQuery(null);
    requestAnimationFrame(() => {
      ta.focus();
      ta.selectionStart = caret;
      ta.selectionEnd = caret;
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const ta = e.currentTarget;
    // wiki 补全导航
    if (wikiQuery !== null && matches.length >= 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setWikiIndex((i) => Math.min(i + 1, Math.max(matches.length - 1, 0)));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setWikiIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        if (matches[wikiIndex]) commitWiki(matches[wikiIndex]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closeWiki();
        return;
      }
    }
    // 快捷键
    if (e.ctrlKey || e.metaKey) {
      const key = e.key.toLowerCase();
      if (key === "b") { e.preventDefault(); runAction("bold"); return; }
      if (key === "i") { e.preventDefault(); runAction("italic"); return; }
      if (key === "k") { e.preventDefault(); runAction("wikilink"); return; }
    }
    // Tab 缩进
    if (e.key === "Tab") {
      e.preventDefault();
      const state = { value, start: ta.selectionStart, end: ta.selectionEnd };
      applyEdit({
        value: value.slice(0, state.start) + "  " + value.slice(state.end),
        start: state.start + 2, end: state.start + 2,
      });
    }
  };

  // --- 同步滚动（分屏模式：跟随预览侧的滚动比例）-------------------------------

  useEffect(() => {
    const ta = taRef.current;
    if (!ta || scrollRatio === undefined) return;
    ta.scrollTop = scrollRatio * (ta.scrollHeight - ta.clientHeight);
  }, [scrollRatio]);

  const handleScroll = () => {
    const ta = taRef.current;
    if (!ta) return;
    const denom = ta.scrollHeight - ta.clientHeight;
    onScrollRatioChange?.(denom > 0 ? ta.scrollTop / denom : 0);
  };

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      {/* 工具栏 */}
      <div className="flex flex-wrap items-center gap-0.5 border-b border-border bg-surface px-2 py-1.5">
        {toolbar.map(({ action, icon: Icon, title }) => (
          <button
            key={action}
            type="button"
            title={title}
            aria-label={title}
            onMouseDown={(e) => e.preventDefault()} // 保持 textarea 焦点
            onClick={() => runAction(action)}
            className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-[6px] text-muted transition-colors hover:bg-surface-hover hover:text-accent"
          >
            <Icon size={15} />
          </button>
        ))}
        <button type="button" title="插入资源链接" aria-label="插入资源链接" onMouseDown={(e) => e.preventDefault()} onClick={() => setResourceOpen((open) => !open)} className="ml-1 flex h-7 cursor-pointer items-center gap-1 rounded-[6px] border border-border px-1.5 text-[10px] text-muted transition-colors hover:border-accent hover:text-accent"><Link2 size={13} />资源</button>
      </div>
      {resourceOpen && <div className="motion-pop absolute left-3 top-11 z-30 w-80 rounded-[10px] border border-border bg-surface p-2 shadow-lg">
        <input autoFocus value={resourceQuery} onChange={(e) => setResourceQuery(e.target.value)} placeholder="搜索笔记或对话" className="mb-1.5 h-8 w-full rounded-md border border-border bg-bg px-2 text-xs outline-none focus:border-accent" />
        <div className="max-h-64 overflow-y-auto">{filteredResources.length ? filteredResources.map((resource) => <button key={resource.url} onMouseDown={(e) => { e.preventDefault(); insertResource(resource); }} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-surface-hover"><span className="rounded bg-accent-soft px-1 py-0.5 text-[9px] text-accent-strong">{resource.kind}</span><span className="truncate">{resource.title}</span></button>) : <div className="px-2 py-3 text-center text-xs text-muted">没有匹配资源</div>}</div>
      </div>}
      {/* 编辑区 */}
      <textarea
        ref={taRef}
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        onChange={(e) => {
          onChange(e.target.value);
          maybeOpenWiki(e.target);
        }}
        onKeyDown={handleKeyDown}
        onScroll={handleScroll}
        onBlur={() => setTimeout(closeWiki, 120)}
        className={cn(
          "min-h-0 flex-1 resize-none bg-surface px-4 py-3",
          "font-mono text-[0.85rem] leading-relaxed text-fg outline-none",
        )}
      />
      {/* wiki 自动补全下拉 */}
      {wikiQuery !== null && (
        <div className="absolute bottom-16 left-4 z-20 w-72 rounded-[10px] border border-border bg-surface p-1 shadow-lg">
          {matches.length > 0 ? (
            matches.map((title, idx) => (
              <button
                key={title}
                type="button"
                onMouseDown={(e) => { e.preventDefault(); commitWiki(title); }}
                className={cn(
                  "block w-full cursor-pointer truncate rounded-md px-2.5 py-1.5 text-left text-xs",
                  idx === wikiIndex
                    ? "bg-accent-soft text-accent-strong"
                    : "text-fg-secondary hover:bg-surface-hover",
                )}
              >
                [[{title}]]
              </button>
            ))
          ) : (
            <button
              type="button"
              onMouseDown={(e) => {
                e.preventDefault();
                onTriggerCreateWiki?.(wikiQuery.trim());
                closeWiki();
              }}
              className="block w-full cursor-pointer rounded-md px-2.5 py-1.5 text-left text-xs text-accent hover:bg-surface-hover"
            >
              + {createWikiLabel ?? "新建笔记"}「{wikiQuery}」
            </button>
          )}
        </div>
      )}
    </div>
  );
}
