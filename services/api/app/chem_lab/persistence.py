"""Chem-lab session persistence: owner-isolated atomic docs + append-only logs.

Layout::

    <NEXT_TUTOR_DATA_DIR>/chem_lab/<owner>/
      sessions/<session_id>.json    # 最近快照和活动状态
      events/<session_id>.jsonl     # 追加语义事件日志（有上限）
      checkpoints/<session_id>.json # 可恢复分支点
      index.json                    # owner-scoped 摘要（扫描派生，非权威）

All writes go through core.atomic; file keys are sanitized. The owner epoch
marker (``.epochs/``) is the cross-process tombstone that keeps late writes
from resurrecting a purged owner directory — same argument as the
illustration store. The engine never imports this module.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from app.core import paths
from app.core.atomic import append_line_sync, atomic_write_text, file_lock

from .errors import ChemLabError

_CHEM_LAB_DIR = paths.bind_storage_path(__name__, "_CHEM_LAB_DIR", "chem_lab")

# Owner epoch 标记目录。名字以 "." 开头——safe() 的首字符规则使任何 owner
# 键都不可能产生这个路径，该目录也天然被 owner 扫描/孤儿清理跳过。
_EPOCHS_DIRNAME = ".epochs"
# 标记损坏时的 fail-closed 返回值：保证迟到写全部被 epoch 闸拒绝。
_EPOCH_INVALID = 2**31

MAX_SESSION_EVENTS = 2000  # 与引擎 MAX_EVENTS_PER_SESSION 对齐


def safe(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}", value) or ".." in value:
        raise ValueError("invalid_chem_lab_key")
    return value


def owner_dir(owner: str) -> Path:
    return _CHEM_LAB_DIR / safe(owner)


def lock(owner: str):
    """Serialize all read-modify-write cycles for one owner."""
    return file_lock(owner_dir(owner))


def _epochs_dir() -> Path:
    # 按次计算：测试沙箱会整体重绑 _CHEM_LAB_DIR。
    return _CHEM_LAB_DIR / _EPOCHS_DIRNAME


def _epoch_marker(owner: str) -> Path:
    return _epochs_dir() / f"{safe(owner)}.json"


def epoch(owner: str) -> int:
    """Owner epoch（0 = 无删除历史）；损坏时 fail-closed 拒绝迟到写。"""
    try:
        return int(json.loads(_epoch_marker(owner).read_text("utf-8"))["epoch"])
    except FileNotFoundError:
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        return _EPOCH_INVALID


def _bump_epoch(owner: str) -> int:
    """失效该 owner 的全部在途写（purge 专用；标记跨 rmtree 保留）。"""
    value = epoch(owner) + 1
    marker = _epoch_marker(owner)
    marker.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(marker, json.dumps({"owner": owner, "epoch": value}))
    return value


def _read_json(path: Path) -> dict | None:
    try:
        result = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None
    return result if isinstance(result, dict) else None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def read_session(owner: str, session_id: str) -> dict | None:
    return _read_json(owner_dir(owner) / "sessions" / f"{safe(session_id)}.json")


def write_session(owner: str, session_id: str, doc: dict, *, expected_epoch: int | None = None) -> None:
    safe(session_id)
    if expected_epoch is not None and epoch(owner) != expected_epoch:
        raise ChemLabError("session_missing", "会话已不存在（账户数据已清除）", status=404)
    path = owner_dir(owner) / "sessions" / f"{safe(session_id)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(doc, ensure_ascii=False, sort_keys=True, allow_nan=False))
    if expected_epoch is not None and epoch(owner) != expected_epoch:
        # 跨进程删除竞态：epoch 在检查后、落盘前失效——补偿删除，绝不让
        # 迟到写复活已清除的 owner 目录。
        path.unlink(missing_ok=True)
        raise ChemLabError("session_missing", "会话已不存在（账户数据已清除）", status=404)


def list_sessions(owner: str) -> list[dict]:
    base = owner_dir(owner) / "sessions"
    rows: list[dict] = []
    if not base.is_dir():
        return rows
    for path in sorted(base.glob("*.json")):
        row = _read_json(path)
        if row is not None:
            rows.append(row)
    return rows


def delete_session_files(owner: str, session_id: str) -> None:
    """删除派生文件并留下 sessions 墓碑（迟到命令不能复活会话）。"""
    safe(session_id)
    root = owner_dir(owner)
    (root / "events" / f"{session_id}.jsonl").unlink(missing_ok=True)
    (root / "checkpoints" / f"{session_id}.json").unlink(missing_ok=True)
    tombstone = {
        "session_id": session_id,
        "owner": safe(owner),
        "deleted": True,
        "deleted_at": time.time(),
    }
    write_session(owner, session_id, tombstone)


# ---------------------------------------------------------------------------
# Event log (append-only JSONL, capped)
# ---------------------------------------------------------------------------

def events_path(owner: str, session_id: str) -> Path:
    return owner_dir(owner) / "events" / f"{safe(session_id)}.jsonl"


def append_events(owner: str, session_id: str, events: list[dict]) -> int:
    """Append events; returns the total line count after the append."""
    if not events:
        return count_events(owner, session_id)
    path = events_path(owner, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    for event in events:
        append_line_sync(path, json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return count_events(owner, session_id)


def read_events(owner: str, session_id: str, *, after_seq: int = 0,
                max_events: int = 200, max_bytes: int = 262_144) -> list[dict]:
    path = events_path(owner, session_id)
    out: list[dict] = []
    try:
        stream = path.open("r", encoding="utf-8")
    except (FileNotFoundError, OSError):
        return out
    size = 0
    with stream:
        for line in stream:
            if len(out) >= max_events or size >= max_bytes:
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if int(event.get("seq", 0)) <= after_seq:
                continue
            size += len(line)
            out.append(event)
    return out


def count_events(owner: str, session_id: str) -> int:
    path = events_path(owner, session_id)
    try:
        with path.open("r", encoding="utf-8") as stream:
            return sum(1 for _ in stream)
    except (FileNotFoundError, OSError):
        return 0


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

def read_checkpoints(owner: str, session_id: str) -> dict:
    doc = _read_json(owner_dir(owner) / "checkpoints" / f"{safe(session_id)}.json")
    if doc is None:
        return {"session_id": safe(session_id), "items": []}
    doc.setdefault("items", [])
    return doc


def write_checkpoints(owner: str, session_id: str, doc: dict) -> None:
    path = owner_dir(owner) / "checkpoints" / f"{safe(session_id)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(doc, ensure_ascii=False, sort_keys=True, allow_nan=False))


# ---------------------------------------------------------------------------
# Owner index (derived summary; never authoritative)
# ---------------------------------------------------------------------------

def write_index(owner: str, items: list[dict]) -> None:
    path = owner_dir(owner) / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"owner": safe(owner), "items": items, "updated_at": time.time()}
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False))


# ---------------------------------------------------------------------------
# Purge / orphan scan
# ---------------------------------------------------------------------------

def iter_owners() -> list[str]:
    root = _CHEM_LAB_DIR
    if not root.is_dir():
        return []
    return [path.name for path in sorted(root.iterdir())
            if path.is_dir() and not path.name.startswith(".")]


def purge(owner: str) -> None:
    root = owner_dir(owner)
    with file_lock(root):
        _bump_epoch(owner)
        shutil.rmtree(root, ignore_errors=True)
