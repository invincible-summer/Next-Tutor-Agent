"""Bounded, process-local guest state. Never reads or writes guest files."""
from __future__ import annotations

import asyncio
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

IDLE_SECONDS = 30 * 60
MAX_CONTEXTS = 512
MAX_QUESTIONS = 100
_lock = threading.RLock()
_contexts: dict[str, "GuestContext"] = {}


@dataclass
class GuestContext:
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    owner_id: str = field(default_factory=lambda: "guest_" + uuid.uuid4().hex)
    touched_at: float = field(default_factory=time.monotonic)
    revoked: bool = False
    language: str = "zh"
    session: Any = None
    questions: dict[str, Any] = field(default_factory=dict)
    submissions: dict[str, dict] = field(default_factory=dict)
    tasks: set[asyncio.Task] = field(default_factory=set)
    chat_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    question_locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def check(self) -> None:
        from .guest_policy import guests_allowed
        if self.revoked or not guests_allowed():
            raise HTTPException(401, {"error": {"code": "guest_session_expired",
                                "message": "临时体验已结束，请重新开始或登录。"}})


def is_guest(student_id: str) -> bool:
    return student_id.startswith("guest_")


def is_legacy_guest_owner(owner: str) -> bool:
    """Exclude abandoned guest namespaces from durable background work."""
    if owner != "student_default" and not is_guest(owner):
        return False
    from app.identity.store import get_by_id
    return get_by_id(owner) is None


def _dispose(context: GuestContext) -> int:
    context.revoked = True
    tasks = list(context.tasks)
    context.session = None
    context.questions.clear()
    context.submissions.clear()
    context.question_locks.clear()
    for task in tasks:
        if not task.done():
            try:
                task.get_loop().call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
    return sum(not task.done() for task in tasks)


def sweep() -> None:
    now = time.monotonic()
    with _lock:
        for token, context in list(_contexts.items()):
            if now - context.touched_at >= IDLE_SECONDS:
                _contexts.pop(token, None)
                _dispose(context)


async def sweep_loop() -> None:
    while True:
        await asyncio.sleep(60)
        sweep()


def create_context() -> GuestContext:
    sweep()
    with _lock:
        if len(_contexts) >= MAX_CONTEXTS:
            raise HTTPException(429, "临时体验繁忙，请稍后再试。")
        context = GuestContext()
        context.check()
        _contexts[context.token] = context
        return context


def get_context(token: str | None) -> GuestContext:
    sweep()
    with _lock:
        context = _contexts.get(token or "")
        if context is None or context.revoked:
            raise HTTPException(401, {"error": {"code": "guest_session_expired",
                                "message": "临时体验已结束，请重新开始或登录。"}})
        context.check()
        context.touched_at = time.monotonic()
        return context


def context_for_owner(owner: str) -> GuestContext:
    with _lock:
        context = next((c for c in _contexts.values() if c.owner_id == owner), None)
        if context is None:
            raise HTTPException(401, "guest_session_expired")
        context.check()
        return context


def dispose(token: str | None) -> None:
    with _lock:
        context = _contexts.pop(token or "", None)
        if context is not None:
            _dispose(context)


def commit(context: GuestContext, action):
    """Serialize a memory update with revocation by admin/expiry cleanup."""
    with _lock:
        context.check()
        return action()


def stats() -> dict:
    sweep()
    with _lock:
        return {"visitors": len(_contexts),
                "sessions": sum(c.session is not None for c in _contexts.values()),
                "questions": sum(len(c.questions) for c in _contexts.values()),
                "tasks": sum(sum(not t.done() for t in c.tasks) for c in _contexts.values())}


def purge_all() -> dict:
    with _lock:
        contexts = list(_contexts.values())
        _contexts.clear()
        return {"visitors": len(contexts), "cancelled_tasks": sum(_dispose(c) for c in contexts)}


@asynccontextmanager
async def active_request(context: GuestContext):
    task = asyncio.current_task()
    with _lock:
        context.check()
        if task is not None:
            context.tasks.add(task)
    try:
        yield
        context.check()
    finally:
        with _lock:
            if task is not None:
                context.tasks.discard(task)
