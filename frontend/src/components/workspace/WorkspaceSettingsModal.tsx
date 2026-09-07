"use client";
import { useEffect, useRef, useState } from "react";
import {
  BookOpen, Download, FolderOpen, Globe2,
  Loader2, Trash2, Upload,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import {
  createWorkspace, deleteWorkspaceFile, downloadLibraryFile,
  getLibrary, getWorkspace, getTextbooks, updateWorkspace, uploadFailures, uploadWorkspaceFiles,
} from "@/lib/api";
import type { TextbookListItem } from "@/lib/api";
import { notifyWsChanged, useWsSettings } from "@/lib/ws-settings";
import type { LibraryFile, LibraryFolder, LibraryTree } from "@/lib/types";
import { Modal, ConfirmModal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { ErrorNote, Skeleton } from "@/components/ui/EmptyState";
import { STRINGS } from "./strings";

type Phase = "loading" | "error" | "ready";

function fmtKb(charCount: number): string {
  return `${Math.max(1, Math.round(charCount / 1024))} KB`;
}

/** 专属夹文件行（纯存储管理：下载/删除，不参与来源选择）。 */
function FileRow({
  file,
  onDelete,
  onDownloadError,
  tr,
}: {
  file: LibraryFile;
  onDelete?: () => void;
  onDownloadError?: () => void;
  tr: (k: string, fb?: string) => string;
}) {
  return (
    <div className="group flex items-center gap-2 rounded-[8px] px-2 py-1.5 hover:bg-surface-hover">
      <span className="min-w-0 flex-1 truncate text-xs text-fg-secondary" title={file.filename}>
        {file.filename}
      </span>
      <span className="tnum shrink-0 text-[10px] text-muted">{fmtKb(file.char_count)}</span>
      {file.has_original && (
        <button
          onClick={() => {
            downloadLibraryFile(file.id).catch(() => onDownloadError?.());
          }}
          title={tr("wsm.download")}
          className="shrink-0 cursor-pointer rounded-[5px] p-1 text-muted opacity-0 transition-opacity hover:bg-surface hover:text-accent group-hover:opacity-100"
        >
          <Download size={12} />
        </button>
      )}
      {onDelete && (
        <button
          onClick={onDelete}
          title={tr("wsm.delete.file.title")}
          className="shrink-0 cursor-pointer rounded-[5px] p-1 text-muted opacity-0 transition-opacity hover:bg-danger/10 hover:text-danger group-hover:opacity-100"
        >
          <Trash2 size={12} />
        </button>
      )}
    </div>
  );
}

/** 全局工作区设置弹窗：创建/编辑合一（外层开关 + 内层 key 重置）。
 *  P6-C3：资料来源只保留教材（公用 + 我的，checkbox 写 file_id）；
 *  专属资料夹仅作存储管理（上传/下载/删除），不再参与对话检索。 */
export function WorkspaceSettingsModal() {
  const { target, close } = useWsSettings();
  return (
    <Modal
      open={target !== null}
      onClose={close}
      width={620}
      title={<Title target={target} />}
      footer={null}
    >
      {/* key=target：切换目标即整体重置表单态（避免 effect 内同步 setState） */}
      {target !== null && <Content key={target} target={target} />}
    </Modal>
  );
}

function Title({ target }: { target: "new" | string | null }) {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  return <>{target === "new" ? tr("wsm.title.new") : tr("wsm.title.edit")}</>;
}

function Content({ target }: { target: "new" | string }) {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const { close } = useWsSettings();
  const isCreate = target === "new";

  const [phase, setPhase] = useState<Phase>("loading");
  const [lib, setLib] = useState<LibraryTree | null>(null);
  const [textbooks, setTextbooks] = useState<TextbookListItem[]>([]);
  const [name, setName] = useState("");
  const [checkedFiles, setCheckedFiles] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadFailed, setUploadFailed] = useState<string | null>(null);
  const [downloadFailed, setDownloadFailed] = useState(false);
  const [confirmFile, setConfirmFile] = useState<LibraryFile | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // 初次载入：setState 只发生在异步回调里（非 effect 体同步调用）。
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getLibrary(),
      getTextbooks().catch(() => [] as TextbookListItem[]),
      target !== "new" ? getWorkspace(target) : Promise.resolve(null),
    ])
      .then(([tree, tbs, detail]) => {
        if (cancelled) return;
        setLib(tree);
        setTextbooks((tbs || []).filter((tb) => tb.status === "ready"));
        setName(detail?.name ?? "");
        setCheckedFiles(new Set(detail?.selected_file_ids ?? []));
        setPhase("ready");
      })
      .catch(() => {
        if (!cancelled) setPhase("error");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 事件上下文重试：允许同步设置 loading。 */
  const retryLoad = () => {
    setPhase("loading");
    setSaveFailed(false);
    Promise.all([
      getLibrary(),
      getTextbooks().catch(() => [] as TextbookListItem[]),
      target !== "new" ? getWorkspace(target) : Promise.resolve(null),
    ])
      .then(([tree, tbs, detail]) => {
        setLib(tree);
        setTextbooks((tbs || []).filter((tb) => tb.status === "ready"));
        setName(detail?.name ?? "");
        setCheckedFiles(new Set(detail?.selected_file_ids ?? []));
        setPhase("ready");
      })
      .catch(() => setPhase("error"));
  };

  const reloadLibrary = () =>
    getLibrary().then(setLib).catch(() => undefined);

  const toggleFiles = (fileIds: string[]) =>
    setCheckedFiles((prev) => {
      const ids = [...new Set(fileIds.filter(Boolean))];
      if (ids.length === 0) return prev;
      const next = new Set(prev);
      const allSelected = ids.every((id) => next.has(id));
      for (const id of ids) {
        if (allSelected) next.delete(id);
        else next.add(id);
      }
      return next;
    });

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0 || target === "new" || uploading) return;
    setUploading(true);
    setUploadFailed(null);
    try {
      const res = await uploadWorkspaceFiles(target, Array.from(files));
      // 200 也可能带逐文件失败（过大/格式不支持/无文本），必须显式提示
      const failures = uploadFailures(res.results);
      if (failures) setUploadFailed(failures);
      await reloadLibrary();
      notifyWsChanged();
    } catch {
      setUploadFailed(tr("wsm.upload.failed"));
    } finally {
      setUploading(false);
    }
  };

  const handleDeleteFile = async () => {
    if (!confirmFile || target === "new") return;
    try {
      await deleteWorkspaceFile(target, confirmFile.id);
    } catch { /* already gone — refresh either way */ }
    setConfirmFile(null);
    await reloadLibrary();
    notifyWsChanged();
  };

  const handleSave = async () => {
    if (!name.trim() || saving || target === null) return;
    setSaving(true);
    setSaveFailed(false);
    try {
      // P6-C3：folder_ids 已废弃（后端忽略），来源只发教材 file_ids。
      if (target === "new") {
        await createWorkspace(name.trim(), [], [...checkedFiles]);
      } else {
        await updateWorkspace(target, {
          name: name.trim(),
          folder_ids: [],
          file_ids: [...checkedFiles],
        });
      }
      notifyWsChanged();
      close();
    } catch {
      setSaveFailed(true);
    } finally {
      setSaving(false);
    }
  };

  // --- 渲染数据 ---
  const exclusiveFolder: LibraryFolder | undefined =
    !isCreate && lib ? lib.folders.find((f) => f.workspace_id === target) : undefined;
  const exclusiveFiles = exclusiveFolder
    ? (lib?.files ?? []).filter((f) => f.folder_id === exclusiveFolder.id)
    : [];
  const publicTbs = textbooks.filter((tb) => tb.scope === "public");
  const ownTbs = textbooks.filter((tb) => tb.scope !== "public");

  const tbRow = (tb: TextbookListItem) => {
    const fileIds = tb.kind === "group" ? (tb.file_ids || []) : [tb.file_id];
    const validIds = fileIds.filter(Boolean);
    const on = validIds.length > 0 && validIds.every((fid) => checkedFiles.has(fid));
    const partiallyOn = !on && validIds.some((fid) => checkedFiles.has(fid));
    return (
      <label
        key={tb.id}
        className="flex w-full cursor-pointer items-center gap-2 rounded-[6px] px-2 py-1.5 text-left transition-colors hover:bg-surface-hover"
      >
        <input
          type="checkbox"
          checked={on}
          ref={(node) => { if (node) node.indeterminate = partiallyOn; }}
          onChange={() => toggleFiles(validIds)}
          disabled={validIds.length === 0}
          aria-label={tb.title}
          className="h-3.5 w-3.5 shrink-0 cursor-pointer accent-[var(--accent)]"
        />
        <BookOpen size={13} className="shrink-0 text-accent/70" />
        <span className="min-w-0 flex-1 truncate text-[0.78rem] text-fg">
          {tb.title}
          {tb.kind === "group" && (
            <span className="text-muted"> · {validIds.length}{tr("wsm.textbooks.volumes", "卷")}</span>
          )}
          {tb.subject && <span className="text-muted"> · {tb.subject}</span>}
        </span>
        {tb.level && <Badge tone="muted">{tb.level}</Badge>}
      </label>
    );
  };

  const tbGroup = (icon: React.ReactNode, label: string, list: TextbookListItem[]) =>
    list.length > 0 && (
      <div className="rounded-[10px] border border-border-light">
        <div className="flex items-center gap-1.5 px-2 py-1.5 text-xs font-medium text-muted">
          {icon}
          {label}
          <span className="text-muted/60">{list.length}</span>
        </div>
        <div className="border-t border-border-light px-1.5 py-1">{list.map(tbRow)}</div>
      </div>
    );

  return (
    <>
      {phase === "loading" && (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-8" />
          <Skeleton className="h-28" />
        </div>
      )}

      {phase === "error" && (
        <ErrorNote message={tr("wsm.load.failed")} retry={retryLoad} />
      )}

      {phase === "ready" && (
        <div className="flex max-h-[62vh] flex-col gap-3 overflow-y-auto pr-0.5">
          {/* 名称 */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted">{tr("wsm.name.label")}</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={tr("wsm.name.placeholder")}
              className="h-9 w-full rounded-[8px] border border-border bg-bg px-3 text-sm text-fg outline-none focus:border-accent"
            />
          </div>

          {/* 资料来源（只保留教材） */}
          <div>
            <div className="mb-1 text-xs font-medium text-muted">{tr("wsm.sources")}</div>
            <p className="mb-2 text-[11px] leading-relaxed text-muted/80">{tr("wsm.sources.desc")}</p>

            <div className="flex flex-col gap-1.5">
              {tbGroup(<Globe2 size={12} className="text-accent/70" />, tr("wsm.textbooks.public", "公用教材库"), publicTbs)}
              {tbGroup(<BookOpen size={12} className="text-accent/70" />, tr("wsm.textbooks.mine", "我的教材"), ownTbs)}
              {textbooks.length === 0 && (
                <p className="px-1 py-2 text-[11px] leading-relaxed text-muted/70">{tr("wsm.empty.library")}</p>
              )}

              {/* 专属资料夹（编辑模式）：工作区拥有的跨会话共享资料 */}
              {exclusiveFolder && (
                <div className="rounded-[10px] border border-accent/30 bg-accent-soft/20">
                  <div className="flex items-center gap-2 px-2 py-1.5">
                    <FolderOpen size={14} className="shrink-0 text-accent" />
                    <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg">
                      {exclusiveFolder.name}
                    </span>
                    <Badge tone="accent">{tr("wsm.exclusive.badge")}</Badge>
                    <button
                      onClick={() => fileRef.current?.click()}
                      disabled={uploading}
                      title={tr("wsm.upload.formats")}
                      className="inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-[6px] border border-border bg-surface px-2 py-0.5 text-[11px] text-fg-secondary transition-colors hover:border-accent hover:text-accent disabled:opacity-60"
                    >
                      {uploading ? <Loader2 size={11} className="animate-spin" /> : <Upload size={11} />}
                      {uploading ? tr("wsm.uploading") : tr("wsm.upload")}
                    </button>
                  </div>
                  <div className="border-t border-accent/20 px-1.5 py-1">
                    {exclusiveFiles.length === 0 && !uploading && (
                      <p className="px-2 py-1 text-[11px] text-muted/60">{tr("res.empty.files", "暂无文件")}</p>
                    )}
                    {exclusiveFiles.map((file) => (
                      <FileRow
                        key={file.id}
                        file={file}
                        tr={tr}
                        onDelete={() => setConfirmFile(file)}
                        onDownloadError={() => setDownloadFailed(true)}
                      />
                    ))}
                    {uploadFailed && (
                      <p className="px-2 py-0.5 text-[11px] text-danger">{uploadFailed}</p>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>

          {saveFailed && <p className="text-[11px] text-danger">{tr("wsm.load.failed")}</p>}
          {downloadFailed && <p className="text-[11px] text-danger">{tr("wsm.download.failed")}</p>}
        </div>
      )}

      {/* 底部操作 */}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={close}>
          {tr("wsm.cancel")}
        </Button>
        <Button size="sm" disabled={!name.trim() || saving || phase !== "ready"} onClick={() => void handleSave()}>
          {saving ? tr("wsm.saving") : isCreate ? tr("wsm.create") : tr("wsm.save")}
        </Button>
      </div>

      {/* 隐藏上传输入：写入专属资料夹 */}
      <input
        ref={fileRef}
        type="file"
        multiple
        accept=".pdf,.docx,.pptx,.txt,.md,.markdown"
        className="hidden"
        onChange={(e) => {
          void handleUpload(e.target.files);
          e.target.value = "";
        }}
      />

      <ConfirmModal
        open={confirmFile !== null}
        onClose={() => setConfirmFile(null)}
        onConfirm={() => void handleDeleteFile()}
        title={tr("wsm.delete.file.title")}
        desc={confirmFile ? `「${confirmFile.filename}」${tr("wsm.delete.file.desc")}` : tr("wsm.delete.file.desc")}
        confirmText={tr("wsm.confirm.delete")}
        cancelText={tr("wsm.cancel")}
      />
    </>
  );
}
