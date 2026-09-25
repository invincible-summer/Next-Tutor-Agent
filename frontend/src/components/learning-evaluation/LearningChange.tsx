"use client";
// 「与之前相比」区块（plan §14.3）：方向 + 可比性 + 陈述 + 替代解释。
// 任务帮助不同 → 明确“暂不宣称能力提升”，不做数值增益。
import { ArrowDownRight, ArrowUpRight, Minus, Shuffle } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { changeTone, et, type Lang } from "@/lib/evaluation-labels";
import type { EvalLearningChange } from "@/lib/types-modules";

const DIR_ICON: Record<string, React.ReactNode> = {
  strengthened: <ArrowUpRight size={12} />,
  weakened: <ArrowDownRight size={12} />,
  mixed: <Shuffle size={12} />,
  stable: <Minus size={12} />,
};

export function LearningChange({
  change,
  lang,
}: {
  change: EvalLearningChange | null | undefined;
  lang: Lang;
}) {
  if (!change) return null;
  const dir = change.direction || "unknown";
  const cmp = change.comparison || "no_prior";
  const label = et(lang, `eval.change.${dir}`);
  return (
    <section aria-label={lang === "en" ? "Compared with before" : "与之前相比"}
      className="rounded-[8px] border border-border-light bg-surface px-3 py-2.5">
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <Badge tone={changeTone(dir)}>
          {DIR_ICON[dir] ?? null}
          {label}
        </Badge>
        <Badge tone="outline">{et(lang, `eval.cmp.${cmp}`)}</Badge>
      </div>
      {change.statement && (
        <p className="text-xs leading-relaxed text-fg-secondary">{change.statement}</p>
      )}
      {change.alternative_explanations?.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {change.alternative_explanations.map((a, i) => (
            <li key={i} className="text-[0.7rem] leading-relaxed text-muted">
              · {a}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
