"""助手轮运行时（A05）。

单 worker 进程内运行模型（与项目其余后台任务一致）：
- 接收 turn 时先持久化 running 受理信息，再创建 asyncio task。
- 每轮保留最近 512 个业务事件的内存环；终态后保留 10 分钟。
- SSE 订阅在同一临界区取得快照游标并接入后续事件（无缺口）。
- 取消：写 cancel_requested，中止模型流，保存最后一批文本；
  该轮尚未 execute 的 proposed 动作同时置 cancelled（§11.6）。
- lifespan 启动只扫描在途状态并标记 interrupted，不重放业务动作。
- 账号清理：cancel_user_turns 提升 owner_generation 并撤销在途任务；
  迟到写入检查 generation，不得在目录清理后重建目录（§12.3-11）。

执行编排本身在 service.execute_turn（A09 起承载意图/工具/回答）；
本模块只负责传输、状态机、事件与持久化纪律。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agents.site_assistant import store
from app.agents.site_assistant.store import AssistantStoreError

_EVENT_RING_SIZE = 512
_TERMINAL_RETENTION_SECONDS = 600
_HEARTBEAT_SECONDS = 15.0
_STREAM_SNAPSHOT_INTERVAL = 0.5
TERMINAL_STATES = ("completed", "cancelled", "failed", "interrupted")
MAX_TURNS_PER_USER = 2  # 跨会话并发上限（§11.3）

_terminal_since: dict[str, float] = {}


class TurnRejected(Exception):
    """受理失败；code 映射 §11.7 错误码。"""

    def __init__(self, status_code: int, code: str, message: str,
                 *, retryable: bool = False,
                 extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.extra = extra or {}


def body_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class _RunningTurn:
    def __init__(self, student_id: str, conversation_id: str,
                 turn_id: str, record: dict[str, Any]) -> None:
        self.student_id = student_id
        self.conversation_id = conversation_id
        self.turn_id = turn_id
        self.record = record
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.events: deque[dict[str, Any]] = deque(maxlen=_EVENT_RING_SIZE)
        self.subscribers: set[asyncio.Queue] = set()
        self.cancel_requested = False
        self.last_event_seq = 0
        self.last_snapshot_write = 0.0
        self.gen = 0                      # 受理时的 owner_generation
        self.request: dict[str, Any] = {}  # 冻结的本轮请求快照


_RUNTIME: "AssistantRuntime | None" = None


class AssistantRuntime:
    """进程级单例；由 main lifespan 启停。"""

    def __init__(self) -> None:
        self._running: dict[str, _RunningTurn] = {}
        self._owner_generation: dict[str, int] = {}
        self._scheduler_task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        await asyncio.to_thread(self._recover_interrupted)
        # §25.3-1 一分钟粒度订阅调度（SITE_ASSISTANT_PROACTIVE_ENABLED
        # 独立开关；用户订阅仍默认逐项关闭）。durable 模式（ADR-0013 C5）
        # 下调度由 maintenance Schedule 驱动，API 进程不再起本地循环；
        # turns/恢复逻辑不迁移（请求生命周期）。
        from . import notifications
        from app.workflows.config import temporal_configured
        if notifications.scheduler_enabled() and not temporal_configured():
            self._scheduler_task = asyncio.create_task(
                self._scheduler_loop())

    async def _scheduler_loop(self) -> None:
        from . import notifications
        while True:
            try:
                await asyncio.to_thread(notifications.scheduler_tick)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass  # 单次 tick 失败不终止调度
            await asyncio.sleep(60)

    async def stop(self) -> None:
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            self._scheduler_task = None
        for turn in list(self._running.values()):
            self._finalize_interrupted(turn)
        await asyncio.gather(
            *(t.task for t in self._running.values() if t.task),
            return_exceptions=True)

    def _recover_interrupted(self) -> None:
        """启动扫描：在途轮标记 interrupted；不重放、不重新执行。"""
        from app.core.guest_runtime import is_legacy_guest_owner
        from app.persistence.documents import tenant_scope

        for tenant, owner, record in store.scan_conversation_records():
            if is_legacy_guest_owner(owner):
                continue
            changed = False
            for turn_id, turn in (record.get("turns") or {}).items():
                if turn.get("state") == "running":
                    turn["state"] = "interrupted"
                    turn["updated_at"] = store.utc_now_iso()
                    changed = True
                    for message in record.get("messages") or []:
                        if (message.get("turn_id") == turn_id
                                and message.get("status") == "streaming"):
                            message["status"] = "interrupted"
            if changed:
                try:
                    with tenant_scope(tenant):
                        store.save_conversation(owner, record)
                except AssistantStoreError:
                    pass

    def _finalize_interrupted(self, turn: _RunningTurn) -> None:
        if turn.task and not turn.task.done():
            turn.task.cancel()

    # -- owner generation（账号清理联动） ----------------------------------

    def cancel_user_turns(self, student_id: str) -> None:
        self._owner_generation[student_id] = (
            self._owner_generation.get(student_id, 0) + 1)
        for turn in list(self._running.values()):
            if turn.student_id != student_id:
                continue
            turn.cancel_requested = True
            if turn.task and not turn.task.done():
                turn.task.cancel()

    def owner_generation(self, student_id: str) -> int:
        return self._owner_generation.get(student_id, 0)

    def _check_generation(self, turn: _RunningTurn) -> bool:
        """迟到写入检查：generation 已提升则放弃持久化。"""
        return self.owner_generation(turn.student_id) == turn.gen

    # -- turn acceptance ----------------------------------------------------

    def is_live(self, turn: _RunningTurn) -> bool:
        info = (turn.record.get("turns") or {}).get(turn.turn_id, {})
        if info.get("state") != "running":
            return False
        return not (turn.task and turn.task.done())

    def active_turn_of(self, student_id: str,
                       conversation_id: str) -> _RunningTurn | None:
        for turn in self._running.values():
            if (turn.student_id == student_id
                    and turn.conversation_id == conversation_id
                    and self.is_live(turn)):
                return turn
        return None

    def user_running_count(self, student_id: str) -> int:
        return sum(1 for t in self._running.values()
                   if t.student_id == student_id and self.is_live(t))

    def accept_turn(
        self, student_id: str, conversation_id: str,
        *, client_message_id: str, text: str,
        expected_conversation_revision: int,
    ) -> dict[str, Any]:
        """受理校验 + 持久化 running 受理信息。返回 turn 元数据。

        抛 TurnRejected：capacity/busy/concurrency/idempotency/revision。
        """
        record = store.load_conversation(student_id, conversation_id)
        if record is None:
            raise TurnRejected(404, "conversation_not_found", "会话不存在。")

        # 幂等：同 client_message_id 且相同正文 hash → 返回旧 turn。
        accepted = record.setdefault("accepted", {})
        hit = accepted.get(client_message_id)
        if hit is not None:
            if hit.get("body_hash") != body_hash(text):
                raise TurnRejected(
                    409, "idempotency_conflict",
                    "同一 client_message_id 已受理不同内容。")
            turn_state = (record.get("turns") or {}).get(
                hit.get("turn_id"), {})
            state = turn_state.get("state", "completed")
            return {"turn_id": hit.get("turn_id"), "state": state,
                    "duplicate": True,
                    "conversation_revision": record.get("revision", 1)}

        if int(record.get("revision", 1)) != int(expected_conversation_revision):
            active = None
            for turn in self._running.values():
                if (turn.student_id == student_id
                        and turn.conversation_id == conversation_id):
                    active = turn.turn_id
                    break
            raise TurnRejected(
                409, "conversation_changed", "会话已变化，请读取最新快照。",
                extra={
                    "latest_revision": record.get("revision", 1),
                    "active_turn_id": active,
                })

        if self.active_turn_of(student_id, conversation_id) is not None:
            raise TurnRejected(409, "conversation_busy", "本会话仍有进行中的一轮。")

        if self.user_running_count(student_id) >= MAX_TURNS_PER_USER:
            raise TurnRejected(
                429, "concurrency_limit",
                "同一账号最多两轮跨会话并发。", retryable=True)

        capacity = store.conversation_capacity(record)
        if not capacity["can_accept_turn"]:
            raise TurnRejected(
                409, "conversation_full",
                "本会话已达容量上限，请开启新对话继续。",
                extra={"reason": capacity["reason"]})

        # 受理：分配 user/assistant 两条消息位与 turn 记录，先落盘。
        turn_id = store.mint_turn_id()
        user_message_id = store.mint_message_id()
        assistant_message_id = store.mint_message_id()
        seq_base = len(record.get("messages") or [])
        now = store.utc_now_iso()
        scope = {"mode": "workspace", "workspace_ids": [],
                 "scope_revisions": {}}
        record["messages"].append({
            "message_id": user_message_id, "seq": seq_base + 1,
            "role": "user", "created_at": now, "turn_id": turn_id,
            "status": "complete", "scope": scope,
            "blocks": [{"block_id": "u1", "type": "markdown", "text": text}],
            "sources": [],
        })
        record["messages"].append({
            "message_id": assistant_message_id, "seq": seq_base + 2,
            "role": "assistant", "created_at": now, "turn_id": turn_id,
            "status": "streaming", "scope": scope,
            "blocks": [], "sources": [],
        })
        record["turns"][turn_id] = {
            "state": "running", "client_message_id": client_message_id,
            "cancel_requested": False, "created_at": now, "updated_at": now,
            "error": None,
        }
        record["accepted"][client_message_id] = {
            "turn_id": turn_id, "body_hash": body_hash(text)}
        record["revision"] = int(record.get("revision", 1)) + 1
        store.save_conversation(student_id, record)

        return {"turn_id": turn_id, "state": "running", "duplicate": False,
                "conversation_revision": record["revision"],
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id}

    def spawn(self, student_id: str, conversation_id: str, turn_id: str,
              request_payload: dict[str, Any]) -> _RunningTurn:
        record = store.load_conversation(student_id, conversation_id)
        assert record is not None
        turn = _RunningTurn(student_id, conversation_id, turn_id, record)
        turn.gen = self.owner_generation(student_id)
        turn.request = request_payload
        self._running[turn_id] = turn
        turn.task = asyncio.create_task(self._run(turn))
        return turn

    # -- events --------------------------------------------------------------

    async def emit(self, turn: _RunningTurn, event: dict[str, Any]) -> None:
        async with turn.lock:
            turn.last_event_seq += 1
            event["event_seq"] = turn.last_event_seq
            event["emitted_at"] = store.utc_now_iso()
            event["schema_version"] = 1
            event["turn_id"] = turn.turn_id
            turn.events.append(event)
            for queue in list(turn.subscribers):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    async def subscribe(self, turn: _RunningTurn, after_seq: int):
        """同一临界区取快照游标并接入后续事件（§11.5）。"""
        async with turn.lock:
            replay = [e for e in turn.events if e["event_seq"] > after_seq]
            queue: asyncio.Queue = asyncio.Queue(maxsize=256)
            turn.subscribers.add(queue)
        return replay, queue

    def unsubscribe(self, turn: _RunningTurn, queue: asyncio.Queue) -> None:
        turn.subscribers.discard(queue)

    def get_running(self, turn_id: str) -> _RunningTurn | None:
        turn = self._running.get(turn_id)
        return turn

    def snapshot_for(self, student_id: str, turn_id: str) -> dict[str, Any] | None:
        """运行态查询：完整快照由 API 层从会话记录组装。"""
        turn = self._running.get(turn_id)
        if turn is not None and turn.student_id == student_id:
            return {"state": "running",
                    "conversation_id": turn.conversation_id}
        return None

    # -- persistence during streaming ----------------------------------------

    async def persist_stream_snapshot(self, turn: _RunningTurn) -> None:
        """流式中最多每 500ms 保存一次文本快照（§12.1）。"""
        now = time.monotonic()
        if now - turn.last_snapshot_write < _STREAM_SNAPSHOT_INTERVAL:
            return
        turn.last_snapshot_write = now
        await asyncio.to_thread(self._save_record, turn)

    def _save_record(self, turn: _RunningTurn,
                     bump_revision: bool = False) -> None:
        if not self._check_generation(turn):
            return
        if bump_revision:
            turn.record["revision"] = int(
                turn.record.get("revision", 1)) + 1
        try:
            store.save_conversation(turn.student_id, turn.record)
        except AssistantStoreError:
            pass  # 存储失败按 storage_unavailable 由终态回报

    # -- turn lifecycle -------------------------------------------------------

    async def _run(self, turn: _RunningTurn) -> None:
        from . import service
        try:
            await service.execute_turn(self, turn)
            state = (turn.record["turns"][turn.turn_id].get("state")
                     or "completed")
        except asyncio.CancelledError:
            state = self._settle_cancel(turn)
        except Exception as exc:  # noqa: BLE001
            state = self._settle_failure(turn, exc)
        finally:
            await self.emit(turn, {"event": "turn_done",
                                   "state": state,
                                   "conversation_revision":
                                       turn.record.get("revision", 1)})
            await asyncio.to_thread(self._save_record, turn, False)
            _terminal_since[turn.turn_id] = time.monotonic()
            # 终态后事件环保留 10 分钟，由清理任务回收。
            try:
                loop = asyncio.get_running_loop()
                loop.call_later(_TERMINAL_RETENTION_SECONDS,
                                self._reap, turn.turn_id)
            except RuntimeError:
                pass

    def _settle_cancel(self, turn: _RunningTurn) -> str:
        info = turn.record["turns"][turn.turn_id]
        if info.get("state") in TERMINAL_STATES:
            return info["state"]
        info["state"] = "cancelled"
        info["updated_at"] = store.utc_now_iso()
        for message in turn.record.get("messages") or []:
            if (message.get("turn_id") == turn.turn_id
                    and message.get("status") == "streaming"):
                message["status"] = "cancelled"
        # §11.6：cancel 同时取消该轮尚未 execute 的 proposed 动作。
        for action in (turn.record.get("actions") or {}).values():
            if action.get("turn_id") == turn.turn_id and action.get(
                    "state") in ("proposed",):
                action["state"] = "cancelled"
        turn.record["revision"] = int(turn.record.get("revision", 1)) + 1
        return "cancelled"

    def _settle_failure(self, turn: _RunningTurn, exc: Exception) -> str:
        info = turn.record["turns"][turn.turn_id]
        info["state"] = "failed"
        info["updated_at"] = store.utc_now_iso()
        info["error"] = {
            "code": "internal_error",
            "message": "回答生成失败，请稍后重试。",
            "retryable": True,
            "request_id": f"req_{turn.turn_id[5:17]}",
        }
        for message in turn.record.get("messages") or []:
            if (message.get("turn_id") == turn.turn_id
                    and message.get("status") == "streaming"):
                message["status"] = "failed"
        turn.record["revision"] = int(turn.record.get("revision", 1)) + 1
        return "failed"

    async def request_cancel(self, student_id: str, turn_id: str) -> dict[str, Any]:
        record_turn = None
        turn = self._running.get(turn_id)
        if turn is not None and turn.student_id == student_id:
            turn.cancel_requested = True
            info = turn.record["turns"].get(turn_id, {})
            info["cancel_requested"] = True
            await asyncio.to_thread(self._save_record, turn)
            record_turn = info
            # §11.6：模型流尽快中止（已发生的业务动作不回滚）。
            if turn.task and not turn.task.done():
                turn.task.cancel()
        else:
            # 已终结或重启后：从存储读取（可重复请求）。
            conversation_id = self._find_conversation_of(student_id, turn_id)
            if conversation_id:
                record = store.load_conversation(student_id, conversation_id)
                if record:
                    record_turn = (record.get("turns") or {}).get(turn_id)
        if record_turn is None:
            raise TurnRejected(404, "entity_not_found", "该轮不存在。")
        return {"turn_id": turn_id,
                "state": record_turn.get("state", "completed"),
                "cancel_requested": True}

    def _find_conversation_of(self, student_id: str,
                              turn_id: str) -> str | None:
        items, _total = store.list_conversations(student_id, limit=100)
        for summary in items:
            cid = summary.get("conversation_id")
            record = store.load_conversation(student_id, cid)
            if record and turn_id in (record.get("turns") or {}):
                return cid
        return None

    def _reap(self, turn_id: str) -> None:
        turn = self._running.get(turn_id)
        if turn is None:
            _terminal_since.pop(turn_id, None)
            return
        if turn.task and not turn.task.done():
            return
        if not turn.subscribers:
            self._running.pop(turn_id, None)
            _terminal_since.pop(turn_id, None)
        else:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.call_later(_TERMINAL_RETENTION_SECONDS,
                            self._reap, turn_id)


def get_runtime() -> AssistantRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = AssistantRuntime()
    return _RUNTIME


def reset_runtime() -> None:
    """测试隔离：丢弃单例。"""
    global _RUNTIME
    _RUNTIME = None
