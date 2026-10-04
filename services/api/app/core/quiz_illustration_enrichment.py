"""Two-stage assessment component-diagram enrichment.

CAT questions are frozen and delivered as text first.  This module may add a
diagram compiled from the project-owned component library afterwards, keyed by
question identity.  It never mutates TaskSnapshot, answer, rubric, or question
revision, and it never accepts model-authored SVG.
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
    normalize_illustration,
)
from app.diagrams.schema import DiagramError

logger = logging.getLogger(__name__)

# Text is already displayed. Reserve room for bounded scene corrections and
# independent re-audits instead of requiring another browser request. The
# ordinary successful path remains declaration + composition + audit.
ILLUSTRATION_DEADLINE_SECONDS = 90.0
GENERATION_CALL_TIMEOUT_SECONDS = 30.0
AUDIT_CALL_TIMEOUT_SECONDS = 20.0
FINAL_AUDIT_RESERVE_SECONDS = 10.0
MAX_ILLUSTRATION_CALLS = 8
MAX_ILLUSTRATION_REPAIRS = 2

_REPAIRABLE_SCENE_CODES = {
    "diagram_invalid_scene", "diagram_missing_fact_binding", "diagram_unknown_parameter",
    "diagram_invalid_parameter", "diagram_data_required", "diagram_component_out_of_bounds",
    "diagram_label_out_of_bounds", "diagram_rotation_not_allowed", "diagram_anchor_missing",
    "diagram_empty_connection", "diagram_asset_not_retrieved", "diagram_asset_version_missing",
    "diagram_invalid_function", "diagram_invalid_range",
    "diagram_condition_mismatch", "diagram_connection_blocked",
}
_AUDIT_ISSUE_CODES = {
    "scientific_mismatch", "condition_added", "answer_leak", "missing_relation", "missing_subject",
    "unreadable_label", "wrong_label", "label_position", "geometry_mismatch", "data_mismatch",
    "direction_mismatch", "audit_unreviewed", "audit_unparseable",
    "submersion_mismatch", "connection_crosses_component", "unsupported_reading",
    "connection_crosses_connection",
}
_AUDIT_WARNING_CODES = {
    "cosmetic_layout", "label_style", "unneeded_annotation", "missing_optional_operation",
}

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


def _task_payload(task: S.TaskSnapshot, guidance: str = "") -> dict[str, Any]:
    """Student-visible question material for requirements and composition."""
    payload = {
        "question_id": task.question_id,
        "question_revision": task.question_revision,
        "type": task.q_type,
        "stem": task.stem,
        "options": task.options,
    }
    if guidance.strip():
        payload["illustration_guidance"] = guidance.strip()[:1200]
    return payload


def _authoring_gold(task: S.TaskSnapshot) -> dict[str, Any]:
    """Private grading material is available only to the independent audit."""
    return {
        "answer": task.answer,
        "explanation": task.explanation,
        "rubric": [criterion.model_dump(mode="json") for criterion in task.rubric],
    }


def _audit_payload(raw: str) -> dict[str, Any]:
    data = extract_json_object(raw)
    if not isinstance(data, dict):
        return {"status": "failed", "issues": ["audit_unparseable"]}
    status = str(data.get("status") or "failed").strip().lower()
    if status not in {"passed", "failed"}:
        status = "failed"
    issues = data.get("issues")
    if not isinstance(issues, list):
        return {"status": "failed", "issues": ["audit_unparseable"]}
    # The issues field holds failure codes, separate from warnings. A
    # contradictory passed result cannot publish or populate the cache.
    if issues:
        status = "failed"
    warnings = data.get("warnings", [])
    warnings = warnings[:8] if isinstance(warnings, list) else []
    # These codes describe presentation only. Unknown prose and all critical
    # issues remain failures, even if the model also calls them warnings.
    if status == "failed" and not issues and warnings and all(
            isinstance(code, str) and code in _AUDIT_WARNING_CODES for code in warnings):
        status = "passed"
    return {
        "status": status,
        "issues": [str(x)[:80] for x in issues[:8]] if isinstance(issues, list) else [],
        "warnings": [code for code in warnings if isinstance(code, str) and code in _AUDIT_WARNING_CODES],
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
    if any(key in scene for key in ("fragments", "svg", "illustration")):
        raise DiagramError("diagram_model_svg_forbidden")
    out: dict[str, Any] = {}
    for key in ("schema_version", "width", "height", "profile", "alt", "caption", "layout_relations"):
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


def _audit_messages(task: S.TaskSnapshot, illustration: QuestionIllustration, *,
                    png: bytes | None = None, geometry=None) -> list[dict[str, Any]]:
    from app.prompts.registry import get as prompt
    messages = [
        {"role": "system", "content": prompt("quiz_illustration_enrichment_audit").text},
        {"role": "user", "content": (
            "独立检查补充题图与冻结题目是否一致。图只能帮助理解已有文字条件，不能新增必需条件、"
            "不能改变正确答案、不能泄露待求结论。"
            f"\n冻结题目={json.dumps(_task_payload(task), ensure_ascii=False)}"
            f"\n审查专用 authoring_gold={json.dumps(_authoring_gold(task), ensure_ascii=False)}"
            f"\n规范化题图={json.dumps(illustration.model_dump(mode='json'), ensure_ascii=False)}"
            "\n只返回 JSON：通过 => {\"status\":\"passed\",\"issues\":[],\"warnings\":[]}；"
            "不通过 => {\"status\":\"failed\",\"issues\":[...],\"warnings\":[]}。"
            "审查只能给出结论，不能重画、修订或返回任何 SVG。不要输出思维链。"
        )},
    ]
    if geometry is not None:
        messages[1]["content"] += "\n实际解析几何=" + json.dumps(geometry, ensure_ascii=False)
    if png is not None:
        from app.illustration.preview import image_message
        messages[1]["content"] = [{"type": "text", "text": messages[1]["content"]}, image_message(png)]
    return messages


async def _complete(llm: Any, budget: GenerationBudget,
                    messages: list[dict[str, Any]], *, timeout: float,
                    max_tokens: int, reserve_seconds: float = 0.0) -> str:
    remaining = budget.remaining_seconds - reserve_seconds
    if remaining <= 0:
        raise TimeoutError("illustration_budget_reserved")
    client = BudgetedLLM(llm, budget, call_timeout=min(timeout, remaining),
                         phase_deadline=budget.deadline)
    content, _usage = await client.complete(
        messages=messages, temperature=0.2, max_tokens=max_tokens,
        disable_thinking=True)
    return str(content or "")


async def _generate_uncached(*, student_id: str, task: S.TaskSnapshot,
                             policy: str, llm: Any | None,
                             guidance: str = "") -> dict[str, Any]:
    # A cache may have appeared between the public fast-path and single-flight
    # task creation (for example, another request completed just before this
    # task was installed).
    cached = _read_cached(student_id, task.question_id, task.question_revision)
    if cached:
        return {**cached, "metrics": {"generation_calls": 0,
                                      "generation_elapsed_ms": 0, "cache_hit": 1}}

    # Warm the shared diagram pipeline before starting the generation clock.
    # Its first import also loads the catalog/compiler stack; counting that
    # one-time Python import against a very short provider deadline can make
    # the first (and only) provider attempt disappear before it starts.
    from app.diagrams import pipeline as _diagram_pipeline
    # Catalog initialization is also lazy and can read/validate the complete
    # material registry on the first request. Warm it before the provider
    # deadline so cold-start bookkeeping cannot consume the only short call.
    _diagram_pipeline.catalog()
    budget = GenerationBudget(
        max_calls=MAX_ILLUSTRATION_CALLS,
        max_repairs=MAX_ILLUSTRATION_REPAIRS,
        deadline=time.monotonic() + ILLUSTRATION_DEADLINE_SECONDS,
    )
    model = llm or get_llm("quiz")
    from app.core.quiz_illustration_policy import account_allows_illustration_review
    # The deterministic sanitizer always runs. The per-student review switch
    # controls the additional semantic audit for this legacy path.
    review_enabled = account_allows_illustration_review(student_id)
    try:
        if review_enabled:
            from app.illustration.review import supports_images
            if not supports_images(model):
                raise IllustrationValidationError("provider_unavailable")
        # The component pipeline is mandatory.  It performs the model's
        # declaration call, deterministic local fuzzy retrieval, then a scene
        # composition call that can reference only returned asset IDs.
        return await _generate_component_enrichment(
            student_id=student_id, task=task, policy=policy, model=model,
            guidance=guidance,
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


async def _generate_component_enrichment(*, student_id, task, policy, model,
                                         budget, review_enabled, guidance=""):
    from app.diagrams.pipeline import (
        compile_questions,
        declare_and_retrieve,
        scene_contract,
        scene_repair_feedback,
        scene_geometry,
        scene_condition_issues,
    )
    client = BudgetedLLM(model, budget, call_timeout=GENERATION_CALL_TIMEOUT_SECONDS)
    context = "冻结文字题，仅补充已有条件，不改写题目：" + json.dumps(
        _task_payload(task, guidance), ensure_ascii=False)
    # Options can contain distractors or the unknown reading. They remain
    # visible to the composer/auditor but cannot authorize a numeric control.
    public_source = task.stem
    bundle = await declare_and_retrieve(client, context=context, policy=policy, count=1,
                                       public_source=public_source)
    if policy == "auto" and not any(
            requirement.illustration_needed for requirement in bundle.requirements):
        # A valid local declaration that says the frozen text needs no visual
        # should not pay for a second composition call.
        _write_cached(student_id, task.question_id, task.question_revision,
                      status="not_required")
        return {"status": "not_required", "illustration": None,
                "metrics": budget.summary()}
    messages = [
        {"role": "system", "content": scene_contract(bundle, policy, public_source=public_source) +
         '\n冻结题只返回 {"questions":[{"diagram_scene":构图或null}]}；不得输出 illustration、SVG 或改变文字、答案、解析、量规。'},
        {"role": "user", "content": context}]
    reserve = FINAL_AUDIT_RESERVE_SECONDS if review_enabled else 0.0
    while True:
        # Provider/timeouts never synthesize defaults or start a new phase.
        text = await _complete(model, budget, messages,
            timeout=GENERATION_CALL_TIMEOUT_SECONDS, max_tokens=3200,
            reserve_seconds=reserve)
        row, direct_svg = _scene_row(extract_json_object(text))
        if direct_svg:
            raise IllustrationValidationError("diagram_model_svg_forbidden")
        scene = _clean_scene(row.get("diagram_scene")) if row is not None else None
        compiled = (compile_questions([{"diagram_scene": scene}], bundle, policy)[0]
                    if row is not None else {"_diagram_error": "diagram_invalid_scene"})
        code = compiled.get("_diagram_error")
        audit_issue_codes = []
        geometry, condition_issues = None, []
        if not code:
            raw = compiled.get("illustration")
            if raw is None:
                if policy == "required":
                    raise IllustrationValidationError("illustration_required_missing")
                _write_cached(student_id, task.question_id, task.question_revision, status="not_required")
                return {"status": "not_required", "illustration": None, "metrics": budget.summary()}
            illustration = normalize_illustration(raw)
            # Use the fitted, actually compiled scene. Presentation fixes may
            # have moved every object uniformly without changing any fact.
            rendered_scene = (compiled.get("diagram_source") or {}).get("scene", scene)
            geometry = scene_geometry(rendered_scene, bundle)
            from app.diagrams.legacy_layout import fit_public_relations
            fitted_scene = fit_public_relations(rendered_scene, geometry, public_source)
            if fitted_scene != rendered_scene:
                compiled = compile_questions([{"diagram_scene": fitted_scene}], bundle, policy)[0]
                if compiled.get("_diagram_error"):
                    raise IllustrationValidationError(compiled["_diagram_error"])
                illustration = normalize_illustration(compiled["illustration"])
                rendered_scene = (compiled.get("diagram_source") or {}).get("scene", fitted_scene)
                geometry = scene_geometry(rendered_scene, bundle)
            condition_issues = scene_condition_issues(geometry, public_source)
            if condition_issues:
                code = "diagram_condition_mismatch"
            if review_enabled and not code:
                from app.illustration import preview
                png = await asyncio.to_thread(preview.render, illustration)
                text = await _complete(model, budget, _audit_messages(task, illustration, png=png, geometry=geometry),
                    timeout=AUDIT_CALL_TIMEOUT_SECONDS, max_tokens=700)
                audit = _audit_payload(text)
                if audit["status"] != "passed":
                    code = "illustration_audit_failed"
                    # Never send a gold-bearing description back to the
                    # composer. Only server-owned finite error codes cross
                    # this boundary; unknown/freeform audit text is omitted.
                    audit_issue_codes = [issue for issue in audit["issues"]
                                         if issue in _AUDIT_ISSUE_CODES]
            if not code:
                _write_cached(student_id, task.question_id, task.question_revision, status="ready",
                              illustration=illustration, diagram_source=compiled.get("diagram_source"))
                return {"status": "ready", "illustration": illustration, "metrics": budget.summary()}
        repairable = code in _REPAIRABLE_SCENE_CODES or code == "illustration_audit_failed"
        has_budget = (budget.calls + 1 + int(review_enabled) <= budget.max_calls
                      and budget.remaining_seconds > reserve + 1)
        if not repairable or not has_budget or not budget.take_repair():
            raise IllustrationValidationError(code)
        feedback = scene_repair_feedback(scene, bundle, code, geometry=geometry, issues=condition_issues)
        messages = messages[:2] + [{"role": "user", "content": json.dumps({
            "scene_repair": True, "previous_scene": scene,
            "machine_feedback": feedback, "audit_issue_codes": audit_issue_codes,
        }, ensure_ascii=False)}]


async def _run_singleflight(*, flight_key: str, student_id: str,
                            task: S.TaskSnapshot, policy: str,
                            llm: Any | None, guidance: str = "") -> dict[str, Any]:
    try:
        return await _generate_uncached(
            student_id=student_id, task=task, policy=policy, llm=llm,
            guidance=guidance)
    finally:
        current = asyncio.current_task()
        async with _inflight_guard:
            if current is not None and _inflight.get(flight_key) is current:
                _inflight.pop(flight_key, None)


async def generate_assessment_illustration(
    *, student_id: str, task: S.TaskSnapshot, policy: str, llm: Any | None = None,
    guidance: str = "",
) -> dict[str, Any]:
    """Generate/cache a supplemental diagram with bounded corrections in 90s."""
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
                policy=policy, llm=llm, guidance=guidance))
            _inflight[flight_key] = shared
    # One browser component may unmount/abort while another view of the same
    # frozen question is already waiting.  Shielding preserves the shared
    # generation/audit and lets it populate cache; it does not keep the HTTP
    # request itself alive.
    return await asyncio.shield(shared)
