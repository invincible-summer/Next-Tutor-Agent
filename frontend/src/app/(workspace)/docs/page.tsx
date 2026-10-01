"use client";
import { navigationSucceeded, navigationFailed } from "@/lib/assistant/navigation";


// /docs 使用文档：全员可读（复用 chat 的 Markdown 渲染，GFM/公式零新依赖）。
// 浏览态带标题锚点目录：xl+ 右侧常驻栏随滚动高亮当前小节，窄屏为可折叠目录；
// 管理员可页内编辑（textarea + 实时预览 + 保存 → PUT /docs/content）。
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, BookOpen, Check, ChevronDown, MessageCircle, Presentation, Pencil, X } from "lucide-react";
import { Markdown } from "@/components/chat/markdown";
import { Textarea } from "@/components/ui/Input";
import styles from "./docs.module.css";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
import { getDocsContent, putDocsContent } from "@/lib/api-modules";
import { API_BASE } from "@/lib/api";
import { relTime } from "@/lib/format";
import { extractToc } from "@/lib/markdown-toc";
import { makePageT } from "@/lib/i18n-page";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import type { Lang } from "@/lib/i18n";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import { DeepLinkQueryReader } from "@/lib/assistant/deep-link";
import { STRINGS } from "./strings";

export default function DocsPage() {
  const { lang } = useUIStore();
  const user = useAuthStore((s) => s.user);
  const tr = makePageT(lang, STRINGS);
  const isAdmin = user?.role === "admin";

  const [doc, setDoc] = useState<{ markdown: string; updated_at: number; updated_by: string; show_manual?: boolean; show_manual_url?: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  // 助手深链（§20.1 使用文档）：?section={section_id} 定位小节锚点。
  const [deepSection, setDeepSection] = useState("");
  const [deepNotice, setDeepNotice] = useState("");
  useAssistantPage({
    navigationStatus: (target) => {
      if (loading) return null;
      if (error || !doc) return navigationFailed;
      if (target.kind === "module") return navigationSucceeded;
      if (target.kind !== "docs_section" || view !== "manual") return null;
      const element = document.getElementById(target.section_id);
      if (!element || !element.getClientRects().length) return null;
      element.scrollIntoView({ block: "start" });
      return navigationSucceeded;
    },
    context: () => ({
      schema_version: 1,
      route_id: "docs",
      route_epoch: currentRouteEpoch(),
      view,
    }),
  });
  const applyDeepLink = useCallback((params: Record<string, string>) => {
    setDeepSection(params.section || "");
  }, []);

  // 「使用手册 / 演示手册」双视图：演示手册是渲染版 PDF（iframe /docs/show）。
  const [view, setView] = useState<"manual" | "show">("manual");

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [draftLang, setDraftLang] = useState<Lang>(lang);
  const [showPreview, setShowPreview] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const loadSequence = useRef(0);

  const scrollRef = useRef<HTMLDivElement>(null);
  const articleRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef(0);
  const toc = useMemo(() => extractToc(doc?.markdown ?? ""), [doc?.markdown]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tocOpen, setTocOpen] = useState(false);

  const load = useCallback(() => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setError(false);
    getDocsContent(lang)
      .then((r) => {
        if (sequence !== loadSequence.current) return;
        setDoc((previous) => ({ ...previous, ...r }));
      })
      .catch(() => { if (sequence === loadSequence.current) setError(true); })
      .finally(() => { if (sequence === loadSequence.current) setLoading(false); });
  }, [lang]);

  useEffect(() => {
    void Promise.resolve().then(() => load());
    return () => { loadSequence.current += 1; };
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

  // 深链定位：文档与目录就绪后跳到小节锚点；找不到给温和提示（§8.3）。
  useEffect(() => {
    if (!deepSection || loading || !doc) return;
    const section = deepSection;
    // 消费走微任务：避免 effect 体内同步 setState 的级联渲染
    // （react-hooks/set-state-in-effect）。
    let alive = true;
    queueMicrotask(() => {
      if (!alive) return;
      setDeepSection("");
      if (view !== "manual") return;
      const exists = toc.some((item) => item.id === section)
        || Boolean(document.getElementById(section));
      if (!exists) {
        setDeepNotice(tr("deep.sectionMissing"));
        return;
      }
      jumpTo(section);
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepSection, loading, doc, toc]);

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
              aria-current={active ? "location" : undefined}
              className={`block border-l py-1.5 pr-2 text-[13px] leading-relaxed transition-colors ${
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
    setDraftLang(lang);
    setSaveError(false);
    setEditing(true);
  };

  const save = async () => {
    setSaving(true);
    setSaveError(false);
    try {
      const r = await putDocsContent(draft, draftLang);
      if (useUIStore.getState().lang === draftLang) {
        loadSequence.current += 1;
        setDoc((previous) => ({ ...previous, ...r }));
        setLoading(false);
        setError(false);
      }
      setEditing(false);
    } catch {
      setSaveError(true);
    } finally {
      setSaving(false);
    }
  };

  const hasShow = !loading && !error && !!doc?.show_manual;
  const showToc = !loading && !error && !editing && view === "manual" && toc.length > 0;

  return (
    <div ref={scrollRef} onScroll={onScroll} className="h-full overflow-y-auto bg-surface-sunken/40 px-6 py-8 page-in">
      <Suspense><DeepLinkQueryReader keys={["section"]} onParams={applyDeepLink} /></Suspense>
      <div className="mx-auto flex w-full max-w-[1200px] items-start gap-10">
        <div
          ref={articleRef}
          className={`min-w-0 flex-1 xl:max-w-[860px] ${showToc ? "" : "mx-auto max-w-[880px]"}`}
        >
          <div className="flex flex-col gap-6">
            {deepNotice && (
              <div className="rounded-[8px] border border-border bg-surface px-3 py-2 text-xs text-muted">{deepNotice}</div>
            )}
            <header className="flex items-start justify-between gap-4 border-b border-border-light pb-6">
              <div>
                <p className="mb-3 text-[10px] font-semibold tracking-[0.22em] text-accent">NEXT TUTOR / FIELD GUIDE</p>
                <h1 className="font-serif text-3xl font-semibold tracking-tight text-fg">{tr("docs.title")}</h1>
                <p className="mt-3 max-w-lg text-sm leading-7 text-fg-secondary">{tr("docs.desc")}</p>
                {hasShow ? (
                  <div
                    role="tablist"
                    aria-label={tr("docs.title")}
                    className="mt-1.5 flex w-fit rounded-[8px] border border-border-light bg-surface p-0.5"
                  >
                    {(["manual", "show"] as const).map((v) => (
                      <button
                        key={v}
                        type="button"
                        role="tab"
                        aria-selected={view === v}
                        disabled={editing}
                        onClick={() => setView(v)}
                        className={`rounded-[6px] px-3 py-1 text-xs font-medium transition-colors ${
                          view === v
                            ? "bg-accent/10 text-accent"
                            : "text-fg-tertiary hover:text-fg-secondary"
                        }`}
                      >
                        {tr(v === "manual" ? "docs.tabManual" : "docs.tabShow")}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
              {isAdmin && !editing && view === "manual" && (
                <Button size="sm" variant="outline" icon={<Pencil size={13} />} disabled={loading || error || !doc} onClick={startEdit}>
                  {tr("docs.edit")}
                </Button>
              )}
            </header>

            {!editing && loading && <PageSkeleton />}
            {!editing && !loading && error && <ErrorNote message={tr("docs.loadFail")} retry={load} />}

            {!loading && !error && !editing && view === "manual" && (
              <div className="grid grid-cols-3 gap-3">
                {([
                  { href: "/resources", icon: BookOpen, title: "docs.startMaterials", desc: "docs.startMaterialsDesc" },
                  { href: "/chat", icon: MessageCircle, title: "docs.startChat", desc: "docs.startChatDesc" },
                  { href: "/course", icon: Presentation, title: "docs.startCourse", desc: "docs.startCourseDesc" },
                ] as const).map(({ href, icon: Icon, title, desc }, index) => (
                  <Link key={href} href={href} className="group rounded-2xl border border-border-light bg-surface p-4 transition-colors hover:border-accent/50 hover:bg-accent/5">
                    <div className="mb-5 flex items-center justify-between text-accent">
                      <Icon size={19} strokeWidth={1.5} />
                      <span className="font-mono text-[10px] text-muted">0{index + 1}</span>
                    </div>
                    <div className="flex items-center justify-between gap-2 text-sm font-medium text-fg">
                      {tr(title)}<ArrowUpRight size={14} className="shrink-0 text-muted group-hover:text-accent" />
                    </div>
                    <p className="mt-2 text-xs leading-5 text-fg-tertiary">{tr(desc)}</p>
                  </Link>
                ))}
              </div>
            )}

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

            {editing && (
              <Card>
                <CardHeader
                  icon={<Pencil size={16} />}
                  title={tr("docs.edit")}
                  desc={tr("docs.editingLocale").replace("{lang}", draftLang === "en" ? "English" : "中文")}
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
                  <Textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    placeholder={tr("docs.ph")}
                    spellCheck={false}
                    aria-label={tr("docs.source")}
                    className="h-[62vh] min-h-[320px] resize-none font-mono text-[13px] leading-relaxed"
                  />
                  {showPreview && (
                    <div className="h-[62vh] min-h-[320px] overflow-y-auto rounded-[8px] border border-border-light bg-surface p-3">
                      <Markdown>{draft || tr("docs.ph")}</Markdown>
                    </div>
                  )}
                </div>
              </Card>
            )}

            {!loading && !error && !editing && doc && view === "show" && doc.show_manual && (
              <Card pad={false} className="overflow-hidden">
                <iframe
                  src={doc.show_manual_url ?? `${API_BASE}/docs/show`}
                  title={tr("docs.showTitle")}
                  // 静态手册只需脚本与同源资源：挡顶层导航/弹窗/表单提交，
                  // 后端数据被污染时也不以完整权限嵌入。
                  sandbox="allow-scripts allow-same-origin"
                  loading="lazy"
                  className="h-[calc(100vh-180px)] min-h-[480px] w-full border-0 bg-[#101418]"
                />
              </Card>
            )}

            {!loading && !error && !editing && doc && view === "manual" && (
              <article className={`${styles.reading} rounded-2xl border border-border-light bg-surface px-8 py-9 lg:px-10`}>
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
              </article>
            )}
          </div>
        </div>

        {showToc && (
          <nav aria-label={tr("docs.toc")} className="sticky top-8 hidden w-56 shrink-0 xl:block">
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
