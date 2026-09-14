"use client";
// 下一步验证行动（plan §14.3/§14.5）：next_probe 是「可选行动」，不自动
// 无限出题。展示 instruction/rationale/帮助级别/预期观察，按钮由容器注入。
import { Compass, Lightbulb } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { et, type Lang } from "@/lib/evaluation-labels";
import type { EvalNextProbe } from "@/lib/types-modules";

export function NextProbeAction({
  probe,
  lang,
  actions,
}: {
  probe: EvalNextProbe | null | undefined;
  lang: Lang;
  /** 行动按钮（如 [开始验证] [先看讲解]），由页面注入。 */
  actions?: React.ReactNode;
}) {
  if (!probe) return null;
  const t = (zh: string, en: string) => (lang === "en" ? en : zh);
  return (
    <section aria-label={t("下一步", "Next step")}
      className="rounded-[8px] border border-accent/30 bg-accent-soft/40 px-3 py-2.5">
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <Badge tone="accent">
          <Compass size={11} className="mr-1" />
          {et(lang, `eval.probe.${probe.kind || "practice"}`)}
        </Badge>
        <Badge tone="outline">{et(lang, `eval.assist.${probe.assistance || "independent"}`)}</Badge>
      </div>
      <p className="text-xs font-medium leading-relaxed text-fg">{probe.instruction}</p>
      {probe.rationale && (
        <p className="mt-1 flex items-start gap-1 text-[0.7rem] leading-relaxed text-muted">
          <Lightbulb size={11} className="mt-0.5 shrink-0" />
          {probe.rationale}
        </p>
      )}
      {probe.expected_observation && (
        <p className="mt-1 text-[0.7rem] leading-relaxed text-muted">
          {t("预期观察：", "Expected observation: ")}
          {probe.expected_observation}
        </p>
      )}
      {actions && <div className="mt-2 flex flex-wrap items-center gap-2">{actions}</div>}
    </section>
  );
}
