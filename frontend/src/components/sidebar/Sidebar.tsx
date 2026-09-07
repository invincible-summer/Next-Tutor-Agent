"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { CheckSquare, Plus, FolderPlus, Trash2, X } from "lucide-react";
import { useUIStore, useChatStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/cn";
import {
  deleteSession, renameSession,
  getPromptMemorySessionStatus,
  deleteWorkspace, renameWorkspace,
  moveSessionToWorkspace, removeSessionFromWorkspace, getWorkspace,
  getSidebarSnapshot,
} from "@/lib/api";
import { notifyWsChanged, useWsSettings, WS_CHANGED_EVENT, SESSION_CHANGED_EVENT } from "@/lib/ws-settings";
import { Button } from "@/components/ui/Button";
import { WorkspaceItem } from "./WorkspaceItem";
import { SessionRow } from "./SessionRow";
import { ConfirmDialog } from "./ConfirmDialog";
import type { WorkspaceItem as WorkspaceInfo, WorkspaceDetail } from "@/lib/types";

const EXPAND_KEY = "edu-agent-ws-expanded";

type SidebarSnapshot = {
  sessions: import("@/lib/types").SessionItem[];
  workspaces: WorkspaceInfo[];
  details: Record<string, WorkspaceDetail>;
};

// The chat route is a dynamic segment, so navigating from one history row to
// another can remount Sidebar. Keep the last *atomically loaded* snapshot at
// module scope and deduplicate concurrent desktop/mobile Sidebar requests.
// This prevents the old transient state: sessions render as loose first, then
// migrate under a workspace after the second request finishes.
let sidebarCache: SidebarSnapshot | null = null;
let sidebarRequest: Promise<SidebarSnapshot> | null = null;

async function fetchSidebarSnapshot(): Promise<SidebarSnapshot> {
  // P2：组合端点一次取齐（服务端拼装 + ETag/304），替代原来的
  // listSessions → listWorkspaces → N×getWorkspace 三级瀑布。
  const snap = await getSidebarSnapshot();
  return {
    sessions: snap.sessions ?? [],
    workspaces: snap.workspaces ?? [],
    details: snap.details ?? {},
  };
}

function loadSidebarSnapshot(force = false): Promise<SidebarSnapshot> {
  if (!force && sidebarCache) return Promise.resolve(sidebarCache);
  if (sidebarRequest) return sidebarRequest;
  sidebarRequest = fetchSidebarSnapshot().then((snapshot) => {
    sidebarCache = snapshot;
    return snapshot;
  }).finally(() => { sidebarRequest = null; });
  return sidebarRequest;
}

interface ConfirmState {
  title: string;
  desc?: string;
  onConfirm: () => void | Promise<void>;
}

interface SessionArchiveState {
  id: string;
  memoryStatus: "recent" | "compacted" | "legacy_unknown" | "none";
  forget: boolean;
}

/** 会话侧栏：新对话主按钮 + 工作区折叠分组 + 其他对话列表。 */
export function Sidebar() {
  const { lang, sidebarOpen } = useUIStore();
  const { setSessions, newChat, sessionId, setSessionId } = useChatStore();
  const initialSidebar = sidebarCache;
  const [sidebarSessions, setSidebarSessions] = useState(initialSidebar?.sessions ?? []);
  const [sidebarReady, setSidebarReady] = useState(Boolean(initialSidebar));
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const router = useRouter();
  const pathname = usePathname();
  // URL is the source of truth for the highlighted ("current") session.
  const urlSessionId = (() => {
    const m = pathname.match(/^\/chat\/([^/]+)/);
    if (!m) return null;
    try { return decodeURIComponent(m[1]); } catch { return m[1]; }
  })();

  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>(initialSidebar?.workspaces ?? []);
  const [wsDetails, setWsDetails] = useState<Record<string, WorkspaceDetail>>(initialSidebar?.details ?? {});
  // null = not hydrated yet (first client render matches SSR: all expanded).
  const [expandedMap, setExpandedMap] = useState<Record<string, boolean> | null>(null);
  const [expandedStored, setExpandedStored] = useState(false);
  const [dragOver, setDragOver] = useState<string | null>(null);
  const [draggedSession, setDraggedSession] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [sessionArchive, setSessionArchive] = useState<SessionArchiveState | null>(null);
  // Batch-manage mode: checkbox selection across both workspace and loose
  // sessions, with a footer action bar (select all / delete / exit).
  const [managing, setManaging] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Hydrate persisted expand state AFTER mount (SSR-safe, cf. store.hydrateClient).
  // Deferred via rAF: setState runs in a callback, not synchronously in the
  // effect body (react-hooks/set-state-in-effect), and the first client
  // render still matches SSR (all expanded).
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      try {
        const raw = localStorage.getItem(EXPAND_KEY);
        if (raw) {
          setExpandedMap(JSON.parse(raw));
          setExpandedStored(true);
        } else {
          setExpandedMap({});
        }
      } catch {
        setExpandedMap({});
      }
    });
    return () => cancelAnimationFrame(id);
  }, []);

  // First run (nothing persisted): default all expanded. Once the user has
  // toggled anything, the persisted map is authoritative (missing = collapsed).
  const isExpandedId = (id: string) =>
    expandedMap === null ? true : (expandedMap[id] ?? !expandedStored);

  const mountedRef = useRef(true);
  const refresh = async (force = true) => {
    try {
      const snapshot = await loadSidebarSnapshot(force);
      if (!mountedRef.current) return;
      // Commit the complete snapshot together. No intermediate state can put
      // workspace sessions in the loose section while details are loading.
      setSidebarSessions(snapshot.sessions);
      setSessions(snapshot.sessions);
      setWorkspaces(snapshot.workspaces);
      setWsDetails(snapshot.details);
      setSidebarReady(true);
    } catch { /* keep the last complete snapshot visible */ }
  };

  useEffect(() => {
    mountedRef.current = true;
    // Cached route remounts render the complete snapshot immediately and do
    // not refetch. The first ever load is deferred one frame so it is an
    // external async subscription, not a cascading setState in the effect.
    const id = sidebarCache ? 0 : requestAnimationFrame(() => { void refresh(false); });
    return () => {
      mountedRef.current = false;
      if (id) cancelAnimationFrame(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 工作区设置保存、资料变化或对话新增后，刷新完整快照；旧快照在新数据
  // 到齐前保持显示，避免再次出现“先归入非工作区再迁移”的视觉跳动。
  useEffect(() => {
    const onChanged = () => { void refresh(true); };
    window.addEventListener(WS_CHANGED_EVENT, onChanged);
    window.addEventListener(SESSION_CHANGED_EVENT, onChanged);
    return () => {
      window.removeEventListener(WS_CHANGED_EVENT, onChanged);
      window.removeEventListener(SESSION_CHANGED_EVENT, onChanged);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const askConfirm = (opts: ConfirmState) => setConfirm(opts);

  // History rows navigate instead of loading in-place: the chat page's
  // URL-driven effect performs the actual loadFull + grade + workspace
  // binding, and repeated clicks on the current row are no-ops.
  const onSelect = (id: string) => {
    if (id === urlSessionId) return;
    router.push(`/chat/${encodeURIComponent(id)}`);
  };

  const onDeleteSession = async (id: string) => {
    const memoryStatus = await getPromptMemorySessionStatus(id).catch(() => "none" as const);
    setSessionArchive({ id, memoryStatus, forget: false });
  };

  const confirmSessionArchive = async () => {
    if (!sessionArchive) return;
    const { id, forget } = sessionArchive;
    setSessionArchive(null);
    await deleteSession(id, forget);
    if (sessionId === id) newChat();
    if (urlSessionId === id) router.replace("/chat");
    refresh();
  };

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const exitManaging = () => {
    setManaging(false);
    setSelected(new Set());
  };

  const selectAll = () => setSelected(new Set(sidebarSessions.map((s) => s.session_id)));

  const onBatchDelete = () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    askConfirm({
      title: tr("session.batchdelete.title"),
      desc: tr("session.batchdelete.desc").replace("%n", String(ids.length)),
      onConfirm: async () => {
        // Reuse the single-session DELETE endpoint per id; no backend change.
        const results = await Promise.allSettled(ids.map((id) => deleteSession(id)));
        if (sessionId && ids.includes(sessionId)) newChat();
        if (urlSessionId && ids.includes(urlSessionId)) router.replace("/chat");
        exitManaging();
        refresh();
        if (results.some((r) => r.status === "rejected")) {
          alert(tr("sidebar.batchdelete.failed"));
        }
      },
    });
  };

  const onRenameSession = async (id: string, title: string) => {
    await renameSession(id, title);
    refresh();
  };

  const onRemoveFromWs = async (wsId: string, sid: string) => {
    await removeSessionFromWorkspace(wsId, sid);
    refresh();
  };

  const onRenameWs = async (wsId: string, name: string) => {
    await renameWorkspace(wsId, name);
    refresh();
  };

  const onDeleteWs = (wsId: string) => {
    askConfirm({
      title: tr("ws.delete"),
      desc: tr("ws.confirm.delete"),
      onConfirm: async () => {
        await deleteWorkspace(wsId);
        refresh();
      },
    });
  };

  const toggleWs = async (wsId: string) => {
    const next = { ...(expandedMap ?? {}) };
    if (!expandedStored) {
      // Seed current defaults so other workspaces stay expanded on first save.
      for (const w of workspaces) if (!(w.workspace_id in next)) next[w.workspace_id] = true;
    }
    next[wsId] = !isExpandedId(wsId);
    setExpandedMap(next);
    setExpandedStored(true);
    try { localStorage.setItem(EXPAND_KEY, JSON.stringify(next)); } catch { /* ignore */ }
    // Always reload detail on toggle (catches changes since last open).
    try {
      const detail = await getWorkspace(wsId);
      setWsDetails((prev) => {
        const next = { ...prev, [wsId]: detail };
        if (sidebarCache) sidebarCache = { ...sidebarCache, details: next };
        return next;
      });
    } catch { /* ignore */ }
  };

  const onNewChatInWs = (wsId: string) => {
    // Create a fresh session bound to this workspace
    useChatStore.getState().aborter?.abort();
    newChat();
    setSessionId(null);
    // The workspace_id will be sent on first message via chatStream
    useChatStore.setState({ messages: [], files: [] });
    // Store the active workspace so the chat page can pass it
    sessionStorage.setItem("edu-agent-active-ws", wsId);
    notifyWsChanged();
    if (pathname !== "/chat") router.push("/chat");
  };

  const onDropOnWs = async (wsId: string, e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(null);
    if (!draggedSession) return;
    await moveSessionToWorkspace(wsId, draggedSession);
    setDraggedSession(null);
    refresh();
  };

  // Split sessions: those in workspaces vs loose
  const wsSessionIds = new Set<string>();
  for (const w of workspaces) {
    const detail = wsDetails[w.workspace_id];
    if (detail) detail.session_ids.forEach((sid) => wsSessionIds.add(sid));
  }
  // Also check session.workspace_id field from list
  for (const s of sidebarSessions) {
    if (s.workspace_id) wsSessionIds.add(s.session_id);
  }
  const looseSessions = sidebarSessions.filter((s) => !s.workspace_id && !wsSessionIds.has(s.session_id));

  return (
    <aside className={cn(
      "flex-shrink-0 overflow-hidden border-r border-border bg-surface transition-all duration-200",
      sidebarOpen ? "w-64" : "w-0",
    )}>
      <div className="flex h-full w-64 flex-col">
        {/* 新对话主按钮 + 新建学习区 */}
        <div className="flex flex-col gap-2 p-3 pb-2">
          <div className="flex items-center gap-2">
            <Button
              className="flex-1"
              icon={<Plus size={15} />}
              onClick={() => {
                // Stop any in-flight stream first: the chat page's effect
                // only aborts when the URL actually changes, which it won't
                // if we're already on /chat.
                useChatStore.getState().aborter?.abort();
                newChat();
                sessionStorage.removeItem("edu-agent-active-ws");
                notifyWsChanged();
                refresh();
                if (pathname !== "/chat") router.push("/chat");
              }}
            >
              {tr("sidebar.new")}
            </Button>
            {sidebarSessions.length > 0 && (
              <button
                onClick={() => (managing ? exitManaging() : setManaging(true))}
                title={tr("sidebar.manage")}
                className={cn(
                  "flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-full transition-colors",
                  managing ? "bg-accent-soft text-accent-strong" : "text-muted hover:bg-surface-hover hover:text-fg",
                )}
              >
                <CheckSquare size={15} />
              </button>
            )}
          </div>
          {!managing && (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              icon={<FolderPlus size={13} />}
              onClick={() => useWsSettings.getState().open("new")}
            >
              {tr("ws.create")}
            </Button>
          )}
        </div>

        {/* Scrollable list */}
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {!sidebarReady && (
            <div className="px-3 py-4 text-center text-xs text-muted">{tr("sidebar.loading", "正在加载对话…")}</div>
          )}
          {sidebarReady && <>
          {/* Workspaces */}
          {workspaces.length > 0 && (
            <p className="px-2.5 pb-1.5 pt-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted/70">
              {tr("ws.title")}
            </p>
          )}
          {workspaces.map((ws) => {
            const detail = wsDetails[ws.workspace_id];
            const wsSessions = detail
              ? sidebarSessions.filter((s) => detail.session_ids.includes(s.session_id))
              : sidebarSessions.filter((s) => s.workspace_id === ws.workspace_id);
            return (
              <WorkspaceItem
                key={ws.workspace_id}
                ws={ws}
                detail={detail}
                sessions={wsSessions}
                activeSessionId={urlSessionId}
                isExpanded={isExpandedId(ws.workspace_id)}
                isDragOver={dragOver === ws.workspace_id}
                onToggle={() => toggleWs(ws.workspace_id)}
                onDragOver={(e) => { e.preventDefault(); setDragOver(ws.workspace_id); }}
                onDragLeave={() => setDragOver(null)}
                onDrop={(e) => onDropOnWs(ws.workspace_id, e)}
                onNewChat={() => onNewChatInWs(ws.workspace_id)}
                onRename={(name) => onRenameWs(ws.workspace_id, name)}
                onDelete={() => onDeleteWs(ws.workspace_id)}
                onRefresh={refresh}
                onSelectSession={onSelect}
                onRenameSession={onRenameSession}
                onDeleteSession={onDeleteSession}
                onRemoveFromWs={onRemoveFromWs}
                onSessionDragStart={setDraggedSession}
                askConfirm={askConfirm}
                selectable={managing}
                isSelected={(id) => selected.has(id)}
                onToggleSelect={toggleSelect}
              />
            );
          })}
          {workspaces.length === 0 && (
            <p className="px-3 py-1 text-[0.65rem] text-muted/50">{tr("ws.empty")}</p>
          )}

          {workspaces.length === 0 && looseSessions.length === 0 && (
            <p className="px-3 py-8 text-center text-xs text-muted">{tr("sidebar.empty")}</p>
          )}

          {/* Loose sessions (not in any workspace) */}
          {looseSessions.length > 0 && (
            <>
              {workspaces.length > 0 && (
                <p className="mt-2 px-2.5 pb-1.5 pt-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted/70">
                  {tr("ws.loose")}
                </p>
              )}
              {looseSessions.map((s) => (
                <SessionRow
                  key={s.session_id}
                  session={s}
                  active={urlSessionId === s.session_id}
                  loose
                  onSelect={onSelect}
                  onRename={onRenameSession}
                  onDelete={onDeleteSession}
                  onDragStart={setDraggedSession}
                  selectable={managing}
                  selected={selected.has(s.session_id)}
                  onToggleSelect={toggleSelect}
                />
              ))}
            </>
          )}
          </>}
        </div>

        {/* 批量管理操作条 */}
        {managing && (
          <div className="border-t border-border p-3">
            <div className="mb-2 flex items-center justify-between px-1">
              <span className="tnum text-[0.7rem] text-muted">
                {tr("sidebar.selected.n").replace("%n", String(selected.size))}
              </span>
              <button
                onClick={selectAll}
                className="cursor-pointer text-[0.7rem] text-accent-strong hover:underline"
              >
                {tr("sidebar.select.all")}
              </button>
            </div>
            <div className="flex gap-2">
              <Button
                variant="danger"
                size="sm"
                className="flex-1"
                icon={<Trash2 size={13} />}
                disabled={selected.size === 0}
                onClick={onBatchDelete}
              >
                {tr("sidebar.delete.selected")}
              </Button>
              <Button variant="outline" size="sm" icon={<X size={13} />} onClick={exitManaging}>
                {tr("sidebar.manage.done")}
              </Button>
            </div>
          </div>
        )}
      </div>

      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          desc={confirm.desc}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            const c = confirm;
            setConfirm(null);
            c.onConfirm();
          }}
        />
      )}
      {sessionArchive && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/30" onClick={() => setSessionArchive(null)} />
          <div className="relative flex max-h-[calc(100vh-2rem)] w-[min(460px,94vw)] flex-col rounded-[10px] border border-border bg-surface p-5 shadow-lg">
            <div className="shrink-0 text-sm font-semibold text-fg">{tr("session.delete.title")}</div>
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
              <p className="mt-2 text-sm leading-relaxed text-fg-secondary">{tr("session.archive.info")}</p>
              {sessionArchive.memoryStatus === "recent" && (
                <label className="mt-4 flex cursor-pointer items-start gap-2 rounded-[8px] border border-danger/25 bg-danger/5 p-3 text-xs text-fg-secondary">
                  <input type="checkbox" className="mt-0.5" checked={sessionArchive.forget} onChange={(e) => setSessionArchive({ ...sessionArchive, forget: e.target.checked })} />
                  <span><b className="text-danger">{tr("session.archive.forget")}</b><br />{tr("session.archive.forget.desc")}</span>
                </label>
              )}
              {sessionArchive.memoryStatus === "compacted" && <p className="mt-3 rounded-[8px] bg-surface-hover p-3 text-xs text-muted">{tr("session.archive.compacted")}</p>}
              {sessionArchive.memoryStatus === "legacy_unknown" && <p className="mt-3 rounded-[8px] bg-surface-hover p-3 text-xs text-muted">{tr("session.archive.legacyUnknown")}</p>}
            </div>
            <div className="mt-5 flex shrink-0 justify-end gap-2"><Button size="sm" variant="ghost" onClick={() => setSessionArchive(null)}>{tr("common.cancel")}</Button><Button size="sm" variant="danger" onClick={() => void confirmSessionArchive()}>{tr("session.archive.confirm")}</Button></div>
          </div>
        </div>
      )}
    </aside>
  );
}
