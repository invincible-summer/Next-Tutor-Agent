"use client";
// 运行状态小标记（plan §14.4）：scope/pending/reconciling 等单独小标记，
// 不渲染成“第六种学生等级”，也不与概念类别共用视觉权重。
import { AlertCircle, Clock, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { et, evalStatusTone, type Lang } from "@/lib/evaluation-labels";

/** 评价运行状态徽标：ready 不显示（避免噪音），其余短暂/异常态提示。 */
export function StatusNote({
  status,
  pendingSourceCount = 0,
  lang,
  className,
}: {
  status: string | null | undefined;
  /** 待评价新来源数（工作区级 pending 小标记）。 */
  pendingSourceCount?: number;
  lang: Lang;
  className?: string;
}) {
  const s = status || "";
  const items: React.ReactNode[] = [];
  if (s && s !== "ready") {
    const Icon =
      s === "pending" ? Clock : s === "reconciling" ? RefreshCw : AlertCircle;
    items.push(
      <Badge key={s} tone={evalStatusTone(s)} className={className}>
        <Icon size={11} className="mr-1" />
        {et(lang, `eval.status.${s}`)}
      </Badge>,
    );
  }
  if (pendingSourceCount > 0) {
    items.push(
      <Badge key="pending" tone="info" className={className}>
        <Clock size={11} className="mr-1" />
        {lang === "en"
          ? `${pendingSourceCount} awaiting evaluation`
          : `${pendingSourceCount} 条待评价`}
      </Badge>,
    );
  }
  if (items.length === 0) return null;
  return <span className="inline-flex flex-wrap items-center gap-1.5">{items}</span>;
}
