"use client";
import { navigationAnchor, navigationSucceeded, navigationFailed } from "@/lib/assistant/navigation";


import { Suspense, useCallback, useEffect, useState } from "react";
import { getUxActivity, getUxGreeting, getUxMotivation } from "@/lib/api";
import type { UxActivity, UxGreeting, UxMotivation } from "@/lib/types";
import {
  getEvalWorkspaces,
  getOrchPlan,
  getOrchToday,
  getRecentQuizQuestions,
  getTeachingLog,
} from "@/lib/api-modules";
import type {
  OrchDailyTask,
  OrchPlanSummary,
  RecentQuizQuestion,
  TeachingLogResp,
  WorkspaceEvaluationListItem,
} from "@/lib/types-modules";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import {
  DeepLinkQueryReader, focusDeepTarget,
} from "@/lib/assistant/deep-link";
import { GreetingBar } from "@/components/pages/dashboard/GreetingBar";
import { StatCards } from "@/components/pages/dashboard/StatCards";
import { ActivityCard } from "@/components/pages/dashboard/ActivityCard";
import { RecentCard } from "@/components/pages/dashboard/RecentCard";
import { AttentionCard } from "@/components/pages/dashboard/AttentionCard";
import { RecentAnswersCard } from "@/components/pages/dashboard/RecentAnswersCard";
import { TodayTasksCard } from "@/components/pages/dashboard/TodayTasksCard";
import { STRINGS } from "./strings";

interface DashData {
  greeting: UxGreeting | null;
  motivation: UxMotivation | null;
  /** 各学习区简短评价近况（§14.1：点击回学习档案，不另算指标）。 */
  evalWorkspaces: WorkspaceEvaluationListItem[];
  teachingLog: TeachingLogResp;
  activity: UxActivity | null;
  recentAnswers: RecentQuizQuestion[];
  orchPlan: OrchPlanSummary | null;
  orchToday: OrchDailyTask[];
}

/** §20.1 学习总览深链 ?range=：7d/30d/this_week → 活动窗口天数。 */
function rangeToDays(range: string): number {
  if (range === "30d") return 30;
  if (range === "this_week") {
    const now = new Date();
    const weekday = (now.getDay() + 6) % 7; // 周一=0
    return weekday + 1;
  }
  return 7;
}

export default function DashboardPage() {
  const lang = useUIStore((s) => s.lang);
  const grade = useUIStore((s) => s.grade);
  const tr = makePageT(lang, STRINGS);

  const [data, setData] = useState<DashData | null>(null);
  const [error, setError] = useState(false);
  // 助手深链（§20.1 学习总览）：?ws= 定位学习区行；?range= 切活动窗口。
  const [deepWs, setDeepWs] = useState("");
  const [loadedRange, setLoadedRange] = useState("");
  const [range, setRange] = useState("7d");
  const [deepNotice, setDeepNotice] = useState("");
  useAssistantPage({
    navigationStatus: (target) => {
      if (error) return navigationFailed;
      if (!data || loadedRange !== range) return null;
      if (target.kind === "module") return navigationSucceeded;
      if (target.kind !== "dashboard_view" || range !== (target.range || "7d")) return null;
      return target.workspace_id ? navigationAnchor("dashboard-ws", target.workspace_id) : navigationSucceeded;
    },
    context: () => ({
      schema_version: 1,
      route_id: "dashboard",
      route_epoch: currentRouteEpoch(),
      workspace_id: deepWs || undefined,
      view: range,
    }),
  });
  const applyDeepLink = useCallback((params: Record<string, string>) => {
    setDeepWs(params.ws || "");
    if (params.range && ["7d", "30d", "this_week"].includes(params.range)) {
      setRange(params.range);
    }
  }, []);

  // M8 问候/动机、L1 活跃度聚合与 M9 编排允许独立降级（层关闭或无数据时
  // 不拖垮整页），M2/M3 投影失败则整页报错重试。纯取数函数，不触碰 setState。
  const fetchAll = useCallback(async (): Promise<DashData> => {
    const [greeting, motivation, evalWorkspaces, teachingLog, activity, recentAnswers, orchPlan, orchToday] =
      await Promise.all([
        getUxGreeting(lang, grade).catch(() => null),
        getUxMotivation().catch(() => null),
        // 统一评价域（G5）：无证据的学习区也出现；失败不拖垮整页。
        getEvalWorkspaces(0, 100)
          .then((r) => r.items ?? [])
          .catch(() => [] as WorkspaceEvaluationListItem[]),
        getTeachingLog(),
        getUxActivity(rangeToDays(range)).catch(() => null),
        getRecentQuizQuestions()
          .then((r) => (r.status === "ok" ? r.questions : []))
          .catch(() => [] as RecentQuizQuestion[]),
        // M9 独立降级：未启用/无目标时不影响整页。
        getOrchPlan().catch(() => null),
        getOrchToday().catch(() => [] as OrchDailyTask[]),
      ]);
    return { greeting, motivation, evalWorkspaces, teachingLog, activity, recentAnswers, orchPlan, orchToday };
  }, [lang, grade, range]);

  useEffect(() => {
    // setState 均发生在 Promise 回调里，避免 effect 内同步 setState。
    let alive = true;
    fetchAll()
      .then((d) => {
        if (!alive) return;
        setLoadedRange(range);
        setData(d);
        setError(false);
      })
      .catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, [fetchAll, range]);

  // 深链学习区定位：评价数据到达后滚动高亮对应行（§8.3）。
  useEffect(() => {
    if (!deepWs || !data) return;
    const ws = deepWs;
    // 消费走微任务：避免 effect 体内同步 setState 的级联渲染
    // （react-hooks/set-state-in-effect）。
    let alive = true;
    queueMicrotask(() => {
      if (!alive) return;
      const exists = (data.evalWorkspaces || []).some((w) => w.workspace_id === ws);
      if (!exists) {
        setDeepNotice(tr("deep.wsMissing"));
        return;
      }
      window.setTimeout(() => {
        if (!focusDeepTarget("dashboard-ws", ws)) setDeepNotice(tr("deep.wsMissing"));
      }, 60);
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepWs, data]);

  // 重试入口（事件处理器中调用，可以同步 setState）。
  const retry = () => {
    setError(false);
    fetchAll()
      .then(setData)
      .catch(() => setError(true));
  };

  return (
    <div className="h-full overflow-y-auto p-6 page-in">
      <Suspense><DeepLinkQueryReader keys={["ws", "range"]} onParams={applyDeepLink} /></Suspense>
      <div className="mx-auto flex max-w-[1200px] flex-col gap-4">
        {deepNotice && (
          <div className="rounded-[8px] border border-border bg-surface px-3 py-2 text-xs text-muted">{deepNotice}</div>
        )}
        {error ? (
          <ErrorNote message={tr("error.load")} retry={retry} />
        ) : !data ? (
          <PageSkeleton />
        ) : (
          <>
            <GreetingBar
              greeting={data.greeting?.greeting ?? null}
              tr={tr}
            />
            {/* 第一阅读区：今日行动优先于纯统计 */}
            <TodayTasksCard plan={data.orchPlan} tasks={data.orchToday} tr={tr} />
            <StatCards workspaces={data.evalWorkspaces} motivation={data.motivation} tr={tr} />
            {/* 待解决优先于活动：行动区 3:2 非对称分栏 */}
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
              <AttentionCard className="xl:col-span-3" deepWorkspaceId={deepWs} workspaces={data.evalWorkspaces} lang={lang} tr={tr} />
              <ActivityCard className="xl:col-span-2" activity={data.activity} tr={tr} />
            </div>
            {/* 回顾层：扁平分区，标题 + 分隔线组织，视觉退后 */}
            <div className="grid grid-cols-1 gap-x-8 gap-y-6 border-t border-border-light pt-5 xl:grid-cols-2">
              <RecentCard teachingLog={data.teachingLog} lang={lang} tr={tr} />
              <RecentAnswersCard items={data.recentAnswers} tr={tr} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
