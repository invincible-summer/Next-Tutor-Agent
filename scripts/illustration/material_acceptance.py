#!/usr/bin/env python3
"""Live SVG draft → manual edit → private/public save → real question PNG.

Explicit provider opt-in and a TemporaryDirectory contain every runtime write.
"""
import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))

CASES = {
    "vessel": ("设计单个烧杯盛水的示意素材，无文字、刻度、数字、仪器，只有透明容器轮廓和定性水面。",
        "烧杯盛水，文字完整说明容器中有水，问烧杯在化学实验中的用途。只有整个容器图一个实体，补充图。"),
    "geometry": ("设计一个圆和准确内接的正三角形，无文字、数字、辅助线、角标或半径线。圆心320,200，半径140，三角形顶点在圆上。",
        "正三角形内接于圆，圆半径6cm，求正三角形边长，文字条件完整，不按比例，补充图。整个构造为一个实体。"),
    "flow": ("设计两个矩形节点，从左到右箭头连接，左框只写‘观察’，右框只写‘记录’，其他文字都不写。",
        "科学观察之后把现象记录下来，流程为观察然后记录，问记录现象有什么作用，文字条件完整。整体流程为一个实体，补充图。"),
}


async def run(output):
    from app.core import paths
    from app.core.config import settings
    from app.core.llm_async import get_llm
    from app.core.json_utils import extract_json_object
    from app.core.quiz_verify import generate_verified_questions
    from app.diagrams import materials
    from app.illustration.preview import render
    from app.core.quiz_illustration import QuestionIllustration
    class RecordedLLM:
        supports_images = True
        def __init__(self, name):
            self.name, self.client, self.calls = name, get_llm("quiz"), 0
        async def complete(self, **kwargs):
            self.calls += 1
            result = await self.client.complete(**kwargs)
            (output / f"{self.name}-call-{self.calls}.json").write_text(result[0])
            return result
    settings.quiz_illustration_pipeline = "v2"
    settings.quiz_illustration_visual_review = "active"
    settings.llm_supports_images = True
    owner = "usr_synthetic_material_acceptance"
    rows = []
    previous = paths.current_override_root()
    try:
        with tempfile.TemporaryDirectory(prefix="svg-material-live-") as directory:
            paths.set_runtime_root(Path(directory))
            for name, (requirement, question_request) in CASES.items():
                client = RecordedLLM(name)
                draft = await materials.generate_draft(requirement, llm=client)
                (output / f"{name}-draft.svg").write_text(draft["svg"])
                (output / f"{name}-draft.png").write_bytes(render(QuestionIllustration.model_validate(draft["illustration"])))
                body = materials.MaterialInput(title="验收自制"+name, description=requirement,
                    svg=draft["svg"], source="llm", enabled=True, scope="public" if name == "flow" else "private")
                original = await asyncio.to_thread(materials.save, owner, body, admin=True)
                # An actual manual revision changes the line weight, preserving
                # scientific coordinates and immutable original source.
                from xml.etree import ElementTree as ET
                root = ET.fromstring(draft["svg"])
                for node in root.iter():
                    if node.get("stroke") not in (None, "none"):
                        node.set("stroke-width", "2.5")
                edited = body.model_copy(update={"svg": ET.tostring(root, encoding="unicode"),
                    "base_revision": 1, "source": "manual"})
                saved = await asyncio.to_thread(materials.save, owner, edited, admin=True, asset_id=original["id"])
                (output / f"{name}-saved.json").write_text(json.dumps(saved, ensure_ascii=False, indent=2))
                questions, metrics = await generate_verified_questions(client, student_id=owner,
                    make_prompt=lambda: "生成一道中文short_answer题。"+question_request+
                        "必须使用当前素材库名称“"+saved["title"]+"”的静态整体素材，需求名逐字使用该名，能力static_illustration。"
                        "只列一个整体实体，不将静态内部子图拆为实体或关系。图形不按比例，不设置像素参数。"
                        "presentation_constraints.preferred_material_names必须包含“"+saved["title"]+"”。"
                        '只输出{"questions":[{type,stem,answer,explanation,difficulty,rubric_criteria,material_contract}]}，difficulty=0.5。'
                        "rubric_criteria必须为[{id,description,weight,critical}]对象数组。实体只列一个静态完整图实体，source_quote使用题干逐字短引用；不声明内部关系，不额外添加半径或答案标注。",
                    parse=lambda raw: extract_json_object(raw).get("questions", []), topic=name,
                    grade="初中", temperature=.2, max_tokens=4500, illustration_policy="required", max_attempts=1)
                used = bool(questions) and "material."+saved["id"] in questions[0]["diagram_source"]["asset_versions"]
                if questions:
                    (output / f"{name}-question.json").write_text(json.dumps(questions[0], ensure_ascii=False, indent=2))
                    image = QuestionIllustration.model_validate(questions[0]["illustration"])
                    (output / f"{name}-question.png").write_bytes(render(image))
                rows.append({"case": name, "passed": used, "revision": saved["revision"],
                    "scope": saved["scope"], "metrics": {k:v for k,v in metrics.items() if k != "raw"}})
                (output / "report.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                print(json.dumps({"case":name, "passed":used, "revision":saved["revision"]}), flush=True)
    finally:
        paths.set_runtime_root(previous)
    return all(row["passed"] for row in rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-llm", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live_llm: parser.error("--live-llm required")
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents: parser.error("output must be outside repository")
    output.mkdir(parents=True, exist_ok=True)
    raise SystemExit(0 if asyncio.run(run(output)) else 1)
