"""课堂运行健康扫描与恢复动作（plan.md §20.3 / J03）。

从磁盘事实确定性计算七类可观察告警，阈值取 §20.3 初始值：

- ``disk_low``                磁盘可用空间 < 1GB
- ``cloud_auth_failures``     云 TTS 鉴权/配置连续失败 ≥ 5（按 owner）
- ``job_failure_rate``        最近 20 个终态 job 失败率 > 20%
- ``queue_stalled``           queued job 超过 300s 未被调度
- ``renderer_failures``       最近失败连续 3 次均为 renderer_unavailable
- ``damaged_lessons``         lesson.json 损坏（含隔离建议，不自动删）
- ``audio_over_budget``       owner 音频超预算（自动 LRU 之外的压力提示）

扫描只读、不 mkdir；恢复动作经 ``run_cleanup`` 显式触发（当前：
全 owner 过期音频清扫）。告警走管理员视图/日志，不新增外发通知。
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core import classroom_store as store
from ..schemas import classroom as sc
from . import limits

log = logging.getLogger("classroom.health")

# 单次扫描最多读取的 job.json 数量（按 mtime 取最新），防止大库失控。
_JOB_SCAN_CAP = 500
# 单个告警携带的 owner/课程样本上限。
_SAMPLE_CAP = 8

_TERMINAL_STATES = (sc.JobState.succeeded, sc.JobState.failed)
_ACTIVE_STATES = (sc.JobState.queued, sc.JobState.running,
                  sc.JobState.awaiting_outline)


def _iter_owner_dirs() -> list[Path]:
    root = store.classroom_root()
    if not root.is_dir():
        return []
    return sorted(d for d in root.iterdir()
                  if d.is_dir() and not d.name.startswith("."))


def _parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _collect_jobs(owners: list[Path]) -> list[dict]:
    """按 mtime 取最新 _JOB_SCAN_CAP 个 job.json 的关键字段。"""
    candidates: list[tuple[float, Path]] = []
    for owner in owners:
        workspaces = owner / "workspaces"
        if not workspaces.is_dir():
            continue
        for ws in workspaces.iterdir():
            lessons = ws / "lessons"
            if not lessons.is_dir():
                continue
            for lesson in lessons.iterdir():
                jobs_dir = lesson / "jobs"
                if not jobs_dir.is_dir():
                    continue
                for job_dir in jobs_dir.iterdir():
                    meta = job_dir / "job.json"
                    try:
                        candidates.append((meta.stat().st_mtime, meta))
                    except OSError:
                        continue
    candidates.sort(reverse=True)
    jobs: list[dict] = []
    for _, path in candidates[:_JOB_SCAN_CAP]:
        raw = store.read_json(path)
        if not raw:
            continue
        jobs.append({
            "owner_id": raw.get("owner_id") or owner.name,
            "job_id": raw.get("job_id") or "",
            "state": str(raw.get("state") or ""),
            "updated_at": _parse_ts(raw.get("updated_at")),
            "last_error": str(raw.get("last_error") or ""),
            "lesson_id": raw.get("lesson_id") or "",
        })
    return jobs


def scan_alerts() -> dict[str, Any]:
    """确定性健康扫描；只读，返回告警列表与检查摘要。"""
    owners = _iter_owner_dirs()
    alerts: list[dict[str, Any]] = []
    checked: dict[str, Any] = {"owners": len(owners)}

    # -- 磁盘 ------------------------------------------------------------
    free = None
    probe = store.classroom_root()
    if not probe.exists():
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        pass
    checked["disk_free_bytes"] = free
    if free is not None and free < limits.DISK_ALERT_BYTES:
        alerts.append({
            "code": "disk_low", "severity": "critical",
            "detail": f"磁盘可用 {free} 字节低于 "
                      f"{limits.DISK_ALERT_BYTES}；可执行 sweep_audio 清理",
        })

    # -- 云 TTS 鉴权连击 ---------------------------------------------------
    auth_owners: list[str] = []
    for owner in owners:
        record = store.read_json(store.owner_meta_path(owner.name)) or {}
        tts = (record.get("quota") or {}).get("tts") or {}
        if int(tts.get("cloud_auth_fail_streak") or 0) >= \
                limits.CLOUD_AUTH_FAIL_ALERT:
            auth_owners.append(owner.name)
    checked["cloud_auth_fail_owners"] = len(auth_owners)
    if auth_owners:
        alerts.append({
            "code": "cloud_auth_failures", "severity": "critical",
            "detail": "云端 TTS 鉴权/配置连续失败（需检查密钥）",
            "owners": auth_owners[:_SAMPLE_CAP],
        })

    # -- job 失败率 / 队列停滞 / renderer 连击 ------------------------------
    jobs = _collect_jobs(owners)
    checked["jobs_scanned"] = len(jobs)
    terminal = [j for j in jobs
                if j["state"] in (s.value for s in _TERMINAL_STATES)
                and j["updated_at"] is not None]
    terminal.sort(key=lambda j: j["updated_at"], reverse=True)
    window = terminal[:limits.JOB_FAILURE_WINDOW]
    failed_in_window = [j for j in window if j["state"] == "failed"]
    checked["job_failure_rate"] = (
        round(len(failed_in_window) / len(window), 3) if window else 0.0)
    if len(window) >= limits.JOB_FAILURE_MIN_SAMPLES and \
            len(failed_in_window) / len(window) > limits.JOB_FAILURE_RATE_ALERT:
        alerts.append({
            "code": "job_failure_rate", "severity": "critical",
            "detail": f"最近 {len(window)} 个终态 job 中 "
                      f"{len(failed_in_window)} 个失败",
            "sample_jobs": [j["job_id"] for j in
                            failed_in_window[:_SAMPLE_CAP]],
        })

    now = datetime.now(timezone.utc)
    stalled = [j for j in jobs if j["state"] == "queued"
               and j["updated_at"] is not None
               and (now - j["updated_at"]).total_seconds()
               > limits.QUEUE_STALL_SECONDS]
    checked["queue_stalled_jobs"] = len(stalled)
    if stalled:
        alerts.append({
            "code": "queue_stalled", "severity": "warning",
            "detail": f"{len(stalled)} 个排队 job 超过 "
                      f"{limits.QUEUE_STALL_SECONDS}s 未被调度",
            "sample_jobs": [j["job_id"] for j in stalled[:_SAMPLE_CAP]],
        })

    renderer_streak = 0
    for j in sorted((j for j in jobs if j["state"] == "failed"
                     and j["updated_at"] is not None),
                    key=lambda j: j["updated_at"], reverse=True):
        if j["last_error"].startswith("renderer_unavailable"):
            renderer_streak += 1
        else:
            break
    checked["renderer_fail_streak"] = renderer_streak
    if renderer_streak >= limits.RENDERER_FAIL_ALERT:
        alerts.append({
            "code": "renderer_failures", "severity": "critical",
            "detail": f"renderer 连续 {renderer_streak} 次失败；"
                      f"检查 Node/Chromium 与生成产物",
        })

    # -- 损坏课程（JSON 损坏可观察；隔离由运维决定，不自动删） ----------------
    damaged: list[dict[str, str]] = []
    for owner in owners:
        workspaces = owner / "workspaces"
        if not workspaces.is_dir():
            continue
        for ws in workspaces.iterdir():
            index = store.read_index(owner.name, ws.name)
            for lesson_id in (index.get("lessons") or {}):
                try:
                    store.load_lesson(owner.name, ws.name, lesson_id)
                except store.LessonDamagedError:
                    damaged.append({"owner_id": owner.name,
                                    "lesson_id": lesson_id})
    checked["damaged_lessons"] = len(damaged)
    if damaged:
        alerts.append({
            "code": "damaged_lessons", "severity": "warning",
            "detail": "课程元数据损坏（标 damaged 隔离，需人工确认）",
            "lessons": damaged[:_SAMPLE_CAP],
        })

    # -- 音频超预算压力提示 --------------------------------------------------
    from .lifecycle import storage_sizes
    over_budget: list[dict[str, Any]] = []
    budget_bytes = limits.AUDIO_OWNER_MB * 1024 * 1024
    for owner in owners:
        _, audio_bytes = storage_sizes(owner.name)
        if audio_bytes > budget_bytes:
            over_budget.append({"owner_id": owner.name,
                                "audio_bytes": audio_bytes})
    checked["audio_over_budget_owners"] = len(over_budget)
    if over_budget:
        alerts.append({
            "code": "audio_over_budget", "severity": "info",
            "detail": f"owner 音频超过 {limits.AUDIO_OWNER_MB}MB（LRU 已"
                      f"自动淘汰，此处提示持续压力）",
            "owners": over_budget[:_SAMPLE_CAP],
        })

    return {
        "status": "ok",
        "generated_at": store.utcnow().isoformat(),
        "alert_count": len(alerts),
        "alerts": alerts,
        "checked": checked,
    }


def run_cleanup(action: str) -> dict[str, Any]:
    """显式恢复动作（管理员触发）；未知动作报 ValueError。

    - sweep_audio：对全部 owner 清扫过期音频（TTL 外成对删除），
      对应 disk_low 告警的回收动作；不删活跃/新鲜文件。
    """
    if action == "sweep_audio":
        from . import audio as audio_mod
        removed_total = 0
        owners_touched = 0
        for owner in _iter_owner_dirs():
            removed = audio_mod.sweep_expired_audio(owner.name)
            if removed:
                owners_touched += 1
            removed_total += removed
        log.info("classroom_cleanup: sweep_audio removed=%d owners=%d",
                 removed_total, owners_touched)
        return {"status": "ok", "action": "sweep_audio",
                "audio_pairs_removed": removed_total,
                "owners_touched": owners_touched}
    raise ValueError(f"unknown cleanup action: {action}")
