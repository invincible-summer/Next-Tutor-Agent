"""课堂生成管线：九阶段与 checked artifact（plan.md §15.1/§15.2，D02）。

每阶段产物写 ``jobs/<job_id>/stages/<phase>.json`` 并把内容 hash 记进
job.artifacts；重试/恢复时 hash 未变的阶段直接复用（不重算、不重花钱）。
worker（D04）负责调度与并发；本模块只做"跑一个 job 到终态"。

阶段顺序：resolve_sources → research → outline → visual_assets →
author_slides → checkpoints → review → render → publish。

测试注入口（PipelineDeps）：fake LLM / mock research / mock 图库 /
排版检查替换 / crash_at 阶段名（在该阶段开始时抛 PipelineCrash 模拟
进程中断，验证 §15.2 恢复语义）。
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from ..core import classroom_store as store
from ..prompts.registry import active_versions
from ..schemas import classroom as sc
from . import limits, sources, validation
from .errors import ClassroomError
from .llm_budget import LLMUsageBudget
from .llm_io import clamp_pages, generate_json
from .media.service import ImageSearchService
from .research.base import ResearchBudget, WebResearchProvider

PHASES: list[sc.JobPhase] = [
    sc.JobPhase.resolve_sources, sc.JobPhase.research, sc.JobPhase.outline,
    sc.JobPhase.visual_assets, sc.JobPhase.author_slides,
    sc.JobPhase.checkpoints, sc.JobPhase.review, sc.JobPhase.render,
    sc.JobPhase.publish,
]

TERMINAL_STATES = (sc.JobState.succeeded, sc.JobState.failed,
                   sc.JobState.cancelled)


class PipelineCrash(Exception):
    """测试注入的"进程中断"：job 停留在 running，下次 run() 从 artifact 恢复。"""


@dataclass
class PipelineDeps:
    llm: Any
    research: WebResearchProvider | None = None
    images: ImageSearchService | None = None
    layout_check: Callable[[str], Any] | None = None
    checkpoint_author: Callable[..., Any] | None = None  # D03 注入
    crash_at: str | None = None


@dataclass
class _Budgets:
    llm: LLMUsageBudget
    research: ResearchBudget
    image: Any
    downloads_bytes: int = 0


def _dump_json(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


def _evidence_pack(records: list[sc.SourceRecord], limit_chars: int) -> str:
    """证据包文本：<material_excerpt>/<web_excerpt> 数据边界 + src_ 元数据。"""
    parts: list[str] = []
    used = 0
    for record in records:
        tag = ("web_excerpt" if record.kind == sc.SourceKind.web
               else "material_excerpt")
        head = (f'<{tag} source_id="{record.source_id}" '
                f'title="{record.title}" '
                f'kind="{record.kind.value}"'
                + (f' page={record.locator.page}'
                   if getattr(record.locator, "page", None) else "")
                + (f' published_at={record.published_at.isoformat()}'
                   if record.published_at else "")
                + ">")
        body = record.excerpt[:max(0, limit_chars - used)]
        if not body:
            continue
        used += len(body)
        parts.append(f"{head}\n{body}\n</{tag}>")
    return "\n".join(parts)


class ClassroomPipeline:
    def __init__(self, owner: str, workspace_id: str, lesson_id: str,
                 job_id: str, deps: PipelineDeps) -> None:
        self.owner = owner
        self.workspace_id = workspace_id
        self.lesson_id = lesson_id
        self.job_id = job_id
        self.deps = deps
        self.warnings: list[str] = []
        self._llm_call_budget: int | None = None

    # ------------------------------------------------------------------ 基础

    def _load_job(self) -> sc.GenerationJob:
        job = store.load_job(self.owner, self.workspace_id, self.lesson_id,
                             self.job_id)
        if job is None:
            raise ClassroomError("source_not_found", "任务不存在")
        return job

    def _update_job(self, **changes: Any) -> sc.GenerationJob:
        """CAS 循环更新 job 字段（io/模型调用不持文件锁，提交时核对）。"""
        def mutate(job: sc.GenerationJob) -> None:
            effective = changes
            if job.cancel_requested and "state" in changes and \
                    changes["state"] not in (sc.JobState.cancelled,
                                             sc.JobState.succeeded):
                effective = {**changes, "state": sc.JobState.cancelled,
                             "phase": None}
            for key, value in effective.items():
                setattr(job, key, value)

        for _attempt in range(4):
            try:
                return store.update_job(
                    self.owner, self.workspace_id, self.lesson_id,
                    self.job_id, mutate)
            except store.CasConflictError:
                continue
        raise ClassroomError("generation_failed", "job 更新冲突")

    def _artifact_path(self, phase: str) -> Path:
        return store.stages_dir(self.owner, self.workspace_id,
                                self.lesson_id, self.job_id) / f"{phase}.json"

    def _stage_done(self, phase: sc.JobPhase, job: sc.GenerationJob) -> bool:
        recorded = job.artifacts.get(phase.value)
        if not recorded:
            return False
        path = self._artifact_path(phase.value)
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        return store.canonical_hash(payload) == recorded

    def _save_stage(self, phase: sc.JobPhase, payload: dict[str, Any],
                    *, inputs: dict[str, str] | None = None) -> None:
        path = self._artifact_path(phase.value)
        path.parent.mkdir(parents=True, exist_ok=True)
        store.stage_file(path.parent, path.name,
                         json.dumps(payload, ensure_ascii=False, indent=1))
        changes: dict[str, Any] = {}
        def _apply(job: sc.GenerationJob) -> None:
            job.artifacts[phase.value] = store.canonical_hash(payload)
            if inputs:
                job.stage_inputs.update(inputs)
        self._mutate(_apply)

    def _mutate(self, fn: Callable[[sc.GenerationJob], None]) -> None:
        for _attempt in range(4):
            try:
                store.update_job(self.owner, self.workspace_id, self.lesson_id,
                                 self.job_id, fn)
                return
            except store.CasConflictError:
                continue
        raise ClassroomError("generation_failed", "job 更新冲突")

    def _read_stage(self, phase: sc.JobPhase) -> dict[str, Any]:
        return json.loads(
            self._artifact_path(phase.value).read_text(encoding="utf-8"))

    def _persist_budgets(self, budgets: _Budgets, started: float) -> None:
        def _apply(job: sc.GenerationJob) -> None:
            job.budget.llm_calls_used = budgets.llm.calls_used
            job.budget.llm_input_tokens_used = budgets.llm.input_used
            job.budget.llm_output_tokens_used = budgets.llm.output_used
            job.budget.search_calls_used = budgets.research.search_calls
            job.budget.extract_calls_used = budgets.research.extract_urls
            job.budget.image_searches_used = budgets.image.calls
            job.budget.active_seconds_used = round(
                job.budget.active_seconds_used + (time.monotonic() - started),
                1)
        self._mutate(_apply)
        budgets.llm.remaining_seconds = max(
            0.0, limits.JOB_DEADLINE_SECONDS
            - self._load_job().budget.active_seconds_used)

    # ------------------------------------------------------------------ 主循环

    async def run(self) -> sc.GenerationJob:
        job = self._load_job()
        if job.state in TERMINAL_STATES:
            return job
        if job.state == sc.JobState.queued:
            job = self._update_job(state=sc.JobState.running)

        # 恢复预算（从持久化 JobBudget 重建，不因重启获得无限预算）
        job = self._load_job()
        from .media.base import ImageSearchBudget
        call_budget = limits.llm_call_budget(10)  # 页数未知前用保守值
        budgets = _Budgets(
            llm=LLMUsageBudget(
                call_budget=max(1, call_budget - job.budget.llm_calls_used)),
            research=ResearchBudget(
                search_calls=job.budget.search_calls_used,
                extract_urls=job.budget.extract_calls_used),
            image=ImageSearchBudget(calls=job.budget.image_searches_used),
        )
        token = _set_llm_hook(budgets.llm)
        stage_started = time.monotonic()
        try:
            for phase in PHASES:
                job = self._load_job()
                if job.state in TERMINAL_STATES:
                    return job
                if job.cancel_requested:
                    return self._update_job(
                        state=sc.JobState.cancelled, phase=None,
                        last_error=None)
                if self._stage_done(phase, job):
                    continue
                if self.deps.crash_at == phase.value:
                    raise PipelineCrash(phase.value)
                self._persist_budgets(budgets, 0.0)
                self._update_job(phase=phase)
                handler = getattr(self, f"_stage_{phase.value}")
                try:
                    await handler(job, budgets)
                except ClassroomError:
                    raise
                self._persist_budgets(budgets, time.monotonic() - stage_started)
                stage_started = time.monotonic()
            return self._load_job()
        except (PipelineCrash,):
            raise
        except _NeedsInputStop:
            return self._load_job()
        except _AwaitingOutlineStop:
            return self._load_job()
        except ClassroomError as exc:
            failed = self._load_job()
            if failed.state in TERMINAL_STATES:
                return failed
            return self._update_job(
                state=sc.JobState.failed, phase=None,
                last_error=f"{exc.code}: {exc.message}"[:2000])
        finally:
            _reset_llm_hook(token)

    # ------------------------------------------------------------------ 阶段 1

    async def _stage_resolve_sources(self, job: sc.GenerationJob,
                                     budgets: _Budgets) -> None:
        brief = self._load_brief()
        resolved = sources.resolve_classroom_sources(
            self.owner, self.workspace_id, brief.source_selection,
            topic=brief.topic, goals=brief.goals)
        blocking = [i for i in resolved.issues
                    if i.code == "source_unauthorized"]
        if resolved.records and not blocking:
            self._save_stage(sc.JobPhase.resolve_sources, {
                "records": [_dump_json(r) for r in resolved.records],
                "scope_fingerprint": resolved.scope_fingerprint,
                "issues": [{"code": i.code, "ref": i.ref,
                            "message": i.message} for i in resolved.issues],
            }, inputs={"resolve_sources": job.brief_hash})
            return
        # strict 必须有教材来源；其他策略无来源也可继续（web_topic）
        if brief.source_policy == sc.SourcePolicy.strict_textbook \
                and not resolved.records:
            self._needs_input("所选教材/资料不可用或尚未解析完成，"
                              "请重新选择来源")
        self._save_stage(sc.JobPhase.resolve_sources, {
            "records": [], "scope_fingerprint": resolved.scope_fingerprint,
            "issues": [{"code": i.code, "ref": i.ref,
                        "message": i.message} for i in resolved.issues],
        }, inputs={"resolve_sources": job.brief_hash})

    def _needs_input(self, message: str) -> None:
        self._update_job(state=sc.JobState.needs_input, phase=None,
                         last_error=message[:2000])
        raise _NeedsInputStop(message)

    def _load_brief(self) -> sc.LessonBrief:
        path = store.job_brief_path(self.owner, self.workspace_id,
                                    self.lesson_id, self.job_id)
        if not path.exists():
            raise ClassroomError("source_not_found", "任务 Brief 缺失")
        return sc.LessonBrief.model_validate(
            json.loads(path.read_text(encoding="utf-8")))

    # ------------------------------------------------------------------ 阶段 2

    async def _stage_research(self, job: sc.GenerationJob,
                              budgets: _Budgets) -> None:
        brief = self._load_brief()
        records: list[sc.SourceRecord] = []
        attempts: list[dict[str, str]] = []
        needed = (brief.research.enabled
                  and brief.source_policy != sc.SourcePolicy.strict_textbook
                  and self.deps.research is not None)
        if needed:
            provider = self.deps.research
            base = self._read_stage(sc.JobPhase.resolve_sources)
            existing = [sc.SourceRecord.model_validate(r)
                        for r in base.get("records", [])]
            plan, _ = await generate_json(
                self.deps.llm, prompt_id="classroom_search_plan",
                user_text=json.dumps({
                    "topic": brief.topic,
                    "goals": brief.goals,
                    "policy": brief.source_policy.value,
                    "timeliness": brief.research.timeliness.value,
                    "covered": [r.title for r in existing],
                    "language": brief.language.value,
                }, ensure_ascii=False),
                model_cls=_SearchPlanModel)
            for item in plan.queries[:limits.SEARCH_CALL_BUDGET]:
                if budgets.research.search_calls >= limits.SEARCH_CALL_BUDGET:
                    break
                try:
                    hits = await provider.search(
                        item.query, timeliness=item.timeliness,
                        owner=self.owner, budget=budgets.research)
                except ClassroomError as exc:
                    attempts.append({"query": item.query,
                                     "error": exc.code})
                    continue
                targets = [h.url for h in hits
                           [:limits.EXTRACT_URL_BUDGET]][:3]
                if not targets:
                    continue
                try:
                    outcome = await provider.extract(
                        targets, owner=self.owner, budget=budgets.research)
                except ClassroomError as exc:
                    attempts.append({"query": item.query,
                                     "error": exc.code})
                    continue
                for page in outcome.pages:
                    from urllib.parse import urlparse
                    domain = urlparse(page.url).hostname or "unknown"
                    records.append(sc.SourceRecord(
                        source_id=store.new_id("src"),
                        kind=sc.SourceKind.web,
                        title=page.title or page.url[:200],
                        locator=sc.WebLocator(
                            url=page.url[:2048],
                            canonical_url=page.canonical_url[:2048],
                            domain=domain[:200],
                            publisher=None,
                            retrieved_at=page.retrieved_at),
                        excerpt=page.text[:limits.EXCERPT_CHARS_PER_MATERIAL],
                        excerpt_hash=store.canonical_hash(page.text),
                        retrieved_at=page.retrieved_at,
                        published_at=page.published_at,
                        as_of=date.today(),
                    ))
                for failure in outcome.failures:
                    self.warnings.append(
                        f"网页提取失败：{failure.url[:120]}")
            # web_topic 的"最新"请求必须检索成功（§7.2）
            if brief.source_policy == sc.SourcePolicy.web_topic \
                    and brief.research.timeliness in ("day", "week") \
                    and not records:
                self._needs_input(
                    "该主题需要最新外部资料，但检索服务不可用；"
                    "可关闭联网改为未核验内容或稍后重试")
        self._save_stage(sc.JobPhase.research, {
            "records": [_dump_json(r) for r in records],
            "attempts": attempts,
        }, inputs={"research": job.brief_hash})

    # ------------------------------------------------------------------ 阶段 3

    async def _stage_outline(self, job: sc.GenerationJob,
                             budgets: _Budgets) -> None:
        brief = self._load_brief()
        from .templates import pedagogy_by_id
        template = pedagogy_by_id(brief.pedagogy_id)
        if template is None:
            raise ClassroomError("content_invalid",
                                 f"未知教学模板 {brief.pedagogy_id}")
        sources_payload = self._read_stage(sc.JobPhase.resolve_sources)
        records = [sc.SourceRecord.model_validate(r)
                   for r in sources_payload.get("records", [])]
        research_payload = self._read_stage(sc.JobPhase.research)
        web_records = [sc.SourceRecord.model_validate(r)
                       for r in research_payload.get("records", [])]
        evidence = _evidence_pack(records + web_records,
                                  limits.EVIDENCE_CHARS_TOTAL)
        user_text = json.dumps({
            "brief": _dump_json(brief),
            "template_page_flow": list(template.page_flow),
            "quality_rules": template.quality_rules,
            "evidence_chars_budget": limits.EVIDENCE_CHARS_TOTAL,
        }, ensure_ascii=False) + "\n\n" + evidence
        plan, _ = await generate_json(
            self.deps.llm, prompt_id="classroom_outline",
            user_text=user_text, model_cls=_OutlineModel)
        # 页数夹紧（§15.4 区间）与目标页数覆盖；再规范成 schema 模型
        clamped = clamp_pages(len(plan.pages), brief.duration_minutes,
                              int(str(brief.page_plan))
                              if str(brief.page_plan).isdigit() else None)
        normalized = sc.OutlinePlan.model_validate({
            "objectives": [
                {"objective_id": o.objective_id, "text": o.text,
                 "evidence_status": o.evidence_status}
                for o in plan.objectives[:12]],
            "pages": [p.model_dump() for p in plan.pages[:clamped]],
            "glossary": [g.model_dump() for g in plan.glossary],
            "scope_note": plan.scope_note,
            "uncovered_note": plan.uncovered_note,
            "total_budget_seconds": plan.total_budget_seconds,
        })
        for index, page in enumerate(normalized.pages, start=1):
            page.order = index
        payload = {"plan": _dump_json(normalized)}
        self._save_stage(sc.JobPhase.outline, payload,
                         inputs={"outline": store.canonical_hash(payload)})
        if job.start_mode == sc.StartMode.outline_first:
            self._update_job(state=sc.JobState.awaiting_outline, phase=None)
            raise _AwaitingOutlineStop()

    # ------------------------------------------------------------------ 阶段 4

    async def _stage_visual_assets(self, job: sc.GenerationJob,
                                   budgets: _Budgets) -> None:
        plan = sc.OutlinePlan.model_validate(
            self._read_stage(sc.JobPhase.outline)["plan"])
        records: list[dict[str, Any]] = []
        if self.deps.images is None or not self.deps.images.available:
            self._save_stage(sc.JobPhase.visual_assets,
                             {"records": [], "warnings": ["无可用图库，"
                              "确定性使用无图布局"]})
            return
        from .media.base import ImageCandidate
        from .media.download import download_and_sanitize
        service = self.deps.images
        seen_intents: set[str] = set()
        semaphore = asyncio.Semaphore(limits.IMAGE_DOWNLOAD_CONCURRENCY)
        used_bytes = 0
        for page in plan.pages:
            if len(records) >= limits.IMAGE_COUNT_MAX:
                break
            intent = page.visual_intent
            if intent is None or not intent.query_terms:
                continue
            key = " ".join(intent.query_terms)
            if key in seen_intents:  # 合并同主题意图
                continue
            seen_intents.add(key)
            try:
                candidates = await service.search(
                    list(intent.query_terms), owner=self.owner,
                    orientation=intent.orientation or "landscape",
                    locale="zh-CN", budget=budgets.image)
            except ClassroomError:
                continue
            chosen = _pick_candidate(candidates, intent.orientation)
            if chosen is None:
                continue
            async with semaphore:
                try:
                    processed = await download_and_sanitize(
                        chosen.download_url)
                except ClassroomError:
                    # 同意图下一个候选替代一次（§8.3.10）
                    backup = _pick_candidate(
                        [c for c in candidates if c.candidate_id
                         != chosen.candidate_id], intent.orientation)
                    if backup is None:
                        self.warnings.append("图片下载失败，改用无图布局")
                        continue
                    try:
                        processed = await download_and_sanitize(
                            backup.download_url)
                        chosen = backup
                    except ClassroomError:
                        self.warnings.append("图片下载失败，改用无图布局")
                        continue
            if used_bytes + len(processed.data) > \
                    limits.IMAGE_DOWNLOAD_TOTAL_BYTES:
                break
            if len(processed.data) > limits.IMAGE_BYTES_HARD_MAX:
                continue
            used_bytes += len(processed.data)
            budgets.downloads_bytes = used_bytes
            asset_id = store.new_id("ast")
            ext = "png" if processed.mime == "image/png" else "webp"
            store.stage_file(
                store.asset_file_path(
                    self.owner, self.workspace_id, self.lesson_id,
                    asset_id, ext).parent,
                store.asset_file_path(
                    self.owner, self.workspace_id, self.lesson_id,
                    asset_id, ext).name,
                processed.data)
            record = sc.AssetRecord(
                asset_id=asset_id, sha256=processed.sha256,
                mime=processed.mime, width=processed.width,
                height=processed.height,
                provenance=sc.AssetProvenance(
                    provider=chosen.provider,
                    provider_asset_id=chosen.provider_asset_id,
                    source_url=chosen.page_url[:2048],
                    creator=chosen.creator[:200],
                    creator_url=chosen.creator_url[:2048],
                    license_url=chosen.license_url[:2048],
                    fetched_at=store.utcnow()),
                alt=(intent.alt or chosen.alt)[:500],
                caption=intent.purpose[:300],
                role=sc.AssetRole(intent.role),
                bytes=len(processed.data),
                status=sc.AssetStatus.ready)
            records.append(_dump_json(record))
        self._save_stage(sc.JobPhase.visual_assets, {"records": records})

    # ------------------------------------------------------------------ 阶段 5

    async def _stage_author_slides(self, job: sc.GenerationJob,
                                   budgets: _Budgets) -> None:
        plan = sc.OutlinePlan.model_validate(
            self._read_stage(sc.JobPhase.outline)["plan"])
        budgets.llm.call_budget = limits.llm_call_budget(len(plan.pages))
        base = self._read_stage(sc.JobPhase.resolve_sources)
        research = self._read_stage(sc.JobPhase.research)
        assets = self._read_stage(sc.JobPhase.visual_assets)
        records = [sc.SourceRecord.model_validate(r)
                   for r in base.get("records", [])
                   + research.get("records", [])]
        asset_records = [sc.AssetRecord.model_validate(a)
                         for a in assets.get("records", [])]
        llm_gate = asyncio.Semaphore(2)
        pages: list[dict[str, Any]] = []

        async def _author_page(page: sc.OutlinePage,
                               neighbors: str) -> None:
            evidence = _evidence_pack(records, 8000)
            page_assets = [a for a in asset_records
                           if page.visual_intent is not None
                           and a.role.value == page.visual_intent.role][:2]
            user_text = json.dumps({
                "slide_id": store.new_slide_id(),
                "order": page.order,
                "page_plan": _dump_json(page),
                "layout": page.layout.value,
                "neighbors": neighbors,
                "glossary": [_dump_json(g) for g in plan.glossary],
                "assets": [{"asset_id": a.asset_id, "alt": a.alt,
                            "caption": a.caption, "width": a.width,
                            "height": a.height} for a in page_assets],
                "allowed_actions": ["reveal", "highlight", "advance"],
            }, ensure_ascii=False) + "\n\n" + evidence
            async with llm_gate:
                result, _ = await generate_json(
                    self.deps.llm, prompt_id="classroom_slide",
                    user_text=user_text, model_cls=_SlideModel,
                    repair_prompt_id="classroom_repair")
            slide = result.slide
            slide.order = page.order
            # LLM 的 claims 对照 → 服务端 TeachingClaim（claim_id 服务端签发）
            teaching_claims = []
            for claim in result.claims:
                if claim.block_id not in {b.id for b in slide.blocks}:
                    continue
                teaching_claims.append(sc.TeachingClaim(
                    claim_id=store.new_id("claim")[:30],
                    text=claim.text[:600],
                    kind=sc.ClaimKind(claim.claim_kind),
                    block_ids=[claim.block_id],
                    segment_ids=[],
                    source_ids=([claim.source_id]
                                if claim.source_id else [])))
            slide.claims = teaching_claims[:sc.MAX_CLAIMS_PER_SLIDE]
            pages.append({"slide": _dump_json(slide)})

        last_summary = ""
        for page in plan.pages:
            await _author_page(page, last_summary)
            last_summary = page.title
        pages.sort(key=lambda p: p["slide"]["order"])
        self._save_stage(sc.JobPhase.author_slides,
                         {"slides": pages,
                          "objectives": [_dump_json(o)
                                         for o in plan.objectives],
                          "glossary": [_dump_json(g)
                                       for g in plan.glossary]})

    # ------------------------------------------------------------------ 阶段 6

    async def _stage_checkpoints(self, job: sc.GenerationJob,
                                 budgets: _Budgets) -> None:
        """检查点阶段（§13.1/§13.2）：密度映射 none→无 / light→reflect /
        standard→正式题（复用既有出题质量门，失败降级 reflect 讲授模式）。
        模板只进私有材料；CheckpointBlock 仅挂 checkpoint 布局页。"""
        slides = [sc.SlideSpec.model_validate(s["slide"]) for s in
                  self._read_stage(sc.JobPhase.author_slides)["slides"]]
        brief = self._load_brief()
        records = [sc.SourceRecord.model_validate(r) for r in
                   self._read_stage(sc.JobPhase.resolve_sources)
                   .get("records", [])]
        evidence_text = _evidence_pack(records, 6000)
        density = sc.CheckpointDensity(brief.checkpoint_density)
        templates: list[dict[str, Any]] = []
        if density == sc.CheckpointDensity.none:
            # 用户明确不要检查点：移除 checkpoint 占位页（该布局必须有
            # checkpoint 块，无块无法编译），页序保持连续
            slides = [s for s in slides
                      if s.layout != sc.SlideLayout.checkpoint]
            for index, slide in enumerate(slides, start=1):
                slide.order = index
            payload = {"templates": templates,
                       "slides": [s.model_dump(mode="json", by_alias=True)
                                  for s in slides]}
            self._save_stage(sc.JobPhase.checkpoints, payload)
            return
        low, high = limits.CHECKPOINT_DENSITY.get(brief.duration_minutes,
                                                  (1, 2))
        hosts = [s for s in slides if s.layout == sc.SlideLayout.checkpoint]
        wanted = min(max(low, 1), high, len(hosts))
        if wanted <= 0:
            self.warnings.append("大纲未包含检查点页，本课无正式检查点")
        for slide in hosts[:wanted]:
            checkpoint_id = store.new_id("ckp")
            slide.blocks.append(sc.CheckpointBlock(
                id=store.new_id("blk"), checkpoint_id=checkpoint_id))
            kind = sc.CheckpointKind.reflect
            template_obj = sc.CheckpointTemplate(
                checkpoint_id=checkpoint_id, slide_id=slide.slide_id,
                kind=kind,
                prompt="用一分钟回想：这一页的核心结论是什么？"
                       "它依赖哪些前提条件？", reflection_seconds=45)
            if density == sc.CheckpointDensity.standard:
                authored, status = await self._author_checkpoint_question(
                    brief, slide, checkpoint_id, evidence_text)
                if status == "question" and authored:
                    template_obj = authored[0]
            templates.append(_dump_json(template_obj))
        payload = {"templates": templates,
                   "slides": [s.model_dump(mode="json", by_alias=True)
                              for s in slides]}
        self._save_stage(sc.JobPhase.checkpoints, payload,
                         inputs={"checkpoints": store.canonical_hash(payload)})

    async def _author_checkpoint_question(
            self, brief: sc.LessonBrief, slide: sc.SlideSpec,
            checkpoint_id: str, evidence_text: str,
    ) -> tuple[list[sc.CheckpointTemplate], str]:
        """标准密度的正式题；测试可经 deps.checkpoint_author 替换。

        出题子系统崩溃（RuntimeError 等）按 §13.2.2 降级 reflect；
        课堂域硬错误（预算耗尽）必须向上传播。"""
        try:
            author = self.deps.checkpoint_author
            if author is not None:
                return await author(brief=brief, slide=slide,
                                    checkpoint_id=checkpoint_id,
                                    evidence_text=evidence_text)
            from .checkpoints import author_question_checkpoint
            return await author_question_checkpoint(
                llm=self.deps.llm, brief=brief, slide=slide,
                checkpoint_id=checkpoint_id,
                evidence_text=evidence_text)
        except ClassroomError:
            raise
        except Exception:
            self.warnings.append("随堂题准备失败，本课为讲授模式")
            return [], "reflect_fallback"

    # ------------------------------------------------------------------ 阶段 7

    async def _stage_review(self, job: sc.GenerationJob,
                            budgets: _Budgets) -> None:
        payload = self._read_stage(sc.JobPhase.checkpoints)
        slides = [sc.SlideSpec.model_validate(s) for s in payload["slides"]]
        templates = [sc.CheckpointTemplate.model_validate(t)
                     for t in payload["templates"]]
        brief = self._load_brief()
        objectives = [sc.Objective.model_validate(o) for o in
                      self._read_stage(sc.JobPhase.author_slides)
                      .get("objectives", [])]
        records = [sc.SourceRecord.model_validate(r) for r in
                   self._read_stage(sc.JobPhase.resolve_sources)
                   .get("records", [])
                   + self._read_stage(sc.JobPhase.research)
                   .get("records", [])]
        issues = validation.structural_gate(slides, templates)
        issues += validation.evidence_gate(brief, objectives, slides, records)
        duration_issues, estimated = validation.duration_gate(
            brief.duration_minutes, slides, brief.language)
        issues += duration_issues
        # 独立 reviewer（教学门）：不能自报通过
        review_payload = {
            "objectives": [_dump_json(o) for o in objectives],
            "slides": [{"slide_id": s.slide_id, "title": s.title,
                        "layout": s.layout.value,
                        "blocks": [getattr(b, "kind", "") for b in s.blocks],
                        "narration": [seg.spoken_text[:120]
                                      for seg in s.segments],
                        "claims": [_dump_json(c) for c in s.claims]}
                       for s in slides],
            "duration_estimate_minutes": estimated // 60,
        }
        report, _ = await generate_json(
            self.deps.llm, prompt_id="classroom_review",
            user_text=json.dumps(review_payload, ensure_ascii=False),
            model_cls=_ReviewModel)
        llm_issues = [sc.ReviewIssue(code=i.code[:64],
                                     severity=sc.Severity(i.severity),
                                     slide_id=i.slide_id,
                                     field_path=i.field_path,
                                     reason=i.reason[:600])
                      for i in report.issues]
        combined = validation.combine_reports(issues, llm_issues)
        # 修复：只针对 blocker/major 页，一次修复后仍严重则失败（§15.2）
        repair_targets = {i.slide_id for i in combined.issues
                          if i.severity != sc.Severity.minor and i.slide_id}
        if repair_targets:
            slides = await self._repair_slides(slides, combined,
                                               repair_targets, records)
            issues = validation.structural_gate(slides, templates)
            issues += validation.evidence_gate(brief, objectives, slides,
                                               records)
            combined = validation.combine_reports(issues, llm_issues)
        if validation.has_blocker(combined.issues):
            worst = next(i for i in combined.issues
                         if i.severity == sc.Severity.blocker)
            raise ClassroomError(
                "content_invalid",
                f"质量门未通过：{worst.code} {worst.reason}")
        final_payload = {
            "report": _dump_json(combined),
            "slides": [s.model_dump(mode="json", by_alias=True)
                       for s in slides],
            "templates": [t.model_dump(mode="json", by_alias=True)
                          for t in templates],
            "objectives": [_dump_json(o) for o in objectives],
        }
        self._save_stage(sc.JobPhase.review, final_payload,
                         inputs={"review": store.canonical_hash(final_payload)})

    async def _repair_slides(self, slides: list[sc.SlideSpec],
                             report: sc.ReviewReport,
                             targets: set[str],
                             records: list[sc.SourceRecord],
                             ) -> list[sc.SlideSpec]:
        from .render.compiler import validate_layout_slots

        evidence = _evidence_pack(records, 8000)
        out = list(slides)
        for index, slide in enumerate(out):
            if slide.slide_id not in targets:
                continue
            errors = [i for i in report.issues if i.slide_id == slide.slide_id]
            result, _ = await generate_json(
                self.deps.llm, prompt_id="classroom_repair",
                user_text=json.dumps({
                    "original": slide.model_dump(mode="json", by_alias=True),
                    "errors": [_dump_json(i) for i in errors],
                }, ensure_ascii=False) + "\n\n" + evidence,
                model_cls=_SlideModel)
            fixed = result.slide
            fixed.order = slide.order
            # 修复页必须仍满足布局 slot 约束；否则保留原页（修复尽力而为）
            probe = sc.LessonRevision(
                revision=1, brief=sc.LessonBrief(topic="probe"),
                source_snapshot=[], slides=[fixed], objectives=[
                    sc.Objective(objective_id="objective_1", text="probe")],
                content_hash="0" * 64, created_at=store.utcnow())
            try:
                validate_layout_slots(probe)
                out[index] = fixed
            except ValueError:
                continue
        return out

    # ------------------------------------------------------------------ 阶段 8

    async def _stage_render(self, job: sc.GenerationJob,
                            budgets: _Budgets) -> None:
        from .render import assets as render_assets
        from .render.compiler import compile_html

        async def _compile_and_check() -> tuple[Any, str, Any]:
            revision = self._assemble_revision(job)
            html = compile_html(
                revision, mode="presentation",
                asset_bytes=self._load_asset_bytes(revision))
            checker = self.deps.layout_check or _default_layout_check
            report = checker(html)
            if hasattr(report, "__await__"):
                report = await report
            return revision, html, report

        _revision, html, report = await _compile_and_check()
        if report is not None and getattr(report, "ok", True) is False:
            # §15.5 视觉门：失败最多 1 次排版修复（只修溢出页），修完
            # 写回 review 载荷——publish 始终从 review 阶段组装。
            review_payload = self._read_stage(sc.JobPhase.review)
            slides = [sc.SlideSpec.model_validate(s)
                      for s in review_payload["slides"]]
            orders = {int(i.get("slide_order", 0)) for i in report.issues}
            targets = {s.slide_id for s in slides if s.order in orders} \
                or {s.slide_id for s in slides}
            records = [sc.SourceRecord.model_validate(r) for r in
                       self._read_stage(sc.JobPhase.resolve_sources)
                       .get("records", [])]
            synthetic = sc.ReviewReport(issues=[
                sc.ReviewIssue(code="layout_overflow", severity="major",
                               slide_id=sid, field_path="blocks",
                               reason="该页在 1280×720 或窄屏视口溢出，"
                                      "请减少/缩短块与文字")
                for sid in targets])
            slides = await self._repair_slides(slides, synthetic, targets,
                                               records)
            review_payload["slides"] = [
                s.model_dump(mode="json", by_alias=True) for s in slides]
            self._save_stage(sc.JobPhase.review, review_payload)
            _revision, html, report2 = await _compile_and_check()
            if report2 is not None and getattr(report2, "ok", True) is False:
                raise ClassroomError("layout_overflow",
                                     "排版修复后仍溢出，未发布")
        self._save_stage(sc.JobPhase.render, {
            "html_chars": len(html),
            "layout_ok": True,
            "renderer_version": render_assets.RUNTIME_VERSION,
        })

    # ------------------------------------------------------------------ 阶段 9

    async def _stage_publish(self, job: sc.GenerationJob,
                             budgets: _Budgets) -> None:
        revision = self._assemble_revision(job)
        # 发布前重查来源授权与内容 hash（§7.1 步骤 6 / §15.2 publish 行）
        sources.assert_sources_authorized(
            self.owner, self.workspace_id, revision.source_snapshot)
        spec_text = store.canonical_json(_dump_json(revision))
        staging = store.prepare_revision_staging(
            self.owner, self.workspace_id, self.lesson_id,
            job.target_revision)
        files: dict[str, str] = {
            "spec.private.json": store.bytes_hash(spec_text.encode("utf-8"))}
        store.stage_file(staging, "spec.private.json", spec_text)
        asset_entries = []
        for asset in revision.assets:
            ext = "png" if asset.mime == "image/png" else "webp"
            src = store.asset_file_path(self.owner, self.workspace_id,
                                        self.lesson_id, asset.asset_id, ext)
            if not src.exists():
                continue
            data = src.read_bytes()
            (staging / "assets").mkdir(exist_ok=True)
            store.stage_file(staging / "assets", f"{asset.asset_id}.{ext}",
                             data)
            name = f"assets/{asset.asset_id}.{ext}"
            files[name] = store.bytes_hash(data)
            asset_entries.append({"asset_id": asset.asset_id,
                                  "file": name,
                                  "sha256": asset.sha256})
        manifest = {
            "revision": job.target_revision,
            "schema_version": 1,
            "content_hash": revision.content_hash,
            "files": files,
            "assets": asset_entries,
            "renderer_version": revision.renderer_version,
            "created_at": revision.created_at.isoformat(),
        }
        fresh = self._load_job()
        store.save_commit_intent(fresh, job.target_revision, manifest)
        lesson = store.commit_revision(
            self.owner, self.workspace_id, self.lesson_id,
            job.target_revision, manifest,
            expected_epoch=fresh.epoch,
            cancel_requested=fresh.cancel_requested)
        store.index_upsert_lesson(self.owner, self.workspace_id, lesson,
                                  job=fresh)
        self._mutate(lambda j: (
            j.artifacts.__setitem__(sc.JobPhase.publish.value,
                                    revision.content_hash),
            j.artifacts.__setitem__("published", "1")))
        self._update_job(state=sc.JobState.succeeded, phase=None,
                         last_error=None)

    # ------------------------------------------------------------------ 组装

    def _assemble_revision(self, job: sc.GenerationJob) -> sc.LessonRevision:
        payload = self._read_stage(sc.JobPhase.review)
        slides = [sc.SlideSpec.model_validate(s) for s in payload["slides"]]
        templates = [sc.CheckpointTemplate.model_validate(t)
                     for t in payload["templates"]]
        objectives = [sc.Objective.model_validate(o)
                      for o in payload["objectives"]]
        brief = self._load_brief()
        records = [sc.SourceRecord.model_validate(r) for r in
                   self._read_stage(sc.JobPhase.resolve_sources)
                   .get("records", [])
                   + self._read_stage(sc.JobPhase.research)
                   .get("records", [])]
        assets = [sc.AssetRecord.model_validate(a) for a in
                  self._read_stage(sc.JobPhase.visual_assets)
                  .get("records", [])]
        glossary = [sc.GlossaryEntry.model_validate(g) for g in
                    self._read_stage(sc.JobPhase.author_slides)
                    .get("glossary", [])]
        from .render import assets as render_assets
        revision = sc.LessonRevision(
            revision=job.target_revision,
            brief=brief,
            source_snapshot=records,
            slides=slides,
            checkpoint_templates=templates,
            objectives=objectives,
            glossary=glossary,
            assets=assets,
            renderer_version=render_assets.RUNTIME_VERSION,
            prompt_versions={k: v for k, v in active_versions().items()
                             if k.startswith("classroom_")},
            review_report=sc.ReviewReport.model_validate(payload["report"]),
            content_hash="0" * 64,  # 先占位，canonical_hash 计算后回填
            created_at=store.utcnow())
        dump = _dump_json(revision)
        dump.pop("content_hash")  # hash 不自环：对无 hash 规范形计算
        digest = store.canonical_hash(dump)
        dump["content_hash"] = digest
        return sc.LessonRevision.model_validate(dump)

    def _load_asset_bytes(self, revision: sc.LessonRevision) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        for asset in revision.assets:
            ext = "png" if asset.mime == "image/png" else "webp"
            path = store.asset_file_path(self.owner, self.workspace_id,
                                         self.lesson_id, asset.asset_id, ext)
            if path.exists():
                out[asset.asset_id] = path.read_bytes()
        return out


class _AwaitingOutlineStop(Exception):
    """outline_first：大纲已就绪等用户审核；状态已持久化。"""


class _NeedsInputStop(Exception):
    """needs_input 终止当前 run；状态已持久化。"""


def _set_llm_hook(budget: LLMUsageBudget):
    from ..core.llm_async import set_llm_budget_hook
    return set_llm_budget_hook(budget)


def _reset_llm_hook(token) -> None:
    from ..core.llm_async import reset_llm_budget_hook
    reset_llm_budget_hook(token)


def _default_layout_check(html: str):
    from .render.check import run_layout_check
    return run_layout_check(html)


def _pick_candidate(candidates: list, orientation: str | None):
    if not candidates:
        return None
    def score(c):
        aspect_ok = (orientation != "portrait" or c.height >= c.width)
        return (aspect_ok, c.width * c.height)
    return max(candidates, key=score)


# ---------------------------------------------------------------------------
# LLM 输出解析模型：对模型输出宽松（extra=ignore），关键字段强校验；
# 规范化到 schema 模型的工作在阶段函数内完成。
# ---------------------------------------------------------------------------

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402


class _LLMModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _SearchQueryItem(_LLMModel):
    purpose: str = ""
    query: str = Field(..., min_length=1, max_length=200)
    timeliness: str = "basic"
    preferred_source: str = ""


class _SearchPlanModel(_LLMModel):
    queries: list[_SearchQueryItem] = Field(default_factory=list,
                                            max_length=6)
    skipped: list[dict] = Field(default_factory=list)


class _OutlineObjectiveItem(_LLMModel):
    objective_id: str = Field(..., pattern=r"^objective_[0-9a-z_-]{1,32}$")
    text: str = Field(..., min_length=1, max_length=200)
    evidence_status: str = "uncovered"
    bloom: str = ""


class _OutlineGlossaryItem(_LLMModel):
    term: str = Field(..., min_length=1, max_length=60)
    definition: str = Field(..., min_length=1, max_length=300)
    spoken_hint: str = ""


class _OutlineIntentItem(_LLMModel):
    role: str
    purpose: str = ""
    required_objects: list[str] = Field(default_factory=list, max_length=6)
    exclude: list[str] = Field(default_factory=list, max_length=6)
    orientation: str | None = None
    query_terms: list[str] = Field(default_factory=list, max_length=5)
    alt: str = ""


class _OutlinePageItem(_LLMModel):
    order: int = 0
    title: str = Field(..., min_length=1, max_length=120)
    layout: str
    objective_ids: list[str] = Field(default_factory=list, max_length=8)
    budget_seconds: int = 0
    key_points: list[str] = Field(default_factory=list, max_length=6)
    visual_intent: _OutlineIntentItem | None = None


class _OutlineModel(_LLMModel):
    objectives: list[_OutlineObjectiveItem] = Field(..., min_length=1)
    pages: list[_OutlinePageItem] = Field(..., min_length=1)
    glossary: list[_OutlineGlossaryItem] = Field(default_factory=list)
    scope_note: str = ""
    uncovered_note: str = ""
    total_budget_seconds: int = 0


class _ClaimItem(_LLMModel):
    block_id: str
    claim_kind: str
    text: str = Field(..., min_length=1, max_length=600)
    source_id: str | None = None


class _SlideModel(_LLMModel):
    slide: sc.SlideSpec
    claims: list[_ClaimItem] = Field(default_factory=list, max_length=12)


class _ReviewIssueItem(_LLMModel):
    code: str = Field(..., min_length=1)
    severity: str = "minor"
    slide_id: str | None = None
    field_path: str | None = None
    reason: str = Field(..., min_length=1)


class _ReviewModel(_LLMModel):
    issues: list[_ReviewIssueItem] = Field(default_factory=list,
                                           max_length=64)
    summary: str = ""
