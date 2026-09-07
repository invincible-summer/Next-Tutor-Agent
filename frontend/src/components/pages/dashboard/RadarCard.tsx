import { useMemo } from "react";
import { Radar as RadarIcon } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { ModuleBadge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Radar } from "@/components/charts/Radar";
import type { MasterySkill } from "@/lib/types-modules";
import type { Tr } from "./shared";

/** 学科掌握雷达：按 subject 分组取 p_known 均值。 */
export function RadarCard({
  skills,
  disabled,
  tr,
}: {
  skills: MasterySkill[];
  disabled: boolean;
  tr: Tr;
}) {
  const axes = useMemo(() => {
    const groups = new Map<string, { sum: number; n: number }>();
    for (const s of skills) {
      const g = groups.get(s.subject) ?? { sum: 0, n: 0 };
      g.sum += s.p_known;
      g.n += 1;
      groups.set(s.subject, g);
    }
    return [...groups.entries()]
      .map(([label, g]) => ({ label, value: g.sum / g.n }))
      .sort((a, b) => b.value - a.value);
  }, [skills]);

  return (
    <Card>
      <CardHeader
        icon={<RadarIcon size={16} />}
        title={tr("radar.title")}
        desc={tr("radar.desc")}
        right={<ModuleBadge id="M2" />}
      />
      {disabled ? (
        <EmptyState title={tr("empty.mastery")} desc={tr("empty.disabled")} />
      ) : axes.length === 0 ? (
        <EmptyState title={tr("empty.mastery")} desc={tr("empty.mastery.desc")} />
      ) : (
        <div className="mx-auto w-full max-w-[340px]">
          <Radar axes={axes} />
        </div>
      )}
    </Card>
  );
}
