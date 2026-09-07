"use client";

// /docs 使用文档：全员可读（复用 chat 的 Markdown 渲染，GFM/公式零新依赖）。
// 浏览态带标题锚点目录：xl+ 右侧常驻栏随滚动高亮当前小节，窄屏为可折叠目录；
// 管理员可页内编辑（textarea + 实时预览 + 保存 → PUT /docs/content）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, Check, ChevronDown, Pencil, X } from "lucide-react";
import { Markdown } from "@/components/chat/markdown";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
import { getDocsContent, putDocsContent } from "@/lib/api-modules";
import { relTime } from "@/lib/format";
import { extractToc } from "@/lib/markdown-toc";
import { makePageT } from "@/lib/i18n-page";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import type { Lang } from "@/lib/i18n";
import { STRINGS } from "./strings";

export default function DocsPage() {
  const { lang } = useUIStore();
  const user = useAuthStore((s) => s.user);
  const tr = makePageT(lang, STRINGS);
  const isAdmin = user?.role === "admin";

  const [doc, setDoc] = useState<{ markdown: string; updated_at: number; updated_by: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [showPreview, setShowPreview] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const articleRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef(0);
  const toc = useMemo(() => extractToc(doc?.markdown ?? ""), [doc?.markdown]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tocOpen, setTocOpen] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(false);
    getDocsContent()
      .then((r) => {
        setDoc({ markdown: r.markdown, updated_at: r.updated_at, updated_by: r.updated_by });
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => load());
  }, [load]);

  // 文档内容变化 → 重置目录状态（首个小节默认高亮）。渲染期调整，避免 effect 级联渲染。
  const [tocEpoch, setTocEpoch] = useState(toc);
  if (tocEpoch !== toc) {
    setTocEpoch(toc);
    setActiveId(toc.length ? toc[0].id : null);
    setTocOpen(false);
  }

  useEffect(() => () => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
  }, []);

  const jumpTo = useCallback((id: string) => {
    setTocOpen(false);
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // 滚动时高亮目录中当前所在小节（rAF 节流；只认正文内的锚点元素）。
  const onScroll = useCallback(() => {
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      const container = scrollRef.current;
      const article = articleRef.current;
      if (!container || !article || !toc.length) return;
      const baseline = container.getBoundingClientRect().top + 120;
      let current: string | null = null;
      for (const item of toc) {
        const el = document.getElementById(item.id);
        if (!el || !article.contains(el)) continue;
        if (el.getBoundingClientRect().top <= baseline) current = item.id;
        else break;
      }
      setActiveId(current);
    });
  }, [toc]);

  const tocList = (
    <ul>
      {toc.map((item, i) => {
        const active = activeId === item.id;
        return (
          <li key={`${i}-${item.id}`}>
            <a
              href={`#${item.id}`}
              onClick={(e) => {
                e.preventDefault();
                jumpTo(item.id);
              }}
              title={item.text}
              className={`block truncate border-l py-1 pr-2 text-[13px] leading-relaxed transition-colors ${
                active
                  ? "border-accent font-medium text-accent"
                  : "border-border-light text-fg-tertiary hover:text-accent"
              }`}
              style={{ paddingLeft: `${(item.depth - 1) * 12 + 10}px` }}
            >
              {item.text}
            </a>
          </li>
        );
      })}
    </ul>
  );

  const startEdit = () => {
    setDraft(doc?.markdown ?? "");
    setSaveError(false);
    setEditing(true);
  };

  const save = async () => {
    setSaving(true);
    setSaveError(false);
    try {
      const r = await putDocsContent(draft);
      setDoc({ markdown: r.markdown, updated_at: r.updated_at, updated_by: r.updated_by });
      setEditing(false);
    } catch {
      setSaveError(true);
    } finally {
      setSaving(false);
    }
  };

  const showToc = !loading && !error && !editing && toc.length > 0;

  return (
    <div ref={scrollRef} onScroll={onScroll} className="h-full overflow-y-auto p-6 page-in">
      <div className="mx-auto flex w-full max-w-[1080px] items-start gap-8">
        <div
          ref={articleRef}
          className={`min-w-0 flex-1 xl:max-w-[860px] ${showToc ? "" : "mx-auto max-w-[880px]"}`}
        >
          <div className="flex flex-col gap-4">
            <header className="flex items-end justify-between gap-3">
              <div>
                <h1 className="font-serif text-xl font-semibold text-fg">{tr("docs.title")}</h1>
                <p className="mt-0.5 text-xs text-muted">{tr("docs.desc")}</p>
              </div>
              {isAdmin && !editing && (
                <Button size="sm" variant="outline" icon={<Pencil size={13} />} onClick={startEdit}>
                  {tr("docs.edit")}
                </Button>
              )}
            </header>

            {loading && <PageSkeleton />}
            {!loading && error && <ErrorNote message={tr("docs.loadFail")} retry={load} />}

            {showToc && (
              <div className="xl:hidden">
                <button
                  type="button"
                  aria-expanded={tocOpen}
                  onClick={() => setTocOpen((v) => !v)}
                  className="flex w-full items-center justify-between rounded-[8px] border border-border-light bg-surface px-3 py-2 text-[13px] font-medium text-fg-secondary transition-colors hover:border-accent hover:text-accent"
                >
                  {tr("docs.toc")}
                  <ChevronDown
                    size={14}
                    className={`transition-transform duration-300 ${tocOpen ? "rotate-180" : ""}`}
                  />
                </button>
                {tocOpen && (
                  <div className="mt-2 rounded-[8px] border border-border-light bg-surface px-3 py-2">
                    {tocList}
                  </div>
                )}
              </div>
            )}

            {!loading && !error && editing && (
              <Card>
                <CardHeader
                  icon={<Pencil size={16} />}
                  title={tr("docs.edit")}
                  right={
                    <div className="flex items-center gap-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setShowPreview((v) => !v)}
                      >
                        {showPreview ? tr("docs.source") : tr("docs.preview")}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        icon={<X size={13} />}
                        disabled={saving}
                        onClick={() => setEditing(false)}
                      >
                        {tr("docs.cancel")}
                      </Button>
                      <Button
                        size="sm"
                        icon={saving ? undefined : <Check size={13} />}
                        disabled={saving}
                        onClick={() => void save()}
                      >
                        {saving ? tr("docs.saving") : tr("docs.save")}
                      </Button>
                    </div>
                  }
                />
                {saveError && (
                  <div className="mb-3">
                    <ErrorNote message={tr("docs.saveFail")} />
                  </div>
                )}
                <div className={showPreview ? "grid grid-cols-1 gap-4 lg:grid-cols-2" : ""}>
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    placeholder={tr("docs.ph")}
                    spellCheck={false}
                    className="h-[62vh] min-h-[320px] w-full resize-none rounded-[8px] border border-border-light bg-surface-sunken p-3 font-mono text-[13px] leading-relaxed text-fg outline-none focus:border-accent"
                  />
                  {showPreview && (
                    <div className="h-[62vh] min-h-[320px] overflow-y-auto rounded-[8px] border border-border-light bg-surface p-3">
                      <Markdown>{draft || tr("docs.ph")}</Markdown>
                    </div>
                  )}
                </div>
              </Card>
            )}

            {!loading && !error && !editing && doc && (
              <Card>
                <CardHeader icon={<BookOpen size={16} />} title={tr("docs.title")} />
                {doc.markdown ? (
                  <Markdown anchorHeadings>{doc.markdown}</Markdown>
                ) : (
                  <p className="text-sm text-muted">{tr("docs.empty")}</p>
                )}
                {doc.updated_at > 0 && (
                  <div className="mt-2 border-t border-border-light pt-2 text-[11px] text-muted">
                    {tr("docs.updated")} {relTime(doc.updated_at, lang as Lang)}
                    {doc.updated_by && (
                      <>
                        {" "}{tr("docs.updatedBy")} {doc.updated_by}
                      </>
                    )}
                  </div>
                )}
              </Card>
            )}
          </div>
        </div>

        {showToc && (
          <nav aria-label={tr("docs.toc")} className="sticky top-6 hidden w-52 shrink-0 xl:block">
            <p className="border-b border-border-light pb-2 text-[11px] font-semibold tracking-wide text-muted">
              {tr("docs.toc")}
            </p>
            <div className="mt-3 max-h-[calc(100vh-8rem)] overflow-y-auto pr-1">{tocList}</div>
          </nav>
        )}
      </div>
    </div>
  );
}
