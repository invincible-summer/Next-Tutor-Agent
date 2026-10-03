"""助手单轮编排服务（A09 完整编排）。

执行步骤（与 §10.2 一一对应）：
1. 冻结请求（身份校验与幂等在 runtime.accept_turn 已完成）；
2. 意图解析：精确别名/固定问法零 LLM 快路，模糊问法一次低预算
   结构化解析（含一次受限修复，计入 2 次模型预算）；
3. 按意图暴露有限工具（≤4 次、并发 ≤3、墙钟 60s）；
4. 确定性事实卡（metrics/choices/notice）+ 文字回答（模型只解释
   事实，不可用/超预算/失败时回落确定性文字）；
5. 持久化最终消息、登记来源反向索引、发送终态。

实际导航与写动作留给 execute 链路（A10）；本模块不写任何业务存储。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from . import catalog
from . import intent as intent_mod
from . import presenters, readers, tools
from . import actions as actions_mod
from . import policy
from .runtime import _RunningTurn, AssistantRuntime

_CHUNK_CHARS = 24
_MAIN_BLOCK_ID = "b0"
_MAX_FACTS_CHARS = 6000


def _assistant_message(turn: _RunningTurn) -> dict[str, Any] | None:
    for message in turn.record.get("messages") or []:
        if (message.get("turn_id") == turn.turn_id
                and message.get("role") == "assistant"):
            return message
    return None


def _recent_history(turn: _RunningTurn, limit: int = 4
                    ) -> list[tuple[str, str]]:
    """最近对话（供指代消解）；只取纯文本，截断到 200 字。"""
    history: list[tuple[str, str]] = []
    for message in (turn.record.get("messages") or [])[-(limit * 2):]:
        if message.get("turn_id") == turn.turn_id:
            continue
        text = ""
        for block in message.get("blocks") or []:
            if block.get("type") == "markdown":
                text = str(block.get("text") or "")
                break
        if text:
            history.append((str(message.get("role", "user")), text[:200]))
    return history[-limit:]


def _make_llm_call(budget: tools.TurnBudget):
    """预算受限的模型调用闭包：意图与回答共享 2 次上限。"""

    async def llm_call(system: str, user: str) -> str:
        if not budget.allow_model():
            raise RuntimeError("model budget exhausted")
        budget.use_model()
        from app.core.llm_async import get_llm
        llm = get_llm("site_assistant")
        timeout = max(3.0, min(budget.remaining_seconds() - 5.0, 30.0))
        answer, _meta = await asyncio.wait_for(
            llm.complete(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                temperature=0.2, max_tokens=800, disable_thinking=True),
            timeout)
        return answer or ""
    return llm_call


def _facts_for_model(parsed: intent_mod.ParsedIntent,
                     results: list[dict[str, Any]],
                     meta: dict[str, Any], window_label: str) -> str:
    """工具事实的模型视图：有界、只含服务端确定性数据。"""
    payload = {
        "intent": parsed.kind,
        "window": (meta.get("window") or None),
        "window_label": window_label or None,
        "tools": [{
            "tool": r.get("tool"),
            "status": r.get("status"),
            "complete": r.get("complete"),
            "data": r.get("data"),
            "notices": r.get("notices"),
            "source_ids": [s.get("source_id") for s in r.get("sources", [])],
        } for r in results],
    }
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return text[:_MAX_FACTS_CHARS]


async def _compose_answer(
    turn: _RunningTurn, parsed, results, meta,
    *, window_label: str, budget: tools.TurnBudget, fallback: str,
) -> str:
    """模型回答（可用且预算允许时）；否则确定性兜底。"""
    from .capabilities import model_available
    if not model_available() or not budget.allow_model():
        return fallback
    try:
        from app.prompts import registry as prompt_registry
        cat = catalog.load_product_catalog()
        system = (
            prompt_registry.get("site_assistant_system").text.format(
                lang="中文" if _lang(turn) == "zh" else "English",
                catalog_version=cat.catalog_version,
                tools="\n".join(
                    f"- {name}: {desc}" for name, desc
                    in tools.TOOL_CATALOG.items()),
                digest=catalog.catalog_digest(_lang(turn))[:1500])
            + "\n\n"
            + prompt_registry.get("site_assistant_answer").text.format(
                lang="中文" if _lang(turn) == "zh" else "English",
                facts=_facts_for_model(parsed, results, meta, window_label))
            + "\n\n"
            + _style_directive(turn.student_id))
        llm_call = _make_llm_call(budget)
        raw = await llm_call(system, str(turn.request.get("text") or ""))
        text = (raw or "").strip()
        if text:
            return text
    except Exception:
        pass  # 模型失败回落确定性事实文字（§10.5：不静默执行未校验输出）
    return fallback


def _lang(turn: _RunningTurn) -> str:
    lang = str(turn.request.get("lang") or "zh")
    return "en" if lang == "en" else "zh"


def _style_directive(student_id: str) -> str:
    """§22.4（B09）：把用户保存的助手偏好注入回答系统提示。

    偏好只约束回答风格（语气/长度/默认范围），不改变事实来源规则；
    读取失败时无指令（默认风格），不阻断回答。
    """
    try:
        from app.core import assistant_store as prefs_store
        prefs = prefs_store.load_preferences(student_id)
        tone = {"neutral": "平实自然",
                "encouraging": "鼓励友好"}.get(
            str(prefs.get("tone")), "平实自然")
        length = {"short": "尽量简短（默认≤3句）",
                  "standard": "标准篇幅",
                  "detailed": "可较详细展开"}.get(
            str(prefs.get("response_length")), "标准篇幅")
        return (f"回答风格（用户设置）：语气{tone}；{length}。"
                "风格不改变事实与来源规则。")
    except Exception:
        return ""


async def execute_turn(runtime: AssistantRuntime, turn: _RunningTurn) -> None:
    """执行一轮：意图 → 工具 → 事实卡 → 回答；事件协议与 §11.5 一致。"""
    if turn.cancel_requested:
        runtime._settle_cancel(turn)
        return

    message = _assistant_message(turn)
    if message is None:  # 受理数据异常：按失败收尾
        raise RuntimeError("assistant message missing after acceptance")

    text = str(turn.request.get("text") or "").strip()
    lang = _lang(turn)
    timezone_name = str(turn.request.get("timezone") or "UTC")
    scope_selection = dict(turn.request.get("scope") or {})
    page_context = dict(turn.request.get("page_context") or {})
    choice = turn.request.get("choice") or None
    budget = tools.TurnBudget()
    registry = readers.SourceRegistry(turn.student_id)

    await runtime.emit(turn, {"event": "status", "stage": "understanding",
                              "label": "正在理解你的问题"})

    # -- 意图（快路或一次结构化解析） --------------------------------------
    route_id = None
    raw_route = str(page_context.get("route_id") or "")
    if raw_route:
        from app.schemas.assistant import AssistantRouteId
        try:
            route_id = AssistantRouteId(raw_route)
        except ValueError:
            route_id = None
    if choice:
        option_id = str(choice.get("option_id") or "")
        parsed = intent_mod.ParsedIntent(
            kind="navigate", text=option_id, confidence="exact")
        try:
            from app.schemas.assistant import AssistantRouteId as _R
            parsed.module_route = _R(option_id)
        except ValueError:
            parsed.module_route = None
    else:
        from .capabilities import model_available
        llm_call = _make_llm_call(budget) if model_available() else None
        parsed = await intent_mod.parse_intent(
            text, lang=lang, route_id=route_id,
            history=_recent_history(turn), llm_call=llm_call)
    if turn.cancel_requested:
        runtime._settle_cancel(turn)
        return

    # -- 工具（有限集合 + 预算） --------------------------------------------
    await runtime.emit(turn, {"event": "status", "stage": "reading",
                              "label": "正在读取相关数据"})
    results, meta = await tools.run_tools(
        turn.student_id, parsed, lang=lang, timezone_name=timezone_name,
        scope_selection=scope_selection, page_context=page_context,
        registry=registry, budget=budget)
    # 提案层（B06 起）需要读业务版本（note base_revision / plan revision）。
    meta["student_id"] = turn.student_id
    # 范围无法唯一确定（§6.2-5）：返回候选，不猜测。
    if meta.get("workspace_candidates") and parsed.kind != "clarify":
        parsed = intent_mod.ParsedIntent(
            kind="clarify", text=parsed.text,
            candidates=parsed.candidates, confidence="strong")
    if turn.cancel_requested:
        runtime._settle_cancel(turn)
        return

    # -- 事实卡与回答 --------------------------------------------------------
    message["scope"] = meta.get("scope") or message.get("scope")
    window_label = ""
    if parsed.window:
        try:
            from datetime import datetime, timezone as _tz
            _s, _e, window_label = intent_mod.resolve_time_window(
                parsed.window, timezone_name,
                custom_days=parsed.custom_days, lang=lang)
        except Exception:
            window_label = ""
    blocks = presenters.build_blocks(
        parsed, results, meta, lang=lang, window_label=window_label,
        timezone_name=timezone_name, now_iso=_utc_now())
    fallback = presenters.deterministic_answer(
        parsed, results, meta, lang=lang, window_label=window_label)

    # -- 受控动作（§9.2 策略表） --------------------------------------------
    proposed = policy.decide_actions(
        parsed, results, meta, conversation_id=turn.conversation_id,
        turn_id=turn.turn_id, lang=lang,
        page_epoch=int(page_context.get("route_epoch") or 0),
        page_context=page_context, timezone_name=timezone_name)
    if proposed:
        record_actions = turn.record.setdefault("actions", {})
        for act in proposed:
            record_actions[act["action_id"]] = act
        blocks.append({
            "block_id": f"b{len(blocks) + 1}", "type": "actions",
            "items": [actions_mod.public_action(a) for a in proposed]})

    await runtime.emit(turn, {"event": "status", "stage": "composing",
                              "label": "正在组织回答"})
    answer = await _compose_answer(
        turn, parsed, results, meta, window_label=window_label,
        budget=budget, fallback=fallback)

    # -- 流式正文 + 结构化块 -------------------------------------------------
    message["blocks"] = [{"block_id": _MAIN_BLOCK_ID, "type": "markdown",
                          "text": ""}]
    for i in range(0, len(answer), _CHUNK_CHARS):
        if turn.cancel_requested:
            runtime._settle_cancel(turn)
            return
        chunk = answer[i:i + _CHUNK_CHARS]
        message["blocks"][0]["text"] = answer[:i + _CHUNK_CHARS]
        await runtime.emit(turn, {
            "event": "text_delta",
            "message_id": message["message_id"],
            "block_id": _MAIN_BLOCK_ID, "delta": chunk})
        await runtime.persist_stream_snapshot(turn)
        await asyncio.sleep(0.01)

    message["blocks"][0]["text"] = answer
    for block in blocks:
        message["blocks"].append(block)
        await runtime.emit(turn, {
            "event": "block_upsert",
            "message_id": message["message_id"], "block": block})

    # 来源：消息级引用 + 反向索引登记（§12.3-7）
    sources = registry.all_sources()
    if sources:
        message["sources"] = sources
        try:
            await asyncio.to_thread(
                store_register_refs, turn, registry, message)
        except Exception:
            pass  # 反向索引可重建；登记失败不失败整轮

    message["status"] = "complete"
    info = turn.record["turns"][turn.turn_id]
    info["state"] = "completed"
    info["updated_at"] = _utc_now()
    turn.record["revision"] = int(turn.record.get("revision", 1)) + 1
    await runtime.emit(turn, {"event": "message_done", "message": message})


def store_register_refs(turn: _RunningTurn, registry: readers.SourceRegistry,
                        message: dict[str, Any]) -> None:
    from app.core import assistant_store
    assistant_store.register_source_refs(
        turn.student_id,
        registry.ref_entries(turn.conversation_id,
                             str(message.get("message_id"))))


def _utc_now() -> str:
    from app.core.assistant_store import utc_now_iso
    return utc_now_iso()
