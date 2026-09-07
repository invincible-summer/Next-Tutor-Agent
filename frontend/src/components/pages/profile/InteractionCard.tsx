import { HeartPulse } from "lucide-react";
import { Badge, ModuleBadge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import type { UxProfileSummary } from "@/lib/types";
import { Row } from "./Row";

type Tr = (key: string, fallback?: string) => string;

/** 右卡：M8 交互画像 —— 语气/详略/图示/节奏/耐心 + 反馈与互动信号。 */
export function InteractionCard({ ux, tr }: { ux: UxProfileSummary; tr: Tr }) {
  const { style } = ux;
  const feedback = Object.entries(ux.recent_feedback_counts);
  return (
    <Card>
      <CardHeader
        icon={<HeartPulse size={16} />}
        title={
          <span className="inline-flex items-center gap-2">
            {tr("m8.title")}
            <ModuleBadge id="M8" />
          </span>
        }
        desc={tr("m8.desc")}
      />

      <div className="flex flex-col gap-4">
        {/* 五维交互风格 */}
        <div className="divide-y divide-border-light">
          <Row label={tr("ux.tone")} value={tr(`ux.tone.${style.tone || "encouraging"}`)} />
          <Row label={tr("ux.detail")} value={tr(`ux.detail.${style.detail_level || "medium"}`)} />
          <Row label={tr("ux.visual")} value={style.visual_preference ? tr("ux.on") : tr("ux.off")} />
          <Row label={tr("ux.pacing")} value={tr(`ux.pacing.${style.pacing || "steady"}`)} />
          <Row label={tr("ux.patience")} value={tr(`ux.patience.${style.patience || "medium"}`)} />
        </div>

        {/* 近期反馈 */}
        <section>
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
            {tr("m8.feedback")}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {feedback.length > 0 ? (
              feedback.map(([k, v]) => (
                <Badge key={k} tone="muted">
                  {k.replace(/_/g, " ")} · <span className="tnum">{v}</span>
                </Badge>
              ))
            ) : (
              <span className="text-xs text-muted">—</span>
            )}
          </div>
        </section>

        {/* 互动信号 */}
        <div className="border-t border-border-light pt-2 text-[11px] text-muted">
          <div>
            {tr("ux.avg.len")} <span className="tnum">{Math.round(ux.avg_response_length)}</span>
            <span className="mx-1.5 text-border">·</span>
            {tr("ux.abandon")} <span className="tnum">{ux.abandon_signals}</span>
            <span className="mx-1.5 text-border">·</span>
            <span className="tnum">{ux.event_count}</span> events
          </div>
          <div className="mt-1">{tr("m8.sources")}</div>
        </div>
      </div>
    </Card>
  );
}
