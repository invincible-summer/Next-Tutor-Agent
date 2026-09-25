"""课堂存储层：唯一 root、路径、原子持久化、CAS、发布事务与可重建索引。

plan.md §16.1/§16.2/§10.1：
- `_CLASSROOM_DIR` 是唯一根常量；其他模块一律调用本模块的路径函数，
  不复制 root 常量或在 import 期冻结派生路径。
- 读取不隐式 mkdir；list/capability 不产生空目录；写入目录 0700、文件 0600。
- 拒绝 symlink 逃逸；ID 严格校验（新 ID 前缀+24 hex；workspace/session/owner
  用路径段校验，兼容现有中文 slug）。
- 发布事务：分配单调 target_revision（空号不复用）→ staging → 校验+fsync →
  原子 rename → lesson 锁内指针提交；commit intent 先落盘，崩溃后可恢复。
- hash = canonical JSON（排序 key、UTF-8）的 SHA-256。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Type, TypeVar

from ..schemas import classroom as sc
from .atomic import atomic_write_bytes, atomic_write_text, file_lock, fsync_dir

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CLASSROOM_DIR = _PROJECT_ROOT / "chat_history" / "classroom"

_DIR_MODE = 0o700
_FILE_MODE = 0o600

M = TypeVar("M")


class ClassroomStorageError(RuntimeError):
    """强制写失败/路径非法 → service 层映射为 storage_unavailable。"""

    code = "storage_unavailable"


class LessonDamagedError(RuntimeError):
    """已发布内容损坏：标 damaged 隔离，不返回空课冒充正常（§16.2）。"""

    code = "damaged"


class CasConflictError(RuntimeError):
    """expected state revision 不匹配 → 409。"""

    code = "revision_conflict"


# ---------------------------------------------------------------------------
# ID 与路径段校验（§10.1）
# ---------------------------------------------------------------------------

_NEW_ID_RES: dict[str, re.Pattern[str]] = {
    kind: re.compile(rf"^{kind}_[0-9a-f]{{24}}$")
    for kind in ("les", "job", "run", "ast", "src", "seg", "blk", "ckp")
}
_SLIDE_ID_RE = re.compile(r"^s_[0-9a-f]{12}$")
_BAD_SEGMENT_RE = re.compile(r"[\x00-\x1f/\\]")


def validate_new_id(kind: str, value: str) -> str:
    pattern = _NEW_ID_RES.get(kind)
    if pattern is None or not pattern.match(value):
        raise ClassroomStorageError(f"非法课堂 {kind} ID")
    return value


def validate_slide_id(value: str) -> str:
    if not _SLIDE_ID_RE.match(value):
        raise ClassroomStorageError("非法课堂页 ID")
    return value


def validate_path_segment(value: str, *, field: str = "id",
                          max_len: int = 128) -> str:
    """workspace/student/session 等既有系统 ID 的路径段校验。

    兼容中文 slug：只拒绝路径穿越、控制字符、空值与超长，不引入新格式。
    """
    if not value or len(value) > max_len or value in (".", ".."):
        raise ClassroomStorageError(f"非法{field}")
    if _BAD_SEGMENT_RE.search(value):
        raise ClassroomStorageError(f"非法{field}")
    return value


def new_id(kind: str) -> str:
    """生成新的课堂 ID（secrets 随机，服务端专用）。"""
    import secrets

    return f"{kind}_{secrets.token_hex(12)}"


def new_slide_id() -> str:
    import secrets

    return f"s_{secrets.token_hex(6)}"


# ---------------------------------------------------------------------------
# 路径函数（§16.1 目录布局）
# ---------------------------------------------------------------------------

def classroom_root() -> Path:
    return _CLASSROOM_DIR


def owner_root(owner_id: str) -> Path:
    validate_path_segment(owner_id, field="owner_id")
    return _CLASSROOM_DIR / owner_id


def owner_meta_path(owner_id: str) -> Path:
    return owner_root(owner_id) / "owner.json"


def image_search_cache_path(owner_id: str, query_hash: str) -> Path:
    validate_path_segment(query_hash, field="query_hash", max_len=64)
    return owner_root(owner_id) / "image-search-cache" / f"{query_hash}.json"


def voice_preview_path(owner_id: str, synthesis_hash: str, ext: str) -> Path:
    validate_path_segment(synthesis_hash, field="synthesis_hash", max_len=64)
    if ext not in ("wav", "json"):
        raise ClassroomStorageError("非法试听扩展名")
    return owner_root(owner_id) / "voice-previews" / f"{synthesis_hash}.{ext}"


def workspace_root(owner_id: str, workspace_id: str) -> Path:
    validate_path_segment(workspace_id, field="workspace_id")
    return owner_root(owner_id) / "workspaces" / workspace_id


def index_path(owner_id: str, workspace_id: str) -> Path:
    return workspace_root(owner_id, workspace_id) / "index.json"


def operations_dir(owner_id: str, workspace_id: str) -> Path:
    return workspace_root(owner_id, workspace_id) / "operations"


def operation_path(owner_id: str, workspace_id: str, operation_id: str) -> Path:
    """可恢复操作文件：归档/恢复/笔记/QA 绑定意图与完成标记（§16.1）。"""
    validate_path_segment(operation_id, field="operation_id")
    return operations_dir(owner_id, workspace_id) / f"{operation_id}.json"


def lesson_root(owner_id: str, workspace_id: str, lesson_id: str) -> Path:
    validate_new_id("les", lesson_id)
    return workspace_root(owner_id, workspace_id) / "lessons" / lesson_id


def lesson_meta_path(owner_id: str, workspace_id: str, lesson_id: str) -> Path:
    return lesson_root(owner_id, workspace_id, lesson_id) / "lesson.json"


def revisions_root(owner_id: str, workspace_id: str, lesson_id: str) -> Path:
    return lesson_root(owner_id, workspace_id, lesson_id) / "revisions"


def revision_dir(owner_id: str, workspace_id: str, lesson_id: str,
                 revision: int) -> Path:
    if not isinstance(revision, int) or revision < 1:
        raise ClassroomStorageError("非法 revision")
    return revisions_root(owner_id, workspace_id, lesson_id) / str(revision)


def revision_spec_path(owner_id: str, workspace_id: str, lesson_id: str,
                       revision: int) -> Path:
    return revision_dir(owner_id, workspace_id, lesson_id, revision) / \
        "spec.private.json"


def jobs_root(owner_id: str, workspace_id: str, lesson_id: str) -> Path:
    return lesson_root(owner_id, workspace_id, lesson_id) / "jobs"


def job_root(owner_id: str, workspace_id: str, lesson_id: str,
             job_id: str) -> Path:
    validate_new_id("job", job_id)
    return jobs_root(owner_id, workspace_id, lesson_id) / job_id


def job_meta_path(owner_id: str, workspace_id: str, lesson_id: str,
                  job_id: str) -> Path:
    return job_root(owner_id, workspace_id, lesson_id, job_id) / "job.json"


def job_staging_dir(owner_id: str, workspace_id: str, lesson_id: str,
                    job_id: str) -> Path:
    return job_root(owner_id, workspace_id, lesson_id, job_id) / "staging"


def asset_file_path(owner_id: str, workspace_id: str, lesson_id: str,
                    asset_id: str, ext: str) -> Path:
    validate_new_id("ast", asset_id)
    if ext not in ("jpg", "png", "webp"):
        raise ClassroomStorageError("非法图片扩展名")
    return lesson_root(owner_id, workspace_id, lesson_id) / "assets" / \
        f"{asset_id}.{ext}"


def asset_meta_path(owner_id: str, workspace_id: str, lesson_id: str,
                    asset_id: str) -> Path:
    validate_new_id("ast", asset_id)
    return lesson_root(owner_id, workspace_id, lesson_id) / "assets" / \
        f"{asset_id}.json"


def run_path(owner_id: str, workspace_id: str, lesson_id: str,
             run_id: str) -> Path:
    validate_new_id("run", run_id)
    return lesson_root(owner_id, workspace_id, lesson_id) / f"runs" / \
        f"{run_id}.json"


def audio_file_path(owner_id: str, workspace_id: str, lesson_id: str,
                    synthesis_hash: str) -> Path:
    validate_path_segment(synthesis_hash, field="synthesis_hash", max_len=64)
    return lesson_root(owner_id, workspace_id, lesson_id) / "audio" / \
        f"{synthesis_hash}.wav"


def audio_meta_path(owner_id: str, workspace_id: str, lesson_id: str,
                    synthesis_hash: str) -> Path:
    validate_path_segment(synthesis_hash, field="synthesis_hash", max_len=64)
    return lesson_root(owner_id, workspace_id, lesson_id) / "audio" / \
        f"{synthesis_hash}.json"


def export_zip_path(owner_id: str, workspace_id: str, lesson_id: str,
                    export_id: str) -> Path:
    validate_new_id("job", export_id)
    return lesson_root(owner_id, workspace_id, lesson_id) / "exports" / \
        f"{export_id}.zip"


def export_meta_path(owner_id: str, workspace_id: str, lesson_id: str,
                     export_id: str) -> Path:
    validate_new_id("job", export_id)
    return lesson_root(owner_id, workspace_id, lesson_id) / "exports" / \
        f"{export_id}.json"


# ---------------------------------------------------------------------------
# 安全目录/文件原语
# ---------------------------------------------------------------------------

def _ensure_dir(path: Path) -> None:
    """创建目录（含父级）并统一 0700；已存在不改权限。"""
    missing: list[Path] = []
    probe = path
    while not probe.exists():
        missing.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent
    path.mkdir(parents=True, exist_ok=True)
    for created in missing:
        try:
            os.chmod(created, _DIR_MODE)
        except OSError:
            pass


def _guard_symlink_parents(path: Path) -> None:
    """拒绝从 classroom 根到目标路径之间任何已存在的 symlink 组件。"""
    try:
        rel = path.relative_to(_CLASSROOM_DIR)
    except ValueError:
        raise ClassroomStorageError("路径不在课堂根内") from None
    current = _CLASSROOM_DIR
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ClassroomStorageError("symlink 逃逸")


def _assert_within(root: Path, path: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ClassroomStorageError("路径逃逸") from None
    _guard_symlink_parents(path)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 通用模型读写（读不 mkdir；损坏 → damaged，不静默置空）
# ---------------------------------------------------------------------------

def _read_model(path: Path, model_cls: Type[M], *,
                allow_missing: bool = True) -> M | None:
    if not path.exists():
        if allow_missing:
            return None
        raise ClassroomStorageError(f"缺失文件: {path.name}")
    _guard_symlink_parents(path)
    if path.is_symlink():
        raise ClassroomStorageError("symlink 逃逸")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LessonDamagedError(f"损坏的 JSON: {path.name}") from exc
    try:
        return model_cls.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise LessonDamagedError(f"损坏的模型: {path.name}") from exc


def _write_model(path: Path, model: Any) -> None:
    _guard_symlink_parents(path)
    _ensure_dir(path.parent)
    text = model.model_dump_json(by_alias=True, indent=2)
    with file_lock(path):
        atomic_write_text(path, text)
        try:
            os.chmod(path, _FILE_MODE)
        except OSError:
            pass


def write_json(path: Path, obj: Any) -> None:
    _guard_symlink_parents(path)
    _ensure_dir(path.parent)
    with file_lock(path):
        atomic_write_text(path, canonical_json(obj))
        try:
            os.chmod(path, _FILE_MODE)
        except OSError:
            pass


def read_json(path: Path) -> Any | None:
    if not path.exists() or path.is_symlink():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_bytes(path: Path, data: bytes) -> None:
    _guard_symlink_parents(path)
    _ensure_dir(path.parent)
    with file_lock(path):
        atomic_write_bytes(path, data)
        try:
            os.chmod(path, _FILE_MODE)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# owner 生命周期元数据（tombstone 防晚写，A03 接入 purge）
# ---------------------------------------------------------------------------

def owner_record(owner_id: str) -> dict:
    return read_json(owner_meta_path(owner_id)) or {
        "lifecycle": "active", "created_at": utcnow().isoformat(),
        "idempotency": {}, "quota": {},
    }


def owner_lifecycle(owner_id: str) -> str:
    if not owner_meta_path(owner_id).exists():
        return "unknown"
    record = read_json(owner_meta_path(owner_id)) or {}
    return str(record.get("lifecycle", "active"))


def mark_owner_purged(owner_id: str) -> None:
    """账号清理最后一步之外的前置 tombstone：之后的课堂写入全部拒绝。"""
    record = owner_record(owner_id)
    record["lifecycle"] = "purged"
    record["purged_at"] = utcnow().isoformat()
    write_json(owner_meta_path(owner_id), record)


def ensure_owner(owner_id: str) -> None:
    if owner_lifecycle(owner_id) == "purged":
        raise ClassroomStorageError("owner 已注销，拒绝晚到写入")
    if not owner_meta_path(owner_id).exists():
        write_json(owner_meta_path(owner_id), owner_record(owner_id))


def assert_owner_writable(owner_id: str) -> None:
    if owner_lifecycle(owner_id) == "purged":
        raise ClassroomStorageError("owner 已注销，拒绝晚到写入")


# ---------------------------------------------------------------------------
# Lesson / Job / Run 读写与 CAS
# ---------------------------------------------------------------------------

def load_lesson(owner_id: str, workspace_id: str,
                lesson_id: str) -> sc.Lesson | None:
    return _read_model(lesson_meta_path(owner_id, workspace_id, lesson_id),
                       sc.Lesson)


def save_lesson(lesson: sc.Lesson) -> None:
    _write_model(lesson_meta_path(lesson.owner_id, lesson.workspace_id,
                                  lesson.lesson_id), lesson)


def update_lesson(owner_id: str, workspace_id: str, lesson_id: str,
                  mutate: Callable[[sc.Lesson], None]) -> sc.Lesson:
    path = lesson_meta_path(owner_id, workspace_id, lesson_id)
    with file_lock(path):
        lesson = _read_model(path, sc.Lesson, allow_missing=False)
        mutate(lesson)
        lesson.updated_at = utcnow()
        atomic_write_text(path, lesson.model_dump_json(by_alias=True, indent=2))
        try:
            os.chmod(path, _FILE_MODE)
        except OSError:
            pass
        return lesson


def load_job(owner_id: str, workspace_id: str, lesson_id: str,
             job_id: str) -> sc.GenerationJob | None:
    return _read_model(job_meta_path(owner_id, workspace_id, lesson_id, job_id),
                       sc.GenerationJob)


def save_job(job: sc.GenerationJob) -> None:
    _write_model(job_meta_path(job.owner_id, job.workspace_id, job.lesson_id,
                               job.job_id), job)


def update_job(owner_id: str, workspace_id: str, lesson_id: str, job_id: str,
               mutate: Callable[[sc.GenerationJob], None], *,
               expected_state_revision: int | None = None) -> sc.GenerationJob:
    path = job_meta_path(owner_id, workspace_id, lesson_id, job_id)
    with file_lock(path):
        job = _read_model(path, sc.GenerationJob, allow_missing=False)
        if expected_state_revision is not None and \
                job.state_revision != expected_state_revision:
            raise CasConflictError("job state revision 冲突")
        mutate(job)
        job.state_revision += 1
        job.updated_at = utcnow()
        atomic_write_text(path, job.model_dump_json(by_alias=True, indent=2))
        try:
            os.chmod(path, _FILE_MODE)
        except OSError:
            pass
        return job


def load_run(owner_id: str, workspace_id: str, lesson_id: str,
             run_id: str) -> sc.ClassroomRun | None:
    return _read_model(run_path(owner_id, workspace_id, lesson_id, run_id),
                       sc.ClassroomRun)


def save_run(run: sc.ClassroomRun) -> None:
    _write_model(run_path(run.owner_id, run.workspace_id, run.lesson_id,
                          run.run_id), run)


def update_run(owner_id: str, workspace_id: str, lesson_id: str, run_id: str,
               mutate: Callable[[sc.ClassroomRun], None], *,
               expected_state_revision: int | None = None) -> sc.ClassroomRun:
    path = run_path(owner_id, workspace_id, lesson_id, run_id)
    with file_lock(path):
        run = _read_model(path, sc.ClassroomRun, allow_missing=False)
        if expected_state_revision is not None and \
                run.state_revision != expected_state_revision:
            raise CasConflictError("run state revision 冲突")
        mutate(run)
        run.state_revision += 1
        run.updated_at = utcnow()
        atomic_write_text(path, run.model_dump_json(by_alias=True, indent=2))
        try:
            os.chmod(path, _FILE_MODE)
        except OSError:
            pass
        return run


def list_runs(owner_id: str, workspace_id: str,
              lesson_id: str) -> list[sc.ClassroomRun]:
    runs_dir = lesson_root(owner_id, workspace_id, lesson_id) / "runs"
    if not runs_dir.exists():
        return []
    result: list[sc.ClassroomRun] = []
    for entry in sorted(runs_dir.iterdir()):
        if not entry.name.endswith(".json"):
            continue
        try:
            run = _read_model(entry, sc.ClassroomRun)
        except LessonDamagedError:
            continue
        if run is not None:
            result.append(run)
    return result


# ---------------------------------------------------------------------------
# Revision 发布事务（§16.2 六步）
# ---------------------------------------------------------------------------

def allocate_revision(owner_id: str, workspace_id: str, lesson_id: str,
                      *, base_revision: int | None = None) -> int:
    """lesson 锁内分配单调 target_revision；失败留空号，永不复用。"""
    def mutate(lesson: sc.Lesson) -> None:
        if base_revision is not None and \
                base_revision not in lesson.published_revisions:
            raise CasConflictError("base_revision 未发布")
        if len(lesson.published_revisions) >= sc.MAX_PUBLISHED_REVISIONS:
            raise ClassroomStorageError("已发布版本达到上限")

    lesson = update_lesson(owner_id, workspace_id, lesson_id, mutate)
    target = lesson.next_revision

    def bump(lesson: sc.Lesson) -> None:
        lesson.next_revision = target + 1

    update_lesson(owner_id, workspace_id, lesson_id, bump)
    return target


def revision_staging_dir(owner_id: str, workspace_id: str, lesson_id: str,
                         revision: int) -> Path:
    return revisions_root(owner_id, workspace_id, lesson_id) / \
        f".staging-{revision}"


def prepare_revision_staging(owner_id: str, workspace_id: str, lesson_id: str,
                             revision: int) -> Path:
    staging = revision_staging_dir(owner_id, workspace_id, lesson_id, revision)
    if staging.exists():
        shutil.rmtree(staging)
    _ensure_dir(staging)
    return staging


def stage_file(staging: Path, name: str, data: bytes | str) -> Path:
    if name in ("", ".") or "/" in name or name.startswith("."):
        raise ClassroomStorageError("非法 staging 文件名")
    path = staging / name
    if isinstance(data, str):
        atomic_write_text(path, data)
    else:
        atomic_write_bytes(path, data)
    return path


def save_commit_intent(job: sc.GenerationJob, revision: int,
                       manifest: dict) -> sc.GenerationJob:
    """rename 前持久化 commit intent：expected epoch + manifest hash。"""
    intent = {
        "revision": revision,
        "manifest_hash": canonical_hash(manifest),
        "expected_epoch": job.epoch,
        "created_at": utcnow().isoformat(),
    }
    return update_job(
        job.owner_id, job.workspace_id, job.lesson_id, job.job_id,
        lambda j: j.artifacts.__setitem__("commit_intent", canonical_json(intent)))


def commit_revision(owner_id: str, workspace_id: str, lesson_id: str,
                    revision: int, manifest: dict, *,
                    expected_epoch: int | None = None,
                    cancel_requested: bool = False) -> sc.Lesson:
    """staging → 校验 → fsync → rename → lesson 锁内指针提交（§16.2）。"""
    staging = revision_staging_dir(owner_id, workspace_id, lesson_id, revision)
    _assert_within(revisions_root(owner_id, workspace_id, lesson_id), staging)
    target = revision_dir(owner_id, workspace_id, lesson_id, revision)
    manifest_path = target / "manifest.json"

    if not staging.is_dir():
        # 幂等重放：revision 目录已提交且 manifest hash 一致 → 只补指针
        existing = read_json(manifest_path)
        if not existing or canonical_hash(existing) != canonical_hash(manifest):
            raise ClassroomStorageError("staging 目录缺失")
        return _publish_pointer(owner_id, workspace_id, lesson_id, revision,
                                manifest, expected_epoch=expected_epoch,
                                cancel_requested=cancel_requested)

    files: dict[str, str] = dict(manifest.get("files", {}))
    if "spec.private.json" not in files:
        raise ClassroomStorageError("manifest 缺少 spec.private.json")
    for name, expected_hash in files.items():
        fpath = staging / name
        if not fpath.is_file():
            raise ClassroomStorageError(f"staging 缺少文件 {name}")
        if bytes_hash(fpath.read_bytes()) != expected_hash:
            raise ClassroomStorageError(f"staging 文件 hash 不匹配 {name}")
        fsync_dir(staging)

    # manifest 最后写（发布标记完整）
    atomic_write_text(staging / "manifest.json", canonical_json(manifest))
    fsync_dir(staging)

    if target.exists():
        # 幂等重放：已提交过则只补指针
        shutil.rmtree(staging, ignore_errors=True)
    else:
        os.rename(staging, target)
        fsync_dir(target.parent)

    return _publish_pointer(owner_id, workspace_id, lesson_id, revision,
                            manifest, expected_epoch=expected_epoch,
                            cancel_requested=cancel_requested)


def _publish_pointer(owner_id: str, workspace_id: str, lesson_id: str,
                     revision: int, manifest: dict, *,
                     expected_epoch: int | None,
                     cancel_requested: bool) -> sc.Lesson:
    def mutate(lesson: sc.Lesson) -> None:
        if lesson.lifecycle != sc.LessonLifecycle.active:
            raise ClassroomStorageError("课程生命周期不允许发布")
        if cancel_requested:
            raise ClassroomStorageError("已请求取消，发布被拦截")
        if revision in lesson.published_revisions:
            return  # 幂等
        if len(lesson.published_revisions) >= sc.MAX_PUBLISHED_REVISIONS:
            raise ClassroomStorageError("已发布版本达到上限")
        lesson.published_revisions.append(revision)
        lesson.latest_ready_revision = revision

    return update_lesson(owner_id, workspace_id, lesson_id, mutate)


def recover_pending_publish(owner_id: str, workspace_id: str, lesson_id: str,
                            job: sc.GenerationJob) -> bool:
    """崩溃恢复：revision 目录已存在但指针未提交时补齐（§16.2 步骤 5）。"""
    intent_raw = job.artifacts.get("commit_intent")
    if not intent_raw:
        return False
    try:
        intent = json.loads(intent_raw) if isinstance(intent_raw, str) \
            else dict(intent_raw)
        revision = int(intent["revision"])
    except (ValueError, KeyError, TypeError):
        return False
    rdir = revision_dir(owner_id, workspace_id, lesson_id, revision)
    manifest_path = rdir / "manifest.json"
    if not manifest_path.is_file():
        return False
    manifest = read_json(manifest_path)
    if not manifest or canonical_hash(manifest) != intent.get("manifest_hash"):
        return False  # 不得"看见 manifest 就无条件发布"
    lesson = load_lesson(owner_id, workspace_id, lesson_id)
    if lesson is None or revision in lesson.published_revisions:
        return False
    _publish_pointer(owner_id, workspace_id, lesson_id, revision, manifest,
                     expected_epoch=None, cancel_requested=False)
    return True


def load_revision(owner_id: str, workspace_id: str, lesson_id: str,
                  revision: int) -> sc.LessonRevision | None:
    return _read_model(
        revision_spec_path(owner_id, workspace_id, lesson_id, revision),
        sc.LessonRevision)


def load_revision_manifest(owner_id: str, workspace_id: str, lesson_id: str,
                           revision: int) -> dict | None:
    path = revision_dir(owner_id, workspace_id, lesson_id, revision) / \
        "manifest.json"
    return read_json(path)


def quarantine_lesson(owner_id: str, workspace_id: str, lesson_id: str,
                      reason: str) -> Path | None:
    """损坏课程隔离到 <owner>/damaged/，不返回空课冒充正常。"""
    src = lesson_root(owner_id, workspace_id, lesson_id)
    if not src.exists():
        return None
    damaged_root = owner_root(owner_id) / "damaged"
    _ensure_dir(damaged_root)
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    dest = damaged_root / f"{lesson_id}_{stamp}"
    shutil.move(str(src), str(dest))
    write_json(damaged_root / f"{lesson_id}_{stamp}.reason.json",
               {"lesson_id": lesson_id, "workspace_id": workspace_id,
                "reason": reason, "moved_at": utcnow().isoformat()})
    return dest


# ---------------------------------------------------------------------------
# 可重建索引（§16.1 index.json：不是内容事实源）
# ---------------------------------------------------------------------------

def empty_index() -> dict:
    return {"version": 1, "updated_at": utcnow().isoformat(),
            "lessons": {}, "jobs": {}}


def read_index(owner_id: str, workspace_id: str) -> dict:
    data = read_json(index_path(owner_id, workspace_id))
    if not isinstance(data, dict) or "lessons" not in data:
        return empty_index()
    return data


def update_index(owner_id: str, workspace_id: str,
                 mutate: Callable[[dict], None]) -> dict:
    path = index_path(owner_id, workspace_id)
    with file_lock(path):
        data = read_json(path)
        if not isinstance(data, dict) or "lessons" not in data:
            data = empty_index()
        mutate(data)
        data["updated_at"] = utcnow().isoformat()
        _ensure_dir(path.parent)
        atomic_write_text(path, canonical_json(data))
        try:
            os.chmod(path, _FILE_MODE)
        except OSError:
            pass
        return data


def index_upsert_lesson(owner_id: str, workspace_id: str,
                        lesson: sc.Lesson, *,
                        job: sc.GenerationJob | None = None) -> None:
    def mutate(data: dict) -> None:
        entry = data["lessons"].setdefault(lesson.lesson_id, {})
        entry.update({
            "lesson_id": lesson.lesson_id,
            "title": lesson.title,
            "updated_at": lesson.updated_at.isoformat(),
            "latest_ready_revision": lesson.latest_ready_revision,
            "lifecycle": lesson.lifecycle.value,
            "latest_job_id": lesson.latest_job_id,
        })
        if job is not None:
            data["jobs"][job.job_id] = {
                "lesson_id": lesson.lesson_id,
                "state": job.state.value,
                "updated_at": job.updated_at.isoformat(),
            }
    update_index(owner_id, workspace_id, mutate)


def index_remove_lesson(owner_id: str, workspace_id: str,
                        lesson_id: str) -> None:
    def mutate(data: dict) -> None:
        data["lessons"].pop(lesson_id, None)
        data["jobs"] = {jid: j for jid, j in data["jobs"].items()
                        if j.get("lesson_id") != lesson_id}
    update_index(owner_id, workspace_id, mutate)


def list_lesson_ids(owner_id: str, workspace_id: str) -> list[str]:
    """从索引读取课程 ID；索引缺失时扫目录重建（不 mkdir）。"""
    index = read_index(owner_id, workspace_id)
    if index["lessons"]:
        return list(index["lessons"].keys())
    lessons_dir = workspace_root(owner_id, workspace_id) / "lessons"
    if not lessons_dir.exists():
        return []
    return sorted(p.name for p in lessons_dir.iterdir() if p.is_dir())


def rebuild_index(owner_id: str, workspace_id: str) -> dict:
    """全量扫描 lessons/ 重建索引（内容以 lesson.json/job.json 为事实源）。"""
    lessons_dir = workspace_root(owner_id, workspace_id) / "lessons"
    lessons: dict[str, dict] = {}
    jobs: dict[str, dict] = {}
    if lessons_dir.exists():
        for entry in sorted(lessons_dir.iterdir()):
            if not entry.is_dir():
                continue
            lesson = _read_model(entry / "lesson.json", sc.Lesson)
            if lesson is None:
                continue
            lessons[lesson.lesson_id] = {
                "lesson_id": lesson.lesson_id,
                "title": lesson.title,
                "updated_at": lesson.updated_at.isoformat(),
                "latest_ready_revision": lesson.latest_ready_revision,
                "lifecycle": lesson.lifecycle.value,
                "latest_job_id": lesson.latest_job_id,
            }
            jobs_dir = entry / "jobs"
            if jobs_dir.exists():
                for jentry in sorted(jobs_dir.iterdir()):
                    if not jentry.is_dir():
                        continue
                    job = _read_model(jentry / "job.json", sc.GenerationJob)
                    if job is not None:
                        jobs[job.job_id] = {
                            "lesson_id": lesson.lesson_id,
                            "state": job.state.value,
                            "updated_at": job.updated_at.isoformat(),
                        }
    data = empty_index()
    data["lessons"] = lessons
    data["jobs"] = jobs
    path = index_path(owner_id, workspace_id)
    _ensure_dir(path.parent)
    with file_lock(path):
        atomic_write_text(path, canonical_json(data))
    return data
