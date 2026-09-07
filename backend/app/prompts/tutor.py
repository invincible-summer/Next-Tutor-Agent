"""Tutor system prompt: three layers (boundary -> decision -> recovery).

L1 (system static, non-compressible): red lines + teaching process + grade
adapter + tool-use policy. Dynamic info (student grade, knowledge files) is
injected as a user-message preamble, NOT baked into the system prompt.

阶段D 起 prompt 文本统一由 prompts/registry.py 注册管理（含版本号）；
此处保留薄 re-export 兼容既有引用。改文本请改注册表并 bump 版本。
"""
from .registry import get as _prompt

TUTOR_SYSTEM = _prompt("tutor_system").text

def error_recovery_hint(error_code: str) -> str:
    """R13: Per error-code concrete recovery instruction for the agent.
    Used to inject a targeted hint when a tool fails, so the model knows exactly
    what to do next instead of guessing."""
    hints = {
        "NOT_FOUND": (
            "检索未找到相关资料。下一步：不要重复相同查询。用你自己的知识讲解，"
            "并在结尾提示学生可上传教材以获得更精准的辅导。"
        ),
        "BAD_ARGS": (
            "参数格式不正确。下一步：检查参数类型与取值范围，用正确的参数重新调用。"
        ),
        "NO_TOOL": (
            "工具不存在。下一步：不要调用该工具。改用已有的工具或直接回答。"
        ),
        "TOOL_ERROR": (
            "工具执行出错。下一步：向学生说明该工具暂时不可用，用你自己的知识回答。"
        ),
        "VALIDATION_ERROR": (
            "参数校验失败。下一步：根据错误信息修正参数后重试一次。"
        ),
        "CIRCUIT_OPEN": (
            "工具因连续失败已禁用。下一步：不要再调用该工具。用你自己的知识回答，"
            "并提示学生可稍后再试。"
        ),
        "TIMEOUT": (
            "工具超时。下一步：向学生说明处理超时，简化请求后重试一次。"
        ),
        "DUPLICATE_CALL": (
            "本回合已用相同参数调用过。下一步：换一种方式回答或调整参数。"
        ),
    }
    return hints.get(error_code, "工具返回错误。下一步：调整策略或用你自己的知识回答。")


def grade_preamble(grade: str, has_knowledge: bool, file_names: list[str] | None = None, *,
                   answer_lang: str = "zh", forced: bool = False,
                   textbooks: list[dict] | None = None) -> str:
    """Dynamic context injected as the first user message (L3, not L1).

    学段语义（P1 学段去僵化）：``grade`` 为空（自动）= 学生未指定学段，不
    预置学段语境与七维度细则，只注入一行轻约束让模型按提问内容/资料自适应
    深度与语言；``grade`` 非空（小学/初中/高中/本科）= 学生显式选定，整块
    学段细则（stage_brief）作为强约束注入——行为与改造前逐字一致。三级解析
    优先级（会话级选择 > 全局默认偏好 > 自动）由调用方决定后传入最终 grade。

    Language policy (auto = follow the LLM's own judgment, no constraint):
      - forced=True (user picked zh/en in settings): deterministic hard
        directive, full answer in that language.
      - forced=False (auto): NO [回答语言] directive is injected — let the
        LLM choose the most fitting answer language from the question itself
        (zh input -> zh, en input -> en, translation -> target language).

    textbooks（P3）：本回合可见教材记录列表（来自 textbook_for_file 反查），
    最多渲染 3 本。注入 [当前教材] 块，要求回答优先依据所选教材、引用标注页码。
    None 或空列表 = 不注入教材块（无教材会话 preamble 零变化）。
    """
    from ..agents.teaching_engine.stage_profile import is_auto, normalize_grade, stage_brief
    parts: list[str] = []
    # [当前教材] 块（P3）：先于学段块注入。教材才是"私人教师"的知识来源，
    # 优先级高于学段细则；学段细则只调表达深度，教材决定回答依据。
    if textbooks:
        tb_lines = []
        for tb in textbooks[:3]:
            title = str(tb.get("title") or "未命名教材").strip() or "未命名教材"
            subject = str(tb.get("subject") or "").strip()
            level = normalize_grade(tb.get("level") or "")
            level_zh = level if level else "未指定学段"
            tag = f"{level_zh}·{subject}" if subject else level_zh
            tb_lines.append(f"《{title}》（{tag}）")
        if tb_lines:
            parts.append(
                "[当前教材] 本对话选用教材：" + "、".join(tb_lines) + "。\n"
                "回答涉及教材内容前，必须先用 knowledge_search 检索教材原文，"
                "并以检索到的教材内容为**主要依据**作答（引用标注页码）；"
                "教材未覆盖的内容先明确说明「教材中未涉及」，再用通用知识补充。"
            )
    # 学段块：显式学段注入七维度强约束（改造前行为不变）；自动学段注入轻约束。
    if is_auto(grade):
        parts.append(
            "[学段] 学生未指定学段。按提问内容、所用资料与对话线索判断适龄的"
            "深度与语言；学生反馈偏难/偏简单时随之调整。"
        )
    else:
        parts.append(stage_brief(grade))
    if forced:
        label = {"zh": "中文", "en": "English"}[answer_lang]
        parts.append(f"[回答语言] 学生已显式指定回答语言为{label}。请全程用{label}回答，含过渡语与提示语，不要混用。")
    if has_knowledge:
        names = "、".join(file_names[:5]) if file_names else "课程资料"
        parts.append(f"[已上传课程资料] 学生已上传：{names}。当问题涉及这些资料内容时，用 knowledge_search 检索相关片段再讲解。")
    else:
        parts.append("[课程资料] 学生尚未上传课程资料。如需引用教材原文，提示学生可上传 PDF/PPT/Word。")
    return "\n".join(parts)


def skill_cards_preamble(skill_ids: list[str]) -> str:
    """Render only this turn's selected Skill contracts into a compact note.

    Tool schemas remain the executable source of arguments; this block supplies
    educational pre/postconditions without listing the entire registry.
    """
    if not skill_ids:
        return ""
    from ..agents.skill_runtime.registry import registry
    cards: list[str] = ["[当前可用 Skill · 仅可按契约使用]"]
    seen: set[str] = set()
    for skill_id in skill_ids:
        if skill_id in seen:
            continue
        seen.add(skill_id)
        try:
            skill = registry.get(skill_id)
        except KeyError:
            continue
        cards.append(
            f"- {skill.id}@{skill.version}｜{skill.display_name}：{skill.description}"
            f"｜前置={','.join(skill.preconditions) or '无'}"
            f"｜成功标准={','.join(skill.postconditions) or '返回有效结果'}"
        )
    return "\n".join(cards) if len(cards) > 1 else ""
