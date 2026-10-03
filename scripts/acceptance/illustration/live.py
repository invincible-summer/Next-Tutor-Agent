#!/usr/bin/env python3
"""Explicit live-provider acceptance against synthetic questions and isolated storage.

python scripts/acceptance/illustration/live.py --live-llm --output /tmp/illustration-review
Never imports the hermetic unittest runner; it deliberately uses configured LLM.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services/api"))

SCENARIOS = {
    "heating": "初中化学：烧杯中的水用点燃的酒精灯加热，烧杯放在金属网上，金属网由三脚架支撑。文字完整描述装置，问为何使用金属网。需要完整装置补充图。液面仅定性，不要求温度读数。",
    "reading": "初中物理：如图读出弹簧测力计的示数。图中量程5 N，示数3 N，必须绘出标定刻度和单位，题干不能直接说示数3 N。读数材料是 essential、reading=depict_only。",
    "buoyancy": "初中物理浮力：重5 N的小球用弹簧测力计悬挂并完全浸没在烧杯水中，未碰容器。图上测力计量程5 N、指示3 N，求浮力。题干不得说3 N，需要读图。液面和球半径仅定性。",
    "series": "初中物理：电池、闭合开关、电阻和电流表组成一个完整串联回路。文字明确所有连接，不用任何真实电表读数，问电流表应如何接入电路。需要整个回路示意图。",
    "geometry": "初中数学：正三角形内接于圆，圆半径为6 cm，求边长。文字完整表达条件，图中只有圆和内接正三角形，不画半径、角标、辅助线或答案；图不按比例。",
    "bar": "初中数学统计：甲、乙、丙三组数据分别10、20、15，用柱状图表示这些已知数据，求平均值。题干完整列出数据，图不要写平均值或答案。",
    "thermal": "初中物理：温度计玻璃泡完全浸没烧杯水中且不碰底，读出图示温度45 °C。刻度采用摄氏度，真实读取刻度，题干不得直接写45。温度图为essential，reading单位必须为°C。液面仅定性。只需要烧杯和温度计两个实体，不将液体和刻度拆为实体。",
    "function": "初中数学：根据y=x**2的函数图像，观察x从-3到3时图像如何变化，求最小值。题干明确函数与区间，图作为supplemental，纵轴[-1,9]只是展示范围。只有完整函数图一个实体，曲线从安全函数构造得到，不手绘。单位为空，x_range和y_range为range事实。不要额外标出答案。",
    "spring": "初中物理：水平弹簧左端固定在墙上，右端连接滑块，滑块放在光滑水平支撑面上。题干完整给出条件，问滑块被拉开后弹簧会如何作用。配图表达固定、连接和支撑，不画受力箭头，长度非定量。",
    "filtration": "初中化学：漏斗过滤液体，漏斗的导管末端紧贴盛有滤液的接收烧杯内壁，问为什么这样操作。文字已说明全部条件，只描绘漏斗和烧杯，配图作为supplemental，液面非定量。",
    "biology": "初中生物：题干说明叶绿体内有基粒和基质，问叶绿体与植物光合作用的关系。用完整叶绿体结构示意作为supplemental，实体只列叶绿体整个构造，不把内部片层拆成素材，不新增数值或数量事实。",
    "geography": "初中地理：地球经纬网示意，题干完整说明经线连接南北极、纬线平行赤道，问经纬网对定位有什么作用。用地球仪整体示意作为supplemental，实体只有完整地球仪，不增加国家或经纬度数值。",
    "molecule": "初中化学：题干已知水分子由2个氢原子和1个氧原子组成，问水分子能否保持水的化学性质。配图用水分子的完整结构示意，实体只列一个水分子完整构造，图作为supplemental；不用计数参数随机添原子。",
    "flow": "跨学科科学方法：实验流程按照提出问题、设计实验、观察记录、分析结果四步依次进行。题干列出这四个步骤，问为什么要记录观察结果。需要完整流程示意图作为supplemental，只声明一个完整流程图实体，标签来自题干的步骤，不能添加额外分支、结论或默认A/B标签。",
}

EXPANSION_SCENARIOS = {
    "new_sound": ("闭端气柱位移模态", "高中物理：一端闭合另一端开放的气柱，以mode=2绘制第二个允许模态（第三谐波）的位移包络。题文完整说明闭端位移节点、开端位移腹点，问为何不能把该曲线当成气流弯曲轨迹。mode为无量纲已知整数事实。"),
    "new_hydration": ("阳离子水合壳示意", "高中化学：Na⁺在水中形成水合壳，已知水分子氧端朝向Na⁺、氢端朝外，每个水分子有2H/1O。图为截面定性示意，不把图上水分子个数当作真实配位数。问为什么该图不能确定离子的真实配位数。允许Na⁺、O、H和水合层截面示意标识。"),
    "new_upwelling": ("沿岸上升流剖面", "高中地理：陆地在右，海水表层向左离岸，深层补偿流沿岸坡上升。已知深层水富含营养盐，问此环流为何有助于海洋生产力。图只表示题文明示离岸表层流、深层补偿流和陆地，没有风向或速度条件。"),
    "new_replication": ("半保留复制子代双链", "高中生物：DNA复制为半保留复制，亲代两个旧链，两个子代双链各有一个旧链和一个新链，旧链实线、新链虚线。问为何两个子代都保留亲代遗传信息。图只示意旧链和新链，不给碱基序列或新增数量。"),
    "new_redshift": ("谱线红移对照", "高中天文：参考与观测各四条谱线间距相同，观测谱线向波长λ增大方向整体平移，问为何识别谱线时应比较整组间距和整体移位。shift=35 diagram_px只用于非定量显示，不是nm或物理红移值，图无需标出35。允许参考、观测、波长λ标识。"),
    "new_unitcircle": ("单位圆正弦投影", "高中数学：单位圆上P对应角度30°，有x、y轴、半径1及水平/竖直投影线，求P的纵坐标。angle=30°必须绑定题文事实。图不直接标出坐标答案或sin数值。允许x、y、P和1。"),
    "new_probability": ("条件概率两层树", "高中数学统计：样本空间S先分A和Ā，P(A)=0.3，P(Ā)=0.7；仅A再分为B、B̄，P(B|A)=0.8、P(B̄|A)=0.2。问P(A∩B)。p_a=0.3、p_b_given_a=0.8为无量纲事实，两个事实属于完整概率树实体。图可写已知概率及条件分支：A，不能写0.24或联合概率答案，也不要画Ā下未知分支。"),
    "new_bridge": ("惠斯通电桥整体电路", "工程基础：四个电阻R₁、R₂、R₃、R₄构成菱形，G检流计接左右中点，电源接上下端。已知装置用检流计观察中点电势差，问为什么它能用于检验电桥平衡。题干完整表达关系，没有阻值或电压数值。不要画额外接线，不把整个电桥拆为未声明器件实体。"),
    "new_membrane": ("膜分离三股流", "环境科学：进料从左进入膜左腔，透过液穿过膜从右流出，浓缩液从左腔下方流出，问为何判断膜分离效果需同时观察透过液与浓缩液。图仅表达进料、膜、透过液、浓缩液的已知流向，无效率、孔径或浓度。"),
    "new_hydroponic": ("水培营养液循环", "农业科学：营养液从储槽经泵P流到根部水培槽，根浸在营养液中，液体从槽右端沿管回流储槽。问为何循环系统有助于根部获得营养。允许营养液、P标识，图不新增浓度、流量或作物数量条件。"),
}
for name, (material, scenario) in EXPANSION_SCENARIOS.items():
    SCENARIOS[name] = scenario + " 配图为supplemental，使用一个完整构图实体，presentation_constraints.preferred_material_names包含“" + material + "”。素材固定标识应显式登记允许的required_marks，不能增添待求结论。"


async def run(output, selected, variation=0):
    from app.core import paths
    from app.core.config import settings
    from app.core.llm_async import get_llm
    from app.core.json_utils import extract_json_object
    from app.core.quiz_verify import generate_verified_questions
    from app.illustration import preview
    render = preview.render
    if not settings.llm_api_key:
        raise RuntimeError("configured_llm_missing")
    settings.quiz_illustration_pipeline = "v2"
    settings.quiz_illustration_visual_review = "active"
    settings.llm_supports_images = True
    class RecordedLLM:
        supports_images = True
        def __init__(self, name):
            self.llm, self.name, self.calls = get_llm("quiz"), name, 0
        async def complete(self, **kwargs):
            self.calls += 1
            result = await self.llm.complete(**kwargs)
            # Synthetic answer-channel JSON only; never reasoning or credentials.
            (output / f"{self.name}-call-{self.calls}.json").write_text(result[0], encoding="utf-8")
            print(json.dumps({"case": self.name, "call": self.calls}, ensure_ascii=False), flush=True)
            return result
    report = {"model": settings.llm_model, "pipeline": "v2", "synthetic": True, "variation": variation, "cases": []}
    with tempfile.TemporaryDirectory(prefix="illustration-live-storage-") as runtime:
        paths.set_runtime_root(Path(runtime))
        for name in selected:
            scenario = SCENARIOS[name]
            if variation:
                if name == "new_sound":
                    scenario = scenario.replace("mode=2", "mode=3").replace("第二个允许模态（第三谐波）", "第三个允许模态（第五谐波）")
                elif name == "new_unitcircle":
                    scenario = scenario.replace("30", "60")
                elif name == "new_probability":
                    values = {"0.3": "0.4", "0.7": "0.6", "0.8": "0.7", "0.2": "0.3", "0.24": "0.28"}
                    scenario = re.sub(r"0\.\d+", lambda match: values.get(match[0], match[0]), scenario)
                elif name == "new_redshift":
                    scenario = scenario.replace("35", "50")
                scenario = scenario.replace("量程5 N", "量程4 N").replace("maximum=5", "maximum=4")
                scenario = scenario.replace("示数3 N", "示数2 N").replace("指示3 N", "指示2 N").replace("说3 N", "说2 N")
                scenario = scenario.replace("读数3 N", "读数2 N").replace("reading=3", "reading=2")
                scenario = scenario.replace("6 cm", "4 cm").replace("10、20、15", "12、24、18").replace("45", "35")
                scenario = scenario.replace("-3到3", "-2到2").replace("[-1,9]", "[-1,5]")
            client, started = RecordedLLM(name), time.monotonic()
            preview_count = 0
            rendered = {}
            def record_preview(image):
                nonlocal preview_count
                png = render(image)
                preview_count += 1
                (output / f"{name}-preview-{preview_count}.png").write_bytes(png)
                (output / f"{name}-preview-{preview_count}.svg").write_text(image.svg, encoding="utf-8")
                rendered[image.content_hash] = png
                return png
            preview.render = record_preview
            def parse(raw):
                data = extract_json_object(raw)
                return data.get("questions", []) if isinstance(data, dict) else []
            questions, meta = await generate_verified_questions(client,
                make_prompt=lambda: "生成一道中文 short_answer 题，满足以下合成验收情境：" + scenario +
                    "\n只返回 {questions:[{type,stem,answer,explanation,knowledge_point,difficulty,rubric_criteria,material_contract}]}。"
                    "difficulty=0.5。rubric_criteria 为 [{id,description,weight,critical}]，必须覆盖读图/关系和解答。"
                    "未按比例的图中半径/长度参数是图形像素，不能用 cm 值直接驱动像素；声明非定量示意。"
                    "补充图事实 source_ref=stem 且 source_quote 逐字出自题干。必要图隐藏的条件 source_ref=blueprint，数值真实且单位正确。",
                parse=parse, topic=name, grade="初中", temperature=.2, max_tokens=4500,
                illustration_policy="required", verify_mode="critic", max_attempts=2)
            row = {"case": name, "passed": bool(questions), "calls": client.calls,
                "elapsed_seconds": round(time.monotonic()-started, 2),
                "metrics": {k: v for k, v in meta.items() if k != "raw"}}
            for index, question in enumerate(questions):
                (output / f"{name}-{index}.json").write_text(json.dumps(question, ensure_ascii=False, indent=2), encoding="utf-8")
                from app.core.quiz_illustration import QuestionIllustration
                image = QuestionIllustration.model_validate(question["illustration"])
                (output / f"{name}-{index}.svg").write_text(image.svg, encoding="utf-8")
                # Preserve the exact PNG already inspected by both gates;
                # redundant browser launches cannot improve that evidence.
                png = rendered.get(image.content_hash)
                (output / f"{name}-{index}.png").write_bytes(png if png is not None else render(image))
            report["cases"].append(row)
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(row, ensure_ascii=False), flush=True)
        paths.set_runtime_root(None)
        preview.render = render
    return all(case["passed"] for case in report["cases"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-llm", action="store_true", help="explicitly enable configured provider calls")
    parser.add_argument("--output", type=Path, required=True, help="private local review artifacts (outside repository)")
    parser.add_argument("--cases", default=",".join(SCENARIOS))
    parser.add_argument("--variation", type=int, choices=[0, 1], default=0)
    args = parser.parse_args()
    if not args.live_llm:
        parser.error("--live-llm is required")
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("acceptance output must be outside the repository")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    names = args.cases.split(",")
    if set(names) - set(SCENARIOS):
        parser.error("unknown case")
    sys.exit(0 if asyncio.run(run(output, names, args.variation)) else 1)
