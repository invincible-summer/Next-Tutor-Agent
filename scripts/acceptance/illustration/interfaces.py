#!/usr/bin/env python3
"""Real provider acceptance of the universal interface, without scenario hints."""
import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services/api"))

# Ordinary task requests only. No asset parameter instructions, entity-map
# fixes, exception prompts or extra model budget are supplied by this suite.
SCENARIOS = {
    "fraction": ("geometry.fraction_bar", "分数条", "一条纸带平均分成8份，其中3份着色，求着色部分占全部的分数。"),
    "stomata": ("biology.stomata", "气孔", "叶表皮的气孔由两个保卫细胞围成，气孔是叶片进行气体交换的通道，问气孔为何影响蒸腾作用。"),
    "water_cycle": ("earth.water_cycle", "水循环", "水从地表蒸发，在高空凝结形成云，降水回到地表，问太阳能在水循环中的作用。"),
    "glacier": ("geography_extended.glacier", "冰川地貌", "冰川侵蚀形成U形谷，问这种谷地横断面为何不同于流水侵蚀常见的V形谷。"),
    "magnet": ("waves.magnet", "条形磁铁", "条形磁铁有两个磁极，问为何磁体中部和两端吸引铁屑的能力不同。"),
    "pie": ("chart.pie", "饼图", "甲、乙、丙三类人数分别为12、24、12，用饼图说明这组数据，求乙类占总人数的比例。"),
    "unitcircle": ("mathematics_extended.unit_circle_projection", "单位圆正弦投影", "单位圆半径为1，P对应60°角，图有坐标轴和投影线，求P的纵坐标。"),
    "sound": ("physics_extended.closed_pipe_modes", "闭端气柱位移模态", "闭端气柱处于第三个允许模态，序号为3，闭端是位移节点、开端是位移腹点，问此时气柱内的驻波为何只允许奇数次谐波。"),
    "reading": ("measurement.dynamometer", "弹簧测力计", "需要从图读取测力计示数，量程4N，实际示数2N。题干不直接透露示数，问图示测力计的读数。"),
    "function": ("function.quadratic", "二次函数", "函数y=x**2，横轴显示区间[-2,2]，纵轴显示范围[-1,5]，求函数的最小值。"),
    "heating": ("recipe.heating_beaker", "加热烧杯", "烧杯中的水用点燃的酒精灯加热，烧杯放在金属网上，金属网由三脚架支撑，问为何使用金属网。"),
    "buoyancy": ("recipe.buoyancy_measurement", "浸没测力", "重5N的小球由弹簧测力计悬挂，完全浸没在烧杯水中且不碰容器，测力计量程5N，实际示数3N。题干不直接透露示数，要求读图并求浮力。"),
    "series": ("recipe.series_circuit", "电池开关电阻电流表串联", "电池、闭合开关、电阻和电流表连接成完整串联回路，问为何电流表串联接入该电路。"),
    "thermal": ("recipe.thermal", "烧杯温度测量", "温度计玻璃泡完全浸没在烧杯水中且不碰底，实际温度45°C。题干不直接透露温度，要求从图读取温度。"),
    "filtration": ("recipe.filtration", "过滤装置", "用漏斗过滤液体，漏斗导管末端紧贴接收烧杯的内壁，问为何这样操作。"),
    "molecule": ("chemistry.water", "水分子", "水分子由2个氢原子和1个氧原子构成，问水分子能否保持水的化学性质。"),
}


async def run(output, variation, selected):
    from app.core import paths
    from app.core.config import settings
    from app.core.llm_async import get_llm
    from app.core.json_utils import extract_json_object
    from app.core.quiz_verify import generate_verified_questions
    from app.core.quiz_illustration import QuestionIllustration
    from app.diagrams import materials
    from app.diagrams.guidance import for_asset
    from app.diagrams.material_templates import TEMPLATES
    from app.illustration import preview
    settings.quiz_illustration_pipeline = "v2"
    settings.quiz_illustration_visual_review = "active"
    settings.llm_supports_images = True
    previous_root, render = paths.current_override_root(), preview.render
    rows = []
    owner = "usr_synthetic_interface_acceptance"
    class RecordedLLM:
        supports_images = True
        def __init__(self, name):
            self.name, self.client, self.calls, self.svg_source_calls = name, get_llm("quiz"), 0, []
        async def complete(self, **kwargs):
            self.calls += 1
            for message in kwargs.get("messages", []):
                content = message.get("content")
                if not isinstance(content, str) or not content.startswith("{"):
                    continue
                payload = json.loads(content)
                if "material_svg_sources" in payload:
                    self.svg_source_calls.append({"assets": [row["asset_id"] for row in payload["material_svg_sources"]],
                        "svg_bytes": sum(len(row["svg"].encode()) for row in payload["material_svg_sources"])})
            raw, usage = await self.client.complete(**kwargs)
            (output / f"{self.name}-call-{self.calls}.json").write_text(raw)
            return raw, usage
    try:
        with tempfile.TemporaryDirectory(prefix="material-interface-live-") as directory:
            paths.set_runtime_root(Path(directory))
            private = {}
            for name, index, request in (
                ("private_flow", 2, "蒸发之后凝结，两个步骤依次为蒸发、凝结，说明蒸发和凝结的物态变化。"),
                ("private_geometry", 0, "正三角形内接于圆，圆半径4cm，求正三角形边长，配图为不按比例示意。")):
                template = TEMPLATES[index]
                saved = await asyncio.to_thread(materials.save, owner, materials.MaterialInput(
                    title="新建验收"+name, svg=template["svg"], subject=template["subject"],
                    parameterization=template["parameterization"], enabled=True))
                private[name] = ("material."+saved["id"], saved["title"], request)
            scenarios = {**SCENARIOS, **private}
            for name in selected:
                aid, title, request = scenarios[name]
                if variation:
                    request = {"fraction": request.replace("8份", "10份").replace("3份", "7份"),
                        "pie": request.replace("12、24、12", "15、15、30"),
                        "unitcircle": request.replace("60°", "30°"),
                        "sound": request.replace("第三个", "第二个").replace("序号为3", "序号为2"),
                        "reading": request.replace("量程4N", "量程5N").replace("示数2N", "示数3N"),
                        "function": request.replace("[-2,2]", "[-3,3]").replace("[-1,5]", "[-1,9]"),
                        "private_flow": "过滤之后回收滤液，两个步骤依次为过滤、回收，问回收滤液时应注意什么。",
                        "private_geometry": request.replace("4cm", "6cm")}.get(name, request)
                client = RecordedLLM(name)
                images = {}
                def record(image):
                    png = render(image)
                    images[image.content_hash] = png
                    (output / f"{name}-preview-{len(images)}.png").write_bytes(png)
                    return png
                preview.render = record
                def parse(raw):
                    data = extract_json_object(raw)
                    return data.get("questions", []) if isinstance(data, dict) else []
                questions, metrics = await generate_verified_questions(client, student_id=owner,
                    make_prompt=lambda: "生成一道中文short_answer题并配图。"+request+
                        "请使用素材“"+title+"”。只输出{questions:[{type,stem,answer,explanation,difficulty,rubric_criteria,material_contract}]}。"
                        "difficulty=0.5，rubric_criteria为[{id,description,weight,critical}]。",
                    parse=parse,
                    topic=title, grade="高中", temperature=.2, max_tokens=4500,
                    illustration_policy="required", max_attempts=2)
                used = bool(questions) and aid in questions[0]["diagram_source"]["asset_versions"]
                for i, question in enumerate(questions):
                    (output / f"{name}-{i}.json").write_text(json.dumps(question, ensure_ascii=False, indent=2))
                    image = QuestionIllustration.model_validate(question["illustration"])
                    (output / f"{name}-{i}.svg").write_text(image.svg)
                    (output / f"{name}-{i}.png").write_bytes(images.get(image.content_hash) or render(image))
                guide = None if aid.startswith("material.") else for_asset(aid)
                rows.append({"case": name, "passed": used, "calls": client.calls,
                    "svg_source_calls": client.svg_source_calls,
                    "has_asset_guidance": bool(guide and guide["hints"]),
                    "metrics": {k: v for k, v in metrics.items() if k != "raw"}})
                (output / "report.json").write_text(json.dumps({"variation": variation,
                    "protocol": "material_interface@1.0.0", "input_mode": "full_svg_no_material_previews",
                    "model": client.client.model, "prompts": {"authoring": "1.16.0", "pipeline": "2.19.0"},
                    "cases": rows}, ensure_ascii=False, indent=2))
                print(json.dumps({"case": name, "passed": used, "calls": client.calls}), flush=True)
    finally:
        preview.render = render
        paths.set_runtime_root(previous_root)
    return all(row["passed"] for row in rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-llm", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variation", type=int, choices=(0, 1), default=0)
    parser.add_argument("--cases", default=",".join([*SCENARIOS, "private_flow", "private_geometry"]))
    args = parser.parse_args()
    if not args.live_llm:
        parser.error("real provider requires --live-llm")
    names = args.cases.split(",")
    if set(names) - {*SCENARIOS, "private_flow", "private_geometry"}:
        parser.error("unknown case")
    args.output = args.output.resolve()
    if args.output == ROOT or ROOT in args.output.parents:
        parser.error("write evidence outside the repository")
    args.output.mkdir(parents=True, exist_ok=True)
    raise SystemExit(0 if asyncio.run(run(args.output, args.variation, names)) else 1)
