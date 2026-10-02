"""Custom knowledge graphs (M5.7): per-student, per-topic LLM-built ontologies.

This covers topics the curated seed packs can never cover (嵌入式开发, 微积分,
a specific textbook's syllabus...). The user provides a topic name and
optionally textbook/syllabus text; ONE LLM call drafts the ontology; everything
after that is deterministic.

THE UNIQUENESS CONTRACT (唯一性铁律 — the whole point of M5.7):
  - At most ONE active graph per (student, topic_key); the active file under
    knowledge/custom/<student>/ is the single source of truth.
  - build() NEVER calls the LLM when an active graph already exists — it
    returns the existing graph. Asking twice cannot drift the syllabus.
  - regenerate() is the ONLY LLM-rebuild entry; it is explicit, archives the
    previous version, and swaps the active file atomically (tmp + os.replace),
    so a crash can never leave a half-written graph.
  - rollback() restores the most recent archive; delete() archives then
    removes the active file. Old versions stay on disk for audit.
  - The runtime READ path (graph merge, retriever, /knowledge/graph) never
    calls the LLM — it only reads these files.

Node ids live in the `custom.<topic_key>.*` namespace so they cannot collide
with seed packs or another topic's graph. Anchoring back to the main graph is
deterministic and strict: a custom concept gets at most one RELATED edge to a
seed node, only when the strict match (exact/alias/substring, score >= 0.8)
finds one — never a loose token-overlap guess.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import replace
from typing import Any

from .graph import KnowledgeGraph
from .schema import EdgeType, KnowledgeEdge, KnowledgeNode
from ...prompts.registry import get as get_prompt

#: level stamped on legacy custom nodes (pre-P6 manual graphs), so the frontend
#: groups them under one 自定义 chip. P6 起教材图谱 level 取上传时选择的学段
#: （小学/初中/高中/本科/其他），自定义组只用于遗留图谱。
CUSTOM_LEVEL = "自定义"
#: 教材图谱的「其他」学段（上传时显式选择，不参与四学段偏好检索）。
OTHER_LEVEL = "其他"

MAX_CHAPTERS = 15
MAX_CONCEPTS = 120
MAX_CONCEPTS_PER_CHAPTER = 20
MAX_ALIASES = 6
MAX_PREREQ_REFS = 6
#: material excerpt fed to the LLM is capped so one upload cannot blow the
#: prompt budget (the API layer truncates to this before calling build).
MAX_MATERIAL_CHARS = 20000

_ANCHOR_MIN_SCORE = 0.8   # exact/alias/substring only; see graph.match_concept_scored
_KEY_SAFE = re.compile(r"[^\w.-]+")

#: 标题压缩归一化：去空白/间隔号/常用标点（「沁园春·江畔」→「沁园春江畔」），
#: 让无间隔号的口语查询（对话检索 / 知识页搜索）能精确命中篇目/节名。
_TITLE_STRIP_RE = re.compile(
    r"[\s·・‧﹒．。.、，,：:；;！!？?（）()［\[\]【】「」『』《》〈〉“”\"'‘’\-—_~～*]+")
_TITLE_NUM_PREFIX_RE = re.compile(
    r"^(?:第[0-9０-９一二三四五六七八九十百零]+[课讲篇章节]|[\d０-９]+[、.．]?)+")
#: 节标题展示长度上限（篇目可长于概念名，超长截断防 OCR 噪声行）。
_MAX_SECTION_NAME_CHARS = 60


def compact_title(name: str) -> str:
    """压缩归一化标题：NFKC + 去空白/间隔号/标点 + 去编号前缀 + casefold。

    「1 沁园春·江畔」「沁园春·江畔」「沁园春 长沙」→「沁园春江畔」。
    检索侧（match_concept / 前端搜索 / 证据门短语匹配）与构建侧共用同一
    归一化，标题匹配不再被间隔号/编号差异击穿。
    """
    t = _TITLE_STRIP_RE.sub("", unicodedata.normalize("NFKC", str(name or "")))
    t = _TITLE_NUM_PREFIX_RE.sub("", t)
    return t.casefold()


def section_aliases(name: str) -> list[str]:
    """节/篇目节点的确定性别名：压缩形式（含/不含编号）+ 无编号原名。"""
    raw = str(name or "").strip()
    if not raw:
        return []
    compact = compact_title(raw)
    no_num = _TITLE_NUM_PREFIX_RE.sub("", unicodedata.normalize("NFKC", raw)).strip()
    aliases = [a for a in (compact, no_num) if a and a != raw]
    return list(dict.fromkeys(aliases))[:MAX_ALIASES]


def topic_key(topic: str) -> str:
    """Deterministic, filesystem-safe key for a topic.

    slug(readable) + short content hash: the slug keeps ids/files human-
    readable, the hash makes two different topics that sanitize to the same
    slug (e.g. punctuation-only differences) still map to different keys.
    Same topic string -> same key, always (case/whitespace-insensitive).
    """
    t = (topic or "").strip().lower()
    slug = _KEY_SAFE.sub("_", t)[:24].strip("._-") or "topic"
    digest = hashlib.md5(t.encode("utf-8")).hexdigest()[:6]
    return f"{slug}.{digest}"


def is_custom_id(node_id: str) -> bool:
    return str(node_id or "").startswith("custom.")


# --- LLM draft ------------------------------------------------------------

_BUILD_PROMPT = get_prompt("knowledge_graph_build").text


def build_prompt(topic: str, material_text: str = "", grade: str = "未指定") -> str:
    """The ONE prompt shape used for both build and regenerate.

    P1: 默认「未指定」（与 _student_grade 一致）——未指定学段时 prompt 文案按
    自适应生成，而不是默认高中。
    """
    material = (material_text or "").strip()[:MAX_MATERIAL_CHARS]
    if material:
        block = ("以下教材/教学大纲文本是主要依据，优先按材料的目录结构组织章节：\n"
                 f"\"\"\"\n{material}\n\"\"\"")
    else:
        block = "（未提供教材文本，请依据该主题的公认知识体系直接生成。）"
    return _BUILD_PROMPT.format(topic=(topic or "").strip(),
                                material_block=block,
                                grade=(grade or "未指定").strip(),
                                max_concepts=MAX_CONCEPTS)


def parse_spec(raw: str) -> dict[str, Any] | None:
    """Tolerant JSON extraction of the LLM draft. None on any parse failure."""
    try:
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        data = json.loads(m.group(0) if m else raw)
        if isinstance(data, dict) and isinstance(data.get("chapters"), list) \
                and data["chapters"]:
            return data
    except Exception:
        pass
    return None


async def generate_spec(topic: str, material_text: str, llm: Any,
                        grade: str = "未指定") -> tuple[dict[str, Any] | None, str]:
    """ONE LLM call -> parsed spec, or (None, error-message). Never raises.

    max_tokens is generous on purpose: the configured model is a reasoning
    model whose thinking channel shares the token budget with the answer, so
    a tight cap (e.g. 4000) is fully eaten by reasoning and returns an EMPTY
    content string.
    """
    try:
        raw, _usage = await llm.complete(
            [{"role": "user", "content": build_prompt(topic, material_text,
                                                      grade=grade)}],
            temperature=0.0, max_tokens=16000, disable_thinking=True)
    except Exception as e:
        return None, f"LLM 调用失败：{e}"
    spec = parse_spec(raw)
    if spec is None:
        return None, "LLM 未产出有效的图谱 JSON"
    return spec, ""


# --- deterministic spec -> graph conversion --------------------------------

def spec_to_graph(spec: dict[str, Any], *, topic_key: str, source: str,
                  base_graph: KnowledgeGraph | None = None,
                  max_chapters: int | None = MAX_CHAPTERS,
                  max_concepts: int | None = MAX_CONCEPTS,
                  max_concepts_per_chapter: int | None = MAX_CONCEPTS_PER_CHAPTER,
                  level: str = CUSTOM_LEVEL,
                  ) -> tuple[dict[str, Any], list[str]]:
    """Convert a parsed LLM spec into {nodes, edges, contents} + warnings.

    Deterministic: ids are assigned in appearance order
    (custom.<key>.chN / custom.<key>.cN), name references resolve within the
    graph (unknown names dropped), PREREQUISITE edges pass the real DAG guard
    (cycle-creating edges are dropped with a warning), and each concept gets
    at most one strict-match RELATED anchor into the seed graph.

    P2 教材图谱复用本函数：``max_chapters`` / ``max_concepts`` 形参化（默认即
    自定义图谱常量，既有调用方零影响）；``level`` 形参化（教材构建传入推断学段，
    仅当 ∈ VALID_STAGES 才生效，否则回退 CUSTOM_LEVEL，让 M5 学段感知检索能
    偏好教材节点）。唯一性铁律 / 归档 / DAG 守卫 / 锚定逻辑全部不变。
    """
    warnings: list[str] = []
    origin = "material" if source.startswith("material:") else "llm"
    subject = str(spec.get("subject") or "").strip()[:30]
    # level：仅当传入合法学段才采用（四学段或「其他」），否则回退 CUSTOM_LEVEL。
    from ..teaching_engine.stage_profile import VALID_STAGES, normalize_grade
    lvl_norm = normalize_grade(level)
    if lvl_norm in VALID_STAGES:
        node_level = lvl_norm
    elif (level or "").strip() == OTHER_LEVEL:
        node_level = OTHER_LEVEL
    else:
        node_level = CUSTOM_LEVEL
    chapters = [c for c in (spec.get("chapters") or []) if isinstance(c, dict)]
    if max_chapters is not None:
        chapters = chapters[:max_chapters]

    # pass 1: assign ids (name -> id) so cross-chapter prereq refs resolve
    name_to_id: dict[str, str] = {}
    # ch_ids: (chapter_id, chapter_name, section_items, kept_concepts, metadata)
    ch_ids: list[tuple[str, str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]] = []
    concept_no = 0
    truncated = False
    for ci, ch in enumerate(chapters, 1):
        ch_name = str(ch.get("name") or f"第{ci}章").strip()[:40] or f"第{ci}章"
        stable_key = str(ch.get("chapter_key") or "").strip()
        ch_id = (f"custom.{topic_key}.ch.{stable_key}" if stable_key
                 else f"custom.{topic_key}.ch{ci}")
        # 节（课/篇目/小节）：不占概念预算；字符串条目（未归一化 spec）也接受。
        section_items: list[dict[str, Any]] = []
        seen_sids: set[str] = set()
        for raw_sec in (ch.get("sections") or []):
            sec = raw_sec if isinstance(raw_sec, dict) else {"name": str(raw_sec)}
            sname = re.sub(r"\s+", " ", str(sec.get("name") or "")).strip()
            if not sname:
                continue
            sname = sname[:_MAX_SECTION_NAME_CHARS].rstrip("，。；、")
            compact = compact_title(sname)
            if len(compact) < 2:
                continue
            sid = f"custom.{topic_key}.s.{hashlib.sha256(compact.encode('utf-8')).hexdigest()[:16]}"
            if sid in seen_sids:
                continue
            seen_sids.add(sid)
            rng = sec.get("page_range") or sec.get("pages") or []
            section_items.append({
                "name": sname, "id": sid, "compact": compact,
                "aliases": section_aliases(sname),
                "page_range": [int(rng[0]), int(rng[1])]
                if isinstance(rng, (list, tuple)) and len(rng) >= 2
                and all(str(x).isdigit() for x in rng[:2]) else [],
                "section_key": str(sec.get("section_key") or "").strip(),
            })
        raw_concepts = [c for c in (ch.get("concepts") or []) if isinstance(c, dict)]
        if max_concepts_per_chapter is not None:
            raw_concepts = raw_concepts[:max_concepts_per_chapter]
        kept: list[dict[str, Any]] = []
        for c in raw_concepts:
            name = str(c.get("name") or "").strip()[:40]
            if not name:
                continue
            if name not in name_to_id:
                if max_concepts is not None and concept_no >= max_concepts:
                    truncated = True
                    continue
                concept_no += 1
                normalized = re.sub(r"\s+", "", name).casefold()
                digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
                name_to_id[name] = f"custom.{topic_key}.c.{digest}"
            # Repeated concepts do not consume budget and must retain PART_OF.
            if name in name_to_id:
                kept.append(c)
        chapter_metadata = dict(ch.get("metadata") or {})
        if stable_key:
            chapter_metadata["chapter_key"] = stable_key
        ch_ids.append((ch_id, ch_name, section_items, kept, chapter_metadata))
    if truncated:
        warnings.append(f"概念数超过上限 {max_concepts}，已截断")

    # pass 2: nodes first (so forward prereq refs resolve), then edges
    tmp = KnowledgeGraph()   # add_edge here enforces the PREREQUISITE DAG
    contents: list[dict[str, Any]] = []
    for ch_id, ch_name, section_items, concepts, chapter_metadata in ch_ids:
        tmp.ensure_node(KnowledgeNode(
            id=ch_id, name=ch_name, subject=subject, level=node_level,
            difficulty=3, origin=origin, kind="chapter",
            metadata=chapter_metadata))
        # 节节点：章内二级结构（课/篇目/小节）。可检索（名称+压缩别名）、
        # 不追踪掌握度；page_range 供概念索引把 chunk 精确圈到节检索域。
        sec_by_ref: dict[str, str] = {}
        for sec in section_items:
            sid = sec["id"]
            if sid not in tmp.nodes:
                tmp.ensure_node(KnowledgeNode(
                    id=sid, name=sec["name"], subject=subject, level=node_level,
                    difficulty=3, aliases=list(sec["aliases"]), origin=origin,
                    kind="section",
                    metadata={"file_ids": [], "volume_ids": [],
                              "chapter_ids": [], "section_key": sec["section_key"],
                              "page_range": list(sec["page_range"])}))
            sec_meta = tmp.nodes[sid].metadata
            fid = str(chapter_metadata.get("file_id")
                      or chapter_metadata.get("volume_id") or "")
            for key, value in (("file_ids", fid), ("volume_ids", fid),
                               ("chapter_ids", ch_id)):
                values = list(sec_meta.get(key) or [])
                if value and value not in values:
                    values.append(value)
                sec_meta[key] = values
            tmp.add_edge(KnowledgeEdge(
                source=sid, target=ch_id, type=EdgeType.PART_OF,
                provenance="custom"), _trusted=True)
            sec_by_ref[sec["name"]] = sid
            sec_by_ref[sec["compact"]] = sid
        for c in concepts:
            name = str(c.get("name") or "").strip()[:40]
            cid = name_to_id.get(name)
            if not cid:
                continue
            try:
                diff = max(1, min(5, int(c.get("difficulty", 3))))
            except (TypeError, ValueError):
                diff = 3
            aliases = [str(a).strip()[:30] for a in (c.get("aliases") or [])
                       if str(a).strip()][:MAX_ALIASES]
            if cid not in tmp.nodes:
                tmp.ensure_node(KnowledgeNode(
                    id=cid, name=name, subject=subject, level=node_level,
                    difficulty=diff, aliases=aliases,
                    description=str(c.get("description") or "").strip()[:200],
                    origin=origin, kind="concept",
                    metadata={"file_ids": [], "volume_ids": [], "chapter_ids": []}))
            concept_node = tmp.nodes[cid]
            meta = concept_node.metadata
            fid = str(chapter_metadata.get("file_id") or chapter_metadata.get("volume_id") or "")
            for key, value in (("file_ids", fid), ("volume_ids", fid),
                               ("chapter_ids", ch_id)):
                values = list(meta.get(key) or [])
                if value and value not in values:
                    values.append(value)
                meta[key] = values
            # 概念挂节（确定性：抽取期按节页码域压缩匹配写入 c["section"]）；
            # 解析不到节时维持概念→章的直接 PART_OF（旧图谱形状不变）。
            section_ref = str(c.get("section") or "").strip()
            sid = (sec_by_ref.get(section_ref)
                   or sec_by_ref.get(compact_title(section_ref)) or "") \
                if section_ref else ""
            if sid:
                section_ids = list(meta.get("section_ids") or [])
                if sid not in section_ids:
                    section_ids.append(sid)
                meta["section_ids"] = section_ids
            tmp.add_edge(KnowledgeEdge(
                source=cid, target=sid or ch_id, type=EdgeType.PART_OF,
                provenance="custom"), _trusted=True)
            definition = str(c.get("definition") or "").strip()[:300]
            example = str(c.get("example") or "").strip()[:200]
            if definition or example:
                contents.append({"concept_id": cid, "definition": definition,
                                 "formula": "", "example": example,
                                 "exercise_hint": "", "source": source})

    # pass 3: prereq / related / seed-anchor edges (all nodes now exist)
    anchor_edges: list[KnowledgeEdge] = []
    anchor_seen: set[tuple[str, str]] = set()
    for _ch_id, _ch_name, _sections, concepts, _chapter_metadata in ch_ids:
        for c in concepts:
            name = str(c.get("name") or "").strip()[:40]
            cid = name_to_id.get(name)
            if not cid or cid not in tmp.nodes:
                continue
            for pname in (c.get("prerequisites") or [])[:MAX_PREREQ_REFS]:
                pname = str(pname).strip()
                pid = name_to_id.get(pname)
                if not pid or pid == cid:
                    if pname and not pid:
                        warnings.append(f"未知前置引用「{pname}」已忽略")
                    continue
                ok = tmp.add_edge(KnowledgeEdge(
                    source=pid, target=cid, type=EdgeType.PREREQUISITE,
                    provenance="custom"))
                if not ok:
                    warnings.append(f"前置边 {pname}→{name} 会形成环，已忽略")
            for rname in (c.get("related") or [])[:MAX_PREREQ_REFS]:
                rid = name_to_id.get(str(rname).strip())
                if rid and rid != cid:
                    tmp.add_edge(KnowledgeEdge(
                        source=cid, target=rid, type=EdgeType.RELATED,
                        provenance="custom"))
            # strict anchor back to the seed graph (at most one per concept);
            # the anchor target lives in the base graph, not in tmp, so these
            # edges bypass tmp and are deduped manually (RELATED: no DAG risk)
            if base_graph is not None:
                anchor, score = base_graph.match_concept_scored(
                    name, min_score=_ANCHOR_MIN_SCORE)
                if anchor is not None and anchor.kind != "chapter" \
                        and (cid, anchor.id) not in anchor_seen:
                    anchor_seen.add((cid, anchor.id))
                    anchor_edges.append(KnowledgeEdge(
                        source=cid, target=anchor.id, type=EdgeType.RELATED,
                        weight=round(score, 4), provenance="custom"))

    # pass 4: 方向守卫——LLM 写反的前置边（更难且更靠后的概念指向更简单、
    # 更靠前的概念）就地反转/移除，不再让「复杂章节指向基础章节」进入 DAG。
    chapter_pos: dict[str, int] = {}
    for ci, (ch_id, *_rest) in enumerate(ch_ids):
        chapter_pos.setdefault(ch_id, ci)
    _repair_prereq_directions(tmp, chapter_pos, warnings)

    concept_nodes = [n for n in tmp.nodes.values() if n.kind == "concept"]
    section_nodes = [n for n in tmp.nodes.values() if n.kind == "section"]
    data = {
        "subject": subject,
        "level": node_level,
        "nodes": [n.to_dict() for n in tmp.nodes.values()],
        "edges": [e.to_dict() for e in (*tmp.edges, *anchor_edges)],
        "contents": contents,
        "concept_count": len(concept_nodes),
        "section_count": len(section_nodes),
    }
    return data, warnings


def _repair_prereq_directions(graph: KnowledgeGraph, chapter_pos: dict[str, int],
                              warnings: list[str]) -> None:
    """前置边方向守卫：双信号可疑边反转，反转会成环则移除。

    可疑 = source 比 target **更难**（difficulty 严格更大）**且** source 的
    章位置比 target **更靠后**。两个信号同时命中才处理：同章边、同难度边、
    缺位置信息的边一律不动——「用难工具定义简单概念」的合法边（同章或跨章
    但 source 在前）不受影响。章位置取概念 chapter_ids 中最靠前的章；列表
    顺序即教学序（单卷 spec 按目录序，组构建合并 spec 已按卷序全局排序）。
    """
    def _pos(node_id: str) -> int | None:
        cids = ((graph.nodes[node_id].metadata or {}).get("chapter_ids")
                if node_id in graph.nodes else None) or []
        vals = [chapter_pos[c] for c in cids if c in chapter_pos]
        return min(vals) if vals else None

    for edge in [e for e in graph.edges
                 if e.type == EdgeType.PREREQUISITE
                 and e.source in graph.nodes and e.target in graph.nodes
                 and graph.nodes[e.source].kind == "concept"
                 and graph.nodes[e.target].kind == "concept"]:
        src = graph.nodes[edge.source]
        dst = graph.nodes[edge.target]
        spos, dpos = _pos(edge.source), _pos(edge.target)
        if spos is None or dpos is None or spos <= dpos \
                or not src.difficulty > dst.difficulty:
            continue
        graph._remove_edge(edge)
        flipped = replace(edge, source=edge.target, target=edge.source)
        if graph.add_edge(flipped):
            warnings.append(
                f"前置边 {src.name}→{dst.name} 疑似方向颠倒（更难且更靠后），"
                f"已反转为 {dst.name}→{src.name}")
        else:
            warnings.append(
                f"前置边 {src.name}→{dst.name} 疑似方向颠倒且反转会成环，已移除")


def graph_meta(payload: dict[str, Any]) -> dict[str, Any]:
    """The small summary dict the API lists/returns (no node/edge payloads)."""
    nodes = payload.get("nodes", []) or []
    edges = payload.get("edges", []) or []
    return {
        "topic": payload.get("topic", ""),
        "topic_key": payload.get("topic_key", ""),
        "subject": payload.get("subject", ""),
        "level": payload.get("level", CUSTOM_LEVEL),
        "version": int(payload.get("version", 1) or 1),
        "source": payload.get("source", "llm"),
        "created_at": payload.get("created_at", ""),
        "updated_at": payload.get("updated_at", ""),
        "node_count": sum(1 for n in nodes if n.get("kind") == "concept"),
        "chapter_count": sum(1 for n in nodes if n.get("kind") == "chapter"),
        "section_count": sum(1 for n in nodes if n.get("kind") == "section"),
        "edge_count": len(edges),
        "archive_count": int(payload.get("archive_count", 0) or 0),
    }
