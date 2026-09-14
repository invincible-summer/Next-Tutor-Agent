#!/usr/bin/env python3
"""旧学习数据迁移 CLI（plan §16.2）。

把旧权威文件的可信原始学习事实迁入统一学习证据 journal
（`students/<sid>.learning_evidence.jsonl`），并按 §16.3 清理/改写各
M-子系统的旧派生字段。原则（§16.1）：

- 只迁原始作答/题目/学生自己写的解释/真实时间/当时可验证的归属；
- 不把旧 BKT `p_known`、Bloom 正确率或任何旧能力数值翻译成新结论；
- 默认零 LLM 调用：开放题只注册为待评价状态（§16.4 backfill 是显式
  后续动作，不在本工具内）；
- MC 判分是确定性本地函数（零 LLM），迁移时照常提交 TaskResult，
  其语义作业显式取消（不留永远 pending 的假队列）。

CLI 合同（§16.2）：
    --dry-run
    --apply --manifest <path>
    --verify --manifest <path>
    --cleanup-legacy --manifest <path>
    --rollback --manifest <path>       # 仅离线恢复/演练（§16.6）

manifest 记录基线版本、schema、每用户输入文件 hash、条目/映射数、
不可恢复分类、输出 hash、清理清单、错误与阶段完成标记。日志与
manifest 都不输出答案/题干等私有正文——只有计数、id 与 hash。

备份目录必须在仓库与运行目录之外（默认 ~/edu_agent_migration_backups），
禁止把私有快照纳入 Git。清理只按 manifest 精确路径执行。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

TOOL_VERSION = "1.0.0"
DEMO_SID = "usr_12e410b4e2"
DEFAULT_BACKUP_ROOT = Path.home() / "edu_agent_migration_backups"

# 旧权威输入文件（迁移后按 manifest 精确删除，§16.3）
LEGACY_INPUT_SUFFIXES = (
    ".learning_records.json", ".quiz_recent.json", ".events.jsonl",
    ".assessment.json",
)
# 改写（去旧派生字段）后保留的文件
TRANSFORM_SUFFIXES = (
    ".json", ".prompt_memory.json", ".evaluation.json", ".eval_traces.jsonl",
    ".orchestration.json", ".teaching.json",
)


def _utc(ts: float) -> str:
    return _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc
                                      ).isoformat(timespec="seconds"
                                                  ).replace("+00:00", "Z")


def _now_iso() -> str:
    return _utc(time.time())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def git_baseline() -> dict[str, str]:
    out = {}
    for key, args in (("commit", ["git", "rev-parse", "HEAD"]),
                      ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"])):
        try:
            out[key] = subprocess.run(
                args, cwd=REPO, capture_output=True, text=True,
                check=True).stdout.strip()
        except Exception:
            out[key] = "unknown"
    return out


def _students_dir() -> Path:
    from app.agents.student_model.evaluation.store import STUDENTS_DIR
    return STUDENTS_DIR


def detect_legacy_students() -> list[str]:
    sdir = _students_dir()
    found: set[str] = set()
    if not sdir.exists():
        return []
    for p in sdir.iterdir():
        if not p.is_file():
            continue
        for ext in LEGACY_INPUT_SUFFIXES:
            if p.name.endswith(ext):
                found.add(p.name[: -len(ext)])
                break
    return sorted(found)


def load_session_workspace_map() -> dict[str, str]:
    """从聊天档案建立 session_id → workspace_id（§16.4：当时的归属）。"""
    mapping: dict[str, str] = {}
    chat_dir = REPO / "chat_history"
    if not chat_dir.exists():
        return mapping
    for p in chat_dir.glob("chat_*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            sid, ws = d.get("session_id"), d.get("workspace_id")
            if sid and ws:
                mapping[str(sid)] = str(ws)
        except Exception:
            continue
    return mapping


# ---------------------------------------------------------------------------
# 单条旧记录 → journal 操作
# ---------------------------------------------------------------------------

_QTYPE_MAP = {
    "multiple_choice": "multiple_choice",
    "mc": "multiple_choice",
    "fill_blank": "fill_blank",
    "short_answer": "short_answer",
    "open": "short_answer",
}

# 旧存储常见截断上限：文本长度恰好等于这些值时按“缺全文”上报（§16.3）
_OLD_CAPS = {200, 500, 800, 1000, 2000, 4000}


def _looks_truncated(text: str) -> bool:
    return len(text or "") in _OLD_CAPS


def _stable_suffix(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def build_record_ops(record: dict[str, Any], *, sid: str,
                     sess_ws: dict[str, str], migrated_qids: set[str],
                     migrated_answer_qids: set[str],
                     counters: dict[str, int], errors: list[dict[str, str]],
                     now_iso: str, provenance: str) -> list[Any]:
    """一条旧记录 → [question_registered, (source_registered, ...)]。

    只迁原始材料：题干/选项/标准答案/学生作答/真实时间。concept_refs
    留空——旧 knowledge_point 是自由文本，不能伪称对应当前图概念。
    旧记录没有冻结量规：rubric 由 correct_answer 确定性导出并在迁移时刻
    冻结（frozen_at=迁移时间，不谎称旧时已冻结，§16.4）。
    """
    from app.agents.student_model.evaluation import schema as S

    rid = str(record.get("record_id") or record.get("id") or "")
    stem = str(record.get("stem") or "")
    answer = str(record.get("correct_answer") or "")
    student_answer = str(record.get("student_answer") or "").strip()
    qid = f"lmr_{rid}" if rid else ""
    if not qid or not stem or not answer:
        counters["unrecoverable_record"] = counters.get(
            "unrecoverable_record", 0) + 1
        errors.append({"record_id": rid or "(none)",
                       "reason": "missing_id_or_stem_or_answer"})
        return []
    if qid in migrated_qids:
        counters["duplicate_record_id"] = counters.get(
            "duplicate_record_id", 0) + 1
        return []
    qtype_raw = str(record.get("type") or "short_answer").lower()
    q_type = _QTYPE_MAP.get(qtype_raw)
    if q_type is None:
        counters["unknown_question_type"] = counters.get(
            "unknown_question_type", 0) + 1
        errors.append({"record_id": rid, "reason": f"unknown_type:{qtype_raw}"})
        return []

    created = float(record.get("created_at") or 0.0) or time.time()
    truncated = _looks_truncated(stem) or _looks_truncated(answer)
    if truncated:
        counters["legacy_truncated"] = counters.get("legacy_truncated", 0) + 1

    rubric = [S.FrozenCriterion(
        id="rc_legacy_answer_match",
        description=("迁移冻结：作答与旧记录的 correct_answer 语义一致即满足。"
                     "由旧记录标准答案确定性导出（未含学生作答），迁移时刻冻结，"
                     "非旧时冻结量规（plan §16.4）。"),
        weight=1.0, critical=True)]
    task = S.TaskSnapshot(
        question_id=qid[:96], question_revision=1,
        q_type=S.QuestionType(q_type),
        stem=stem[:4000],
        options={str(k): str(v) for k, v in (record.get("options") or {}).items()},
        answer=answer[:4000],
        explanation=str(record.get("explanation") or "")[:6000],
        rubric=rubric,
        verification=S.TaskVerification(status="unreviewed"),
        concept_refs=[],
        task_family="legacy_learning_record",
        novelty=("legacy_truncated: 旧存储原文可能截断（plan §16.3）"
                 if truncated else ""),
        source_badge="旧学习记录迁移",
        frozen_at=now_iso,
        workspace_id="")
    ops: list[Any] = [S.OpQuestionRegistered(task=task)]
    migrated_qids.add(qid)
    counters["tasks_registered"] = counters.get("tasks_registered", 0) + 1

    if not student_answer:
        counters["unanswered_material_only"] = counters.get(
            "unanswered_material_only", 0) + 1
        return ops
    if str(record.get("source_status") or "active") == "deleted":
        counters["deleted_source_skipped"] = counters.get(
            "deleted_source_skipped", 0) + 1
        return ops
    if qid in migrated_answer_qids:
        counters["duplicate_attempt"] = counters.get("duplicate_attempt", 0) + 1
        return ops

    session_id = str(record.get("session_id") or "")
    workspace = sess_ws.get(session_id, "")     # 不确定 → 留空（unassigned）
    attempt_id = "att_" + _stable_suffix(f"{sid}|{qid}|answer")
    source_id = "src_" + _stable_suffix(f"{sid}|{qid}|source")
    receipt = S.SourceReceipt(
        source_id=source_id, source_revision=1,
        # 旧记录是题卡/测评的作答尝试（attempt 制、带 task_ref）→
        # ASSESSMENT 观察类型（现行 chat quiz 卡也走 ASSESSMENT，§11.4）；
        # DIALOGUE 留给无任务的对话观察，错题本/最近习题投影只认
        # ASSESSMENT 来源，映射成 DIALOGUE 会让迁移作答从投影消失。
        kind=S.SourceKind.ASSESSMENT,
        observed_at=_utc(created),
        workspace_id_at_observation=workspace,
        canonical_text=student_answer[:2_000_000],
        assistance_events=[],
        task_ref=S.QuestionRef(question_id=qid[:96], question_revision=1),
        attempt_id=attempt_id,
        source_session_ref=session_id[:96],
        provenance=S.SourceProvenance(provenance),
        created_at=now_iso)
    ops.append(S.OpSourceRegistered(source=receipt, job_id=""))
    migrated_answer_qids.add(qid)
    counters["sources_registered"] = counters.get("sources_registered", 0) + 1
    if workspace:
        counters["workspace_attributed"] = counters.get(
            "workspace_attributed", 0) + 1
    else:
        counters["workspace_unassigned"] = counters.get(
            "workspace_unassigned", 0) + 1

    if q_type == "multiple_choice":
        # MC 确定性判分（零 LLM）：提交 TaskResult 并显式取消语义作业，
        # 不留永远在途的假 pending（§10.3 / §16.4）。
        from app.agents.student_model.evaluation.grading import grade_mc_task
        job_id = "job_" + uuid.uuid4().hex[:16]
        job = S.EvaluationJob(
            job_id=job_id, kind=S.JobKind.ASSESSMENT_EVALUATION,
            source_id=source_id, source_revision=1,
            workspace_id=workspace, scope_revision="",
            priority=S.JobPriority.BACKFILL.value,
            created_at=now_iso, updated_at=now_iso)
        result = grade_mc_task(task, student_answer, now=now_iso)
        ops.append(S.OpJobRequested(job=job))
        ops.append(S.OpResultCommitted(
            job_id=job_id, source_id=source_id, source_revision=1,
            scope_revision="no_scope", task_result=result))
        ops.append(S.OpJobCancelled(
            job_id=job_id,
            reason="migration: deterministic MC graded locally; "
                   "semantic backfill not run (plan §16.4)"))
        counters["mc_graded"] = counters.get("mc_graded", 0) + 1
    else:
        counters["open_pending_backfill"] = counters.get(
            "open_pending_backfill", 0) + 1
    return ops


# ---------------------------------------------------------------------------
# 文件级改写（去旧派生字段，§16.3）
# ---------------------------------------------------------------------------

def transform_profile(sid: str) -> dict[str, Any]:
    """`<sid>.json`：保留 profile 身份/学段/偏好；删除 mastery/memory
    顶层块与 weak/strong（StudentProfile 当前 schema 白名单往返）。"""
    from app.agents.student_model.store import load_blob, save_blob
    path = _students_dir() / f"{sid}.json"
    dropped: list[str] = []
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        dropped = [k for k in ("mastery", "memory") if k in raw]
        prof = raw.get("profile") or {}
        dropped += [k for k in ("weak_points", "strong_points") if k in prof]
    blob = load_blob(sid)
    save_blob(sid, blob)      # 当前 schema 白名单字段，旧字段自然消失
    return {"transform": "profile", "dropped_fields": dropped}


def transform_prompt_memory(sid: str) -> dict[str, Any]:
    """`<sid>.prompt_memory.json`：保留偏好与会话归属元数据；删除
    current_level 与旧 LLM 压缩的学习能力摘要（§16.3）。"""
    pm_path = _students_dir() / f"{sid}.prompt_memory.json"
    if not pm_path.exists():
        return {"transform": "prompt_memory", "skipped": "absent"}
    data = json.loads(pm_path.read_text(encoding="utf-8"))
    core = dict(data.get("core_profile") or {})
    dropped = []
    if "current_level" in core:
        dropped.append("current_level")
        del core["current_level"]
    if core.get("learning_summary"):
        # 旧值由已删除的 LLM 水平归纳生成；新体系该字段恒空（G4/A11）
        dropped.append("learning_summary(旧LLM归纳)")
        core["learning_summary"] = ""
    core.setdefault("current_level_retired", "")
    core.setdefault("tone_preference", "")
    core.setdefault("explanation_preference", "")
    data["core_profile"] = core
    pm_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    return {"transform": "prompt_memory", "dropped_fields": dropped}


def transform_m7_evaluation(sid: str) -> dict[str, Any]:
    """`<sid>.evaluation.json`：strategies 往返当前模型（去 avg_gain 等旧
    增益字段）；proposals/advisor 保留（行动与系统质量事实，§16.3）。"""
    from app.agents.evaluation.schema import StrategyEffectiveness
    path = _students_dir() / f"{sid}.evaluation.json"
    if not path.exists():
        return {"transform": "evaluation", "skipped": "absent"}
    data = json.loads(path.read_text(encoding="utf-8"))
    before = data.get("strategies") or []
    data["strategies"] = [StrategyEffectiveness.from_dict(s).to_dict()
                          for s in before if isinstance(s, dict)]
    removed = sum(1 for s in before if isinstance(s, dict)
                  and ("avg_gain" in s or "learning_gain" in s))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return {"transform": "evaluation", "strategies": len(data["strategies"]),
            "legacy_gain_fields_removed": removed}


def transform_m7_traces(sid: str) -> dict[str, Any]:
    """`<sid>.eval_traces.jsonl`：逐行往返 TurnTrace（去 before/after_mastery
    与 learning_gain；保留 tokens/steps/duration 等系统质量事实）。"""
    from app.agents.evaluation.schema import TurnTrace
    path = _students_dir() / f"{sid}.eval_traces.jsonl"
    if not path.exists():
        return {"transform": "eval_traces", "skipped": "absent"}
    out_lines, kept, removed = [], 0, 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        t = json.loads(line)
        if any(k in t for k in ("before_mastery", "after_mastery",
                                "learning_gain")):
            removed += 1
        out_lines.append(json.dumps(TurnTrace.from_dict(t).to_dict(),
                                    ensure_ascii=False))
        kept += 1
    path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""),
                    encoding="utf-8")
    return {"transform": "eval_traces", "traces": kept,
            "traces_with_legacy_numerics": removed}


def transform_orchestration(sid: str) -> dict[str, Any]:
    """`<sid>.orchestration.json`：goal_states 往返当前 GoalState 模型（去
    mastered_ratio/mastered_skills 旧字段）；用户计划/勾选/时间原样保留。"""
    from app.agents.learning_orchestration.schema import GoalState
    path = _students_dir() / f"{sid}.orchestration.json"
    if not path.exists():
        return {"transform": "orchestration", "skipped": "absent"}
    data = json.loads(path.read_text(encoding="utf-8"))
    removed = 0
    new_states = []
    for gs in (data.get("goal_states") or []):
        if isinstance(gs, dict) and ("mastered_ratio" in gs
                                     or "mastered_skills" in gs):
            removed += 1
        try:
            new_states.append(GoalState.from_dict(gs).to_dict())
        except Exception:
            removed += 1     # 无法解析的旧分析整条丢弃，不伪造新口径
    data["goal_states"] = new_states
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return {"transform": "orchestration", "goal_states": len(new_states),
            "legacy_state_entries_removed": removed}


def clean_demo_workspaces(sid: str) -> dict[str, Any]:
    """§16.3 末行：demo 工作区的旧派生 public_memory 无法区分原能力断言
    时移除旧派生摘要（置空，可再生），不删除原始对话。"""
    if sid != DEMO_SID:
        return {"transform": "demo_workspaces", "skipped": "not_demo"}
    ws_dir = REPO / "chat_history" / "workspaces"
    cleaned = []
    for p in ws_dir.glob("ws_*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("student_id") != sid:
            continue
        if d.get("public_memory"):
            d["public_memory"] = ""
            p.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                         encoding="utf-8")
            cleaned.append(p.name)
    return {"transform": "demo_workspaces", "cleaned_files": cleaned}


# ---------------------------------------------------------------------------
# 旧 events.jsonl / quiz_recent.json / assessment.json 的对账
# ---------------------------------------------------------------------------

def classify_events(sid: str, counters: dict[str, int]) -> None:
    """events.jsonl：quiz_graded 与已迁移 records 对账（同一批旧权威数据，
    归并计数）；concept_taught/self_report 不成为学习证据（§16.3）。"""
    path = _students_dir() / f"{sid}.events.jsonl"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            counters["event_corrupt_line"] = counters.get(
                "event_corrupt_line", 0) + 1
            continue
        kind = e.get("type") or e.get("kind") or "?"
        if kind == "quiz_graded":
            counters["event_quiz_graded"] = counters.get(
                "event_quiz_graded", 0) + 1
        elif kind in ("concept_taught", "self_report"):
            counters[f"event_{kind}_not_evidence"] = counters.get(
                f"event_{kind}_not_evidence", 0) + 1
        else:
            counters["event_other_kind"] = counters.get(
                "event_other_kind", 0) + 1


def _norm_stem(text: str) -> str:
    """题干归一化（空白折叠）用于 quiz_recent ↔ learning_records 镜像
    对账；两个旧存储的 id 空间不同，只有内容可比。"""
    import re
    return re.sub(r"\s+", "", text or "")


def classify_quiz_recent(sid: str, migrated_qids: set[str],
                         migrated_stems: set[str],
                         counters: dict[str, int]) -> list[dict[str, Any]]:
    """quiz_recent.json：与 learning_records 镜像对账；缺失的原始题目
    按同一路径迁移，然后独立结果真相退役（§16.3 第 5 行）。

    返回需要迁移的旧记录（learning_record 形状）。"""
    path = _students_dir() / f"{sid}.quiz_recent.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        counters["quiz_recent_corrupt"] = counters.get(
            "quiz_recent_corrupt", 0) + 1
        return []
    to_migrate: list[dict[str, Any]] = []
    for item in (data.get("questions") or []):
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id") or "")
        stem = str(item.get("stem") or "")
        if rid and f"lmr_{rid}" in migrated_qids:
            counters["quiz_recent_duplicate"] = counters.get(
                "quiz_recent_duplicate", 0) + 1
            continue
        stem_n = _norm_stem(stem)
        # quiz_recent 是截断镜像（旧存储 stem 截 160 字/答案截 200 字）：
        # 归一化题干互为前缀即同一题，不含新增原始材料。
        if (stem_n and len(stem_n) >= 30
                and any(s.startswith(stem_n) or stem_n.startswith(s)
                        for s in migrated_stems)):
            counters["quiz_recent_duplicate"] = counters.get(
                "quiz_recent_duplicate", 0) + 1
            continue
        counters["quiz_recent_missing_in_records"] = counters.get(
            "quiz_recent_missing_in_records", 0) + 1
        to_migrate.append({
            "record_id": f"qr_{rid}",
            "session_id": str(item.get("session_id") or ""),
            "type": str(item.get("type") or "short_answer"),
            "created_at": item.get("ts") or time.time(),
            "stem": stem,
            "correct_answer": str(item.get("correct_answer") or ""),
            "explanation": str(item.get("explanation") or ""),
            "options": item.get("options") or {},
            "student_answer": str(item.get("student_answer") or ""),
            "source_status": str(item.get("source_status") or "active"),
        })
    return to_migrate


def _assessment_question_to_record(q: dict[str, Any],
                                   results: dict[str, dict[str, Any]],
                                   slot: dict[str, Any]) -> dict[str, Any] | None:
    """旧槽题目 → learning_record 形状（复用同一条迁移路径）。"""
    if not isinstance(q, dict):
        return None
    qid = str(q.get("id") or "")
    stem = str(q.get("stem") or "")
    answer = str(q.get("answer") or "")
    if not qid or not stem or not answer:
        return None
    res = results.get(qid) or {}
    return {
        "record_id": f"as_{qid}",
        "session_id": str(slot.get("session_id") or ""),
        "type": str(q.get("q_type") or "short_answer"),
        "created_at": slot.get("created_at") or time.time(),
        "stem": stem, "correct_answer": answer,
        "explanation": str(q.get("explanation") or ""),
        "options": q.get("options") or {},
        "student_answer": str(res.get("student_answer")
                              or res.get("answer") or ""),
        "source_status": "active",
    }


# ---------------------------------------------------------------------------
# 单学生迁移计划 / 应用
# ---------------------------------------------------------------------------

def _existing_question_ids(state: Any) -> set[str]:
    """journal 中已注册的 question_id（含此前迁移，幂等重入）。"""
    out: set[str] = set()
    for revs in getattr(state, "tasks", {}).values():
        out.update(revs.keys())
    return out


def plan_student(sid: str, sess_ws: dict[str, str], now_iso: str
                 ) -> tuple[dict[str, Any], list[list[Any]]]:
    """计算迁移计划与操作组（不写任何文件）。dry-run 与 apply 共用。

    返回 (manifest 摘要, ops_groups)；ops_groups 每组是一个事务的操作
    列表（组内首 op 一定是 question_registered，用于幂等过滤）。
    """
    from app.agents.student_model.evaluation.store import get_journal

    sdir = _students_dir()
    provenance = ("demo_fixture" if sid == DEMO_SID else "migration")
    input_files: dict[str, str] = {}
    for suffix in LEGACY_INPUT_SUFFIXES:
        p = sdir / f"{sid}{suffix}"
        if p.exists():
            input_files[p.name] = sha256_file(p)

    counters: dict[str, int] = {}
    errors: list[dict[str, str]] = []
    migrated_qids: set[str] = set()
    migrated_answer_qids: set[str] = set()
    migrated_stems: set[str] = set()
    ops_groups: list[list[Any]] = []

    def _add(ops: list[Any], record: dict[str, Any] | None = None) -> None:
        if ops:
            ops_groups.append(ops)
        if record is not None:
            stem = _norm_stem(str(record.get("stem") or ""))
            if stem:
                migrated_stems.add(stem)

    lr_path = sdir / f"{sid}.learning_records.json"
    if lr_path.exists():
        try:
            records = json.loads(lr_path.read_text(encoding="utf-8")
                                 ).get("records") or []
            counters["learning_records_input"] = len(records)
        except Exception:
            records = []
            counters["learning_records_corrupt"] = 1
            errors.append({"record_id": "(learning_records)",
                           "reason": "corrupt_json"})
        for record in records:
            _add(build_record_ops(
                record, sid=sid, sess_ws=sess_ws, migrated_qids=migrated_qids,
                migrated_answer_qids=migrated_answer_qids,
                counters=counters, errors=errors, now_iso=now_iso,
                provenance=provenance), record)

    # 旧单槽 assessment.json：可恢复题目/作答走同一路径；旧算法实例
    # 状态不迁移，assessment_id 记录稳定映射（§16.3 第 4 行）。
    as_path = sdir / f"{sid}.assessment.json"
    if as_path.exists():
        try:
            slot = json.loads(as_path.read_text(encoding="utf-8"))
            old_id = str(slot.get("assessment_id") or "")
            counters["assessment_slot_mapped"] = counters.get(
                "assessment_slot_mapped", 0) + (1 if old_id else 0)
            counters["assessment_slot_terminated"] = counters.get(
                "assessment_slot_terminated", 0) + 1
            results_by_qid = {
                str((r or {}).get("question_id")
                    or (r or {}).get("id") or ""): r or {}
                for r in (slot.get("results") or [])}
            for q in (slot.get("questions") or []):
                record = _assessment_question_to_record(
                    q, results_by_qid, slot)
                if record is None:
                    counters["assessment_slot_question_unrecoverable"] = \
                        counters.get(
                            "assessment_slot_question_unrecoverable", 0) + 1
                    continue
                _add(build_record_ops(
                    record, sid=sid, sess_ws=sess_ws,
                    migrated_qids=migrated_qids,
                    migrated_answer_qids=migrated_answer_qids,
                    counters=counters, errors=errors, now_iso=now_iso,
                    provenance=provenance), record)
        except Exception:
            counters["assessment_slot_corrupt"] = counters.get(
                "assessment_slot_corrupt", 0) + 1
            errors.append({"record_id": "(assessment)",
                           "reason": "corrupt_json"})

    classify_events(sid, counters)
    for record in classify_quiz_recent(sid, migrated_qids, migrated_stems,
                                       counters):
        _add(build_record_ops(
            record, sid=sid, sess_ws=sess_ws, migrated_qids=migrated_qids,
            migrated_answer_qids=migrated_answer_qids, counters=counters,
            errors=errors, now_iso=now_iso, provenance=provenance), record)

    journal = get_journal(sid)
    state = journal.state()          # 同时校验旧 journal 可加载
    existing = _existing_question_ids(state)
    fresh_groups = [g for g in ops_groups
                    if g[0].task.question_id not in existing]
    skipped = len(ops_groups) - len(fresh_groups)

    plan = {
        "student_id": sid,
        "provenance": provenance,
        "input_files": input_files,
        "journal_path": str(journal.path),
        "journal_preexisting": journal.path.exists(),
        "plan": {
            "transactions_to_append": len(fresh_groups),
            "tasks": sum(1 for g in fresh_groups if g[0].op == "question_registered"),
            "sources": sum(1 for g in fresh_groups
                           for op in g if op.op == "source_registered"),
            "skipped_already_registered": skipped,
        },
        "counters": counters,
        "errors": errors[:200],
        "cleanup_paths": [str(sdir / f"{sid}{s}")
                          for s in LEGACY_INPUT_SUFFIXES
                          if (sdir / f"{sid}{s}").exists()],
        "transforms": {
            "profile": (sdir / f"{sid}.json").exists(),
            "prompt_memory": (sdir / f"{sid}.prompt_memory.json").exists(),
            "evaluation": (sdir / f"{sid}.evaluation.json").exists(),
            "eval_traces": (sdir / f"{sid}.eval_traces.jsonl").exists(),
            "orchestration": (sdir / f"{sid}.orchestration.json").exists(),
            "teaching_verify_only": (sdir / f"{sid}.teaching.json").exists(),
            "demo_workspaces": sid == DEMO_SID,
        },
    }
    return plan, fresh_groups


def apply_student(plan: dict[str, Any], ops_groups: list[list[Any]],
                  backup_dir: Path) -> dict[str, Any]:
    """执行计划：先备份全部受影响文件（仓库/运行目录之外），再追加
    journal 事务与文件改写，返回 post-hash 供 verify。"""
    from app.agents.student_model.evaluation.store import get_journal

    sid = plan["student_id"]
    sdir = _students_dir()
    sid_backup = backup_dir / sid
    sid_backup.mkdir(parents=True, exist_ok=True)

    # 1) 备份：旧输入 + 被改写文件 + 旧 journal（若已存在）
    files_to_backup: list[Path] = []
    for suffix in LEGACY_INPUT_SUFFIXES + TRANSFORM_SUFFIXES:
        p = sdir / f"{sid}{suffix}"
        if p.exists() and p.is_file():
            files_to_backup.append(p)
    journal_path = Path(plan["journal_path"])
    if journal_path.exists():
        files_to_backup.append(journal_path)
    if sid == DEMO_SID:
        for p in (REPO / "chat_history" / "workspaces").glob("ws_*.json"):
            try:
                if json.loads(p.read_text(encoding="utf-8")
                              ).get("student_id") == sid:
                    files_to_backup.append(p)
            except Exception:
                pass
    backup_manifest: dict[str, str] = {}
    for p in files_to_backup:
        dest = sid_backup / p.name
        if not dest.exists():
            shutil.copy2(p, dest)
        backup_manifest[p.name] = sha256_file(dest)

    # 2) journal 追加（同一 validator/锁路径，§16.4）
    journal = get_journal(sid)
    journal.invalidate_cache()
    for ops in ops_groups:
        journal.append(ops)

    # 3) 文件改写
    transform_results: dict[str, Any] = {}
    if plan["transforms"].get("profile"):
        transform_results["profile"] = transform_profile(sid)
    if plan["transforms"].get("prompt_memory"):
        transform_results["prompt_memory"] = transform_prompt_memory(sid)
    if plan["transforms"].get("evaluation"):
        transform_results["evaluation"] = transform_m7_evaluation(sid)
    if plan["transforms"].get("eval_traces"):
        transform_results["eval_traces"] = transform_m7_traces(sid)
    if plan["transforms"].get("orchestration"):
        transform_results["orchestration"] = transform_orchestration(sid)
    if plan["transforms"].get("demo_workspaces"):
        transform_results["demo_workspaces"] = clean_demo_workspaces(sid)

    # 4) post-hash 只覆盖 apply 后仍存在的改写文件；旧输入文件在
    # cleanup 阶段按清单删除，其完整性由 manifest 的 input_files hash 负责。
    post_hashes: dict[str, str] = {}
    touched_paths: dict[str, str] = {}
    for p in files_to_backup:
        if any(p.name == f"{sid}{s}" for s in LEGACY_INPUT_SUFFIXES):
            continue
        post_hashes[p.name] = sha256_file(p)
        touched_paths[p.name] = str(p)
    journal.invalidate_cache()
    state = journal.state()
    return {
        "backup_dir": str(sid_backup),
        "backup_files": backup_manifest,
        "touched_paths": touched_paths,
        "transactions_appended": len(ops_groups),
        "journal_post_hash": (sha256_file(journal.path)
                              if journal.path.exists() else ""),
        "journal_sources": len(state.sources),
        "post_hashes": post_hashes,
        "transform_results": transform_results,
    }


# ---------------------------------------------------------------------------
# manifest 与五个阶段
# ---------------------------------------------------------------------------

def new_manifest(students: list[str]) -> dict[str, Any]:
    from app.agents.student_model.evaluation import schema as S
    return {
        "tool_version": TOOL_VERSION,
        "baseline": git_baseline(),
        "journal_schema_version": int(S.SCHEMA_VERSION),
        "created_at": _now_iso(),
        "students_requested": students,
        "students": {},
        "errors": [],
        "phases": {"dry_run": False, "apply": False, "verify": False,
                   "cleanup_legacy": False, "rollback": False},
        "phase_timestamps": {},
    }


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_outside_repo(path: Path, what: str) -> None:
    resolved = path.resolve()
    repo = REPO.resolve()
    if resolved == repo or repo in resolved.parents:
        raise SystemExit(f"{what} 不得位于仓库目录内: {resolved}")


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    _assert_outside_repo(path, "manifest")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def cmd_dry_run(args) -> int:
    students = args.students or detect_legacy_students()
    if not students:
        print("no legacy input files found under students/ — nothing to do")
        return 0
    sess_ws = load_session_workspace_map()
    now_iso = _now_iso()
    manifest = new_manifest(students)
    manifest["phases"]["dry_run"] = True
    manifest["phase_timestamps"]["dry_run"] = now_iso
    print(f"dry-run over {len(students)} student(s); "
          f"session→workspace map: {len(sess_ws)} entries")
    for sid in students:
        plan, _ops = plan_student(sid, sess_ws, now_iso)
        manifest["students"][sid] = plan
        p = plan["plan"]
        print(f"  {sid}: txs={p['transactions_to_append']} "
              f"tasks={p['tasks']} sources={p['sources']} "
              f"skipped_registered={p['skipped_already_registered']} "
              f"cleanup={len(plan['cleanup_paths'])} "
              f"errors={len(plan['errors'])}")
        for key in sorted(plan["counters"]):
            print(f"    {key}: {plan['counters'][key]}")
    if args.manifest:
        save_manifest(Path(args.manifest), manifest)
        print(f"dry-run manifest → {args.manifest}")
    return 0


def cmd_apply(args) -> int:
    students = args.students or detect_legacy_students()
    if not students:
        print("no legacy input files found — nothing to apply")
        return 0
    backup_root = Path(args.backup_root or DEFAULT_BACKUP_ROOT).expanduser()
    backup_root.mkdir(parents=True, exist_ok=True)
    _assert_outside_repo(backup_root, "backup root")
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = backup_root / f"mig_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    manifest_path = Path(args.manifest)
    sess_ws = load_session_workspace_map()
    now_iso = _now_iso()
    manifest = new_manifest(students)
    total_errs = 0
    for sid in students:
        plan, ops_groups = plan_student(sid, sess_ws, now_iso)
        result = apply_student(plan, ops_groups, backup_dir)
        manifest["students"][sid] = {**plan, "apply": result}
        total_errs += len(plan["errors"])
        print(f"  applied {sid}: txs={result['transactions_appended']} "
              f"sources_now={result['journal_sources']} "
              f"backup={result['backup_dir']}")
    manifest["phases"]["apply"] = True
    manifest["phase_timestamps"]["apply"] = _now_iso()
    manifest["backup_dir"] = str(backup_dir)
    manifest["errors"] = [e for sid in students
                          for e in manifest["students"][sid]["errors"]]
    save_manifest(manifest_path, manifest)
    print(f"apply complete; manifest → {manifest_path} "
          f"(reported issues: {total_errs})")
    print("run --verify next; --cleanup-legacy only after verify passes")
    # G7 验收发现：journal 状态在长驻服务内进程级缓存，外部重写文件后
    # 不会自动失效——apply/rollback 后必须重启在线服务（或另行调用
    # invalidate_cache），否则 /quiz/recent 等投影继续返回旧状态。
    print("NOTE: restart any long-running backend services now — "
          "they cache journal state in-process and will not see "
          "the migrated journal until restarted")
    return 0


def cmd_verify(args) -> int:
    from app.agents.student_model.evaluation.store import get_journal
    manifest = load_manifest(Path(args.manifest))
    failures: list[str] = []
    for sid, entry in manifest["students"].items():
        apply_info = entry.get("apply") or {}
        journal_path = Path(entry["journal_path"])
        if apply_info.get("transactions_appended"):
            if not journal_path.exists():
                failures.append(f"{sid}: journal missing after apply")
            elif (sha256_file(journal_path)
                    != apply_info.get("journal_post_hash")):
                failures.append(f"{sid}: journal hash drift")
        for name, expected in (apply_info.get("post_hashes") or {}).items():
            p = Path((apply_info.get("touched_paths") or {}).get(name, "")
                     or journal_path.parent / name)
            if not p.exists():
                failures.append(f"{sid}: transformed file missing: {name}")
            elif sha256_file(p) != expected:
                failures.append(f"{sid}: hash drift on {name}")
        if "journal_sources" in apply_info:
            st = get_journal(sid).state()
            if len(st.sources) != apply_info["journal_sources"]:
                failures.append(
                    f"{sid}: source count drift "
                    f"{len(st.sources)} != {apply_info['journal_sources']}")
        if manifest["phases"].get("cleanup_legacy"):
            for path in entry.get("cleanup_paths", []):
                if Path(path).exists():
                    failures.append(f"{sid}: legacy file still present: {path}")
    if failures:
        print(f"VERIFY FAILED ({len(failures)}):")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    manifest["phases"]["verify"] = True
    manifest["phase_timestamps"]["verify"] = _now_iso()
    save_manifest(Path(args.manifest), manifest)
    print("verify OK: journal/transform hashes and counts all match manifest")
    return 0


def cmd_cleanup_legacy(args) -> int:
    manifest = load_manifest(Path(args.manifest))
    if not manifest["phases"].get("verify"):
        raise SystemExit("--cleanup-legacy 要求先通过 --verify（§16.6 顺序）")
    removed: list[str] = []
    sdir = _students_dir()
    for sid, entry in manifest["students"].items():
        for path_str in entry.get("cleanup_paths", []):
            p = Path(path_str)
            if p.exists():
                # 只删 manifest 精确列出的旧输入文件（§16.2：禁止宽泛删除）
                if p.parent == sdir and p.name.startswith(sid + ".") and any(
                        p.name == f"{sid}{s}" for s in LEGACY_INPUT_SUFFIXES):
                    p.unlink()
                    removed.append(p.name)
                else:
                    raise SystemExit(f"cleanup 拒绝不安全路径: {p}")
    manifest["phases"]["cleanup_legacy"] = True
    manifest["phase_timestamps"]["cleanup_legacy"] = _now_iso()
    manifest["cleanup_removed"] = removed
    save_manifest(Path(args.manifest), manifest)
    print(f"cleanup-legacy removed {len(removed)} file(s): "
          + ", ".join(removed))
    return 0


def cmd_rollback(args) -> int:
    manifest = load_manifest(Path(args.manifest))
    if not manifest["phases"].get("apply"):
        raise SystemExit("manifest 未标记 apply，无需回滚")
    backup_dir = Path(manifest["backup_dir"])
    if not backup_dir.exists():
        raise SystemExit(f"备份目录不存在: {backup_dir}")
    print("WARNING: rollback 仅用于离线恢复/演练（§16.6）。若 apply 后已有"
          "新写入，应先安全导出新增 journal 再操作。")
    restored: list[str] = []
    for sid, entry in manifest["students"].items():
        sid_backup = backup_dir / sid
        apply_info = entry.get("apply") or {}
        touched = apply_info.get("touched_paths") or {}
        if sid_backup.exists():
            for src in sorted(sid_backup.iterdir()):
                dest = Path(touched.get(src.name, "")
                            or _students_dir() / src.name)
                shutil.copy2(src, dest)
                restored.append(str(dest))
        # journal：apply 前不存在 → 迁移新增的 journal 直接删除
        journal_path = Path(entry["journal_path"])
        if not entry.get("journal_preexisting") and journal_path.exists():
            journal_path.unlink()
            restored.append("(removed) " + journal_path.name)
    manifest["phases"]["rollback"] = True
    manifest["phase_timestamps"]["rollback"] = _now_iso()
    manifest["rollback_restored"] = restored
    save_manifest(Path(args.manifest), manifest)
    print(f"rollback restored {len(restored)} item(s) from {backup_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="旧学习数据 → 统一学习证据 journal 迁移（plan §16）")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--verify", action="store_true")
    modes.add_argument("--cleanup-legacy", action="store_true")
    modes.add_argument("--rollback", action="store_true")
    parser.add_argument("--manifest", help="manifest 路径（仓库之外）")
    parser.add_argument("--students", nargs="*",
                        help="要迁移的学生 id（默认自动探测旧输入文件）")
    parser.add_argument("--backup-root",
                        help=f"备份根目录（默认 {DEFAULT_BACKUP_ROOT}，"
                             "必须在仓库与运行目录之外）")
    args = parser.parse_args()
    for mode in ("apply", "verify", "cleanup_legacy", "rollback"):
        if getattr(args, mode.replace("-", "_")) and not args.manifest:
            parser.error(f"--{mode} 需要 --manifest <path>")

    if args.dry_run:
        return cmd_dry_run(args)
    if args.apply:
        return cmd_apply(args)
    if args.verify:
        return cmd_verify(args)
    if getattr(args, "cleanup_legacy"):
        return cmd_cleanup_legacy(args)
    return cmd_rollback(args)


if __name__ == "__main__":
    sys.exit(main())
