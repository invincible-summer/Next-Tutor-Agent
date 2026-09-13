"""M5 纯图/范围原语（plan §5.1 第 4 条：从 API 路由提炼，评价模块复用）。

无 IO、无 LLM、不 import API 层——`api/v1/knowledge.py` 与
`agents/student_model/evaluation/scope.py` 共用同一份卷归属语义。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

# 节点归属规则版本：卷过滤/闭包算法语义变化时 +1（进入 scope_revision）。
NODE_ATTRIBUTION_RULE_VERSION = 1


def _edge_endpoints(edge: dict[str, Any]) -> tuple[str, str]:
    source = str(edge.get("source") or edge.get("from") or "")
    target = str(edge.get("target") or edge.get("to") or "")
    return source, target


def chapter_closure(edges: Iterable[dict[str, Any]], chapter_ids: set[str],
                    section_ids: set[str]) -> dict[str, set[str]]:
    """章 → 成员闭包（PART_OF 传递：概念→节→章 与 概念→章 两种形状）。

    返回 {chapter_id: {member_id, ...}}，member 含节与概念；旧图谱（无节）
    退化为直接子成员。与原 `api/v1/knowledge.py::_chapter_closure` 行为一致。
    """
    part_children: dict[str, set[str]] = {}
    for edge in edges or []:
        if str(edge.get("type") or "").upper() != "PART_OF":
            continue
        source_id, target_id = _edge_endpoints(edge)
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


def _node_volume_ids(node: dict[str, Any]) -> set[str]:
    """节点声称的卷归属（chapter/section: file_id|volume_id；concept:
    file_ids|volume_ids）。缺 provenance 的旧节点返回空集——不放行（§5.1.5）。
    """
    meta = node.get("metadata") or {}
    out: set[str] = set()
    for key in ("file_id", "volume_id"):
        v = str(meta.get(key) or "")
        if v:
            out.add(v)
    for key in ("file_ids", "volume_ids"):
        v = meta.get(key)
        if isinstance(v, list):
            out.update(str(x) for x in v if x)
    return out


def volume_scoped_subgraph(
        nodes: list[dict[str, Any]], edges: list[dict[str, Any]],
        selected_file_ids: set[str]) -> tuple[list[dict[str, Any]],
                                              list[dict[str, Any]]]:
    """抽取已选卷的节点与内部边（A08：选上册不进下册）。

    章按自身卷归属过滤，概念经章闭包归属；节点显式卷归属与章闭包取并集
    后仍必须与已选卷相交。无卷归属的节点不因「组内某卷已选」而放行。
    """
    if not selected_file_ids:
        return [], []
    by_id = {str(n.get("id") or ""): n for n in nodes}
    chapter_ids = {nid for nid, n in by_id.items()
                   if n.get("kind") == "chapter"}
    section_ids = {nid for nid, n in by_id.items()
                   if n.get("kind") == "section"}
    members = chapter_closure(edges, chapter_ids, section_ids)
    chapter_to_volumes: dict[str, set[str]] = {}
    for cid in chapter_ids:
        vols = _node_volume_ids(by_id[cid])
        if vols & selected_file_ids:
            chapter_to_volumes[cid] = vols

    allowed: set[str] = set()
    for cid in chapter_to_volumes:
        allowed.add(cid)
        allowed |= members.get(cid, set())
    for nid, n in by_id.items():
        if n.get("kind") != "concept":
            continue
        claimed = _node_volume_ids(n)
        if claimed and (claimed & selected_file_ids):
            allowed.add(nid)
            continue
        # 概念自身 provenance 缺失时，仅当其所属章在已选卷内才放行
        for cid in chapter_to_volumes:
            if nid in members.get(cid, set()):
                allowed.add(nid)
                break
    scoped_nodes = [n for n in nodes if str(n.get("id") or "") in allowed]
    scoped_edges = [e for e in edges
                    if _edge_endpoints(e)[0] in allowed
                    and _edge_endpoints(e)[1] in allowed]
    return scoped_nodes, scoped_edges


def _sha(obj: Any, length: int = 40) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def graph_content_revision(payload: dict[str, Any]) -> str:
    """graph_revision：图谱结构/来源/语义的内容指纹（§5.2），不依赖 mtime。
    显示顺序/坐标类字段（chapter_order/raw_heading/page_range）不参与。"""
    nodes = []
    for n in payload.get("nodes") or []:
        nodes.append([str(n.get("id") or ""), str(n.get("kind") or ""),
                      str(n.get("name") or ""),
                      str(n.get("description") or ""),
                      sorted(str(a) for a in (n.get("aliases") or [])),
                      sorted(_node_volume_ids(n))])
    edges = [[*_edge_endpoints(e), str(e.get("type") or "")]
             for e in payload.get("edges") or []]
    return "gr_" + _sha({"n": sorted(nodes), "e": sorted(edges)})


def concept_revision(textbook_id: str, node: dict[str, Any]) -> str:
    """concept_revision：教材标识、稳定节点 ID、定义/目标语义、来源卷与
    稳定内容定位的指纹（§5.2）。单纯显示顺序/坐标变化不改变此值。"""
    meta = node.get("metadata") or {}
    return "cr_" + _sha({
        "tb": textbook_id,
        "id": str(node.get("id") or ""),
        "name": str(node.get("name") or ""),
        "desc": str(node.get("description") or ""),
        "aliases": sorted(str(a) for a in (node.get("aliases") or [])),
        "vols": sorted(_node_volume_ids(node)),
        "chapters": sorted(str(c) for c in meta.get("chapter_ids") or []),
    })


def scope_revision_for(selected: list[dict[str, Any]]) -> str:
    """scope_revision：已选卷有序集合、ownership、graph_revision、节点归属
    规则版本的确定性计算（§5.2）。selected 每项含 owner/textbook/file_ids/
    graph_revision。"""
    return "sr_" + _sha({
        "rule": NODE_ATTRIBUTION_RULE_VERSION,
        "vols": selected,
    })
