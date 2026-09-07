"""Knowledge Intelligence projection API (M5 observability).

Read-only views over the knowledge ontology (curated seed + reasoner-learned
edges) with the student's M2 mastery overlaid, powering the frontend
knowledge-graph panel and the concept detail page. Mirrors the /assessment
endpoints' graceful-degradation contract.

All endpoints are READ-ONLY: the graph is only traversed, mastery is only
projected via StudentModel.mastery_view(); nothing persists. Every handler
degrades to a clear status (ok | disabled | not_found | error) and never
raises into a request.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agents import knowledge as _kn
from app.identity.deps import resolve_student_id

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _mastery_overlay(student_id: str) -> dict[str, dict[str, Any]] | None:
    """{skill_id: {p_known, state, attempts, correct}} from M2, or None when
    the student model is disabled (mastery then renders as null)."""
    try:
        from app.agents import student_model as _sm
        if not _sm.is_enabled():
            return None
        sm = _sm.get_student_model(student_id)
        states: dict[str, str] = {}
        for rec in sm.memory.values():
            if getattr(rec, "skill_id", ""):
                states[rec.skill_id] = rec.state.value
        # union of BKT-tracked skills and memory-state skills: mastery_view()
        # only covers skills with BKT update events, so memory-only skills
        # (e.g. seeded by teaching turns) would never get a mastery colour.
        out: dict[str, dict[str, Any]] = {}
        for sid, m in sm.mastery_view().items():
            out[sid] = {
                "p_known": (m or {}).get("p_known", 0.0),
                "state": states.get(sid, "unknown"),
                "attempts": (m or {}).get("attempts", 0),
                "correct": (m or {}).get("correct", 0),
            }
        for sid, state in states.items():
            if sid not in out:
                out[sid] = {"p_known": 0.0, "state": state,
                            "attempts": 0, "correct": 0}
        return out
    except Exception:
        return None


def _chapter_closure(edges: list[dict[str, Any]], chapter_ids: set[str],
                     section_ids: set[str]) -> dict[str, set[str]]:
    """章 → 成员闭包（PART_OF 传递：概念→节→章 与 概念→章 两种形状）。

    返回 {chapter_id: {member_id, ...}}，member 含节与概念；旧图谱（无节）
    退化为直接子成员，行为不变。
    """
    part_children: dict[str, set[str]] = {}
    for edge in edges or []:
        if str(edge.get("type") or "").upper() != "PART_OF":
            continue
        source_id = str(edge.get("source") or edge.get("from") or "")
        target_id = str(edge.get("target") or edge.get("to") or "")
        if source_id and target_id:
            part_children.setdefault(target_id, set()).add(source_id)
    section_chapter: dict[str, str] = {}
    for cid in chapter_ids:
        for member in part_children.get(cid, set()):
            if member in section_ids:
                section_chapter[member] = cid
    out: dict[str, set[str]] = {cid: set() for cid in chapter_ids}
    for cid in chapter_ids:
        for member in part_children.get(cid, set()):
            out[cid].add(member)
            if member in section_ids:
                out[cid] |= part_children.get(member, set())
    return out


@router.get("/graph")
def knowledge_graph(
    textbook_id: str = Query(default="", description="按教材/教材组隔离图谱"),
    file_id: str = Query(default="", description="教材组内单卷；必须同时提供 textbook_id"),
    level: str = Query(default=""),
    subject: str = Query(default=""),
    view: str = Query(default="full", pattern="^(full|overview|chapter|search)$"),
    chapter_id: str = Query(default=""),
    q: str = Query(default="", max_length=120),
    student_id: str = Depends(resolve_student_id),
) -> dict:
    """The full knowledge graph (seed + learned edges) with per-node mastery
    overlaid for this student. `learned_edges` counts reasoner/material
    provenance edges (vs curated seed), so the UI can show what M5.5 added."""
    if not _kn.is_enabled():
        return {"status": "disabled"}
    try:
        overlay = _mastery_overlay(student_id) or {}
        from app.agents.knowledge.custom_graph import CUSTOM_LEVEL, OTHER_LEVEL
        if file_id and not textbook_id:
            raise HTTPException(400, "file_id 必须与 textbook_id 同时提供")
        raw_nodes: list[dict[str, Any]] = []
        raw_edges: list[dict[str, Any]] = []
        coverage: list[dict[str, Any]] = []
        if textbook_id:
            from app.core.textbook import find_textbook_scoped
            from app.agents.knowledge import store as kg_store
            found = find_textbook_scoped(student_id, textbook_id)
            if found is None:
                raise HTTPException(404, "教材不存在")
            record, owner = found
            allowed_files = set(record.get("file_ids") or [])
            if file_id and file_id not in allowed_files:
                raise HTTPException(404, "教材文件不存在")
            payload = kg_store.load_custom_graph(owner, record["topic_key"])
            if payload is None:
                return {"status": "not_found", "nodes": [], "edges": [],
                        "coverage": list(record.get("volumes") or [])}
            raw_nodes = [dict(n) for n in (payload.get("nodes") or [])]
            raw_edges = [dict(e) for e in (payload.get("edges") or [])]
            coverage = list(payload.get("coverage") or record.get("volumes") or [])
        else:
            g = _kn.get_knowledge_service().graph_for(student_id)
            raw_nodes = [n.to_dict() for n in g.nodes.values()]
            raw_edges = [{"source": e.source, "target": e.target,
                          "type": e.type.value, "provenance": e.provenance}
                         for e in g.edges]

        if file_id:
            chapter_ids = {str(n.get("id") or "") for n in raw_nodes
                           if n.get("kind") == "chapter"
                           and str((n.get("metadata") or {}).get("file_id") or
                                   (n.get("metadata") or {}).get("volume_id") or "") == file_id}
            section_ids = {str(n.get("id") or "") for n in raw_nodes
                           if n.get("kind") == "section"}
            member_ids = _chapter_closure(raw_edges, chapter_ids, section_ids)
            allowed_ids = chapter_ids | set().union(*member_ids.values()) \
                if member_ids else chapter_ids
            raw_nodes = [n for n in raw_nodes if str(n.get("id") or "") in allowed_ids]
            raw_edges = [e for e in raw_edges
                         if str(e.get("source") or e.get("from") or "") in allowed_ids
                         and str(e.get("target") or e.get("to") or "") in allowed_ids]
            coverage = [v for v in coverage if str(v.get("file_id") or "") == file_id]

        if level:
            raw_nodes = [n for n in raw_nodes if str(n.get("level") or "") == level]
        if subject:
            raw_nodes = [n for n in raw_nodes if str(n.get("subject") or "") == subject]
        allowed_ids = {str(n.get("id") or "") for n in raw_nodes}
        raw_edges = [e for e in raw_edges
                     if str(e.get("source") or e.get("from") or "") in allowed_ids
                     and str(e.get("target") or e.get("to") or "") in allowed_ids]

        if view == "chapter":
            if not chapter_id or chapter_id not in allowed_ids:
                raise HTTPException(404, "章节不存在")
            section_ids = {str(n.get("id") or "") for n in raw_nodes
                           if n.get("kind") == "section"}
            member_ids = _chapter_closure(raw_edges, {chapter_id}, section_ids)
            allowed_ids = {chapter_id} | (set().union(*member_ids.values())
                                           if member_ids else set())
            raw_nodes = [n for n in raw_nodes if str(n.get("id") or "") in allowed_ids]
            raw_edges = [e for e in raw_edges
                         if str(e.get("source") or e.get("from") or "") in allowed_ids
                         and str(e.get("target") or e.get("to") or "") in allowed_ids]
        elif view == "search":
            from app.core.evidence_gate import fold_punct
            term = fold_punct(q).casefold()
            if not term:
                return {"status": "ok", "nodes": [], "edges": [], "coverage": coverage,
                        "view": view}
            matched = set()
            for n in raw_nodes:
                haystack = fold_punct(" ".join(
                    [str(n.get("name") or ""),
                     str(n.get("description") or ""),
                     *[str(a) for a in (n.get("aliases") or [])]])).casefold()
                if term in haystack:
                    matched.add(str(n.get("id") or ""))
            # Include chapter/section containers for matched descendants.
            for edge in raw_edges:
                source_id = str(edge.get("source") or edge.get("from") or "")
                target_id = str(edge.get("target") or edge.get("to") or "")
                if source_id in matched and str(edge.get("type") or "").upper() == "PART_OF":
                    matched.add(target_id)
            allowed_ids = matched
            raw_nodes = [n for n in raw_nodes if str(n.get("id") or "") in matched]
            raw_edges = [e for e in raw_edges
                         if str(e.get("source") or e.get("from") or "") in matched
                         and str(e.get("target") or e.get("to") or "") in matched]
        elif view == "overview":
            chapters = [n for n in raw_nodes if n.get("kind") == "chapter"]
            chapter_ids = {str(n.get("id") or "") for n in chapters}
            section_ids = {str(n.get("id") or "") for n in raw_nodes
                           if n.get("kind") == "section"}
            child_ids = _chapter_closure(raw_edges, chapter_ids, section_ids)
            # 概念计数走传递闭包（概念可挂节：概念→节→章）；节数单独计。
            counts: dict[str, int] = {cid: 0 for cid in chapter_ids}
            section_counts: dict[str, int] = {cid: 0 for cid in chapter_ids}
            for cid, members in child_ids.items():
                section_counts[cid] = sum(1 for m in members if m in section_ids)
                counts[cid] = sum(1 for m in members if m not in section_ids)
            raw_nodes = [{**n, "metadata": {**dict(n.get("metadata") or {}),
                                             "concept_count": counts.get(str(n.get("id") or ""), 0),
                                             "section_count": section_counts.get(str(n.get("id") or ""), 0)}}
                         for n in chapters]
            raw_edges = []

        nodes: list[dict[str, Any]] = []
        for source_node in raw_nodes:
            d = dict(source_node)
            if d.get("level") == CUSTOM_LEVEL:
                d["level"] = OTHER_LEVEL  # P6：遗留「自定义」图谱归入「其他」组
            m = overlay.get(str(d.get("id") or ""))
            d["mastery"] = ({"p_known": m["p_known"], "state": m["state"]}
                            if m else None)
            nodes.append(d)
        nodes.sort(key=lambda d: d["id"])
        edges = [{"from": e.get("source") or e.get("from"),
                  "to": e.get("target") or e.get("to"), "type": e.get("type")}
                 for e in raw_edges]
        learned = sum(1 for e in raw_edges if e.get("provenance") != "seed")
        return {"status": "ok", "nodes": nodes, "edges": edges,
                "learned_edges": learned, "coverage": coverage, "view": view,
                "scope": {"level": level, "subject": subject,
                          "textbook_id": textbook_id, "file_id": file_id}}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/concepts/{concept_id}")
def knowledge_concept(
    concept_id: str,
    student_id: str = Depends(resolve_student_id),
) -> dict:
    """One concept's detail page: node fields + resolved teaching content,
    typed neighbor edges, this student's mastery, recent teaching-log turns
    (M3) and matching episodic memories (M6). Accepts a node id or a free-text
    name (fuzzy-matched like the retriever does)."""
    if not _kn.is_enabled():
        return {"status": "disabled"}
    try:
        from app.agents.knowledge.schema import EdgeType
        g = _kn.get_knowledge_service().graph_for(student_id)
        node = g.get(concept_id) or g.match_concept(concept_id)
        if node is None:
            return {"status": "not_found", "concept": None}

        def _refs(ids: list[str]) -> list[dict[str, str]]:
            return [{"id": i, "name": g.nodes[i].name if i in g.nodes else i}
                    for i in ids]

        prereq_ids: list[str] = []
        unlock_ids: list[str] = []
        parent_ids: list[str] = []
        child_ids: list[str] = []
        for e in g.edges:
            if e.type == EdgeType.PREREQUISITE:
                if e.target == node.id:
                    prereq_ids.append(e.source)
                elif e.source == node.id:
                    unlock_ids.append(e.target)
            elif e.type == EdgeType.PART_OF:
                # 结构归属（课文/单元节点原本只有这类边，缺了它们详情页就空白）。
                # part_of 的 source 是成员、target 是容器（与前端 from→to 一致）：
                # 课文的 parents=所属单元；概念的 parents=所属课文/单元；
                # children=课文/单元下的成员。
                if e.source == node.id:
                    parent_ids.append(e.target)
                elif e.target == node.id:
                    child_ids.append(e.source)
        edges = {
            "prerequisites": _refs(prereq_ids),
            "unlocks": _refs(unlock_ids),
            "parents": _refs(parent_ids),
            "children": _refs(child_ids),
            "related": _refs(g.neighbors(node.id, edge_type=EdgeType.RELATED)),
            "applications": _refs(g.neighbors(node.id, edge_type=EdgeType.APPLICATION)),
            "misconceptions": _refs(g.neighbors(node.id, edge_type=EdgeType.MISCONCEPTION)),
        }
        content, _snippets = _kn.ContentResolver(g.contents).resolve(
            node.id, query_hint=node.name)
        mastery = (_mastery_overlay(student_id) or {}).get(node.id)
        # teaching log (M3) is keyed by whatever concept string the turn used
        # (often the display name, sometimes the skill id) -- try both.
        teaching: list[dict[str, Any]] = []
        try:
            from app.agents.teaching_engine.teaching_log import recent_for_concept
            teaching = [e.to_dict() for e in
                        recent_for_concept(student_id, node.id, limit=5)]
            if not teaching and node.name != node.id:
                teaching = [e.to_dict() for e in
                            recent_for_concept(student_id, node.name, limit=5)]
        except Exception:
            teaching = []
        # episodic memories (M6) mentioning this concept
        memories: list[dict[str, Any]] = []
        try:
            from app.agents.memory import episodic as _episodic
            memories = [e.to_dict() for e in
                        _episodic.episodes_for_concept(student_id, node.name,
                                                       limit=5)]
        except Exception:
            memories = []
        concept = node.to_dict()
        concept["content"] = content.to_dict()
        return {"status": "ok", "concept": concept, "edges": edges,
                "mastery": mastery, "teaching_log": teaching,
                "memories": memories}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# --- M5.8 taxonomy ----------------------------------------------------------


def _visible_textbooks(student_id: str) -> list[tuple[dict[str, Any], str]]:
    """Return own textbooks plus public textbooks, with own ids taking priority."""
    from app.core import textbook as tb_store
    out: list[tuple[dict[str, Any], str]] = []
    seen: set[str] = set()
    for sid in (student_id, tb_store.PUBLIC_STUDENT_ID):
        if sid == tb_store.PUBLIC_STUDENT_ID and student_id == sid:
            continue
        for tb in tb_store.load_textbooks(sid):
            if tb["id"] in seen:
                continue
            seen.add(tb["id"])
            out.append((tb, sid))
    return out


@router.get("/taxonomy")
def knowledge_taxonomy(student_id: str = Depends(resolve_student_id)) -> dict:
    """动态投影 M5 三级目录：学段 → 资料中心学科 → 教材组。

    资料中心教材记录是分类唯一事实源；图谱只提供当前 topic_key 下的节点
    数量/节点 id。编辑标题、学科、学段或组备注不会触碰图谱节点和边。
    """
    if not _kn.is_enabled():
        return {"status": "disabled", "levels": []}
    try:
        g = _kn.get_knowledge_service().graph_for(student_id)
        from app.agents.knowledge.custom_graph import OTHER_LEVEL, CUSTOM_LEVEL
        buckets: dict[str, dict[str, dict[str, Any]]] = {}
        order = ("小学", "初中", "高中", "本科", "其他")
        from app.core.library import load_library
        from app.agents.knowledge import store as kg_store
        for tb, owner_sid in _visible_textbooks(student_id):
            level = str(tb.get("level") or "其他").strip()
            if level == CUSTOM_LEVEL or level not in order:
                level = OTHER_LEVEL
            prefix = f"custom.{tb['topic_key']}."
            node_ids = sorted(n.id for n in g.nodes.values() if n.id.startswith(prefix))
            subject = str(tb.get("subject") or "").strip()
            if not subject:
                subject = next((n.subject for n in g.nodes.values() if n.id.startswith(prefix) and n.subject), "未分类")
            group_id = tb["id"]
            lib = load_library(owner_sid)
            payload = kg_store.load_custom_graph(owner_sid, tb["topic_key"]) or {}
            coverage_by_file = {str(v.get("file_id") or ""): v
                                for v in (payload.get("coverage") or tb.get("volumes") or [])}
            # 节数按卷统计（节节点 metadata.file_ids 记录归属卷）
            sections_by_file: dict[str, int] = {}
            for n in (payload.get("nodes") or []):
                if n.get("kind") == "section":
                    for f in (n.get("metadata") or {}).get("file_ids") \
                            or (n.get("metadata") or {}).get("volume_ids") or []:
                        if f:
                            sections_by_file[str(f)] = sections_by_file.get(str(f), 0) + 1
            file_ids = list(tb.get("file_ids") or ([] if not tb.get("file_id") else [tb["file_id"]]))
            volumes = []
            for fid in file_ids:
                meta = lib.find_file(fid) or {}
                cov = coverage_by_file.get(fid, {})
                volumes.append({
                    "file_id": fid,
                    "name": meta.get("filename") or fid,
                    "chapter_count": int(cov.get("included_chapter_count") or 0),
                    "section_count": sections_by_file.get(fid, 0),
                    "concept_count": int(cov.get("included_concept_count") or 0),
                    "status": cov.get("status") or "pending",
                    "truncated": bool(cov.get("truncated", False)),
                    "effective_limits": cov.get("effective_limits") or {},
                    "error": cov.get("error") or "",
                })
            subject_bucket = buckets.setdefault(level, {}).setdefault(subject, {
                "name": subject, "groups": []})
            subject_bucket["groups"].append({
                "id": group_id,
                "textbook_id": group_id,
                "topic_key": tb["topic_key"],
                "name": tb.get("group_name") or tb.get("title") or "未命名教材",
                "note": tb.get("group_note", ""),
                "kind": tb.get("kind", "single"),
                "scope": tb.get("scope", "private"),
                "status": tb.get("status", "building"),
                "file_ids": file_ids,
                "volumes": volumes,
                "graph_policy": tb.get("graph_policy") or {},
                # Prefix is enough for client-side isolation and avoids
                # duplicating hundreds of node ids in the taxonomy payload.
                "node_prefix": prefix,
                "node_count": sum(1 for nid in node_ids if ".ch" not in nid and ".s." not in nid),
                "chapter_count": sum(1 for nid in node_ids if ".ch" in nid),
                "section_count": sum(1 for nid in node_ids if ".s." in nid),
            })
        levels = []
        for level in [*order, *sorted(k for k in buckets if k not in order)]:
            subjects = buckets.get(level, {})
            if subjects:
                levels.append({
                    "name": level,
                    "subjects": [
                        {"name": name, "groups": sorted(value["groups"], key=lambda x: x["name"])}
                        for name, value in sorted(subjects.items(), key=lambda x: x[0])
                    ],
                })
        return {"status": "ok", "levels": levels}
    except Exception as e:
        return {"status": "error", "levels": [], "message": str(e)}


# --- M5.8 catalog -----------------------------------------------------------


@router.get("/catalog")
def knowledge_catalog(student_id: str = Depends(resolve_student_id)) -> dict:
    """Stage -> subjects catalog（M4 出题/M9 目标表单的两级下拉）。

    P6-A2 起考纲 seed 已移除：学段×学科目录从该生可见合并图（公用 + 自有
    教材图谱）的节点 level/subject 聚合；遗留「自定义」level 归并为「其他」。
    """
    if not _kn.is_enabled():
        return {"status": "disabled"}
    try:
        from app.agents.knowledge.custom_graph import CUSTOM_LEVEL, OTHER_LEVEL
        svc = _kn.get_knowledge_service()
        g = svc.graph_for(student_id)
        by_level: dict[str, set[str]] = {}
        for n in g.nodes.values():
            lv = OTHER_LEVEL if n.level == CUSTOM_LEVEL else (n.level or "")
            if not lv:
                continue
            by_level.setdefault(lv, set()).add(n.subject or "")
        order = ("小学", "初中", "高中", "本科", "其他")
        levels = [lv for lv in order if lv in by_level] + \
            sorted(lv for lv in by_level if lv not in order)
        stages = [{"level": lv,
                   "subjects": sorted(s for s in by_level[lv] if s)}
                  for lv in levels]
        return {"status": "ok", "stages": stages}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# --- M5.7 custom graphs -----------------------------------------------------
# P6-A4：知识谱系只来自教材——手动 build/regenerate/rollback 端点已移除
# （教材图谱生命周期由 /textbooks/* 管理）。保留只读列表与删除（清理遗留
# 手动图谱）。运行时读路径从不调 LLM。


@router.get("/custom")
def custom_list(student_id: str = Depends(resolve_student_id)) -> dict:
    """List this student's active custom graphs (meta only)."""
    if not _kn.is_enabled():
        return {"status": "disabled"}
    return {"status": "ok",
            "graphs": _kn.get_knowledge_service().list_custom(student_id=student_id)}


@router.delete("/custom/{topic_key}")
async def custom_delete(
    topic_key: str,
    student_id: str = Depends(resolve_student_id),
) -> dict:
    """Archive a legacy standalone graph into the unified recycle bin."""
    if not _kn.is_enabled():
        return {"status": "disabled"}
    from app.core.trash import archive_knowledge_graph
    try:
        item = archive_knowledge_graph(student_id, topic_key)
    except FileNotFoundError:
        raise HTTPException(404, "知识谱系不存在")
    _kn.get_knowledge_service().invalidate_custom_cache(student_id)
    return {"status": "archived", "topic_key": topic_key, "trash_item": item}
