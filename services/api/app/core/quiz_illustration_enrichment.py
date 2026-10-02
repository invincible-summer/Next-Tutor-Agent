"""Two-stage assessment component-diagram enrichment.

CAT questions are frozen and delivered as text first.  This module may add a
diagram compiled from the project-owned component library afterwards, keyed by
question identity.  It never mutates TaskSnapshot, answer, rubric, or question
revision, and it never accepts model-authored SVG.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
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
    normalize_illustration,
)
from app.diagrams.schema import DiagramError

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
MAX_ILLUSTRATION_CALLS = 3

from app.core import paths

_STUDENTS_DIR = paths.bind_storage_path(__name__, "_STUDENTS_DIR", "students")
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


def _recent_diagram_context(student_id: str, question_id: str,
                            question_revision: int, *, limit: int = 12) -> tuple[set[str], set[str]]:
    """Read recent frozen diagrams for adaptive visual diversity.

    The cache is the source of truth for delivered question material.  We use
    only asset IDs and content hashes as generation hints; no private student
    text is sent to the model.  The current question is excluded so retries
    remain cache/idempotency safe.
    """
    current = _key(question_id, question_revision)
    try:
        path = _path(student_id)
        with file_lock(path):
            items = _load_store(student_id).get("items", {})
    except (OSError, RuntimeError, ValueError):
        return set(), set()
    if not isinstance(items, dict):
        return set(), set()
    ordered = sorted(
        ((str(key), value) for key, value in items.items() if isinstance(value, dict)),
        key=lambda item: int(item[1].get("updated_at") or 0), reverse=True)
    asset_ids: set[str] = set()
    hashes: set[str] = set()
    count = 0
    for key, item in ordered:
        if key == current or item.get("status") != "ready":
            continue
        illustration = item.get("illustration")
        if isinstance(illustration, dict) and illustration.get("content_hash"):
            hashes.add(str(illustration["content_hash"]))
        source = item.get("diagram_source")
        if isinstance(source, dict):
            for asset_id in (source.get("asset_versions") or {}):
                if isinstance(asset_id, str):
                    asset_ids.add(asset_id)
            scene_hash = source.get("scene_hash")
            if scene_hash:
                hashes.add(str(scene_hash))
        count += 1
        if count >= limit:
            break
    return asset_ids, hashes


def _scene_variants(scene: dict, *, seed: str) -> list[dict]:
    """Return small layout variants while retaining the LLM's composition."""
    digest = hashlib.sha256(str(seed).encode("utf-8")).digest()
    options = ((6, -4), (-6, 4), (4, 5), (-4, -5), (0, 6))
    start = digest[0] % len(options)
    width = float(scene.get("width") or 640)
    height = float(scene.get("height") or 400)
    variants = []
    for offset in range(len(options)):
        dx, dy = options[(start + offset) % len(options)]
        candidate = copy.deepcopy(scene)
        nodes = candidate.get("nodes", [])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node["x"] = max(4, min(max(4, width - 4),
                                    float(node.get("x", 0)) + dx))
            node["y"] = max(4, min(max(4, height - 4),
                                    float(node.get("y", 0)) + dy))
        variants.append(candidate)
    return variants


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
                  illustration: QuestionIllustration | None = None,
                  diagram_source: dict | None = None) -> None:
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
            "diagram_source": diagram_source,
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


def _audit_payload(raw: str) -> dict[str, Any]:
    data = extract_json_object(raw)
    if not isinstance(data, dict):
        return {"status": "failed", "issues": ["audit_unparseable"]}
    status = str(data.get("status") or "failed").strip().lower()
    if status not in {"passed", "failed"}:
        status = "failed"
    issues = data.get("issues")
    return {
        "status": status,
        "issues": [str(x)[:80] for x in issues[:8]] if isinstance(issues, list) else [],
    }


def _scene_row(data: Any) -> tuple[dict[str, Any] | None, bool]:
    """Extract the one allowed scene row and retain a direct-SVG signal.

    Models occasionally omit the outer ``questions`` wrapper or wrap it in a
    harmless ``result`` object.  Those are protocol variations, not reasons to
    discard a locally retrievable diagram.  A direct ``illustration`` field is
    different: it is the retired model-authored SVG path and must stay rejected.
    """
    direct_svg = False
    candidates: list[Any] = []
    if isinstance(data, dict):
        direct_svg = data.get("illustration") is not None
        rows = data.get("questions")
        if isinstance(rows, list):
            candidates.extend(rows)
        for key in ("result", "data"):
            nested = data.get(key)
            if isinstance(nested, dict):
                direct_svg = direct_svg or nested.get("illustration") is not None
                nested_rows = nested.get("questions")
                if isinstance(nested_rows, list):
                    candidates.extend(nested_rows)
        if isinstance(data.get("diagram_scene"), dict):
            candidates.append(data)
        elif isinstance(data.get("scene"), dict):
            candidates.append({"diagram_scene": data["scene"]})
        elif "nodes" in data:
            candidates.append({"diagram_scene": data})
    elif isinstance(data, list):
        candidates.extend(data)
    for item in candidates:
        if isinstance(item, dict):
            direct_svg = direct_svg or item.get("illustration") is not None
            if "diagram_scene" in item:
                return item, direct_svg
            if "nodes" in item:
                return {"diagram_scene": item}, direct_svg
    return None, direct_svg


def _clean_scene(raw: Any) -> dict[str, Any] | None:
    """Normalize harmless scene naming drift before strict compilation.

    This is deliberately a closed projection.  It accepts common camelCase
    aliases and drops prose/metadata, while custom fragments and arbitrary SVG
    fields remain unavailable to the compiler.
    """
    if not isinstance(raw, dict):
        return None
    scene = raw.get("diagram_scene", raw)
    if not isinstance(scene, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("schema_version", "width", "height", "profile", "alt", "caption"):
        if key in scene:
            out[key] = scene[key]
    out.setdefault("schema_version", 1)
    out.setdefault("width", 640)
    out.setdefault("height", 400)
    out.setdefault("profile", "textbook")
    out["alt"] = str(out.get("alt") or "题目条件示意图")[:600]
    out["caption"] = str(out.get("caption") or "")[:120]

    nodes = scene.get("nodes")
    clean_nodes = []
    if not isinstance(nodes, list):
        return None
    for index, node in enumerate(nodes[:24], 1):
        if not isinstance(node, dict):
            continue
        item = {key: node[key] for key in
                ("id", "asset_id", "version", "x", "y", "scale", "rotation", "params", "label")
                if key in node}
        if not item.get("id"):
            item["id"] = node.get("nodeId") or f"node_{index}"
        if not item.get("asset_id"):
            item["asset_id"] = node.get("assetId") or node.get("asset") or ""
        if "params" not in item and isinstance(node.get("parameters"), dict):
            item["params"] = node["parameters"]
        clean_nodes.append(item)
    out["nodes"] = clean_nodes

    connections = scene.get("connections", [])
    clean_connections = []
    if isinstance(connections, list):
        for connection in connections[:48]:
            if not isinstance(connection, dict):
                continue
            item = {key: connection[key] for key in ("kind", "route", "label") if key in connection}
            start = connection.get("start", connection.get("from"))
            end = connection.get("end", connection.get("to"))
            if isinstance(start, str):
                start = {"node": start}
            if isinstance(end, str):
                end = {"node": end}
            if isinstance(start, dict) and isinstance(end, dict):
                item["start"] = {key: start[key] for key in ("node", "anchor") if key in start}
                item["end"] = {key: end[key] for key in ("node", "anchor") if key in end}
                clean_connections.append(item)
    out["connections"] = clean_connections

    labels = scene.get("labels", [])
    out["labels"] = [
        {key: label[key] for key in ("text", "x", "y", "anchor") if key in label}
        for label in labels[:24] if isinstance(label, dict)
    ] if isinstance(labels, list) else []
    return out


def _audit_messages(task: S.TaskSnapshot, illustration: QuestionIllustration) -> list[dict[str, str]]:
    from app.prompts.registry import get as prompt
    return [
        {"role": "system", "content": prompt("quiz_illustration_enrichment_audit").text},
        {"role": "user", "content": (
            "独立检查补充题图与冻结题目是否一致。图只能帮助理解已有文字条件，不能新增必需条件、"
            "不能改变正确答案、不能泄露待求结论。"
            f"\n冻结题目={json.dumps(_task_payload(task), ensure_ascii=False)}"
            f"\n规范化题图={json.dumps(illustration.model_dump(mode='json'), ensure_ascii=False)}"
            "\n只返回 JSON：通过 => {\"status\":\"passed\",\"issues\":[]}；"
            "不通过 => {\"status\":\"failed\",\"issues\":[...]}。"
            "审查只能给出结论，不能重画、修订或返回任何 SVG。不要输出思维链。"
        )},
    ]


async def _complete(llm: Any, budget: GenerationBudget,
                    messages: list[dict[str, str]], *, timeout: float,
                    max_tokens: int) -> str:
    remaining = budget.remaining_seconds
    if remaining <= FINAL_AUDIT_RESERVE_SECONDS and budget.calls > 0:
        # Keep a small tail buffer so a slow provider cannot consume the whole
        # enrichment deadline. There is no redraw or second SVG path here.
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
        # The component pipeline is mandatory.  It performs the model's
        # declaration call, deterministic local fuzzy retrieval, then a scene
        # composition call that can reference only returned asset IDs.
        return await _generate_component_enrichment(
            student_id=student_id, task=task, policy=policy, model=model,
            budget=budget, review_enabled=review_enabled)
    except (IllustrationValidationError, DiagramError, TimeoutError,
            asyncio.TimeoutError) as exc:
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
        if policy == "auto":
            # Optional diagrams must never turn an otherwise usable frozen
            # question into a generation error.  Do not cache an unexpected
            # provider/runtime failure as "not required": the UI's explicit
            # retry action should be able to make a fresh bounded attempt.
            return {"status": "failed", "illustration": None,
                    "code": "diagram_optional_unavailable",
                    "metrics": budget.summary()}
        return {"status": "failed", "illustration": None,
                "code": "illustration_generation_failed",
                "metrics": budget.summary()}


async def _generate_component_enrichment(*, student_id, task, policy, model, budget, review_enabled):
    from app.diagrams.pipeline import (
        compile_questions,
        declare_and_retrieve,
        fallback_scene,
        scene_contract,
    )
    client = BudgetedLLM(model, budget, call_timeout=GENERATION_CALL_TIMEOUT_SECONDS)
    context = "冻结文字题，仅补充已有条件，不改写题目：" + json.dumps(_task_payload(task), ensure_ascii=False)
    recent_asset_ids, recent_hashes = _recent_diagram_context(
        student_id, task.question_id, task.question_revision)
    bundle = await declare_and_retrieve(client, context=context, policy=policy)
    if policy == "auto" and not any(
            requirement.illustration_needed for requirement in bundle.requirements):
        # A valid local declaration that says the frozen text needs no visual
        # should not pay for a second composition call.
        _write_cached(student_id, task.question_id, task.question_revision,
                      status="not_required")
        return {"status": "not_required", "illustration": None,
                "metrics": budget.summary()}
    local_recovery = False
    try:
        text = await _complete(model, budget, [
            {"role": "system", "content": scene_contract(
                bundle, policy, avoid_asset_ids=recent_asset_ids) +
             '\n冻结题只返回 {"questions":[{"diagram_scene":构图或null}]}；不得输出 illustration、SVG 或改变文字、答案、解析、量规。'},
            {"role": "user", "content": context}],
            timeout=max(.1, min(GENERATION_CALL_TIMEOUT_SECONDS,
                                budget.remaining_seconds-FINAL_AUDIT_RESERVE_SECONDS)), max_tokens=2600)
        data = extract_json_object(text)
    except Exception as exc:
        # Composition is an enrichment phase.  If the provider times out or
        # returns a transport/protocol error after requirements were retrieved,
        # compile the safe local arrangement immediately.  This keeps the
        # frozen text question usable and avoids spending a redraw call.
        logger.warning("diagram scene composition unavailable; using local recovery: %s",
                       type(exc).__name__)
        data = None
        local_recovery = True
    row, direct_svg = _scene_row(data)
    if direct_svg:
        # Preserve the compiler's explicit rejection code for the old direct
        # SVG contract; local recovery must never turn that into acceptance.
        row = row or {"illustration": {"kind": "svg"}}
    else:
        scene = _clean_scene(row.get("diagram_scene") if row else None)
        if scene is None:
            scene = fallback_scene(
                bundle, alt="题目条件示意图", avoid_asset_ids=recent_asset_ids,
                seed=f"{task.question_id}:{task.question_revision}")
            local_recovery = True
        row = {"diagram_scene": scene} if scene is not None else {"diagram_scene": None}
    compiled = compile_questions(
        [row], bundle, policy,
        fallback_avoid_asset_ids=recent_asset_ids,
        fallback_seed=f"{task.question_id}:{task.question_revision}")[0]
    if compiled.get("_diagram_error"):
        raise IllustrationValidationError(compiled["_diagram_error"])

    deduplicated = False
    current_illustration = compiled.get("illustration")
    current_source = compiled.get("diagram_source")
    current_hashes = {
        str(value) for value in (
            current_illustration.get("content_hash") if isinstance(current_illustration, dict) else None,
            current_source.get("scene_hash") if isinstance(current_source, dict) else None,
        ) if value
    }
    if current_hashes & recent_hashes and not direct_svg:
        # Preserve the LLM's semantic composition first.  A small deterministic
        # translation changes only presentation, so repeated CAT questions do
        # not look identical while the physical relationships and parameters
        # remain exactly those selected by the model.
        scene = _clean_scene(row.get("diagram_scene"))
        if scene is not None:
            for variant in _scene_variants(
                    scene, seed=f"{task.question_id}:{task.question_revision}"):
                candidate = compile_questions(
                    [{"diagram_scene": variant}], bundle, policy,
                    fallback_avoid_asset_ids=recent_asset_ids,
                    fallback_seed=f"{task.question_id}:{task.question_revision}")[0]
                if candidate.get("_diagram_error"):
                    continue
                candidate_illustration = candidate.get("illustration")
                candidate_source = candidate.get("diagram_source")
                candidate_hashes = {
                    str(value) for value in (
                        candidate_illustration.get("content_hash")
                        if isinstance(candidate_illustration, dict) else None,
                        candidate_source.get("scene_hash")
                        if isinstance(candidate_source, dict) else None,
                    ) if value
                }
                if not candidate_hashes & recent_hashes:
                    compiled = candidate
                    deduplicated = True
                    break
        # If a translation cannot compile (for example a scene was already at
        # the edge of the canvas), use the deterministic local compositor with
        # alternative authorized assets and a stable seed.
        if not deduplicated:
            recovery = fallback_scene(
                bundle, alt=str(current_illustration.get("alt")
                                if isinstance(current_illustration, dict) else "题目条件示意图"),
                allowed_assets={aid for need in bundle.needs
                                if need.get("question_slot") == "q1"
                                for aid in need.get("candidates", [])},
                avoid_asset_ids=recent_asset_ids,
                seed=f"{task.question_id}:{task.question_revision}")
            if recovery is not None:
                candidate = compile_questions(
                    [{"diagram_scene": recovery}], bundle, policy,
                    fallback_avoid_asset_ids=recent_asset_ids,
                    fallback_seed=f"{task.question_id}:{task.question_revision}")[0]
                if not candidate.get("_diagram_error"):
                    candidate_illustration = candidate.get("illustration")
                    candidate_source = candidate.get("diagram_source")
                    candidate_hashes = {
                        str(value) for value in (
                            candidate_illustration.get("content_hash")
                            if isinstance(candidate_illustration, dict) else None,
                            candidate_source.get("scene_hash")
                            if isinstance(candidate_source, dict) else None,
                        ) if value
                    }
                    if not candidate_hashes & recent_hashes:
                        compiled = candidate
                        deduplicated = True
                        local_recovery = True
    raw = compiled.get("illustration")
    if raw is None:
        if policy == "required":
            raise IllustrationValidationError("illustration_required_missing")
        _write_cached(student_id, task.question_id, task.question_revision, status="not_required")
        return {"status": "not_required", "illustration": None, "metrics": budget.summary()}
    illustration = normalize_illustration(raw)
    if review_enabled:
        text = await _complete(model, budget, _audit_messages(task, illustration),
            timeout=AUDIT_CALL_TIMEOUT_SECONDS, max_tokens=700)
        if _audit_payload(text)["status"] != "passed":
            raise IllustrationValidationError("illustration_audit_failed")
    _write_cached(student_id, task.question_id, task.question_revision, status="ready",
                  illustration=illustration, diagram_source=compiled.get("diagram_source"))
    metrics = budget.summary()
    if local_recovery or compiled.get("diagram_recovery"):
        metrics["diagram_recovery"] = 1
    if deduplicated:
        metrics["diagram_deduplicated"] = 1
    return {"status": "ready", "illustration": illustration, "metrics": metrics}


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
    """Generate/cache one supplemental diagram within a hard 30-second budget."""
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
