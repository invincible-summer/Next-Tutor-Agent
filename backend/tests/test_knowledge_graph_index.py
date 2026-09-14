"""Knowledge-graph traversal index + cold-merge performance contract.

The textbook packs merge ~16K nodes / 30K edges into every student's graph.
Two structural rules keep that affordable (a regression froze the whole
service for minutes -- see update_plan 2026-08-23):

  1. add_edge dedupe is O(1) (edge-key set), not a linear scan over edges.
  2. _reaches / ancestors / descendants walk adjacency maps, not the edge
     list; the store's merged-graph stamp ignores *.chunks.json so chunk
     rewrites can no longer invalidate the merged cache.

These tests pin the semantics (equivalence against a naive reference over
the edge list) and the cache-invalidation contract.
"""
from __future__ import annotations

import json
import random
import threading
import unittest
from unittest import mock

from app.agents.knowledge import KnowledgeGraph
from app.agents.knowledge import store as store_mod
from app.agents.knowledge.manager import KnowledgeService
from app.agents.knowledge.schema import EdgeType, KnowledgeEdge, KnowledgeNode
from tests.storage_sandbox import StorageSandboxTestCase


def _node(nid: str, difficulty: int = 3) -> KnowledgeNode:
    return KnowledgeNode(id=nid, name=nid, subject="s", difficulty=difficulty)


def _edge(src: str, tgt: str, etype: EdgeType = EdgeType.PREREQUISITE,
          weight: float = 1.0) -> KnowledgeEdge:
    return KnowledgeEdge(source=src, target=tgt, type=etype, weight=weight)


def _naive_ancestors(g: KnowledgeGraph, node_id: str) -> set[str]:
    """The pre-index reference: scan the full edge list per DFS step."""
    seen: set[str] = set()
    stack = [node_id]
    while stack:
        cur = stack.pop()
        for e in g.edges:
            if e.type == EdgeType.PREREQUISITE and e.target == cur \
                    and e.source not in seen:
                seen.add(e.source)
                stack.append(e.source)
    return seen


def _naive_descendants(g: KnowledgeGraph, node_id: str) -> set[str]:
    seen: set[str] = set()
    stack = [node_id]
    while stack:
        cur = stack.pop()
        for e in g.edges:
            if e.type == EdgeType.PREREQUISITE and e.source == cur \
                    and e.target not in seen:
                seen.add(e.target)
                stack.append(e.target)
    return seen


def _random_dag(seed: int, n: int = 60) -> KnowledgeGraph:
    """Layered random DAG (layer i nodes may depend on layer i-1 only)."""
    rng = random.Random(seed)
    g = KnowledgeGraph()
    layers: list[list[str]] = []
    for i in range(6):
        ids = [f"n{seed}_{i}_{j}" for j in range(n // 6)]
        for nid in ids:
            g.ensure_node(_node(nid, difficulty=rng.randint(1, 5)))
        layers.append(ids)
    for i in range(1, len(layers)):
        for nid in layers[i]:
            for pre in rng.sample(layers[i - 1], k=rng.randint(0, 3)):
                g.add_edge(_edge(pre, nid))
            # a few non-prereq context edges
            if rng.random() < 0.3:
                other = rng.choice(layers[i])
                if other != nid:
                    g.add_edge(_edge(nid, other, EdgeType.RELATED))
    return g


class TestTraversalIndexEquivalence(unittest.TestCase):

    def test_ancestors_descendants_match_naive_scan(self):
        for seed in (1, 2, 3):
            g = _random_dag(seed)
            for nid in list(g.nodes)[:25]:
                self.assertEqual(set(g.prerequisites_of(nid)),
                                 _naive_ancestors(g, nid))
                self.assertEqual(set(g.descendants_of(nid)),
                                 _naive_descendants(g, nid))

    def test_reaches_matches_naive_and_respects_type(self):
        g = _random_dag(7)
        naive_reach = {}
        for a in list(g.nodes)[:15]:
            for b in list(g.nodes)[:15]:
                # reference BFS over the raw edge list
                seen, stack = {a}, [a]
                while stack:
                    cur = stack.pop()
                    for e in g.edges:
                        if e.type == EdgeType.PREREQUISITE and e.source == cur:
                            seen.add(e.target)
                            stack.append(e.target)
                naive_reach[(a, b)] = b in seen
        for (a, b), expected in naive_reach.items():
            self.assertEqual(g._reaches(a, b, EdgeType.PREREQUISITE), expected,
                             f"_reaches({a},{b}) diverged")

    def test_dedupe_and_cycle_guard_still_reject(self):
        g = KnowledgeGraph()
        g.ensure_node(_node("a"))
        g.ensure_node(_node("b"))
        g.ensure_node(_node("c"))
        self.assertTrue(g.add_edge(_edge("a", "b")))
        self.assertFalse(g.add_edge(_edge("a", "b")))          # exact dup
        # a different type over the same pair is a distinct edge
        self.assertTrue(g.add_edge(_edge("a", "b", EdgeType.RELATED)))
        self.assertFalse(g.add_edge(_edge("a", "b", EdgeType.RELATED)))
        self.assertTrue(g.add_edge(_edge("b", "c")))
        # c -> a would close the cycle a -> b -> c -> a (untrusted writes only)
        self.assertFalse(g.add_edge(_edge("c", "a")))
        # trusted seeding bypasses the cycle guard by contract
        self.assertTrue(g.add_edge(_edge("c", "a"), _trusted=True))

    def test_neighborhood_unchanged_semantics(self):
        g = _random_dag(11)
        for nid in list(g.nodes)[:20]:
            nb = g.neighborhood(nid, depth=2, limit=12)
            self.assertTrue(len(nb) <= 12)
            self.assertNotIn(nid, nb)
            # naive BFS over the raw edge list (both directions, same types)
            seen, frontier, out = {nid}, [nid], set()
            for _ in range(2):
                nxt = []
                for cur in frontier:
                    for e in g.edges:
                        if e.type not in (EdgeType.RELATED, EdgeType.APPLICATION):
                            continue
                        other = e.target if e.source == cur \
                            else e.source if e.target == cur else None
                        if other and other not in seen:
                            seen.add(other)
                            nxt.append(other)
                            out.add(other)
                frontier = nxt
            self.assertTrue(set(nb) <= out)
            self.assertEqual(len(set(nb)), len(nb))   # no duplicates


class TestStoreChunksExclusion(StorageSandboxTestCase):

    def test_stamp_and_listing_ignore_chunks_files(self):
        store_mod.save_custom_graph("stu_idx", "k1.aa", {
            "nodes": [{"id": "x", "name": "X", "subject": "s"}],
            "edges": [], "contents": []})
        chunks_path = store_mod._student_dir("stu_idx") / "k1.aa.chunks.json"
        chunks_path.write_text(json.dumps(
            {"concepts": {"x": ["c1"]}}), encoding="utf-8")

        stamp = store_mod.list_custom_stamp("stu_idx")
        self.assertIn(("k1.aa", mock.ANY), stamp)
        self.assertNotIn(("k1.aa.chunks", mock.ANY), stamp)

        payloads = store_mod.list_custom_graphs("stu_idx")
        self.assertEqual([p for p in payloads if p.get("nodes")],
                         [{"nodes": [{"id": "x", "name": "X", "subject": "s"}],
                           "edges": [], "contents": []}])

        # rewriting chunks must NOT invalidate the merged-graph stamp
        before = store_mod.list_custom_stamp("stu_idx")
        chunks_path.write_text(json.dumps(
            {"concepts": {"x": ["c2"]}}), encoding="utf-8")
        self.assertEqual(store_mod.list_custom_stamp("stu_idx"), before)


class TestGraphForConcurrency(StorageSandboxTestCase):

    def test_concurrent_cold_merges_build_once(self):
        store_mod.save_custom_graph("stu_conc", "k1.bb", {
            "nodes": [{"id": "y", "name": "Y", "subject": "s"}],
            "edges": [], "contents": []})
        svc = KnowledgeService()
        # deepcopy is called once per actual merge; a race would run it twice
        with mock.patch("app.agents.knowledge.manager.copy.deepcopy",
                        wraps=__import__("copy").deepcopy) as dp:
            outs: list[object] = []
            barrier = threading.Barrier(4)

            def worker():
                barrier.wait()
                outs.append(svc.graph_for("stu_conc"))

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertTrue(all(o is outs[0] for o in outs))
        self.assertEqual(dp.call_count, 1)


# G4：student_model.skill_graph 已删（M5 合并图谱为唯一图源）；其缓存
# 契约由上方 KnowledgeService/graph_for 的合并缓存测试覆盖。


class TestEvaluationViewNamespace(StorageSandboxTestCase):
    """M9 的读侧助手必须按调用者的 student_id 键控 journal / 图谱命名
    空间（旧缺陷：无条件回退游客命名空间）。"""

    def test_evaluation_view_uses_caller_namespace(self):
        """G4：M9 读侧（评价投影 journal 键控 + M5 graph_for）必须用调用者
        的 student_id，不得静默回退游客命名空间。"""
        from app.agents.learning_orchestration.manager import (
            get_orchestration_service)
        from app.agents.knowledge.manager import KnowledgeService
        from app.agents.student_model.evaluation import store as ev_store

        svc = get_orchestration_service()
        seen_journal: list[str] = []
        seen_graph: list[str] = []
        real_journal = ev_store.get_journal

        def spy_journal(sid):
            seen_journal.append(sid)
            return real_journal(sid)

        real_graph = KnowledgeService.graph_for

        def spy_graph(self, student_id=""):
            seen_graph.append(student_id)
            return real_graph(self, student_id)

        with mock.patch.object(ev_store, "get_journal", spy_journal), \
                mock.patch.object(KnowledgeService, "graph_for", spy_graph):
            svc._evaluation_view_safe("usr_namespace_probe")
            svc._concept_names_safe("数学", student_id="usr_namespace_probe2")
            svc._prereq_map_safe("usr_namespace_probe3")
        self.assertEqual(seen_journal, ["usr_namespace_probe"])
        self.assertEqual(
            seen_graph, ["usr_namespace_probe2", "usr_namespace_probe3"])


if __name__ == "__main__":
    unittest.main()
