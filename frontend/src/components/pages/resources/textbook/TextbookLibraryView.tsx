"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Globe2, ListChecks, RefreshCw, Square, Upload } from "lucide-react";
import type { Lang } from "@/lib/i18n";
import { makePageT } from "@/lib/i18n-page";
import { STRINGS } from "@/app/(workspace)/resources/strings";
import {
  getTextbooks,
  getTextbookFigureStatus,
  uploadTextbooks,
  rebuildTextbookGraph,
  cancelTextbookParse,
  bulkRebuildTextbooks,
  bulkCancelTextbooks,
  deleteTextbook,
  downloadTextbook,
  uploadFailures,
  type TextbookListItem,
  type TextbookRefreshMode,
} from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { WS_CHANGED_EVENT } from "@/lib/ws-settings";
import { TextbookUpload, type TextbookUploadOpts } from "./TextbookUpload";
import { TextbookCard } from "./TextbookCard";
import { TextbookDrawer } from "./TextbookDrawer";
import { EmptyState, ErrorNote, Skeleton } from "@/components/ui/EmptyState";
import { ConfirmModal, Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";

/** 教材库视图：上传（必选学段）+ 公用/我的分区卡片网格（building 轮询）+ 批量
 * 多选（批量重建三选一策略 / 批量取消，提交后由 building 轮询接管进度）+ 详情
 * 抽屉 + 删除确认。
 * 边栏联动：filterGroupId 过滤到指定教材组；focusTextbookId 变化时打开对应详情抽屉。 */
export function TextbookLibraryView({
  lang,
  filterGroupId = null,
  focusTextbookId = null,
  onClearFilter,
  onClearFocus,
}: {
  lang: Lang;
  filterGroupId?: string | null;
  focusTextbookId?: string | null;
  onClearFilter?: () => void;
  onClearFocus?: () => void;
}) {
  const tr = makePageT(lang, STRINGS);
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  const [textbooks, setTextbooks] = useState<TextbookListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [drawerId, setDrawerId] = useState<string | null>(null);
  // 边栏点卷（focusTextbookId）派生优先；卡片详情点击走本地 drawerId
  const activeDrawerId = focusTextbookId ?? drawerId;
  const [confirmDel, setConfirmDel] = useState<TextbookListItem | null>(null);
  const [confirmCancel, setConfirmCancel] = useState<TextbookListItem | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [refreshTarget, setRefreshTarget] = useState<TextbookListItem | null>(null);
  const [refreshMode, setRefreshMode] = useState<TextbookRefreshMode>("rag_graph");
  const [refreshUpgradeHint, setRefreshUpgradeHint] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // --- 批量操作：多选 + 批量重建/批量取消（提交后由既有 building 轮询接管进度） ---
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkRefreshOpen, setBulkRefreshOpen] = useState(false);
  const [bulkMode, setBulkMode] = useState<TextbookRefreshMode>("rag_graph");
  const [confirmBulkCancel, setConfirmBulkCancel] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkNote, setBulkNote] = useState<string | null>(null);

  const refresh = useCallback(() => {
    // setState 只在 Promise 回调里发生（lint: react-hooks/set-state-in-effect），
    // 与 resources 页 bootLoad 同一模式；返回 promise 供轮询/手动刷新 await。
    return getTextbooks()
      .then((tbs) => { setTextbooks(tbs); setActionError(null); })
      .catch(() => setActionError(tr("res.load.failed")))
      .finally(() => setLoading(false));
  }, [tr]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // 构建期 2s 轮询；持久 OCR 等待期降为 15s，等待本身不占并发。
  useEffect(() => {
    const hasBuilding = textbooks.some((t) => t.status === "building");
    const hasWaiting = textbooks.some((t) => t.status === "ocr_waiting");
    if (!hasBuilding && !hasWaiting) {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    if (!pollRef.current) {
      pollRef.current = setInterval(refresh, hasBuilding ? 2000 : 15000);
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [textbooks, refresh]);

  // 空闲不轮询：数据新鲜度由焦点回归 + 资料变更事件驱动（组合端点带 ETag，
  // 数据未变时 304 由浏览器缓存复用，成本极低）；防抖合并连发事件。
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const schedule = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => { void refresh(); }, 500);
    };
    window.addEventListener("focus", schedule);
    window.addEventListener(WS_CHANGED_EVENT, schedule);
    return () => {
      if (timer) clearTimeout(timer);
      window.removeEventListener("focus", schedule);
      window.removeEventListener(WS_CHANGED_EVENT, schedule);
    };
  }, [refresh]);

  const handleUpload = async (files: File[], opts: TextbookUploadOpts) => {
    if (uploading) return;
    setUploading(true);
    setUploadError(null);
    try {
      const res = await uploadTextbooks(files, opts);
      const failures = uploadFailures(res.results);
      if (failures) setUploadError(failures);
      await refresh();
    } catch {
      setUploadError(tr("res.upload.failed"));
    } finally {
      setUploading(false);
      // 提交完成即收起上传弹窗；逐文件失败提示在页头 ErrorNote 展示
      setUploadOpen(false);
    }
  };

  const handleRebuild = async () => {
    if (!refreshTarget) return;
    const target = refreshTarget;
    setRefreshTarget(null);
    try {
      await rebuildTextbookGraph(target.id, refreshMode);
      await refresh();
    } catch {
      setActionError(tr("res.load.failed"));
    }
  };

  const handleDelete = async () => {
    if (!confirmDel) return;
    const tb = confirmDel;
    setConfirmDel(null);
    try {
      await deleteTextbook(tb.id);
      await refresh();
    } catch {
      setActionError(tr("res.load.failed"));
    }
  };

  const handleCancel = async () => {
    if (!confirmCancel || cancelling) return;
    const tb = confirmCancel;
    setConfirmCancel(null);
    setCancelling(true);
    try {
      await cancelTextbookParse(tb.id);
      await refresh();
    } catch {
      setActionError(tr("res.load.failed"));
    } finally {
      setCancelling(false);
    }
  };

  const openRefresh = (tb: TextbookListItem) => {
    setRefreshMode("rag_graph");
    setRefreshUpgradeHint(false);
    setRefreshTarget(tb);
    // 旧书探测：.txt 无 [图/[页码= 标记 → 默认推荐一次「完整重新 OCR」升级
    getTextbookFigureStatus(tb.id)
      .then((s) => {
        if (!s.has_markers) {
          setRefreshUpgradeHint(true);
          setRefreshMode((m) => (m === "rag_graph" ? "full_ocr" : m));
        }
      })
      .catch(() => undefined);
  };

  // --- 批量操作 handlers ---

  const toggleSelected = useCallback((id: string) => {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const exitSelecting = useCallback(() => {
    setSelecting(false);
    setSelected(new Set());
    setBulkNote(null);
  }, []);

  const handleBulkRebuild = async () => {
    if (bulkBusy || selected.size === 0) return;
    setBulkBusy(true);
    setBulkRefreshOpen(false);
    try {
      const res = await bulkRebuildTextbooks([...selected], bulkMode);
      const queued = res.results.filter((r) => r.status === "building").length;
      setBulkNote(tr("res.tb.bulk.summary.rebuild")
        .replace("%n", String(queued))
        .replace("%k", String(res.results.length - queued)));
      setSelected(new Set());
      await refresh();
    } catch {
      setActionError(tr("res.load.failed"));
    } finally {
      setBulkBusy(false);
    }
  };

  const handleBulkCancel = async () => {
    if (bulkBusy || selected.size === 0) return;
    setConfirmBulkCancel(false);
    setBulkBusy(true);
    try {
      const res = await bulkCancelTextbooks([...selected]);
      const stopped = res.results.filter((r) => r.status === "cancelled").length;
      setBulkNote(tr("res.tb.bulk.summary.cancel")
        .replace("%n", String(stopped))
        .replace("%k", String(res.results.length - stopped)));
      setSelected(new Set());
      await refresh();
    } catch {
      setActionError(tr("res.load.failed"));
    } finally {
      setBulkBusy(false);
    }
  };

  const renderCard = (tb: TextbookListItem) => {
    const isPublic = tb.scope === "public";
    const writable = !isPublic || isAdmin; // 公用教材仅管理员可重建/删除
    return (
      <TextbookCard
        key={tb.id}
        tb={tb}
        lang={lang}
        tr={tr}
        onDetail={() => { onClearFocus?.(); setDrawerId(tb.id); }}
        onRebuild={writable ? () => openRefresh(tb) : undefined}
        onDownload={
          tb.has_original
            ? () => void downloadTextbook(tb.id).catch(() => setActionError(tr("res.download.failed")))
            : undefined
        }
        onDelete={writable ? () => setConfirmDel(tb) : undefined}
        onCancel={
          writable && (tb.status === "building" || tb.status === "ocr_waiting" || tb.status === "ocr_paused")
            ? () => setConfirmCancel(tb)
            : undefined
        }
        selecting={selecting}
        checked={selected.has(tb.id)}
        onToggleSelect={writable ? () => toggleSelected(tb.id) : undefined}
      />
    );
  };

  /** 三策略单选（单本与批量刷新弹窗共用） */
  const renderModeRadios = (mode: TextbookRefreshMode, setMode: (m: TextbookRefreshMode) => void) => (
    ([
      ["rag_graph", tr("res.tb.refresh.rag"), tr("res.tb.refresh.rag.desc")],
      ["graph_only", tr("res.tb.refresh.graph"), tr("res.tb.refresh.graph.desc")],
      ["full_ocr", tr("res.tb.refresh.ocr"), tr("res.tb.refresh.ocr.desc")],
    ] as const).map(([m, label, desc]) => (
      <label key={m} className={`block cursor-pointer rounded-lg border p-3 ${mode === m ? "border-accent bg-accent-soft/30" : "border-border"}`}>
        <span className="flex items-center gap-2 font-medium text-fg">
          <input type="radio" checked={mode === m} onChange={() => setMode(m)} /> {label}
        </span>
        <span className="mt-1 block pl-5 text-xs text-muted">{desc}</span>
      </label>
    ))
  );

  if (loading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-24" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <Skeleton className="h-28" /><Skeleton className="h-28" /><Skeleton className="h-28" />
        </div>
      </div>
    );
  }

  const publicTbs = textbooks.filter((t) => t.scope === "public");
  const ownTbs = textbooks.filter((t) => t.scope !== "public");

  // 边栏教材组过滤：命中组直接展示（跨公用/自有）；无命中则提示并展示空态
  const filtered = filterGroupId
    ? textbooks.filter((t) => t.id === filterGroupId)
    : null;
  const filterName = filterGroupId
    ? (textbooks.find((t) => t.id === filterGroupId)?.title ?? "")
    : "";

  return (
    <div className="flex flex-col gap-4">
      {/* 页头：标题 + 简介 + 上传入口（表单本体移至弹窗） */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-fg">{tr("res.tb.title", "教材库")}</h2>
          <p className="mt-0.5 max-w-xl text-xs leading-relaxed text-muted">
            {tr("res.tb.upload.intro", "支持大 PDF 教材（≤256MB），可多卷编组；上传后自动解析并构建知识图谱。")}
          </p>
        </div>
        <Button icon={<Upload size={14} />} onClick={() => setUploadOpen(true)}>
          {tr("res.tb.upload.open", "上传教材")}
        </Button>
      </div>
      {uploadError && <ErrorNote message={uploadError} />}
      {actionError && <ErrorNote message={actionError} />}
      {bulkNote && (
        <p className="rounded-[8px] bg-accent-soft/40 px-3 py-2 text-xs text-accent-strong">{bulkNote}</p>
      )}

      {/* 批量操作条：进入多选后提交批量重建（三选一策略）/ 批量取消 */}
      {textbooks.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-border bg-surface px-4 py-2.5">
          {selecting ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-fg">
                  {tr("res.tb.bulk.selected").replace("%n", String(selected.size))}
                </span>
                <Button
                  size="sm" variant="outline"
                  onClick={() => setSelected(new Set(
                    (filtered ?? [...publicTbs, ...ownTbs])
                      .filter((t) => t.scope !== "public" || isAdmin)
                      .map((t) => t.id),
                  ))}
                >
                  {tr("res.tb.bulk.selectAll")}
                </Button>
                <Button size="sm" variant="ghost" disabled={selected.size === 0} onClick={() => setSelected(new Set())}>
                  {tr("res.tb.bulk.clear")}
                </Button>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm" icon={<RefreshCw size={13} />}
                  disabled={selected.size === 0 || bulkBusy}
                  onClick={() => { setBulkMode("rag_graph"); setBulkRefreshOpen(true); }}
                >
                  {tr("res.tb.bulk.rebuild")}
                </Button>
                <Button
                  size="sm" variant="outline" icon={<Square size={13} className="fill-current" />}
                  disabled={selected.size === 0 || bulkBusy}
                  onClick={() => setConfirmBulkCancel(true)}
                >
                  {tr("res.tb.bulk.cancel")}
                </Button>
                <Button size="sm" variant="ghost" onClick={exitSelecting}>
                  {tr("res.tb.bulk.exit")}
                </Button>
              </div>
            </>
          ) : (
            <>
              <span className="text-xs text-muted">{tr("res.tb.bulk.hint")}</span>
              <Button
                size="sm" variant="outline" icon={<ListChecks size={13} />}
                onClick={() => { setSelecting(true); setBulkNote(null); }}
              >
                {tr("res.tb.bulk.enter")}
              </Button>
            </>
          )}
        </div>
      )}

      {filtered && (
        <div className="flex items-center justify-between gap-3 rounded-[10px] border border-border bg-surface px-4 py-2.5">
          <div className="min-w-0 text-sm font-medium text-fg">
            {tr("res.tb.nav.filtered", "教材组")}：{filterName || tr("res.tb.nav.filteredMiss", "该组不在当前账号可见范围")}
          </div>
          {onClearFilter && (
            <Button size="sm" variant="outline" onClick={onClearFilter}>
              {tr("res.tb.nav.clearFilter", "显示全部")}
            </Button>
          )}
        </div>
      )}

      {filtered ? (
        filtered.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map(renderCard)}
          </div>
        ) : (
          <EmptyState title={tr("res.tb.nav.filteredMiss", "该组不在当前账号可见范围")} className="py-8" />
        )
      ) : (
        <>
      {publicTbs.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="flex items-center gap-1.5 text-xs font-semibold text-muted">
            <Globe2 size={12} className="text-accent/70" />
            {tr("res.tb.public", "公用教材库")}
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {publicTbs.map(renderCard)}
          </div>
        </section>
      )}

      <section className="flex flex-col gap-2">
        {publicTbs.length > 0 && (
          <h2 className="text-xs font-semibold text-muted">{tr("res.tb.mine", "我的教材")}</h2>
        )}
        {ownTbs.length === 0 && publicTbs.length === 0 ? (
          <EmptyState
            icon={<BookOpen size={28} />}
            title={tr("res.tb.empty.title")}
            desc={tr("res.tb.empty.desc")}
          />
        ) : ownTbs.length === 0 ? (
          <EmptyState title={tr("res.tb.empty.mine", "还没有自己的教材")} className="py-8" />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {ownTbs.map(renderCard)}
          </div>
        )}
      </section>
        </>
      )}

      {/* 边栏点卷 → focusTextbookId 派生打开抽屉；卡片点击走本地状态；关闭时两者都清 */}
      <TextbookDrawer
        key={activeDrawerId ?? "closed"}
        textbookId={activeDrawerId}
        open={activeDrawerId !== null}
        lang={lang}
        tr={tr}
        canWrite={(() => {
          const t = textbooks.find((x) => x.id === activeDrawerId);
          return !t || t.scope !== "public" || isAdmin; // 公用教材仅管理员可写
        })()}
        onClose={() => { setDrawerId(null); onClearFocus?.(); }}
        onUpdated={() => void refresh()}
      />

      {/* 上传弹窗：表单 props/回调与原先内嵌时完全一致 */}
      <Modal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        title={tr("res.tb.upload.open", "上传教材")}
        width={560}
      >
        <TextbookUpload uploading={uploading} tr={tr} isAdmin={isAdmin} onFiles={handleUpload} />
      </Modal>

      <Modal
        open={refreshTarget !== null}
        onClose={() => setRefreshTarget(null)}
        title={tr("res.tb.refresh.title")}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRefreshTarget(null)}>{tr("common.cancel")}</Button>
            <Button variant={refreshMode === "full_ocr" ? "danger" : "primary"} size="sm" onClick={() => void handleRebuild()}>
              {tr("res.tb.refresh.start")}
            </Button>
          </>
        }
      >
        <div className="space-y-2">
          {refreshUpgradeHint && (
            <p className="rounded-md bg-accent-soft/40 p-2 text-xs text-accent-strong">
              {tr("res.tb.refresh.upgrade")}
            </p>
          )}
          {renderModeRadios(refreshMode, setRefreshMode)}
          {refreshMode === "full_ocr" && <p className="rounded-md bg-danger/5 p-2 text-xs text-danger">{tr("res.tb.refresh.ocr.warn")}</p>}
        </div>
      </Modal>

      {/* 批量刷新弹窗：三策略单选，批量不做旧书升级探测（需要时逐本处理） */}
      <Modal
        open={bulkRefreshOpen}
        onClose={() => setBulkRefreshOpen(false)}
        title={tr("res.tb.bulk.rebuild.title").replace("%n", String(selected.size))}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setBulkRefreshOpen(false)}>{tr("common.cancel")}</Button>
            <Button variant={bulkMode === "full_ocr" ? "danger" : "primary"} size="sm" disabled={bulkBusy} onClick={() => void handleBulkRebuild()}>
              {tr("res.tb.refresh.start")}
            </Button>
          </>
        }
      >
        <div className="space-y-2">
          <p className="text-xs text-muted">{tr("res.tb.bulk.hint")}</p>
          {renderModeRadios(bulkMode, setBulkMode)}
          {bulkMode === "full_ocr" && <p className="rounded-md bg-danger/5 p-2 text-xs text-danger">{tr("res.tb.refresh.ocr.warn")}</p>}
        </div>
      </Modal>

      <ConfirmModal
        open={confirmBulkCancel && !bulkBusy}
        onClose={() => setConfirmBulkCancel(false)}
        onConfirm={() => void handleBulkCancel()}
        title={tr("res.tb.bulk.cancel.title")}
        desc={tr("res.tb.bulk.cancel.desc").replace("%n", String(selected.size))}
        confirmText={tr("res.tb.bulk.cancel")}
        cancelText={tr("common.cancel")}
      />

      <ConfirmModal
        open={confirmDel !== null}
        onClose={() => setConfirmDel(null)}
        onConfirm={() => void handleDelete()}
        title={tr("res.tb.delete.title")}
        desc={confirmDel ? `「${confirmDel.title}」${tr("res.tb.delete.desc")}` : tr("res.tb.delete.desc")}
        confirmText={tr("res.confirm.delete")}
        cancelText={tr("common.cancel")}
      />

      <ConfirmModal
        open={confirmCancel !== null && !cancelling}
        onClose={() => setConfirmCancel(null)}
        onConfirm={() => void handleCancel()}
        title={tr("res.tb.cancel.confirm.title", "终止解析")}
        desc={confirmCancel ? `「${confirmCancel.title}」${tr("res.tb.cancel.confirm.desc")}` : tr("res.tb.cancel.confirm.desc")}
        confirmText={cancelling ? tr("res.tb.cancel.confirm.done", "已终止解析") : tr("res.tb.cancel.confirm.start", "终止解析")}
        cancelText={tr("common.cancel")}
      />
    </div>
  );
}
