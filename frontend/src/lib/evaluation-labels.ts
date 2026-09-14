// 统一学习评价的唯一 state/condition/process 文案与 tone 映射（plan §14/§15.3）。
// 中英双语；所有页面（Memory/ConceptDrawer/QuizCard/…）共用这里，
// 禁止在不同页面复制状态翻译，禁止把类别映射成 0-100 数值。
import type { Lang } from "./i18n";
import type { BadgeTone } from "@/components/ui/Badge";

export type { Lang };

type Dict = Record<string, string>;

const ZH: Dict = {
  // ConceptEvalState（§4.4：类别不是等级阶梯）
  "eval.state.not_observed": "尚无学习证据",
  "eval.state.emerging": "已有局部证据",
  "eval.state.supported_in_scope": "在这些条件下已有支持",
  "eval.state.fragile": "有明确待解决点",
  "eval.state.conflicting": "证据尚待核对",
  // EvaluationStatus（运行状态，不是第六种学生等级）
  "eval.status.ready": "评价就绪",
  "eval.status.pending": "评价处理中",
  "eval.status.reconciling": "正在重新核对",
  "eval.status.unavailable": "暂时无法完成评价",
  "eval.status.disabled": "评价未启用",
  // ClaimStatus
  "eval.claim.supported": "有证据支持",
  "eval.claim.tentative": "初步观察",
  "eval.claim.challenged": "受到反例质疑",
  "eval.claim.unobserved": "未观察",
  // ChangeDirection / ChangeComparison
  "eval.change.strengthened": "比之前更稳",
  "eval.change.weakened": "比之前弱",
  "eval.change.mixed": "有进有退",
  "eval.change.stable": "基本持平",
  "eval.change.unknown": "不足以比较",
  "eval.cmp.comparable": "条件相近，可比较",
  "eval.cmp.partially_comparable": "任务帮助不同，只能部分比较",
  "eval.cmp.not_comparable": "任务帮助不同，暂不比较",
  "eval.cmp.no_prior": "这是第一次观察",
  // ProbeKind / AssistanceLevel
  "eval.probe.explain": "用自己的话解释",
  "eval.probe.practice": "完成一次练习",
  "eval.probe.variant": "换一种形式再做一题",
  "eval.probe.transfer": "在新情境中运用",
  "eval.probe.delayed_recheck": "过几天再确认一次",
  "eval.probe.self_check": "自己检查并说明依据",
  "eval.assist.full_demo": "看过完整示范",
  "eval.assist.key_hints": "有关键提示",
  "eval.assist.independent": "独立完成",
  // EvidenceConditionKind
  "eval.cond.ordinary": "常规练习条件",
  "eval.cond.transfer": "新情境迁移条件",
  "eval.cond.retention": "间隔保持条件",
  "eval.cond.self_check": "自检条件",
  // BloomProcess（实际表现方式：只展示有引用的标签，不做六层金字塔）
  "eval.process.remember": "回忆",
  "eval.process.understand": "理解",
  "eval.process.apply": "应用",
  "eval.process.analyze": "分析",
  "eval.process.evaluate": "评价",
  "eval.process.create": "创造",
  // KnowledgeType（仅在详细依据中显示）
  "eval.ktype.factual": "事实性知识",
  "eval.ktype.conceptual": "概念性知识",
  "eval.ktype.procedural": "程序性知识",
  "eval.ktype.metacognitive": "元认知知识",
  // SourceKind / 帮助事件
  "eval.source.dialogue": "对话解释",
  "eval.source.assessment": "习题作答",
  "eval.hint.hint_requested": "查看了提示",
  "eval.hint.answer_revealed": "查看了答案",
  "eval.hint.worked_example": "看过完整示范",
  "eval.hint.teacher_probe": "教师追问引导",
  "eval.hint.prior_exposure": "此前接触过同类型题",
  // ScopeStatus / availability
  "eval.scope.current": "当前范围",
  "eval.scope.out_of_scope": "已超出当前教材范围",
  "eval.scope.source_removed": "原始材料已删除",
  "eval.scope.needs_mapping": "教材改版后需要重新对应",
  "eval.avail.available": "材料可用",
  "eval.avail.archived": "材料已归档",
  "eval.avail.deleted": "材料已删除",
};

const EN: Dict = {
  "eval.state.not_observed": "No learning evidence yet",
  "eval.state.emerging": "Some local evidence",
  "eval.state.supported_in_scope": "Supported under these conditions",
  "eval.state.fragile": "Clear point to resolve",
  "eval.state.conflicting": "Evidence needs checking",
  "eval.status.ready": "Evaluation ready",
  "eval.status.pending": "Evaluating",
  "eval.status.reconciling": "Re-checking",
  "eval.status.unavailable": "Evaluation unavailable",
  "eval.status.disabled": "Evaluation disabled",
  "eval.claim.supported": "Supported by evidence",
  "eval.claim.tentative": "Preliminary observation",
  "eval.claim.challenged": "Challenged by counter-evidence",
  "eval.claim.unobserved": "Not observed",
  "eval.change.strengthened": "Stronger than before",
  "eval.change.weakened": "Weaker than before",
  "eval.change.mixed": "Mixed progress",
  "eval.change.stable": "About the same",
  "eval.change.unknown": "Not enough to compare",
  "eval.cmp.comparable": "Similar conditions, comparable",
  "eval.cmp.partially_comparable": "Different help, only partly comparable",
  "eval.cmp.not_comparable": "Different help, not comparable",
  "eval.cmp.no_prior": "First observation",
  "eval.probe.explain": "Explain in your own words",
  "eval.probe.practice": "Complete a practice item",
  "eval.probe.variant": "Try a variant of this item",
  "eval.probe.transfer": "Apply it in a new context",
  "eval.probe.delayed_recheck": "Re-check in a few days",
  "eval.probe.self_check": "Self-check and justify",
  "eval.assist.full_demo": "Full demonstration seen",
  "eval.assist.key_hints": "Key hints given",
  "eval.assist.independent": "Independent",
  "eval.cond.ordinary": "Ordinary practice conditions",
  "eval.cond.transfer": "Transfer to a new context",
  "eval.cond.retention": "After a time interval",
  "eval.cond.self_check": "Self-check conditions",
  "eval.process.remember": "Remember",
  "eval.process.understand": "Understand",
  "eval.process.apply": "Apply",
  "eval.process.analyze": "Analyze",
  "eval.process.evaluate": "Evaluate",
  "eval.process.create": "Create",
  "eval.ktype.factual": "Factual knowledge",
  "eval.ktype.conceptual": "Conceptual knowledge",
  "eval.ktype.procedural": "Procedural knowledge",
  "eval.ktype.metacognitive": "Metacognitive knowledge",
  "eval.source.dialogue": "Dialogue explanation",
  "eval.source.assessment": "Assessment answer",
  "eval.hint.hint_requested": "Hint viewed",
  "eval.hint.answer_revealed": "Answer revealed",
  "eval.hint.worked_example": "Worked example seen",
  "eval.hint.teacher_probe": "Teacher probe",
  "eval.hint.prior_exposure": "Prior exposure to this task family",
  "eval.scope.current": "In current scope",
  "eval.scope.out_of_scope": "Outside current textbook scope",
  "eval.scope.source_removed": "Source removed",
  "eval.scope.needs_mapping": "Needs re-mapping after textbook revision",
  "eval.avail.available": "Material available",
  "eval.avail.archived": "Material archived",
  "eval.avail.deleted": "Material deleted",
};

/** 评价域标签翻译（eval.*）。 */
export function et(lang: Lang, key: string, fallback?: string): string {
  return (lang === "en" ? EN : ZH)[key] ?? fallback ?? key;
}

/** ConceptEvalState → Badge 色调（唯一映射；颜色之外组件另有图标/线型/文字）。 */
export function evalStateTone(state: string): BadgeTone {
  switch (state) {
    case "supported_in_scope":
      return "success";
    case "emerging":
      return "info";
    case "fragile":
      return "warning";
    case "conflicting":
      return "accent2";
    default:
      return "muted";
  }
}

/** EvaluationStatus → Badge 色调（运行状态小标记，不是学生等级）。 */
export function evalStatusTone(status: string): BadgeTone {
  switch (status) {
    case "ready":
      return "muted";
    case "pending":
    case "reconciling":
      return "info";
    default:
      return "outline";
  }
}

/** ClaimStatus → Badge 色调。 */
export function claimTone(status: string): BadgeTone {
  switch (status) {
    case "supported":
      return "success";
    case "tentative":
      return "info";
    case "challenged":
      return "danger";
    default:
      return "muted";
  }
}

/** ChangeDirection → Badge 色调。 */
export function changeTone(direction: string): BadgeTone {
  switch (direction) {
    case "strengthened":
      return "success";
    case "weakened":
      return "danger";
    case "mixed":
      return "warning";
    case "stable":
      return "muted";
    default:
      return "outline";
  }
}

/** 图谱节点的视觉规则（§14.4）：没有评价 → 中性虚线；局部证据 → 普通
 * 强调；条件支持 → 实线支持；待解决/待核对 → 图标。返回 CSS class 片段
 * 由图谱组件拼装，禁止把类别折算成颜色渐变或数值。 */
export function evalStateVisual(state: string | null | undefined): {
  ring: "none" | "emerging" | "supported" | "attention";
  icon: "" | "check" | "alert" | "help";
} {
  switch (state) {
    case "supported_in_scope":
      return { ring: "supported", icon: "check" };
    case "emerging":
      return { ring: "emerging", icon: "" };
    case "fragile":
      return { ring: "attention", icon: "alert" };
    case "conflicting":
      return { ring: "attention", icon: "help" };
    default:
      return { ring: "none", icon: "" };
  }
}
