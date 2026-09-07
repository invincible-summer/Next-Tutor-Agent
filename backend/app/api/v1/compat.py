"""OpenAI 兼容门面：让本 Agent 作为标准 OpenAI 兼容服务被第三方平台接入。

遵循《openai-compatible-agent-integration-guide.md》（清小搭广场接入规范）：
  - GET  {baseUrl}/models            连通性 + 凭证校验
  - POST {baseUrl}/chat/completions  对话（SSE 流式 + 非流式 JSON）
  - baseUrl 填到版本段：本部署即 https://<域名>/api/v1

鉴权：部署方在 .env 配置 COMPAT_API_KEY 作为接入凭证，请求经
Authorization: Bearer <key>（或 x-api-key 头）校验；无效一律 401，
未配置一律 503（门面默认关闭）。

会话映射：接入平台每轮都携带完整 messages 数组，而本 Agent 的会话自身
累积历史，因此只取最后一条 user 消息作为新输入；会话按凭证哈希派生
固定 id（compat_<sha256[:12]>），student 命名空间固定为 "compat_agent"，
与真人学生的数据完全隔离、也不出现在任何用户的会话列表里。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.context import estimate_tokens
from app.core.ratelimit import rate_limit

router = APIRouter(tags=["openai_compat"])

# 门面会话的专属学生命名空间：与真人用户数据隔离，会话列表按 student_id
# 过滤时天然不可见。
COMPAT_STUDENT_ID = "compat_agent"


# --- 请求模型 ---------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: Any = ""  # str 或 OpenAI 多模态数组


class ChatCompletionsRequest(BaseModel):
    messages: list[ChatMessage]
    stream: bool = False          # 严格 JSON 布尔，缺失按非流式
    model: Any = None             # 忽略（网关不强校验）
    max_tokens: int | None = None


# --- 鉴权 -------------------------------------------------------------------

def _check_credential(authorization: str | None, x_api_key: str | None) -> None:
    """Bearer 或 x-api-key 校验；未配置凭证时门面关闭（503）。"""
    key = (settings.compat_api_key or "").strip()
    if not key:
        raise HTTPException(503, "compat API 未启用（服务端未配置 COMPAT_API_KEY）")
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_key:
        token = x_api_key.strip()
    if token != key:
        raise HTTPException(401, "invalid_credential")


# --- helpers ----------------------------------------------------------------

def _last_user_text(messages: list[ChatMessage]) -> str:
    """取最后一条 user 消息的文本（多模态数组只取 text 段）。"""
    for m in reversed(messages):
        if m.role != "user":
            continue
        if isinstance(m.content, str):
            return m.content.strip()
        if isinstance(m.content, list):
            parts = [str(p.get("text", "")) for p in m.content
                     if isinstance(p, dict) and p.get("type") == "text"]
            return "\n".join(p for p in parts if p).strip()
    return ""


def _compat_session_id() -> str:
    digest = hashlib.sha256(settings.compat_api_key.encode()).hexdigest()[:12]
    return f"compat_{digest}"


def _chunk(cid: str, created: int, delta: dict[str, Any],
           finish_reason: str | None = None, **extra: Any) -> str:
    payload: dict[str, Any] = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    payload.update(extra)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _usage_from(trace_summary: dict[str, Any] | None) -> dict[str, int]:
    ts = trace_summary or {}
    return {
        "prompt_tokens": int(ts.get("prompt_tokens") or 0),
        "completion_tokens": int(ts.get("completion_tokens") or 0),
        "total_tokens": int(ts.get("total_tokens") or 0),
    }


def _probe_short_circuit(cid: str, created: int, stream: bool):
    """max_tokens<=2 的探测最小对话：不启动 Agent（一次完整教学轮要跑多次
    LLM 调用、几十秒，探测会超时），直接返回合法的最小 OpenAI 结构。"""
    content = "你好"
    if stream:
        frames = (
            _chunk(cid, created, {"role": "assistant"})
            + _chunk(cid, created, {"content": content})
            + _chunk(cid, created, {}, "stop",
                     usage={"prompt_tokens": 0, "completion_tokens": 1,
                            "total_tokens": 1})
            + "data: [DONE]\n\n"
        )
        return StreamingResponse(
            iter([frames]), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    return {
        "id": cid, "object": "chat.completion", "created": created,
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": content},
                     "finish_reason": "length"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 1, "total_tokens": 1},
    }


# --- 端点 -------------------------------------------------------------------

@router.get("/models")
async def models(authorization: str | None = Header(default=None),
                 x_api_key: str | None = Header(default=None)):
    _check_credential(authorization, x_api_key)
    return {"object": "list", "data": [
        {"id": "edu-agent", "object": "model", "owned_by": "edu_agent"},
    ]}


@router.post("/chat/completions",
             dependencies=[Depends(rate_limit("compat_chat", 30))])
async def chat_completions(req: ChatCompletionsRequest,
                           authorization: str | None = Header(default=None),
                           x_api_key: str | None = Header(default=None)):
    _check_credential(authorization, x_api_key)
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    # 探测快道：minimalChat 用 max_tokens:1 探测结构，不必惊动 Agent。
    if req.max_tokens is not None and req.max_tokens <= 2:
        return _probe_short_circuit(cid, created, req.stream)

    user_text = _last_user_text(req.messages)
    if not user_text:
        raise HTTPException(400, "messages 中需要至少一条非空 user 消息")

    from app.core.llm_async import get_llm
    from app.core.session import TutorSession, load_session
    from app.agents import supervisor
    from app.api.v1.chat import _build_tools

    session = load_session(_compat_session_id()) or TutorSession(
        session_id=_compat_session_id(), grade=settings.compat_grade or "本科")
    session.student_id = COMPAT_STUDENT_ID
    tools = _build_tools(session)
    llm = get_llm()

    if not req.stream:
        answer_parts: list[str] = []
        trace_summary: dict[str, Any] | None = None
        finish = "stop"
        try:
            async for ev in supervisor.run(
                user_text, session, tools, llm=llm,
                student_id=COMPAT_STUDENT_ID,
            ):
                if ev.get("type") == "answer" and ev.get("is_delta"):
                    answer_parts.append(ev.get("content") or "")
                elif ev.get("type") == "done":
                    trace_summary = ev.get("trace_summary")
                elif ev.get("type") == "error":
                    finish = "stop"  # finish_reason 白名单：无 error 值
        except Exception as e:
            raise HTTPException(502, f"agent 执行失败: {e}")
        return {
            "id": cid, "object": "chat.completion", "created": created,
            "choices": [{"index": 0,
                         "message": {"role": "assistant",
                                     "content": "".join(answer_parts)},
                         "finish_reason": finish}],
            "usage": _usage_from(trace_summary),
        }

    async def event_stream():
        # 1. role 帧（恰好一次，首帧）
        yield _chunk(cid, created, {"role": "assistant"})
        try:
            async for ev in supervisor.run(
                user_text, session, tools, llm=llm,
                student_id=COMPAT_STUDENT_ID,
            ):
                et = ev.get("type")
                if et == "thinking" and ev.get("is_delta"):
                    # L1 reasoning：思考增量进 delta.reasoning（只出不入）
                    piece = ev.get("content") or ""
                    if piece:
                        yield _chunk(cid, created, {"reasoning": piece})
                elif et == "answer" and ev.get("is_delta"):
                    piece = ev.get("content") or ""
                    if piece:
                        yield _chunk(cid, created, {"content": piece})
                elif et == "done":
                    # 2. stop 帧（恰好一次）+ usage 合并在此帧
                    yield _chunk(cid, created, {}, "stop",
                                 usage=_usage_from(ev.get("trace_summary")))
                elif et == "error":
                    # 流式中途出错：stop 帧 + error 字段，finish_reason 不用 error
                    yield _chunk(cid, created, {}, "stop",
                                 error={"type": "upstream_error",
                                        "message": str(ev.get("message", ""))[:200]})
        except Exception as e:
            yield _chunk(cid, created, {}, "stop",
                         error={"type": "upstream_error", "message": str(e)[:200]})
        # 3. 终止哨兵（必须）
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"})
