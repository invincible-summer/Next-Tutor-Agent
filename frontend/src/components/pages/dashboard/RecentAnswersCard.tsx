import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { verdictTone } from "@/lib/labels";
import type { LearningRecordItem } from "@/lib/types-modules";
import type { Tr } from "./shared";

function verdictLabel(tr: Tr, verdict: string): string {
  if (verdict === "correct") return tr("answers.verdict.correct");
  if (verdict === "partial") return tr("answers.verdict.partial");
  if (verdict === "wrong") return tr("answers.verdict.wrong");
  return tr("answers.verdict.unknown");
}

/** 最近作答（L1 学习账本投影）：比系统级"平均增益"对学生更有行动性。
 * 取代了旧 EvalSummaryCard（C14：与洞察页 OverviewStats 重复）。 */
export function RecentAnswersCard({
  items,
  tr,
}: {
  items: LearningRecordItem[];
  tr: Tr;
}) {
  const rows = items.slice(0, 10);
  return (
    <Card>
      <CardHeader
        icon={<span className="text-accent">✓</span>}
        title={tr("answers.title")}
        desc={tr("answers.desc")}
      />
      {rows.length === 0 ? (
        <EmptyState title={tr("answers.empty")} desc={tr("answers.empty.desc")} />
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {rows.map((r) => (
            <li key={r.record_id} className="flex items-center gap-3 py-2">
              <Badge tone={verdictTone(r.verdict)}>
                {verdictLabel(tr, r.verdict)}
              </Badge>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-fg">
                  {r.knowledge_point || r.stem}
                </div>
                <div className="mt-0.5 truncate text-[11px] text-muted">{r.stem}</div>
              </div>
              <div className="shrink-0 text-right">
                <div className="tnum text-[11px] text-muted">
                  {new Date((r.updated_at || r.created_at) * 1000).toLocaleDateString()}
                </div>
                <div className="text-[11px] text-muted">
                  {r.source_kind === "assessment" ? tr("answers.from.assessment") : tr("answers.from.chat")}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
