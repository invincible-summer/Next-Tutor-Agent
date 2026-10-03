"""Declare → project retrieval → bounded scene generation. No agent search tool."""
from __future__ import annotations

import json
import math
import re

from app.core.json_utils import extract_json_object
from app.prompts.registry import get

from .catalog import CandidateBundle, catalog, retrieve, search
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


def _public_context(context: str) -> str:
    data = extract_json_object(context)
    if isinstance(data, dict) and isinstance(data.get("stem"), str):
        texts = [data["stem"], str(data.get("illustration_guidance") or "")]
        options = data.get("options")
        if isinstance(options, dict):
            texts.extend(str(value) for value in options.values())
        elif isinstance(options, list):
            texts.extend(str(value) for value in options)
        return " ".join(texts)
    return str(context or "")


def _contains_name(context: str, name: str) -> bool:
    if re.fullmatch(r"[a-zA-Z0-9 _-]+", name):
        return bool(re.search(r"(?<![a-zA-Z0-9_])" + re.escape(name) +
                              r"(?![a-zA-Z0-9_])", context, re.IGNORECASE))
    return re.sub(r"\s+", "", name).lower() in re.sub(r"\s+", "", context).lower()


def fallback_requirements(context: str, *, policy: str) -> dict:
    """Build a bounded local declaration when a provider returns bad JSON.

    This is only a recovery declaration.  It still goes through the same local
    fuzzy catalog and candidate authorization; the model never receives a
    hidden search result or a way to name arbitrary files.
    """
    text = _public_context(context).lower()
    normalized = re.sub(r"\s+", "", text)
    needs: list[dict] = []
    seen: set[str] = set()
    seen_names: set[str] = set()
    for needles, asset_id, name in _FALLBACK_NEEDS:
        normalized_name = re.sub(r"\s+", "", name).lower()
        if asset_id in seen or normalized_name in seen_names:
            continue
        if any(_contains_name(text, needle) for needle in needles):
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
                                and _contains_name(text, str(name))), None)
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


def _quantity_sources(asset, key: str, spec: dict, public_source: str) -> list[dict]:
    """Expose only public numeric literals compatible with a control's unit.

    V1 has no entity/fact IDs. Keep known measurement units and range roles
    strict rather than treating an unrelated matching number as a reading.
    Unknown-unit legacy controls still require a literal and actual-PNG audit.
    """
    from .semantics import parameter_semantics
    unit = spec.get("unit", "")
    if key == "maximum" and not unit:
        unit = parameter_semantics(asset.id, asset.version).get("reading", {}).get("unit", "")
    patterns = {"°C": r"(?:°\s*C|℃|摄氏度)", "A": r"(?:A|安培|安)",
                "V": r"(?:V|伏特|伏)", "N": r"(?:N|牛顿|牛)", "mL": r"(?:mL|毫升)"}
    number = r"(?<![\d.])([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![\d.])"
    suffix = r"\s*" + patterns.get(unit, re.escape(unit)) + r"(?![A-Za-z])" if unit else ""
    rows = []
    for clause in re.split(r"[。；;！？!?\n]", public_source):
        if key == "maximum" and not re.search(r"量程|最大|上限|range|maximum|max", clause, re.I):
            continue
        if key == "capacity" and not re.search(r"容量|量程|capacity|range", clause, re.I):
            continue
        for match in re.finditer(number + suffix, clause):
            prefix = re.split(r"[,，]", clause[max(0, match.start() - 40):match.start()])[-1]
            if key == "reading" and not re.search(
                    r"(?:示数|读数|显示|指示|测得|reading|reads?|indicates?)\s*(?:为|是|约|等于|=|:|：|is|of)?\s*$",
                    prefix, re.I):
                continue
            if key == "maximum" and not re.search(r"量程|最大|上限|range|maximum|max", prefix, re.I):
                continue
            if key == "capacity" and not re.search(r"容量|量程|capacity|range", prefix, re.I):
                continue
            value = float(match.group(1))
            if not math.isfinite(value) or ("minimum" in spec and value < spec["minimum"]
                    or "maximum" in spec and value > spec["maximum"]):
                continue
            if spec.get("type") == "integer" and not value.is_integer():
                continue
            rows.append({"value": int(value) if value.is_integer() else value,
                         "unit": unit, "source_quote": clause.strip()[:240]})
    return rows[:24]


def _unsupported_quantity_controls(asset, public_source: str | None) -> list[str]:
    if public_source is None:
        return []
    from .semantics import parameter_semantics
    return [key for key, spec in parameter_semantics(asset.id, asset.version).items()
            if key in _required_parameters(asset) and spec.get("role") == "quantity"
            and not spec.get("non_quantitative_allowed")
            and not _quantity_sources(asset, key, spec, public_source)]


def _public_control_candidates(bundle: CandidateBundle, public_source: str | None) -> CandidateBundle:
    if public_source is None:
        return bundle
    eligible = {aid: asset for aid, asset in bundle.assets.items()
                if aid not in _UNBOUND_LEGACY_TEMPLATES
                and not _unsupported_quantity_controls(asset, public_source)}
    needs = [{**need, "candidates": [aid for aid in need["candidates"] if aid in eligible]}
             for need in bundle.needs]
    for need in needs:
        need["status"] = "matched" if need["candidates"] else "missing"
    return CandidateBundle(bundle.requirements, eligible, needs)


async def declare_and_retrieve(llm, *, context: str, policy: str, grade: str = "",
                               count: int | None = None, public_source: str | None = None) -> CandidateBundle:
    feature_vocabulary = sorted({feature for asset in catalog()[1].values()
                                 for feature in asset.features})
    context_candidates = [
        {"name": asset.title, "aliases": list(asset.aliases), "features": list(asset.features),
         "parameters": list(asset.parameter_schema()),
         "unsupported_quantity_controls": _unsupported_quantity_controls(asset, public_source)}
        for asset in search(_public_context(context), top_k=5)
    ]
    messages = [
            {"role": "system", "content": get("quiz_visual_requirements").text},
            {"role": "user", "content": json.dumps({"illustration_policy": policy,
                "available_features": feature_vocabulary,
                "context_candidates": context_candidates,
                "question_slots": [f"q{i}" for i in range(1, count + 1)] if count is not None else None,
                "task_context": context}, ensure_ascii=False)}]
    try:
        full, _ = await llm.complete(messages=messages,
            temperature=.1, max_tokens=3500, disable_thinking=True)
    except TimeoutError:
        # A timed-out phase has already spent its bounded allowance. Do not
        # start a composition call from inferred needs after that failure.
        raise
    except Exception:
        # Requirement declaration is advisory: common classroom objects can be
        # inferred locally and still pass through the same fuzzy catalog and
        # authorization boundary. No local arrangement is synthesized.
        return _public_control_candidates(retrieve_declaration(
            fallback_requirements(context, policy=policy), policy=policy, grade=grade), public_source)
    data = extract_json_object(full)
    try:
        bundle = _public_control_candidates(
            retrieve_declaration(data, policy=policy, grade=grade, count=count), public_source)
    except DiagramError as original_error:
        # A malformed declaration must not make an otherwise usable CAT fail
        # when the local project can infer a common, bounded visual need.
        try:
            return _public_control_candidates(retrieve_declaration(
                fallback_requirements(context, policy=policy), policy=policy, grade=grade, count=count), public_source)
        except DiagramError:
            feedback = {"code": original_error.code, "context_candidates": context_candidates}
    else:
        complete = bool(bundle.assets) and (
            public_source is None or all(need["candidates"] for need in bundle.needs))
        if complete or not any(r.illustration_needed for r in bundle.requirements):
            return bundle
        feedback = {"code": "diagram_no_matching_candidates", "context_candidates": context_candidates,
                    "missing_needs": [
                        {"name": need.name, "features": need.features,
                         "name_matches": [{"name": asset.title, "features": list(asset.features),
                                           "unsupported_quantity_controls": _unsupported_quantity_controls(asset, public_source)}
                                          for asset in search(need.name, top_k=5)]}
                        for requirement in bundle.requirements for need in requirement.needs],
                    "public_parameter_rule": "不得补造读数或量程；无数值来源时选择不要求这些参数的符号素材，按实际features重新声明"}
    # One declaration correction is useful when valid project materials were
    # excluded by an unsupported filter or the required policy was ignored.
    # It remains an ordinary metered call and never makes a local scene.
    messages.append({"role": "user", "content": json.dumps({
        "declaration_repair": True, "previous_declaration": data,
        "machine_feedback": feedback,
    }, ensure_ascii=False)})
    full, _ = await llm.complete(messages=messages, temperature=.1,
                                max_tokens=2000, disable_thinking=True)
    return _public_control_candidates(
        retrieve_declaration(extract_json_object(full), policy=policy, grade=grade, count=count), public_source)


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


def scene_contract(bundle: CandidateBundle, policy: str, *, public_source: str | None = None) -> str:
    from .semantics import instantiate_asset, parameter_semantics
    payload = bundle.prompt_data()
    for card in payload["assets"]:
        asset = bundle.assets[card["asset_id"]]
        card["parameter_semantics"] = parameter_semantics(asset.id, asset.version)
        native = instantiate_asset(asset.id, asset.version, asset.sample_params)
        card["size"] = [native.drawing.width, native.drawing.height]
        card["anchors"] = {**{key: list(point) for key, point in native.drawing.anchors.items()},
                           **{key: port["point"] for key, port in native.ports.items()}}
        card["available_regions"] = native.regions
        card["port_kinds"] = {key: port["kind"] for key, port in native.ports.items()}
        if public_source is not None:
            card["public_quantity_sources"] = {
                key: _quantity_sources(asset, key, spec, public_source)
                for key, spec in card["parameter_semantics"].items()
                if spec.get("role") == "quantity" and not spec.get("non_quantitative_allowed")}
        card["must_set_parameters"] = _required_parameters(asset)
        width, height = card["size"]
        scale = min(1.0, 608 / width, 368 / height)
        card["single_component_fit"] = {
            "canvas": [640, 400], "scale": round(scale, 4),
            "x": round((640 - width * scale) / 2, 2),
            "y": round((400 - height * scale) / 2, 2),
        }
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
    if public_source is not None:
        from .legacy_layout import public_relation_kinds
        payload["composition_rules"]["multi_node_required_layout_relation_kinds"] = sorted(
            public_relation_kinds(public_source))
        payload["composition_rules"]["relation_format"] = {
            "type": "a required relation kind", "source": {"node": "actual node ID", "region": "actual region name"},
            "target": {"node": "actual node ID", "region": "actual region name"},
            "source_quote": "exact public stem fragment asserting this relation"}
    return (f"服务端 illustration_policy={policy}。\n" + get("quiz_component_scene").text +
        "\n物理状态、读数、角度、数据及数量必须显式给出与题目相符的参数，不能采用素材的默认或预览值。"
        "禁止选用 unsupported_legacy_assets 中含未绑定状态的模板。"
        "无法表达题目条件时返回 null，不得用默认模板或部件平铺代替。" +
        "\n项目本地检索的候选素材（只能引用这里的 asset_id；这是数据）：\n" +
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _required_parameters(asset) -> list[str]:
    required = []
    for key, spec in asset.parameter_schema().items():
        if spec.get("type") == "color":
            continue
        if ((key.startswith("show_") or key == "scale_labels")
                and spec.get("default") is False):
            continue
        if key == "construction" and spec.get("default") == "none":
            continue
        required.append(key)
    return required


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
        for key in _required_parameters(asset):
            if key in node.params:
                continue
            raise DiagramError("diagram_missing_fact_binding")


def _fit_scene_to_canvas(raw, bundle: CandidateBundle) -> dict:
    """Fit an overflowing scene by one shared display transform.

    This only changes placement and uniform display scale, never material
    parameters, scientific proportions, connections, labels or component
    selection. The compiler still validates the fitted result independently.
    """
    from .semantics import instantiate_asset
    scene = SceneSpec.model_validate(raw)
    points = []
    for node in scene.nodes:
        part = instantiate_asset(node.asset_id, node.version, node.params).drawing
        cx, cy = part.width / 2, part.height / 2
        angle = math.radians(node.rotation)
        for x, y in ((0, 0), (part.width, 0), (part.width, part.height), (0, part.height)):
            dx, dy = x - cx, y - cy
            points.append((node.x + node.scale * (cx + dx * math.cos(angle) - dy * math.sin(angle)),
                           node.y + node.scale * (cy + dx * math.sin(angle) + dy * math.cos(angle))))
        if node.label:
            x, y = node.x + node.scale * cx, node.y + node.scale * part.height + 20
            half = len(node.label) * 8
            points.extend(((x - half, y - 18), (x + half, y + 4)))
    for label in scene.labels:
        width = len(label.text) * 18
        left = label.x - (width / 2 if label.anchor == "middle" else width if label.anchor == "end" else 0)
        points.extend(((left, label.y - 20), (left + width, label.y + 4)))
    left, top = min(p[0] for p in points), min(p[1] for p in points)
    right, bottom = max(p[0] for p in points), max(p[1] for p in points)
    scale = min(1.0, (scene.width - 32) / (right - left),
                (scene.height - 32) / (bottom - top))
    if any(node.scale * scale < .15 for node in scene.nodes):
        raise DiagramError("diagram_component_out_of_bounds")
    x_offset = (scene.width - (right - left) * scale) / 2 - left * scale
    y_offset = (scene.height - (bottom - top) * scale) / 2 - top * scale
    fitted = scene.model_dump(mode="json")
    for node in fitted["nodes"]:
        node["x"] = node["x"] * scale + x_offset
        node["y"] = node["y"] * scale + y_offset
        node["scale"] *= scale
    for label in fitted["labels"]:
        label["x"] = label["x"] * scale + x_offset
        label["y"] = label["y"] * scale + y_offset
    return fitted


def scene_geometry(raw, bundle: CandidateBundle) -> dict:
    """Project actual native geometry to canvas coordinates, without gold."""
    from .semantics import instantiate_asset, parameter_semantics
    scene = SceneSpec.model_validate(raw)
    result = {"nodes": [], "connections": [],
              "layout_relations": [row.model_dump(mode="json") for row in scene.layout_relations]}
    points, port_kinds = {}, {}
    for node in scene.nodes:
        asset = bundle.assets[node.asset_id]
        geometry = instantiate_asset(node.asset_id, node.version, node.params)
        part = geometry.drawing
        port_kinds[node.id] = {key: value["kind"] for key, value in geometry.ports.items()}
        angle, cx, cy = math.radians(node.rotation), part.width / 2, part.height / 2
        def point(x, y):
            dx, dy = x - cx, y - cy
            return [node.x + node.scale * (cx + dx * math.cos(angle) - dy * math.sin(angle)),
                    node.y + node.scale * (cy + dx * math.sin(angle) + dy * math.cos(angle))]
        regions = {}
        for name, region in geometry.regions.items():
            x, y, w, h = region["bounds"]
            corners = [point(px, py) for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))]
            left, top = min(p[0] for p in corners), min(p[1] for p in corners)
            right, bottom = max(p[0] for p in corners), max(p[1] for p in corners)
            regions[name] = {"bounds": [left, top, right - left, bottom - top],
                             "occlusion": region.get("occlusion")}
        local_anchors = {**part.anchors, **{key: port["point"] for key, port in geometry.ports.items()}}
        anchors = {key: point(*value) for key, value in local_anchors.items()}
        points[node.id] = anchors
        corners = [point(px, py) for px, py in ((0, 0), (part.width, 0),
                   (part.width, part.height), (0, part.height))]
        left, top = min(p[0] for p in corners), min(p[1] for p in corners)
        result["nodes"].append({"id": node.id, "asset_id": node.asset_id,
            "bounds": [left, top, max(p[0] for p in corners)-left, max(p[1] for p in corners)-top],
            "parameters": asset.parameters(node.params), "parameter_semantics": parameter_semantics(node.asset_id),
            "regions": regions, "anchors": anchors,
            "port_kinds": {key: port["kind"] for key, port in geometry.ports.items()}, "computed": part.facts})
    protected = [{"node": row["id"], "region": name, "bounds": region["bounds"]}
                 for row in result["nodes"] for name, region in row["regions"].items()
                 if region.get("occlusion") == "never_cover" or name == "body" and region.get("occlusion") == "forbidden"]
    bodies = {row["id"]: row["regions"]["body"]["bounds"] for row in result["nodes"]}
    from .legacy_layout import wire_networks
    networks = wire_networks([row.model_dump(mode="json") for row in scene.connections])
    occupied = []
    for connection in scene.connections:
        if connection.kind == "wire" and any(port_kinds.get(endpoint.node, {}).get(endpoint.anchor) != "wire"
                for endpoint in (connection.start, connection.end)):
            raise DiagramError("diagram_anchor_missing")
        start = points[connection.start.node][connection.start.anchor]
        end = points[connection.end.node][connection.end.anchor]
        if connection.kind == "wire":
            from .legacy_layout import wire_route
            route = wire_route(start, end, route=connection.route, blockers=protected,
                width=scene.width, height=scene.height,
                endpoint_bodies=[(connection.start.node, bodies[connection.start.node]),
                                 (connection.end.node, bodies[connection.end.node])],
                occupied=occupied, network=networks[(connection.start.node, connection.start.anchor)])
            occupied.append({"network": networks[(connection.start.node, connection.start.anchor)], "points": route})
        elif connection.route == "orthogonal":
            middle = (start[0] + end[0]) / 2
            route = [start, [middle, start[1]], [middle, end[1]], end]
        else:
            route = [start, end]
        result["connections"].append({"kind": connection.kind, "points": route,
            "start_node": connection.start.node, "start_anchor": connection.start.anchor,
            "end_node": connection.end.node, "end_anchor": connection.end.anchor})
    return result


def _wire_topology_issues(connections: list[dict]) -> list[dict]:
    """Reject visible contacts that merge distinct declared terminal networks."""
    wires = [(index, row) for index, row in enumerate(connections) if row["kind"] == "wire"]
    roots = {}
    def endpoint(row, side):
        return row[f"{side}_node"], row[f"{side}_anchor"]
    def find(key):
        roots.setdefault(key, key)
        if roots[key] != key:
            roots[key] = find(roots[key])
        return roots[key]
    for _, row in wires:
        roots[find(endpoint(row, "start"))] = find(endpoint(row, "end"))
    def cross(a, b):
        return a[0] * b[1] - a[1] * b[0]
    def touch(a, b, c, d):
        r, s = [b[i] - a[i] for i in (0, 1)], [d[i] - c[i] for i in (0, 1)]
        rr, ss = sum(value * value for value in r), sum(value * value for value in s)
        if min(rr, ss) < 1e-12:
            return False
        delta = [c[i] - a[i] for i in (0, 1)]
        denominator = cross(r, s)
        if abs(denominator) > 1e-9:
            first, second = cross(delta, s) / denominator, cross(delta, r) / denominator
            return -1e-9 <= first <= 1 + 1e-9 and -1e-9 <= second <= 1 + 1e-9
        if abs(cross(delta, r)) > 1e-9:
            return False
        first = sum(delta[i] * r[i] for i in (0, 1)) / rr
        second = first + sum(s[i] * r[i] for i in (0, 1)) / rr
        return max(0.0, min(first, second)) <= min(1.0, max(first, second)) + 1e-9
    issues = []
    for position, (first_index, first) in enumerate(wires):
        for second_index, second in wires[position + 1:]:
            if find(endpoint(first, "start")) == find(endpoint(second, "start")):
                continue
            if any(touch(a, b, c, d) for a, b in zip(first["points"], first["points"][1:])
                   for c, d in zip(second["points"], second["points"][1:])):
                issues.append({"code": "connection_crosses_connection",
                               "connections": [first_index, second_index]})
    return issues[:24]


def scene_condition_issues(geometry: dict, public_stem: str) -> list[dict]:
    """Reject unsupported numeric controls and provable geometry conflicts."""
    issues = []
    for node in geometry["nodes"]:
        asset = catalog()[1][node["asset_id"]]
        for key, spec in node["parameter_semantics"].items():
            value = node["parameters"].get(key)
            if (spec.get("role") == "quantity" and not spec.get("non_quantitative_allowed")
                    and type(value) in {int, float} and not any(
                        math.isclose(value, source["value"], rel_tol=1e-10, abs_tol=1e-10)
                        for source in _quantity_sources(asset, key, spec, public_stem))):
                issues.append({"code": "unsupported_reading", "node": node["id"], "parameter": key})
    from .legacy_layout import relation_issues
    issues.extend(relation_issues(geometry, public_stem))
    nodes = {node["id"]: node for node in geometry["nodes"]}
    for index, relation in enumerate(geometry.get("layout_relations", [])):
        kind = {"supported_by": "support", "suspended_from": "rope"}.get(relation["type"])
        if kind is None:
            continue
        for side in ("source", "target"):
            reference = relation[side]
            node = nodes[reference["node"]]
            candidates = {key: node["anchors"][key] for key, purpose in node.get("port_kinds", {}).items()
                          if purpose == kind and key in node["anchors"]}
            if not candidates:
                continue
            # The relation primitives are vertical contacts. Use the actual
            # lower/upper contact faces, rather than the padded canvas box.
            lower = (relation["type"] == "supported_by") == (side == "source")
            edge = (max if lower else min)(point[1] for point in candidates.values())
            allowed = {key: point for key, point in candidates.items() if abs(point[1] - edge) <= 1}
            if reference.get("anchor") not in allowed:
                issues.append({"code": "relation_contact_port_required", "relation": index,
                               "side": side, "allowed_anchors": allowed})
    issues.extend(_wire_topology_issues(geometry["connections"]))
    def intersects(start, end, bounds):
        x, y, w, h = bounds
        t0, t1 = 0.0, 1.0
        for origin, delta, low, high in ((start[0], end[0]-start[0], x+1, x+w-1),
                                        (start[1], end[1]-start[1], y+1, y+h-1)):
            if abs(delta) < 1e-9:
                if not low < origin < high:
                    return False
            else:
                enter, leave = sorted(((low-origin)/delta, (high-origin)/delta))
                t0, t1 = max(t0, enter), min(t1, leave)
                if t0 >= t1:
                    return False
        return True
    for connection in geometry["connections"]:
        if connection["kind"] != "wire":
            continue
        for node in geometry["nodes"]:
            for name, region in node["regions"].items():
                if region.get("occlusion") == "never_cover" and any(
                        intersects(start, end, region["bounds"])
                        for start, end in zip(connection["points"], connection["points"][1:])):
                    issues.append({"code": "connection_crosses_component", "node": node["id"], "region": name})
    return issues[:24]


def scene_repair_feedback(raw, bundle: CandidateBundle, code: str, *, geometry=None, issues=None) -> dict:
    """Return bounded machine diagnostics without inventing a replacement."""
    feedback = {"code": code, "nodes": []}
    missing = [issue["type"] for issue in issues or [] if issue.get("code") == "missing_layout_relation"]
    if missing:
        feedback["required_layout_relation_kinds"] = missing
        feedback["correction"] = (
            "Return layout_relations inside diagram_scene for these public relation kinds. "
            "Use actual node IDs and region/anchor names, with exact asserting public source_quote. "
            "Returning the same scene without these relations fails again. "
            "The server positions declared regions without changing scientific parameters.")
    if geometry is not None:
        feedback["rendered_geometry"] = geometry
    if issues:
        feedback["condition_issues"] = issues
    if code == "diagram_invalid_function":
        feedback["function_syntax"] = {
            "variable": "x", "constants": ["pi", "e"], "operators": ["+", "-", "*", "/", "**"],
            "functions": ["sin", "cos", "tan", "exp", "log", "sqrt", "abs"],
            "power_example": "x**2", "omit_assignment_prefix": True,
        }
    nodes = raw.get("nodes", []) if isinstance(raw, dict) else []
    for node in nodes[:24] if isinstance(nodes, list) else []:
        if not isinstance(node, dict):
            continue
        asset = bundle.assets.get(node.get("asset_id"))
        if asset is None:
            feedback["nodes"].append({"id": node.get("id"), "asset_not_authorized": True})
            continue
        params = node.get("params") or {}
        params = params if isinstance(params, dict) else {}
        card = asset.card()
        detail = {"id": node.get("id"), "asset_id": asset.id, "version": asset.version,
                  "missing_parameters": [key for key in _required_parameters(asset) if key not in params],
                  "unknown_parameters": sorted(set(params) - set(card["parameters"])),
                  "parameters": card["parameters"], "size": card["size"],
                  "anchors": list(card["anchors"]), "rotation_allowed": card["rotation_allowed"]}
        from .semantics import instantiate_asset
        native = instantiate_asset(asset.id, asset.version, asset.sample_params)
        detail["size"] = [native.drawing.width, native.drawing.height]
        detail["anchors"] = sorted(set(native.drawing.anchors) | set(native.ports))
        detail["anchor_points"] = {**{key: list(point) for key, point in native.drawing.anchors.items()},
                                   **{key: port["point"] for key, port in native.ports.items()}}
        detail["available_regions"] = native.regions
        detail["port_kinds"] = {key: port["kind"] for key, port in native.ports.items()}
        if asset.id in _UNBOUND_LEGACY_TEMPLATES:
            detail["unsupported_unbound_state"] = True
        feedback["nodes"].append(detail)
    return feedback


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
                try:
                    compiled = compile_scene(raw, allowed_assets=eligible)
                except DiagramError as exc:
                    if exc.code != "diagram_component_out_of_bounds":
                        raise
                    fitted = _fit_scene_to_canvas(raw, bundle)
                    compiled = compile_scene(fitted, allowed_assets=eligible)
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
