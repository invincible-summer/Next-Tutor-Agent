"""工具目录与预算（plan.md §10.3 / §10.6，A09）。

工具只能由后端注册；模型不可自定义 URL、路径、SQL 或代码。每轮硬预算：
- 最多 4 次只读工具调用；
- 最多 2 次模型调用（意图解析与回答共享，含一次受限修复）；
- 总墙钟 60 秒；
- 工具并发最多 3 个，有依赖的实体解析与取数串行（§10.2-6）。
所有工具实现都在 readers.py；本模块只做选择、并发与预算控制。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from . import intent as intent_mod
from . import readers

MAX_TOOL_CALLS = 4
MAX_MODEL_CALLS = 2
TURN_WALL_CLOCK_SECONDS = 60.0
MAX_TOOL_CONCURRENCY = 3

# 供提示词与 capabilities 使用的目录描述（§10.3 表的投影）。
TOOL_CATALOG: dict[str, str] = {
    "get_product_help": "module_id?/query/lang → 版本化帮助段落与入口",
    "resolve_destination": "query/entity_kind?/workspace_id? → 唯一目标或≤5候选",
    "get_learning_summary": "scope/time_window → 学习事实、待处理状态、来源",
    "get_concept_explanation": "workspace_id/concept_key → 现有主张与变化",
    "get_teaching_summary": "time_window → 窗口统计、诊断、提案摘要",
    "get_saved_tasks": "time_window?/status? → 已保存任务与目标快照",
    "find_course": "workspace_id?/query?/resume_only → 课程候选与状态",
    "get_reference_detail": "source_id → 有界来源摘要与 locator",
    "search_site_entities": "q/kinds?/workspace_id?/offset?/limit? → 全站实体检索（§20.4）",
}

_RESULT_STATUSES = ("ready", "empty", "disabled", "pending", "partial",
                    "error")


class TurnBudget:
    """单轮预算记账（工具次数 / 模型次数 / 墙钟）。"""

    def __init__(self, *, deadline_seconds: float = TURN_WALL_CLOCK_SECONDS,
                 max_tool_calls: int = MAX_TOOL_CALLS,
                 max_model_calls: int = MAX_MODEL_CALLS) -> None:
        self.started_at = time.monotonic()
        self.deadline_seconds = deadline_seconds
        self.max_tool_calls = max_tool_calls
        self.max_model_calls = max_model_calls
        self.tool_calls = 0
        self.model_calls = 0

    def remaining_seconds(self) -> float:
        return self.deadline_seconds - (time.monotonic() - self.started_at)

    def allow_tool(self) -> bool:
        return (self.tool_calls < self.max_tool_calls
                and self.remaining_seconds() > 1.0)

    def allow_model(self) -> bool:
        return (self.model_calls < self.max_model_calls
                and self.remaining_seconds() > 5.0)

    def use_model(self) -> None:
        self.model_calls += 1

    def timed_out(self) -> bool:
        return self.remaining_seconds() <= 0


def _pick_tool_names(kind: str) -> list[str]:
    """按意图暴露有限工具（普通问答不一定调用工具，§10.3）。"""
    return {
        "guide": ["get_product_help"],
        "navigate": ["resolve_destination", "get_product_help"],
        "search": ["search_site_entities", "resolve_destination",
                   "find_course"],
        "learning_report": ["get_learning_summary", "get_saved_tasks"],
        "teaching_report": ["get_teaching_summary"],
        "planning_advice": ["get_saved_tasks", "get_learning_summary"],
        "prepare_action": ["find_course", "get_product_help",
                           "get_saved_tasks"],
        "general_chat": [],
        "clarify": [],
    }.get(kind, [])


async def run_tools(
    student_id: str,
    parsed: intent_mod.ParsedIntent,
    *,
    lang: str,
    timezone_name: str,
    scope_selection: dict[str, Any],
    page_context: dict[str, Any] | None,
    registry: readers.SourceRegistry,
    budget: TurnBudget,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """执行意图对应的工具计划；返回 (结果列表, 解析后的范围)。

    范围解析（依赖实体归属校验）先于取数串行执行；无依赖的读取以
    ≤3 并发执行。超预算的调用直接返回受限结果，不静默吞掉。
    """
    scope, ws_candidates = readers.resolve_scope(
        student_id, scope_selection, page_context,
        workspace_name=parsed.workspace_name)

    now = now or datetime.now(tz=timezone.utc)
    start_at = end_at = None
    if parsed.window:
        start_at, end_at, _label = intent_mod.resolve_time_window(
            parsed.window, timezone_name, now=now,
            custom_days=parsed.custom_days, lang=lang)

    names = _pick_tool_names(parsed.kind)[:budget.max_tool_calls]
    semaphore = asyncio.Semaphore(MAX_TOOL_CONCURRENCY)

    async def _run_one(name: str) -> dict[str, Any]:
        if not budget.allow_tool():
            return readers.tool_result(
                name, status="pending",
                notices=[{"code": "tool_budget_exhausted",
                          "message": "本轮工具调用已达上限。"}])
        budget.tool_calls += 1
        try:
            async with semaphore:
                return await _dispatch(
                    name, student_id, parsed, lang=lang,
                    timezone_name=timezone_name, scope=scope,
                    page_context=page_context, registry=registry,
                    start_at=start_at, end_at=end_at, now=now)
        except Exception:
            return readers.tool_result(
                name, status="error",
                notices=[{"code": "tool_failed",
                          "message": "工具读取失败。"}])

    results = await asyncio.gather(*(_run_one(n) for n in names))
    for result in results:
        if result["status"] not in _RESULT_STATUSES:
            result["status"] = "error"
    meta = {"scope": scope, "workspace_candidates": ws_candidates,
            "window": ({"start_at": start_at.isoformat(),
                        "end_at": end_at.isoformat()} if start_at else None)}
    return list(results), meta


async def _dispatch(
    name: str,
    student_id: str,
    parsed: intent_mod.ParsedIntent,
    *,
    lang: str,
    timezone_name: str,
    scope: dict[str, Any],
    page_context: dict[str, Any] | None,
    registry: readers.SourceRegistry,
    start_at: datetime | None,
    end_at: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    if name == "get_product_help":
        module_id = (parsed.module_route.value
                     if parsed.module_route is not None else "")
        return await asyncio.to_thread(
            readers.read_product_help, module_id=module_id,
            query=parsed.text, lang=lang, registry=registry)
    if name == "resolve_destination":
        return await asyncio.to_thread(
            readers.read_destination, student_id, query=parsed.text,
            lang=lang, entity_kind=None,
            workspace_name=parsed.workspace_name,
            page_context=page_context, registry=registry,
            lesson_edit=parsed.lesson_edit, page=parsed.page)
    if name == "get_learning_summary":
        start = start_at or _fallback_start(now)
        return await asyncio.to_thread(
            readers.read_learning_summary, student_id,
            start_at=start, end_at=end_at or now,
            timezone_name=timezone_name,
            workspace_ids=(scope.get("workspace_ids")
                           if scope.get("mode") == "workspace" else None),
            registry=registry)
    if name == "get_teaching_summary":
        start = start_at or _fallback_start(now)
        return await asyncio.to_thread(
            readers.read_teaching_summary, student_id,
            start_at=start, end_at=end_at or now, registry=registry)
    if name == "get_saved_tasks":
        return await readers.read_saved_tasks(student_id)
    if name == "find_course":
        ws_id = (scope.get("workspace_ids") or [None])[0] \
            if scope.get("mode") == "workspace" else None
        return await asyncio.to_thread(
            readers.read_courses, student_id, workspace_id=ws_id,
            resume_only=parsed.kind == "prepare_action", lang=lang)
    if name == "search_site_entities":
        from . import search as search_mod
        ws_id = (scope.get("workspace_ids") or [""])[0] \
            if scope.get("mode") == "workspace" else ""
        # 自然语言整句先净化（两个力度合并检索，「找到我的笔记《X》」
        # →「X」），双向包含匹配兜住净化漏网的完整标题（§20.4）。
        data = await asyncio.to_thread(
            search_mod.search_with_expanded_query, student_id,
            text=parsed.text, workspace_id=ws_id, limit=5)
        # 每个结果铸造可点击来源（locator=实体深链目标；§7.7/§20.4
        # 搜索结果点击只导航），source_id 回填进结果项供追问引用。
        for item in data.get("items") or []:
            target = item.get("target")
            if not isinstance(target, dict) or not target.get("kind"):
                continue
            item["source_id"] = registry.mint(
                kind="site_search",
                title=str(item.get("title") or item.get("entity_id")
                          or "搜索结果"),
                locator=target,
                workspace_id=(str(item.get("workspace_id"))
                              if item.get("workspace_id") else None))
        status = "ready" if data.get("items") else "empty"
        return readers.tool_result(name, status=status, data=data,
                                   scope=scope,
                                   data_revision=f"search:{int(now.timestamp())}")
    if name == "get_concept_explanation":
        ws_id = (scope.get("workspace_ids") or [""])[0]
        return await asyncio.to_thread(
            readers.read_concept_explanation, student_id,
            workspace_id=ws_id, concept_key=parsed.text,
            registry=registry)
    if name == "get_reference_detail":
        return readers.read_reference_detail(
            parsed.source_id or "", registry)
    raise ValueError(f"unknown tool {name}")


def _fallback_start(now: datetime) -> datetime:
    from datetime import timedelta
    return now - timedelta(days=7)
