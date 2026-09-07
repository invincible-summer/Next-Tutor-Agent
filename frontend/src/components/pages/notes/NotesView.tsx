"use client";
// 笔记主视图：引力图首页/图谱 + 编辑/预览 + AI 面板。
// 负责：初始加载、URL 同步（/notes/<id>）、800ms 防抖自动保存 + Ctrl+S、
// 409 冲突处理、来自 AI 面板的远程热更新、编辑/预览/分屏切换、
// 居中「笔记中心」弹窗（文件夹/标签/列表/新建）、AI 面板折叠/拖宽。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { listSessions } from "@/lib/api";
import { FolderOpen, Minimize2, Network, NotebookPen, Sparkles, X } from "lucide-react";
import { ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
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
  const [viewMode, setViewMode] = useState<ViewMode>("split");
  const [showGraph, setShowGraph] = useState(false);
  const [graph, setGraph] = useState<NotesGraph | null>(null);
  const [centerOpen, setCenterOpen] = useState(false);
  const [centerTag, setCenterTag] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [scrollRatio, setScrollRatio] = useState(0);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [aiDrawerOpen, setAiDrawerOpen] = useState(false);
  const toolbar = useMemo(() => makeToolbar(tr), [tr]);

  // agentMode 从 localStorage 水合（旧四模式值迁移：suggest/collab→plan、
  // cowrite/auto→authorize；真实模式以每笔记智能体状态为准，loadAgent 会覆盖）
  useEffect(() => {
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
      window.alert(e instanceof Error ? e.message : "创建失败");
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
      window.alert(e instanceof Error ? e.message : "删除失败");
    }
  };

  const handleRename = async (title: string) => {
    if (!currentId || title === detail?.note.title) return;
    try {
      await patchNote(currentId, { title });
      await reloadCurrent();
      await loadVault();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "重命名失败");
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
      {/* 中栏 */}
      <div className="flex min-w-0 flex-1 flex-col bg-bg">
        {chromeHeader && (
          <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border bg-surface px-1.5">
            <button
              onClick={() => setCenterOpen(true)}
              title={tr("tb.center")}
              aria-label={tr("tb.center")}
              className="shrink-0 cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-surface-hover hover:text-accent"
            >
              <FolderOpen size={15} />
            </button>
            {currentId && (
              <button
                onClick={() => setShowGraph((v) => !v)}
                title={tr("graph.title")}
                aria-label={tr("graph.title")}
                className={cn(
                  "cursor-pointer rounded-md p-1.5 transition-colors",
                  showGraph
                    ? "bg-accent-soft text-accent-strong"
                    : "text-muted hover:bg-surface-hover hover:text-accent",
                )}
              >
                <Network size={15} />
              </button>
            )}
            <div className="flex-1" />
            <PanelToggleButton
              side="right" open={aiPanelOpen} onToggle={openAiSurface}
              label={tr("tb.toggleAi")}
            />
          </div>
        )}
        {showGraph || !currentId || !detail ? (
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
      {aiDrawerOpen && (
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
        </div>
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
              <button
                onClick={() => setFocusMode(false)}
                title={tr("tb.focus.exit")}
                aria-label={tr("tb.focus.exit")}
                className="cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-surface-hover hover:text-accent"
              >
                <Minimize2 size={15} />
              </button>
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
          open={revisionOpen}
          onClose={() => setRevisionOpen(false)}
          noteId={currentId}
          currentRevision={detail?.note.revision ?? 0}
          tr={tr}
          onRestored={() => { void reloadCurrent(); void loadVault(); }}
        />
      )}
    </div>
  );
}
