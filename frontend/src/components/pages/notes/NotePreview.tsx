"use client";
// 笔记预览：复用 chat 的 Markdown 渲染器（GFM + KaTeX 白得），前置处理：
//  - [[标题]] / [[标题|别名]] → 内部 wikilink 链接（note:// 协议），点击
//    由父级拦截（已存在 → 打开；未解析 → 建新笔记）。
//  - #标签 → 可点击 chip（note://tag/xxx），父级过滤列表。
// 底部渲染反向链接与出链（Obsidian 的核心体验）。
import { useMemo } from "react";
import { ArrowLeft, Link2 } from "lucide-react";
import { Markdown } from "@/components/chat/markdown";
import type { NoteDetail, NoteSummary } from "@/lib/types-notes";

const WIKI_RE = /\[\[([^\[\]|]+)(?:\|([^\[\]]+))?\]\]/g;
// 行首的 # 是标题，不当作标签；要求前面是空白或行首
const RESOURCE_RE = /(?<!\]\()(?<!href=["'])(note:\/\/[^\s)<>]+|conversation:\/\/(?:session|notes)\/[^\s)<>]+)/g;
const TAG_RE = /(^|\s)#([A-Za-z0-9_\-\u4e00-\u9fff]{1,24})/g;

export function preprocessWiki(content: string): string {
  let out = content.replace(WIKI_RE, (_m, title: string, alias?: string) => {
    const t = encodeURIComponent(title.trim());
    const label = (alias || title).trim();
    return `[${label}](note://${t})`;
  });
  out = out.replace(RESOURCE_RE, (url) => {
    const label = url.startsWith("note://") ? "笔记链接"
      : url.startsWith("conversation://session/") ? "历史对话" : "助手线程";
    return `[${label} · ${url.split("/").pop()}](${url})`;
  });
  out = out.replace(TAG_RE, (m, lead: string, tag: string) => {
    // 跳过 markdown 标题行（# 开头的行首已由 ^ 匹配，但标题无空格后缀词）
    return `${lead}[#${tag}](note://tag/${encodeURIComponent(tag)})`;
  });
  return out;
}

export function NotePreview({
  content,
  className,
  onWikiLink,
  onTagClick,
  onResourceLink,
}: {
  content: string;
  className?: string;
  onWikiLink?: (title: string) => void;
  onTagClick?: (tag: string) => void;
  onResourceLink?: (url: string) => void;
}) {
  const processed = useMemo(() => preprocessWiki(content), [content]);
  return (
    <div
      className={className}
      onClick={(e) => {
        const anchor = (e.target as HTMLElement).closest("a");
        const href = anchor?.getAttribute("href") || "";
        if (!href.startsWith("note://") && !href.startsWith("conversation://")) return;
        e.preventDefault();
        if (href.startsWith("conversation://") || /^note:\/\/note_/.test(href)) {
          onResourceLink?.(href);
        } else if (href.startsWith("note://tag/")) {
          onTagClick?.(decodeURIComponent(href.slice("note://tag/".length)));
        } else {
          onWikiLink?.(decodeURIComponent(href.slice("note://".length)));
        }
      }}
    >
      <Markdown className="chat-prose">{processed}</Markdown>
    </div>
  );
}

/** 反向链接 + 出链（预览底部）。 */
export function BacklinksPanel({
  detail,
  onOpenNote,
  onCreateNote,
  onOpenResource,
}: {
  detail: NoteDetail;
  onOpenNote: (id: string) => void;
  onCreateNote: (title: string) => void;
  onOpenResource?: (url: string) => void;
}) {
  const { backlinks, links } = detail;
  if (backlinks.length === 0 && links.resolved.length === 0
      && links.unresolved.length === 0) {
    return null;
  }
  return (
    <div className="mt-6 space-y-3 border-t border-border pt-4 text-xs">
      {backlinks.length > 0 && (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 font-medium text-fg-secondary">
            <ArrowLeft size={13} /> 反向链接（{backlinks.length}）
          </div>
          <div className="flex flex-wrap gap-1.5">
            {backlinks.map((b: NoteSummary) => (
              <button
                key={b.id}
                onClick={() => onOpenNote(b.id)}
                className="cursor-pointer rounded-full border border-border bg-surface px-2.5 py-1 text-fg-secondary transition-colors hover:border-accent hover:text-accent"
              >
                {b.title}
              </button>
            ))}
          </div>
        </div>
      )}
      {(links.resources?.length ?? 0) > 0 && (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 font-medium text-fg-secondary"><Link2 size={13} /> 资源链接</div>
          <div className="grid gap-1.5">
            {links.resources?.map((resource) => (
              <button key={resource.url} disabled={!resource.resolved} onClick={() => onOpenResource?.(resource.url)} className={`rounded-md border px-2.5 py-2 text-left ${resource.resolved ? "cursor-pointer border-border bg-surface hover:border-accent" : "cursor-not-allowed border-danger/40 bg-danger/5"}`}>
                <div className="font-medium text-fg-secondary">{resource.title}</div>
                <div className="mt-0.5 text-[10px] text-muted">{resource.type} · {resource.resolved ? `${resource.message_count ?? resource.folder_name ?? "可访问"}` : resource.status === "deleted" ? "该资源已删除" : "该资源不存在或无法访问"}</div>
              </button>
            ))}
          </div>
        </div>
      )}
      {(links.resolved.length > 0 || links.unresolved.length > 0) && (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 font-medium text-fg-secondary">
            <Link2 size={13} /> 出链（{links.resolved.length + links.unresolved.length}）
          </div>
          <div className="flex flex-wrap gap-1.5">
            {links.resolved.map((l) => (
              <button
                key={l.note_id}
                onClick={() => onOpenNote(l.note_id)}
                className="cursor-pointer rounded-full border border-border bg-surface px-2.5 py-1 text-fg-secondary transition-colors hover:border-accent hover:text-accent"
              >
                {l.title}
              </button>
            ))}
            {links.unresolved.map((title) => (
              <button
                key={title}
                onClick={() => onCreateNote(title)}
                title="点击创建"
                className="cursor-pointer rounded-full border border-dashed border-border px-2.5 py-1 text-muted transition-colors hover:border-accent2 hover:text-accent2"
              >
                {title} ?
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
