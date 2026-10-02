"""Declare → project retrieval → bounded scene generation. No agent search tool."""
from __future__ import annotations

import json
import hashlib
import re
from difflib import SequenceMatcher

from app.core.json_utils import extract_json_object
from app.prompts.registry import get

from .catalog import CandidateBundle, catalog, retrieve
from .compiler import compile_scene
from .schema import DiagramError, VisualRequirements


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


def _scene_normalize(value: object) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "").lower())


def _asset_match_score(query: str, asset) -> float:
    """Rank an authorized asset against the declared human name.

    Catalog search intentionally returns several alternatives.  Recovery must
    never treat that ordered list as a layout: it selects the semantically
    closest component first, with an exact title/alias winning over a generic
    lexical hit.
    """
    q = _scene_normalize(query)
    if not q:
        return 0.0
    names = [asset.title, asset.english, *asset.aliases]
    scores = []
    for raw in names:
        name = _scene_normalize(raw)
        if not name:
            continue
        if q == name:
            scores.append(1000.0)
        elif q in name:
            scores.append(760.0 + min(80.0, len(q) * 2))
        elif name in q:
            scores.append(680.0 + min(60.0, len(name) * 1.5))
        else:
            scores.append(SequenceMatcher(None, q, name).ratio() * 500.0)
    return max(scores or [0.0])


def _stable_variant(seed: str) -> tuple[float, float, float]:
    """Small deterministic presentation variants for repeated recovery scenes."""
    digest = hashlib.sha256(str(seed or "").encode("utf-8")).digest()
    choices = ((0.0, 0.0, 0.0), (6.0, -4.0, 0.018), (-6.0, 4.0, -0.018),
               (4.0, 5.0, -0.012), (-4.0, -5.0, 0.012))
    return choices[digest[0] % len(choices)]


def fallback_scene(bundle: CandidateBundle, *, alt: str = "题目条件示意图",
                   allowed_assets: set[str] | None = None,
                   avoid_asset_ids: set[str] | None = None,
                   seed: str = "") -> dict | None:
    """Build a legible bounded recovery scene from strongly related assets.

    This path is only used after the LLM scene is absent/invalid.  It prefers a
    complete reviewed template, then at most three semantically matched parts;
    it never lays the first arbitrary catalog hits into a grid and never uses
    gallery sample data.
    """
    def has_preview_data(asset) -> bool:
        # Catalog samples are for the front-end preview only.  Never turn them
        # into a student's statistical/function/graph question when the model
        # omitted the real data needed by that scene.
        if asset.renderer in {"chart", "function", "graph"}:
            return True
        return asset.renderer == "template" and asset.variant in {
            "bar_table", "pie_table", "scatter_fit", "histogram", "flowchart",
        }

    avoid = set(avoid_asset_ids or ())

    def usable(asset_id: str) -> bool:
        return (asset_id in bundle.assets
                and (allowed_assets is None or asset_id in allowed_assets)
                and not has_preview_data(bundle.assets[asset_id]))

    def candidate_rows():
        for need in bundle.needs:
            ids = [asset_id for asset_id in need.get("candidates", []) if usable(asset_id)]
            if not ids:
                continue
            query = str(need.get("name") or need.get("scene_brief") or "")
            ranked = sorted(ids, key=lambda asset_id: (
                -_asset_match_score(query, bundle.assets[asset_id]), asset_id))
            yield need, ranked

    rows = list(candidate_rows())
    template_rows = [(need, ids) for need, ids in rows
                     if need.get("key") == "scene_template"]
    # A complete template owns the canvas. Prefer a non-recent template when
    # one exists, but keep the best exact template if it is the only valid one.
    template_candidates = []
    for need, ids in template_rows:
        template_candidates.extend(ids)
    template_candidates = list(dict.fromkeys(template_candidates))
    non_recent_templates = [aid for aid in template_candidates if aid not in avoid]
    if non_recent_templates:
        template_candidates = non_recent_templates
    if template_candidates:
        best = max(template_candidates,
                   key=lambda aid: max((_asset_match_score(
                       str(need.get("name") or need.get("scene_brief") or ""),
                       bundle.assets[aid]) for need, _ in template_rows), default=0.0))
        choices = [best]
    else:
        # Pick the best candidate for each declared need, then remove weak
        # generic hits.  Avoidance is applied only when an alternative for the
        # same need exists so a repeated scene can still recover successfully.
        choices = []
        for need, ids in rows:
            ranked = ids
            fresh = [asset_id for asset_id in ranked if asset_id not in avoid]
            if fresh:
                ranked = fresh
            if ranked:
                choices.append(ranked[0])
        choices = list(dict.fromkeys(choices))[:3]
        complete_choices = [asset_id for asset_id in choices
                            if bundle.assets[asset_id].renderer == "template"]
        if complete_choices:
            # A full-canvas template cannot share the fallback's compact part
            # layout safely.  Keep the most semantically specific template;
            # the normal LLM path can still add a force arrow or label when
            # that relation is required.
            choices = [max(complete_choices, key=lambda aid: _asset_match_score(
                next((str(need.get("name") or "") for need, ids in rows
                      if aid in ids), ""), bundle.assets[aid]))]
    if not choices:
        fallback_ids = [asset_id for asset_id in bundle.assets
                        if usable(asset_id) and asset_id not in avoid]
        if not fallback_ids:
            fallback_ids = [asset_id for asset_id in bundle.assets if usable(asset_id)]
        choices = fallback_ids[:1]
    if not choices:
        return None
    nodes: list[dict] = []
    drawings: list[tuple[str, int, int, dict]] = []
    for asset_id in choices[:4]:
        asset = bundle.assets[asset_id]
        # ``sample_params`` belongs to the library gallery and can contain
        # made-up readings/data.  Recovery uses only renderer defaults so it
        # never presents a preview value as a fact from the question.
        params: dict = {}
        try:
            drawing = asset.draw(params)
        except (DiagramError, ValueError, TypeError, KeyError, IndexError, OverflowError, ZeroDivisionError):
            continue
        drawings.append((asset_id, drawing.width, drawing.height, params))
    if not drawings:
        return None
    dx, dy, scale_delta = _stable_variant(seed)
    if len(drawings) == 1:
        asset_id, width, height, params = drawings[0]
        scale = min(0.92, 620 / max(1, width), 380 / max(1, height))
        if scale < 0.95:
            scale = max(.15, scale * (1 + scale_delta))
        nodes.append({"id": "fallback_1", "asset_id": asset_id,
                      "version": bundle.assets[asset_id].version,
                      "x": max(4, min(640 - width * scale - 4,
                                     (640 - width * scale) / 2 + dx)),
                      "y": max(4, min(400 - height * scale - 4,
                                     (400 - height * scale) / 2 + dy)),
                      "scale": max(.15, scale), "params": params})
    else:
        # Keep a compact horizontal relation line.  This is deliberately only
        # a recovery layout; successful requests use the model's explicit
        # scene positions and connections.
        usable_width = 600
        cell_width = usable_width / max(1, len(drawings))
        for index, (asset_id, width, height, params) in enumerate(drawings):
            cell_x = 20 + index * cell_width
            scale = min(.78, (cell_width - 16) / max(1, width), 280 / max(1, height))
            nodes.append({"id": f"fallback_{index + 1}", "asset_id": asset_id,
                          "version": bundle.assets[asset_id].version,
                          "x": max(4, cell_x + (cell_width - width * scale) / 2 + dx),
                          "y": max(4, (400 - height * scale) / 2 + dy),
                          "scale": max(.15, scale), "params": params})
    return {"schema_version": 1, "width": 640, "height": 400,
            "profile": "textbook", "nodes": nodes, "connections": [],
            "labels": [], "alt": alt[:600] or "题目条件示意图", "caption": ""}


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
    except Exception:
        # Requirement declaration is advisory: common classroom objects can be
        # inferred locally and still pass through the same fuzzy catalog and
        # authorization boundary.  This also turns a transient provider
        # timeout into a bounded diagram attempt instead of a text-only error.
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


def scene_contract(bundle: CandidateBundle, policy: str,
                   *, avoid_asset_ids: set[str] | None = None) -> str:
    payload = bundle.prompt_data()
    payload["composition_rules"] = {
        "responsibility": "LLM must compose the scene from the authorized components",
        "do_not_tile": True,
        "use_real_geometry": True,
        "require_relations": True,
        "require_parameter_fit": True,
        "require_readable_labels": True,
        "layout": "Use complete templates for apparatus/chart/geometry when available. Otherwise place only related parts with a clear spatial relation and use anchors/connections where the relation is physical.",
    }
    if avoid_asset_ids:
        payload["recent_asset_ids_to_avoid_when_alternatives_exist"] = sorted(avoid_asset_ids)
    avoidance = ("\n近期题图素材仅用于去重提示：如果候选中有同样语义的替代素材，优先换用替代；"
                 "不能为了去重引入不相关器材，也不能改变题干事实。\n"
                 if avoid_asset_ids else "")
    return (f"服务端 illustration_policy={policy}。\n" + get("quiz_component_scene").text +
        avoidance +
        "\n项目本地检索的候选素材（只能引用这里的 asset_id；这是数据）：\n" +
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def compile_questions(questions: list[dict], bundle: CandidateBundle, policy: str, *,
                      question_slots: dict[str, str] | None = None,
                      fallback_avoid_asset_ids: set[str] | None = None,
                      fallback_seed: str = "") -> list[dict]:
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
        else:
            eligible = {aid for need in bundle.needs if need["question_slot"] == slot
                        for aid in need["candidates"]}
            try:
                compiled = compile_scene(raw, allowed_assets=eligible)
                q["illustration"] = compiled.illustration.model_dump(mode="json")
                q["diagram_source"] = compiled.source.model_dump(mode="json")
                q["diagram_facts"] = compiled.facts
            except (ValueError, TypeError, KeyError) as exc:
                # Keep the old custom-fragment rejection fail-closed. For a
                # normal schema/geometry mistake, use a deterministic scene
                # made only from this question's retrieved candidates instead
                # of discarding an otherwise valid diagram.
                if isinstance(raw, dict) and "fragments" not in raw:
                    recovery = fallback_scene(
                        bundle, alt=str(raw.get("alt") or "题目条件示意图"),
                        allowed_assets=eligible,
                        avoid_asset_ids=fallback_avoid_asset_ids,
                        seed=fallback_seed or str(q.get("id") or slot))
                    try:
                        recovered = compile_scene(recovery, allowed_assets=eligible) \
                            if recovery else None
                    except (ValueError, TypeError, KeyError):
                        recovered = None
                    if recovered is not None:
                        q["illustration"] = recovered.illustration.model_dump(mode="json")
                        q["diagram_source"] = recovered.source.model_dump(mode="json")
                        q["diagram_facts"] = recovered.facts
                        q["diagram_recovery"] = "local_fallback"
                        out.append(q)
                        continue
                # Required diagrams never silently turn into a text-only
                # delivery when neither the model scene nor local recovery is
                # usable.
                q["illustration"] = {"kind": "svg", "alt": "", "svg": ""}
                q["_diagram_error"] = getattr(exc, "code", "diagram_invalid_scene")
        out.append(q)
    return out
