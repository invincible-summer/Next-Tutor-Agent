// /insights 页面内部小工具：失败类型/提案状态的配色与格式化。
import type { BadgeTone } from "@/components/ui/Badge";

export type Tr = (key: string, fallback?: string) => string;

/** Donut 轮换配色（NONE 单独用 success）。 */
const PALETTE = [
  "rgb(var(--danger))",
  "rgb(var(--warning))",
  "rgb(var(--info))",
  "rgb(var(--muted))",
  "rgb(var(--accent))",
  "rgb(var(--accent2))",
  "rgb(var(--success))",
];

export function failureLabel(tr: Tr, ft: string): string {
  return tr(`ins.failure.${ft}`, ft || "—");
}

export function failureColor(ft: string, index: number): string {
  if (ft === "none") return "rgb(var(--success))";
  return PALETTE[index % PALETTE.length];
}

export function failureTone(ft: string): BadgeTone {
  if (!ft || ft === "none") return "success";
  if (ft === "no_assessment" || ft === "retrieval_miss") return "warning";
  return "danger";
}

export function proposalTargetTone(target: string): BadgeTone {
  switch (target) {
    case "prompt":
      return "info";
    case "policy":
      return "accent";
    case "strategy":
      return "warning";
    default:
      return "muted";
  }
}

export function proposalStatusTone(status: string): BadgeTone {
  switch (status) {
    case "proposed":
      return "warning";
    case "approved":
      return "accent";
    case "applied":
      return "success";
    default:
      return "muted";
  }
}

/** 学习增益：+0.00 格式，null 显示 —。 */
export function fmtGain(g: number | null | undefined): string {
  if (g == null) return "—";
  return `${g >= 0 ? "+" : ""}${g.toFixed(2)}`;
}
