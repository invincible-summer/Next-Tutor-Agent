"""Explicit paid-LLM acceptance; redirects all app storage to a temporary sandbox.

Run from repo root: python scripts/acceptance/classroom/live.py --output /tmp/course-live
Uses synthetic material only. Not included in unittest discovery.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "api"))


async def generate(output: Path, resume: Path | None = None, brief_path: Path | None = None) -> None:
    # Resolve the live client before importing keyless regression-test helpers.
    from app.core.llm_async import get_llm
    llm = get_llm('classroom')
    from tests.storage_sandbox import StorageSandboxTestCase
    from app.classroom.pipeline import ClassroomPipeline, PipelineDeps
    from app.classroom.render.compiler import compile_html
    from app.core import classroom_store as store
    from app.core import workspace as ws_mod
    from app.schemas import classroom as sc
    from app.prompts.registry import active_versions
    sandbox = StorageSandboxTestCase()
    sandbox.setUp()
    output.mkdir(parents=True, exist_ok=True)
    try:
        owner, workspace = 'usr_liveacceptance', 'ws_live_课堂验收'
        ws_mod.save_workspace(ws_mod.Workspace(workspace_id=workspace, name='课堂验收', student_id=owner))
        brief = sc.LessonBrief(
            topic='从平均速度到瞬时速度：导数的第一课',
            goals=['理解变化量与平均变化率', '用割线逼近理解瞬时速度', '用定义计算二次函数在一点的导数'],
            source_policy='textbook_plus', research=sc.ResearchBrief(enabled=False),
            duration_minutes=15, page_plan='6_9', theme_id='academic_clear@2', grade='大一',
            image_density='none', checkpoint_density='light', content_review_enabled=True,
            custom_requirements=r'规划8页，制作适合课堂投影的专业教学课件。以 x(t)=t^2 米为贯穿案例，准确使用单位、条件和极限。让学生先预测、再看数值和图、最后推导及迁移。用真正服务理解的函数图，不强行套同一版式。普通文本里也可以出现 \Delta、\delta 等数学符号。')
        if brief_path:
            brief = sc.LessonBrief.model_validate_json(brief_path.read_text())
            if brief.source_selection.files or brief.source_selection.extra_sessions:
                raise ValueError('Live acceptance uses synthetic briefs without private source references')
        (output / 'brief.json').write_text(brief.model_dump_json(indent=2))
        lesson, jobid, now = store.new_id('les'), store.new_id('job'), store.utcnow()
        store.save_lesson(sc.Lesson(lesson_id=lesson, owner_id=owner, workspace_id=workspace,
            title=brief.topic, created_at=now, updated_at=now, latest_job_id=jobid))
        target = store.allocate_revision(owner, workspace, lesson)
        job = sc.GenerationJob(job_id=jobid, owner_id=owner, workspace_id=workspace,
            lesson_id=lesson, target_revision=target, renderer_version='2.0.0',
            slide_prompt_version=active_versions()['classroom_slide'],
            brief_hash=store.canonical_hash(brief.model_dump(mode='json', by_alias=True)),
            created_at=now, updated_at=now)
        if resume:
            previous = sc.GenerationJob.model_validate_json((resume / 'job.json').read_text())
            if previous.brief_hash != job.brief_hash or previous.slide_prompt_version != job.slide_prompt_version:
                raise ValueError('Resume requires the same brief and prompt version')
            job.artifacts = previous.artifacts
            job.budget = previous.budget
            stages = store.stages_dir(owner, workspace, lesson, jobid)
            stages.mkdir(parents=True, exist_ok=True)
            for name in job.artifacts:
                (stages / f'{name}.json').write_bytes((resume / f'{name}.json').read_bytes())
        store.save_job(job)
        store.stage_file(store.job_root(owner, workspace, lesson, jobid), 'brief.json', brief.model_dump_json())
        pipeline = ClassroomPipeline(owner, workspace, lesson, jobid, PipelineDeps(llm=llm))
        print(json.dumps({'event': 'started', 'model': llm.model}), flush=True)
        task = asyncio.create_task(pipeline.run())
        try:
            while not task.done():
                await asyncio.wait([task], timeout=20)
                current = store.load_job(owner, workspace, lesson, jobid)
                print(json.dumps({'event': 'progress', 'state': current.state.value,
                    'phase': current.phase.value if current.phase else None,
                    'calls': current.budget.llm_calls_used}), flush=True)
            result = await task
        finally:
            current = store.load_job(owner, workspace, lesson, jobid)
            (output / 'job.json').write_text(current.model_dump_json(indent=2, by_alias=True))
            for stage in store.stages_dir(owner, workspace, lesson, jobid).glob('*.json'):
                (output / stage.name).write_bytes(stage.read_bytes())
        revision = store.load_revision(owner, workspace, lesson, target)
        if revision is None and pipeline._artifact_path('review').exists():
            revision = pipeline._assemble_revision(result)
        if revision:
            (output / 'revision.json').write_text(revision.model_dump_json(indent=2, by_alias=True))
            (output / 'course.html').write_text(compile_html(revision, mode='offline'))
        summary = {'model': llm.model, 'state': result.state.value, 'error': result.last_error,
            'pages': len(revision.slides) if revision else 0, 'budget': result.budget.model_dump(mode='json')}
        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    finally:
        await llm.client.close()
        sandbox.tearDown()
        sandbox.doCleanups()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--resume', type=Path, help='Reuse stages from an earlier run of this same acceptance brief')
    parser.add_argument('--brief', type=Path, help='Synthetic LessonBrief JSON for another subject; no private source references')
    args = parser.parse_args()
    asyncio.run(generate(args.output.resolve(), args.resume.resolve() if args.resume else None,
                         args.brief.resolve() if args.brief else None))
