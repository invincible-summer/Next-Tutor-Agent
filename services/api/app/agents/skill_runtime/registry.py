"""Single source of truth for executable education skills."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .manifest import SkillKind, SkillManifest


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, dict[str, SkillManifest]] = {}
        self._active: dict[str, str] = {}

    def register(self, manifest: SkillManifest, *, active: bool = True) -> None:
        manifest.validate()
        versions = self._skills.setdefault(manifest.id, {})
        if manifest.version in versions:
            raise ValueError(f"Skill 重复注册: {manifest.id}@{manifest.version}")
        versions[manifest.version] = manifest
        if active or manifest.id not in self._active:
            self._active[manifest.id] = manifest.version

    def get(self, skill_id: str, version: str | None = None) -> SkillManifest:
        versions = self._skills.get(skill_id)
        if not versions:
            raise KeyError(f"未知 Skill: {skill_id}")
        selected = version or self._active[skill_id]
        if selected not in versions:
            raise KeyError(f"Skill {skill_id} 无版本 {selected}")
        return versions[selected]

    def all_active(self) -> list[SkillManifest]:
        return [self.get(skill_id) for skill_id in self._skills]

    def active_versions(self) -> dict[str, str]:
        return dict(self._active)

    def for_role(self, role: str) -> list[SkillManifest]:
        return [s for s in self.all_active() if s.role == role]

    def for_intent(self, intent: str) -> list[SkillManifest]:
        return [s for s in self.all_active() if intent in s.intents]

    def by_tool_name(self, tool_name: str) -> SkillManifest | None:
        return next((s for s in self.all_active() if s.tool_name == tool_name), None)

    def validate_references(self) -> None:
        known = set(self._skills)
        for skill in self.all_active():
            missing = [s for s in skill.fallback_skills if s not in known]
            if missing:
                raise ValueError(f"Skill {skill.id} 引用了未知 fallback: {missing}")


registry = SkillRegistry()


def _register_builtins() -> None:
    manifests: Iterable[SkillManifest] = (
        SkillManifest(
            id="agent.skill.teaching.direct_explain", version="1.0.0",
            role="teaching", kind=SkillKind.ADVISORY, display_name="分层讲解",
            description="按学生学段、掌握状态和当前教学模式直接讲解知识。",
            intents=("explain", "solve", "review", "generate", "plan"),
            postconditions=("answer_grade_appropriate", "no_unverified_mastery_write"),
            use_when=("普通知识讲解", "不依赖学生资料原文"),
            avoid_when=("用户明确要求依据已上传资料",),
            cost_level="medium", prompt_ref="skill_teaching_direct_explain",
        ),
        SkillManifest(
            id="agent.skill.knowledge.search_materials", version="1.0.0",
            role="knowledge", kind=SkillKind.ATOMIC, display_name="资料证据检索",
            description="在当前会话与学习区授权资料中检索可引用的原文片段。",
            intents=("explain", "solve", "review", "diagnose", "generate", "plan"),
            tool_name="knowledge_search", preconditions=("materials_available",),
            postconditions=("retrieval_result_grounded",),
            use_when=("用户引用已上传资料", "回答必须依据教材或笔记"),
            avoid_when=("没有授权资料", "普通常识讲解且未要求资料依据"),
            fallback_skills=("agent.skill.teaching.direct_explain",),
            cost_level="low", prompt_ref="skill_knowledge_search_materials",
        ),
        SkillManifest(
            id="agent.skill.assessment.generate_practice", version="1.0.0",
            role="assessment", kind=SkillKind.ATOMIC, display_name="结构化练习生成",
            description="按知识点、学段、难度和数量生成可交互练习。",
            intents=("practice", "diagnose", "review"), tool_name="generate_quiz",
            preconditions=("grade_available",),
            postconditions=("questions_present", "questions_answer_verified"),
            use_when=("用户明确要求出题、练习或测验", "教学策略明确安排收尾检测"),
            avoid_when=("用户只要求讲解", "用户要求仿照参考题且参考题完整"),
            fallback_skills=("agent.skill.teaching.direct_explain",),
            side_effects=("append_quiz_history",), cost_level="high",
            prompt_ref="skill_assessment_generate_practice",
        ),
        SkillManifest(
            id="agent.skill.assessment.fit_variants", version="1.0.0",
            role="assessment", kind=SkillKind.ATOMIC, display_name="参考题变式生成",
            description="拆解完整参考题的考点与结构，生成同考点的真实变式。",
            intents=("practice", "generate"), tool_name="fit_quiz",
            preconditions=("reference_question_available", "grade_available"),
            postconditions=("questions_present", "variants_preserve_learning_target",
                            "questions_answer_verified"),
            use_when=("用户要求仿照、类似题或变式题且已提供参考题",),
            avoid_when=("只说‘这道题’但上下文中没有参考题",),
            fallback_skills=("agent.skill.assessment.generate_practice",),
            cost_level="high", prompt_ref="skill_assessment_fit_variants",
        ),
        SkillManifest(
            id="agent.skill.memory.recall_history", version="1.0.0",
            role="memory", kind=SkillKind.ATOMIC, display_name="历史学习证据回忆",
            description="检索被压缩或较早的完整师生对话、错题与作答记录。",
            intents=("diagnose", "review", "explain", "solve"),
            tool_name="recall_history", preconditions=("history_available",),
            postconditions=("history_result_delimited",),
            use_when=("压缩摘要不足", "诊断需要查找过去错题或作答"),
            avoid_when=("当前上下文已经包含所需信息", "没有历史记录"),
            fallback_skills=("agent.skill.assessment.generate_practice",),
            cost_level="low", prompt_ref="skill_memory_recall_history",
        ),
    )
    for manifest in manifests:
        registry.register(manifest)
    registry.validate_references()


_register_builtins()


def capability_tool_map() -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for skill in registry.all_active():
        if skill.tool_name:
            grouped[skill.role].add(skill.tool_name)
    # Keep every current planner role present, including pure teaching.
    for role in ("knowledge", "teaching", "assessment", "memory"):
        grouped.setdefault(role, set())
    return dict(grouped)


def skill_ids_for_role(role: str) -> list[str]:
    return [s.id for s in registry.for_role(role)]


def tool_names_for_skills(skill_ids: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for skill_id in skill_ids:
        try:
            skill = registry.get(skill_id)
        except KeyError:
            continue
        if skill.tool_name:
            names.add(skill.tool_name)
    return names
