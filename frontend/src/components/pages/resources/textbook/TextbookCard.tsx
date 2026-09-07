"use client";
import { BookOpen, Download, RefreshCw, Trash2, AlertTriangle, FileText, Square, CheckSquare } from "lucide-react";
import type { TextbookListItem } from "@/lib/api";
import type { Lang } from "@/lib/i18n";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { relTime } from "@/lib/format";
import { cn } from "@/lib/cn";

const STAGE_KEY: Record<string, string> = {
  parse: "res.tb.stage.parse",
  ocr: "res.tb.stage.ocr",
  index: "res.tb.stage.index",
  skeleton: "res.tb.stage.skeleton",
  chapters: "res.tb.stage.chapters",
  merge: "res.tb.stage.merge",
  ocr_waiting: "res.tb.stage.ocr_waiting",
  ocr_paused: "res.tb.stage.ocr_paused",
};

/** 教材卡片：标题/学科·学段徽标/章节·概念数/状态徽标（building 带进度条）/操作；
 * 批量选择模式下标题前显示复选框（仅可写项传入 onToggleSelect）。 */
export function TextbookCard({
  tb,
  lang,
  tr,
  onDetail,
  onRebuild,
  onDownload,
  onDelete,
  onCancel,
  selecting = false,
  checked = false,
  onToggleSelect,
}: {
  tb: TextbookListItem;
  lang: Lang;
  tr: (key: string, fallback?: string) => string;
  onDetail: () => void;
  onRebuild?: () => void;
  onDownload?: () => void;
  onDelete?: () => void;
  onCancel?: () => void;
  /** 批量选择模式（显示复选框） */
  selecting?: boolean;
  checked?: boolean;
  onToggleSelect?: () => void;
}) {
  const isBuilding = tb.status === "building" || tb.status === "ocr_waiting";
  const isFailed = tb.status === "graph_failed" || tb.status === "failed" || tb.status === "ocr_paused";
  const stageLabel = isBuilding
    ? `${tr(STAGE_KEY[tb.progress.stage] ?? "res.tb.stage.parse")} ${tb.progress.done}/${tb.progress.total}`
    : "";

  const iconBtn =
    "cursor-pointer rounded-[6px] p-1 text-muted opacity-0 transition-opacity group-hover:opacity-100";

  return (
    <Card
      className={cn(
        "group relative flex h-full flex-col gap-2 p-3.5 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md",
        selecting && checked && "border-accent ring-1 ring-accent/40",
      )}
      pad={false}
    >
      <div className="flex items-start gap-2.5">
        {selecting && onToggleSelect && (
          <button
            onClick={(e) => { e.stopPropagation(); onToggleSelect(); }}
            title={tr("res.tb.bulk.toggle", "选择此教材")}
            className="mt-0.5 shrink-0 cursor-pointer rounded-[6px] p-1 text-muted transition-colors hover:bg-accent-soft hover:text-accent"
          >
            {checked ? <CheckSquare size={16} className="text-accent" /> : <Square size={16} />}
          </button>
        )}
        <div className="mt-0.5 shrink-0 rounded-[10px] bg-accent-soft/60 p-1.5 text-accent">
          <BookOpen size={16} />
        </div>
        <button onClick={onDetail} className="min-w-0 flex-1 text-left">
          <div className="truncate text-sm font-medium text-fg hover:text-accent" title={tb.title}>
            {tb.title}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-muted">
            {tb.subject && <span className="tnum">{tb.subject}</span>}
            {tb.level && <span className="tnum">· {tb.level}</span>}
            {!tb.subject && !tb.level && <span className="tnum">{tb.filename}</span>}
          </div>
          {tb.kind === "group" && (tb.volumes || []).length > 0 && (
            <div
              className="mt-0.5 truncate text-[11px] text-muted/80"
              title={(tb.volumes || []).map((v) => v.filename).join("、")}
            >
              {(tb.volumes || []).map((v) => v.filename).join("、")}
            </div>
          )}
        </button>
        <div className="flex shrink-0 items-center gap-0.5">
          {onDownload && tb.has_original && (
            <button onClick={(e) => { e.stopPropagation(); onDownload(); }} title={tr("res.download")} className={`${iconBtn} hover:bg-accent-soft hover:text-accent`}>
              <Download size={14} />
            </button>
          )}
          {onRebuild && (
            <button onClick={(e) => { e.stopPropagation(); onRebuild(); }} title={tr("res.tb.refresh.tip", "刷新 RAG 与知识谱系；默认不重新 OCR")} className={`${iconBtn} hover:bg-accent-soft hover:text-accent`}>
              <RefreshCw size={14} />
            </button>
          )}
          {onCancel && (
            <button onClick={(e) => { e.stopPropagation(); onCancel(); }} title={tr("res.tb.cancel.tip", "终止当前解析（OCR / 构建）；已有文本与切片保留")} className={`${iconBtn} hover:bg-danger/10 hover:text-danger`}>
              <Square size={14} className="fill-current" />
            </button>
          )}
          {onDelete && (
            <button onClick={(e) => { e.stopPropagation(); onDelete(); }} title={tr("res.delete")} className={`${iconBtn} hover:bg-danger/10 hover:text-danger`}>
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>

      {/* 状态行：徽章/统计整体换行，不在徽章内部断字 */}
      <div className="flex flex-wrap items-center gap-2">
        {tb.scope === "public" && <Badge tone="accent">{tr("res.tb.public.badge", "公用")}</Badge>}
        {tb.kind === "group" && (
          <Badge tone="outline">
            {tr("res.tb.group.badge", "教材组")} · {(tb.file_ids || []).length} {tr("res.tb.group.volumes", "卷")}
          </Badge>
        )}
        {tb.status === "ready" && <Badge tone="success">{tr("res.tb.ready")}</Badge>}
        {isBuilding && (
          <Badge tone="accent">
            {tr("res.tb.building")}{stageLabel ? ` · ${stageLabel}` : ""}
          </Badge>
        )}
        {tb.status === "ocr_paused" && <Badge tone="warning">{tr("res.tb.ocr_paused", "OCR 已暂停")}</Badge>}
        {tb.status === "graph_failed" && (
          <Badge tone="warning">
            <AlertTriangle size={11} className="mr-0.5" />
            {tr("res.tb.graph_failed")}
          </Badge>
        )}
        {tb.status === "failed" && <Badge tone="danger">{tr("res.tb.failed")}</Badge>}
        {tb.status === "ready" && (
          <span className="tnum whitespace-nowrap text-[11px] text-muted">
            {tb.chapter_count} {tr("res.tb.chapters")} · {tb.concept_count} {tr("res.tb.concepts")}
          </span>
        )}
      </div>

      {tb.status === "ocr_waiting" && (() => {
        const volumes = Object.values(tb.ocr_state?.volumes || {});
        const pending = volumes.reduce((n, v) => n + (v.pending_pages?.length || 0), 0);
        const attempts = Math.max(0, ...volumes.flatMap((v) => Object.values(v.attempts || {})));
        const nextAt = volumes.map((v) => v.next_retry_at || 0).filter(Boolean).sort()[0];
        const blocked = volumes.some((v) => ["multimodal_not_configured", "authentication_error", "permission_error", "model_or_endpoint_not_found", "bad_request"].includes(v.last_error_code || ""));
        return (
          <div className="rounded-[6px] bg-accent-soft/20 px-2 py-1.5 text-[11px] text-muted">
            {blocked ? tr("res.tb.ocr.blocked", "多模态配置阻塞") : tr("res.tb.ocr.waiting", "等待下一次多模态 OCR")}
            {` · ${tr("res.tb.ocr.attempt", "第")}${attempts}${tr("res.tb.ocr.attempt.suffix", "次")} · ${pending} ${tr("res.tb.ocr.pages", "页待处理")}`}
            {nextAt ? ` · ${new Date(nextAt * 1000).toLocaleTimeString()}` : ""}
          </div>
        );
      })()}

      {/* building 进度条 */}
      {isBuilding && (
        <div className="h-1 w-full overflow-hidden rounded-full bg-surface-hover">
          <div
            className="h-full rounded-full bg-accent transition-all"
            style={{ width: `${Math.max(6, tb.progress.total ? (tb.progress.done / tb.progress.total) * 100 : 6)}%` }}
          />
        </div>
      )}

      {/* 失败错误 + 重试 */}
      {isFailed && (
        <div className="flex items-center justify-between gap-2 rounded-[6px] bg-danger/5 px-2 py-1.5">
          <span className="line-clamp-2 min-w-0 flex-1 text-[11px] text-danger">{tb.error || tr("res.tb.graph_failed")}</span>
          {(tb.status === "graph_failed" || tb.status === "ocr_paused") && onRebuild && (
            <button onClick={onRebuild} className="shrink-0 rounded-[6px] bg-accent px-2 py-1 text-[11px] font-medium text-white hover:opacity-90">
              {tr("res.tb.retry")}
            </button>
          )}
        </div>
      )}

      <div className="mt-auto flex items-center justify-between pt-1 text-[11px] text-muted">
        <span className="inline-flex items-center gap-1"><FileText size={11} /> {relTime(tb.updated_at || tb.created_at, lang)}</span>
        <button onClick={onDetail} className="font-medium text-accent hover:underline">{tr("res.tb.detail")}</button>
      </div>
    </Card>
  );
}
