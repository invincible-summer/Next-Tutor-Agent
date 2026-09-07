"""Textbook registry (P2): 一等公民「教材」的注册记录 + CRUD。

教材 = Library 文件（kind="textbook"）+ Textbook 注册记录 + M5.7 图谱，三者以
``file_id`` / ``topic_key`` 双向链接。本模块只负责**注册记录**（状态机/进度/
章节概念数/warnings），不持有教材文本（文本在 Library）也不持有图谱（图谱在
M5.7 store）。Library 文件、向量索引、M5.7 图谱的清理由 api/v1/textbook.py 的
级联删除协调，本模块只管记录本身的增删改查 + 给 preamble 反查（textbook_for_file）。

存储（镜像 core/library.py 规范）：
  chat_history/library/<student_key>.textbooks.json   注册记录数组
原子写 + file_lock + ``_key()`` 剥目录防遍历，腐坏文件按空处理不崩。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LIBRARY_DIR = _PROJECT_ROOT / "chat_history" / "library"
_MAX_TITLE = 120
_MAX_WARNINGS = 10

#: 公用教材库命名空间（P6-B2）：公用教材的 Library 文件/注册记录/图谱全部
#: 落在该保留学生键下；所有账号可读可选用，仅管理员可写（API 层强制）。
#: 真实用户 id 是 usr_<uuid>，不会与之冲突。
PUBLIC_STUDENT_ID = "public"

#: 教材归属范围：private（个人，默认）/ public（公用教材库，仅管理员可写）
SCOPES = ("private", "public")

#: 记录形态：single（单 PDF 教材，默认）/ group（教材组——多卷合一图谱）。
#: 旧记录无 kind 字段，一律视为 single（零迁移）。
KINDS = ("single", "group")

# 教材图谱构建状态机：
#   building    后台构建中（progress 追踪 stage/done/total）
#   ready       构建完成（chapter_count/concept_count 已填，或 GRAPH_ENABLED=0 直接 ready）
#   graph_failed 图谱构建失败（教材仍可检索；可 rebuild_graph 重试）
#   failed      严重失败（解析/索引阶段，教材不可用）
STATUSES = ("building", "ocr_waiting", "ocr_paused", "ready", "partial",
            "graph_failed", "failed")

# progress.stage 取值
STAGES = ("parse", "ocr", "ocr_waiting", "ocr_paused", "index", "skeleton", "chapters", "merge")


def _default_student_id() -> str:
    from ..agents.student_model.store import DEFAULT_STUDENT_ID
    return DEFAULT_STUDENT_ID


def _key(student_id: str) -> str:
    """Filesystem-safe student key (traversal guard); "" maps to the guest."""
    bare = Path(student_id or "").name
    return bare or _default_student_id()


def _index_path(student_id: str) -> Path:
    return _LIBRARY_DIR / f"{_key(student_id)}.textbooks.json"


def _new_id() -> str:
    return "tb_" + uuid.uuid4().hex[:10]


def _topic_key(textbook_id: str) -> str:
    """Stable, unique topic_key derived from the textbook id (杜绝同名碰撞).

    M5.7 store 按 topic_key 唯一；用 textbook_id 派生保证同名教材也不覆盖彼此。
    """
    return f"tb-{textbook_id}"


def _now() -> float:
    return time.time()


def _limit(value: Any) -> int | None:
    """Normalize a graph limit. ``None`` means unlimited; invalid values inherit."""
    if value in (None, "", 0, "0"):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def normalize_graph_policy(raw: Any, file_ids: list[str] | None = None) -> dict[str, Any]:
    """Return the persisted group-default + per-volume graph policy shape."""
    data = raw if isinstance(raw, dict) else {}
    overrides: dict[str, dict[str, int | None]] = {}
    allowed = set(file_ids or [])
    source = data.get("volume_overrides") if isinstance(data.get("volume_overrides"), dict) else {}
    for file_id, value in source.items():
        fid = str(file_id or "").strip()
        if not fid or (allowed and fid not in allowed) or not isinstance(value, dict):
            continue
        overrides[fid] = {
            "max_chapters": _limit(value.get("max_chapters")),
            "max_concepts": _limit(value.get("max_concepts")),
        }
    return {
        "default_max_chapters": _limit(data.get("default_max_chapters")),
        "default_max_concepts": _limit(data.get("default_max_concepts")),
        "volume_overrides": overrides,
    }


def effective_graph_limits(record: dict[str, Any], file_id: str) -> dict[str, int | None]:
    policy = normalize_graph_policy(record.get("graph_policy"), list(record.get("file_ids") or []))
    override = policy["volume_overrides"].get(file_id)
    if override is not None:
        return dict(override)
    return {
        "max_chapters": policy["default_max_chapters"],
        "max_concepts": policy["default_max_concepts"],
    }


def _sanitize_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Validate + lightly coerce a record loaded from disk. None if unusable."""
    if not isinstance(raw, dict):
        return None
    tb_id = str(raw.get("id") or "").strip()
    file_id = str(raw.get("file_id") or "").strip()
    kind = raw.get("kind") if raw.get("kind") in KINDS else "single"
    file_ids = [str(f) for f in (raw.get("file_ids") or []) if str(f).strip()]
    if not tb_id:
        return None
    if kind == "group":
        # 教材组：多卷合一图谱；file_id 置空，卷列表必须非空。
        if not file_ids:
            return None
        file_id = ""
    elif not file_id:
        return None
    rec: dict[str, Any] = {
        "id": tb_id,
        "kind": kind,
        "file_id": file_id,
        "file_ids": file_ids if kind == "group" else ([file_id] if file_id else []),
        "topic_key": str(raw.get("topic_key") or _topic_key(tb_id)).strip() or _topic_key(tb_id),
        "title": str(raw.get("title") or "未命名教材").strip()[:_MAX_TITLE] or "未命名教材",
        # group_name is the stable third-level M5 category. Legacy records
        # use title as a zero-migration fallback.
        "group_name": str(raw.get("group_name") or raw.get("title") or "未命名教材").strip()[:_MAX_TITLE] or "未命名教材",
        "group_note": str(raw.get("group_note") or "").strip()[:500],
        "subject": str(raw.get("subject") or "").strip()[:30],
        "level": str(raw.get("level") or "").strip(),
        "scope": raw.get("scope") if raw.get("scope") in SCOPES else "private",
        "status": raw.get("status") if raw.get("status") in STATUSES else "building",
        "progress": raw.get("progress") if isinstance(raw.get("progress"), dict)
                    else {"stage": "parse", "done": 0, "total": 1},
        "chapter_count": int(raw.get("chapter_count") or 0),
        "concept_count": int(raw.get("concept_count") or 0),
        "graph_policy": normalize_graph_policy(raw.get("graph_policy"),
                                                 file_ids if kind == "group" else [file_id]),
        "volumes": list(raw.get("volumes") or []),
        "needs_reextract": bool(raw.get("needs_reextract", False)),
        "ocr_pages": int(raw.get("ocr_pages") or 0),
        "ocr_state": dict(raw.get("ocr_state") or {})
                     if isinstance(raw.get("ocr_state"), dict) else {},
        "parse_cancel_requested": bool(raw.get("parse_cancel_requested", False)),
        "rag_index": dict(raw.get("rag_index") or {})
                     if isinstance(raw.get("rag_index"), dict) else {},
        "warnings": [str(w) for w in (raw.get("warnings") or [])][:_MAX_WARNINGS],
        "error": str(raw.get("error") or "")[:300],
        "created_at": float(raw.get("created_at") or _now()),
        "updated_at": float(raw.get("updated_at") or _now()),
    }
    return rec


# --- read ---

def load_textbooks(student_id: str) -> list[dict[str, Any]]:
    """All textbook records for a student ([] when nothing persisted yet)."""
    path = _index_path(student_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []  # corrupt: degrade to empty, never raise
    recs = data.get("textbooks", []) if isinstance(data, dict) else []
    out = [_sanitize_record(r) for r in recs if isinstance(r, dict)]
    return [r for r in out if r is not None]


def _load_raw(student_id: str) -> list[dict[str, Any]]:
    return load_textbooks(student_id)


def find_textbook(student_id: str, tb_id: str) -> dict[str, Any] | None:
    return next((t for t in load_textbooks(student_id) if t["id"] == tb_id), None)


def find_textbook_scoped(student_id: str, tb_id: str) -> tuple[dict[str, Any], str] | None:
    """自有优先、公用兜底的教材查找（P6-B2）。

    返回 (record, owner_student_id)；record 上带 scope。找不到返回 None。
    用于 API 层：公用教材所有账号可读/可选用，写操作由 API 另行鉴权。
    """
    own = find_textbook(student_id, tb_id)
    if own is not None:
        return own, student_id
    pub = find_textbook(PUBLIC_STUDENT_ID, tb_id)
    if pub is not None:
        return pub, PUBLIC_STUDENT_ID
    return None


def textbook_for_file(student_id: str, file_id: str) -> dict[str, Any] | None:
    """Reverse-lookup the textbook registered for a Library file_id.

    Used by the preamble builder (P3): given a session's merged knowledge files,
    find which ones are registered textbooks so [当前教材] can list them.
    Returns the first match (a file should belong to at most one textbook).
    教材组（kind=group）的任意卷反查命中组记录（[当前教材] 显示组名）。
    """
    return next((t for t in load_textbooks(student_id)
                 if t["file_id"] == file_id or file_id in t.get("file_ids", [])),
                None)


# --- write ---

def _save(student_id: str, records: list[dict[str, Any]]) -> None:
    _LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    path = _index_path(student_id)
    with file_lock(path):
        atomic_write_text(path, json.dumps(
            {"student_id": _key(student_id), "textbooks": records},
            ensure_ascii=False, indent=2))


def create_textbook(student_id: str, *, file_id: str, title: str,
                    subject: str = "", level: str = "",
                    scope: str = "private") -> dict[str, Any]:
    """Register a new textbook record in 'building' state. Idempotent on file_id:
    if a textbook already exists for this file_id, return it unchanged (re-upload
    of the same file should not create a duplicate record)."""
    existing = textbook_for_file(student_id, file_id)
    if existing is not None:
        return existing
    tb_id = _new_id()
    now = _now()
    rec = {
        "id": tb_id,
        "file_id": file_id,
        "topic_key": _topic_key(tb_id),
        "title": (title or "未命名教材").strip()[:_MAX_TITLE] or "未命名教材",
        "group_name": (title or "未命名教材").strip()[:_MAX_TITLE] or "未命名教材",
        "group_note": "",
        "subject": (subject or "").strip()[:30],
        "level": (level or "").strip(),
        "scope": scope if scope in SCOPES else "private",
        "status": "building",
        "progress": {"stage": "parse", "done": 0, "total": 1},
        "chapter_count": 0,
        "concept_count": 0,
        "graph_policy": normalize_graph_policy(None, [file_id]),
        "volumes": [],
        "ocr_pages": 0,
        "ocr_state": {},
        "rag_index": {},
        "warnings": [],
        "error": "",
        "created_at": now,
        "updated_at": now,
    }
    records = _load_raw(student_id)
    records.append(rec)
    _save(student_id, records)
    return rec


def create_group(student_id: str, *, file_ids: list[str], title: str,
                 subject: str = "", group_note: str = "",
                 level: str = "", scope: str = "private",
                 graph_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """注册教材组（多卷合一图谱）：多 PDF 编为一组，建一个统一知识谱系。

    id 复用 ``_new_id()``（tb_ 前缀 → topic_key=tb-tb_xxx，概念 id 前缀
    ``custom.tb-`` 与 single 一致，检索/预索引消费端零改动）。"""
    tb_id = _new_id()
    now = _now()
    rec = {
        "id": tb_id,
        "kind": "group",
        "file_id": "",
        "file_ids": [f for f in file_ids if f],
        "topic_key": _topic_key(tb_id),
        "title": (title or "未命名教材组").strip()[:_MAX_TITLE] or "未命名教材组",
        "group_name": (title or "未命名教材组").strip()[:_MAX_TITLE] or "未命名教材组",
        "group_note": (group_note or "").strip()[:500],
        "subject": (subject or "").strip()[:30],
        "level": (level or "").strip(),
        "scope": scope if scope in SCOPES else "private",
        "status": "building",
        "progress": {"stage": "parse", "done": 0, "total": 1},
        "chapter_count": 0,
        "concept_count": 0,
        "graph_policy": normalize_graph_policy(graph_policy, [f for f in file_ids if f]),
        "volumes": [],
        "ocr_pages": 0,
        "ocr_state": {},
        "rag_index": {},
        "warnings": [],
        "error": "",
        "created_at": now,
        "updated_at": now,
    }
    records = _load_raw(student_id)
    records.append(rec)
    _save(student_id, records)
    return rec


def add_group_files(student_id: str, group_id: str,
                    file_ids: list[str]) -> dict[str, Any] | None:
    """向教材组追加卷（有序、去重）；返回更新后记录或 None。"""
    records = _load_raw(student_id)
    updated = None
    for r in records:
        if r["id"] == group_id and r.get("kind") == "group":
            cur = list(r.get("file_ids") or [])
            for fid in file_ids:
                if fid and fid not in cur:
                    cur.append(fid)
            r["file_ids"] = cur
            r["graph_policy"] = normalize_graph_policy(r.get("graph_policy"), cur)
            r["updated_at"] = _now()
            updated = r
            break
    if updated is not None:
        _save(student_id, records)
    return updated


def remove_group_file(student_id: str, group_id: str,
                      file_id: str) -> dict[str, Any] | None:
    """从教材组移除一卷；返回更新后记录（file_ids 可能因此变空）或 None。"""
    records = _load_raw(student_id)
    updated = None
    for r in records:
        if r["id"] == group_id and r.get("kind") == "group":
            r["file_ids"] = [f for f in (r.get("file_ids") or []) if f != file_id]
            r["graph_policy"] = normalize_graph_policy(r.get("graph_policy"), r["file_ids"])
            r["updated_at"] = _now()
            updated = r
            break
    if updated is not None:
        _save(student_id, records)
    return updated


def update_textbook(student_id: str, tb_id: str, **fields) -> dict[str, Any] | None:
    """Patch one textbook record's fields (status/progress/counts/warnings/...).

    ``level``/``title``/``subject`` user edits also flow through here (PATCH
    endpoint). Returns the updated record, or None when not found. Never raises.
    """
    records = _load_raw(student_id)
    updated = None
    for r in records:
        if r["id"] == tb_id:
            for k, v in fields.items():
                if k == "warnings":
                    r[k] = [str(w) for w in (v or [])][:_MAX_WARNINGS]
                elif k in ("chapter_count", "concept_count"):
                    try:
                        r[k] = int(v or 0)
                    except (TypeError, ValueError):
                        pass
                elif k == "progress" and isinstance(v, dict):
                    r[k] = v
                elif k == "status":
                    r[k] = v if v in STATUSES else r["status"]
                elif k == "graph_policy":
                    r[k] = normalize_graph_policy(v, list(r.get("file_ids") or []))
                elif k == "volumes":
                    r[k] = list(v or [])
                elif k in ("title", "group_name", "subject", "group_note", "error"):
                    limit = {"title": _MAX_TITLE, "group_name": _MAX_TITLE,
                             "group_note": 500, "error": 300}.get(k, 30)
                    r[k] = str(v or "")[:limit]
                    if k in ("title", "group_name"):
                        # Keep the old title field/API compatible while the
                        # taxonomy consumes the explicit group_name field.
                        r["title"] = r[k]
                        r["group_name"] = r[k]
                elif k == "level":
                    r[k] = str(v or "").strip()
                else:
                    r[k] = v
            r["updated_at"] = _now()
            updated = r
            break
    if updated is not None:
        _save(student_id, records)
    return updated


def parse_cancelled(student_id: str, tb_id: str) -> bool:
    """Cooperative cancel flag: set by the cancel endpoint, observed by every
    build checkpoint (volume loop / chapter loop / per-page OCR settle)."""
    rec = find_textbook(student_id, tb_id)
    return bool(rec and rec.get("parse_cancel_requested"))


def settle_cancelled_parse(student_id: str, tb_id: str) -> str:
    """Settle a cancelled parse into a terminal status.

    ready when any volume still has usable extracted text (chunks stay
    searchable), otherwise failed. 不清 parse_cancel_requested——仍在跑的
    构建检查点还要观测它；标记由下一轮构建开始时统一清除。幂等；返回终态。
    """
    rec = find_textbook(student_id, tb_id)
    if rec is None:
        return "missing"
    has_text = False
    try:
        from .library import library_data_dir
        for fid in list(rec.get("file_ids") or []) + [rec.get("file_id") or ""]:
            if not fid:
                continue
            p = library_data_dir(student_id) / f"{fid}.txt"
            if p.exists() and p.stat().st_size > 50:
                has_text = True
                break
    except Exception:
        has_text = True  # 无法判定时保守按可用处理，避免误标 failed
    status = "ready" if has_text else "failed"
    updated = update_textbook(
        student_id, tb_id, status=status,
        progress={"stage": "merge", "done": 1, "total": 1},
        error="" if has_text else "解析已手动终止（无可用文本，可重新构建）")
    return str((updated or {}).get("status") or status)


def remove_textbook(student_id: str, tb_id: str) -> bool:
    """Remove a textbook record (does NOT touch the Library file / graph / vectors
    — the API layer coordinates cascade cleanup). Returns True if removed."""
    records = _load_raw(student_id)
    new = [r for r in records if r["id"] != tb_id]
    if len(new) == len(records):
        return False
    _save(student_id, new)
    return True


def reap_stale_builds() -> int:
    """启动收割（P5a-A4）：把残留的 building 记录置为 graph_failed。

    图谱构建是**进程内** asyncio 任务，随进程死亡——进程启动时不存在任何合法的
    building 状态，残留的必是上次崩溃/重启的孤儿。收割后用户可经 rebuild_graph
    重试。返回收割的记录条数。永不抛出（启动路径不容失败）。
    """
    reaped = 0
    try:
        files = list(_LIBRARY_DIR.glob("*.textbooks.json"))
    except Exception:
        return 0
    for fp in files:
        try:
            key = fp.name[: -len(".textbooks.json")]
            records = load_textbooks(key)
            changed = False
            for r in records:
                if r["status"] == "building":
                    volume_states = ((r.get("ocr_state") or {}).get("volumes") or {})
                    resumable = [v for v in volume_states.values()
                                 if isinstance(v, dict) and v.get("status") in {"ocr", "waiting"}
                                 and (v.get("pending_pages") or [])]
                    if resumable:
                        r["status"] = "ocr_waiting"
                        r["progress"] = {
                            "stage": "ocr_waiting",
                            "done": sum(len(v.get("successful_pages") or []) for v in resumable),
                            "total": sum(len(v.get("target_pages") or []) for v in resumable),
                        }
                        r["error"] = str(resumable[0].get("last_error_summary") or
                                         "服务重启后等待继续多模态 OCR")[:300]
                        r["updated_at"] = _now()
                        changed = True
                        continue
                    r["status"] = "graph_failed"
                    r["error"] = "服务重启导致图谱构建中断，可点击「重建图谱」重试"
                    r["updated_at"] = _now()
                    reaped += 1
                    changed = True
            if changed:
                _save(key, records)
        except Exception:
            continue  # 单个文件损坏不影响其它账号
    return reaped


def migrate_legacy_single_to_groups() -> int:
    """Idempotently give every legacy single textbook the uniform group model.

    IDs, topic keys and file IDs remain unchanged; no graph extraction or LLM
    call is performed. Existing graphs stay readable and are marked for a later
    explicit full rebuild because no complete per-volume spec cache exists.
    """
    migrated = 0
    try:
        files = list(_LIBRARY_DIR.glob("*.textbooks.json"))
    except Exception:
        return 0
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            records = raw.get("textbooks", []) if isinstance(raw, dict) else []
            changed = False
            for record in records:
                if not isinstance(record, dict):
                    continue
                kind = record.get("kind") or "single"
                fid = str(record.get("file_id") or "").strip()
                if kind != "group" and fid:
                    record["kind"] = "group"
                    record["file_ids"] = [fid]
                    record["file_id"] = ""
                    record["graph_policy"] = normalize_graph_policy(
                        record.get("graph_policy"), [fid])
                    record.setdefault("volumes", [])
                    record["needs_reextract"] = True
                    record["updated_at"] = _now()
                    migrated += 1
                    changed = True
                elif kind == "group":
                    fids = [str(x) for x in record.get("file_ids") or [] if str(x)]
                    normalized = normalize_graph_policy(record.get("graph_policy"), fids)
                    if record.get("graph_policy") != normalized:
                        record["graph_policy"] = normalized
                        changed = True
                    if "volumes" not in record:
                        record["volumes"] = []
                        changed = True
            if changed:
                key = path.name[: -len(".textbooks.json")]
                _save(key, records)
        except Exception:
            continue
    return migrated

# Explicit derived-index refresh tasks (separate from OCR retry scheduler).
_REFRESH_TASKS: dict[tuple[str, str], Any] = {}


def refresh_task_running(student_id: str, textbook_id: str) -> bool:
    task = _REFRESH_TASKS.get((student_id, textbook_id))
    return bool(task is not None and not task.done())


def register_refresh_task(student_id: str, textbook_id: str, task: Any) -> None:
    _REFRESH_TASKS[(student_id, textbook_id)] = task


def finish_refresh_task(student_id: str, textbook_id: str, task: Any) -> None:
    key = (student_id, textbook_id)
    if _REFRESH_TASKS.get(key) is task:
        _REFRESH_TASKS.pop(key, None)


def cancel_refresh_task(student_id: str, textbook_id: str) -> None:
    task = _REFRESH_TASKS.pop((student_id, textbook_id), None)
    if task is not None and not task.done():
        task.cancel()


def cancel_all_refresh_tasks() -> None:
    for task in list(_REFRESH_TASKS.values()):
        if not task.done():
            task.cancel()
    _REFRESH_TASKS.clear()
