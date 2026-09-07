"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { FolderOpen, LibraryBig } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { relTime } from "@/lib/format";
import {
  createLibraryFolder,
  deleteLibraryFile,
  deleteLibraryFolder,
  downloadLibraryFile,
  downloadSessionFile,
  getLibrary,
  listSessions,
  loadSession,
  moveLibraryFile,
  renameLibraryFolder,
  renameLibraryFile,
  uploadFailures,
  uploadLibraryFiles,
} from "@/lib/api";
import { WS_CHANGED_EVENT } from "@/lib/ws-settings";
import { getKnowledgeTaxonomy } from "@/lib/api-modules";
import type { LibraryFolder, LibraryTree, SessionItem } from "@/lib/types";
import type { KnowledgeTaxonomyLevel } from "@/lib/types-modules";
import { ConfirmModal } from "@/components/ui/Modal";
import { EmptyState, ErrorNote, Skeleton } from "@/components/ui/EmptyState";
import { STRINGS } from "@/app/(workspace)/resources/strings";
import { SourceSidebar } from "./SourceSidebar";
import { UploadZone } from "./UploadZone";
import { FileCard } from "./FileCard";
import { TextbookSidebar } from "./textbook/TextbookSidebar";
import type { ResourceFile, SourceRef } from "./types";

// 教材库视图（图谱/重组件）懒加载：文件库路由首屏不打包它，
// 切到 /resources/textbooks 时才拉取分包，Skeleton 兜底视觉不变。
const TextbookLibraryView = dynamic(
  () => import("./textbook/TextbookLibraryView").then((m) => m.TextbookLibraryView),
  { loading: () => (
    <div className="flex flex-col gap-4">
      <Skeleton className="h-16" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
      </div>
    </div>
  ) },
);

type BootState = "loading" | "error" | "ready";
type SessionState = "idle" | "loading" | "error" | "ready";

/** 资料中心主体：教材库 / 文件库双 Tab 由路由段承载
 *  （/resources/files | /resources/textbooks），tab 仅作展示态。 */
export function ResourcesView({ tab }: { tab: "files" | "textbooks" }) {
  const lang = useUIStore((s) => s.lang);
  const tr = makePageT(lang, STRINGS);

  const [boot, setBoot] = useState<BootState>("loading");
  const [tree, setTree] = useState<LibraryTree>({ folders: [], files: [] });
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [selected, setSelected] = useState<SourceRef | null>(null);
  // 会话附件视图：唯一需要二次拉取的详情
  const [sessionFiles, setSessionFiles] = useState<ResourceFile[]>([]);
  const [sessionTitle, setSessionTitle] = useState("");
  const [sessionState, setSessionState] = useState<SessionState>("idle");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [confirmFolder, setConfirmFolder] = useState<LibraryFolder | null>(null);
  const [confirmFile, setConfirmFile] = useState<ResourceFile | null>(null);

  // 教材库 Tab 专属：M5 taxonomy 分层导航（学段→学科→教材组→卷）
  const [taxoLevels, setTaxoLevels] = useState<KnowledgeTaxonomyLevel[]>([]);
  const [taxoLoading, setTaxoLoading] = useState(tab === "textbooks");
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [focusTextbookId, setFocusTextbookId] = useState<string | null>(null);

  const sessionSeq = useRef(0);
  const busyRef = useRef(false);

  const fetchAll = async () => {
    const [lib, ss] = await Promise.all([getLibrary(), listSessions()]);
    return { tree: lib, sessions: (ss.sessions ?? []).filter((s) => s.file_count > 0) };
  };

  const applyBoot = useCallback((r: { tree: LibraryTree; sessions: SessionItem[] }) => {
    setTree(r.tree);
    setSessions(r.sessions);
    setSelected((prev) => prev ?? { kind: "all" });
    setBoot("ready");
  }, []);

  const bootLoad = useCallback(() => {
    fetchAll()
      .then(applyBoot)
      .catch(() => setBoot("error"));
  }, [applyBoot]);

  useEffect(() => {
    // 文件库 Tab 才需要资料库树+会话列表；教材库 Tab 有自己的数据源（taxonomy/textbooks）
    if (tab === "files") bootLoad();
  }, [tab, bootLoad]);

  // 教材库 Tab：M5 taxonomy 分层（构建/改名后经 WS_CHANGED_EVENT 防抖刷新）
  useEffect(() => {
    if (tab !== "textbooks") return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = () => {
      getKnowledgeTaxonomy()
        .then((r) => { if (r.status === "ok") setTaxoLevels(r.levels ?? []); })
        .catch(() => undefined)
        .finally(() => setTaxoLoading(false));
    };
    load();
    const schedule = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(load, 800);
    };
    window.addEventListener(WS_CHANGED_EVENT, schedule);
    return () => {
      if (timer) clearTimeout(timer);
      window.removeEventListener(WS_CHANGED_EVENT, schedule);
    };
  }, [tab]);

  // URL 卫生：旧深链 /resources?tab=textbooks 经 307 后 query 会残留在子路由
  // 上（Next 默认透传未匹配参数）。进页后静默清掉，地址栏保持规范段形式。
  useEffect(() => {
    if (window.location.search) {
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  // 工作区设置弹窗 / 边栏的资料变更会广播事件 → 刷新资料库树。
  useEffect(() => {
    const onChanged = () => {
      getLibrary().then(setTree).catch(() => undefined);
    };
    window.addEventListener(WS_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(WS_CHANGED_EVENT, onChanged);
  }, []);

  const refreshTree = async () => {
    const lib = await getLibrary();
    setTree(lib);
  };

  // 选中会话 → 懒加载其附件（资料库视图直接用 tree 派生，无需请求）
  useEffect(() => {
    if (selected?.kind !== "session") return;
    const seq = ++sessionSeq.current;
    loadSession(selected.id)
      .then((d) => {
        if (seq !== sessionSeq.current) return;
        setSessionTitle(d.title);
        setSessionFiles((d.knowledge_files ?? []) as ResourceFile[]);
        setSessionState("ready");
      })
      .catch(() => {
        if (seq !== sessionSeq.current) return;
        setSessionState("error");
      });
  }, [selected]);

  const applySelection = (sel: SourceRef) => {
    const same =
      selected !== null &&
      selected.kind === sel.kind &&
      (!("id" in sel) || ("id" in selected && selected.id === sel.id));
    if (same) return;
    setSelected(sel);
    if (sel.kind === "session") {
      setSessionState("loading");
    }
  };

  // --- 文件夹操作 ---
  const handleCreateFolder = async (name: string) => {
    try {
      await createLibraryFolder(name);
      await refreshTree();
    } catch { /* 保持现状，可重试 */ }
  };

  const handleRenameFolder = async (id: string, name: string) => {
    try {
      await renameLibraryFolder(id, name);
      await refreshTree();
    } catch { /* 保持现状 */ }
  };

  const handleDeleteFolder = async () => {
    if (!confirmFolder || busyRef.current) return;
    busyRef.current = true;
    try {
      await deleteLibraryFolder(confirmFolder.id);
      if (selected?.kind === "folder" && selected.id === confirmFolder.id) {
        setSelected({ kind: "all" });
      }
      setConfirmFolder(null);
      await refreshTree();
    } finally {
      busyRef.current = false;
    }
  };

  // --- 文件操作 ---
  // 上传目标：文件夹视图 → 该文件夹；全部/未归档/空库 → 根（未归档）。
  const uploadTarget =
    selected?.kind === "folder" ? selected.id : "";

  const handleUpload = async (files: File[]) => {
    if (uploading) return;
    setUploading(true);
    setUploadError(null);
    try {
      const res = await uploadLibraryFiles(uploadTarget, files);
      // 200 也可能带逐文件失败（过大/格式不支持/无文本），必须显式提示
      const failures = uploadFailures(res.results);
      if (failures) setUploadError(failures);
      await refreshTree();
    } catch {
      setUploadError(tr("res.upload.failed"));
    } finally {
      setUploading(false);
    }
  };

  const handleRenameFile = async (file: ResourceFile, filename: string) => {
    await renameLibraryFile(file.id, filename);
    await refreshTree();
  };

  const handleMove = async (file: ResourceFile, folderId: string) => {
    try {
      await moveLibraryFile(file.id, folderId);
      await refreshTree();
    } catch { /* 保持现状 */ }
  };

  const handleDeleteFile = async () => {
    if (!confirmFile || busyRef.current) return;
    busyRef.current = true;
    try {
      await deleteLibraryFile(confirmFile.id);
      setConfirmFile(null);
      await refreshTree();
    } finally {
      busyRef.current = false;
    }
  };

  // --- 视图派生 ---
  const isLibraryView = selected?.kind === "all" || selected?.kind === "root" || selected?.kind === "folder";
  const currentFiles: ResourceFile[] =
    selected?.kind === "all"
      ? (tree.files as ResourceFile[])
      : selected?.kind === "root"
        ? (tree.files.filter((f) => !f.folder_id) as ResourceFile[])
        : selected?.kind === "folder"
          ? (tree.files.filter((f) => f.folder_id === selected.id) as ResourceFile[])
          : sessionFiles;

  const currentName =
    selected?.kind === "all"
      ? tr("res.all")
      : selected?.kind === "root"
        ? tr("res.root")
        : selected?.kind === "folder"
          ? (tree.folders.find((f) => f.id === selected.id)?.name ?? "")
          : sessionTitle;

  const moveTargets = (file: ResourceFile) =>
    [
      ...(file.folder_id ? [{ id: "", name: tr("res.move.root") }] : []),
      ...tree.folders
        .filter((f) => f.id !== file.folder_id)
        .map((f) => ({ id: f.id, name: f.name })),
    ];

  const globalEmpty = boot === "ready" && tree.files.length === 0 && sessions.length === 0;
  const totalChars = currentFiles.reduce((acc, f) => acc + (f.char_count || 0), 0);
  const totalChunks = currentFiles.reduce((acc, f) => acc + (f.chunk_count || 0), 0);
  const updatedAt = selected?.kind === "session"
    ? sessions.find((s) => s.session_id === selected.id)?.updated_at
    : undefined;

  return (
    <div className="page-in flex h-full">
      {tab === "textbooks" ? (
        <TextbookSidebar
          tr={tr}
          levels={taxoLevels}
          loading={taxoLoading}
          selectedGroupId={selectedGroupId}
          onSelectGroup={setSelectedGroupId}
          onOpenTextbook={setFocusTextbookId}
        />
      ) : (
        <SourceSidebar
          lang={lang}
          tr={tr}
          tree={tree}
          sessions={sessions}
          selected={selected}
          onSelect={applySelection}
          onCreateFolder={handleCreateFolder}
          onRenameFolder={handleRenameFolder}
          onDeleteFolder={setConfirmFolder}
        />
      )}

      <div className="min-w-0 flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex max-w-[1200px] flex-col gap-4">
          <header>
            <h1 className="font-serif text-xl font-semibold text-fg">{tr("res.title")}</h1>
            <p className="mt-1 text-xs text-muted">{tab === "textbooks" ? tr("res.tb.desc") : tr("res.desc")}</p>
          </header>

          {/* P4: 教材库 / 文件库 Tab —— 路由段化（Link 预取，切换零延迟） */}
          <div className="flex w-fit items-center gap-1 rounded-full bg-surface-sunken p-1">
            {(["files", "textbooks"] as const).map((t) => (
              <Link
                key={t}
                href={`/resources/${t}`}
                className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition-colors ${
                  tab === t ? "bg-surface text-accent shadow-sm" : "text-fg-secondary hover:text-fg"
                }`}
              >
                {t === "textbooks" ? tr("res.tab.textbooks") : tr("res.tab.files")}
              </Link>
            ))}
          </div>

          {tab === "textbooks" ? (
            <TextbookLibraryView
              lang={lang}
              filterGroupId={selectedGroupId}
              focusTextbookId={focusTextbookId}
              onClearFilter={() => setSelectedGroupId(null)}
              onClearFocus={() => setFocusTextbookId(null)}
            />
          ) : (
            <>
          {boot === "loading" && (
            <div className="flex flex-col gap-4">
              <Skeleton className="h-24" />
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                <Skeleton className="h-28" />
                <Skeleton className="h-28" />
                <Skeleton className="h-28" />
              </div>
            </div>
          )}

          {boot === "error" && (
            <ErrorNote
              message={tr("res.load.failed")}
              retry={() => {
                setBoot("loading");
                bootLoad();
              }}
            />
          )}

          {boot === "ready" && globalEmpty && (
            <>
              <UploadZone uploading={uploading} tr={tr} onFiles={handleUpload} />
              {uploadError && <ErrorNote message={uploadError} />}
              <EmptyState
                icon={<LibraryBig size={28} />}
                title={tr("res.empty.title")}
                desc={tr("res.empty.desc")}
              />
            </>
          )}

          {boot === "ready" && !globalEmpty && !selected && (
            <EmptyState icon={<FolderOpen size={28} />} title={tr("res.pick.hint.title")} desc={tr("res.pick.hint.desc")} />
          )}

          {boot === "ready" && !globalEmpty && selected && (
            <>
              {isLibraryView && (
                <div className="flex flex-col gap-2">
                  <UploadZone uploading={uploading} tr={tr} onFiles={handleUpload} />
                  {uploadError && <ErrorNote message={uploadError} />}
                </div>
              )}

              {selected.kind === "session" && (
                <div className="flex items-center justify-between gap-3 rounded-[10px] border border-border bg-surface px-4 py-3">
                  <div>
                    <div className="text-sm font-medium text-fg">{tr("res.session.note.title")}</div>
                    <div className="mt-0.5 text-xs text-muted">{tr("res.session.note.desc")}</div>
                  </div>
                  <Link
                    href={`/chat/${encodeURIComponent(selected.id)}`}
                    className="inline-flex h-7 shrink-0 items-center rounded-[8px] border border-border bg-surface px-2.5 text-xs font-medium text-fg-secondary transition-colors hover:border-accent hover:text-accent"
                  >
                    {tr("res.session.open")}
                  </Link>
                </div>
              )}

              {selected.kind === "session" && sessionState === "loading" && (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  <Skeleton className="h-28" />
                  <Skeleton className="h-28" />
                  <Skeleton className="h-28" />
                </div>
              )}

              {selected.kind === "session" && sessionState === "error" && (
                <ErrorNote message={tr("res.load.failed")} retry={() => setSessionState("loading")} />
              )}

              {downloadError && <ErrorNote message={downloadError} />}

              {(isLibraryView || sessionState === "ready") && (
                <>
                  <div className="flex items-baseline justify-between gap-3">
                    <h2 className="min-w-0 truncate font-serif text-base font-semibold text-fg">{currentName}</h2>
                    <div className="tnum shrink-0 text-[11px] text-muted">
                      {currentFiles.length} {tr("res.files.unit")} · {totalChars} {tr("res.chars.unit")} · {totalChunks}{" "}
                      {tr("res.chunks.unit")}
                      {updatedAt ? ` · ${tr("res.updated")} ${relTime(updatedAt, lang)}` : ""}
                    </div>
                  </div>

                  {currentFiles.length === 0 ? (
                    <EmptyState title={tr("res.empty.files")} className="py-8" />
                  ) : (
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                      {currentFiles.map((f) => (
                        <FileCard
                          key={f.id}
                          file={f}
                          lang={lang}
                          tr={tr}
                          moveTargets={isLibraryView ? moveTargets(f) : undefined}
                          onDownload={
                            f.has_original
                              ? selected.kind === "session"
                                ? () => {
                                    setDownloadError(null);
                                    downloadSessionFile(selected.id, f.id)
                                      .catch(() => setDownloadError(tr("res.download.failed")));
                                  }
                                : () => {
                                    setDownloadError(null);
                                    downloadLibraryFile(f.id)
                                      .catch(() => setDownloadError(tr("res.download.failed")));
                                  }
                              : undefined
                          }
                          onMove={isLibraryView ? (folderId) => void handleMove(f, folderId) : undefined}
                          onRename={isLibraryView ? (filename) => handleRenameFile(f, filename) : undefined}
                          onDelete={isLibraryView ? () => setConfirmFile(f) : undefined}
                        />
                      ))}
                    </div>
                  )}
                </>
              )}
            </>
          )}
            </>
          )}
        </div>
      </div>

      <ConfirmModal
        open={confirmFolder !== null}
        onClose={() => setConfirmFolder(null)}
        onConfirm={() => void handleDeleteFolder()}
        title={tr("res.delete.folder.title")}
        desc={confirmFolder ? `「${confirmFolder.name}」${tr("res.delete.folder.desc")}` : tr("res.delete.folder.desc")}
        confirmText={tr("res.confirm.delete")}
        cancelText={tr("common.cancel")}
      />
      <ConfirmModal
        open={confirmFile !== null}
        onClose={() => setConfirmFile(null)}
        onConfirm={() => void handleDeleteFile()}
        title={tr("res.delete.file.title")}
        desc={confirmFile ? `「${confirmFile.filename}」${tr("res.delete.file.desc")}` : tr("res.delete.file.desc")}
        confirmText={tr("res.confirm.delete")}
        cancelText={tr("common.cancel")}
      />
    </div>
  );
}
