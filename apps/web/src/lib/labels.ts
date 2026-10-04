// 全站共享的领域标签字典已迁入 @next-tutor/domain（跨端复用）。
// 本文件 re-export 纯语义映射，并保留 Web 表现层的 Badge 色调；
// 评价状态标签见 evaluation-labels.ts。
import type { Lang } from "./i18n";
import type { BadgeTone } from "@/components/ui/Badge";

export type { Lang };

export { KNOWLEDGE_LEVEL_ORDER, dt } from "@next-tutor/domain";

/** 教学模式 → Badge 色调（六模式一组柔和区分色）。 */
export function modeTone(mode: string): BadgeTone {
  switch (mode) {
    case "introduction":
      return "info";
    case "explanation":
      return "accent";
    case "remediation":
      return "danger";
    case "practice":
      return "warning";
    case "review":
      return "muted";
    case "challenge":
      return "accent2";
    default:
      return "muted";
  }
}

/** 判分结论 → Badge 色调。 */
export function verdictTone(verdict: string): BadgeTone {
  switch (verdict) {
    case "correct":
      return "success";
    case "partial":
      return "warning";
    case "wrong":
      return "danger";
    default:
      return "muted";
  }
}
