#!/usr/bin/env python3
"""Live illustration acceptance for frozen, synthetic CAT questions.

python3 scripts/acceptance/illustration/cat.py --live-llm --mode both \
    --output /tmp/cat-illustration-review --variation 0

No question generation, test-runner imports, real accounts or production data.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services/api"))
CASE_NAMES = ("geometry", "heating", "thermal", "function", "bar", "series", "motion", "cells", "coast", "flow")


def synthetic_questions(variation: int) -> dict[str, dict]:
    radius = 4 if variation else 6
    temperature = 35 if variation else 45
    bound = 2 if variation else 3
    ymax = 5 if variation else 9
    values = [12, 24, 18] if variation else [10, 20, 15]
    rows = {
        "geometry": (
            f"正三角形内接于圆，圆半径为{radius} cm，求正三角形的边长。",
            f"{radius}√3 cm", "正三角形的边长等于其外接圆半径的√3倍。",
        ),
        "heating": (
            "烧杯中的水用点燃的酒精灯加热，烧杯放在石棉网上，石棉网由三脚架支撑。为什么使用石棉网？",
            "使烧杯受热均匀。", "网面分散火焰传来的热量，使烧杯底部受热较均匀。",
        ),
        "thermal": (
            f"温度计玻璃泡完全浸没在烧杯水中，且不接触杯底，示数为{temperature} °C。"
            "读数时为什么要使视线与液柱上端相平？",
            "避免视差。", "视线与液柱上端相平时，可减少观察角度造成的读数误差。",
        ),
        "function": (
            f"已知二次函数y=x²，自变量x的区间为[-{bound},{bound}]，展示的纵轴区间为[-1,{ymax}]。"
            "求此自变量区间内的最小函数值。",
            "0", "x²总是非负，区间包含x=0，此时函数值为0。",
        ),
        "bar": (
            f"问卷统计显示，甲、乙、丙三组的数据分别为{values[0]}、{values[1]}、{values[2]}。"
            "求这三组数据的平均值。",
            str(sum(values) // 3), "将三组数据相加后除以3，得到平均值。",
        ),
        "series": (
            "电池、闭合开关、电阻和电流表组成一个完整串联回路。电流表应怎样连接，才能测量通过电阻的电流？",
            "电流表与电阻串联。", "串联回路各处电流相等，电流表应串联接入被测支路。",
        ),
        "motion": (
            "小车在水平导轨上向右匀速运动，题图展示小车、导轨及运动方向。小车受到的合外力有什么特点？",
            "合外力为零。", "匀速直线运动的速度不变，加速度为零，合外力也为零。",
        ),
        "cells": (
            "植物细胞和动物细胞均有细胞膜、细胞质及细胞核，植物细胞还具有细胞壁。比较二者共有的结构。",
            "细胞膜、细胞质和细胞核。", "依据题面两类细胞的结构条件，找出它们共有的三个结构。",
        ),
        "coast": (
            "海岸左侧为海面，右侧为陆地，近地面风由海面吹向陆地。判断此风向对应海风还是陆风。",
            "海风。", "近地面由海面吹向陆地的风为海风，图中方向对应此定义。",
        ),
        "flow": (
            "信息从输入端A依次经过筛选步骤B、处理步骤C，最后到达输出端D。途中经过几个处理步骤？",
            "两个。", "信息依次经过筛选和处理两个中间步骤，输入输出端不计为处理步骤。",
        ),
    }
    return {
        name: {"type": "short_answer", "stem": stem, "answer": answer,
               "explanation": explanation, "material_contract": {"visual_role": "supplemental"}}
        for name, (stem, answer, explanation) in rows.items()
    }


def frozen_material(name: str, question: dict, variation: int, mode="v2"):
    from app.agents.student_model.evaluation import schema as S
    from app.illustration.contracts import material_contract
    if mode == "v3":
        from app.illustration.v3_contracts import material_contract

    task = S.TaskSnapshot(
        question_id=f"q_cat_acceptance_{name}_v{variation}", question_revision=1,
        q_type=S.QuestionType.SHORT_ANSWER, stem=question["stem"], answer=question["answer"],
        explanation=question["explanation"],
        rubric=[S.FrozenCriterion(id="c1", description="依据题面条件准确说明结论", weight=1.0)],
    )
    contract = material_contract({**question, "rubric": [c.model_dump(mode="json") for c in task.rubric]},
        question_ref=task.question_id, revision=task.question_revision, frozen=True, grade="初中")
    return task, contract


async def run(output: Path, mode: str, selected: list[str], variation: int) -> bool:
    from app.core import paths
    from app.core.atomic import atomic_write_bytes, atomic_write_text

    def write_json(path: Path, value) -> None:
        atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))

    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous_root = paths.current_override_root()
    modes = ("v1", "v2", "v3") if mode == "all" else (("v1", "v2") if mode == "both" else (mode,))
    report = {"synthetic": True, "frozen_questions": True, "mode": mode,
              "variation": variation, "review_enabled": True, "cases": []}
    with tempfile.TemporaryDirectory(prefix="cat-illustration-live-") as sandbox:
        # auth_secret is the data root's sibling, so it also stays in sandbox.
        paths.set_runtime_root(Path(sandbox) / "data")
        try:
            from app.core.config import settings
            from app.core.llm_async import get_llm
            from app.core.quiz_illustration_enrichment import generate_assessment_illustration
            from app.identity.store import create_user, update_user
            from app.illustration import preview
            from app.illustration.orchestrator import PROMPT_VERSIONS, workflow
            from app.illustration.v3 import PROMPT_VERSIONS as V3_PROMPT_VERSIONS
            from app.prompts.registry import get as prompt

            if not settings.llm_api_key:
                report["failure_code"] = "configured_llm_missing"
                write_json(output / "report.json", report)
                print(json.dumps({"failure_code": "configured_llm_missing"}), flush=True)
                return False
            report["model"] = settings.llm_model
            report["image_input_supported"] = settings.llm_supports_images
            report["prompt_versions"] = {
                "v1": {name: prompt(name).version for name in (
                    "quiz_visual_requirements", "quiz_component_scene", "quiz_illustration_enrichment_audit")},
                "v2": PROMPT_VERSIONS,
                "v3": V3_PROMPT_VERSIONS,
            }
            if not settings.llm_supports_images:
                report["failure_code"] = "provider_unavailable"
                write_json(output / "report.json", report)
                print(json.dumps({"failure_code": "provider_unavailable"}), flush=True)
                return False
            owner = "usr_synthetic_cat_acceptance"
            user = create_user("synthetic-cat@acceptance.invalid", "Synthetic CAT", "unused-synthetic-hash",
                               user_id=owner)
            user.profile.prefs.update(quiz_svg_enabled=True, quiz_illustration_review_enabled=True)
            update_user(user)
            saved_review = settings.quiz_illustration_visual_review
            render = preview.render
            settings.quiz_illustration_visual_review = "active"

            class RecordedLLM:
                def __init__(self, directory: Path, case: str, implementation: str):
                    self.llm, self.directory = get_llm("quiz"), directory
                    self.case, self.mode, self.calls = case, implementation, 0

                async def complete(self, **kwargs):
                    self.calls += 1
                    call, started = self.calls, time.monotonic()
                    result = await self.llm.complete(**kwargs)
                    # complete()[0] is the answer channel; never save reasoning,
                    # provider configuration, request headers or image payloads.
                    atomic_write_text(self.directory / f"call-{call}.json", str(result[0] or ""))
                    usage = result[1] if len(result) > 1 else None
                    row = {"call": call, "elapsed_seconds": round(time.monotonic() - started, 2)}
                    if isinstance(usage, dict):
                        row["usage"] = {key: usage[key] for key in
                            ("prompt_tokens", "completion_tokens", "total_tokens") if key in usage}
                    write_json(self.directory / f"call-{call}-metadata.json", row)
                    print(json.dumps({"case": self.case, "mode": self.mode, **row}), flush=True)
                    return result

                async def close(self):
                    await self.llm.client.close()

            try:
                questions = synthetic_questions(variation)
                for name in selected:
                    for implementation in modes:
                        directory = output / f"{implementation}-{name}-v{variation}"
                        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                        task, contract = frozen_material(name, questions[name], variation, implementation)
                        before = task.model_dump_json()
                        write_json(directory / "frozen-task.json", task.model_dump(mode="json"))
                        write_json(directory / "input-contract.json", contract.model_dump(mode="json"))
                        client = RecordedLLM(directory, name, implementation)
                        started, events, rendered = time.monotonic(), [], {}
                        preview_count = 0

                        def record_preview(image):
                            nonlocal preview_count
                            png = render(image)
                            preview_count += 1
                            atomic_write_text(directory / f"preview-{preview_count}.svg", image.svg)
                            atomic_write_bytes(directory / f"preview-{preview_count}.png", png)
                            rendered[image.content_hash] = png
                            return png

                        def record_stage(stage, data):
                            event = {"stage": stage, "elapsed_seconds": round(time.monotonic() - started, 2)}
                            event.update({key: data[key] for key in (
                                "generation_calls", "illustration_repairs", "completion_tokens",
                                "candidate_count", "review_status", "failure_code") if key in data})
                            events.append(event)
                            write_json(directory / "stages.json", events)

                        preview.render = record_preview
                        row = {"case": name, "mode": implementation, "status": "failed", "passed": False}
                        try:
                            if implementation == "v1":
                                result = await generate_assessment_illustration(
                                    student_id=owner, task=task, policy="required", llm=client)
                                image = result.get("illustration")
                            else:
                                result = await workflow(client, contract, "required", frozen=True,
                                                        owner=owner, stage=record_stage, pipeline_mode=implementation)
                                compiled = result.get("compiled")
                                image = compiled.illustration if compiled else None
                                actual_contract = result.get("contract")
                                if actual_contract is not None and (
                                        actual_contract.public_question != contract.public_question or
                                        actual_contract.authoring_gold != contract.authoring_gold or
                                        actual_contract.question_ref != contract.question_ref or
                                        actual_contract.question_revision != contract.question_revision):
                                    raise RuntimeError("frozen_question_changed")
                                if compiled:
                                    write_json(directory / "source.json", compiled.source.model_dump(mode="json"))
                                    write_json(directory / "reviews.json", result["reviews"])
                            if task.model_dump_json() != before:
                                raise RuntimeError("frozen_question_changed")
                            row.update(status=result["status"], code=result.get("code"),
                                       metrics=result.get("metrics", {}))
                            if result["status"] == "ready" and image is not None:
                                png = result.get("png") or rendered.get(image.content_hash) or record_preview(image)
                                atomic_write_text(directory / "final.svg", image.svg)
                                atomic_write_bytes(directory / "final.png", png)
                                row.update(passed=True, content_hash=image.content_hash,
                                           svg=str((directory / "final.svg").relative_to(output)),
                                           png=str((directory / "final.png").relative_to(output)))
                        except Exception as exc:
                            # Provider exception text may contain credentials or
                            # payloads. Only a closed code/type enters the report.
                            code = getattr(exc, "code", None)
                            if type(exc) is RuntimeError and exc.args == ("frozen_question_changed",):
                                code = "frozen_question_changed"
                            row.update(status="failed", code=code or "acceptance_failed",
                                       error_type=type(exc).__name__)
                            if events:
                                row["metrics"] = {key: events[-1][key] for key in
                                    ("generation_calls", "illustration_repairs", "completion_tokens")
                                    if key in events[-1]}
                        finally:
                            preview.render = render
                            try:
                                await client.close()
                            except Exception as exc:
                                row.update(status="failed", passed=False, code="acceptance_cleanup_failed",
                                           error_type=type(exc).__name__)
                        row.update(calls=client.calls, elapsed_seconds=round(time.monotonic() - started, 2))
                        report["cases"].append(row)
                        write_json(output / "report.json", report)
                        print(json.dumps(row, ensure_ascii=False), flush=True)
            finally:
                preview.render = render
                settings.quiz_illustration_visual_review = saved_review
        except Exception as exc:
            report.update(failure_code="acceptance_failed", error_type=type(exc).__name__)
            write_json(output / "report.json", report)
            print(json.dumps({"failure_code": "acceptance_failed", "error_type": type(exc).__name__}), flush=True)
            return False
        finally:
            paths.set_runtime_root(previous_root)
    return bool(report["cases"]) and all(row["passed"] for row in report["cases"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-llm", action="store_true", help="explicitly allow configured provider calls")
    parser.add_argument("--mode", choices=("v1", "v2", "v3", "both", "all"), default="both")
    parser.add_argument("--output", type=Path, required=True, help="artifact directory outside the repository")
    parser.add_argument("--variation", type=int, choices=(0, 1), default=0)
    parser.add_argument("--cases", default=",".join(CASE_NAMES[:6]), help="comma-separated subset of the six cases")
    args = parser.parse_args()
    if not args.live_llm:
        parser.error("--live-llm is required")
    output = args.output.expanduser().resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("--output must be outside the repository")
    selected = [name.strip() for name in args.cases.split(",")]
    if not selected or set(selected) - set(CASE_NAMES) or len(set(selected)) != len(selected):
        parser.error("--cases must contain distinct names from " + ",".join(CASE_NAMES))
    return 0 if asyncio.run(run(output, args.mode, selected, args.variation)) else 1


if __name__ == "__main__":
    sys.exit(main())
