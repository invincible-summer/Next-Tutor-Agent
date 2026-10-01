"""本人只读领域适配器（plan.md §10.3，A09）。

每个 read_* 都返回 §10.4 的统一结果形状：
    {tool, status, data, scope, generated_at, data_revision, complete,
     omitted_count, sources, notices}
- empty（成功读取且无记录）与 error（无法读取）绝不互换；
- 功能关闭返回 disabled，不调用不存在的 provider；
- 只读：不物化任务、不触发评价、不写任何业务存储；
- 来源（AssistantSource）只能在这里铸造并登记进 SourceRegistry，
  模型与展示层只允许引用已返回的 source_id（§7.7）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core import assistant_store as store
from app.schemas.assistant import AssistantRouteId

from . import catalog


def _now_iso() -> str:
    return store.utc_now_iso()


class SourceRegistry:
    """轮内来源登记：source_id → AssistantSource dict（可重建反查投影）。"""

    def __init__(self, student_id: str) -> None:
        self.student_id = student_id
        self._sources: dict[str, dict[str, Any]] = {}

    def mint(self, *, kind: str, title: str, locator: dict[str, Any],
             workspace_id: str | None = None,
             observed_at: str | None = None,
             revision: str | None = None,
             origin: tuple[str, str] | None = None) -> str:
        source_id = store.mint_source_ref_id()
        entry: dict[str, Any] = {
            "source_id": source_id, "kind": kind, "title": title[:200],
            "workspace_id": workspace_id,
            "observed_at": observed_at, "retrieved_at": _now_iso(),
            "revision": revision, "locator": locator,
            "availability": "available",
        }
        self._sources[source_id] = entry
        if origin is not None:
            entry["_origin"] = {"kind": origin[0], "id": origin[1]}
        return source_id

    def mint_entry(self, **kwargs: Any) -> dict[str, Any] | None:
        """铸造并返回展示用来源 dict（无 registry 时返回 None）。"""
        source_id = self.mint(**kwargs)
        return dict(self._sources[source_id])

    def get(self, source_id: str) -> dict[str, Any] | None:
        return self._sources.get(source_id)

    def all_sources(self) -> list[dict[str, Any]]:
        """展示用来源列表：剥离内部 _origin（§7.7 展示契约）。"""
        return [{k: v for k, v in s.items() if k != "_origin"}
                for s in self._sources.values()]

    def ref_entries(self, conversation_id: str,
                    message_id: str) -> list[dict[str, Any]]:
        """§12.3-7：写入 references.json 的登记行（不含展示字段）。"""
        entries = []
        for source in self._sources.values():
            origin = source.get("_origin")
            if origin is None:
                continue
            entries.append({
                "origin_kind": origin["kind"], "origin_id": origin["id"],
                "source_id": source["source_id"],
                "conversation_id": conversation_id,
                "message_id": message_id,
                "revision": source.get("revision"),
            })
        return entries


def tool_result(tool: str, *, status: str, data: Any = None,
            scope: dict[str, Any] | None = None,
            data_revision: str = "", complete: bool = True,
            omitted_count: int | None = None,
            sources: list[dict[str, Any]] | None = None,
            notices: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "tool": tool, "status": status, "data": data,
        "scope": scope or {"mode": "account", "workspace_ids": [],
                           "scope_revisions": {}},
        "generated_at": _now_iso(), "data_revision": data_revision,
        "complete": complete, "omitted_count": omitted_count,
        "sources": sources or [], "notices": notices or [],
    }


def _public_scope(mode: str, workspace_ids: list[str]) -> dict[str, Any]:
    return {"mode": mode, "workspace_ids": workspace_ids,
            "scope_revisions": {}}


# ---------------------------------------------------------------------------
# 范围解析（§6.2）
# ---------------------------------------------------------------------------

def owned_workspaces(student_id: str) -> list[dict[str, Any]]:
    from app.core import workspace as workspace_core
    return [w for w in workspace_core.list_workspaces()
            if w.get("student_id") == student_id]


def resolve_scope(
    student_id: str,
    scope_selection: dict[str, Any],
    page_context: dict[str, Any] | None,
    *,
    workspace_name: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """按 §6.2 顺序解析范围；无法唯一确定时返回候选（不猜测）。

    返回 (ResolvedScope dict, workspace 候选列表)。候选非空时调用方应
    走 clarify 分支给出选择卡。
    """
    workspaces = owned_workspaces(student_id)
    by_id = {w["workspace_id"]: w for w in workspaces}

    # 0) 本轮文字明确指定的工作区（最优先）
    if workspace_name:
        needle = "".join(str(workspace_name).lower().split())
        hits = [w for w in workspaces
                if needle in "".join(str(w.get("name", "")).lower().split())
                or needle in str(w.get("workspace_id", "")).lower()]
        if len(hits) == 1:
            wid = hits[0]["workspace_id"]
            return _public_scope("workspace", [wid]), []
        if len(hits) > 1:
            return _public_scope("all_workspaces",
                                 [w["workspace_id"] for w in workspaces]), hits

    mode = str((scope_selection or {}).get("mode") or "follow_page")
    if mode == "workspace":
        wid = str((scope_selection or {}).get("workspace_id") or "")
        if wid in by_id:
            return _public_scope("workspace", [wid]), []
        # 固定的工作区已不可访问：诚实降级为全部工作区，不冒充单区。
        return (_public_scope("all_workspaces",
                              [w["workspace_id"] for w in workspaces]), [])
    if mode == "all_workspaces":
        return (_public_scope("all_workspaces",
                              [w["workspace_id"] for w in workspaces]), [])

    # follow_page：当前页面已验证的工作区
    page_ws = str((page_context or {}).get("workspace_id") or "")
    if page_ws and page_ws in by_id:
        return _public_scope("workspace", [page_ws]), []
    # 无工作区页面问个人总体近况：全部本人工作区分组（§6.2-4）
    return (_public_scope("all_workspaces",
                          [w["workspace_id"] for w in workspaces]), [])


# ---------------------------------------------------------------------------
# §10.3 工具对应的读取器
# ---------------------------------------------------------------------------

def read_product_help(*, module_id: str = "", query: str = "",
                      lang: str = "zh",
                      registry: SourceRegistry | None = None) -> dict[str, Any]:
    """get_product_help：版本化帮助段落；公开、无个人数据。"""
    try:
        cat = catalog.load_product_catalog()
        if module_id:
            try:
                route = AssistantRouteId(module_id)
            except ValueError:
                route = None
            modules = [catalog.get_module(route)] if route else []
            modules = [m for m in modules if m is not None]
        else:
            matches = catalog.find_module_matches(query, lang)
            modules = [m for m in matches
                       if catalog.match_score(m, query, None)[0] >= 20]
        if not modules:
            return tool_result("get_product_help", status="empty",
                           data={"catalog_version": cat.catalog_version,
                                 "modules": []})
        sources = []
        for module in modules[:3]:
            if registry is not None:
                entry = registry.mint_entry(
                    kind="product_help",
                    title=(module.name.zh if lang == "zh" else module.name.en),
                    locator={"kind": "module",
                             "route_id": module.route_id.value},
                    origin=("product_catalog",
                            f"{module.route_id.value}@{cat.catalog_version}"))
                if entry is not None:
                    sources.append(entry)
        data_modules = []
        for module in modules[:3]:
            data_modules.append({
                "route_id": module.route_id.value,
                "name": module.name.zh if lang == "zh" else module.name.en,
                "summary": (module.summary.zh if lang == "zh"
                            else module.summary.en),
                "steps": module.steps.zh if lang == "zh" else module.steps.en,
                "prerequisites": (module.prerequisites.zh if lang == "zh"
                                  else module.prerequisites.en),
            })
        return tool_result(
            "get_product_help", status="ready",
            data={"catalog_version": cat.catalog_version,
                  "modules": data_modules},
            data_revision=f"catalog:{cat.catalog_version}",
            sources=sources)
    except Exception:
        return tool_result("get_product_help", status="error",
                       notices=[{"code": "catalog_unavailable",
                                 "message": "功能目录读取失败。"}])


def _lesson_edit_candidates(
    student_id: str, query: str,
    page_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """「编辑课件」候选（§2.1/§8.3）：页面实体课程优先，其次按名称匹配。

    候选 target 带 view=edit；option_id 用「编辑+课程名」让 choices 点击后
    重入同一意图形成闭环。同名课程通过工作区后缀区分（§8.4）。
    """
    import re as _re
    out: list[dict[str, Any]] = []
    owned = {w["workspace_id"]: str(w.get("name", ""))
             for w in owned_workspaces(student_id)}
    ctx = page_context or {}
    entity = ctx.get("entity") or None
    ws_id = str(ctx.get("workspace_id") or "")

    def _ws_name(ws: str) -> str:
        return owned.get(ws, "")

    if entity and str(entity.get("kind") or "") == "lesson" \
            and ws_id in owned:
        eid = str(entity.get("id") or "")
        if eid:
            title = eid
            try:
                from app.core import classroom_store as cstore
                lessons = cstore.read_index(
                    student_id, ws_id).get("lessons") or {}
                entries = (lessons.values() if isinstance(lessons, dict)
                           else lessons)
                for les in entries:
                    if isinstance(les, dict) \
                            and str(les.get("lesson_id")) == eid:
                        title = str(les.get("title") or eid)
                        break
            except Exception:
                pass
            out.append({
                "kind": "lesson", "workspace_id": ws_id,
                "title": title,
                "workspace_name": _ws_name(ws_id),
                "target": {"kind": "lesson", "workspace_id": ws_id,
                           "lesson_id": eid, "view": "edit"},
            })
    if not out:
        needle = _re.sub(
            r"(帮我|请|我想|我要|麻烦|编辑|修改|改一下|调整|润色|优化|一下|"
            r"这个|这节|那节|这门|的|课件|课程|讲稿|PPT|ppt|幻灯片|打开|进入|"
            r"课|节|那|这|the|lesson|course|slides|deck)",
            "", str(query or ""), flags=_re.IGNORECASE)
        needle = "".join(needle.lower().split())
        if needle:
            from . import search as search_mod
            data = search_mod.search_site_entities(
                student_id, q=needle, kinds="lesson", limit=5)
            for item in data.get("items") or []:
                target = dict(item.get("target") or {})
                if target.get("kind") != "lesson":
                    continue
                target["view"] = "edit"
                ws = str(item.get("workspace_id") or "")
                out.append({
                    "kind": "lesson", "workspace_id": ws or None,
                    "title": str(item.get("title") or item.get("entity_id")),
                    "workspace_name": _ws_name(ws),
                    "target": target,
                })
    return out[:5]


def read_destination(
    student_id: str, *, query: str, lang: str = "zh",
    entity_kind: str | None = None,
    workspace_name: str | None = None,
    page_context: dict[str, Any] | None = None,
    registry: SourceRegistry | None = None,
    lesson_edit: bool = False,
    page: int | None = None,
) -> dict[str, Any]:
    """resolve_destination：唯一目标或最多 5 个候选；本人范围只读。"""
    candidates: list[dict[str, Any]] = []
    if lesson_edit:
        # 编辑课件：课程候选优先（页面实体 → 名称匹配）；无候选回落
        # 课程模块入口，由用户在课程列表选择。
        lesson_cands = _lesson_edit_candidates(student_id, query, page_context)
        if lesson_cands:
            return tool_result(
                "resolve_destination", status="ready",
                data={"candidates": lesson_cands,
                      "unique": len(lesson_cands) == 1},
                scope=_public_scope("all_workspaces",
                                    [w["workspace_id"] for w in
                                     owned_workspaces(student_id)]))
    # 1) 目录模块（score>=50 入选）。强命中 = 原始得分 >=80（整句即模块
    #    名/别名）或净化后的检索词与模块名/别名精确相等：「带我去记忆
    #    中心」净化后就是「记忆中心」，模块是用户所指；「找到我的笔记
    #    《X》」里「笔记」只是句子包含别名（60 分），让位给具名实体。
    from . import search as search_mod
    entity_needle = search_mod.clean_query(query)
    needle_norm = "".join(str(entity_needle or "").lower().split())
    strong_module = False
    for module in catalog.find_module_matches(query, lang, limit=3):
        score = catalog.match_score(module, query, None)[0]
        if score < 50:
            continue
        module_tokens = {catalog._normalize(module.name.zh),
                         catalog._normalize(module.name.en)}
        module_tokens.update(catalog._normalize(a) for a in module.aliases)
        if score >= 80 or (needle_norm
                           and needle_norm in module_tokens):
            strong_module = True
        candidates.append({
            "kind": "module", "route_id": module.route_id.value,
            "title": module.name.zh if lang == "zh" else module.name.en,
            "target": {"kind": "module",
                       "route_id": module.route_id.value},
        })
    # 2) 本人工作区（按名称/文字命中）
    needle = "".join(str(workspace_name or query).lower().split())
    if needle:
        for ws in owned_workspaces(student_id):
            name = "".join(str(ws.get("name", "")).lower().split())
            if needle in name or needle == str(ws.get("workspace_id")):
                candidates.append({
                    "kind": "workspace", "workspace_id": ws["workspace_id"],
                    "title": str(ws.get("name", "")),
                    "target": {"kind": "workspace_chat",
                               "workspace_id": ws["workspace_id"]},
                })
    # 3) 页面实体（发送时冻结的上下文；归属已由后端复核）
    ctx = page_context or {}
    entity = ctx.get("entity") or None
    ws_id = str(ctx.get("workspace_id") or "")
    owned = {w["workspace_id"] for w in owned_workspaces(student_id)}
    if entity and ws_id in owned:
        kind = str(entity.get("kind") or "")
        eid = str(entity.get("id") or "")
        if kind == "chat":
            candidates.append({
                "kind": "chat_session", "title": f"会话 {eid}",
                "target": {"kind": "chat_session", "session_id": eid}})
        elif kind == "lesson":
            candidates.append({
                "kind": "lesson", "title": f"课程 {eid}",
                "target": {"kind": "lesson", "workspace_id": ws_id,
                           "lesson_id": eid}})
    # 4) 具名实体（§20.4：笔记/文件/对话/课程等）：目录未强命中时参与
    #    解析，让「找到/打开我的笔记《X》」落到实体深链。唯一 exact/title
    #    命中的具名实体优先于目录弱命中（如「笔记」二字对笔记模块的包含
    #    匹配）；工作区/页面实体候选在场时不顶替（如实给出选择）。
    #    模块强命中（如「记忆中心」别名）时跳过实体候选，防模糊标题
    #    劫持模块导航。file 类目标消费用户原文里的页码（§20.3）。
    if not strong_module:
        entity_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        if entity_needle:
            data = search_mod.search_with_expanded_query(
                student_id, text=query, limit=5)
            for item in data.get("items") or []:
                if item.get("match_kind") not in ("exact", "title"):
                    continue
                target = dict(item.get("target") or {})
                if not target.get("kind"):
                    continue
                if target.get("kind") == "file" and page is not None:
                    target["page"] = page
                entity_pairs.append((item, {
                    "kind": str(item.get("entity_kind") or ""),
                    "entity_id": str(item.get("entity_id") or ""),
                    "title": str(item.get("title") or ""),
                    "target": target,
                }))
        if entity_pairs:
            # 最佳档位唯一者获得顶替权：exact 命中的具名实体优先于
            # 反向包含的弱 title 命中（课程《定积分》不稀释笔记《定积分
            # 与可积性》的唯一性）。
            rank = {"exact": 0, "title": 1}
            best = min(rank.get(item.get("match_kind"), 9)
                       for item, _cand in entity_pairs)
            best_pairs = [p for p in entity_pairs
                          if rank.get(p[0].get("match_kind"), 9) == best]
            has_non_module = any(c.get("kind") != "module"
                                 for c in candidates)
            if len(best_pairs) == 1 and not has_non_module:
                candidates = [best_pairs[0][1]]
            else:
                candidates.extend(cand for _item, cand in entity_pairs)
    candidates = candidates[:5]
    if not candidates:
        return tool_result("resolve_destination", status="empty",
                       data={"candidates": []})
    return tool_result(
        "resolve_destination", status="ready",
        data={"candidates": candidates, "unique": len(candidates) == 1},
        scope=_public_scope("all_workspaces",
                            [w["workspace_id"] for w in owned_workspaces(
                                student_id)]))


def read_learning_summary(
    student_id: str, *, start_at: datetime, end_at: datetime,
    timezone_name: str, workspace_ids: list[str] | None = None,
    registry: SourceRegistry | None = None,
) -> dict[str, Any]:
    """get_learning_summary：规范化活动快照（A03）的只读包装。"""
    from app.agents.activity_aggregator import learning_activity_snapshot
    scope = _public_scope(
        "workspace" if workspace_ids else "all_workspaces",
        workspace_ids or [])
    try:
        snap = learning_activity_snapshot(
            student_id, start_at=start_at, end_at=end_at,
            timezone=timezone_name, workspace_ids=workspace_ids)
    except Exception:
        return tool_result("get_learning_summary", status="error", scope=scope,
                       notices=[{"code": "learning_summary_unavailable",
                                 "message": "学习活动读取失败。"}])
    sources_status = [v.get("status") for v in snap.get("sources", {}).values()]
    has_error = any(s == "error" for s in sources_status)
    has_ready = any(s in ("ready", "partial", "empty") for s in sources_status)
    answers = snap.get("answers") or {}
    has_data = bool(snap.get("recorded_learning_days")
                    or snap.get("teaching_turns")
                    or int(answers.get("answer_attempt_count") or 0))
    if has_error and not has_ready:
        status = "error"
    elif has_error:
        status = "partial"
    elif has_data:
        status = "ready"
    else:
        status = "empty"
    sources = []
    ws_names = {w["workspace_id"]: str(w.get("name", ""))
                for w in owned_workspaces(student_id)}
    if registry is not None and (workspace_ids or []):
        for wid in workspace_ids[:3]:
            entry = registry.mint_entry(
                kind="learning_evidence", title="学习证据记录",
                locator={"kind": "learning_archive", "workspace_id": wid,
                         "tab": "changes"},
                workspace_id=wid,
                origin=("learning_evidence_journal", wid))
            if entry is not None:
                sources.append(entry)
    # §7.2/§11.4 workspace_summaries：每区评价状态/覆盖/已有主张（只读，
    # 不触发 synthesis/backfill/review；失败单区跳过并计入 notices）。
    eval_notices: list[dict[str, str]] = []
    summaries = _workspace_evaluation_summaries(
        student_id, workspace_ids, ws_names, eval_notices)
    data_revision = f"activity:v{snap.get('metric_version', 2)}:{int(end_at.timestamp())}"
    return tool_result(
        "get_learning_summary", status=status,
        data={"window": snap.get("window"),
              "recorded_learning_days": snap.get("recorded_learning_days"),
              "answers": answers,
              "teaching_turns": snap.get("teaching_turns"),
              "completed_tasks": snap.get("completed_tasks"),
              "unscoped": snap.get("unscoped"),
              "days_by_workspace": snap.get("days_by_workspace"),
              "workspace_summaries": summaries,
              "workspace_names": ws_names},
        scope=scope, data_revision=data_revision,
        complete=bool(snap.get("complete")),
        notices=[dict(n) for n in snap.get("notices", [])] + eval_notices,
        sources=sources)


def _workspace_evaluation_summaries(
    student_id: str, workspace_ids: list[str] | None,
    ws_names: dict[str, str],
    notices: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """每工作区：评价状态、覆盖、1–3 条可追溯主张、待处理桶（只读）。

    单区解析失败不拖垮整份报告（跳过 + notice）；全部失败时返回空列表，
    报告 complete 不因此为 False（覆盖说明单列）。
    """
    wids = list(dict.fromkeys(workspace_ids or list(ws_names.keys())))[:3]
    out: list[dict[str, Any]] = []
    for wid in wids:
        try:
            from app.agents.student_model.evaluation import projections
            from app.agents.student_model.evaluation import schema as S
            from app.agents.student_model.evaluation.scope import (
                get_scope_resolver)
            from app.agents.student_model.evaluation.store import get_journal
            eval_scope = get_scope_resolver().resolve(student_id, wid)
            views = projections.concept_views(student_id, eval_scope)
            observed = sum(
                1 for v in views
                if v.evidence_count > 0
                or (v.state is not None
                    and getattr(v.state, "value", "") != "not_observed"))
            stated = sorted(
                (v for v in views
                 if (v.statement or "").strip()
                 and v.evaluation_status == S.EvaluationStatus.READY),
                key=lambda v: v.evidence_count, reverse=True)[:3]
            state_obj = get_journal(student_id).state()
            job_by_source: dict[str, Any] = {
                rt.job.source_id: rt.job for rt in state_obj.jobs.values()
                if rt.job.source_id}
            pending = failed = disabled = 0
            active_reviews = 0
            for src in state_obj.sources.values():
                if src.receipt.workspace_id_at_observation != wid:
                    continue
                if src.availability != "available":
                    continue
                if state_obj.review_active_by_source.get(
                        src.receipt.source_id):
                    active_reviews += 1
                if src.current_interpretation_id:
                    continue
                job = job_by_source.get(src.receipt.source_id)
                if job is None:
                    disabled += 1
                elif job.state == S.JobState.FAILED:
                    failed += 1
                elif job.state == S.JobState.CANCELLED:
                    continue
                else:
                    pending += 1
            out.append({
                "workspace_id": wid,
                "workspace_name": ws_names.get(wid, wid),
                "scope_revision": eval_scope.scope_revision[:64],
                "evaluation_status": "pending",  # 占位；下方按状态覆写
                "coverage": {"observed_concepts": observed,
                             "coverage_note": None},
                "statements": [{
                    "text": (v.statement or "")[:1000],
                    "status": getattr(v.state, "value", None),
                    "scope_note": None,
                } for v in stated],
                "pending_counts": {
                    "evaluation_pending": pending,
                    "active_reviews": active_reviews,
                    "failed_jobs": failed,
                },
                "_disabled_source_count": disabled,
            })
            if stated or observed:
                out[-1]["evaluation_status"] = "ready"
            elif pending or failed or active_reviews:
                out[-1]["evaluation_status"] = "pending"
        except Exception:
            notices.append({
                "code": "workspace_evaluation_unavailable",
                "message": f"工作区 {ws_names.get(wid, wid)} 的学习评价暂不可用。",
            })
    return out


def read_teaching_summary(
    student_id: str, *, start_at: datetime, end_at: datetime,
    registry: SourceRegistry | None = None,
) -> dict[str, Any]:
    """get_teaching_summary：窗口教学投影 + 提案实际状态（账号级）。"""
    from app.agents.evaluation.window import teaching_window_report
    scope = _public_scope("account", [])
    try:
        report = teaching_window_report(
            student_id, start_at=start_at, end_at=end_at)
    except Exception:
        return tool_result("get_teaching_summary", status="error", scope=scope,
                       notices=[{"code": "teaching_summary_unavailable",
                                 "message": "教学记录读取失败。"}])
    proposals: list[dict[str, Any]] = []
    try:
        from app.agents.evaluation import get_evaluation_service
        for p in get_evaluation_service().proposals(student_id)[:10]:
            proposals.append({
                "proposal_id": str(p.get("proposal_id") or ""),
                "title": str(p.get("title") or "")[:200] or None,
                "status": str(p.get("status") or "proposed"),
                "impact_turns": p.get("impact_turns"),
            })
    except Exception:
        report.setdefault("notices", []).append(
            {"code": "teaching_proposals_unavailable",
             "message": "教学提案读取失败。"})
    sources = []
    if registry is not None and report.get("total_turns"):
        entry = registry.mint_entry(
            kind="teaching_report", title="AI 教学效果记录",
            locator={"kind": "module", "route_id": "insights"},
            origin=("teaching_trace_window",
                    f"{int(start_at.timestamp())}:{int(end_at.timestamp())}"))
        if entry is not None:
            sources.append(entry)
    for p in proposals[:5]:
        if registry is None or not p.get("proposal_id"):
            continue
        entry = registry.mint_entry(
            kind="teaching_proposal",
            title=p.get("title") or "教学改进提案",
            locator={"kind": "teaching_proposal",
                     "proposal_id": p["proposal_id"]},
            origin=("teaching_proposal", p["proposal_id"]))
        if entry is not None:
            sources.append(entry)
    status = report.get("status", "empty")
    data_revision = (f"teaching:{int(end_at.timestamp())}:"
                     f"{report.get('coverage', {}).get('inspected_count', 0)}")
    return tool_result(
        "get_teaching_summary", status=status,
        data={"window": report.get("window"),
              "total_turns": report.get("total_turns"),
              "failure_distribution": report.get("failure_distribution"),
              "top_strategies": report.get("top_strategies"),
              "coverage": report.get("coverage"),
              "proposals": proposals,
              "pending_proposals": sum(
                  1 for p in proposals if p.get("status") == "proposed")},
        scope=scope, data_revision=data_revision,
        complete=bool(report.get("coverage", {}).get("complete")),
        notices=[dict(n) for n in report.get("notices", [])],
        sources=sources)


async def read_saved_tasks(student_id: str, *,
                           now: float | None = None) -> dict[str, Any]:
    """get_saved_tasks：不物化、不重规划的只读任务快照（GAP-04）。

    now（epoch 秒）透传给快照的"今天"计算：调度器合成时钟（测试/停机
    补发）必须与任务查询的日期口径一致，否则按真实日期查询会永远查空。
    """
    try:
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        snap = await get_orchestration_service().saved_tasks_snapshot(
            student_id, now=now)
    except Exception:
        return tool_result("get_saved_tasks", status="error",
                       notices=[{"code": "saved_tasks_unavailable",
                                 "message": "任务快照读取失败。"}])
    today = snap.get("today") or []
    open_tasks = snap.get("open") or []
    has_data = bool(today or open_tasks or snap.get("recently_completed"))
    return tool_result(
        "get_saved_tasks", status="ready" if has_data else "empty",
        data={"today": today[:10], "open": open_tasks[:10],
              "recently_completed": (snap.get("recently_completed") or [])[:5],
              "coverage": snap.get("coverage")},
        data_revision=f"orchestration:{snap.get('state_updated_at', 0)}",
        complete=False,  # GAP-10：当前快照不能证明完整历史
        notices=[{"code": "task_history_incomplete",
                  "message": "任务为当前快照，不含更早完整历史。"}])


def read_courses(student_id: str, *, workspace_id: str | None = None,
                 resume_only: bool = False,
                 lang: str = "zh") -> dict[str, Any]:
    """find_course：课程/课堂候选与状态；只读、无音频合成。"""
    scope = _public_scope("workspace" if workspace_id else "all_workspaces",
                          [workspace_id] if workspace_id else [])
    try:
        from app.classroom import capabilities as classroom_caps
        allowed, _reason = classroom_caps.user_allowed(student_id)
        if not allowed:
            return tool_result("find_course", status="disabled", scope=scope,
                           notices=[{"code": "classroom_disabled",
                                     "message": "课堂功能未开放。"}])
        from app.classroom import service as classroom_service
        ws_ids = ([workspace_id] if workspace_id
                  else [w["workspace_id"] for w in owned_workspaces(student_id)])
        lessons: list[dict[str, Any]] = []
        resume: dict[str, Any] | None = None
        for wid in ws_ids[:5]:
            listing = classroom_service.list_lessons(
                student_id, wid, page=1, page_size=20)
            lessons.extend({"workspace_id": wid, **item}
                           for item in listing.get("items", []))
            if listing.get("resume") and resume is None:
                resume = {"workspace_id": wid, **listing["resume"]}
        if resume_only:
            data = {"lessons": [], "resume": resume,
                    "resume_only": True}
            status = "ready" if resume else "empty"
        else:
            data = {"lessons": lessons[:5], "resume": resume,
                    "resume_only": False}
            status = "ready" if (lessons or resume) else "empty"
        return tool_result(
            "find_course", status=status, data=data, scope=scope,
            data_revision=f"lessons:{len(lessons)}:{resume is not None}")
    except Exception:
        return tool_result("find_course", status="error", scope=scope,
                       notices=[{"code": "lessons_unavailable",
                                 "message": "课程列表读取失败。"}])


def read_concept_explanation(
    student_id: str, *, workspace_id: str, concept_key: str,
    registry: SourceRegistry | None = None,
) -> dict[str, Any]:
    """get_concept_explanation：现有主张/条件/变化/next_probe；只读。"""
    scope = _public_scope("workspace", [workspace_id])
    try:
        from app.agents.student_model.evaluation import projections
        from app.agents.student_model.evaluation.scope import (
            get_scope_resolver)
        eval_scope = get_scope_resolver().resolve(student_id, workspace_id)
        views = projections.concept_views(student_id, eval_scope)
    except Exception:
        return tool_result("get_concept_explanation", status="error", scope=scope,
                       notices=[{"code": "concept_evaluation_unavailable",
                                 "message": "概念评价读取失败。"}])
    needle = "".join(concept_key.lower().split())
    hits = [v for v in views
            if needle in "".join(str(v.concept_ref.display_name or "")
                                 .lower().split())
            or needle == str(v.concept_ref.concept_id)]
    if not hits:
        return tool_result("get_concept_explanation", status="empty", scope=scope,
                       data={"concepts": []})
    sources = []
    out: list[dict[str, Any]] = []
    for view in hits[:3]:
        source_id = None
        if registry is not None:
            entry = registry.mint_entry(
                kind="concept_evaluation",
                title=str(view.concept_ref.display_name or "概念"),
                locator={"kind": "concept",
                         "concept_id": str(view.concept_ref.concept_id),
                         "workspace_id": workspace_id},
                workspace_id=workspace_id,
                origin=("concept_judgment",
                        f"{workspace_id}:{view.concept_ref.concept_id}"))
            if entry is not None:
                sources.append(entry)
                source_id = entry["source_id"]
        out.append({
            "concept_id": str(view.concept_ref.concept_id),
            "display_name": str(view.concept_ref.display_name or ""),
            "state": (getattr(view.state, "value", None)
                      if view.state is not None else None),
            "evaluation_status": getattr(view.evaluation_status, "value",
                                         str(view.evaluation_status)),
            "statement": view.statement or "",
            "change": (getattr(view.change, "comparison", None)
                       if view.change is not None else None),
            "next_probe": view.next_probe or "",
            "evidence_count": view.evidence_count,
            "source_id": source_id,
        })
    return tool_result(
        "get_concept_explanation", status="ready",
        data={"concepts": out}, scope=scope,
        data_revision=f"concepts:{eval_scope.scope_revision[:32]}",
        sources=[])


def read_reference_detail(source_id: str,
                          registry: SourceRegistry) -> dict[str, Any]:
    """get_reference_detail：source_id 必须来自本轮已授权结果。"""
    source = registry.get(source_id)
    if source is None:
        return tool_result("get_reference_detail", status="error",
                       notices=[{"code": "source_not_found",
                                 "message": "来源不在本轮结果内。"}])
    origin = source.get("_origin") or {}
    related = store.lookup_origin_refs(
        registry.student_id, str(origin.get("kind") or ""),
        str(origin.get("id") or ""))
    return tool_result(
        "get_reference_detail",
        status="ready" if source.get("availability") == "available"
        else "error",
        data={"source_id": source_id, "kind": source.get("kind"),
              "title": source.get("title"),
              "workspace_id": source.get("workspace_id"),
              "observed_at": source.get("observed_at"),
              "revision": source.get("revision"),
              "locator": source.get("locator"),
              "referenced_in_turns": len(related),
              "available": source.get("availability") == "available"},
        notices=[] if source.get("availability") == "available"
        else [{"code": "source_unavailable",
               "message": "来源已不可用。"}],
        sources=[{k: v for k, v in source.items() if k != "_origin"}])
