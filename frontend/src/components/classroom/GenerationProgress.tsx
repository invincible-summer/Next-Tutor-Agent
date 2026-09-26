"use client";
/* 生成进度（plan.md §4.2 / §14.4 / E03）。
 * 六阶段可理解文案 + 页数进度（来自阶段产物，不按时间伪造）；SSE 订阅
 * （认证 fetch 读流，断线指数退避 1/2/4/8→15s，多次失败回落轮询）；
 * 离开页面立即 abort；SSE 断开不影响生成。取消 = POST；失败可重试；
 * awaiting_outline 支持改标题后确认继续。不展示思维链或原始 prompt。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Eye, Loader2,
  RotateCcw, XCircle,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { cn } from "@/lib/cn";
import { cancelJob, continueJob, getJob, retryJob, subscribeJobEvents } from "@/lib/api-classroom";
import type { JobPublic } from "@/lib/types-classroom.generated";
import { Button } from "@/components/ui/Button";
import { STRINGS } from "./strings";

/** §4.2 六阶段：内部 phase → 用户可见阶段（视觉/检查点/审校并入编写与排版）。 */
const VISIBLE_STAGES = [
  { key: "resolve_sources", phases: ["resolve_sources"] },
  { key: "research", phases: ["research"] },
  { key: "outline", phases: ["outline"] },
  { key: "author_slides", phases: ["author_slides", "visual_assets", "checkpoints", "review"] },
  { key: "render", phases: ["render"] },
  { key: "publish", phases: ["publish"] },
] as const;

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

export function GenerationProgress({ workspaceId, lessonId, job,
  onUpdated, compact }: {
  workspaceId: string;
  lessonId: string;
  /** 初始 snapshot（来自 GET L/GET J；组件随后用 SSE/轮询保持最新）。 */
  job: JobPublic;
  /** 每次状态变化向上冒泡（详情页据此重载课程）。 */
  onUpdated?: (job: JobPublic) => void;
  /** 列表卡内的紧凑形态：只显示阶段行 + 进度。 */
  compact?: boolean;
}) {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const [current, setCurrent] = useState<JobPublic>(job);
  const [conn, setConn] = useState<"live" | "poll" | "off">("live");
  const [outlineOpen, setOutlineOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [outlineTitles, setOutlineTitles] = useState<Record<string, string>>({});
  const notifiedRef = useRef(false);
  const isTerminal = TERMINAL.has(current.state);

  const apply = useCallback((next: Partial<JobPublic> & { state_revision: number }) => {
    setCurrent((prev) =>
      next.state_revision > prev.state_revision
        ? ({ ...prev, ...next } as JobPublic)
        : prev);
  }, []);

  // 状态变化向上冒泡（跳过首次，避免 mount 时的重复加载）。
  useEffect(() => {
    if (!onUpdated) return;
    if (!notifiedRef.current) { notifiedRef.current = true; return; }
    onUpdated(current);
  }, [current, onUpdated]);

  // SSE（§14.4）：显式 AbortSignal；断开指数退避；连续失败转 10s 轮询。
  useEffect(() => {
    if (done) return;
    const controller = new AbortController();
    let attempt = 0;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    const startPolling = () => {
      if (pollTimer || stopped) return;
      setConn("poll");
      pollTimer = setInterval(() => {
        getJob(workspaceId, lessonId, current.job_id)
          .then((snap) => {
            apply(snap);
            if (TERMINAL.has(snap.state)) controller.abort();
          })
          .catch(() => undefined);
      }, 10_000);
    };

    const subscribe = () => {
      if (stopped || controller.signal.aborted) return;
      subscribeJobEvents(workspaceId, lessonId, current.job_id, {
        onSnapshot: (snap) => {
          attempt = 0;
          setConn("live");
          apply({
            state_revision: snap.state_revision, state: snap.state,
            phase: snap.phase,
            progress: {
              completed_slides: snap.completed_slides ?? 0,
              total_slides: snap.total_slides ?? 0,
            },
            warnings: snap.warnings ?? [],
          });
        },
        onTerminal: (snap) => {
          apply({
            state_revision: snap.state_revision, state: snap.state,
            phase: snap.phase,
          });
          controller.abort();
        },
        onError: () => {
          if (stopped || controller.signal.aborted) return;
          attempt += 1;
          if (attempt >= 4) { startPolling(); return; }
          const delay = Math.min(1000 * 2 ** (attempt - 1), 15_000);
          retryTimer = setTimeout(subscribe, delay);
        },
      }, controller.signal);
    };
    subscribe();
    return () => {
      stopped = true;
      controller.abort(); // 离开页面立即断开（不影响服务端生成）
      if (pollTimer) clearInterval(pollTimer);
      if (retryTimer) clearTimeout(retryTimer);
    };
    // 依赖 isTerminal：终态时挂断订阅；重试回 queued 再重新接上。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, lessonId, current.job_id, isTerminal]);

  const stageIndex = useMemo(() => {
    const idx = VISIBLE_STAGES.findIndex((s) =>
      s.phases.includes((current.phase ?? "") as never));
    return idx;
  }, [current.phase]);

  const progress = current.progress ?? {};
  const done = TERMINAL.has(current.state);
  const connLabel = done ? "off" : conn;
  const generating = current.state === "queued" || current.state === "running";

  const doCancel = async () => {
    if (busy) return;
    setBusy("cancel");
    try {
      apply(await cancelJob(workspaceId, lessonId, current.job_id,
        { expected_state_revision: current.state_revision }));
    } catch { /* CAS 冲突/已终态：下一次快照会带真实状态 */ }
    finally { setBusy(null); }
  };

  const doRetry = async () => {
    if (busy) return;
    setBusy("retry");
    try {
      apply(await retryJob(workspaceId, lessonId, current.job_id,
        { expected_state_revision: current.state_revision }));
    } catch { /* CAS 冲突：等下一次快照 */ }
    finally { setBusy(null); }
  };

  const doContinue = async () => {
    if (busy) return;
    setBusy("continue");
    try {
      const snap = await continueJob(workspaceId, lessonId, current.job_id,
        { expected_state_revision: current.state_revision });
      apply(snap);
    } catch { /* 冲突/来源变化：等快照后按 next_actions 提示 */ }
    finally { setBusy(null); }
  };

  if (compact) {
    return (
      <div className="flex items-center gap-1.5 text-[0.7rem] text-accent-strong/90">
        {generating && <Loader2 size={11} className="animate-spin" />}
        {stageLabel(tr, current.phase)}
        {progress.total_slides
          ? ` · ${progress.completed_slides ?? 0}/${progress.total_slides}`
          : ""}
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-4">
      {/* 阶段链 */}
      <ol className="flex flex-col gap-1.5" aria-label={tr("cls.progress.stages")}>
        {VISIBLE_STAGES.map((stage, i) => {
          const state = done && current.state === "succeeded" ? "done"
            : i < stageIndex ? "done"
            : i === stageIndex ? (done ? "error" : "active")
            : "todo";
          return (
            <li key={stage.key} className="flex items-center gap-2.5">
              <span className={cn(
                "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[0.65rem]",
                state === "done" && "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
                state === "active" && "border-accent bg-accent-soft/50 text-accent-strong",
                state === "error" && "border-danger/40 bg-danger/10 text-danger",
                state === "todo" && "border-border text-muted/60",
              )}>
                {state === "done" ? <CheckCircle2 size={13} /> :
                 state === "active" ? <Loader2 size={12} className="animate-spin" /> :
                 state === "error" ? <XCircle size={13} /> : i + 1}
              </span>
              <span className={cn(
                "text-[0.78rem]",
                state === "active" ? "font-medium text-fg" :
                state === "todo" ? "text-muted/70" : "text-fg-secondary",
              )}>
                {tr(`cls.stage.${stage.key}`)}
              </span>
            </li>
          );
        })}
      </ol>

      {/* 页数进度 */}
      {(progress.total_slides ?? 0) > 0 && (
        <p className="tnum text-[0.72rem] text-muted">
          {tr("cls.progress.pages").replace("%c", String(progress.completed_slides ?? 0))
            .replace("%t", String(progress.total_slides))}
        </p>
      )}
      {!done && (
        <p className="text-[0.7rem] text-muted/80">{tr("cls.progress.minutes")}</p>
      )}
      {connLabel === "poll" && !done && (
        <p className="text-[0.68rem] text-amber-600 dark:text-amber-400">{tr("cls.progress.polling")}</p>
      )}

      {/* 可见失败原因 / 警告（§4.2：不展示内部思维链或原始 prompt） */}
      {current.state === "failed" && (
        <div className="rounded-[10px] border border-danger/30 bg-danger/5 px-3 py-2.5">
          <p className="flex items-center gap-1.5 text-[0.75rem] font-medium text-danger">
            <AlertTriangle size={13} />
            {tr("cls.progress.failed")}
            {stageLabel(tr, current.phase) ? ` · ${stageLabel(tr, current.phase)}` : ""}
          </p>
          {current.last_error && (
            <p className="mt-1 break-all text-[0.7rem] text-danger/90">{current.last_error.slice(0, 200)}</p>
          )}
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" variant="outline" icon={busy === "retry" ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />} disabled={Boolean(busy)} onClick={() => void doRetry()}>
              {tr("cls.progress.retry")}
            </Button>
            <span className="self-center text-[0.68rem] text-muted/80">{tr("cls.progress.or.settings")}</span>
          </div>
        </div>
      )}
      {current.state === "cancelled" && (
        <p className="rounded-[10px] border border-border bg-surface-hover px-3 py-2 text-[0.72rem] text-muted">
          {tr("cls.progress.cancelled")}
          <Button size="sm" variant="ghost" className="ml-2" disabled={Boolean(busy)} onClick={() => void doRetry()}>
            {tr("cls.progress.retry")}
          </Button>
        </p>
      )}
      {current.state === "succeeded" && (
        <p className="flex items-center gap-1.5 rounded-[10px] border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-[0.75rem] text-emerald-700 dark:text-emerald-400">
          <CheckCircle2 size={14} />
          {tr("cls.progress.ready")}
        </p>
      )}
      {current.state === "needs_input" && (
        <div className="rounded-[10px] border border-amber-500/30 bg-amber-500/10 px-3 py-2.5 text-[0.73rem] text-amber-800 dark:text-amber-300">
          <p>{tr("cls.progress.needs_input")}</p>
          {(current.next_actions ?? []).length > 0 && (
            <p className="mt-1 text-[0.68rem] opacity-80">{current.next_actions!.join(" · ")}</p>
          )}
        </div>
      )}

      {/* 大纲审核（§4.1：选了「先看大纲」的用户在这里确认后继续） */}
      {current.state === "awaiting_outline" && (
        <div className="rounded-[10px] border border-border bg-surface px-3 py-2.5">
          <button
            type="button"
            onClick={() => setOutlineOpen((v) => !v)}
            aria-expanded={outlineOpen}
            className="flex w-full cursor-pointer items-center gap-1 text-[0.78rem] font-medium text-fg"
          >
            {outlineOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            {tr("cls.progress.outline.review")}
          </button>
          <p className="mt-1 text-[0.7rem] text-muted">{tr("cls.progress.outline.hint")}</p>
          {outlineOpen && <OutlineReviewBlock workspaceId={workspaceId} lessonId={lessonId} jobId={current.job_id} titles={outlineTitles} onTitles={setOutlineTitles} />}
          <div className="mt-2 flex gap-2">
            <Button size="sm" disabled={Boolean(busy)} onClick={() => void doContinue()}>
              {busy === "continue" ? <Loader2 size={12} className="animate-spin" /> : null}
              {tr("cls.progress.outline.confirm")}
            </Button>
            <Button size="sm" variant="ghost" disabled={Boolean(busy)} onClick={() => void doCancel()}>
              {tr("cls.progress.cancel")}
            </Button>
          </div>
        </div>
      )}

      {/* 操作：取消（进行中）/ 重试（失败） */}
      {generating && (
        <div className="flex items-center gap-2">
          {current.cancel_requested ? (
            <p className="text-[0.7rem] text-muted">{tr("cls.progress.cancelling")}</p>
          ) : (
            <Button size="sm" variant="outline" icon={busy === "cancel" ? <Loader2 size={12} className="animate-spin" /> : <XCircle size={12} />} disabled={Boolean(busy)} onClick={() => void doCancel()}>
              {tr("cls.progress.cancel")}
            </Button>
          )}
        </div>
      )}

      {/* 失败草稿预览入口（详情页在生成未完成时渲染 preview 面板） */}
      {current.state === "failed" && (progress.completed_slides ?? 0) > 0 && (
        <p className="flex items-center gap-1.5 text-[0.7rem] text-muted">
          <Eye size={12} />
          {tr("cls.progress.draft.note")}
        </p>
      )}
    </div>
  );
}

function stageLabel(tr: (k: string, fb?: string) => string, phase: string | null | undefined) {
  if (!phase) return "";
  const stage = VISIBLE_STAGES.find((s) => (s.phases as readonly string[]).includes(phase));
  return stage ? tr(`cls.stage.${stage.key}`) : phase;
}

/** 大纲只读 + 标题微调（结构编辑不在首发范围；PATCH outline 保持结构）。 */
function OutlineReviewBlock({ workspaceId, lessonId, jobId, titles, onTitles }: {
  workspaceId: string; lessonId: string; jobId: string;
  titles: Record<string, string>; onTitles: (t: Record<string, string>) => void;
}) {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const [outline, setOutline] = useState<{ pages: { title: string; order: number }[]; scope_note?: string } | null>(null);
  useEffect(() => {
    let cancelled = false;
    // preview 端点复用为大纲读取（只读草稿 DTO，§14.1）
    import("@/lib/api-classroom").then(({ getJobPreview }) =>
      getJobPreview(workspaceId, lessonId, jobId)
        .then((p) => { if (!cancelled) setOutline(p.outline ?? null); })
        .catch(() => undefined));
    return () => { cancelled = true; };
  }, [workspaceId, lessonId, jobId]);
  if (!outline) {
    return <p className="mt-2 text-[0.7rem] text-muted">{tr("cls.progress.outline.loading")}</p>;
  }
  return (
    <ol className="mt-2 flex flex-col gap-1 border-l border-border-light pl-3">
      {outline.pages.map((p) => (
        <li key={p.order} className="flex items-baseline gap-2 text-[0.73rem]">
          <span className="tnum w-4 shrink-0 text-muted">{p.order}</span>
          <input
            value={titles[`${p.order}`] ?? p.title}
            maxLength={120}
            onChange={(e) => onTitles({ ...titles, [`${p.order}`]: e.target.value })}
            className="w-full rounded-[6px] border border-transparent bg-transparent px-1.5 py-0.5 text-fg-secondary outline-none transition-colors hover:border-border focus:border-accent focus:bg-surface"
            aria-label={`outline page ${p.order}`}
          />
        </li>
      ))}
      {outline.scope_note && (
        <p className="mt-1 text-[0.68rem] text-muted/80">{outline.scope_note}</p>
      )}
    </ol>
  );
}
