// 全站共享的领域标签：掌握度状态、教学模式、边的类型、反馈信号等。
// 双语内置，避免每个页面重复定义。
import type { Lang } from "./i18n";
import type { BadgeTone } from "@/components/ui/Badge";

export type { Lang };

type Dict = Record<string, string>;

/** 学段固定展示顺序（未知学段按字典序追加在后）。后端 taxonomy/catalog 同序。 */
export const KNOWLEDGE_LEVEL_ORDER: string[] = ["小学", "初中", "高中", "本科", "其他"];

const ZH: Dict = {
  "state.understood": "已掌握",
  "state.partial": "部分理解",
  "state.misconception": "存在误解",
  "state.introduced": "已接触",
  "state.unknown": "未评估",
  "mode.introduction": "新知引入",
  "mode.explanation": "概念讲解",
  "mode.remediation": "纠错巩固",
  "mode.practice": "练习训练",
  "mode.review": "复习回顾",
  "mode.challenge": "挑战拓展",
  "edge.prerequisite": "前置",
  "edge.related": "相关",
  "edge.part_of": "组成",
  "edge.application": "应用",
  "edge.misconception": "易混",
  "event.concept_taught": "学习概念",
  "event.quiz_graded": "完成测验",
  "event.goal_set": "设定目标",
  "verdict.correct": "回答正确",
  "verdict.partial": "部分正确",
  "verdict.wrong": "回答错误",
};

const EN: Dict = {
  "state.understood": "Mastered",
  "state.partial": "Partial",
  "state.misconception": "Misconception",
  "state.introduced": "Introduced",
  "state.unknown": "Unassessed",
  "mode.introduction": "Introduction",
  "mode.explanation": "Explanation",
  "mode.remediation": "Remediation",
  "mode.practice": "Practice",
  "mode.review": "Review",
  "mode.challenge": "Challenge",
  "edge.prerequisite": "Prerequisite",
  "edge.related": "Related",
  "edge.part_of": "Part of",
  "edge.application": "Application",
  "edge.misconception": "Confusable",
  "event.concept_taught": "Concept taught",
  "event.quiz_graded": "Quiz graded",
  "event.goal_set": "Goal set",
  "verdict.correct": "Correct",
  "verdict.partial": "Partially correct",
  "verdict.wrong": "Wrong",
};

/** 领域标签翻译：state.* / mode.* / edge.* / event.* / verdict.* */
export function dt(lang: Lang, key: string, fallback?: string): string {
  return (lang === "en" ? EN : ZH)[key] ?? fallback ?? key;
}

/** 掌握度状态 → Badge 色调。 */
export function stateTone(state: string): BadgeTone {
  switch (state) {
    case "understood":
      return "success";
    case "partial":
      return "warning";
    case "misconception":
      return "danger";
    case "introduced":
      return "info";
    default:
      return "muted";
  }
}

/** 掌握度状态 → CSS 颜色变量（图表/图谱节点用）。 */
export function stateColor(state: string): string {
  switch (state) {
    case "understood":
      return "rgb(var(--m-mastered))";
    case "partial":
      return "rgb(var(--m-learning))";
    case "misconception":
      return "rgb(var(--m-weak))";
    default:
      return "rgb(var(--m-todo))";
  }
}

/** p_known → 四态颜色（无 state 时按数值推断）。 */
export function masteryColor(p: number | null | undefined): string {
  if (p == null) return "rgb(var(--m-todo))";
  if (p >= 0.8) return "rgb(var(--m-mastered))";
  if (p >= 0.5) return "rgb(var(--m-learning))";
  return "rgb(var(--m-weak))";
}

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
