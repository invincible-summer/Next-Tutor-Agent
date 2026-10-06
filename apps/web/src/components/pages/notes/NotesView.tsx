"use client";
import { navigationAnchor, navigationSucceeded } from "@/lib/assistant/navigation";

// 笔记主视图：引力图首页/图谱 + 编辑/预览 + AI 面板。
// 负责：初始加载、URL 同步（/notes/<id>）、800ms 防抖自动保存 + Ctrl+S、
// 409 冲突处理、来自 AI 面板的远程热更新、编辑/预览/分屏切换、
// 居中「笔记中心」弹窗（文件夹/标签/列表/新建）、AI 面板折叠/拖宽。
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { listSessions } from "@/lib/api";
import { FolderOpen, Minimize2, Network, NotebookPen, Sparkles, X } from "lucide-react";
import { ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
import { INPUT_CLS } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { makePageT } from "@/lib/i18n-page";
import { useUIStore } from "@/lib/store";
import { NOTES_LAYOUT_DEFAULTS, useNotesStore } from "@/lib/store-notes";
import { VAULT_AGENT_KEY } from "@/lib/api-notes";
import {
  createNote, deleteNote, exportNoteFile, exportVaultZip, getNoteTemplates,
  patchNote, createNotesFolder, renameNotesFolder, deleteNotesFolder,
} from "@/lib/api-notes";
import { normalizeAgentMode } from "@/lib/types-notes";
import type { NotesGraph } from "@/lib/types-notes";
import type { SessionItem } from "@/lib/types";
import {
  consumeAssistantDraft, deleteAssistantDraft, getAssistantDraft,
} from "@/lib/assistant/api";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { MODAL_LAYER, isTopmost, lockBodyScroll, registerOverlay } from "@/lib/assistant/overlay";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import { DeepLinkQueryReader } from "@/lib/assistant/deep-link";
import { STRINGS } from "@/app/(workspace)/notes/[[...noteId]]/strings";
import { MarkdownEditor, makeToolbar } from "./MarkdownEditor";
import { NotePreview, BacklinksPanel } from "./NotePreview";
import { NoteToolbar, SaveBadge, ViewModeSwitch, type ViewMode } from "./NoteToolbar";
import { NotesCenter } from "./NotesCenter";
import { AIPanel } from "./AIPanel";
import { GenerateWizard } from "./GenerateWizard";
import { RevisionDrawer } from "./RevisionDrawer";
import { TextForceGraph } from "./TextForceGraph";
import { PanelResizer } from "./PanelResizer";
import { PanelToggleButton } from "./PanelToggleButton";
import { DEMO_MODE } from "@/lib/demo";

export function NotesView({ noteId }: { noteId?: string }) {
  const router = useRouter();
  const lang = useUIStore((s) => s.lang);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);

  const {
    vault, vaultError, vaultLoading, loadVault,
    currentId, detail, content, saveState, saveError, conflictDetail,
    openNote, setContent, saveNow, reloadCurrent,
    setAgentMode, loadAgent, pendingRemoteRefresh,
    acceptPendingRemoteRefresh, dismissPendingRemoteRefresh,
    aiPanelOpen, toggleAiPanel,
    rightWidth, setRightWidth,
    hydrateLayout, focusMode, setFocusMode,
  } = useNotesStore();

  const [templates, setTemplates] = useState<Awaited<ReturnType<typeof getNoteTemplates>>["templates"]>([]);
  const [viewMode, setViewMode] = useState<ViewMode>(DEMO_MODE ? "preview" : "split");
  const [showGraph, setShowGraph] = useState(false);
  const [graph, setGraph] = useState<NotesGraph | null>(null);
  const [centerOpen, setCenterOpen] = useState(false);
  const [centerTag, setCenterTag] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [scrollRatio, setScrollRatio] = useState(0);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [aiDrawerOpen, setAiDrawerOpen] = useState(false);
  // A13 补齐：助手笔记草稿（§19.5 note_draft 落点）。临时编辑器只持有
  // 草稿内容，不调用创建笔记 API 模拟「尚未保存」；正式保存成功后才
  // consume。编辑器已有内容时按 §19.6 给合并/保留选择，默认不覆盖。
  const [noteDraft, setNoteDraft] = useState<
    { draftId: string; title: string; markdown: string } | null>(null);
  const [mergeDraft, setMergeDraft] = useState<
    { draftId: string; title: string; markdown: string } | null>(null);
  const [draftBusy, setDraftBusy] = useState(false);
  const noteDraftIdRef = useRef<string | null>(null);
  // §20.1 笔记深链：?revision=N 打开修订抽屉并预览该版本。
  const [deepRevision, setDeepRevision] = useState(0);
  const toolbar = useMemo(() => makeToolbar(tr), [tr]);

  // §20.2 页面适配器：上下文（当前笔记实体/视图）+ dirty 保护（§5.4
  // 保存并前往/放弃修改并前往/留在此页）。
  useAssistantPage({
    navigationStatus: (target) => {
      if (target.kind === "module") return navigationSucceeded;
      if (target.kind !== "note" && target.kind !== "note_revision") return null;
      if (currentId !== target.note_id || detail?.note.id !== target.note_id) return null;
      return target.kind === "note_revision"
        ? navigationAnchor("note-revision", `${target.note_id}:${target.revision}`) : navigationSucceeded;
    },
    context: () => ({
      schema_version: 1,
      route_id: "notes",
      route_epoch: currentRouteEpoch(),
      ...(currentId ? {
        entity: {
          kind: "note" as const, id: currentId,
          revision: String(detail?.note.revision ?? ""),
        },
      } : {}),
      view: noteDraft ? "assistant_draft" : undefined,
    }),
    clientState: () => ({
      dirty: saveState === "dirty" || Boolean(noteDraft),
      blocking_activity: saveState === "dirty" || noteDraft
        ? ("unsaved_editor" as const) : ("none" as const),
      activity_label: tr("tb.untitled"),
      safe_bottom_px: 24,
    }),
    beforeNavigate: async () => {
      if (saveState !== "dirty") return "allow";
      if (window.confirm(tr("nav.saveAndGo"))) {
        await saveNow();
        return "allow";
      }
      if (window.confirm(tr("nav.discardAndGo"))) return "allow";
      return "stay";
    },
  });
  const applyDeepLink = useCallback((params: Record<string, string>) => {
    const rev = Number.parseInt(params.revision || "", 10);
    setDeepRevision(Number.isFinite(rev) && rev >= 1 ? rev : 0);
  }, []);
  // 修订深链：笔记打开后开抽屉；由 RevisionDrawer 消化 initialPreview。
  useEffect(() => {
    if (!deepRevision || !currentId) return;
    void Promise.resolve().then(() => setRevisionOpen(true));
  }, [deepRevision, currentId]);

  // agentMode 从 localStorage 水合（旧四模式值迁移：suggest/collab→plan、
  // cowrite/auto→authorize；真实模式以每笔记智能体状态为准，loadAgent 会覆盖）
  useEffect(() => {
    if (DEMO_MODE) return;
    const saved = localStorage.getItem("edu-agent-notes-mode");
    const mode = normalizeAgentMode(saved);
    setAgentMode(mode);
  }, [setAgentMode]);

  // 初始加载
  useEffect(() => {
    void loadVault();
    hydrateLayout();
    void getNoteTemplates().then((r) => setTemplates(r.templates)).catch(() => {});
    void listSessions().then((r) => setSessions(r.sessions)).catch(() => {});
  }, [loadVault, hydrateLayout]);
  useEffect(() => {
    if (noteId !== currentId) void openNote(noteId ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noteId]);
  // 切换笔记 = 切换其专属智能体（历史/模式/待批复计划）
  useEffect(() => {
    void loadAgent(currentId || VAULT_AGENT_KEY);
  }, [currentId, loadAgent]);

  // 自动保存：dirty 后 800ms
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (saveState !== "dirty") return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => { void saveNow(); }, 800);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [content, saveState, saveNow]);

  // Ctrl+S
  useEffect(() => {
    const fn = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (currentId) void saveNow();
      }
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [currentId, saveNow]);

  // Esc 退出专注模式
  useEffect(() => {
    if (!focusMode) return;
    const fn = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setFocusMode(false);
      }
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [focusMode, setFocusMode]);

  const navigate = useCallback((id: string | null) => {
    router.replace(id ? `/notes/${encodeURIComponent(id)}` : "/notes");
  }, [router]);

  // 关系图数据：图谱模式或未选笔记（图谱即首页封面）时需要
  useEffect(() => {
    if (!showGraph && currentId) return;
    let alive = true;
    import("@/lib/api-notes")
      .then((m) => m.getNotesGraph())
      .then((g) => { if (alive) setGraph(g); })
      .catch(() => { /* keep old graph */ });
    return () => { alive = false; };
  }, [showGraph, currentId, vault]);

  // 助手笔记草稿：?assistant_draft= 加载（§19.5/§19.6），去除 URL 参数
  // 但保留其他定位参数；失败静默（草稿可能已过期，页面正常可用）。
  useEffect(() => {
    const draftId = new URLSearchParams(window.location.search)
      .get("assistant_draft");
    if (!draftId || noteDraftIdRef.current) return;
    noteDraftIdRef.current = draftId;
    getAssistantDraft(draftId)
      .then((draft) => {
        const prefill = (draft as {
          prefill?: { kind?: string; title?: string; markdown?: string };
          consumed?: boolean; expired?: boolean;
        }).prefill;
        if (prefill?.kind !== "note" || draft.consumed || draft.expired) {
          return;
        }
        const st = useNotesStore.getState();
        if (st.currentId || st.content.trim()) {
          setMergeDraft({
            draftId,
            title: String(prefill.title || ""),
            markdown: String(prefill.markdown || ""),
          });
        } else {
          setNoteDraft({
            draftId,
            title: String(prefill.title || ""),
            markdown: String(prefill.markdown || ""),
          });
        }
        const params = new URLSearchParams(window.location.search);
        params.delete("assistant_draft");
        const query = params.toString();
        window.history.replaceState(null, "",
          query ? `/notes?${query}` : "/notes");
      })
      .catch(() => undefined);
  }, []);

  /** §19.6 合并：附加到当前笔记缓冲 → 立即正式保存 → 才 consume。 */
  const acceptMergeDraft = async () => {
    if (!mergeDraft || !currentId) return;
    const merged = content.trim()
      ? `${content}\n\n---\n\n${mergeDraft.markdown}`
      : mergeDraft.markdown;
    setContent(merged);
    setDraftBusy(true);
    try {
      await saveNow();
      consumeAssistantDraft(mergeDraft.draftId,
        { kind: "note", id: currentId }).catch(() => undefined);
      setMergeDraft(null);
    } catch {
      // 保存失败保留草稿选择横幅，用户可重试
    } finally {
      setDraftBusy(false);
    }
  };

  /** §19.5 临时编辑器正式保存：createNote 成功后才 consume（§19.6）。 */
  const saveNoteDraft = async () => {
    if (!noteDraft || draftBusy) return;
    setDraftBusy(true);
    try {
      const { note } = await createNote({
        title: noteDraft.title.trim() || tr("tb.untitled"),
        content: noteDraft.markdown,
      });
      await loadVault();
      consumeAssistantDraft(noteDraft.draftId,
        { kind: "note", id: note.id }).catch(() => undefined);
      noteDraftIdRef.current = null;
      setNoteDraft(null);
      navigate(note.id);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : tr("draft.saveFailed"));
    } finally {
      setDraftBusy(false);
    }
  };

  const discardNoteDraft = () => {
    if (!noteDraft) return;
    const id = noteDraft.draftId;
    noteDraftIdRef.current = null;
    setNoteDraft(null);
    deleteAssistantDraft(id).catch(() => undefined);
  };

  const handleCreate = async (opts: { title?: string; templateId?: string; content?: string }) => {
    try {
      const { note } = await createNote({
        title: opts.title || "",
        template_id: opts.templateId || "",
        content: opts.content || "",
      });
      await loadVault();
      navigate(note.id);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : tr("error.create"));
    }
  };

  const handleDelete = async () => {
    if (!currentId) return;
    if (saveState === "dirty") await saveNow();
    try {
      await deleteNote(currentId);
      await loadVault();
      navigate(null);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : tr("error.delete"));
    }
  };

  const handleRename = async (title: string) => {
    if (!currentId || title === detail?.note.title) return;
    try {
      await patchNote(currentId, { title });
      await reloadCurrent();
      await loadVault();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : tr("error.rename"));
    }
  };

  const noteTitles = useMemo(
    () => (vault?.notes ?? []).map((n) => n.title), [vault]);
  const resourceLinks = useMemo(() => [
    ...(vault?.notes ?? []).map((note) => ({ id: note.id, title: note.title, url: `note://${note.id}`, kind: "note" as const })),
    ...sessions.map((session) => ({ id: session.session_id, title: session.title, url: `conversation://session/${session.session_id}`, kind: "session" as const })),
  ], [vault?.notes, sessions]);

  // 助手写入后的自动刷新：干净态直接热替换 + 重开详情（含反向链接）；
  // 脏态由 store 置横幅（ai.remote.banner），绝不静默跳过或覆盖用户输入。
  const remoteUpdate = (noteId_: string, content_: string, revision: number, title: string) => {
    if (!noteId_) return;
    const st = useNotesStore.getState();
    st.applyRemoteUpdate(noteId_, content_, revision, title);
    if (noteId_ === st.currentId && useNotesStore.getState().saveState !== "dirty") {
      void st.reloadCurrent();
    }
  };

  // 窄屏（<lg）AI 面板不可内联：悬浮按钮 + motion-drawer 抽屉承接
  const openAiSurface = () => {
    if (typeof window !== "undefined"
        && window.matchMedia("(min-width: 1024px)").matches) {
      toggleAiPanel();
    } else {
      setAiDrawerOpen(true);
    }
  };

  // 移动端 AI 抽屉：注册进覆盖层栈（Escape 只关最上层）并锁定背景滚动
  useEffect(() => {
    if (!aiDrawerOpen) return;
    const unregister = registerOverlay({
      id: "notes-ai-drawer", kind: "drawer", layer: MODAL_LAYER,
      onClose: () => setAiDrawerOpen(false),
    });
    const unlock = lockBodyScroll();
    const fn = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isTopmost("notes-ai-drawer")) setAiDrawerOpen(false);
    };
    window.addEventListener("keydown", fn);
    return () => {
      window.removeEventListener("keydown", fn);
      unlock();
      unregister();
    };
  }, [aiDrawerOpen]);

  if (vaultLoading && !vault) return <PageSkeleton />;
  if (vaultError && !vault) {
    return (
      <div className="p-6">
        <ErrorNote message={tr("notes.error.load")} retry={() => void loadVault()} />
      </div>
    );
  }
  if (!vault) return null;

  // 首页/图谱视图没有 NoteToolbar，折叠入口由这条微型头部承接
  const chromeHeader = showGraph || !currentId || !detail;

  return (
    <div className="flex h-full min-h-0">
      <Suspense><DeepLinkQueryReader keys={["revision"]} onParams={applyDeepLink} /></Suspense>
      {/* 中栏 */}
      <div className="flex min-w-0 flex-1 flex-col bg-bg">
        {chromeHeader && (
          <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border bg-surface px-1.5">
            <Button
              iconOnly variant="ghost" tone="accent" size="sm"
              onClick={() => setCenterOpen(true)}
              title={tr("tb.center")}
              aria-label={tr("tb.center")}
            >
              <FolderOpen size={15} />
            </Button>
            {currentId && (
              <Button
                iconOnly variant="ghost" tone="accent" size="sm"
                selected={showGraph}
                onClick={() => setShowGraph((v) => !v)}
                title={tr("graph.title")}
                aria-label={tr("graph.title")}
              >
                <Network size={15} />
              </Button>
            )}
            <div className="flex-1" />
            <PanelToggleButton
              side="right" open={aiPanelOpen} onToggle={openAiSurface}
              label={tr("tb.toggleAi")}
            />
          </div>
        )}
        {mergeDraft && currentId && (
          <div className="flex items-center gap-2 border-b border-accent/25 bg-accent-soft/50 px-4 py-1.5 text-[11px] text-accent-strong">
            <Sparkles size={12} className="shrink-0" />
            <span className="min-w-0 flex-1 truncate">{tr("draft.merge.banner")}</span>
            <button
              onClick={() => void acceptMergeDraft()}
              disabled={draftBusy || saveState === "saving"}
              className="shrink-0 cursor-pointer rounded-md border border-accent/40 px-2 py-0.5 font-medium transition-colors hover:bg-accent-soft disabled:opacity-50"
            >
              {tr("draft.merge.accept")}
            </button>
            <button
              onClick={() => setMergeDraft(null)}
              aria-label={tr("draft.merge.dismiss")}
              title={tr("draft.merge.dismiss")}
              className="shrink-0 cursor-pointer rounded-md p-1 text-muted transition-colors hover:bg-surface-hover hover:text-fg"
            >
              <X size={12} />
            </button>
          </div>
        )}
        {noteDraft && !currentId ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center gap-2 border-b border-border bg-surface px-4 py-2">
              <NotebookPen size={14} className="shrink-0 text-accent" />
              <span className="text-xs font-medium text-fg">{tr("draft.banner")}</span>
              <span className="min-w-0 flex-1 truncate text-[11px] text-muted">{tr("draft.hint")}</span>
            </div>
            <div className="border-b border-border px-4 py-2">
              <input
                value={noteDraft.title}
                onChange={(e) => setNoteDraft({ ...noteDraft, title: e.target.value })}
                placeholder={tr("draft.title")}
                aria-label={tr("draft.title")}
                maxLength={120}
                className={INPUT_CLS}
              />
            </div>
            <div className="flex min-h-0 flex-1">
              <MarkdownEditor
                value={noteDraft.markdown}
                onChange={(markdown) => setNoteDraft({ ...noteDraft, markdown })}
                placeholder={tr("ed.placeholder")}
                noteTitles={noteTitles}
                toolbar={toolbar}
                createWikiLabel={tr("notes.new")}
                onTriggerCreateWiki={(title) => void handleCreate({ title })}
              />
            </div>
            <div className="flex items-center gap-2 border-t border-border bg-surface px-4 py-2">
              <Button
                onClick={() => void saveNoteDraft()}
                disabled={draftBusy}
                className="!h-8 !rounded-lg !px-4 !text-xs"
              >
                {tr("draft.save")}
              </Button>
              <button
                onClick={discardNoteDraft}
                disabled={draftBusy}
                className="cursor-pointer rounded-lg border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-surface-hover hover:text-fg disabled:opacity-50"
              >
                {tr("draft.discard")}
              </button>
            </div>
          </div>
        ) : showGraph || !currentId || !detail ? (
          <TextForceGraph
            graph={graph}
            folderNames={Object.fromEntries(vault.folders.map((f) => [f.id, f.name]))}
            tr={tr}
            home={!currentId}
            noteCount={vault.stats.note_count}
            onOpenCenter={() => setCenterOpen(true)}
            onOpenNote={(id) => { setShowGraph(false); navigate(id); }}
            onCreateNote={(title) => void handleCreate({ title })}
            onOpenSession={(id) => router.push(`/chat/${encodeURIComponent(id)}`)}
            onOpenTextbook={() => router.push("/resources/textbooks")}
            onGenerate={() => setWizardOpen(true)}
          />
        ) : (
          <>
            <NoteToolbar
              detail={detail}
              folders={vault.folders}
              saveState={saveState}
              conflictOpen={saveState === "conflict"}
              onCloseConflict={() => useNotesStore.setState({ saveState: "error" })}
              onLoadLatest={() => void reloadCurrent()}
              onOverwrite={() => {
                useNotesStore.setState({
                  detail: conflictDetail,
                  content: conflictDetail?.content ?? content,
                  saveState: "dirty",
                  conflictDetail: null,
                });
              }}
              onRename={(t) => void handleRename(t)}
              onMove={(fid) => void patchNote(currentId, { folder_id: fid })
                .then(() => { void reloadCurrent(); void loadVault(); })}
              onTags={(tags) => void patchNote(currentId, { tags })
                .then(() => { void reloadCurrent(); void loadVault(); })}
              onHistory={() => setRevisionOpen(true)}
              onExport={() => void exportNoteFile(currentId, detail.note.title)}
              onDelete={() => void handleDelete()}
              onOpenCenter={() => setCenterOpen(true)}
              rightOpen={aiPanelOpen}
              onToggleRight={openAiSurface}
              viewMode={viewMode}
              onViewMode={setViewMode}
              graphOn={showGraph}
              onToggleGraph={() => setShowGraph((v) => !v)}
              onFocus={() => setFocusMode(true)}
              tr={tr}
            />
            {saveError && saveState !== "conflict" && (
              <div className="px-4 py-1 text-[11px] text-danger">{saveError}</div>
            )}
            {/* 助手更新 × 本地脏态：横幅由用户决断（载入最新 / 保留我的） */}
            {pendingRemoteRefresh && pendingRemoteRefresh.noteId === currentId && (
              <div className="flex items-center gap-2 border-b border-accent/25 bg-accent-soft/50 px-4 py-1.5 text-[11px] text-accent-strong">
                <Sparkles size={12} className="shrink-0" />
                <span className="min-w-0 flex-1 truncate">{tr("ai.remote.banner")}</span>
                <button
                  onClick={() => void acceptPendingRemoteRefresh()}
                  className="shrink-0 cursor-pointer rounded-md border border-accent/40 px-2 py-0.5 font-medium transition-colors hover:bg-accent-soft"
                >
                  {tr("ai.remote.loadLatest")}
                </button>
                <button
                  onClick={dismissPendingRemoteRefresh}
                  aria-label={tr("ai.remote.keepMine")}
                  className="shrink-0 cursor-pointer rounded-md p-1 text-muted transition-colors hover:bg-surface-hover hover:text-fg"
                >
                  <X size={12} />
                </button>
              </div>
            )}
            {/* 编辑/预览 */}
            <div className="flex min-h-0 flex-1">
              {viewMode !== "preview" && (
                <div className="flex min-w-0 flex-1 flex-col">
                  <MarkdownEditor
                    value={content}
                    onChange={setContent}
                    placeholder={tr("ed.placeholder")}
                    noteTitles={noteTitles}
                    toolbar={toolbar}
                    createWikiLabel={tr("notes.new")}
                    onTriggerCreateWiki={(title) => void handleCreate({ title })}
                    onScrollRatioChange={setScrollRatio}
                    scrollRatio={scrollRatio}
                    resourceLinks={resourceLinks}
                  />
                </div>
              )}
              {viewMode !== "edit" && (
                <div className={cn(
                  "min-w-0 overflow-y-auto px-5 py-4",
                  viewMode === "split" && "flex-1 border-l border-border",
                )}>
                  <NotePreview
                    content={content}
                    onWikiLink={(title) => {
                      const target = vault.notes.find((n) => n.title === title);
                      if (target) navigate(target.id);
                      else void handleCreate({ title });
                    }}
                    onTagClick={(tag) => { setCenterTag(tag); setCenterOpen(true); }}
                    onResourceLink={(url) => {
                      if (url.startsWith("note://")) navigate(url.slice("note://".length));
                      else if (url.startsWith("conversation://session/")) router.push(`/chat/${encodeURIComponent(url.slice("conversation://session/".length))}`);
                    }}
                  />
                  <BacklinksPanel
                    detail={detail}
                    onOpenNote={navigate}
                    onCreateNote={(title) => void handleCreate({ title })}
                    onOpenResource={(url) => {
                      if (url.startsWith("note://")) navigate(url.slice("note://".length));
                      else if (url.startsWith("conversation://session/")) router.push(`/chat/${encodeURIComponent(url.slice("conversation://session/".length))}`);
                    }}
                  />
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* 右栏：AI 面板（lg+ 内联可拖宽；<lg 由悬浮按钮 + 抽屉承接） */}
      {aiPanelOpen && (
        <>
          <PanelResizer
            side="right" width={rightWidth}
            onResize={setRightWidth}
            onReset={() => setRightWidth(NOTES_LAYOUT_DEFAULTS.rightWidth)}
            className="hidden lg:block"
          />
          <div className="hidden shrink-0 lg:block" style={{ width: rightWidth }}>
            <AIPanel
              tr={tr}
              onRemoteUpdate={remoteUpdate}
              onVaultChanged={() => void loadVault()}
            />
          </div>
        </>
      )}
      {!aiPanelOpen && (
        <button
          onClick={openAiSurface}
          title={tr("tb.toggleAi")}
          aria-label={tr("tb.toggleAi")}
          className="fixed bottom-5 right-4 z-40 flex h-10 w-10 cursor-pointer items-center justify-center rounded-full border border-border bg-surface text-accent shadow-lg transition-colors hover:bg-accent-soft lg:hidden"
        >
          <Sparkles size={16} />
        </button>
      )}
      {aiDrawerOpen && typeof document !== "undefined" && createPortal(
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => setAiDrawerOpen(false)}
            aria-hidden
          />
          <div className="motion-drawer absolute right-0 top-0 h-full w-[min(24rem,92vw)] bg-surface shadow-2xl">
            <AIPanel
              tr={tr}
              onRemoteUpdate={remoteUpdate}
              onVaultChanged={() => void loadVault()}
              onClose={() => setAiDrawerOpen(false)}
            />
          </div>
        </div>,
        document.body,
      )}

      {/* 专注模式：覆盖整个应用的编辑覆盖层，Esc 退出 */}
      {focusMode && detail && (
        <div className="fixed inset-0 z-50 flex flex-col bg-bg">
          <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border bg-surface px-3">
            <NotebookPen size={14} className="shrink-0 text-accent" />
            <span className="min-w-0 truncate text-sm font-medium text-fg">
              {detail.note.title || tr("tb.untitled")}
            </span>
            <SaveBadge saveState={saveState} tr={tr} />
            <div className="ml-auto flex items-center gap-1.5">
              <ViewModeSwitch mode={viewMode} onChange={setViewMode} tr={tr} />
              <Button
                iconOnly variant="ghost" tone="accent" size="sm"
                onClick={() => setFocusMode(false)}
                title={tr("tb.focus.exit")}
                aria-label={tr("tb.focus.exit")}
              >
                <Minimize2 size={15} />
              </Button>
            </div>
          </div>
          <div className="flex min-h-0 flex-1">
            {viewMode !== "preview" && (
              <div className="flex min-w-0 flex-1 flex-col">
                <MarkdownEditor
                  value={content}
                  onChange={setContent}
                  placeholder={tr("ed.placeholder")}
                  noteTitles={noteTitles}
                  toolbar={toolbar}
                  createWikiLabel={tr("notes.new")}
                  onTriggerCreateWiki={(title) => void handleCreate({ title })}
                  onScrollRatioChange={setScrollRatio}
                  scrollRatio={scrollRatio}
                />
              </div>
            )}
            {viewMode !== "edit" && (
              <div className={cn(
                "min-w-0 overflow-y-auto px-5 py-4",
                viewMode === "split" && "flex-1 border-l border-border",
              )}>
                <NotePreview
                  content={content}
                  onWikiLink={(title) => {
                    const target = vault.notes.find((n) => n.title === title);
                    if (target) navigate(target.id);
                    else void handleCreate({ title });
                  }}
                  onTagClick={(tag) => { setFocusMode(false); setCenterTag(tag); setCenterOpen(true); }}
                />
              </div>
            )}
          </div>
        </div>
      )}

      {/* 笔记中心：文件夹/标签/搜索/列表/新建 一站式管理弹窗（条件挂载，每次打开状态全新） */}
      {centerOpen && (
      <NotesCenter
        open
        onClose={() => setCenterOpen(false)}
        vault={vault}
        currentId={currentId}
        initialTag={centerTag}
        templates={templates}
        tr={tr}
        lang={lang}
        onOpenNote={(id) => { setCenterOpen(false); navigate(id); }}
        onCreateBlank={() => void handleCreate({})}
        onCreateFromTemplate={(templateId) => void handleCreate({ templateId })}
        onGenerate={() => { setCenterOpen(false); setWizardOpen(true); }}
        onCreateFolder={(name, parentId = "") => {
          if (!name.trim()) return;
          void createNotesFolder(name, parentId).then(() => loadVault());
        }}
        onRenameFolder={(fid, name) => void renameNotesFolder(fid, name)
          .then(() => loadVault())}
        onDeleteFolder={(fid) => void deleteNotesFolder(fid)
          .then(() => loadVault())}
        onExportAll={() => void exportVaultZip()}
        onExportFolder={(fid) => void exportVaultZip(
          fid, vault.folders.find((f) => f.id === fid)?.name)}
        onVaultChanged={loadVault}
      />
      )}

      {/* 向导/抽屉（条件挂载：每次打开都是干净的初始状态） */}
      {wizardOpen && (
      <GenerateWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        templates={templates}
        tr={tr}
        lang={lang}
        onCreated={(note) => {
          void loadVault();
          navigate(note.id);
        }}
        onVaultChanged={() => void loadVault()}
      />
      )}
      {currentId && (
        <RevisionDrawer
          key={`${currentId}:${deepRevision}`}
          open={revisionOpen}
          onClose={() => { setRevisionOpen(false); setDeepRevision(0); }}
          noteId={currentId}
          currentRevision={detail?.note.revision ?? 0}
          initialPreviewRevision={deepRevision || undefined}
          tr={tr}
          onRestored={() => { void reloadCurrent(); void loadVault(); }}
        />
      )}
    </div>
  );
}
