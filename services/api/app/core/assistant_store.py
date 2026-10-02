"""站内学习助手私有存储（plan.md §12.1）。

布局：``chat_history/assistant/<student_id>/`` 下
  conversations/<conversation_id>.json   会话/轮/动作唯一事实源
  drafts/<draft_id>.json                 交接草稿（默认 30 分钟有效）
  index.json                             可重建的列表索引 + 受理键索引
  references.json                        可重建的来源→消息反向索引
  invalidations.json                     来源失效标记与 owner_generation

纪律（§12.1）：
- 会话 JSON 是唯一事实源；index/references 可重建，不作为授权依据。
- 全部落盘走 core.atomic 的 file_lock + atomic_write_text；锁内绝不做
  网络/模型调用。
- ID 由服务端铸造（astc_/astt_/astm_/asta_/astd_/astx_/asts_ + UUID4 hex）。
- 容量：每会话 200 条消息或 2 MiB 先到为准；受理新轮前预留 2 条消息
  与 256 KiB 输出空间，超出返回 conversation_full。
- 测试隔离：storage_sandbox 已登记 _ASSISTANT_DIR；孤儿扫描已登记
  assistant 分类；账号清理在 core/account_data 中覆盖本根。
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text, file_lock

from app.core import paths

_ASSISTANT_DIR = paths.bind_storage_path(__name__, "_ASSISTANT_DIR", "assistant")

SCHEMA_VERSION = 1
MAX_MESSAGES_PER_CONVERSATION = 200
MAX_CONVERSATION_BYTES = 2 * 1024 * 1024
TURN_RESERVE_MESSAGES = 2
TURN_RESERVE_BYTES = 256 * 1024
DRAFT_TTL_SECONDS = 1800
ACTION_TTL_SECONDS = 600


class AssistantStoreError(Exception):
    """存储读写失败（≠不存在）。调用方映射为 storage_unavailable。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# ID 铸造（§19.1：服务端生成，客户端不可指定）
# ---------------------------------------------------------------------------

def mint_conversation_id() -> str:
    return f"astc_{uuid.uuid4().hex}"


def mint_turn_id() -> str:
    return f"astt_{uuid.uuid4().hex}"


def mint_message_id() -> str:
    return f"astm_{uuid.uuid4().hex}"


def mint_action_id() -> str:
    return f"asta_{uuid.uuid4().hex}"


def mint_draft_id() -> str:
    return f"astd_{uuid.uuid4().hex}"


def mint_command_id() -> str:
    return f"astx_{uuid.uuid4().hex}"


def mint_source_ref_id() -> str:
    return f"asts_{uuid.uuid4().hex}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 路径（裸 ID 构造，防穿越）
# ---------------------------------------------------------------------------

def _safe_key(student_id: str) -> str:
    return Path(student_id).name


def _student_root(student_id: str) -> Path:
    return _ASSISTANT_DIR / _safe_key(student_id)


def _conversations_dir(student_id: str) -> Path:
    return _student_root(student_id) / "conversations"


def _drafts_dir(student_id: str) -> Path:
    return _student_root(student_id) / "drafts"


def _conversation_path(student_id: str, conversation_id: str) -> Path:
    return _conversations_dir(student_id) / f"{Path(conversation_id).name}.json"


def _draft_path(student_id: str, draft_id: str) -> Path:
    return _drafts_dir(student_id) / f"{Path(draft_id).name}.json"


def _index_path(student_id: str) -> Path:
    return _student_root(student_id) / "index.json"


def _references_path(student_id: str) -> Path:
    return _student_root(student_id) / "references.json"


def _invalidations_path(student_id: str) -> Path:
    return _student_root(student_id) / "invalidations.json"


def _preferences_path(student_id: str) -> Path:
    return _student_root(student_id) / "preferences.json"


# §24.6 AssistantPreferences 白名单与默认值（保存于用户 profile 的
# prefs.assistant 分支；不覆盖其他 prefs）。
_PREFS_DEFAULTS: dict[str, Any] = {
    "response_length": "standard",
    "tone": "neutral",
    "default_scope": "follow_page",
    "proactive_enabled": False,
    "voice_input_mode": "hold",
    "send_after_recording": False,
    "auto_read": False,
    "voice_policy": "auto",
    "voice_id": None,
    "allow_local_fallback": True,
    "playback_rate": 1,
    "volume": 0.8,
}
_PREFS_KEYS = frozenset(_PREFS_DEFAULTS) | {"revision"}


def load_preferences(student_id: str) -> dict[str, Any]:
    """读取用户 profile.prefs.assistant（缺省回 §24.6 默认值）。

    与 client UI prefs（主题/字号/语言，useUIStore 本地）作用范围不同；
    本分支只承载助手回答风格与语音偏好。
    """
    prefs: dict[str, Any] = {}
    try:
        from app.identity import store as id_store
        user = id_store.get_by_id(student_id)
        if user is not None:
            raw = (user.profile.prefs or {}).get("assistant")
            if isinstance(raw, dict):
                prefs = raw
    except Exception:
        prefs = {}
    out = dict(_PREFS_DEFAULTS)
    for key in _PREFS_DEFAULTS:
        if prefs.get(key) is not None:
            out[key] = prefs[key]
    out["revision"] = int(prefs.get("revision") or 1)
    return out


def save_preferences(student_id: str, prefs: dict[str, Any],
                     *, base_revision: int | None = None) -> dict[str, Any]:
    """局部更新 + 版本合并（§24.6）：只写白名单键，revision+1。

    base_revision 与当前不一致时抛 AssistantStoreError("conflict")，
    由调用方以 409 提示客户端先读取再合并；不覆盖其他 prefs 分支。
    """
    from app.identity import store as id_store
    from app.identity.store import update_profile_fields
    current = load_preferences(student_id)
    if base_revision is not None \
            and int(base_revision) != int(current.get("revision") or 1):
        raise AssistantStoreError(
            "conflict", "助手偏好已被其他端修改，请刷新后重试。")
    merged = dict(current)
    for key in _PREFS_DEFAULTS:
        if prefs.get(key) is not None:
            merged[key] = prefs[key]
    merged["revision"] = int(current.get("revision") or 1) + 1
    payload = {k: v for k, v in merged.items() if k != "updated_at"}
    try:
        update_profile_fields(student_id, {}, {"assistant": payload})
    except ValueError as exc:
        raise AssistantStoreError("not_found", str(exc)) from exc
    return merged


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AssistantStoreError(
            "corrupt", f"存储文件损坏: {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise AssistantStoreError("corrupt", f"存储文件结构非法: {path.name}")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        atomic_write_text(path, json.dumps(data, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

def _summary_of(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "conversation_id": record["conversation_id"],
        "title": record.get("title") or "新对话",
        "revision": int(record.get("revision", 1)),
        "created_at": record.get("created_at", ""),
        "updated_at": record.get("updated_at", ""),
        "message_count": len(record.get("messages") or []),
    }


def _load_index(student_id: str) -> dict[str, Any]:
    data = _read_json(_index_path(student_id))
    if data is None:
        return rebuild_index(student_id)
    return data


def rebuild_index(student_id: str) -> dict[str, Any]:
    """从会话文件重建索引（索引是投影，可随时重建）。"""
    index: dict[str, Any] = {"version": SCHEMA_VERSION,
                             "conversations": [], "request_index": {}}
    conv_dir = _conversations_dir(student_id)
    if conv_dir.is_dir():
        for path in sorted(conv_dir.glob("*.json")):
            try:
                record = _read_json(path)
            except AssistantStoreError:
                continue  # 单个坏会话不阻塞索引重建
            if record is None:
                continue
            index["conversations"].append(_summary_of(record))
            for key, turn in (record.get("accepted") or {}).items():
                index["request_index"][key] = record["conversation_id"]
    index["conversations"].sort(key=lambda s: s.get("updated_at", ""),
                                reverse=True)
    _write_json(_index_path(student_id), index)
    return index


def create_conversation(
    student_id: str, *, client_request_id: str, title: str | None = None,
) -> dict[str, Any]:
    """创建会话；client_request_id 幂等（重复返回同一会话）。"""
    root = _student_root(student_id)
    root.mkdir(parents=True, exist_ok=True)
    index_path = _index_path(student_id)
    with file_lock(index_path):
        index = _load_index(student_id)
        existing = index.get("request_index", {}).get(client_request_id)
        if existing:
            record = load_conversation(student_id, existing)
            if record is not None:
                return record
        conversation_id = mint_conversation_id()
        now = utc_now_iso()
        record = {
            "schema_version": SCHEMA_VERSION,
            "conversation_id": conversation_id,
            "title": (title or "").strip()[:120] or "新对话",
            "revision": 1,
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "turns": {},
            "actions": {},
            "accepted": {},
        }
        _write_json(_conversation_path(student_id, conversation_id), record)
        index["conversations"].insert(0, _summary_of(record))
        index["request_index"][client_request_id] = conversation_id
        _write_json(index_path, index)
        return record


def load_conversation(student_id: str, conversation_id: str) -> dict[str, Any] | None:
    """读取会话；不存在返回 None，损坏抛 AssistantStoreError。"""
    return _read_json(_conversation_path(student_id, conversation_id))


def save_conversation(student_id: str, record: dict[str, Any]) -> None:
    """原子保存会话并同步索引摘要。"""
    record["updated_at"] = utc_now_iso()
    _write_json(_conversation_path(student_id, record["conversation_id"]),
                record)
    index_path = _index_path(student_id)
    with file_lock(index_path):
        index = _load_index(student_id)
        summary = _summary_of(record)
        conversations = index.get("conversations") or []
        for i, item in enumerate(conversations):
            if item.get("conversation_id") == record["conversation_id"]:
                conversations[i] = summary
                break
        else:
            conversations.insert(0, summary)
        conversations.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        index["conversations"] = conversations[:500]
        _write_json(index_path, index)


def list_conversations(
    student_id: str, *, offset: int = 0, limit: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    items = _load_index(student_id).get("conversations") or []
    total = len(items)
    return items[offset:offset + limit], total


def delete_conversation(student_id: str, conversation_id: str) -> bool:
    """删除会话及其关联草稿与索引/引用条目；幂等（不存在也返回 True）。"""
    conv_path = _conversation_path(student_id, conversation_id)
    record = None
    if conv_path.exists():
        try:
            record = _read_json(conv_path)
        except AssistantStoreError:
            record = None
    # 关联草稿按 conversation_id 找（草稿知道自己属于哪个会话）
    drafts_dir = _drafts_dir(student_id)
    if drafts_dir.is_dir():
        for path in drafts_dir.glob("*.json"):
            try:
                draft = _read_json(path)
            except AssistantStoreError:
                continue
            if draft and draft.get("conversation_id") == conversation_id:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
    try:
        conv_path.unlink(missing_ok=True)
    except OSError as exc:
        raise AssistantStoreError("io_error", f"删除会话失败: {exc}") from exc
    index_path = _index_path(student_id)
    with file_lock(index_path):
        index = _load_index(student_id)
        index["conversations"] = [
            s for s in index.get("conversations") or []
            if s.get("conversation_id") != conversation_id]
        index["request_index"] = {
            k: v for k, v in (index.get("request_index") or {}).items()
            if v != conversation_id}
        _write_json(index_path, index)
    if record is not None:
        try:
            drop_references_for_conversation(student_id, conversation_id)
        except AssistantStoreError:
            pass  # 反向索引可重建，失败不阻塞删除
    return True


def conversation_capacity(record: dict[str, Any]) -> dict[str, Any]:
    """容量检查（§12.1：200 条消息 / 2 MiB，先到为准）。"""
    message_count = len(record.get("messages") or [])
    size = len(json.dumps(record, ensure_ascii=False).encode("utf-8"))
    messages_ok = (message_count + TURN_RESERVE_MESSAGES
                   <= MAX_MESSAGES_PER_CONVERSATION)
    bytes_ok = (size + TURN_RESERVE_BYTES <= MAX_CONVERSATION_BYTES)
    return {
        "message_count": message_count,
        "size_bytes": size,
        "can_accept_turn": bool(messages_ok and bytes_ok),
        "reason": None if (messages_ok and bytes_ok) else (
            "message_limit" if not messages_ok else "size_limit"),
    }


# ---------------------------------------------------------------------------
# 草稿
# ---------------------------------------------------------------------------

def create_draft(student_id: str, draft: dict[str, Any]) -> dict[str, Any]:
    draft_id = draft.get("draft_id") or mint_draft_id()
    now = datetime.now(timezone.utc)
    record = dict(draft)
    record.update({
        "draft_id": draft_id,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=DRAFT_TTL_SECONDS)).isoformat(),
        "consumed": False,
        "result_entity": draft.get("result_entity"),
    })
    _write_json(_draft_path(student_id, draft_id), record)
    return record


def load_draft(student_id: str, draft_id: str) -> dict[str, Any] | None:
    draft = _read_json(_draft_path(student_id, draft_id))
    if draft is None:
        return None
    expires_at = draft.get("expires_at", "")
    try:
        if expires_at and datetime.fromisoformat(expires_at) < (
                datetime.now(timezone.utc)):
            draft["expired"] = True
    except ValueError:
        draft["expired"] = True
    return draft


def consume_draft(
    student_id: str, draft_id: str, *,
    result_entity: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    path = _draft_path(student_id, draft_id)
    draft = _read_json(path)
    if draft is None:
        return None
    if draft.get("consumed"):
        return draft  # 幂等：重复消费返回同一记录
    with file_lock(path):
        draft["consumed"] = True
        if result_entity is not None:
            draft["result_entity"] = result_entity
        _write_json(path, draft)
    return draft


def delete_draft(student_id: str, draft_id: str) -> bool:
    try:
        _draft_path(student_id, draft_id).unlink(missing_ok=True)
        return True
    except OSError as exc:
        raise AssistantStoreError("io_error", f"删除草稿失败: {exc}") from exc


def purge_expired_drafts(student_id: str) -> int:
    """删除已过期草稿；无文件时不创建目录。返回删除数。"""
    drafts_dir = _drafts_dir(student_id)
    if not drafts_dir.is_dir():
        return 0
    removed = 0
    now = datetime.now(timezone.utc)
    for path in drafts_dir.glob("*.json"):
        try:
            draft = _read_json(path)
        except AssistantStoreError:
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
            continue
        if draft is None:
            continue
        expires_at = str(draft.get("expires_at") or "")
        try:
            expired = (not expires_at
                       or datetime.fromisoformat(expires_at) < now)
        except ValueError:
            expired = True
        if expired:
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


# ---------------------------------------------------------------------------
# 来源反向索引与失效（§12.3-7/8/9/10）
# ---------------------------------------------------------------------------

def register_source_refs(student_id: str, entries: list[dict[str, Any]]) -> None:
    """登记消息级来源引用：origin (kind,id) → 会话/消息。可重建投影。"""
    if not entries:
        return
    path = _references_path(student_id)
    with file_lock(path):
        refs = _read_json(path) or {"version": SCHEMA_VERSION, "by_origin": {}}
        by_origin: dict[str, list[dict[str, Any]]] = refs.setdefault(
            "by_origin", {})
        for entry in entries:
            key = f"{entry.get('origin_kind')}:{entry.get('origin_id')}"
            bucket = by_origin.setdefault(key, [])
            if not any(item.get("source_id") == entry.get("source_id")
                       for item in bucket):
                bucket.append({
                    "source_id": entry.get("source_id"),
                    "conversation_id": entry.get("conversation_id"),
                    "message_id": entry.get("message_id"),
                    "revision": entry.get("revision"),
                    "registered_at": utc_now_iso(),
                })
        _write_json(path, refs)


def drop_references_for_conversation(student_id: str, conversation_id: str) -> None:
    path = _references_path(student_id)
    if not path.exists():
        return
    with file_lock(path):
        refs = _read_json(path) or {"version": SCHEMA_VERSION, "by_origin": {}}
        by_origin = refs.setdefault("by_origin", {})
        for key in list(by_origin.keys()):
            by_origin[key] = [item for item in by_origin[key]
                              if item.get("conversation_id") != conversation_id]
            if not by_origin[key]:
                del by_origin[key]
        _write_json(path, refs)


def lookup_origin_refs(
    student_id: str, origin_kind: str, origin_id: str,
) -> list[dict[str, Any]]:
    path = _references_path(student_id)
    if not path.exists():
        return []
    refs = _read_json(path) or {}
    return list((refs.get("by_origin") or {}).get(
        f"{origin_kind}:{origin_id}", []))


def mark_origin_invalidated(
    student_id: str, origin_kind: str, origin_id: str,
) -> dict[str, Any]:
    """登记来源永久失效标记（先标记，后续清理派生正文；§12.3-10）。"""
    path = _invalidations_path(student_id)
    with file_lock(path):
        state = _read_json(path) or {
            "version": SCHEMA_VERSION, "owner_generation": 1,
            "source_invalidations": [], "pending_cleanups": []}
        marks = state.setdefault("source_invalidations", [])
        if not any(m.get("origin_kind") == origin_kind
                   and m.get("origin_id") == origin_id for m in marks):
            marks.append({"origin_kind": origin_kind, "origin_id": origin_id,
                          "marked_at": utc_now_iso()})
        _write_json(path, state)
        return state


def is_origin_invalidated(
    student_id: str, origin_kind: str, origin_id: str,
) -> bool:
    path = _invalidations_path(student_id)
    if not path.exists():
        return False
    state = _read_json(path) or {}
    return any(m.get("origin_kind") == origin_kind
               and m.get("origin_id") == origin_id
               for m in state.get("source_invalidations") or [])


# ---------------------------------------------------------------------------
# owner generation（§12.3-11：账号清理后旧任务不得回写）
# ---------------------------------------------------------------------------

def bump_owner_generation(student_id: str) -> int:
    path = _invalidations_path(student_id)
    with file_lock(path):
        state = _read_json(path) or {
            "version": SCHEMA_VERSION, "owner_generation": 1,
            "source_invalidations": [], "pending_cleanups": []}
        state["owner_generation"] = int(state.get("owner_generation", 1)) + 1
        _write_json(path, state)
        return state["owner_generation"]


def current_owner_generation(student_id: str) -> int:
    path = _invalidations_path(student_id)
    if not path.exists():
        return 1
    state = _read_json(path) or {}
    return int(state.get("owner_generation", 1))


# ---------------------------------------------------------------------------
# 账号清理入口（core/account_data 调用）
# ---------------------------------------------------------------------------

def assistant_storage_size(student_id: str) -> int:
    root = _student_root(student_id)
    if not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def purge_assistant_data(student_id: str) -> int:
    """删除该账号的全部助手数据；返回释放字节数。无数据不建目录。"""
    root = _student_root(student_id)
    if not root.exists():
        return 0
    size = assistant_storage_size(student_id)
    shutil.rmtree(root, ignore_errors=True)
    return size


def stop_assistant_tasks(student_id: str) -> None:
    """账号清理前停止在途助手轮任务（A05 runtime 注册后生效）。"""
    try:
        from app.agents.site_assistant import runtime as _runtime  # noqa: F401
        _runtime.cancel_user_turns(student_id)
    except Exception:
        pass
