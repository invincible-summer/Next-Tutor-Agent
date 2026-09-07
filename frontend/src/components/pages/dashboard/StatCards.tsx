import { AlertTriangle, BookOpenCheck, CheckCircle2, Flame } from "lucide-react";
import { Stat } from "@/components/ui/Stat";
import type { MasterySkill } from "@/lib/types-modules";
import type { UxMotivation } from "@/lib/types";
import { fill, type Tr } from "./shared";

/** 四统计卡：已掌握 / 学习中 / 需关注 / 连续天数。 */
export function StatCards({
  skills,
  motivation,
  tr,
}: {
  skills: MasterySkill[];
  motivation: UxMotivation | null;
  tr: Tr;
}) {
  const mastered = skills.filter((s) => s.p_known >= 0.8).length;
  const learning = skills.filter((s) => s.p_known >= 0.5 && s.p_known < 0.8).length;
  const attention = skills.filter((s) => s.state === "misconception" || s.p_known < 0.5).length;
  return (
    <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <Stat
        icon={<CheckCircle2 size={16} />}
        label={tr("stat.mastered")}
        value={mastered}
        tone="success"
      />
      <Stat
        icon={<BookOpenCheck size={16} />}
        label={tr("stat.learning")}
        value={learning}
        tone="warning"
      />
      <Stat
        icon={<AlertTriangle size={16} />}
        label={tr("stat.attention")}
        value={attention}
        tone="danger"
      />
      <Stat
        icon={<Flame size={16} />}
        label={tr("stat.streak")}
        value={motivation?.streak_days ?? 0}
        tone="accent2"
        foot={motivation ? fill(tr("stat.active.foot"), motivation.active_days) : undefined}
      />
    </div>
  );
}
