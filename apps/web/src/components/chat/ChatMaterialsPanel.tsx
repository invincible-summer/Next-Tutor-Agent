"use client";
import { useState } from "react";
import { BookOpen, ChevronRight, FileText, PanelRightClose, UploadCloud } from "lucide-react";
import { cn } from "@/lib/cn";
import { t, type Lang } from "@/lib/i18n";
import { useUIStore } from "@/lib/store";
import type { MaterialSource } from "@/lib/types";

function SourceRow({ source, lang }: { source: MaterialSource; lang: Lang }) {
  const label = source.source_scope === "workspace" || source.source_scope === "workspace_textbook"
    ? t(lang, "chat.materials.workspace", "工作区共享")
    : source.source_scope === "library"
      ? t(lang, "chat.materials.reference", "本对话引用")
      : t(lang, "chat.materials.session", "本对话上传");
  return (
    <div className="rounded-[8px] border border-border-light bg-surface px-2.5 py-2">
      <div className="flex items-start gap-2">
        <FileText size={14} className="mt-0.5 shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[0.74rem] font-medium text-fg" title={source.filename}>{source.filename}</p>
          <p className="mt-0.5 text-[0.65rem] text-muted">{label}</p>
          <p className="mt-1 text-[0.65rem] text-muted">
            {source.chunk_count ?? 0} {t(lang, "chat.materials.chunks", "个片段")}{source.has_original ? ` · ${t(lang, "chat.materials.original", "可下载原件")}` : ""}
            {source.ocr_used ? ` · ${t(lang, "chat.materials.ocr", "已 OCR")}` : ""}
          </p>
        </div>
        <ChevronRight size={13} className="mt-0.5 shrink-0 text-muted/50" />
      </div>
    </div>
  );
}

export function ChatMaterialsPanel({
  sources,
  open,
  onClose,
}: {
  sources: MaterialSource[];
  open: boolean;
  onClose: () => void;
}) {
  const { lang } = useUIStore();
  // Desktop keeps the historical "open by default" panel. On narrow screens
  // the same default must not mount a full-height modal drawer over the quiz
  // and input controls; the learner opens the drawer explicitly from this
  // compact edge button instead.
  const [mobileOpen, setMobileOpen] = useState(false);
  if (!open) return null;
  const workspace = sources.filter((s) => s.source_scope === "workspace" || s.source_scope === "workspace_textbook");
  const references = sources.filter((s) => s.source_scope === "library");
  const session = sources.filter((s) => !workspace.includes(s) && !references.includes(s));
  const group = (title: string, icon: React.ReactNode, items: MaterialSource[]) => (
    <section className="space-y-1.5">
      <div className="flex items-center gap-1.5 px-1 text-[0.68rem] font-semibold text-fg-secondary">
        {icon}<span>{title}</span><span className="text-muted">{items.length}</span>
      </div>
      {items.length > 0 ? items.map((source) => <SourceRow key={`${source.source_scope}-${source.id}`} source={source} lang={lang} />) : (
        <p className="rounded-[8px] border border-dashed border-border-light px-2.5 py-2 text-[0.68rem] text-muted">{t(lang, "chat.materials.empty", "暂无资料")}</p>
      )}
    </section>
  );
  return (
    <>
      {!mobileOpen && (
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          className="fixed right-2 top-20 z-20 flex h-8 w-8 items-center justify-center rounded-[9px] border border-border bg-bg/95 text-muted shadow-sm hover:bg-surface-hover hover:text-fg md:hidden"
          aria-label={t(lang, "chat.materials.open", "打开资料栏")}
        >
          <BookOpen size={15} />
        </button>
      )}
      {mobileOpen && (
        <div className="fixed inset-0 z-20 bg-black/20 md:hidden" onClick={() => setMobileOpen(false)} />
      )}
      <aside className={cn(
        "relative z-30 w-[270px] shrink-0 flex-col border-l border-border bg-bg/95 p-3 md:flex",
        mobileOpen
          ? "max-md:fixed max-md:right-0 max-md:top-0 max-md:flex max-md:h-full max-md:shadow-xl"
          : "max-md:hidden",
      )} aria-label={t(lang, "chat.materials.title", "当前资料")}>
        <div className="mb-3 flex items-center gap-2">
          <BookOpen size={15} className="text-accent" />
          <h2 className="flex-1 text-[0.8rem] font-semibold text-fg">{t(lang, "chat.materials.title", "当前资料")}</h2>
          <button onClick={() => setMobileOpen(false)} className="rounded p-1 text-muted hover:bg-surface-hover hover:text-fg md:hidden" aria-label={t(lang, "chat.materials.close", "关闭资料栏")}>
            <PanelRightClose size={15} />
          </button>
          <button onClick={onClose} className="rounded p-1 text-muted hover:bg-surface-hover hover:text-fg max-md:hidden" aria-label={t(lang, "chat.materials.close", "关闭资料栏")}>
            <PanelRightClose size={15} />
          </button>
        </div>
        <p className="mb-3 text-[0.68rem] leading-relaxed text-muted">{t(lang, "chat.materials.desc", "只显示当前会话真实可检索的资料来源。")}</p>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto">
          {group(t(lang, "chat.materials.workspace.group", "工作区公共资料"), <BookOpen size={12} className="text-accent" />, workspace)}
          {group(t(lang, "chat.materials.reference.group", "本对话引用教材"), <BookOpen size={12} className="text-warning" />, references)}
          {group(t(lang, "chat.materials.session.group", "本对话上传文件"), <UploadCloud size={12} className="text-success" />, session)}
        </div>
      </aside>
    </>
  );
}
