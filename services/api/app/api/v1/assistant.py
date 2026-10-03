"""站内学习助手 API。

所有路径前缀 /api/v1/assistant。错误统一为一层 envelope（§11.1），
不暴露堆栈、存储路径或他人实体存在性。个人能力要求真实登录：
AUTH_MODE=0 的共享 student_default 不提供助手会话（§11.1）。

A05 覆盖：capabilities / guide / conversations CRUD / turns 受理与快照 /
SSE events / cancel / drafts。动作 execute/ack 随 A10 加入。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from app.agents.site_assistant import capabilities as caps
from app.agents.site_assistant import guide as guide_svc
from app.agents.site_assistant.ratelimit import SlidingWindowRateLimiter
from app.agents.site_assistant.runtime import (
    AssistantRuntime,
    TurnRejected,
    get_runtime,
)
from app.core import assistant_store as store
from app.core.assistant_store import AssistantStoreError
from app.identity.deps import optional_user, require_user
from app.identity.models import User
from app.schemas.assistant import (
    ActionAckRequest,
    ActionApproveRequest,
    ActionExecuteRequest,
    ActionUndoRequest,
    AssistantCapabilities,
    AssistantPreferencesInput,
    AssistantTurnRequest,
    ConversationCreateRequest,
    GuideRequest,
    GuideResponse,
    TurnCancelRequest,
)

router = APIRouter(prefix="/assistant", tags=["assistant"])

# 访客导览限流：每 IP 每分钟 10 次（§11.3）。
_guide_limiter = SlidingWindowRateLimiter(max_events=10, window_seconds=60.0)
# 登录用户 turn 受理限流：每分钟 20 次（§11.3）。
_turn_limiter = SlidingWindowRateLimiter(max_events=20, window_seconds=60.0)


def assistant_error(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    request_id: str | None = None,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """§11.1 固定错误体：一层 envelope，无堆栈与内部路径。"""
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "request_id": request_id or f"req_{uuid.uuid4().hex[:12]}",
        }
    }
    if extra:
        body["error"]["extra"] = extra
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def _rejected_response(exc: TurnRejected) -> JSONResponse:
    headers: dict[str, str] = {}
    if exc.status_code == 429:
        headers["Retry-After"] = "5"
    return assistant_error(exc.status_code, exc.code, exc.message,
                           retryable=exc.retryable, extra=exc.extra or None,
                           headers=headers or None)


# ---------------------------------------------------------------------------
# capabilities 与登录后的功能导览（A02）
# ---------------------------------------------------------------------------

@router.get("/capabilities", response_model=AssistantCapabilities)
def get_capabilities(user: User = Depends(require_user)) -> AssistantCapabilities:
    """能力旗标：不查询个人记录；enabled=false 时也正常返回。"""
    return caps.build_assistant_capabilities(user)


@router.get("/search")
def site_search(
    q: str = Query("", max_length=200),
    kinds: str = Query(""),
    workspace_id: str = Query("", max_length=128),
    offset: int = Query(0, ge=0),
    limit: int = Query(5, ge=1, le=20),
    include_content: bool = Query(False),
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """§20.4 全站实体检索：只读、本人范围；正文检索需显式 include_content。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能搜索你的学习内容。")
    from app.agents.site_assistant import search as search_mod
    kind_list = [k for k in kinds.split(",") if k] if kinds else None
    try:
        data = search_mod.search_site_entities(
            user.id, q=q, kinds=kind_list, workspace_id=workspace_id,
            offset=offset, limit=limit, include_content=include_content)
    except Exception:
        return assistant_error(503, "storage_unavailable",
                               "搜索暂不可用，请稍后重试。", retryable=True)
    return JSONResponse(
        content=data,
        headers={"Cache-Control": "private, max-age=0, no-store"})


@router.post("/guide", response_model=GuideResponse, dependencies=[Depends(require_user)])
async def post_guide(request: GuideRequest, http: Request) -> GuideResponse | JSONResponse:
    if not caps.assistant_enabled():
        return assistant_error(
            503, "capability_disabled", "学习助手当前未开放。", retryable=True)
    client_ip = http.client.host if http.client is not None else "unknown"
    allowed, retry_after = _guide_limiter.allow(client_ip)
    if not allowed:
        return assistant_error(
            429, "rate_limited", "请求过于频繁，请稍后再试。",
            headers={"Retry-After": str(int(retry_after) + 1)})
    return await guide_svc.answer_guide(request)


# ---------------------------------------------------------------------------
# 会话（§11.2）
# ---------------------------------------------------------------------------

@router.post("/conversations", status_code=201)
def create_conversation(
    request: ConversationCreateRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    if not caps.assistant_enabled():
        return assistant_error(503, "capability_disabled",
                               "学习助手当前未开放。", retryable=True)
    try:
        record = store.create_conversation(
            user.id, client_request_id=request.client_request_id,
            title=request.title)
    except AssistantStoreError as exc:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    return JSONResponse(status_code=201, content={
        "conversation_id": record["conversation_id"],
        "revision": record["revision"],
        "created_at": record["created_at"],
    })


@router.get("/conversations")
def list_conversations(
    user: User | None = Depends(optional_user),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    try:
        items, total = store.list_conversations(
            user.id, offset=offset, limit=limit)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    return JSONResponse(content={"items": items, "total": total,
                                 "offset": offset, "limit": limit})


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    user: User | None = Depends(optional_user),
    before_seq: int | None = Query(default=None, ge=1),
    limit: int = Query(default=40, ge=1, le=100),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    try:
        record = store.load_conversation(user.id, conversation_id)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    if record is None:
        return assistant_error(404, "conversation_not_found", "会话不存在。")
    messages = list(record.get("messages") or [])
    if before_seq is not None:
        messages = [m for m in messages if m.get("seq", 0) < before_seq]
    messages = messages[-limit:]
    has_more = bool(messages) and (
        (record.get("messages") or [{}])[0].get("seq", 0)
        < messages[0].get("seq", 0))
    runtime = get_runtime()
    active_turn = None
    running = runtime.active_turn_of(user.id, conversation_id)
    if running is not None:
        active_turn = {"turn_id": running.turn_id, "state": "running",
                       "created_at": (record.get("turns") or {}).get(
                           running.turn_id, {}).get("created_at")}
    else:
        for turn_id, info in (record.get("turns") or {}).items():
            if info.get("state") == "running":
                active_turn = {"turn_id": turn_id, "state": "running",
                               "created_at": info.get("created_at")}
                break
    return JSONResponse(content={
        "conversation_id": record["conversation_id"],
        "title": record.get("title", "新对话"),
        "revision": record.get("revision", 1),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "messages": messages,
        "has_more": has_more,
        "active_turn": active_turn,
    })


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str,
    user: User | None = Depends(optional_user),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> None:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    try:
        record = store.load_conversation(user.id, conversation_id)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    if record is None:
        return Response(status_code=204)  # 重复删除同属已删除对象仍 204
    if if_match is not None and if_match != str(record.get("revision", 1)):
        return assistant_error(409, "revision_conflict",
                               "会话版本已变化。", extra={
                                   "latest_revision": record.get("revision", 1)})
    # 取消在途轮，使动作/ack 失效（§11.2）。
    runtime = get_runtime()
    running = runtime.active_turn_of(user.id, conversation_id)
    if running is not None:
        running.cancel_requested = True
        if running.task and not running.task.done():
            running.task.cancel()
    try:
        store.delete_conversation(user.id, conversation_id)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# 轮（§11.2 / §11.3 / §11.5 / §19.2）
# ---------------------------------------------------------------------------

def _turn_snapshot(record: dict[str, Any], turn_id: str) -> dict[str, Any] | None:
    info = (record.get("turns") or {}).get(turn_id)
    if info is None:
        return None
    user_message = None
    assistant_message = None
    for message in record.get("messages") or []:
        if message.get("turn_id") != turn_id:
            continue
        if message.get("role") == "user":
            user_message = message
        elif message.get("role") == "assistant":
            assistant_message = message
    if user_message is None or assistant_message is None:
        return None
    actions = [a for a in (record.get("actions") or {}).values()
               if a.get("turn_id") == turn_id]
    from app.agents.site_assistant.actions import public_action
    actions = [public_action(a) for a in actions]
    accepted = record.get("accepted") or {}
    client_message_id = ""
    for key, value in accepted.items():
        if value.get("turn_id") == turn_id:
            client_message_id = key
            break
    return {
        "schema_version": 1,
        "turn_id": turn_id,
        "conversation_id": record["conversation_id"],
        "conversation_revision": record.get("revision", 1),
        "client_message_id": client_message_id,
        "state": info.get("state", "completed"),
        "cancel_requested": bool(info.get("cancel_requested")),
        "created_at": info.get("created_at"),
        "updated_at": info.get("updated_at"),
        "last_event_seq": 0,
        "user_message": user_message,
        "assistant_message": assistant_message,
        "actions": actions,
        "error": info.get("error"),
    }


def _locate_turn(student_id: str, turn_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """定位 turn 所属会话；返回 (record, snapshot)。"""
    items, _total = store.list_conversations(student_id, limit=100)
    for summary in items:
        record = store.load_conversation(
            student_id, summary.get("conversation_id"))
        if record is None:
            continue
        snapshot = _turn_snapshot(record, turn_id)
        if snapshot is not None:
            return record, snapshot
    return None


@router.post("/conversations/{conversation_id}/turns", status_code=202)
async def create_turn(
    conversation_id: str,
    request: AssistantTurnRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    if not caps.assistant_enabled():
        # §17-5：模型故障仍保留静态帮助与确定性导航——只在总开关关闭时
        # 拒绝受理；模型不可用由轮内确定性兜底承接。
        return assistant_error(
            503, "capability_disabled",
            "助手服务当前不可用。", retryable=True)
    allowed, retry_after = _turn_limiter.allow(user.id)
    if not allowed:
        return assistant_error(
            429, "rate_limited", "请求过于频繁，请稍后再试。",
            headers={"Retry-After": str(int(retry_after) + 1)})
    text = request.text.strip()
    runtime = get_runtime()
    try:
        meta = await asyncio.to_thread(
            runtime.accept_turn, user.id, conversation_id,
            client_message_id=request.client_message_id, text=text,
            expected_conversation_revision=(
                request.expected_conversation_revision))
    except TurnRejected as exc:
        return _rejected_response(exc)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)

    state = meta["state"]
    if not meta["duplicate"] and state == "running":
        payload = {
            "schema_version": request.schema_version,
            "client_message_id": request.client_message_id,
            "text": text,
            "lang": request.lang,
            "timezone": request.timezone,
            "scope": (request.scope.model_dump(mode="json")
                      if hasattr(request.scope, "model_dump")
                      else dict(request.scope)),
            "page_context": (request.page_context.model_dump(mode="json")
                             if hasattr(request.page_context, "model_dump")
                             else dict(request.page_context)),
            "choice": (request.choice.model_dump(mode="json")
                       if request.choice else None),
        }
        runtime.spawn(user.id, conversation_id, meta["turn_id"], payload)

    return JSONResponse(status_code=202, content={
        "turn_id": meta["turn_id"],
        "conversation_id": conversation_id,
        "conversation_revision": meta["conversation_revision"],
        "state": state,
        "events_path": f"/api/v1/assistant/turns/{meta['turn_id']}/events",
        "duplicate": meta["duplicate"],
    })


@router.get("/turns/{turn_id}")
def get_turn(
    turn_id: str,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    try:
        located = _locate_turn(user.id, turn_id)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    if located is None:
        return assistant_error(404, "entity_not_found", "该轮不存在。")
    _record, snapshot = located
    runtime = get_runtime()
    running = runtime.get_running(turn_id)
    if running is not None and running.student_id == user.id:
        snapshot["last_event_seq"] = running.last_event_seq
    return JSONResponse(content=snapshot)


@router.post("/turns/{turn_id}/cancel")
async def cancel_turn(
    turn_id: str,
    request: TurnCancelRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    runtime = get_runtime()
    try:
        result = await runtime.request_cancel(user.id, turn_id)
    except TurnRejected as exc:
        return _rejected_response(exc)
    return JSONResponse(content=result)


# --- 动作 execute / ack / 查询（§9.3/§9.4/§19.3/§19.4，A10） -----------------

@router.get("/preferences")
async def get_preferences(
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """§22.4 助手偏好（回答风格白名单）；与本地 UI 偏好作用范围不同。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.core import assistant_store as prefs_store
    return JSONResponse(content=prefs_store.load_preferences(user.id))


@router.put("/preferences")
async def put_preferences(
    request: AssistantPreferencesInput,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """白名单合并写入（§24.6 版本合并）；只影响助手回答风格与语音偏好。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.core import assistant_store as prefs_store
    from app.core.assistant_store import AssistantStoreError
    try:
        merged = prefs_store.save_preferences(
            user.id, request.model_dump(exclude={"base_revision"}),
            base_revision=request.base_revision)
    except AssistantStoreError as exc:
        if exc.code == "conflict":
            return assistant_error(409, "revision_conflict", str(exc))
        return assistant_error(503, "storage_unavailable", str(exc),
                               retryable=True)
    return JSONResponse(content=merged)


# --- 助手语音（B11） -----------------------------------------

@router.get("/voice/capabilities")
async def get_voice_capabilities(
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """识别提示、可用 provider、音色、语言与限制；不合成。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.voice.tts.service import tts_capabilities
    caps = tts_capabilities()
    caps["recognition"] = {
        "provider": "browser",
        "hint": "浏览器语音识别（能力随浏览器与系统语言而异）。",
        "max_seconds": 60,
    }
    return JSONResponse(content=caps)


class AudioJobRequest(BaseModel):
    model_config = {"extra": "forbid"}
    message_id: str = Field(min_length=1, max_length=64)
    block_ids: list[str] = Field(default_factory=list, max_length=40)
    policy: Literal["auto", "cloud", "local", "silent"] = "auto"
    voice_id: str = Field(default="", max_length=128)
    language: Literal["zh", "en"] = "zh"
    allow_local_fallback: bool = True
    client_request_id: str = Field(min_length=8, max_length=64)


class VoicePreviewRequest(BaseModel):
    model_config = {"extra": "forbid"}
    policy: Literal["auto", "cloud", "local"] = "auto"
    voice_id: str = Field(default="", max_length=128)
    language: Literal["zh", "en"] = "zh"
    allow_local_fallback: bool = True


def _voice_error_response(exc) -> JSONResponse:
    return JSONResponse(status_code=int(exc.status), content={
        "error": {"code": str(exc.code), "message": str(exc.message)}})


def _locate_message(user: User, message_id: str):
    from app.core import assistant_store as store
    items, _total = store.list_conversations(user.id, limit=100)
    for summary in items:
        cid = summary.get("conversation_id")
        record = store.load_conversation(user.id, cid)
        if record is None:
            continue
        for message in record.get("messages") or []:
            if str(message.get("message_id")) == str(message_id) \
                    and message.get("role") == "assistant":
                message["conversation_id"] = cid
                return message
    return None


@router.post("/audio/jobs", status_code=202)
async def create_audio_job(
    request: AudioJobRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """§24.3：从已存本人消息取允许朗读文本；去重命中返回 200。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import voice as voice_svc
    message = _locate_message(user, request.message_id)
    if message is None:
        return assistant_error(404, "entity_not_found",
                               "消息不存在或不可朗读。")
    try:
        job, cached = await asyncio.to_thread(
            voice_svc.create_job, user.id,
            message=message, policy=request.policy,
            voice_id=request.voice_id, language=request.language,
            allow_local_fallback=request.allow_local_fallback,
            client_request_id=request.client_request_id)
    except voice_svc.VoiceRejected as exc:
        return _voice_error_response(exc)
    if cached:
        return JSONResponse(status_code=200,
                            content=voice_svc.public_job(job))
    asyncio.create_task(_run_audio_job(user.id, job["job_id"]))
    return JSONResponse(status_code=202, content=voice_svc.public_job(job))


async def _run_audio_job(student_id: str, job_id: str) -> None:
    from app.agents.site_assistant import voice as voice_svc
    try:
        await voice_svc.run_job(student_id, job_id)
    except voice_svc.VoiceRejected:
        pass


@router.get("/audio/jobs/{job_id}")
async def get_audio_job(
    job_id: str,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import voice as voice_svc
    job = voice_svc.load_job(user.id, job_id)
    if job is None:
        return assistant_error(404, "entity_not_found", "合成任务不存在。")
    if str(job.get("state")) == "queued":
        # 重启后未完成的排队任务：补调度（幂等；running 单飞）。
        asyncio.create_task(_run_audio_job(user.id, job_id))
    return JSONResponse(content=voice_svc.public_job(job))


@router.post("/audio/jobs/{job_id}/cancel")
async def cancel_audio_job(
    job_id: str,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import voice as voice_svc
    try:
        job = await asyncio.to_thread(
            voice_svc.cancel_job, user.id, job_id)
    except voice_svc.VoiceRejected as exc:
        return _voice_error_response(exc)
    return JSONResponse(content=job)


@router.get("/audio/clips/{clip_id}/content")
async def get_audio_clip(
    clip_id: str,
    user: User | None = Depends(optional_user),
) -> Response:
    """鉴权二进制音频；Content-Type 来自已登记格式；不访问任意路径。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import voice as voice_svc
    try:
        data, media_type, _job = await asyncio.to_thread(
            voice_svc.clip_content, user.id, clip_id)
    except voice_svc.VoiceRejected as exc:
        return _voice_error_response(exc)
    from fastapi.responses import Response as RawResponse
    return RawResponse(content=data, media_type=media_type)


@router.post("/voice/preview")
async def voice_preview(
    request: VoicePreviewRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """固定短样例；限流；不接受任意长文本（§24.3）。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import voice as voice_svc
    try:
        out = await voice_svc.synthesize_preview(
            user.id, policy=request.policy, voice_id=request.voice_id,
            language=request.language,
            allow_local_fallback=request.allow_local_fallback)
    except voice_svc.VoiceRejected as exc:
        return _voice_error_response(exc)
    return JSONResponse(content=out)


@router.get("/actions/{action_id}/preview")
async def get_action_preview(
    action_id: str,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """§21.4 预览：只读；同参数幂等返回同一预览。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import previews as previews_svc
    from app.agents.site_assistant.actions import ActionRejected
    try:
        # prepare_preview：plan.regenerate 先建候选（§21.6.3），其余确定。
        preview = await previews_svc.prepare_preview(
            user.id, action_id, is_admin=(user.role == "admin"))
    except ActionRejected as exc:
        return _rejected_response(exc)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    return JSONResponse(content=preview)


@router.post("/actions/{action_id}/approve")
async def approve_action(
    action_id: str,
    request: ActionApproveRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """§21.4 审批：approve 表示允许该具体动作，不等于已执行。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import previews as previews_svc
    from app.agents.site_assistant.actions import ActionRejected
    try:
        approval = await asyncio.to_thread(
            previews_svc.approve, user.id, action_id,
            preview_id=request.preview_id,
            parameter_hash_in=request.parameter_hash,
            decision=request.decision)
    except ActionRejected as exc:
        return _rejected_response(exc)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    return JSONResponse(content=approval)


@router.post("/actions/{action_id}/undo")
async def undo_action(
    action_id: str,
    request: ActionUndoRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """§21.4/§21.5 撤销：真实补偿，仅可逆操作且窗口内有效。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    if not caps.assistant_enabled():
        return assistant_error(503, "capability_disabled",
                               "助手服务当前不可用。", retryable=True)
    from app.agents.site_assistant import undo as undo_svc
    from app.agents.site_assistant.actions import ActionRejected
    try:
        result = await asyncio.to_thread(
            undo_svc.undo_action, user.id, action_id,
            client_request_id=str(request.client_request_id),
            expected_result_revision=request.expected_result_revision,
            is_admin=(user.role == "admin"))
    except ActionRejected as exc:
        return _rejected_response(exc)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    return JSONResponse(content=result)


@router.get("/actions/{action_id}")
async def get_action(
    action_id: str,
    client_instance_id: str | None = Query(default=None),
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import actions as actions_svc
    from app.agents.site_assistant.actions import ActionRejected
    try:
        result = await asyncio.to_thread(
            actions_svc.get_action, user.id, action_id, client_instance_id)
    except ActionRejected as exc:
        return _rejected_response(exc)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    return JSONResponse(content=result)


@router.post("/actions/{action_id}/execute")
async def execute_action(
    action_id: str,
    request: ActionExecuteRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """200：命令已就绪（A10 全同步）；202+command=null 预留给 >3s 准备。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    if not caps.assistant_enabled():
        return assistant_error(503, "capability_disabled",
                               "助手服务当前不可用。", retryable=True)
    from app.agents.site_assistant import actions as actions_svc
    from app.agents.site_assistant.actions import ActionRejected
    try:
        result = await asyncio.to_thread(
            actions_svc.execute_action, user.id, action_id,
            invocation_id=request.invocation_id,
            client_instance_id=request.client_instance_id,
            route_epoch=request.route_epoch,
            approval_id=request.approval_id,
            is_admin=(user.role == "admin"),
            main_loop=asyncio.get_running_loop())
    except ActionRejected as exc:
        return _rejected_response(exc)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    # 202+command=null 只用于异步准备未完（§19.4）；A10 全同步路径 200。
    status = 202 if result.get("action", {}).get("state") == "executing" \
        else 200
    return JSONResponse(status_code=status, content=result)


@router.post("/actions/{action_id}/ack")
async def ack_action(
    action_id: str,
    request: ActionAckRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import actions as actions_svc
    from app.agents.site_assistant.actions import ActionRejected
    try:
        result = await asyncio.to_thread(
            actions_svc.ack_action, user.id, action_id,
            command_id=request.command_id, ack_token=request.ack_token,
            result=request.result, error_code=request.error_code)
    except ActionRejected as exc:
        return _rejected_response(exc)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    return JSONResponse(content=result)


@router.get("/turns/{turn_id}/events", response_model=None)
async def stream_turn_events(
    turn_id: str,
    user: User | None = Depends(optional_user),
    after_seq: int = Query(default=0, ge=0),
) -> StreamingResponse | JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    runtime = get_runtime()
    running = runtime.get_running(turn_id)
    if (running is None or running.student_id != user.id
            or not runtime.is_live(running)):
        # 已终结或重启后：仅观察——发完整 snapshot + turn_done（§11.5），
        # 不新建轮、不重新调用模型。
        try:
            located = _locate_turn(user.id, turn_id)
        except AssistantStoreError:
            return assistant_error(503, "storage_unavailable",
                                   "存储暂不可用，请稍后重试。", retryable=True)
        if located is None:
            return assistant_error(404, "entity_not_found", "该轮不存在。")
        _record, snapshot = located

        async def replay() -> Any:
            yield _format_sse({
                "event": "snapshot", "snapshot": snapshot,
                "last_event_seq": 0})
            yield _format_sse({
                "event": "turn_done", "state": snapshot.get("state"),
                "conversation_revision": snapshot.get(
                    "conversation_revision", 1)})

        return StreamingResponse(replay(), media_type="text/event-stream",
                                 headers=_sse_headers())
    replay_events, queue = await runtime.subscribe(running, after_seq)

    async def event_stream() -> Any:
        try:
            for event in replay_events:
                yield _format_sse(event)
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # 15s 心跳注释
                    continue
                yield _format_sse(event)
                if event.get("event") == "turn_done":
                    break
        finally:
            runtime.unsubscribe(running, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers=_sse_headers())


def _sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "private, no-store",
        "X-Accel-Buffering": "no",
    }


def _format_sse(event: dict[str, Any]) -> str:
    event_name = str(event.get("event", "message"))
    return (f"event: {event_name}\n"
            f"id: {event.get('turn_id')}:{event.get('event_seq')}\n"
            f"data: {json.dumps(event, ensure_ascii=False)}\n\n")


# ---------------------------------------------------------------------------
# 草稿（§9.5 / §11.2；消费语义在 A13 完整接入）
# ---------------------------------------------------------------------------

@router.get("/drafts/{draft_id}")
def get_draft(
    draft_id: str,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手。")
    try:
        draft = store.load_draft(user.id, draft_id)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    if draft is None:
        return assistant_error(404, "entity_not_found", "草稿不存在。")
    return JSONResponse(content=draft)


@router.post("/drafts/{draft_id}/consume")
def consume_draft(
    draft_id: str,
    payload: dict[str, Any],
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手。")
    existing = None
    try:
        existing = store.load_draft(user.id, draft_id)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    if existing is None:
        return assistant_error(404, "entity_not_found", "草稿不存在。")
    if existing.get("expired"):
        return assistant_error(410, "draft_expired", "草稿已过期。")
    if existing.get("consumed"):
        # §19.6：重复消费携带相同结果实体 → 幂等返回；空或不同实体 → 409。
        recorded = existing.get("result_entity") or {}
        requested = (payload or {}).get("result_entity") or {}
        if recorded and recorded == requested:
            return JSONResponse(content=existing)
        return assistant_error(
            409, "draft_already_consumed",
            "草稿已绑定其他结果实体。", retryable=False)
    try:
        consumed = store.consume_draft(
            user.id, draft_id, result_entity=payload.get("result_entity"))
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    return JSONResponse(content=consumed)


@router.delete("/drafts/{draft_id}", status_code=204)
def delete_draft(
    draft_id: str,
    user: User | None = Depends(optional_user),
) -> None:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手。")
    try:
        store.delete_draft(user.id, draft_id)
    except AssistantStoreError:
        return assistant_error(503, "storage_unavailable",
                               "存储暂不可用，请稍后重试。", retryable=True)
    return Response(status_code=204)

# --- 助手工作流（C01） ----------------------------------------

class WorkflowCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    conversation_id: str = Field(min_length=8, max_length=64)
    client_request_id: str = Field(min_length=8, max_length=64)
    template: str = Field(min_length=4, max_length=64)
    objective: str = Field(min_length=4, max_length=2000)
    scope: dict = Field(default_factory=dict)
    selection_ids: dict = Field(default_factory=dict)


class WorkflowApproveRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=1)
    plan_hash: str = Field(min_length=8, max_length=128)
    approved_step_ids: list[str] = Field(min_length=1, max_length=8)


class WorkflowStartRequest(BaseModel):
    model_config = {"extra": "forbid"}
    client_request_id: str = Field(min_length=8, max_length=64)
    expected_revision: int = Field(ge=1)


class WorkflowStepRetryRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=1)
    client_request_id: str = Field(min_length=8, max_length=64)


def _workflow_error(exc) -> JSONResponse:
    return JSONResponse(status_code=int(exc.status), content={
        "error": {"code": str(exc.code), "message": str(exc.message)}})


@router.post("/workflows", status_code=201)
async def create_workflow(
    request: WorkflowCreateRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """§23.5：只创建 draft，不写业务。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import workflows as wf_svc
    try:
        wf = await asyncio.to_thread(
            wf_svc.create_workflow, user.id,
            conversation_id=request.conversation_id,
            template=request.template, objective=request.objective,
            scope=dict(request.scope),
            selection_ids=dict(request.selection_ids),
            client_request_id=request.client_request_id)
    except wf_svc.WorkflowRejected as exc:
        return _workflow_error(exc)
    preview = await asyncio.to_thread(wf_svc.workflow_preview, user.id, wf)
    return JSONResponse(status_code=201, content=preview)


@router.get("/workflows")
async def list_workflows(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    state: str = Query(default=""),
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import workflows as wf_svc
    # 恢复点：等待/中断的工作流在此续跑（幂等单飞，§23.4-6）。
    await asyncio.to_thread(wf_svc.resume_or_recover, user.id)
    items, total = await asyncio.to_thread(
        wf_svc.list_workflows, user.id, offset=offset, limit=limit,
        state=state)
    return JSONResponse(content={"items": items, "total": total})


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import workflows as wf_svc
    wf = await asyncio.to_thread(
        wf_svc.load_workflow, user.id, workflow_id)
    if wf is None:
        return assistant_error(404, "entity_not_found", "工作流不存在。")
    preview = await asyncio.to_thread(
        wf_svc.workflow_preview, user.id, wf)
    return JSONResponse(content={"workflow": wf, "preview": preview})


@router.post("/workflows/{workflow_id}/approve")
async def approve_workflow(
    workflow_id: str,
    request: WorkflowApproveRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import workflows as wf_svc
    try:
        wf = await asyncio.to_thread(
            wf_svc.approve_workflow, user.id, workflow_id,
            expected_revision=request.expected_revision,
            plan_hash_in=request.plan_hash,
            approved_step_ids=request.approved_step_ids)
    except wf_svc.WorkflowRejected as exc:
        return _workflow_error(exc)
    return JSONResponse(content=wf)


@router.post("/workflows/{workflow_id}/start", status_code=202)
async def start_workflow(
    workflow_id: str,
    request: WorkflowStartRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import workflows as wf_svc
    try:
        wf = await asyncio.to_thread(
            wf_svc.start_workflow, user.id, workflow_id,
            client_request_id=request.client_request_id,
            expected_revision=request.expected_revision)
    except wf_svc.WorkflowRejected as exc:
        return _workflow_error(exc)
    # 入队后由后台任务执行；领域长作业等待持久化（§23.4-4）。
    wf_svc._running[str(wf.get("workflow_id"))] = asyncio.create_task(
        wf_svc.run_workflow(user.id, workflow_id))
    return JSONResponse(status_code=202, content=wf)


@router.post("/workflows/{workflow_id}/cancel")
async def cancel_workflow(
    workflow_id: str,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import workflows as wf_svc
    try:
        wf = await asyncio.to_thread(
            wf_svc.cancel_workflow, user.id, workflow_id)
    except wf_svc.WorkflowRejected as exc:
        return _workflow_error(exc)
    return JSONResponse(content=wf)


@router.post("/workflows/{workflow_id}/steps/{step_id}/retry")
async def retry_workflow_step(
    workflow_id: str,
    step_id: str,
    request: WorkflowStepRetryRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """§23.5：只重试该失败步骤（幂等键复用，不重复创建实体）。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import workflows as wf_svc
    try:
        wf = await asyncio.to_thread(
            wf_svc.retry_step, user.id, workflow_id, step_id=step_id,
            expected_revision=request.expected_revision,
            client_request_id=request.client_request_id)
    except wf_svc.WorkflowRejected as exc:
        return _workflow_error(exc)
    wf_svc._running[workflow_id] = asyncio.create_task(
        wf_svc.run_workflow(user.id, workflow_id))
    return JSONResponse(status_code=202, content=wf)


class WorkflowResumeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=1)
    choice_id: str = Field(default="", max_length=128)
    approval_id: str = Field(default="", max_length=128)


@router.post("/workflows/{workflow_id}/resume")
async def resume_workflow(
    workflow_id: str,
    request: WorkflowResumeRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """§23.5：补足等待条件后继续（queued 后台续跑）。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import workflows as wf_svc
    try:
        wf = await asyncio.to_thread(
            wf_svc.resume_workflow, user.id, workflow_id,
            expected_revision=request.expected_revision,
            choice_id=request.choice_id,
            approval_id=request.approval_id)
    except wf_svc.WorkflowRejected as exc:
        return _workflow_error(exc)
    wf_svc._running[workflow_id] = asyncio.create_task(
        wf_svc.run_workflow(user.id, workflow_id))
    return JSONResponse(status_code=202, content=wf)


@router.get("/workflows/{workflow_id}/events")
async def workflow_events(
    workflow_id: str,
    after_seq: int = Query(default=0, ge=0),
    user: User | None = Depends(optional_user),
) -> Response:
    """§23.5 SSE：快照 + step_updated + workflow_done（仅观察）。"""
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    from app.agents.site_assistant import workflows as wf_svc

    async def _gen():
        try:
            async for event, payload, seq in wf_svc.stream_workflow_events(
                    user.id, workflow_id, after_seq=after_seq):
                data = json.dumps({"event": event, "seq": seq,
                                   **payload}, ensure_ascii=False,
                                  default=str)
                yield (f"id: {workflow_id}:{seq}\nevent: {event}\n"
                       f"data: {data}\n\n")
        except wf_svc.WorkflowRejected as exc:
            data = json.dumps({"event": "error", "code": str(exc.code),
                               "message": str(exc.message)},
                              ensure_ascii=False)
            yield f"event: error\ndata: {data}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "private, no-store",
                                      "X-Accel-Buffering": "no"})


# --- 主动服务：订阅 / 收件箱 / 报告（C04/C05） -------------------

class SubscriptionCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    client_request_id: str = Field(min_length=8, max_length=64)
    kind: str = Field(min_length=4, max_length=32)
    timezone: str = Field(default="UTC", max_length=64)
    local_time: str = Field(default="", max_length=5)
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    scope: dict = Field(default_factory=dict)


class SubscriptionPatchRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_revision: int = Field(ge=1)
    enabled: bool | None = None
    timezone: str | None = Field(default=None, max_length=64)
    local_time: str | None = Field(default=None, max_length=5)
    weekdays: list[int] | None = Field(default=None, max_length=7)
    scope: dict | None = None
    quiet_hours: dict | None = None


def _subscription_error(exc) -> JSONResponse:
    return JSONResponse(status_code=int(exc.status), content={
        "error": {"code": str(exc.code), "message": str(exc.message)}})


def _require_user(user: User | None) -> User | JSONResponse | None:
    if user is None:
        return assistant_error(401, "authentication_required",
                               "登录后才能使用学习助手会话。")
    return None


@router.get("/subscriptions")
async def list_subscriptions(
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    denied = _require_user(user)
    if isinstance(denied, JSONResponse):
        return denied
    from app.agents.site_assistant import notifications as notify
    subs = await asyncio.to_thread(notify.load_subscriptions, user.id)
    return JSONResponse(content={"items": sorted(
        subs.values(), key=lambda s: str(s.get("created_at")),
        reverse=True)})


@router.post("/subscriptions", status_code=201)
async def create_subscription(
    request: SubscriptionCreateRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    """§25.1：创建即用户显式开启（默认建议时间仅用于表单预填）。"""
    denied = _require_user(user)
    if isinstance(denied, JSONResponse):
        return denied
    from app.agents.site_assistant import notifications as notify
    if not notify.scheduler_enabled():
        return assistant_error(503, "capability_disabled",
                               "主动服务调度当前未开放。")
    try:
        sub = await asyncio.to_thread(
            notify.create_subscription, user.id,
            kind=request.kind, timezone_name=request.timezone,
            local_time=request.local_time, weekdays=request.weekdays,
            scope=dict(request.scope),
            client_request_id=request.client_request_id)
    except notify.SubscriptionRejected as exc:
        return _subscription_error(exc)
    return JSONResponse(status_code=201, content=sub)


@router.patch("/subscriptions/{subscription_id}")
async def patch_subscription(
    subscription_id: str,
    request: SubscriptionPatchRequest,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    denied = _require_user(user)
    if isinstance(denied, JSONResponse):
        return denied
    from app.agents.site_assistant import notifications as notify
    patch = {k: getattr(request, k) for k in (
        "enabled", "timezone", "local_time", "weekdays", "scope",
        "quiet_hours") if getattr(request, k) is not None}
    try:
        sub = await asyncio.to_thread(
            notify.update_subscription, user.id, subscription_id,
            expected_revision=request.expected_revision, patch=patch)
    except notify.SubscriptionRejected as exc:
        return _subscription_error(exc)
    return JSONResponse(content=sub)


@router.delete("/subscriptions/{subscription_id}", status_code=204)
async def delete_subscription(
    subscription_id: str,
    user: User | None = Depends(optional_user),
) -> Response:
    denied = _require_user(user)
    if isinstance(denied, JSONResponse):
        return denied
    from app.agents.site_assistant import notifications as notify
    await asyncio.to_thread(notify.delete_subscription, user.id,
                            subscription_id)
    return Response(status_code=204)


@router.get("/notifications")
async def list_notifications(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    unread_only: bool = Query(default=False),
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    denied = _require_user(user)
    if isinstance(denied, JSONResponse):
        return denied
    from app.agents.site_assistant import notifications as notify
    items, total = await asyncio.to_thread(
        notify.list_notifications, user.id,
        offset=offset, limit=limit, unread_only=unread_only)
    return JSONResponse(content={"items": items, "total": total,
                                 "offset": offset, "limit": limit})


@router.post("/notifications/{notification_id}/read")
async def read_notification(
    notification_id: str,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    denied = _require_user(user)
    if isinstance(denied, JSONResponse):
        return denied
    from app.agents.site_assistant import notifications as notify
    note = await asyncio.to_thread(
        notify.mark_notification, user.id, notification_id,
        action="read")
    if note is None:
        return assistant_error(404, "entity_not_found", "通知不存在。")
    return JSONResponse(content=note)


@router.post("/notifications/{notification_id}/dismiss")
async def dismiss_notification(
    notification_id: str,
    body: dict | None = None,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    denied = _require_user(user)
    if isinstance(denied, JSONResponse):
        return denied
    from app.agents.site_assistant import notifications as notify
    mute = bool((body or {}).get("mute_entity"))
    note = await asyncio.to_thread(
        notify.mark_notification, user.id, notification_id,
        action="dismiss", mute_entity=mute)
    if note is None:
        return assistant_error(404, "entity_not_found", "通知不存在。")
    return JSONResponse(content=note)


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str,
    user: User | None = Depends(optional_user),
) -> JSONResponse:
    denied = _require_user(user)
    if isinstance(denied, JSONResponse):
        return denied
    from app.agents.site_assistant import reports
    report = await asyncio.to_thread(
        reports.load_report, user.id, report_id)
    if report is None:
        return assistant_error(404, "entity_not_found", "报告不存在。")
    return JSONResponse(content=report)


@router.delete("/reports/{report_id}", status_code=204)
async def delete_report(
    report_id: str,
    user: User | None = Depends(optional_user),
) -> Response:
    """删除助手报告与派生副本；原业务证据不受影响（§25.4）。"""
    denied = _require_user(user)
    if isinstance(denied, JSONResponse):
        return denied
    from app.agents.site_assistant import reports
    await asyncio.to_thread(reports.delete_report, user.id, report_id)
    return Response(status_code=204)
