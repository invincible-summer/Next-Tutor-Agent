"use client";

import { useCallback, useEffect, useState } from "react";
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

export default function DashboardPage() {
  const lang = useUIStore((s) => s.lang);
  const grade = useUIStore((s) => s.grade);
  const tr = makePageT(lang, STRINGS);

  const [data, setData] = useState<DashData | null>(null);
  const [error, setError] = useState(false);

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
        getUxActivity(14).catch(() => null),
        getRecentQuizQuestions()
          .then((r) => (r.status === "ok" ? r.questions : []))
          .catch(() => [] as RecentQuizQuestion[]),
        // M9 独立降级：未启用/无目标时不影响整页。
        getOrchPlan().catch(() => null),
        getOrchToday().catch(() => [] as OrchDailyTask[]),
      ]);
    return { greeting, motivation, evalWorkspaces, teachingLog, activity, recentAnswers, orchPlan, orchToday };
  }, [lang, grade]);

  useEffect(() => {
    // setState 均发生在 Promise 回调里，避免 effect 内同步 setState。
    fetchAll()
      .then((d) => {
        setData(d);
        setError(false);
      })
      .catch(() => setError(true));
  }, [fetchAll]);

  // 重试入口（事件处理器中调用，可以同步 setState）。
  const retry = () => {
    setError(false);
    fetchAll()
      .then(setData)
      .catch(() => setError(true));
  };

  return (
    <div className="h-full overflow-y-auto p-6 page-in">
      <div className="mx-auto flex max-w-[1200px] flex-col gap-4">
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
            <StatCards workspaces={data.evalWorkspaces} motivation={data.motivation} tr={tr} />
            <TodayTasksCard plan={data.orchPlan} tasks={data.orchToday} tr={tr} />
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <AttentionCard workspaces={data.evalWorkspaces} lang={lang} tr={tr} />
              <ActivityCard activity={data.activity} tr={tr} />
            </div>
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <RecentCard teachingLog={data.teachingLog} lang={lang} tr={tr} />
              <div className="flex flex-col gap-4" />
            </div>
            <RecentAnswersCard items={data.recentAnswers} tr={tr} />
          </>
        )}
      </div>
    </div>
  );
}
