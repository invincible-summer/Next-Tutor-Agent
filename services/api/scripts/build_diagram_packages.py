"""Reproduce one SVG/metadata/guidance directory per built-in material."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def recipe_svg(aid):
    from app.diagrams.catalog import catalog
    from app.diagrams.semantics import RECIPES, asset_card, parameter_semantics
    from app.illustration.contracts import QuestionMaterialContract, SceneDraftV2, VisualBriefV2, CandidateBundleV2
    from app.illustration.layout import compile_scene
    recipe = RECIPES[aid]
    parameters = parameter_semantics(aid)
    facts = []
    for key, (role, child_key) in recipe.bindings.items():
        if parameters[key].get("unit") == "diagram_px":
            continue
        child = next(child for name, child, *_ in recipe.children if name == role)
        asset = catalog()[1][child]
        value = parameters[key].get("default") if key == "fill" else asset.parameters(asset.sample_params).get(
            child_key, parameters[key].get("default"))
        facts.append({"id": "sample_"+key, "entity_id": role, "value": value,
            "type": "state" if isinstance(value, bool) else "data" if isinstance(value, list) else "scalar",
            "unit": parameters[key].get("unit", ""), "source_ref": "blueprint", "display_policy": "explicit"})
    roles = [role for role, *_ in recipe.children]
    contract = QuestionMaterialContract(question_ref="material_preview", visual_role="essential",
        public_question={"stem": "完整配方外观预览：" + "；".join(recipe.aliases)},
        entities=[{"id": role, "name": role} for role in roles], facts=facts)
    brief = VisualBriefV2(visual_role="essential", purpose="素材预览，样例参数不是题目事实",
        needs=[{"need_id": "preview", "name": recipe.title, "entity_ids": roles,
            "fact_bindings": [fact["id"] for fact in facts]}])
    card = asset_card(aid)
    bundle = CandidateBundleV2(catalog_version="preview", metadata_version="2.0.0", retrieval_trace={},
        needs=[{"need_id": "preview", "matched": True, "candidate_ids": [aid]}], assets=[card])
    scene = SceneDraftV2(asset_instances=[{"instance_id": "preview", "need_id": "preview",
        "asset_id": aid, "version": card["version"], "entity_map": {role: role for role in roles},
        "fact_bindings": {fact["id"].removeprefix("sample_"): fact["id"] for fact in facts},
        "non_quantitative": [key for key, spec in parameters.items() if spec.get("unit") == "diagram_px"],
        "anchor_intent": "完整配方外观预览", "x": 20, "y": 10}], alt="素材外观预览，样例条件不是题目事实")
    return compile_scene(scene, contract=contract, brief=brief, bundle=bundle).illustration.svg


def main():
    from app.core.quiz_illustration import normalize_svg
    from app.diagrams.catalog import catalog
    from app.diagrams.guidance import GUIDE_DIR
    from app.diagrams.semantics import RECIPES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = json.loads((GUIDE_DIR.parent / "catalog.json").read_text())
    rows = {row["id"]: row for row in data["assets"]}
    for aid, recipe in RECIPES.items():
        rows[aid] = {"id": aid, "title": recipe.title, "aliases": list(recipe.aliases),
            "version": 1, "kind": "recipe", "renderer": "registered_v2_recipe",
            "preview_only": True}
    rows["material.static"] = {"id": "material.static", "title": "静态素材设计基底", "version": 1,
        "kind": "design_template", "preview_only": True}
    for aid, row in rows.items():
        directory = GUIDE_DIR / aid
        if aid in RECIPES:
            try:
                svg = recipe_svg(aid)
            except Exception as exc:
                raise RuntimeError("invalid recipe preview: " + aid) from exc
        elif aid == "material.static":
            from app.diagrams.material_templates import TEMPLATES
            svg = normalize_svg(TEMPLATES[0]["svg"], components=True).svg
        else:
            asset = catalog()[1][aid]
            svg = normalize_svg(asset.draw(preview=True).svg(), components=True).svg
        metadata = {**row, "svg_file": "asset.svg", "guidance_file": "usage_guide.json",
            "svg_hash": "sha256:"+hashlib.sha256(svg.encode()).hexdigest()}
        contents = {"asset.svg": svg + "\n", "material.json": json.dumps(metadata, ensure_ascii=False, indent=2)+"\n"}
        if not (directory / "usage_guide.json").exists():
            contents["usage_guide.json"] = '{\n  "version": "1.0.0",\n  "hints": {}\n}\n'
        for name, content in contents.items():
            path = directory / name
            if args.check:
                if not path.exists() or path.read_text() != content:
                    raise SystemExit(f"material package out of date: {aid}/{name}")
            else:
                directory.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
    print(f"{len(rows)} isolated built-in SVG material packages")


if __name__ == "__main__":
    main()
