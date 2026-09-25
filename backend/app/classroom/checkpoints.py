"""课堂检查点题模板生成（plan.md §13.2，D03）。

复用 ``core.quiz_verify.generate_verified_questions``——结构过滤、独立
critic 复核、教材 grounding、量规冻结语义原样保留，不写新的一次模型
直出评分器。模板只存进 revision 私有材料（spec.private.json），生成期
绝不注册为学生作答；run 级实例化（稳定 question_id/幂等注册）在 I 阶段。

随堂题准备失败 → 返回空表，调用方降级为 reflect（讲授模式），符合
§13.2.2"检查点 optional 时可发布降级版本"。
"""
from __future__ import annotations

import json
from typing import Any

from ..schemas import classroom as sc
from .llm_io import extract_json

_GROUNDING_HEADER = "\n\n[命题事实边界]\n本轮要求根据给定教材证据命题。\n" \
    "每题的题干、正确答案与解析中的教材事实必须能由下方证据直接支持。\n" \
    "可以重新设计数值/情境以形成练习，但不得引入教材证据之外的新定理、" \
    "新定义或教材专属事实。\n"


class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:  # 模板占位符缺失→空串，不崩
        return ""


def build_quiz_prompt(*, brief: sc.LessonBrief, slide_title: str,
                      evidence_text: str = "") -> str:
    """复用注册表 quiz_generate 模板 + 学段锚点 + 命题事实边界。"""
    from ..agents.teaching_engine.stage_profile import (
        difficulty_anchor, example_style)
    from ..prompts.registry import get as get_prompt

    grade = brief.grade or "高中"
    base = get_prompt("quiz_generate").text
    filled = base.format_map(_SafeFormat(
        grade=grade,
        topic=f"{brief.topic}（{slide_title}）",
        count=1,
        difficulty="easy",
        difficulty_zh="基础",
        anchor=difficulty_anchor(grade),
        example_style=example_style(grade),
        blueprint="",  # 蓝图轮省略：随堂单题走单轮（工具层同款回退路径）
    ))
    if evidence_text:
        filled += _GROUNDING_HEADER + evidence_text
    return filled


def _parse_questions(raw: str) -> list[dict[str, Any]]:
    try:
        data = extract_json(raw)
    except (ValueError, json.JSONDecodeError):
        return []
    items = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [q for q in items if isinstance(q, dict)]


def _public_question(question: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "type", "stem", "options", "answer", "explanation",
            "knowledge_point", "difficulty", "bloom_level", "rubric",
            "verification")
    return {k: question[k] for k in keep if k in question}


async def author_question_checkpoint(
        *, llm: Any, brief: sc.LessonBrief, slide: sc.SlideSpec,
        checkpoint_id: str, evidence_text: str = "",
) -> tuple[list[sc.CheckpointTemplate], str]:
    """为一个 checkpoint 布局页生成正式题模板。

    返回 (templates, status)；status:
      - "question"：正式题模板（含审核元数据）
      - "reflect_fallback"：出题失败/无题 → 调用方降级 reflect
    """
    from ..core.quiz_verify import generate_verified_questions

    prompt = build_quiz_prompt(brief=brief, slide_title=slide.title,
                               evidence_text=evidence_text)
    questions, meta = await generate_verified_questions(
        llm,
        make_prompt=lambda: prompt,
        parse=_parse_questions,
        topic=brief.topic, grade=brief.grade or "高中",
        difficulty="easy",
        temperature=0.4, max_tokens=2000,
        grounding_context=evidence_text[:6000],
        illustration_policy="off",
    )
    if not questions:
        return [], "reflect_fallback"
    question = questions[0]
    stem = str(question.get("stem") or "").strip()
    if not stem:
        return [], "reflect_fallback"
    template = sc.CheckpointTemplate(
        checkpoint_id=checkpoint_id, slide_id=slide.slide_id,
        kind=sc.CheckpointKind.question,
        prompt=stem[:500],
        verified_question_template=_public_question(question),
        optional=True)
    return [template], "question"
