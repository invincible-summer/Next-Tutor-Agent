"use client";

import { useCallback, useEffect, useState } from "react";
import { getUxActivity, getUxGreeting, getUxMotivation } from "@/lib/api";
import type { UxActivity, UxGreeting, UxMotivation } from "@/lib/types";
import {
  getLearningRecords,
  getMastery,
  getOrchPlan,
  getOrchToday,
  getTeachingLog,
} from "@/lib/api-modules";
import type {
  LearningRecordItem,
  MasteryResp,
  OrchDailyTask,
  OrchPlanSummary,
  TeachingLogResp,
} from "@/lib/types-modules";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
import { GreetingBar } from "@/components/pages/dashboard/GreetingBar";
import { StatCards } from "@/components/pages/dashboard/StatCards";
import { RadarCard } from "@/components/pages/dashboard/RadarCard";
import { ActivityCard } from "@/components/pages/dashboard/ActivityCard";
import { RecentCard } from "@/components/pages/dashboard/RecentCard";
import { AttentionCard } from "@/components/pages/dashboard/AttentionCard";
import { RecentAnswersCard } from "@/components/pages/dashboard/RecentAnswersCard";
import { TodayTasksCard } from "@/components/pages/dashboard/TodayTasksCard";
import { STRINGS } from "./strings";

interface DashData {
  greeting: UxGreeting | null;
  motivation: UxMotivation | null;
  mastery: MasteryResp;
  teachingLog: TeachingLogResp;
  activity: UxActivity | null;
  recentAnswers: LearningRecordItem[];
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
    const [greeting, motivation, mastery, teachingLog, activity, recentAnswers, orchPlan, orchToday] =
      await Promise.all([
        getUxGreeting(lang, grade).catch(() => null),
        getUxMotivation().catch(() => null),
        getMastery(),
        getTeachingLog(),
        getUxActivity(14).catch(() => null),
        getLearningRecords(10).then((r) => (r.status === "ok" ? r.items : [])).catch(() => [] as LearningRecordItem[]),
        // M9 独立降级：未启用/无目标时不影响整页。
        getOrchPlan().catch(() => null),
        getOrchToday().catch(() => [] as OrchDailyTask[]),
      ]);
    return { greeting, motivation, mastery, teachingLog, activity, recentAnswers, orchPlan, orchToday };
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

  const skills = data?.mastery.skills ?? [];
  const masteryDisabled = data?.mastery.status === "disabled";

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
            <StatCards skills={skills} motivation={data.motivation} tr={tr} />
            <TodayTasksCard plan={data.orchPlan} tasks={data.orchToday} tr={tr} />
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <RadarCard skills={skills} disabled={masteryDisabled} tr={tr} />
              <ActivityCard activity={data.activity} tr={tr} />
            </div>
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <RecentCard teachingLog={data.teachingLog} lang={lang} tr={tr} />
              <AttentionCard
                skills={skills}
                disabled={masteryDisabled}
                lang={lang}
                tr={tr}
              />
            </div>
            <RecentAnswersCard items={data.recentAnswers} tr={tr} />
          </>
        )}
      </div>
    </div>
  );
}
