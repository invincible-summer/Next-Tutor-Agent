"""学习证据 journal：每学生唯一事务事实源（plan §6.3–§6.5）。

`students/<sid>.learning_evidence.jsonl` —— 唯一事务 journal。index 与
views 是可删除重建的投影，不属于本模块职责（见 projections.py）。

纪律（§6.5）：
- 持久化短临界区按 student journal key 用 `file_lock`，里面禁止 await；
  LLM 网络调用期间绝不持锁。
- 只有最后一条不完整/校验失败的尾事务可隔离截去；中部损坏必须
  `journal_corrupt` 阻止受影响写入，不得“坏文件当空档案”。
- `generation + last_seq` 是 watermark；generation 切换用于永久删除重写。
- 同一来源版本只有一个当前有效解释；重复投递只有一次效果（由重放语义
  保证：同 (source_id, source_revision) 的新解释替换旧解释的 current 位）。
"""
from __future__ import annotations

import hashlib
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.core.atomic import (append_line_sync, atomic_write_text,
                             fsync_dir, file_lock)
from . import schema as S

# 项目根 students/（与其他 student store 同款解析策略）
_PROJECT_ROOT = Path(__file__).resolve().parents[5]
STUDENTS_DIR = _PROJECT_ROOT / "students"

JOURNAL_SUFFIX = ".learning_evidence.jsonl"

MAX_AUTO_RETRY = 2              # §10.3：自动网络重试最多 2 次额外尝试


class JournalError(RuntimeError):
    """journal 层可见错误（向 service 上抛，不 except-pass）。"""


class JournalCorruptError(JournalError):
    """中部损坏：受影响写入被阻止（§6.5）。"""


class GenerationConflictError(JournalError):
    """expected_generation 与 journal 当前 generation 不一致（§10.2 提交前
    再检查：权限撤销/删除/改版时丢弃旧结果）。"""


def _safe_student(student_id: str) -> str:
    name = Path(str(student_id or "")).name.strip()
    if not name or name.startswith(".") or ".." in name or "/" in name:
        raise JournalError(f"invalid student id: {student_id!r}")
    return name


def journal_path(student_id: str) -> Path:
    return STUDENTS_DIR / f"{_safe_student(student_id)}{JOURNAL_SUFFIX}"


def new_transaction_id() -> str:
    return "tx_" + uuid.uuid4().hex[:20]


def new_generation() -> str:
    return "gen_" + uuid.uuid4().hex[:20]


def new_source_id() -> str:
    return "src_" + uuid.uuid4().hex[:16]


def new_claim_id() -> str:
    return "clm_" + uuid.uuid4().hex[:14]


def new_observation_id() -> str:
    return "obs_" + uuid.uuid4().hex[:14]


def new_judgment_id() -> str:
    return "jdg_" + uuid.uuid4().hex[:14]


def new_interpretation_id() -> str:
    return "itp_" + uuid.uuid4().hex[:14]


def new_job_id() -> str:
    return "job_" + uuid.uuid4().hex[:16]


def new_synthesis_id() -> str:
    return "syn_" + uuid.uuid4().hex[:14]


def new_review_id() -> str:
    return "rev_" + uuid.uuid4().hex[:14]


def answer_fingerprint(text: str) -> str:
    """完整答案指纹（A05）：以规范化全文哈希，不用题干/答案前缀判重。"""
    digest = hashlib.sha256(
        S.canonicalize_text(text).encode("utf-8")).hexdigest()
    return "af_" + digest[:32]


# ---------------------------------------------------------------------------
# 重放状态
# ---------------------------------------------------------------------------

@dataclass
class SourceState:
    receipt: S.SourceReceipt
    availability: str = "available"           # available|archived|deleted
    current_interpretation_id: str = ""
    interpretations: dict[str, dict[str, Any]] = field(default_factory=dict)
    # interpretation_id -> {abstained, revoked, committed_at, job_id}
    revisions: dict[int, dict[str, Any]] = field(default_factory=dict)
    # source_revision -> {canonical_text, reason}


@dataclass
class JobRuntimeState:
    job: S.EvaluationJob
    retry_not_before: str = ""                # retry_wait 退避
    last_error_code: str = ""
    outbox_pending: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class JournalState:
    student_id: str
    generation: str = ""
    last_seq: int = 0
    corrupt: bool = False
    corrupt_detail: str = ""
    sources: dict[str, SourceState] = field(default_factory=dict)
    tasks: dict[str, dict[int, S.TaskSnapshot]] = field(default_factory=dict)
    jobs: dict[str, JobRuntimeState] = field(default_factory=dict)
    judgments: dict[str, S.ConceptJudgment] = field(default_factory=dict)
    # judgment_id -> judgment；当前有效概念判断另由 concept_current 维护
    concept_current: dict[tuple[str, str], str] = field(default_factory=dict)
    # (workspace_id, concept_key) -> judgment_id
    syntheses: dict[str, S.ScopeSynthesis] = field(default_factory=dict)
    reviews: dict[str, S.ReviewRequestRecord] = field(default_factory=dict)
    review_active_by_source: dict[str, str] = field(default_factory=dict)
    review_by_job: dict[str, str] = field(default_factory=dict)
    # job_id -> review_id（R06：复核按本 job 绑定的 review 执行，不再
    # "查同 source 第一条"）
    outbox_unacked: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict)  # (event_id, consumer) -> item
    workspace_scopes: dict[str, str] = field(default_factory=dict)
    # workspace_id -> 最新 scope_revision（scope_changed）
    assessments: dict[str, dict[str, Any]] = field(default_factory=dict)
    # (question_id, question_revision) -> 答前帮助事件（assistance_recorded）
    assistance_by_question: dict[tuple[str, int],
                                 list[S.AssistanceEvent]] = field(
        default_factory=dict)

    @property
    def watermark(self) -> str:
        return f"{self.generation}:{self.last_seq}"


def _apply_op(state: JournalState, op: Any) -> None:
    if isinstance(op, S.OpQuestionRegistered):
        revs = state.tasks.setdefault(op.task.question_id, {})
        revs[op.task.question_revision] = op.task
    elif isinstance(op, S.OpSourceRegistered):
        if op.source.source_id not in state.sources:
            state.sources[op.source.source_id] = SourceState(
                receipt=op.source)
            state.sources[op.source.source_id].revisions[
                op.source.source_revision] = {
                "canonical_text": op.source.canonical_text}
    elif isinstance(op, S.OpSourceRevised):
        src = state.sources.get(op.source_id)
        if src is not None:
            src.receipt.source_revision = op.source_revision
            src.receipt.canonical_text = op.canonical_text
            src.revisions[op.source_revision] = {
                "canonical_text": op.canonical_text, "reason": op.reason}
            # 改版使旧解释失效：待评价，不自动重评（§14.6）
            src.current_interpretation_id = ""
    elif isinstance(op, S.OpJobRequested):
        state.jobs[op.job.job_id] = JobRuntimeState(job=op.job)
    elif isinstance(op, S.OpJobLeased):
        rt = state.jobs.get(op.job_id)
        if rt is not None:
            rt.job.state = S.JobState.RUNNING
            rt.job.lease_token = op.lease_token
            rt.job.lease_expires_at = op.lease_expires_at
            # R17：认领事务原子递增 attempt_count（此前只在认领方内存
            # 副本 +1，重放不增值 → 连续失败恒为 1，MAX_AUTO_RETRY 永真）
            rt.job.attempt_count += 1
    elif isinstance(op, S.OpJobInputPrepared):
        rt = state.jobs.get(op.job_id)
        if rt is not None:
            rt.job.input_hash = op.input_hash
            rt.job.prompt_binding = op.prompt_binding or rt.job.prompt_binding
    elif isinstance(op, S.OpJobSubresultStaged):
        pass  # 子结果暂存由父 job 合并逻辑读取（G3 child groups）
    elif isinstance(op, S.OpJobFailed):
        rt = state.jobs.get(op.job_id)
        if rt is not None:
            rt.last_error_code = op.error_code
            rt.job.attempt_count = op.attempt_count
            rt.job.transport_attempts = op.transport_attempts
            rt.job.error_code = op.error_code
            if op.retryable and op.attempt_count <= MAX_AUTO_RETRY:
                rt.job.state = S.JobState.RETRY_WAIT
                # R17：绝对时刻优先（fail 事务冻结，重启/重放不改值）；
                # 旧 journal 无该字段时按间隔推导保持兼容
                rt.retry_not_before = (op.retry_not_before
                                       or _retry_not_before(
                                           op.retry_after_seconds))
            else:
                rt.job.state = S.JobState.FAILED
    elif isinstance(op, S.OpJobCancelled):
        rt = state.jobs.get(op.job_id)
        if rt is not None:
            rt.job.state = S.JobState.CANCELLED
    elif isinstance(op, S.OpResultCommitted):
        _apply_result_committed(state, op)
    elif isinstance(op, S.OpReviewRequested):
        state.reviews[op.review.review_id] = op.review
        state.review_active_by_source[op.review.source_id] = op.review.review_id
        if op.job_id:
            state.review_by_job[op.job_id] = op.review.review_id
            state.jobs.setdefault(op.job_id, JobRuntimeState(
                job=S.EvaluationJob(
                    job_id=op.job_id, kind=S.JobKind.REVIEW,
                    source_id=op.review.source_id, workspace_id="",
                    scope_revision="", state=S.JobState.QUEUED,
                    priority=S.JobPriority.REVIEW.value)))
    elif isinstance(op, S.OpReviewResolved):
        review = state.reviews.get(op.review_id)
        decision = op.decision.decision
        if review is not None:
            review.decided_kind = decision.value
            review.decided_at = S.utc_now_iso()
            review.resolution_note = op.decision.reason[:600]
            # R06：insufficient 保持待确定（active），其余结案并清
            # active 索引——允许同一来源的新合法异议。
            if decision != S.ReviewDecisionKind.INSUFFICIENT_EVIDENCE:
                review.status = "resolved"
                if state.review_active_by_source.get(
                        review.source_id) == review.review_id:
                    state.review_active_by_source.pop(review.source_id, None)
        # R06：复核 job 随决定进入终态（不再停在 running）
        if op.job_id:
            rt = state.jobs.get(op.job_id)
            if rt is not None and rt.job.state not in (
                    S.JobState.SUCCEEDED, S.JobState.ABSTAINED,
                    S.JobState.FAILED, S.JobState.CANCELLED):
                rt.job.state = (
                    S.JobState.ABSTAINED
                    if decision == S.ReviewDecisionKind.INSUFFICIENT_EVIDENCE
                    else S.JobState.SUCCEEDED)
                rt.job.error_code = ""
        if decision == S.ReviewDecisionKind.UPHOLD:
            pass  # 原解释保持有效（受理时未撤销）
        elif decision == S.ReviewDecisionKind.REVISE:
            # 替代解释的物化由同一事务内先行的 result_committed 完成；
            # 这里只维护指针（存在性由提交顺序保证）。
            if review is not None and op.replacement_interpretation_id:
                src = state.sources.get(review.source_id)
                if src is not None and op.replacement_interpretation_id in \
                        src.interpretations:
                    src.current_interpretation_id = \
                        op.replacement_interpretation_id
        elif decision == S.ReviewDecisionKind.INVALIDATE:
            if review is not None:
                src = state.sources.get(review.source_id)
                if src is not None:
                    src.current_interpretation_id = ""
                    for iid, meta in src.interpretations.items():
                        meta["revoked"] = True
        for item in op.outbox:
            _track_outbox(state, item)
    elif isinstance(op, S.OpReviewDismissed):
        review = state.reviews.get(op.review_id)
        if review is not None:
            review.status = "dismissed"
            if state.review_active_by_source.get(
                    review.source_id) == review.review_id:
                state.review_active_by_source.pop(review.source_id, None)
        if op.job_id:
            rt = state.jobs.get(op.job_id)
            if rt is not None and rt.job.state not in (
                    S.JobState.SUCCEEDED, S.JobState.ABSTAINED,
                    S.JobState.FAILED, S.JobState.CANCELLED):
                rt.job.state = S.JobState.CANCELLED
    elif isinstance(op, S.OpInterpretationRevoked):
        for src in state.sources.values():
            if op.interpretation_id in src.interpretations:
                src.interpretations[op.interpretation_id]["revoked"] = True
                if src.current_interpretation_id == op.interpretation_id:
                    src.current_interpretation_id = ""
        for jid in op.affected_judgment_ids:
            state.judgments.pop(jid, None)
        for key, cur in list(state.concept_current.items()):
            if cur in op.affected_judgment_ids:
                state.concept_current[key] = ""
        # R08：引用失效的综合隐藏正文（保留历史行供审计）
        for sid in op.affected_synthesis_ids:
            syn = state.syntheses.get(sid)
            if syn is not None:
                syn.revoked = True
        for item in op.outbox:
            _track_outbox(state, item)
    elif isinstance(op, S.OpSynthesisCommitted):
        state.syntheses[op.synthesis.synthesis_id] = op.synthesis
        # R01/R08：综合提交与 job 终态原子（§6.2 结果提交行）
        if op.job_id:
            rt = state.jobs.get(op.job_id)
            if rt is not None:
                rt.job.state = S.JobState.SUCCEEDED
                rt.job.error_code = ""
        for judgment in op.restored_judgments:
            state.judgments[judgment.judgment_id] = judgment
            state.concept_current[(judgment.workspace_id,
                                   judgment.concept_ref.key)] =                 judgment.judgment_id
    elif isinstance(op, S.OpScopeChanged):
        state.workspace_scopes[op.workspace_id] = op.scope_revision
        for key in op.affected_concept_keys:
            state.concept_current.pop(
                (op.workspace_id, key), None)
    elif isinstance(op, S.OpSourceArchived):
        src = state.sources.get(op.source_id)
        if src is not None:
            src.availability = "archived"
    elif isinstance(op, S.OpSourceRestored):
        src = state.sources.get(op.source_id)
        if src is not None:
            src.availability = "available"
    elif isinstance(op, S.OpConsumerAck):
        state.outbox_unacked.pop((op.event_id, op.consumer), None)
    elif isinstance(op, S.OpAssessmentSessionChanged):
        state.assessments[op.assessment_id] = op.detail or {}
    elif isinstance(op, S.OpAssistanceRecorded):
        if op.question_ref is not None:
            key = (op.question_ref.question_id,
                   op.question_ref.question_revision)
            state.assistance_by_question.setdefault(key, []).append(
                op.assistance)
    # question_registered/assistance_recorded 在 receipt 字段内聚合；
# OpSourceRegistered 的 assistance 已在 receipt 内。


def _track_outbox(state: JournalState, item: dict[str, Any]) -> None:
    event_id = str(item.get("event_id") or "")
    consumer = str(item.get("consumer") or "")
    if event_id and consumer:
        state.outbox_unacked[(event_id, consumer)] = item


def _apply_result_committed(state: JournalState,
                            op: S.OpResultCommitted) -> None:
    rt = state.jobs.get(op.job_id)
    # 仅判分（interpretation=None 且 abstained=False，MC 受理时先行落盘）
    # 不终结 job——语义 job 仍待提交（§6.4）。
    completes = op.interpretation is not None or op.abstained
    if rt is not None and completes:
        rt.job.state = (S.JobState.ABSTAINED if op.abstained
                        else S.JobState.SUCCEEDED)
        rt.job.error_code = ""
    src = state.sources.get(op.source_id)
    if src is not None and op.task_result is not None:
        # 判分记录（MC 受理时先行落盘 / 开放题语义提交时附带）
        src.interpretations.setdefault("", {})["task_result"] = \
            op.task_result.model_dump()
    if src is not None:
        if op.interpretation is not None and op.interpretation_id:
            src.interpretations[op.interpretation_id] = {
                "abstained": op.abstained,
                "revoked": False,
                "job_id": op.job_id,
                "source_revision": op.source_revision,
                "task_result": (op.task_result.model_dump()
                                if op.task_result else None),
                "continuation": (op.continuation.model_dump()
                                 if op.continuation else None),
                "raw_interpretation": op.interpretation.model_dump(),
                # R10：claim local_id → obs/概念/来源稳定映射
                "observation_map": list(op.observation_map or []),
            }
            # 同一来源版本只有一个当前有效解释（§6.5）
            src.current_interpretation_id = op.interpretation_id
        elif op.interpretation_id:
            src.interpretations[op.interpretation_id] = {
                "abstained": True, "revoked": False, "job_id": op.job_id,
                "source_revision": op.source_revision,
                "task_result": (op.task_result.model_dump()
                                if op.task_result else None),
            }
            src.current_interpretation_id = op.interpretation_id
    for judgment in op.judgments:
        state.judgments[judgment.judgment_id] = judgment
        state.concept_current[(judgment.workspace_id,
                               judgment.concept_ref.key)] = judgment.judgment_id
    for item in op.outbox:
        _track_outbox(state, item)


def _retry_not_before(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc)
            + timedelta(seconds=max(0, seconds))).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# journal 读写
# ---------------------------------------------------------------------------

class EvidenceJournal:
    """每学生 journal 的进程内门面（单 worker，§6.5）。

    所有方法同步短临界区；调用方在 async 上下文中须经 `asyncio.to_thread`
    或在非锁窗口调用——锁内禁止 await 由类型与用法约定保证。
    """

    def __init__(self, student_id: str) -> None:
        self.student_id = student_id
        self._state: JournalState | None = None
        self._load_lock = threading.RLock()

    # -- paths ---------------------------------------------------------
    @property
    def path(self) -> Path:
        return journal_path(self.student_id)

    def index_path(self) -> Path:
        return self.path.with_suffix(".index.json")

    def views_path(self) -> Path:
        p = self.path
        return p.with_name(p.name.replace(JOURNAL_SUFFIX, ".learner_views.json"))

    # -- state ---------------------------------------------------------
    def state(self) -> JournalState:
        if self._state is None:
            with self._load_lock:
                if self._state is None:
                    self._state = self._load()
        return self._state

    def invalidate_cache(self) -> None:
        """外部重写 journal 文件后强制重载（迁移/测试）。"""
        with self._load_lock:
            self._state = None

    def _load(self) -> JournalState:
        state = JournalState(student_id=self.student_id)
        path = self.path
        if not path.exists():
            state.generation = new_generation()
            return state
        raw = path.read_bytes()
        lines = raw.split(b"\n")
        trailing_newline = raw.endswith(b"\n")
        if trailing_newline and lines and lines[-1] == b"":
            lines = lines[:-1]     # 末尾换行产生的空元素不参与行号
        # 尾部不完整行（无换行结尾）可隔离截去（§6.5）
        incomplete_tail = (not trailing_newline) and bool(lines)
        if incomplete_tail:
            lines = lines[:-1]
        good_bytes_end = 0
        corrupt_at: int | None = None
        corrupt_detail = ""
        txs: list[S.JournalTransaction] = []
        offset = 0
        for i, line in enumerate(lines):
            line_len = len(line) + 1  # + 换行
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                offset += line_len
                good_bytes_end = offset
                continue
            try:
                tx = S.JournalTransaction.model_validate_json(text)
                if not tx.verify_checksum():
                    raise ValueError("checksum mismatch")
                txs.append(tx)
            except Exception as exc:  # noqa: BLE001 - 单行损坏定位
                is_tail = (i == len(lines) - 1) and not corrupt_at
                if is_tail:
                    # 仅最后一条完整行损坏：隔离截去（§6.5）
                    break
                corrupt_at = i
                corrupt_detail = f"line {i + 1}: {exc}"
                break
            offset += line_len
            good_bytes_end = offset
        if corrupt_at is not None:
            # 中部损坏：保留已读前缀供诊断，但阻止后续写入
            state.corrupt = True
            state.corrupt_detail = corrupt_detail
            state.generation = txs[0].generation if txs else new_generation()
            state.last_seq = txs[-1].seq if txs else 0
            for tx in txs:
                _apply_tx(state, tx)
            raise JournalCorruptError(
                f"journal corrupt for {self.student_id}: {corrupt_detail}")
        if incomplete_tail or (good_bytes_end < len(raw)):
            self._truncate_to(good_bytes_end)
        if not txs:
            state.generation = new_generation()
            return state
        state.generation = txs[-1].generation
        state.last_seq = txs[-1].seq
        for tx in txs:
            _apply_tx(state, tx)
        return state

    def _truncate_to(self, end: int) -> None:
        with file_lock(self.path):
            with self.path.open("r+b") as f:
                f.truncate(end)
                f.flush()
                import os as _os
                _os.fsync(f.fileno())
            fsync_dir(self.path.parent)

    # -- write ---------------------------------------------------------
    def append(self, operations: list[Any], *,
               expected_generation: str | None = None) -> S.JournalTransaction:
        """原子追加一个完整事务（锁内：检查恢复/分配 seq/写完整行/fsync）。

        LLM 网络调用绝不在本方法内；`expected_generation` 不匹配即抛
        GenerationConflictError（提交前身份/范围再检查，§10.2）。
        """
        if not operations:
            raise JournalError("empty transaction")
        with file_lock(self.path):
            state = self.state()
            if state.corrupt:
                raise JournalCorruptError(
                    f"journal corrupt for {self.student_id}: "
                    f"{state.corrupt_detail}")
            if expected_generation is not None \
                    and expected_generation != state.generation:
                raise GenerationConflictError(
                    f"generation conflict: expected {expected_generation}, "
                    f"journal at {state.generation}")
            tx = S.JournalTransaction(
                generation=state.generation,
                seq=state.last_seq + 1,
                transaction_id=new_transaction_id(),
                created_at=S.utc_now_iso(),
                operations=operations)
            tx.checksum = tx.resolved_checksum()
            line = tx.model_dump_json() + "\n"
            append_line_sync(self.path, line)
            state.last_seq = tx.seq
            _apply_tx(state, tx)
            return tx

    # -- rewrite (permanent deletion) ----------------------------------
    def rewrite(self, keep: Callable[[S.JournalTransaction], bool],
                reason: str,
                transform: Callable[[S.JournalTransaction],
                                    S.JournalTransaction] | None = None,
                ) -> str:
        """带 generation 的永久删除重写（§5.3：物理去除敏感内容与引用副本，
        不是只追加 tombstone）。返回新 generation。调用方负责范围/权限判断。
        R07：transform 允许保留行脱敏（如独立 assessment detach 会话定位）。"""
        with file_lock(self.path):
            state = self.state()
            kept: list[S.JournalTransaction] = []
            for tx in self._iter_raw_transactions():
                if not keep(tx):
                    continue
                if transform is not None:
                    tx = transform(tx)
                kept.append(tx)
            new_gen = new_generation()
            seq = 0
            lines: list[str] = []
            for old in kept:
                seq += 1
                tx = old.model_copy(update={
                    "generation": new_gen, "seq": seq,
                    "transaction_id": new_transaction_id(),
                    "created_at": S.utc_now_iso()})
                tx.checksum = tx.resolved_checksum()
                lines.append(tx.model_dump_json())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, "".join(l + "\n" for l in lines))
            fsync_dir(self.path.parent)
            self._state = JournalState(student_id=self.student_id)
            self._state.generation = new_gen
            self._state.last_seq = seq
            for tx in self._iter_raw_transactions():
                _apply_tx(self._state, tx)
            return new_gen

    def _iter_raw_transactions(self) -> list[S.JournalTransaction]:
        if not self.path.exists():
            return []
        out: list[S.JournalTransaction] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            out.append(S.JournalTransaction.model_validate_json(text))
        return out

    # -- helpers -------------------------------------------------------
    def register_source(self, receipt: S.SourceReceipt,
                        job: S.EvaluationJob | None = None) -> S.JournalTransaction:
        """受理来源（+ 可选 job_requested 同一事务，§6.4）。"""
        ops: list[Any] = [S.OpSourceRegistered(source=receipt)]
        if job is not None:
            ops.append(S.OpJobRequested(job=job))
        return self.append(ops)

    def register_question(self, task: S.TaskSnapshot) -> S.JournalTransaction:
        return self.append([S.OpQuestionRegistered(task=task)])


def _apply_tx(state: JournalState, tx: S.JournalTransaction) -> None:
    for op in tx.operations:
        _apply_op(state, op)


# ---------------------------------------------------------------------------
# 进程级缓存（与 student_model.manager._CACHE 同款纪律；沙箱要重置）
# ---------------------------------------------------------------------------

_JOURNALS: dict[str, EvidenceJournal] = {}
_CACHE_LOCK = threading.Lock()


def get_journal(student_id: str) -> EvidenceJournal:
    key = _safe_student(student_id)
    with _CACHE_LOCK:
        journal = _JOURNALS.get(key)
        if journal is None:
            journal = EvidenceJournal(key)
            _JOURNALS[key] = journal
        return journal


def reset_journal_cache() -> None:
    with _CACHE_LOCK:
        _JOURNALS.clear()


def purge_journal_files(student_id: str) -> bool:
    """账号/证据永久删除：journal + 派生投影文件全部清除，无空目录残留。
    先经 rewrite 取消在途（lifecycle 负责），此处只做物理清除。"""
    path = journal_path(student_id)
    removed = False
    with file_lock(path):
        for p in (path, path.with_suffix(".index.json"),
                  EvidenceJournal(student_id).views_path()):
            try:
                if p.exists():
                    p.unlink()
                    removed = True
            except OSError:
                pass
        reset_journal_cache()
        return removed
