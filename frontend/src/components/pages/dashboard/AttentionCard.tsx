import { useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge, ModuleBadge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import { Progress } from "@/components/ui/Progress";
import { dt, stateTone } from "@/lib/labels";
import type { Lang } from "@/lib/i18n";
import type { MasterySkill } from "@/lib/types-modules";
import type { Tr } from "./shared";

/** 需要关注：state=misconception 或 p_known<0.5 的技能（分页展示）。
 * 阈值与 StatCards 的"需关注"统计口径统一（C14 修正：原 0.4 与统计卡 0.5 不一致）。 */
export function AttentionCard({
  skills,
  disabled,
  lang,
  tr,
}: {
  skills: MasterySkill[];
  disabled: boolean;
  lang: Lang;
  tr: Tr;
}) {
  const [page, setPage] = useState(0);
  const rows = useMemo(
    () =>
      skills
        .filter((s) => s.state === "misconception" || s.p_known < 0.5)
        .sort((a, b) => a.p_known - b.p_known),
    [skills],
  );
  // 行内含进度条，5 条/页保持卡片紧凑
  const cur = Math.min(page, pageCount(rows.length) - 1);
  const visible = paged(rows, cur);

  return (
    <Card>
      <CardHeader
        icon={<AlertTriangle size={16} />}
        title={tr("attention.title")}
        desc={tr("attention.desc")}
        right={<ModuleBadge id="M2" />}
      />
      {disabled ? (
        <EmptyState title={tr("empty.attention")} desc={tr("empty.disabled")} />
      ) : rows.length === 0 ? (
        <EmptyState title={tr("empty.attention")} desc={tr("empty.attention.desc")} />
      ) : (
        <div className="-mx-2 flex flex-col">
          {visible.map((s) => (
            <Link
              key={s.skill_id}
              href="/knowledge"
              className="block rounded-[8px] px-2 py-2 transition-colors hover:bg-surface-hover"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm text-fg">{s.concept}</span>
                <Badge tone={stateTone(s.state)}>{dt(lang, `state.${s.state}`, s.state)}</Badge>
              </div>
              <div className="mt-1.5 flex items-center gap-2">
                <Progress
                  value={s.p_known}
                  tone={s.state === "misconception" ? "danger" : "warning"}
                  className="flex-1"
                />
                <span className="tnum w-9 shrink-0 text-right text-xs text-muted">
                  {Math.round(s.p_known * 100)}%
                </span>
              </div>
              {s.mistakes[0] && (
                <div className="mt-1 truncate text-xs text-muted">{s.mistakes[0]}</div>
              )}
            </Link>
          ))}
        </div>
      )}
      <Pager page={cur} total={rows.length} onPage={setPage} />
    </Card>
  );
}
