"use client";

import { Flame } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Progress } from "@/components/ui/Progress";
import type { OrchHabit } from "@/lib/types-modules";

type Tr = (key: string, fallback?: string) => string;

function MiniStat({ label, value, unit }: { label: string; value: number; unit?: string }) {
  return (
    <div className="rounded-[8px] bg-bg px-3 py-2">
      <p className="text-[0.64rem] text-muted">{label}</p>
      <p className="tnum text-lg font-semibold leading-tight text-fg">
        {value}
        {unit && <span className="ml-0.5 text-[0.66rem] font-normal text-muted">{unit}</span>}
      </p>
    </div>
  );
}

/** 学习习惯卡：连击 / 完成率 / 活跃天数 / 拖延统计。 */
export function HabitCard({ habit, tr }: { habit: Partial<OrchHabit>; tr: Tr }) {
  const streak = habit.current_streak ?? 0;
  return (
    <Card>
      <CardHeader
        icon={<Flame size={16} />}
        title={tr("habit.title")}
        desc={tr("habit.desc")}
        right={
          streak > 0 ? (
            <span className="flex items-center gap-1 text-accent2">
              <Flame size={15} />
              <span className="tnum text-lg font-semibold">{streak}</span>
              <span className="text-[0.66rem] text-muted">{tr("habit.days")}</span>
            </span>
          ) : undefined
        }
      />
      <div className="mb-3 grid grid-cols-3 gap-2">
        <MiniStat label={tr("habit.streak.longest")} value={habit.longest_streak ?? 0} unit={tr("habit.days")} />
        <MiniStat label={tr("habit.active")} value={habit.total_active_days ?? 0} unit={tr("habit.days")} />
        <MiniStat label={tr("habit.procrastination")} value={habit.procrastination_count ?? 0} />
      </div>
      <div className="flex items-center justify-between text-[0.72rem]">
        <span className="text-muted">{tr("habit.rate")}</span>
        <span className="tnum font-medium text-fg">
          {Math.round((habit.completion_rate ?? 0) * 100)}%
          <span className="ml-1.5 text-muted/70">
            {habit.completed_tasks ?? 0}/{habit.total_tasks ?? 0}
          </span>
        </span>
      </div>
      <Progress value={habit.completion_rate ?? 0} tone="success" className="mt-1" />
    </Card>
  );
}
