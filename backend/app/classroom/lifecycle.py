"""课堂生命周期：归档快照、恢复、purge、上传清理与可恢复操作。

plan.md §16.4：
- 单课归档：先写 durable op、标 lifecycle=archiving、提升 epoch、取消
  生成/lease，再快照内容与 run；音频缓存与过期 exports 不打包（可重建）。
- 工作区归档在 trash bundle commit 前冻结该区所有课堂写入，commit 后删
  活跃课堂子树；恢复按原 ID 检查冲突，run 恢复为 paused、lease 清空、
  中断 job 进入 needs_input(recovered_after_archive)。
- purge_account：先 tombstone 吊销工作资格，再删整个 owner 课堂根。
- uploads_only 清理：删自有上传图与包含其 bytes 的编译产物，保留派生
  讲稿/spec/进度；asset 标 unavailable，不篡改冻结 spec。
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from ..core import classroom_store as store
from ..schemas import classroom as sc

# 快照/恢复时跳过的可重建目录（§16.4：音频缓存与过期 exports 可不打包）
_SNAPSHOT_IGNORE = shutil.ignore_patterns("audio", "exports")


# ---------------------------------------------------------------------------
# 可恢复操作（operations/<op_id>.json）
# ---------------------------------------------------------------------------

def begin_operation(owner_id: str, workspace_id: str, kind: str,
                    payload: dict[str, Any] | None = None) -> str:
    op_id = f"op_{int(time.time() * 1000):x}_{store.new_id('job')[4:16]}"
    record = {
        "operation_id": op_id, "kind": kind,
        "state": "running",
        "payload": dict(payload or {}),
        "created_at": store.utcnow().isoformat(),
        "updated_at": store.utcnow().isoformat(),
    }
    store.write_json(store.operation_path(owner_id, workspace_id, op_id), record)
    return op_id


def complete_operation(owner_id: str, workspace_id: str, op_id: str,
                       result: dict[str, Any] | None = None) -> None:
    path = store.operation_path(owner_id, workspace_id, op_id)
    record = store.read_json(path) or {}
    record.update({"state": "succeeded",
                   "result": dict(result or {}),
                   "updated_at": store.utcnow().isoformat()})
    store.write_json(path, record)


def fail_operation(owner_id: str, workspace_id: str, op_id: str,
                   error: str) -> None:
    path = store.operation_path(owner_id, workspace_id, op_id)
    record = store.read_json(path) or {}
    record.update({"state": "failed", "error": error[:500],
                   "updated_at": store.utcnow().isoformat()})
    store.write_json(path, record)


# ---------------------------------------------------------------------------
# 单课归档/恢复载荷（由 core/trash.py 调用）
# ---------------------------------------------------------------------------

def freeze_lesson(owner_id: str, workspace_id: str, lesson_id: str) -> None:
    """归档前冻结：lifecycle=archiving + 取消在途 job + 清 lease。"""

    def mutate(lesson: sc.Lesson) -> None:
        lesson.lifecycle = sc.LessonLifecycle.archiving

    store.update_lesson(owner_id, workspace_id, lesson_id, mutate)

    jobs_dir = store.jobs_root(owner_id, workspace_id, lesson_id)
    if jobs_dir.exists():
        for jentry in sorted(jobs_dir.iterdir()):
            if not jentry.is_dir():
                continue
            job = store.read_json(jentry / "job.json")
            if not job:
                continue
            if job.get("state") in ("queued", "running", "awaiting_outline"):
                def cancel(j: sc.GenerationJob) -> None:
                    j.cancel_requested = True
                    j.epoch += 1
                try:
                    store.update_job(owner_id, workspace_id, lesson_id,
                                     str(job.get("job_id")), cancel)
                except Exception:
                    pass

    for run in store.list_runs(owner_id, workspace_id, lesson_id):
        def clear_lease(r: sc.ClassroomRun) -> None:
            r.lease = None
        try:
            store.update_run(owner_id, workspace_id, lesson_id, run.run_id,
                             clear_lease)
        except Exception:
            pass


def snapshot_lesson_into(owner_id: str, workspace_id: str, lesson_id: str,
                         dest: Path) -> dict[str, Any]:
    """把课程子树快照到 trash bundle 的 payload 位置；返回摘要元数据。"""
    lesson = store.load_lesson(owner_id, workspace_id, lesson_id)
    if lesson is None:
        raise FileNotFoundError("课程不存在")
    src = store.lesson_root(owner_id, workspace_id, lesson_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, ignore=_SNAPSHOT_IGNORE, dirs_exist_ok=True)
    return {
        "workspace_id": workspace_id,
        "lesson_id": lesson_id,
        "title": lesson.title,
        "latest_ready_revision": lesson.latest_ready_revision,
        "published_count": len(lesson.published_revisions),
    }


def _prune_empty_parents(path: Path, stop_at: Path) -> None:
    """删除 path 起向上到 stop_at（不含）之间新出现的空目录。

    归档/删除后 lessons/、workspaces/ 可能变空；owner.json 恒在 owner 根，
    空目录只可能是中间层。rmdir 非空即失败，与并发新建课程天然互斥。
    """
    current = path
    stop_resolved = stop_at.resolve()
    while current.resolve() != stop_resolved:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def delete_lesson_active(owner_id: str, workspace_id: str,
                         lesson_id: str) -> None:
    root = store.lesson_root(owner_id, workspace_id, lesson_id)
    shutil.rmtree(root, ignore_errors=True)
    store.index_remove_lesson(owner_id, workspace_id, lesson_id)
    _prune_empty_parents(root.parent, store.owner_root(owner_id))


def _adjust_restored_tree(owner_id: str, workspace_id: str,
                          lesson_id: str) -> None:
    """恢复后的 run → paused、lease 清空；中断 job → needs_input。"""
    for run in store.list_runs(owner_id, workspace_id, lesson_id):
        def adjust(r: sc.ClassroomRun) -> None:
            if r.status == sc.RunStatus.active:
                r.status = sc.RunStatus.paused
            r.lease = None
        try:
            store.update_run(owner_id, workspace_id, lesson_id, run.run_id,
                             adjust)
        except Exception:
            pass

    jobs_dir = store.jobs_root(owner_id, workspace_id, lesson_id)
    if jobs_dir.exists():
        for jentry in sorted(jobs_dir.iterdir()):
            if not jentry.is_dir():
                continue
            raw = store.read_json(jentry / "job.json")
            if not raw:
                continue
            if raw.get("state") in ("queued", "running", "awaiting_outline"):
                def recovered(j: sc.GenerationJob) -> None:
                    j.state = sc.JobState.needs_input
                    j.last_error = "recovered_after_archive"
                try:
                    store.update_job(owner_id, workspace_id, lesson_id,
                                     str(raw.get("job_id")), recovered)
                except Exception:
                    pass


def restore_lesson_tree(owner_id: str, workspace_id: str, lesson_id: str,
                        src: Path) -> dict[str, Any]:
    """按原 ID 恢复课程子树；已存在同 ID 课程则报冲突。"""
    dest = store.lesson_root(owner_id, workspace_id, lesson_id)
    if dest.exists():
        raise FileExistsError("同 ID 课程已存在")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    _adjust_restored_tree(owner_id, workspace_id, lesson_id)

    def revive(lesson: sc.Lesson) -> None:
        lesson.lifecycle = sc.LessonLifecycle.active

    try:
        store.update_lesson(owner_id, workspace_id, lesson_id, revive)
    except Exception:
        pass
    store.rebuild_index(owner_id, workspace_id)
    lesson = store.load_lesson(owner_id, workspace_id, lesson_id)
    return {"lesson_id": lesson_id, "title": lesson.title if lesson else ""}


# ---------------------------------------------------------------------------
# 工作区级课堂快照/恢复（trash.archive_workspace / restore 集成）
# ---------------------------------------------------------------------------

def snapshot_workspace_into(owner_id: str, workspace_id: str,
                            dest: Path) -> list[str]:
    """冻结并快照该工作区全部课程；返回课程 ID 列表（bundle commit 前）。"""
    lesson_ids = store.list_lesson_ids(owner_id, workspace_id)
    for lesson_id in lesson_ids:
        freeze_lesson(owner_id, workspace_id, lesson_id)
    restored_ids: list[str] = []
    for lesson_id in lesson_ids:
        try:
            snapshot_lesson_into(owner_id, workspace_id, lesson_id,
                                 dest / lesson_id)
            restored_ids.append(lesson_id)
        except FileNotFoundError:
            continue
    return restored_ids


def delete_workspace_active(owner_id: str, workspace_id: str) -> None:
    """trash bundle commit 之后删除活跃课堂子树（§16.4）。"""
    root = store.workspace_root(owner_id, workspace_id)
    shutil.rmtree(root, ignore_errors=True)
    _prune_empty_parents(root.parent, store.owner_root(owner_id))


def restore_workspace_from(owner_id: str, workspace_id: str,
                           payload_dir: Path) -> list[str]:
    if not payload_dir.is_dir():
        return []
    restored: list[str] = []
    for lesson_dir in sorted(payload_dir.iterdir()):
        if not lesson_dir.is_dir():
            continue
        try:
            restore_lesson_tree(owner_id, workspace_id, lesson_dir.name,
                                lesson_dir)
            restored.append(lesson_dir.name)
        except FileExistsError:
            continue
    return restored


# ---------------------------------------------------------------------------
# 账号级清理（account_data 集成）
# ---------------------------------------------------------------------------

def purge_owner_classroom(owner_id: str, *, tombstone: bool = False) -> dict[str, Any]:
    """删除整个 owner 课堂根。

    tombstone=True 仅用于账号注销：先写根外 purged 标记吊销课堂工作资格，
    防止清理期间/之后的晚到写回复活目录。管理员 scope=all 清理不销号，
    不打 tombstone；对已 tombstone 的 owner 幂等（根已删，直接返回）。
    """
    if tombstone:
        store.mark_owner_purged(owner_id)
    root = store.owner_root(owner_id)
    freed = _tree_bytes(root)
    shutil.rmtree(root, ignore_errors=True)
    return {"owner_id": owner_id, "freed_bytes": freed}


def _tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def storage_sizes(owner_id: str) -> tuple[int, int]:
    """返回 (内容字节数[不含音频], 音频字节数)。只读，不 mkdir。"""
    root = store.owner_root(owner_id)
    if not root.is_dir():
        return 0, 0
    content = 0
    audio = 0
    for p in root.rglob("*"):
        try:
            if not p.is_file():
                continue
            size = p.stat().st_size
        except OSError:
            continue
        if "audio" in p.relative_to(root).parts or \
                "voice-previews" in p.relative_to(root).parts:
            audio += size
        else:
            content += size
    return content, audio


def strip_uploaded_images(owner_id: str) -> int:
    """uploads_only 清理：删自有上传图 bytes 与包含其的编译产物/导出。

    冻结 spec 不动；asset 元数据标 unavailable；frame.html/exports ZIP
    含内嵌图片 bytes，一并删除（读取时按 asset 状态显示缺图说明）。
    """
    root = store.owner_root(owner_id)
    if not root.is_dir():
        return 0
    stripped = 0
    for asset_meta in sorted(root.glob("workspaces/*/lessons/*/assets/*.json")):
        data = store.read_json(asset_meta)
        if not data:
            continue
        provenance = data.get("provenance") or {}
        if provenance.get("provider") != "upload":
            continue
        asset_id = str(data.get("asset_id") or asset_meta.stem)
        for ext in ("jpg", "png", "webp"):
            try:
                asset_meta.with_name(f"{asset_id}.{ext}").unlink(missing_ok=True)
            except OSError:
                pass
        data["status"] = sc.AssetStatus.unavailable.value
        data["stripped_at"] = store.utcnow().isoformat()
        store.write_json(asset_meta, data)
        stripped += 1
    if stripped:
        # 含内嵌图片 bytes 的派生编译产物与导出一并删除（可重建）。
        for frame in root.glob("workspaces/*/lessons/*/revisions/*/frame.html"):
            try:
                frame.unlink()
            except OSError:
                pass
        for export in root.glob("workspaces/*/lessons/*/exports/*.zip"):
            try:
                export.unlink()
            except OSError:
                pass
    return stripped
