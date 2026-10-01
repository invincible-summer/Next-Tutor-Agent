"""§25.4/§26.3 可持久化助手报告（C05）。

简报复用第 11 节 LearningReport 结构，不定义第二套学习指标；报告独立
保存为 report_id（astr_ + hex），默认保留 90 天；删除报告只删除助手
副本与派生内容，不删除原业务证据（§25.4/§26.3）。
"""
from __future__ import annotations

import json
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.assistant_store import _student_root

REPORT_RETENTION_DAYS = 90


def _reports_dir(student_id: str) -> pathlib.Path:
    return _student_root(student_id) / "reports"


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def new_report_id() -> str:
    return "astr_" + uuid.uuid4().hex[:16]


def save_report(student_id: str, payload: dict[str, Any],
                report_id: str = "") -> str:
    """持久化报告并返回 report_id；重复保存同一 id 覆盖内容。"""
    rid = str(report_id or payload.get("report_id") or new_report_id())
    payload = dict(payload)
    payload["report_id"] = rid
    payload.setdefault("created_at", _now().isoformat())
    payload["expires_at"] = (
        _now() + timedelta(days=REPORT_RETENTION_DAYS)).isoformat()
    from app.core.atomic import atomic_write_text
    path = _reports_dir(student_id) / f"{rid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False))
    return rid


def load_report(student_id: str,
                report_id: str) -> dict[str, Any] | None:
    path = _reports_dir(student_id) / f"{report_id}.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if str(report.get("expires_at") or "") < _now().isoformat():
        # 到期报告不再返回正文（保留期清理由 sweep 兜底）。
        return {"report_id": report_id, "expired": True}
    return report


def delete_report(student_id: str, report_id: str) -> bool:
    """删除助手报告与派生副本；不触碰原业务证据。幂等。"""
    path = _reports_dir(student_id) / f"{report_id}.json"
    if not path.exists():
        return False
    path.unlink(missing_ok=True)
    return True


def list_reports(student_id: str, *, limit: int = 20,
                 offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    d = _reports_dir(student_id)
    if not d.exists():
        return [], 0
    items: list[dict[str, Any]] = []
    for p in pathlib.Path(d).glob("*.json"):
        try:
            report = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({"report_id": report.get("report_id") or p.stem,
                      "kind": report.get("kind"),
                      "window": report.get("window"),
                      "created_at": report.get("created_at")})
    items.sort(key=lambda r: str(r.get("created_at")), reverse=True)
    return items[offset:offset + limit], len(items)


def sweep_expired(student_id: str) -> int:
    """清理过期报告（读取路径兜底；返回清理数量）。"""
    d = _reports_dir(student_id)
    if not d.exists():
        return 0
    now_iso = _now().isoformat()
    removed = 0
    for p in pathlib.Path(d).glob("*.json"):
        try:
            report = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(report.get("expires_at") or "") < now_iso:
            p.unlink(missing_ok=True)
            removed += 1
    return removed
