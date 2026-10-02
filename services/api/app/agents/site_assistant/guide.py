"""访客公共导览（plan.md §11.2 /guide、§19.9）。

无个人工具、无服务端会话持久化；历史只在当前请求中出现。精确
别名/模块命中走零 LLM 快路；模糊问法且模型可用时做一次受限
导览回答（输入为目录摘要，输出限长纯文本）。
"""
from __future__ import annotations

from app.agents.site_assistant import catalog
from app.agents.site_assistant.capabilities import model_available
from app.core.config import settings
from app.schemas.assistant import (
    AssistantRouteId,
    GuideModuleEntry,
    GuideRequest,
    GuideResponse,
)

_DEFAULT_FOLLOWUPS_ZH = [
    "网站能帮我做什么？",
    "从哪里开始学习？",
    "怎么备课上课？",
]
_DEFAULT_FOLLOWUPS_EN = [
    "What can this site do for me?",
    "Where should I start?",
    "How do I prepare a lesson?",
]

# 导览 LLM 回答的私有提示词（访客场景，无动作、无个人数据）。
_GUIDE_SYSTEM = (
    "你是网站「{site}」的公共导览助手。只依据下方功能目录回答；"
    "不编造不存在的功能，不提供账号数据，不输出链接或代码。"
    "用{lang}回答，先直接回答问题，再给最多3条下一步建议，总长不超过600字。\n\n"
    "功能目录：\n{digest}"
)


def _deterministic_answer(
    modules: list[catalog.CatalogModule], lang: str,
) -> tuple[str, list[GuideModuleEntry]]:
    entries: list[GuideModuleEntry] = []
    parts: list[str] = []
    zh = lang == "zh"
    for module in modules[:3]:
        name = module.name.zh if zh else module.name.en
        summary = module.summary.zh if zh else module.summary.en
        parts.append(f"{name}：{summary}")
        entries.append(GuideModuleEntry(
            route_id=module.route_id, title=name, description=summary))
    if parts:
        head = "为你找到这些功能：" if zh else "Here is what I found:"
        return head + "\n" + "\n".join(parts), entries
    return "", entries


def _general_answer(lang: str) -> tuple[str, list[GuideModuleEntry]]:
    zh = lang == "zh"
    if zh:
        text = (
            "这个网站围绕三条主线：1）聊天辅导——带着教材提问，AI 老师讲解并出题；"
            "2）备课上课——把教材生成一节课，可配音、随堂提问并导出；"
            "3）回顾学习——查看学习总览、记忆中心与 AI 教学效果洞察。"
            "可以先上传教材，再建立学习区开始。"
        )
    else:
        text = (
            "This site has three main paths: 1) Chat Tutoring — ask with your "
            "textbooks and get explanations and practice; 2) Courses — turn a "
            "textbook into a voiced lesson you can teach and export; "
            "3) Review — dashboard, memory center and AI teaching insights. "
            "Upload a textbook, then create a workspace to begin."
        )
    entries: list[GuideModuleEntry] = []
    for route_id in (AssistantRouteId.CHAT, AssistantRouteId.COURSE,
                     AssistantRouteId.DASHBOARD):
        module = catalog.get_module(route_id)
        if module is None:
            continue
        name = module.name.zh if zh else module.name.en
        summary = module.summary.zh if zh else module.summary.en
        entries.append(GuideModuleEntry(
            route_id=route_id, title=name, description=summary))
    return text, entries


async def answer_guide(request: GuideRequest) -> GuideResponse:
    lang = request.lang
    route_hint = request.route_id
    matches = catalog.find_module_matches(request.question, lang, route_hint)
    strong = [m for m in matches
              if catalog.match_score(m, request.question, route_hint)[0] >= 50]

    if strong:
        text, entries = _deterministic_answer(strong, lang)
        followups = _DEFAULT_FOLLOWUPS_ZH if lang == "zh" else _DEFAULT_FOLLOWUPS_EN
        return GuideResponse(
            schema_version=1, text=text[:6000],
            module_entries=entries, followups=followups)

    if model_available():
        try:
            from app.core.llm_async import get_llm
            llm = get_llm("site_assistant_guide")
            system = _GUIDE_SYSTEM.format(
                site="Next Tutor Agent",
                lang="中文" if lang == "zh" else "English",
                digest=catalog.catalog_digest(lang))
            history = [{"role": item.role, "content": item.text}
                       for item in request.history]
            answer, _meta = await llm.complete(
                [{"role": "system", "content": system},
                 *history,
                 {"role": "user", "content": request.question}],
                temperature=0.3, max_tokens=800, disable_thinking=True)
            text = (answer or "").strip()[:6000]
            if text:
                _, entries = _general_answer(lang)
                # LLM 正文 + 目录入口（入口仍来自经校验的目录）。
                return GuideResponse(
                    schema_version=1, text=text,
                    module_entries=entries[:3],
                    followups=_DEFAULT_FOLLOWUPS_ZH if lang == "zh"
                    else _DEFAULT_FOLLOWUPS_EN)
        except Exception:
            pass  # 模型失败回落确定性导览，不向访客暴露错误细节

    text, entries = _general_answer(lang)
    return GuideResponse(
        schema_version=1, text=text, module_entries=entries,
        followups=_DEFAULT_FOLLOWUPS_ZH if lang == "zh" else _DEFAULT_FOLLOWUPS_EN)


def guide_rate_limiter():
    from app.agents.site_assistant.ratelimit import SlidingWindowRateLimiter
    return SlidingWindowRateLimiter(max_events=10, window_seconds=60.0)
