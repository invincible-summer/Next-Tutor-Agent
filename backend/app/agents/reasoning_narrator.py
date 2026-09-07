"""Public, bounded process summaries for the learning experience."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.config import settings
from .state import TaskPlan, TaskUnderstanding


@dataclass(frozen=True)
class PublicReasoningEvent:
    stage: str
    level: str
    content: str


def _level_for(understanding: TaskUnderstanding) -> str:
    configured = settings.reasoning_summary_level
    if configured in {"compact", "standard", "detailed"}:
        return configured
    intent = understanding.intent.value if understanding.intent else ""
    return "detailed" if intent in {"solve", "diagnose", "plan"} else "standard"


def build_reasoning_events(understanding: TaskUnderstanding, plan: TaskPlan,
                           strategy: Any) -> list[PublicReasoningEvent]:
    concept = understanding.concept or understanding.subject or "当前问题"
    intent = understanding.intent.value if understanding.intent else "explain"
    level = _level_for(understanding)
    response_format = getattr(understanding, "response_format", "")
    if response_format == "one_sentence":
        task_text = f"我会锁定「{concept}」最核心且准确的定义，并严格压缩成一句话。"
    elif response_format == "concise":
        task_text = f"我会只保留理解「{concept}」所必需的信息，避免按默认教学模板过度展开。"
    else:
        task_text = {
            "explain": f"我先确认任务重点：需要围绕「{concept}」建立可以迁移应用的理解，而不是只记住一句结论。",
            "solve": f"我先把「{concept}」中的已知条件、求解目标和约束分开检查，避免直接套用不适合的公式。",
            "diagnose": f"我会区分「{concept}」究竟是概念误解、步骤遗漏还是计算失误，再决定如何补救。",
            "practice": f"我会根据当前学段和「{concept}」选择合适的题型与难度，并确保题目可以直接作答。",
            "review": f"我会把「{concept}」按知识结构、常见联系和易错点重新整理，而不是简单重复旧答案。",
            "plan": f"我会先确认「{concept}」的目标、现状和时间约束，再把计划拆成可以执行和检查的步骤。",
        }.get(intent, f"我正在确认「{concept}」的目标、必要信息和最合适的完成方式。")
    events = [PublicReasoningEvent("understanding", level, task_text)]
    actions: list[str] = []
    for step in plan.steps[:4]:
        label = {"knowledge": "核对已授权资料中的依据",
                 "teaching": "按当前学段分层讲解",
                 "assessment": "安排结构化检测题并让你先作答",
                 "memory": "必要时回看此前作答与薄弱点"}.get(step.agent_role)
        if label:
            actions.append(label)
    mode = getattr(getattr(strategy, "mode", None), "value", "") if strategy else ""
    if response_format == "one_sentence":
        plan_text = "本轮只输出一句完整答案，不追加例子、表格、检测题或后续邀请。"
    elif response_format == "concise":
        plan_text = "本轮采用精简路线，只完成当前请求，不自动扩展默认教学章节。"
    else:
        plan_text = "接下来的路线是：" + "；".join(dict.fromkeys(actions or ["直接完成回答"])) + "。"
        if mode:
            plan_text += f" 当前采用 {mode} 教学策略，我会据此调整例子、推导深度和检查难度。"
    if level == "detailed" and response_format not in {"one_sentence", "concise"}:
        plan_text += " 我也会检查条件是否充分、结论是否有证据，以及最终步骤能否由你自己复现。"
    events.append(PublicReasoningEvent("planning", level, plan_text))
    return events


def tool_progress_summary(tool_name: str, *, succeeded: bool) -> PublicReasoningEvent:
    label = {"knowledge_search": "资料依据检索", "generate_quiz": "结构化检测题生成",
             "fit_quiz": "参考题变式生成", "recall_history": "历史学习证据回看"}.get(tool_name, tool_name)
    content = (f"{label}已经完成，我正在把结果和前面的教学目标合并，确保最终回答不重复工具内容，并明确告诉你下一步怎么做。"
               if succeeded else f"{label}没有得到有效结果，我会保留已经确认的信息，改用安全的降级方式继续，而不是反复调用或猜测。")
    return PublicReasoningEvent("tool_result", "standard", content)
