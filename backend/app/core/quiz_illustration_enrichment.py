"""Two-stage assessment illustration enrichment.

CAT questions are frozen and delivered as text first.  This module may add a
reviewed *supplemental* diagram afterwards, keyed by question identity.  It
never mutates TaskSnapshot, answer, rubric, or question revision.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from app.agents.student_model.evaluation import schema as S
from app.core.atomic import atomic_write_text, file_lock
from app.core.json_utils import extract_json_object
from app.core.llm_async import get_llm
from app.core.quiz_generation_budget import BudgetedLLM, GenerationBudget
from app.core.quiz_illustration import (
    IllustrationValidationError,
    QuestionIllustration,
    illustration_grammar,
    normalize_illustration,
)

logger = logging.getLogger(__name__)

# Enrichment runs after the text question is already displayed, so this
# deadline no longer sits on the student's critical path.  The old 18s/7s
# pair was tuned for the retired synchronous flow and routinely killed real
# 2-6KiB SVG generations at the 7-second call timeout; the constants below
# keep the ≤3-logical-call contract while letting a real model finish.
ILLUSTRATION_DEADLINE_SECONDS = 30.0
GENERATION_CALL_TIMEOUT_SECONDS = 18.0
AUDIT_CALL_TIMEOUT_SECONDS = 10.0
FINAL_AUDIT_RESERVE_SECONDS = 4.0
REPAIR_MIN_REMAINING_SECONDS = 8.0
MAX_ILLUSTRATION_CALLS = 3

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_STUDENTS_DIR = _PROJECT_ROOT / "students"
_STORE_SUFFIX = ".question_illustrations.json"
# True single-flight registry: concurrent callers for the same frozen question
# await one background task and therefore share both success and failure.  The
# task is shielded from an individual HTTP/client cancellation and removes
# itself on completion, so later explicit retries can start a fresh attempt.
_inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}
_inflight_guard = asyncio.Lock()


def _safe_student(student_id: str) -> str:
    raw = str(student_id or "").strip()
    name = Path(raw).name
    if (not name or raw != name or name.startswith(".") or ".." in name
            or "/" in raw or "\\" in raw):
        raise ValueError("invalid_student_id")
    return name


def _path(student_id: str) -> Path:
    return _STUDENTS_DIR / f"{_safe_student(student_id)}{_STORE_SUFFIX}"


def _key(question_id: str, question_revision: int) -> str:
    return f"{question_id}:{int(question_revision)}"


def _flight_key(student_id: str, question_id: str,
                question_revision: int) -> str:
    return f"{_safe_student(student_id)}:{_key(question_id, question_revision)}"


def _load_store(student_id: str) -> dict[str, Any]:
    path = _path(student_id)
    if not path.exists():
        return {"version": 1, "items": {}}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("illustration_store_corrupt") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        raise RuntimeError("illustration_store_corrupt")
    return data


def _read_cached(student_id: str, question_id: str,
                 question_revision: int) -> dict[str, Any] | None:
    path = _path(student_id)
    with file_lock(path):
        item = _load_store(student_id).get("items", {}).get(
            _key(question_id, question_revision))
    if not isinstance(item, dict):
        return None
    status = str(item.get("status") or "")
    if status == "ready" and isinstance(item.get("illustration"), dict):
        try:
            illustration = QuestionIllustration.model_validate(item["illustration"])
        except Exception:
            return None
        return {"status": "ready", "illustration": illustration}
    if status == "not_required":
        return {"status": "not_required", "illustration": None}
    return None


def get_cached_assessment_illustration(
    student_id: str, question_id: str, question_revision: int,
) -> dict[str, Any] | None:
    """Return an already-decided enrichment without consulting current switches.

    Account/ops switches gate *new* generation. A previously reviewed SVG is
    historical question material and remains readable after the switch is
    turned off, matching the existing frozen-illustration product contract.
    """
    return _read_cached(student_id, question_id, question_revision)


def _write_cached(student_id: str, question_id: str, question_revision: int,
                  *, status: str,
                  illustration: QuestionIllustration | None = None) -> None:
    path = _path(student_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        data = _load_store(student_id)
        items = data.setdefault("items", {})
        items[_key(question_id, question_revision)] = {
            "question_id": question_id,
            "question_revision": int(question_revision),
            "status": status,
            "illustration": illustration.model_dump(mode="json") if illustration else None,
            "updated_at": int(time.time()),
        }
        atomic_write_text(path, json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _task_payload(task: S.TaskSnapshot) -> dict[str, Any]:
    return {
        "question_id": task.question_id,
        "question_revision": task.question_revision,
        "type": task.q_type,
        "stem": task.stem,
        "options": task.options,
        "answer": task.answer,
        "explanation": task.explanation,
    }


def _generated_illustration(raw: str) -> Any:
    data = extract_json_object(raw)
    if not isinstance(data, dict):
        raise IllustrationValidationError("illustration_invalid_schema")
    return data.get("illustration")


def _audit_payload(raw: str) -> dict[str, Any]:
    data = extract_json_object(raw)
    if not isinstance(data, dict):
        return {"status": "failed", "issues": ["audit_unparseable"]}
    status = str(data.get("status") or "failed").strip().lower()
    if status not in {"passed", "repair", "failed"}:
        status = "failed"
    issues = data.get("issues")
    return {
        "status": status,
        "issues": [str(x)[:80] for x in issues[:8]] if isinstance(issues, list) else [],
        "illustration": data.get("illustration"),
    }


_SVG_SKELETON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 240">'
    '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="4" refY="3" '
    'orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="#000"/></marker></defs>'
    '<line x1="40" y1="200" x2="320" y2="200" stroke="#000" stroke-width="2" '
    'marker-end="url(#arrow)"/><text x="40" y="225" font-size="18">F</text></svg>'
)


def _generation_messages(task: S.TaskSnapshot, policy: str) -> list[dict[str, str]]:
    from app.prompts.registry import get as prompt
    system = prompt("quiz_illustration_enrichment").text
    payload = json.dumps(_task_payload(task), ensure_ascii=False)
    if policy == "required":
        # Presenting null as an equal option invites the model to bail out on
        # hard diagrams; under required it is a contract violation, not a choice.
        contract = ("只返回 JSON：{\"illustration\":{\"kind\":\"svg\",\"alt\":\"...\","
                    "\"caption\":\"...\",\"svg\":\"...\"}}。本次必须配图，"
                    "illustration 不允许为 null，必须给出一份可用的 SVG。")
    else:
        contract = ("只返回 JSON：{\"illustration\": null|{\"kind\":\"svg\",\"alt\":\"...\","
                    "\"caption\":\"...\",\"svg\":\"...\"}}。")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            f"illustration_policy={policy}\n"
            "为这道已经冻结的文字题生成补充题图。不得改写题干、选项、答案或解析；"
            "图不得增加文字题中不存在的必需条件，也不得泄露答案。\n"
            f"题目数据：{payload}\n"
            f"允许的 SVG 语法：{illustration_grammar()}\n"
            f"结构参考（只示意骨架与命名空间，内容必须按本题重画，勿照抄）：{_SVG_SKELETON}\n"
            + contract
        )},
    ]


def _repair_messages(task: S.TaskSnapshot, policy: str, raw: Any,
                     issue_code: str) -> list[dict[str, str]]:
    from app.prompts.registry import get as prompt
    return [
        {"role": "system", "content": prompt("quiz_illustration_enrichment").text},
        {"role": "user", "content": (
            f"illustration_policy={policy}\n"
            "上一版题图未通过确定性 SVG 校验。只修复 illustration，不得改题。"
            f"错误码={issue_code}。\n"
            f"冻结题目={json.dumps(_task_payload(task), ensure_ascii=False)}\n"
            f"上一版 illustration={json.dumps(raw, ensure_ascii=False)}\n"
            f"允许的 SVG 语法={illustration_grammar()}\n"
            "输出同样的单个 JSON 对象。"
            + ("本次必须配图：不得返回 null 或放弃，必须重新给出一份能通过上述语法校验的 SVG。"
               if policy == "required" else "")
        )},
    ]


def _audit_messages(task: S.TaskSnapshot, illustration: QuestionIllustration,
                    *, allow_repair: bool) -> list[dict[str, str]]:
    from app.prompts.registry import get as prompt
    return [
        {"role": "system", "content": prompt("quiz_illustration_enrichment_audit").text},
        {"role": "user", "content": (
            "独立检查补充题图与冻结题目是否一致。图只能帮助理解已有文字条件，不能新增必需条件、"
            "不能改变正确答案、不能泄露待求结论。"
            f"\n冻结题目={json.dumps(_task_payload(task), ensure_ascii=False)}"
            f"\n规范化题图={json.dumps(illustration.model_dump(mode='json'), ensure_ascii=False)}"
            f"\nallow_repair={str(allow_repair).lower()}。"
            "\n只返回 JSON：passed => {\"status\":\"passed\",\"issues\":[]}；"
            "失败且 allow_repair=true 可返回 {\"status\":\"repair\",\"issues\":[...],"
            "\"illustration\":{\"kind\":\"svg\",\"alt\":\"...\",\"caption\":\"...\",\"svg\":\"...\"}}"
            "（illustration 只含这四个字段，不要回显 sanitizer_version/content_hash/宽高等服务端字段）；"
            "否则 {\"status\":\"failed\",\"issues\":[...]}。不要输出思维链。"
        )},
    ]


async def _complete(llm: Any, budget: GenerationBudget,
                    messages: list[dict[str, str]], *, timeout: float,
                    max_tokens: int) -> str:
    remaining = budget.remaining_seconds
    if remaining <= FINAL_AUDIT_RESERVE_SECONDS and budget.calls > 0:
        raise TimeoutError("illustration_budget_reserved")
    client = BudgetedLLM(llm, budget, call_timeout=min(timeout, remaining),
                         phase_deadline=budget.deadline)
    content, _usage = await client.complete(
        messages=messages, temperature=0.2, max_tokens=max_tokens,
        disable_thinking=True)
    return str(content or "")


async def _generate_uncached(*, student_id: str, task: S.TaskSnapshot,
                             policy: str, llm: Any | None) -> dict[str, Any]:
    # A cache may have appeared between the public fast-path and single-flight
    # task creation (for example, another request completed just before this
    # task was installed).
    cached = _read_cached(student_id, task.question_id, task.question_revision)
    if cached:
        return {**cached, "metrics": {"generation_calls": 0,
                                      "generation_elapsed_ms": 0, "cache_hit": 1}}

    budget = GenerationBudget(
        max_calls=MAX_ILLUSTRATION_CALLS,
        deadline=time.monotonic() + ILLUSTRATION_DEADLINE_SECONDS,
    )
    model = llm or get_llm("quiz")
    from app.core.quiz_illustration_policy import account_allows_illustration_review
    # The deterministic sanitizer always runs.  The independent semantic audit
    # is the step most likely to time out or false-reject on a real model; the
    # per-student switch skips it entirely (verified-ready, no audit trail).
    review_enabled = account_allows_illustration_review(student_id)
    try:
        raw_text = await _complete(
            model, budget, _generation_messages(task, policy),
            timeout=GENERATION_CALL_TIMEOUT_SECONDS, max_tokens=3200)
        raw = _generated_illustration(raw_text)
        if raw is None:
            if policy == "required":
                raise IllustrationValidationError("illustration_required_missing")
            _write_cached(student_id, task.question_id, task.question_revision,
                          status="not_required")
            return {"status": "not_required", "illustration": None,
                    "metrics": budget.summary()}
        try:
            illustration = normalize_illustration(raw)
        except IllustrationValidationError as exc:
            if (budget.remaining_seconds < REPAIR_MIN_REMAINING_SECONDS
                    or not budget.take_repair()):
                raise
            repaired_text = await _complete(
                model, budget, _repair_messages(task, policy, raw, exc.code),
                timeout=min(GENERATION_CALL_TIMEOUT_SECONDS,
                            budget.remaining_seconds - FINAL_AUDIT_RESERVE_SECONDS),
                max_tokens=3200)
            repaired_raw = _generated_illustration(repaired_text)
            if repaired_raw is None:
                raise IllustrationValidationError("illustration_required_missing")
            illustration = normalize_illustration(repaired_raw)

        if review_enabled:
            if budget.remaining_seconds < FINAL_AUDIT_RESERVE_SECONDS:
                raise TimeoutError("illustration_audit_budget_missing")
            # allow_repair invites a full SVG redraw inside the audit response,
            # which needs a real token budget; a pure verdict stays small.
            can_repair = (budget.calls < budget.max_calls - 1
                          and budget.remaining_seconds >= REPAIR_MIN_REMAINING_SECONDS)
            audit_text = await _complete(
                model, budget, _audit_messages(task, illustration,
                                               allow_repair=can_repair),
                timeout=AUDIT_CALL_TIMEOUT_SECONDS,
                max_tokens=2400 if can_repair else 700)
            audit = _audit_payload(audit_text)
            if audit["status"] == "repair":
                if (budget.calls >= budget.max_calls
                        or budget.remaining_seconds < FINAL_AUDIT_RESERVE_SECONDS
                        or not budget.take_repair()):
                    raise IllustrationValidationError("illustration_audit_failed")
                repaired_raw = audit.get("illustration")
                if isinstance(repaired_raw, dict):
                    # Auditors echo the snapshot they were shown; only the four
                    # model-authored fields may enter re-normalization, otherwise
                    # the snapshot-canonical branch rejects the redrawn SVG.
                    repaired_raw = {key: repaired_raw.get(key)
                                    for key in ("kind", "alt", "caption", "svg")}
                repaired = normalize_illustration(repaired_raw)
                final_text = await _complete(
                    model, budget, _audit_messages(task, repaired, allow_repair=False),
                    timeout=budget.remaining_seconds, max_tokens=700)
                final = _audit_payload(final_text)
                if final["status"] != "passed":
                    raise IllustrationValidationError("illustration_audit_failed")
                illustration = repaired
            elif audit["status"] != "passed":
                raise IllustrationValidationError("illustration_audit_failed")

        _write_cached(student_id, task.question_id, task.question_revision,
                      status="ready", illustration=illustration)
        return {"status": "ready", "illustration": illustration,
                "metrics": budget.summary()}
    except (IllustrationValidationError, TimeoutError, asyncio.TimeoutError) as exc:
        code = getattr(exc, "code", None) or str(exc) or "illustration_generation_failed"
        sanitized = re.sub(r"[^a-z0-9_]+", "_", code.lower())[:80]
        logger.warning(
            "assessment illustration failed student=%s question=%s rev=%s code=%s metrics=%s",
            student_id, task.question_id, task.question_revision, sanitized,
            budget.summary())
        return {"status": "failed", "illustration": None,
                "code": sanitized,
                "metrics": budget.summary()}
    except Exception:
        logger.exception(
            "assessment illustration crashed student=%s question=%s rev=%s",
            student_id, task.question_id, task.question_revision)
        return {"status": "failed", "illustration": None,
                "code": "illustration_generation_failed",
                "metrics": budget.summary()}


async def _run_singleflight(*, flight_key: str, student_id: str,
                            task: S.TaskSnapshot, policy: str,
                            llm: Any | None) -> dict[str, Any]:
    try:
        return await _generate_uncached(
            student_id=student_id, task=task, policy=policy, llm=llm)
    finally:
        current = asyncio.current_task()
        async with _inflight_guard:
            if current is not None and _inflight.get(flight_key) is current:
                _inflight.pop(flight_key, None)


async def generate_assessment_illustration(
    *, student_id: str, task: S.TaskSnapshot, policy: str, llm: Any | None = None,
) -> dict[str, Any]:
    """Generate/cache one supplemental diagram within a hard 18-second budget."""
    if policy == "off":
        return {"status": "not_required", "illustration": None,
                "metrics": {"generation_calls": 0, "generation_elapsed_ms": 0}}
    cached = _read_cached(student_id, task.question_id, task.question_revision)
    if cached:
        return {**cached, "metrics": {"generation_calls": 0,
                                      "generation_elapsed_ms": 0, "cache_hit": 1}}

    flight_key = _flight_key(student_id, task.question_id, task.question_revision)
    async with _inflight_guard:
        shared = _inflight.get(flight_key)
        if shared is None:
            shared = asyncio.create_task(_run_singleflight(
                flight_key=flight_key, student_id=student_id, task=task,
                policy=policy, llm=llm))
            _inflight[flight_key] = shared
    # One browser component may unmount/abort while another view of the same
    # frozen question is already waiting.  Shielding preserves the shared
    # generation/audit and lets it populate cache; it does not keep the HTTP
    # request itself alive.
    return await asyncio.shield(shared)
