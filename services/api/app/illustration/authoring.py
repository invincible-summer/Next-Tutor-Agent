"""Text authoring followed by bounded V2/V3 illustration tasks.

Image calls have their own budgets instead of consuming the text generator's
six-call budget. The batch deadline includes retrieval, rendering and review.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from app.core.config import settings
from app.core.quiz_generation_budget import BudgetedLLM, GenerationBudget, new_quiz_budget
from app.illustration.contracts import IllustrationError
from app.illustration.publishing import publish_gate_passed
from app.prompts.registry import get


def _authoring_contract(mode, make_prompt, topic):
    if mode == "v3":
        from app.illustration.v3_contracts import VisualSpecV3
        return get("quiz_illustration_v3_authoring").text + "\nvisual_spec schema：" + json.dumps(
            VisualSpecV3.model_json_schema(), ensure_ascii=False)
    from app.illustration.requirements import authoring_material_schema, capability_guide, named_material_sources
    sources = named_material_sources(topic)
    related = capability_guide(make_prompt(), selected_asset_ids=[row["asset_id"] for row in sources])
    related["named_material_svg_sources"] = sources
    return get("quiz_illustration_authoring").text + "\nmaterial_contract schema：" + json.dumps(
        authoring_material_schema(), ensure_ascii=False) + "\n相关素材信息（不是本题事实）：" + json.dumps(
            related, ensure_ascii=False)


def bind_question_identity(question):
    """Bind a reviewed draft to its final server identity without redrawing."""
    raw = question.get("material_contract")
    if not raw or not question.get("diagram_source"):
        return
    if raw.get("schema_version") == 3:
        from app.illustration.v3_contracts import QuestionVisualContractV3 as Contract
    else:
        from app.illustration.contracts import QuestionMaterialContract as Contract
    contract = Contract.model_validate({**raw, "question_ref": question["id"], "contract_hash": ""})
    question["material_contract"] = contract.model_dump(mode="json")
    question["diagram_source"]["contract_hash"] = contract.contract_hash


def bind_review_grounding(contract, context):
    """Keep trusted textbook evidence private to the combined reviewer."""
    if not str(context or "").strip():
        return contract
    from app.core.quiz_design import grounding_block
    data = contract.model_dump(mode="json", exclude={"contract_hash"})
    data["authoring_gold"]["grounding_context"] = grounding_block(context)
    return type(contract).model_validate(data)


async def generate(llm, *, pipeline_mode, make_prompt, parse, topic, grade,
                   difficulty="", temperature, max_tokens, illustration_policy,
                   max_attempts=2, required_type="", verify_mode="critic",
                   grounding_context="", feedback=None, _phase_deadline=None,
                   _allow_reauthor=True, **_ignored):
    from app.core.quiz_verify import (filter_well_formed, freeze_rubric,
                                      prepare_illustrations, verify_questions)
    if not isinstance(llm, BudgetedLLM):
        llm = BudgetedLLM(llm, new_quiz_budget())
    text_budget = llm.budget
    meta = {"mode": verify_mode, "attempts": 0, "critic": "skipped", "raw": "",
            "diagram_mode": pipeline_mode, "answer_verified": False,
            "dropped_ill_formed": 0, "dropped_by_critic": 0,
            "dropped_rejected": 0, "dropped_revision_required": 0,
            "unreviewed_count": 0, "critic_flags": [], "illustration_failures": []}
    contract_prompt = _authoring_contract(pipeline_mode, make_prompt, topic)
    questions = []
    for attempt in range(1, max_attempts + 1):
        if not text_budget.available:
            break
        meta["attempts"] = attempt
        try:
            raw, _ = await llm.complete(messages=[
                {"role": "system", "content": make_prompt() + "\n\n" + contract_prompt},
                {"role": "user", "content": "只输出符合 system 合同的 JSON。"}],
                temperature=temperature, max_tokens=max_tokens, disable_thinking=True)
            candidates = [row for row in parse(raw)[:5] if isinstance(row, dict)]
        except Exception:
            meta["generation_error"] = "generation_unavailable"
            continue
        if required_type:
            candidates = [row for row in candidates if row.get("type") == required_type]
        candidates, invalid = filter_well_formed(candidates)
        meta["dropped_ill_formed"] += len(invalid)
        # Source snapshots and audit claims are always generated server-side.
        questions = [{key: value for key, value in row.items() if key not in {
            "diagram_source", "diagram_facts", "illustration_review", "illustration_artifact_id", "verification"}}
            for row in candidates if row.get("illustration") is None and row.get("diagram_scene") is None]
        if questions:
            break
        meta["generation_error"] = "invalid_question_json"
    if not questions:
        meta.update(text_budget.summary())
        return [], meta

    set_id = uuid.uuid4().hex[:8]
    for index, question in enumerate(questions, 1):
        question["id"] = f"q_{set_id}_{index}"
        rubric = freeze_rubric(question, question["id"])
        if rubric is None:
            question["rubric_criteria"] = [{"id": "c1", "description": "最终答案正确",
                                            "weight": 1.0, "critical": True}]
            rubric = freeze_rubric(question, question["id"])
        question["rubric"] = rubric
        question["rubric_criteria"] = rubric["criteria"]
    phase_deadline = _phase_deadline or time.monotonic() + settings.quiz_illustration_deadline_seconds
    semaphore = asyncio.Semaphore(2)
    image_metrics = []

    async def illustrate(question):
        metrics = {}
        image_metrics.append(metrics)
        def observe(_stage, data):
            metrics.update({key: data[key] for key in ("generation_calls", "illustration_repairs",
                "protocol_corrections", "completion_tokens", "generation_elapsed_ms") if key in data})
        spec = question.get("visual_spec") if pipeline_mode == "v3" else question.get("material_contract")
        essential = isinstance(spec, dict) and spec.get("visual_role") == "essential"
        try:
            async with semaphore:
                from app.illustration.orchestrator import generate_question
                result = await generate_question(llm, question, illustration_policy,
                    grade=grade, pipeline_mode=pipeline_mode, phase_deadline=phase_deadline,
                    grounding_context=grounding_context, stage=observe)
                metrics.update(result.pop("_illustration_metrics", {}))
                bind_question_identity(result)
                return result
        except (IllustrationError, ValueError, TimeoutError) as exc:
            code = getattr(exc, "code", "budget_exhausted" if isinstance(exc, TimeoutError) else "invalid_contract")
        except Exception:
            code = "provider_unavailable"
        meta["illustration_failures"].append({"question_id": question["id"], "code": code})
        if illustration_policy == "required" or essential:
            meta["dropped_revision_required"] += 1
            return None
        # Only an independently answerable auto question may survive without a
        # supplementary image. A request for an image never counts as success.
        return {**question, "illustration": None, "visual_role": "none",
                "material_contract": None, "verification": {"status": "unreviewed"}}

    tasks = [asyncio.create_task(illustrate(question)) for question in questions]
    try:
        async with asyncio.timeout(max(0, phase_deadline - time.monotonic())):
            outputs = await asyncio.gather(*tasks)
    except TimeoutError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        outputs = [task.result() if task.done() and not task.cancelled() and task.exception() is None
                   else None for task in tasks]
        meta["diagram_error"] = "budget_exhausted"
    outputs = [row for row in outputs if row is not None]
    frozen_rubrics = {row["id"]: row.get("rubric") for row in outputs}
    questions, invalid = prepare_illustrations(outputs, illustration_policy, component_metadata=True)
    for row in questions:
        # The generic candidate whitelist intentionally strips model-supplied
        # rubrics. Restore only the server-frozen rubric actually reviewed.
        row["rubric"] = frozen_rubrics[row["id"]]
    meta["dropped_revision_required"] += len(invalid)
    reauthor_codes = {"invalid_contract", "question_material_incomplete", "candidate_not_found",
                      "scene_capability_unsupported", "parameter_unbound", "no_meaningful_visual"}
    if (not questions and _allow_reauthor and text_budget.available
            and phase_deadline > time.monotonic()
            and any(row["code"] in reauthor_codes for row in meta["illustration_failures"])
            and text_budget.take_repair()):
        codes = sorted({row["code"] for row in meta["illustration_failures"] if row["code"] in reauthor_codes})
        revised, revised_meta = await generate(llm, pipeline_mode=pipeline_mode,
            make_prompt=lambda: make_prompt()+"\n上一轮配图无法满足本模式材料合同："+json.dumps(codes)
                +"。在原有命题要求内重新设计可绘制且条件自洽的题目，保留配图要求，不切换配图版本。",
            parse=parse, topic=topic, grade=grade, difficulty=difficulty,
            temperature=temperature, max_tokens=max_tokens, illustration_policy=illustration_policy,
            max_attempts=1, required_type=required_type, verify_mode=verify_mode,
            grounding_context=grounding_context, feedback=feedback,
            _phase_deadline=phase_deadline, _allow_reauthor=False)
        revised_meta["illustration_failures"] = meta["illustration_failures"]+revised_meta["illustration_failures"]
        revised_meta["illustration_metrics"] = image_metrics+revised_meta.get("illustration_metrics", [])
        revised_meta["generation_calls"] = text_budget.calls + sum(
            row.get("generation_calls", 0) for row in revised_meta["illustration_metrics"])
        revised_meta["completion_tokens"] = text_budget.completion_tokens + sum(
            row.get("completion_tokens", 0) for row in revised_meta["illustration_metrics"])
        revised_meta["attempts"] += meta["attempts"]
        return revised, revised_meta
    reviewed = [row for row in questions if publish_gate_passed(row.get("diagram_source"))]
    pending = [row for row in questions if row not in reviewed]
    if reviewed:
        meta["critic"] = "ok"
    if pending and verify_mode == "critic":
        # A no-image decision can arrive during declaration. Its text audit
        # uses reserved remaining calls and the batch deadline, never an
        # expired parent text deadline or an unbounded fresh budget.
        available_calls = max(0, text_budget.max_calls - text_budget.calls)
        audit_budget = GenerationBudget(max_calls=min(1, available_calls), max_repairs=0,
                                         deadline=phase_deadline)
        audit_client = BudgetedLLM(llm.unwrap_provider(), audit_budget)
        kept, rejected, ok = await verify_questions(audit_client, pending, topic=topic,
            grade=grade, difficulty=difficulty, grounding_context=grounding_context,
            review_illustrations=False)
        text_budget.calls += audit_budget.calls
        text_budget.completion_tokens += audit_budget.completion_tokens
        questions = [row for row in questions if row in reviewed or row in kept]
        meta["dropped_by_critic"] += len(rejected)
        if not ok:
            meta["critic"] = "error"
    meta["answer_verified"] = bool(questions) and all(
        (row.get("verification") or {}).get("status") == "passed" for row in questions)
    meta["unreviewed_count"] = sum((row.get("verification") or {}).get("status") != "passed"
                                    for row in questions)
    meta.update(text_budget.summary())
    meta["text_generation_calls"] = text_budget.calls
    meta["illustration_metrics"] = image_metrics
    meta["generation_calls"] += sum(row.get("generation_calls", 0) for row in image_metrics)
    meta["completion_tokens"] += sum(row.get("completion_tokens", 0) for row in image_metrics)
    return questions, meta
