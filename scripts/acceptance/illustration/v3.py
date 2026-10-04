#!/usr/bin/env python3
"""Live V3 question authoring, essential stimuli, task registration and freezing.

Only project-authored synthetic inputs and temporary accounts are used. Output
must stay outside the repository; provider reasoning and secrets are not saved.
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


async def run(output: Path, stimulus: str = "reading") -> bool:
    from app.core import paths
    previous_root = paths.current_override_root()
    report = {"synthetic": True, "pipeline_mode": "v3", "stimulus": stimulus, "passed": False}
    with tempfile.TemporaryDirectory(prefix="v3-authoring-live-") as sandbox:
        paths.set_runtime_root(Path(sandbox) / "data")
        try:
            from app.agents.assessment.manager import register_task_snapshots, task_snapshot_from_quiz_dict
            from app.core.atomic import atomic_write_bytes, atomic_write_text
            from app.core.config import settings
            from app.core.json_utils import extract_json_object
            from app.core.llm_async import get_llm
            from app.core.quiz_verify import generate_verified_questions
            from app.identity.store import create_user, update_user
            from app.illustration import persistence
            from app.illustration.v3 import PROMPT_VERSIONS

            output.mkdir(parents=True, exist_ok=True, mode=0o700)
            def write_json(path, value):
                atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))

            provider = get_llm("quiz")
            user = create_user("synthetic-v3@acceptance.invalid", "Synthetic V3",
                               "unused-synthetic-hash", user_id="usr_synthetic_v3_acceptance")
            owner = user.id
            user.profile.prefs.update(quiz_svg_enabled=True, quiz_illustration_mode="v3")
            update_user(user)

            class RecordedLLM:
                supports_images = settings.llm_supports_images
                calls = 0
                async def complete(self, **kwargs):
                    self.calls += 1
                    call = self.calls
                    result = await provider.complete(**kwargs)
                    atomic_write_text(output / f"call-{call}.json", str(result[0] or ""))
                    print(json.dumps({"call": call}), flush=True)
                    return result

            client = RecordedLLM()
            started = time.monotonic()
            try:
                prompt = """为隔离验收生成2道项目自制的初中short_answer题，只输出{"questions":[...]}。
每题包含type、stem、answer、explanation、rubric_criteria和visual_spec。
第一题为真实必要读图题：学生只能依据温度计图读取液柱所指温度，液柱实际为7°C，量程0到10°C，最小分度1°C，每5°C印数字。
7仅进入visual_spec.drawing_inputs的depict_only值及私有答案/解析/量规，公开题干不直接给读数。其visual_role为essential。
第二题为小车在水平导轨上向右匀速运动的合外力问题，文字本身独立可答，visual_role为supplemental；图只表达车、轨道和运动方向，不标合外力答案。
配图由后续V3模型自由加工素材并绘制缺失元素，按其轻量visual_spec合同返回，禁止SVG及审核字段。"""
                if stimulus == "count":
                    prompt = prompt.replace(
                        "第一题为真实必要读图题：学生只能依据温度计图读取液柱所指温度，液柱实际为7°C，量程0到10°C，最小分度1°C，每5°C印数字。\n7仅进入visual_spec.drawing_inputs的depict_only值及私有答案/解析/量规，公开题干不直接给读数。其visual_role为essential。",
                        "第一题为真实必要读图题：图中有3个彼此分离、等大的圆形，学生只能数图中的圆形获得数量。数量3只进入drawing_inputs的depict_only值及私有答案/解析/量规，不在公开题干、标签、alt或caption中写出圆形总数。其visual_role为essential。")
                rows, meta = await generate_verified_questions(client, student_id=owner,
                    illustration_mode="v3", make_prompt=lambda: prompt,
                    parse=lambda raw: (extract_json_object(raw) or {}).get("questions", []),
                    topic="合成读图与运动", grade="初中", temperature=.1, max_tokens=5000,
                    illustration_policy="required", verify_mode="basic")
                write_json(output / "generation.json", {"questions": rows, "verification": meta})
                tasks = [task_snapshot_from_quiz_dict({**row, "question_id": f"q_v3_live_{i}"})
                         for i, row in enumerate(rows, 1)]
                register_task_snapshots(owner, tasks)
                results = []
                for index, task in enumerate(tasks, 1):
                    artifact = persistence.read(owner, "artifacts", task.illustration_artifact_id)
                    atomic_write_text(output / f"question-{index}.svg", task.illustration.svg)
                    atomic_write_bytes(output / f"question-{index}.png",
                        (persistence.owner_dir(owner) / "previews" / f"{artifact['artifact_id']}.png").read_bytes())
                    write_json(output / f"question-{index}-task.json", task.model_dump(mode="json"))
                    results.append({"question_id": task.question_id, "visual_role": task.visual_role,
                        "artifact_id": task.illustration_artifact_id,
                        "gates": task.diagram_source.review_gates,
                        "canonical_identity_bound": task.material_contract.question_ref == task.question_id})
                report.update(questions=results, calls=client.calls, prompt_versions=PROMPT_VERSIONS,
                    model=settings.llm_model, elapsed_seconds=round(time.monotonic()-started, 2))
                report["passed"] = len(tasks) == 2 and any(task.visual_role == "essential" for task in tasks)
            finally:
                await provider.client.close()
        except Exception as exc:
            report.update(failure_code=getattr(exc, "code", "acceptance_failed"), error_type=type(exc).__name__)
        finally:
            from app.core.atomic import atomic_write_text
            atomic_write_text(output / "report.json", json.dumps(report, ensure_ascii=False, indent=2))
            paths.set_runtime_root(previous_root)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report["passed"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-llm", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stimulus", choices=("reading", "count"), default="reading")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        parser.error("Output must be outside the repository")
    return 0 if asyncio.run(run(output, args.stimulus)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
