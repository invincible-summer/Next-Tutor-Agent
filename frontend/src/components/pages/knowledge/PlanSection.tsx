"use client";

// 教学计划区（M3 教学引擎）：由 /plan 页迁入 /knowledge 的完整计划视图——
// 六模式状态机 + 动态难度表盘 + 接下来学/该复习两栏 + 教学日志。
// 词条复用 plan 路由的 STRINGS（单一副本）；/plan 路由保留 redirect 深链。
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { BookOpen, Gauge, RotateCcw, Workflow } from "lucide-react";
import { makePageT } from "@/lib/i18n-page";
import type { Lang } from "@/lib/i18n";
import { dt } from "@/lib/labels";
import { getTeachingLog } from "@/lib/api-modules";
import type { LearningPathResp, TeachingLogResp } from "@/lib/types-modules";
import { Badge, ModuleBadge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState, ErrorNote, Skeleton } from "@/components/ui/EmptyState";
import { ModeStepper } from "@/components/pages/plan/ModeStepper";
import { PathList } from "@/components/pages/plan/PathList";
import { TeachingLog } from "@/components/pages/plan/TeachingLog";
import { DifficultyDots } from "@/components/pages/plan/DifficultyDots";
import { STRINGS as PLAN_STRINGS } from "@/app/(workspace)/plan/strings";

type LoadState = "loading" | "ok" | "error" | "disabled";

/** 教学日志加载：disabled/loading/ok/error 四态，失败降级不拖垮整页。 */
function useTeachingLog(): [LoadState, TeachingLogResp | null] {
  const [state, setState] = useState<LoadState>("loading");
  const [log, setLog] = useState<TeachingLogResp | null>(null);

  const fetchLog = useCallback(() => {
    getTeachingLog()
      .then((r) => {
        if (r.status === "disabled") {
          setState("disabled");
          return;
        }
        if (r.status === "error") {
          setState("error");
          return;
        }
        setLog(r);
        setState("ok");
      })
      .catch(() => setState("error"));
  }, []);

  useEffect(() => {
    void fetchLog();
  }, [fetchLog]);

  return [state, log];
}

export function PlanSection({
  path,
  pathLoading,
  pathError,
  lang,
}: {
  path: LearningPathResp | null;
  pathLoading: boolean;
  pathError: boolean;
  lang: Lang;
}) {
  const router = useRouter();
  const tr = makePageT(lang, PLAN_STRINGS);
  const [logState, log] = useTeachingLog();

  // 当前模式 = last_ts 最近的概念的 current_mode
  const currentMode = useMemo(() => {
    const concepts = log?.concepts ?? {};
    let best: { mode: string; ts: number } | null = null;
    for (const c of Object.values(concepts)) {
      if (c.current_mode && (best === null || c.last_ts > best.ts)) {
        best = { mode: c.current_mode, ts: c.last_ts };
      }
    }
    return best?.mode ?? null;
  }, [log]);

  const goAsk = (prefixKey: string) => (name: string) =>
    router.push(`/chat?q=${encodeURIComponent(tr(prefixKey) + name)}`);

  const difficulty = path?.difficulty ?? null;
  const state: LoadState = logState === "disabled" || path?.status === "disabled"
    ? "disabled"
    : logState === "loading" || pathLoading ? "loading"
      : logState === "error" || pathError ? "error"
        : "ok";

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <h2 className="font-serif text-base font-semibold text-fg">{tr("page.title")}</h2>
        <ModuleBadge id="M3" />
      </div>

      {state === "loading" && <Skeleton className="h-28 w-full" />}
      {state === "error" && <ErrorNote message={tr("error.load")} />}
      {state === "disabled" && (
        <EmptyState icon={<Workflow size={28} />} title={tr("disabled.title")} desc={tr("disabled.desc")} />
      )}

      {state === "ok" && log && (
        <>
          <Card>
            <CardHeader icon={<Workflow size={16} />} title={tr("mode.title")} desc={tr("mode.desc")} />
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0 flex-1">
                <ModeStepper current={currentMode} lang={lang} />
                <div className="mt-3 flex items-start gap-2 text-xs">
                  {currentMode && (
                    <Badge tone="accent" className="mt-px shrink-0">
                      {dt(lang, `mode.${currentMode}`, currentMode)}
                    </Badge>
                  )}
                  <p className="leading-relaxed text-muted">
                    {currentMode ? tr(`focus.${currentMode}`) : tr("focus.none")}
                  </p>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-4 rounded-[8px] border border-border-light bg-surface-sunken px-4 py-3">
                <div className="text-accent">
                  <Gauge size={20} />
                </div>
                <div>
                  <div className="text-xs text-muted">{tr("difficulty.label")}</div>
                  <div className="mt-0.5 flex items-center gap-2">
                    <span className="tnum text-lg font-semibold text-fg">
                      {difficulty ?? "—"}
                      <span className="text-xs font-normal text-muted"> / 5</span>
                    </span>
                    <DifficultyDots value={difficulty ?? 0} size="md" />
                  </div>
                  <div className="mt-0.5 text-[11px] text-muted">{tr("difficulty.desc")}</div>
                </div>
              </div>
            </div>
          </Card>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <PathList
              icon={<BookOpen size={16} />}
              title={tr("next.title")}
              desc={tr("next.desc")}
              items={path?.next_to_learn ?? []}
              goLabel={tr("next.go")}
              emptyText={tr("list.empty")}
              onGo={goAsk("q.learn")}
            />
            <PathList
              icon={<RotateCcw size={16} />}
              title={tr("review.title")}
              desc={tr("review.desc")}
              items={path?.review ?? []}
              goLabel={tr("review.go")}
              emptyText={tr("list.empty")}
              onGo={goAsk("q.review")}
            />
          </div>

          <TeachingLog concepts={log.concepts ?? {}} lang={lang} tr={tr} />
        </>
      )}
    </section>
  );
}
