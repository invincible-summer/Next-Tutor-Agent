"""Owner-isolated atomic jobs, append-only run events, immutable artifacts."""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path

from app.core import paths
from app.core.atomic import atomic_write_bytes, atomic_write_text, file_lock

from .contracts import IllustrationError

_ILLUSTRATIONS_DIR = paths.bind_storage_path(__name__, "_ILLUSTRATIONS_DIR", "illustrations")

# Owner epoch 标记目录。名字以 "." 开头——safe() 的首字符规则使任何 owner
# 键都不可能产生这个路径，该目录也天然被 owner 扫描/孤儿清理跳过。
_EPOCHS_DIRNAME = ".epochs"
# 标记文件损坏时的 fail-closed 返回值：保证迟到写全部被 epoch 闸拒绝。
_EPOCH_INVALID = 2**31


def safe(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}", value) or ".." in value:
        raise ValueError("invalid_illustration_key")
    return value


def owner_dir(owner: str) -> Path:
    return _ILLUSTRATIONS_DIR / safe(owner)


def _epochs_dir() -> Path:
    # 按次计算：测试沙箱会整体重绑 _ILLUSTRATIONS_DIR。
    return _ILLUSTRATIONS_DIR / _EPOCHS_DIRNAME


def _epoch_marker(owner: str) -> Path:
    return _epochs_dir() / f"{safe(owner)}.json"


def epoch(owner: str) -> int:
    """Owner epoch（0 = 无删除历史）。

    磁盘化单调标记：durable 模式下 API 与 worker 双进程必须读同一 epoch，
    进程内存 dict 只在单进程假设下成立。purge 只 bump 不清标记——它是
    「该 owner 目录已删除」的跨进程 tombstone，防止迟到写复活目录。
    标记损坏时返回失效哨兵：宁可拒绝一切迟到写，也不冒复活风险。
    """
    try:
        return int(json.loads(_epoch_marker(owner).read_text("utf-8"))["epoch"])
    except FileNotFoundError:
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        return _EPOCH_INVALID


def _bump_epoch(owner: str) -> int:
    """失效该 owner 的全部在途写（purge 专用；标记跨 rmtree 保留）。"""
    value = epoch(owner)+1
    marker = _epoch_marker(owner)
    marker.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(marker, json.dumps({"owner": owner, "epoch": value}))
    return value


def iter_owners() -> list[str]:
    """列出有存储目录的 owner（跳过内部标记目录）。"""
    root = _ILLUSTRATIONS_DIR
    if not root.is_dir():
        return []
    return [path.name for path in sorted(root.iterdir())
            if path.is_dir() and not path.name.startswith(".")]


def read(owner: str, kind: str, key: str) -> dict | None:
    if kind not in {"jobs", "runs", "artifacts", "sessions", "scenario_jobs", "scenario_revisions"}:
        raise ValueError("invalid_illustration_kind")
    path = owner_dir(owner) / kind / f"{safe(key)}.json"
    try:
        result = json.loads(path.read_text("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("corrupt illustration store")
        return result
    except FileNotFoundError:
        return None


def write(owner: str, kind: str, key: str, value: dict, *, expected_epoch=None, immutable=False):
    root = owner_dir(owner)
    with file_lock(root):
        if expected_epoch is not None and epoch(owner) != expected_epoch:
            raise IllustrationError("policy_disabled")
        if kind not in {"jobs", "runs", "artifacts", "sessions", "scenario_jobs", "scenario_revisions"}:
            raise ValueError("invalid_illustration_kind")
        path = root / kind / f"{safe(key)}.json"
        if immutable and path.exists():
            if read(owner, kind, key) != value:
                raise IllustrationError("patch_conflict")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
        if expected_epoch is not None and epoch(owner) != expected_epoch:
            # durable 模式跨进程删除竞态：epoch 在检查后、落盘前失效——
            # 补偿删除刚写的文件，绝不让迟到写复活已删除目录。文件模式下
            # file_lock 已串行化 purge，此闸不可能触发，行为零漂移。
            path.unlink(missing_ok=True)
            raise IllustrationError("policy_disabled")


def find_job(owner: str, question_id: str, revision: int, *, include_shadow=False, pipeline_mode=None):
    root = owner_dir(owner) / "jobs"
    if not root.is_dir():
        return None
    rows = [read(owner, "jobs", path.stem) for path in root.glob("illjob_*.json")]
    rows = [r for r in rows if r and r["question_id"] == question_id and r["question_revision"] == revision
            and (include_shadow or not r.get("shadow"))]
    if pipeline_mode is not None:
        rows = [r for r in rows if r["status"] == "ready"
                or r.get("pipeline_mode", "v2") == pipeline_mode]
    # Frozen material wins forever, independent of current catalog/config.
    return max(rows, key=lambda row: (row["status"] == "ready", row["created_at"]), default=None)


def stage(owner: str, job: dict, name: str, *, data=None, expected_epoch=None):
    root = owner_dir(owner)
    with file_lock(root):
        run = read(owner, "runs", job["run_id"]) or {
            "run_id": job["run_id"], "job_id": job["job_id"], "events": [], "created_at": time.time()}
        event = {"sequence": len(run["events"])+1, "stage": name, "at": time.time()}
        if data:
            event.update(data)
        run["events"].append(event)
        write(owner, "runs", job["run_id"], run, expected_epoch=expected_epoch)
        job["stage"] = name
        job["updated_at"] = time.time()
        write(owner, "jobs", job["job_id"], job, expected_epoch=expected_epoch)


def freeze(owner: str, job: dict, compiled, png: bytes, *, contract, reviews, expected_epoch=None):
    artifact_id = "ill_" + uuid.uuid4().hex
    artifact = {"artifact_id": artifact_id, "question_id": job["question_id"],
        "question_revision": job["question_revision"], "contract_hash": contract.contract_hash,
        "scene_hash": compiled.source.scene_hash, "content_hash": compiled.illustration.content_hash,
        "catalog_version": compiled.source.catalog_version, "renderer_version": compiled.source.renderer_version,
        "prompt_versions": job["prompt_versions"], "visual_role": contract.visual_role,
        "pipeline_mode": job.get("pipeline_mode", "v2"),
        "status": "frozen", "illustration": compiled.illustration.model_dump(mode="json"),
        "source": compiled.source.model_dump(mode="json"), "review": reviews,
        "created_at": time.time()}
    root = owner_dir(owner)
    with file_lock(root):
        if expected_epoch is not None and epoch(owner) != expected_epoch:
            raise IllustrationError("policy_disabled")
        preview_path = root / "previews" / f"{artifact_id}.png"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write_bytes(preview_path, png)
            write(owner, "artifacts", artifact_id, artifact, expected_epoch=expected_epoch, immutable=True)
        except IllustrationError:
            # 注册失败（含跨进程删除竞态）：未登记的 preview 是孤儿，撤销。
            preview_path.unlink(missing_ok=True)
            raise
        job.update(status="ready", artifact_id=artifact_id, visual_role=contract.visual_role)
        stage(owner, job, "frozen", data={"content_hash": artifact["content_hash"]}, expected_epoch=expected_epoch)
    return artifact


def _active_job_ids(owner: str) -> tuple[list[str], list[str]]:
    """扫描该 owner 仍处 queued/running 的 (quiz, scenario) job id。"""
    active: list[list[str]] = [[], []]
    for kind, sink in zip(("jobs", "scenario_jobs"), active):
        base = owner_dir(owner) / kind
        if not base.is_dir():
            continue
        for path in base.glob("*.json"):
            try:
                row = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(row, dict) and row.get("status") in {"queued", "running"}:
                sink.append(path.stem)
    return active[0], active[1]


def _cancel_durable_workflows(owner: str) -> None:
    """Best-effort 取消 durable 模式下该 owner 的在途 workflow。

    epoch 已先失效，迟到写本就会被拒——取消只是省掉无谓的模型开销。
    失败静默（Temporal 不可达时 epoch 闸仍是正确性保障）。
    """
    try:
        from app.workflows.config import temporal_configured
        if not temporal_configured():
            return
        from app.workflows.illustration_common import cancel_owner_workflows
        quiz_ids, scenario_ids = _active_job_ids(owner)
        if quiz_ids or scenario_ids:
            cancel_owner_workflows(owner, quiz_ids=quiz_ids,
                                    scenario_ids=scenario_ids)
    except Exception:
        pass


def purge(owner: str):
    root = owner_dir(owner)
    with file_lock(root):
        _bump_epoch(owner)
        from .orchestrator import stop_owner
        stop_owner(owner)
        from .scenario import stop_owner as stop_scenario_owner
        stop_scenario_owner(owner)
        _cancel_durable_workflows(owner)
        shutil.rmtree(root, ignore_errors=True)


def freeze_task(owner, task):
    """Prepare immutable material before the journal's atomic registration.

    The journal reference is the publication point. An unreferenced prepared
    artifact is private and cannot be read through a question endpoint.
    """
    if task.illustration is None or task.illustration.schema_version != 3:
        return
    if task.illustration_artifact_id:
        artifact = read(owner, "artifacts", task.illustration_artifact_id)
        if artifact is None or artifact["content_hash"] != task.illustration.content_hash:
            raise IllustrationError("invalid_contract")
        return
    from .orchestrator import _new_job
    from .layout import CompiledIllustration
    from .preview import render
    mode = "v3" if task.material_contract.schema_version == 3 else "v2"
    job = _new_job(task.material_contract, "required" if task.visual_role == "essential" else "auto",
                   pipeline_mode=mode)
    job.update(question_id=task.question_id, question_revision=task.question_revision,
               status="running", stage="publish_ready")
    artifact = freeze(owner, job, CompiledIllustration(task.illustration, task.diagram_source, []),
        render(task.illustration), contract=task.material_contract,
        reviews={"machine": "passed", **{key: value.model_dump(mode="json")
            for key, value in task.diagram_source.review_evidence.items()}})
    task.illustration_artifact_id = artifact["artifact_id"]
