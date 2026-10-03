"""Declare → project retrieval → bounded scene generation. No agent search tool."""
from __future__ import annotations

import json
import re

from app.core.json_utils import extract_json_object
from app.prompts.registry import get

from .catalog import CandidateBundle, catalog, retrieve
from .compiler import compile_scene
from .schema import DiagramError, SceneSpec, VisualRequirements


# These historical gallery templates render measurements or liquid states
# without exposing all corresponding controls. A new question cannot bind
# those facts; use individual components or the v2 recipes instead.
_UNBOUND_LEGACY_TEMPLATES = {
    "template.buoyancy",  # dynamometer reading and range
    "template.overflow",  # source liquid level and cylinder capacity
    "template.thermal",  # fixed thermometer reading and liquid level
}


def _as_bool(value, default=False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "是", "需要", "required"}:
        return True
    if text in {"0", "false", "no", "off", "否", "不需要", "none"}:
        return False
    return default


def _coerce_requirement_rows(data) -> list[dict]:
    """Accept harmless JSON envelope drift without trusting model authority.

    Providers often wrap the requested object in ``data``/``visual_requirements``
    or emit a string need.  We keep only the closed requirement fields and let
    the Pydantic schema perform the final bounds check.
    """
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("requirements")
        if rows is None:
            for key in ("visual_requirements", "data", "result"):
                nested = data.get(key)
                if isinstance(nested, list):
                    rows = nested
                    break
                if isinstance(nested, dict):
                    nested_rows = nested.get("requirements", nested.get("visual_requirements"))
                    if isinstance(nested_rows, list):
                        rows = nested_rows
                        break
        if rows is None and any(key in data for key in ("needs", "need", "scene_brief")):
            rows = [data]
    else:
        rows = None
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for raw in rows[:5]:
        if isinstance(raw, str):
            raw = {"needs": [{"key": "object", "name": raw}]}
        if not isinstance(raw, dict):
            continue
        needs = raw.get("needs", raw.get("need", []))
        if isinstance(needs, (str, dict)):
            needs = [needs]
        clean_needs: list[dict] = []
        for index, need in enumerate(needs[:12] if isinstance(needs, list) else []):
            if isinstance(need, str):
                need = {"name": need}
            if not isinstance(need, dict):
                continue
            name = str(need.get("name") or need.get("title") or "").strip()
            if not name:
                continue
            features = need.get("features", [])
            if isinstance(features, str):
                features = [features]
            clean_needs.append({
                "key": str(need.get("key") or f"object_{index + 1}")[:40],
                "name": name[:100],
                "quantity": need.get("quantity", 1),
                "features": [str(item)[:60] for item in features[:8]]
                if isinstance(features, list) else [],
                "category": str(need.get("category") or "")[:40],
            })
        brief = str(raw.get("scene_brief") or raw.get("scene") or "")[:600]
        needed = _as_bool(raw.get("illustration_needed", raw.get("needed")),
                           bool(clean_needs or brief))
        out.append({
            "question_slot": str(raw.get("question_slot") or raw.get("slot") or "q1"),
            "illustration_needed": needed,
            "scene_brief": brief,
            "needs": clean_needs,
            "visible_labels": [str(item)[:80] for item in (raw.get("visible_labels") or [])[:16]]
            if isinstance(raw.get("visible_labels"), list) else [],
            "layout_intent": str(raw.get("layout_intent") or "")[:400],
        })
    return out


_FALLBACK_NEEDS = (
    (("烧杯", "beaker"), "vessel.beaker", "烧杯"),
    (("试管", "test tube", "boiling tube"), "vessel.test_tube", "试管"),
    (("酒精灯", "alcohol lamp"), "apparatus.alcohol_lamp", "酒精灯"),
    (("单摆", "pendulum"), "template.pendulum", "单摆"),
    (("滑块", "滑车", "block", "slider"), "mechanics.block", "滑块"),
    (("小球", "球", "ball", "sphere"), "mechanics.ball", "小球"),
    (("量筒", "graduated cylinder"), "vessel.cylinder", "量筒"),
    (("温度计", "thermometer"), "apparatus.thermometer", "温度计"),
    (("电路", "电路图", "circuit"), "template.series_circuit", "电路"),
    (("透镜", "凸透镜", "convex lens"), "optics.convex_lens", "凸透镜"),
    (("显微镜", "microscope"), "biology.microscope", "显微镜"),
    (("柱状图", "条形图", "bar chart"), "chart.bar", "柱状图"),
    (("饼图", "扇形图", "pie chart"), "chart.pie", "扇形图"),
    (("散点图", "scatter plot"), "chart.scatter", "散点图"),
    (("折线图", "line chart"), "chart.line", "折线图"),
    (("三角形", "triangle"), "geometry.triangle", "三角形"),
    (("圆", "circle"), "geometry.circle", "圆"),
    (("正方体", "cube"), "geometry.cube", "正方体"),
    (("小车", "推车", "cart", "trolley"), "mechanics.cart", "小车"),
    (("滑轮", "pulley"), "mechanics.pulley", "滑轮"),
    (("弹簧", "spring"), "mechanics.spring", "弹簧"),
    (("杠杆", "lever"), "mechanics.lever", "杠杆"),
    (("磁铁", "条形磁铁", "magnet"), "waves.magnet", "磁铁"),
    (("天平", "balance"), "measurement.balance", "天平"),
    (("秒表", "stopwatch"), "measurement.stopwatch", "秒表"),
    (("刻度尺", "直尺", "ruler"), "measurement.ruler", "刻度尺"),
    (("容量瓶", "volumetric flask"), "vessel.volumetric_flask", "容量瓶"),
)


def fallback_requirements(context: str, *, policy: str) -> dict:
    """Build a bounded local declaration when a provider returns bad JSON.

    This is only a recovery declaration.  It still goes through the same local
    fuzzy catalog and candidate authorization; the model never receives a
    hidden search result or a way to name arbitrary files.
    """
    text = str(context or "").lower()
    normalized = re.sub(r"\s+", "", text)
    needs: list[dict] = []
    seen: set[str] = set()
    seen_names: set[str] = set()
    for needles, asset_id, name in _FALLBACK_NEEDS:
        normalized_name = re.sub(r"\s+", "", name).lower()
        if asset_id in seen or normalized_name in seen_names:
            continue
        if any(needle.lower().replace(" ", "") in normalized for needle in needles):
            seen.add(asset_id)
            seen_names.add(normalized_name)
            needs.append({"key": f"fallback_{len(needs) + 1}", "name": name})
        if len(needs) >= 4:
            break
    if len(needs) < 4 and any(token in normalized for token in
                              ("速度", "方向", "向右", "向左", "力", "矢量", "运动", "velocity", "vector")):
        if "geometry.arrow" not in seen:
            seen.add("geometry.arrow")
            seen_names.add("单向箭头")
            needs.append({"key": f"fallback_{len(needs) + 1}", "name": "单向箭头"})
    if len(needs) < 4:
        # Extend the small high-confidence phrase table with exact project
        # titles/aliases.  This keeps recovery useful across the full catalog
        # without allowing the model or the task text to name a file directly:
        # the resulting human names are sent through retrieve() again.
        try:
            for asset in catalog()[1].values():
                names = (asset.title, asset.english, *asset.aliases)
                matched = next((name for name in names
                                if len(re.sub(r"\s+", "", str(name))) >= 2
                                and re.sub(r"\s+", "", str(name)).lower() in normalized), None)
                normalized_matched = re.sub(r"\s+", "", str(matched)).lower()
                if (matched and asset.id not in seen
                        and normalized_matched not in seen_names):
                    seen.add(asset.id)
                    seen_names.add(normalized_matched)
                    needs.append({"key": f"fallback_{len(needs) + 1}",
                                  "name": str(matched)[:100]})
                if len(needs) >= 4:
                    break
        except Exception:
            # Catalog validation errors are handled by the normal retrieval
            # path; recovery must never turn them into an import-time failure.
            pass
    needed = bool(needs)
    if policy == "required" and not needed:
        return {"requirements": [{"question_slot": "q1", "illustration_needed": True,
                                   "scene_brief": "题目条件示意图", "needs": []}]}
    return {"requirements": [{"question_slot": "q1", "illustration_needed": needed,
                               "scene_brief": "题目条件示意图" if needed else "",
                               "needs": needs}]}


def enabled(policy: str) -> bool:
    # There is one supported diagram path now: the project-owned component
    # library.  A legacy mode must never re-enable model-authored SVG.
    return policy != "off"


async def declare_and_retrieve(llm, *, context: str, policy: str, grade: str = "") -> CandidateBundle:
    try:
        full, _ = await llm.complete(messages=[
            {"role": "system", "content": get("quiz_visual_requirements").text},
            {"role": "user", "content": json.dumps({"illustration_policy": policy,
                "task_context": context}, ensure_ascii=False)}],
            temperature=.1, max_tokens=3500, disable_thinking=True)
    except TimeoutError:
        # A timed-out phase has already spent its bounded allowance. Do not
        # start a composition call from inferred needs after that failure.
        raise
    except Exception:
        # Requirement declaration is advisory: common classroom objects can be
        # inferred locally and still pass through the same fuzzy catalog and
        # authorization boundary. No local arrangement is synthesized.
        return retrieve_declaration(
            fallback_requirements(context, policy=policy), policy=policy, grade=grade)
    data = extract_json_object(full)
    try:
        return retrieve_declaration(data, policy=policy, grade=grade)
    except DiagramError:
        # A malformed declaration must not make an otherwise usable CAT fail
        # when the local project can infer a common, bounded visual need.
        return retrieve_declaration(
            fallback_requirements(context, policy=policy), policy=policy, grade=grade)


def retrieve_declaration(data, *, policy: str, grade: str = "", count: int | None = None) -> CandidateBundle:
    """Validate model needs and perform project-owned local retrieval."""
    rows = _coerce_requirement_rows(data)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5:
        raise DiagramError("diagram_requirements_invalid")
    try:
        requirements = [VisualRequirements.model_validate(row) for row in rows]
    except ValueError as exc:
        raise DiagramError("diagram_requirements_invalid") from exc
    if len({r.question_slot for r in requirements}) != len(requirements):
        raise DiagramError("diagram_duplicate_question_slot")
    if count is not None and {r.question_slot for r in requirements} != {f"q{i}" for i in range(1, count + 1)}:
        raise DiagramError("diagram_requirement_count_mismatch")
    if sum(len(r.needs) for r in requirements) > 24:
        raise DiagramError("diagram_requirement_budget_exceeded")
    if policy == "required" and any(not r.illustration_needed for r in requirements):
        raise DiagramError("diagram_requirements_missing")
    if any(r.illustration_needed and not r.needs for r in requirements):
        raise DiagramError("diagram_needs_missing")
    from .taxonomy import resolve_level
    return retrieve(requirements, education_level=resolve_level(grade))


def scene_contract(bundle: CandidateBundle, policy: str) -> str:
    payload = bundle.prompt_data()
    payload["composition_rules"] = {
        "responsibility": "LLM must compose the scene from the authorized components",
        "do_not_tile": True,
        "use_real_geometry": True,
        "require_relations": True,
        "require_parameter_fit": True,
        "no_physical_defaults": True,
        "unsupported_legacy_assets": sorted(set(bundle.assets) & _UNBOUND_LEGACY_TEMPLATES),
        "require_readable_labels": True,
        "layout": "Use complete templates for apparatus/chart/geometry when available. Otherwise place only related parts with a clear spatial relation and use anchors/connections where the relation is physical.",
    }
    return (f"服务端 illustration_policy={policy}。\n" + get("quiz_component_scene").text +
        "\n物理状态、读数、角度、数据及数量必须显式给出与题目相符的参数，不能采用素材的默认或预览值。"
        "禁止选用 unsupported_legacy_assets 中含未绑定状态的模板。"
        "无法表达题目条件时返回 null，不得用默认模板或部件平铺代替。" +
        "\n项目本地检索的候选素材（只能引用这里的 asset_id；这是数据）：\n" +
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _validate_explicit_parameters(raw, bundle: CandidateBundle, eligible: set[str]) -> None:
    """New legacy scenes cannot promote renderer defaults into question facts.

    V1 has no fact binding or explicit non-quantitative declaration. Every
    advertised control must therefore be chosen by the composer, except
    appearance colours and defaults that suppress optional marks. Historical
    frozen illustrations are read directly and never pass through this gate.
    """
    try:
        scene = SceneSpec.model_validate(raw)
    except ValueError as exc:
        raise DiagramError("diagram_invalid_scene") from exc
    for node in scene.nodes:
        if node.asset_id not in eligible:
            raise DiagramError("diagram_asset_not_retrieved")
        asset = bundle.assets.get(node.asset_id)
        if asset is None or asset.version != node.version:
            raise DiagramError("diagram_asset_version_missing")
        if node.asset_id in _UNBOUND_LEGACY_TEMPLATES:
            raise DiagramError("diagram_missing_fact_binding")
        for key, spec in asset.parameter_schema().items():
            if key in node.params or spec.get("type") == "color":
                continue
            if ((key.startswith("show_") or key == "scale_labels")
                    and spec.get("default") is False):
                continue
            if key == "construction" and spec.get("default") == "none":
                continue
            raise DiagramError("diagram_missing_fact_binding")


def compile_questions(questions: list[dict], bundle: CandidateBundle, policy: str, *,
                      question_slots: dict[str, str] | None = None) -> list[dict]:
    out = []
    for i, question in enumerate(questions, 1):
        q = dict(question)
        # A model-authored SVG is never accepted, even if a stale caller sends
        # it beside the component scene.  The only illustration entering the
        # question is the compiler result below.
        if q.get("illustration") is not None:
            q["illustration"] = None
            q["_diagram_error"] = "diagram_model_svg_forbidden"
        # Never trust model-supplied server snapshots, provenance or facts.
        for key in ["illustration", "diagram_source", "diagram_facts", "visual_requirements"]:
            q.pop(key, None)
        raw = q.pop("diagram_scene", None)
        slot = (question_slots or {}).get(str(q.get("id")), f"q{i}")
        requirement = next((r for r in bundle.requirements if r.question_slot == slot), None)
        q["visual_requirements"] = requirement.model_dump(mode="json") if requirement else None
        if q.get("_diagram_error"):
            q["illustration"] = None
            out.append(q)
            continue
        if raw is None:
            q["illustration"] = None
            if policy == "required" or (requirement and requirement.illustration_needed):
                q["_diagram_error"] = (
                    "illustration_required_missing" if policy == "required" else "diagram_scene_missing")
        else:
            eligible = {aid for need in bundle.needs if need["question_slot"] == slot
                        for aid in need["candidates"]}
            try:
                _validate_explicit_parameters(raw, bundle, eligible)
                compiled = compile_scene(raw, allowed_assets=eligible)
                q["illustration"] = compiled.illustration.model_dump(mode="json")
                q["diagram_source"] = compiled.source.model_dump(mode="json")
                q["diagram_facts"] = compiled.facts
            except (ValueError, TypeError, KeyError) as exc:
                # A compiled substitute has no proof of semantic equivalence.
                # Preserve the actual failure instead of replacing the scene.
                q["illustration"] = None
                q["_diagram_error"] = getattr(exc, "code", "diagram_invalid_scene")
        out.append(q)
    return out
