"""Unit tests for the Knowledge Intelligence module (M5).

Covers: schema round-trips, the DAG invariant (PREREQUISITE cycle rejection),
traversal primitives (prerequisites_of / descendants_of / neighbors /
neighborhood), fuzzy + BM25 concept matching, the ConceptRetriever (BM25 +
KG-traversal fusion), the KnowledgeService facade, the env switch, the
Dependency Reasoner (M5.5), and the learned-edge store.

P6-A2：考纲 seed 包已删除（知识只来自教材）。原依赖 seed 内容的用例改用
内联小图谱 `_mini_seed_graph()`（结构与_legacy 微积分/运动学一致），
seed-pack 聚合/校验器/超集不变量等测试随包一并移除。
All pure functions, no LLM (reasoner uses a fake).
"""
import os
import unittest

from app.agents.knowledge import (ConceptRetriever, EdgeType, KnowledgeContent,
                                  KnowledgeContext, KnowledgeEdge,
                                  KnowledgeGraph, KnowledgeNode,
                                  KnowledgeService, is_enabled)
from app.agents.knowledge.seed import seed_skill_prereqs
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402


def _toy_graph() -> KnowledgeGraph:
    """A -> B -> C chain (PREREQUISITE) + B -RELATED-> D."""
    return KnowledgeGraph(
        nodes=[
            {"id": "a", "name": "alpha", "subject": "x", "difficulty": 1,
             "aliases": ["al"], "common_errors": ["err-a"]},
            {"id": "b", "name": "beta", "subject": "x", "difficulty": 2},
            {"id": "c", "name": "gamma", "subject": "x", "difficulty": 3},
            {"id": "d", "name": "delta", "subject": "x", "difficulty": 2},
        ],
        edges=[
            {"source": "a", "target": "b", "type": "prerequisite"},
            {"source": "b", "target": "c", "type": "prerequisite"},
            {"source": "b", "target": "d", "type": "related"},
        ],
    )


def _mini_seed_graph() -> KnowledgeGraph:
    """内联小图谱：P6-A2 后考纲 seed 已删，用它替代原 seed 内容做算法验证。"""
    return KnowledgeGraph(
        nodes=[
            {"id": "math.function.definition", "name": "函数", "subject": "数学",
             "level": "高中", "difficulty": 1},
            {"id": "math.function.monotonicity", "name": "单调性", "subject": "数学",
             "level": "高中", "difficulty": 2},
            {"id": "math.calculus.limit", "name": "极限", "subject": "数学",
             "level": "高中", "difficulty": 3, "aliases": ["极限思想"]},
            {"id": "math.calculus.derivative", "name": "导数", "subject": "数学",
             "level": "高中", "difficulty": 4, "aliases": ["微商"],
             "common_errors": ["与积分混淆"]},
            {"id": "math.calculus.integral", "name": "积分", "subject": "数学",
             "level": "高中", "difficulty": 4},
            {"id": "physics.kinematics.velocity", "name": "速度", "subject": "物理",
             "level": "高中", "difficulty": 2},
            {"id": "chemistry.atom", "name": "原子", "subject": "化学",
             "level": "高中", "difficulty": 2},
        ],
        edges=[
            {"source": "math.function.definition",
             "target": "math.function.monotonicity", "type": "prerequisite"},
            {"source": "math.function.monotonicity",
             "target": "math.calculus.limit", "type": "prerequisite"},
            {"source": "math.calculus.limit",
             "target": "math.calculus.derivative", "type": "prerequisite"},
            {"source": "math.calculus.derivative",
             "target": "math.calculus.integral", "type": "prerequisite"},
            {"source": "physics.kinematics.velocity",
             "target": "math.calculus.derivative", "type": "application"},
        ],
        contents=[{"concept_id": "math.calculus.derivative",
                   "definition": "瞬时变化率", "formula": "", "example": "",
                   "exercise_hint": "", "source": "seed"}],
    )


def _mini_service() -> KnowledgeService:
    """KnowledgeService with the mini graph injected (seed is empty post-P6)."""
    ks = KnowledgeService()
    ks._graph = _mini_seed_graph()
    ks._retriever = ConceptRetriever(ks._graph)
    return ks


class TestSchema(unittest.TestCase):
    def test_node_roundtrip(self):
        n = KnowledgeNode(id="x.y", name="nm", subject="s", level="高中",
                          difficulty=4, description="d", aliases=["a"],
                          common_errors=["e"])
        n2 = KnowledgeNode.from_dict(n.to_dict())
        self.assertEqual(n2.id, "x.y")
        self.assertEqual(n2.aliases, ["a"])
        self.assertEqual(n2.difficulty, 4)
        self.assertIn("nm", n.search_text())
        self.assertIn("d", n.search_text())

    def test_edge_roundtrip_and_edgetype(self):
        e = KnowledgeEdge(source="a", target="b", type=EdgeType.PREREQUISITE,
                          weight=0.7, provenance="reasoner")
        d = e.to_dict()
        self.assertEqual(d["type"], "prerequisite")
        self.assertEqual(d["weight"], 0.7)
        e2 = KnowledgeEdge.from_dict(d)
        self.assertEqual(e2.type, EdgeType.PREREQUISITE)
        self.assertEqual(EdgeType.from_value("application"), EdgeType.APPLICATION)
        self.assertEqual(EdgeType.from_value("nonsense"), EdgeType.RELATED)
        self.assertEqual(EdgeType.from_value(None), EdgeType.RELATED)

    def test_content_roundtrip_and_has_any(self):
        c = KnowledgeContent(concept_id="x", definition="d", formula="f")
        self.assertTrue(c.has_any)
        c2 = KnowledgeContent.from_dict(c.to_dict())
        self.assertEqual(c2.definition, "d")
        self.assertFalse(KnowledgeContent(concept_id="x").has_any)

    def test_context_roundtrip(self):
        ctx = KnowledgeContext(concept="导数", node_id="m.deriv",
                               prerequisite_chain=["a", "b"], confidence=0.5)
        d = ctx.to_dict()
        self.assertEqual(d["prerequisite_chain"], ["a", "b"])
        self.assertEqual(d["confidence"], 0.5)
        ctx2 = KnowledgeContext.from_dict(d)
        self.assertEqual(ctx2.concept, "导数")
        self.assertEqual(ctx2.confidence, 0.5)


class TestGraphDAG(unittest.TestCase):
    def test_dedupe_and_unknown_endpoints(self):
        g = _toy_graph()
        self.assertFalse(g.add_edge(KnowledgeEdge("a", "b", EdgeType.PREREQUISITE)))
        self.assertFalse(g.add_edge(KnowledgeEdge("a", "zzz", EdgeType.RELATED)))
        self.assertFalse(g.add_edge(KnowledgeEdge("a", "a", EdgeType.RELATED)))

    def test_prerequisite_cycle_rejected(self):
        g = _toy_graph()
        self.assertFalse(g.add_edge(KnowledgeEdge("c", "a", EdgeType.PREREQUISITE)))
        self.assertTrue(g.add_edge(KnowledgeEdge("c", "a", EdgeType.RELATED)))

    def test_seed_is_empty_post_p6(self):
        """P6-A2：考纲 seed 已删，seed_nodes/edges 为空（图谱只来自教材）。"""
        from app.agents.knowledge.seed import seed_edges, seed_nodes
        self.assertEqual(seed_nodes(), [])
        self.assertEqual(seed_edges(), [])
        self.assertEqual(seed_skill_prereqs(), set())


class TestTraversal(unittest.TestCase):
    def test_prerequisites_transitive_root_first(self):
        g = _toy_graph()
        self.assertEqual(g.prerequisites_of("c"), ["a", "b"])
        self.assertEqual(g.prerequisites_of("b"), ["a"])
        self.assertEqual(g.prerequisites_of("a"), [])

    def test_descendants(self):
        g = _toy_graph()
        self.assertEqual(g.descendants_of("a"), ["b", "c"])
        self.assertEqual(g.descendants_of("b"), ["c"])
        self.assertEqual(g.descendants_of("d"), [])

    def test_neighbors_excludes_prereq_and_caps(self):
        g = _toy_graph()
        self.assertEqual(g.neighbors("b"), ["d"])
        self.assertEqual(g.neighbors("d"), ["b"])
        self.assertEqual(g.neighbors("a"), [])

    def test_neighborhood_bfs_depth_limit(self):
        g = _toy_graph()
        self.assertEqual(g.neighborhood("b", depth=1), ["d"])
        self.assertEqual(g.neighborhood("zzz", depth=2), [])

    def test_neighborhood_application_edge(self):
        g = _mini_seed_graph()
        self.assertIn("math.calculus.derivative",
                      g.neighborhood("physics.kinematics.velocity", depth=1))


class TestMatch(unittest.TestCase):
    def test_exact_name_and_alias(self):
        g = _toy_graph()
        self.assertEqual(g.match_concept("beta").id, "b")
        self.assertEqual(g.match_concept("al").id, "a")

    def test_substring_and_token_and_miss(self):
        g = _mini_seed_graph()
        self.assertEqual(g.match_concept("导数").id, "math.calculus.derivative")
        self.assertEqual(g.match_concept("微商").id, "math.calculus.derivative")
        self.assertIsNone(g.match_concept("量子纠缠"))
        self.assertIsNone(g.match_concept(""))


class TestRetriever(unittest.TestCase):
    def test_retrieve_primary_concept(self):
        ks = _mini_service()
        r = ks.retrieve("导数", top_k=3)
        self.assertGreater(len(r), 0)
        self.assertEqual(r[0]["concept_id"], "math.calculus.derivative")
        self.assertGreater(r[0]["score"], 0)
        self.assertEqual(r[0]["source"], "bm25")

    def test_retrieve_alias_match(self):
        ks = _mini_service()
        r = ks.retrieve("微商")
        self.assertTrue(any(x["concept_id"] == "math.calculus.derivative" for x in r))

    def test_retrieve_traversal_expansion(self):
        # velocity -> derivative via APPLICATION should be surfaced
        ks = _mini_service()
        r = ks.retrieve("速度与加速度", top_k=5, traverse_depth=1)
        ids = {x["concept_id"] for x in r}
        self.assertIn("physics.kinematics.velocity", ids)
        self.assertIn("math.calculus.derivative", ids)

    def test_retriever_on_toy_graph(self):
        g = _toy_graph()
        ret = ConceptRetriever(g)
        r = ret.retrieve("beta", top_k=3, traverse_depth=1)
        self.assertEqual(r[0]["concept_id"], "b")
        ids = {x["concept_id"] for x in r}
        self.assertIn("d", ids)  # traversal from b via RELATED

    def test_confidence_for(self):
        ks = _mini_service()
        self.assertGreater(ks.confidence_for("导数"), 0)
        self.assertEqual(ks.confidence_for(""), 0.0)


class TestKnowledgeService(unittest.TestCase):
    def test_graph_empty_post_p6(self):
        """P6-A2：无 seed、无教材图谱时主图为空（知识只来自教材）。"""
        ks = KnowledgeService()
        self.assertEqual(len(ks.graph.nodes), 0)
        self.assertEqual(len(ks.graph.edges), 0)

    def test_build_context_missing_prereqs(self):
        ks = _mini_service()
        ctx = ks.build_context(
            concept="积分",
            mastery_view={
                "math.function.definition": {"p_known": 0.9},
                "math.function.monotonicity": {"p_known": 0.85},
                "math.calculus.limit": {"p_known": 0.8},
                "math.calculus.derivative": {"p_known": 0.3},
            })
        self.assertEqual(ctx.node_id, "math.calculus.integral")
        self.assertIn("导数", ctx.prerequisite_chain)
        self.assertEqual(ctx.missing_prereqs, ["导数"])

    def test_build_context_all_mastered(self):
        ks = _mini_service()
        mv = {nid: {"p_known": 0.9} for nid in ks.graph.nodes}
        ctx = ks.build_context(concept="积分", mastery_view=mv)
        self.assertEqual(ctx.missing_prereqs, [])

    def test_build_context_unknown_concept(self):
        ks = _mini_service()
        ctx = ks.build_context(concept="量子纠缠")
        self.assertEqual(ctx.concept, "量子纠缠")
        self.assertEqual(ctx.node_id, "")

    def test_is_enabled_env(self):
        old = os.environ.get("KNOWLEDGE_INTELLIGENCE_MODE")
        try:
            os.environ["KNOWLEDGE_INTELLIGENCE_MODE"] = "1"
            self.assertTrue(is_enabled())
            os.environ["KNOWLEDGE_INTELLIGENCE_MODE"] = "0"
            self.assertFalse(is_enabled())
            os.environ.pop("KNOWLEDGE_INTELLIGENCE_MODE", None)
            self.assertTrue(is_enabled())
        finally:
            if old is not None:
                os.environ["KNOWLEDGE_INTELLIGENCE_MODE"] = old
            else:
                os.environ.pop("KNOWLEDGE_INTELLIGENCE_MODE", None)


class TestContentResolver(unittest.TestCase):
    def test_seed_content_for_derivative(self):
        from app.agents.knowledge import ContentResolver
        g = _mini_seed_graph()
        r = ContentResolver(g.contents)
        content, snippets = r.resolve("math.calculus.derivative")
        self.assertTrue(content.has_any)
        self.assertIn("瞬时变化率", content.definition)
        self.assertEqual(content.source, "seed")
        self.assertEqual(snippets, [])

    def test_material_fallback(self):
        from app.agents.knowledge import ContentResolver
        g = _mini_seed_graph()
        from app.core.retriever import BM25Index, chunk_text

        class FakeStore:
            def __init__(self):
                self.chunks = chunk_text("导数是瞬时变化率", source="t.pdf")
            def has_knowledge(self):
                return True
            def search(self, q, top_k=3):
                idx = BM25Index(self.chunks)
                return [{"source": c.source, "text": c.text, "score": sc}
                        for c, sc in idx.search(q, top_k=top_k)]

        r = ContentResolver(g.contents, FakeStore())
        content, snippets = r.resolve("math.calculus.derivative", query_hint="导数")
        self.assertEqual(len(snippets), 1)
        self.assertEqual(snippets[0]["source"], "t.pdf")

    def test_unknown_concept_empty(self):
        from app.agents.knowledge import ContentResolver
        r = ContentResolver(_mini_seed_graph().contents)
        content, snippets = r.resolve("does.not.exist")
        self.assertFalse(content.has_any)
        self.assertEqual(snippets, [])


class TestContextBuilder(unittest.TestCase):
    def test_build_context_derivative(self):
        from app.agents.knowledge import build_knowledge_context, render_knowledge_directive
        ks = _mini_service()
        ctx = build_knowledge_context(concept="导数", graph=ks.graph, retriever=ks.retriever)
        self.assertEqual(ctx.node_id, "math.calculus.derivative")
        self.assertIn("极限", ctx.prerequisite_chain)
        self.assertTrue(any("积分" in e for e in ctx.common_errors))
        block = render_knowledge_directive(ctx)
        self.assertIn("[知识智能", block)
        self.assertIn("易错点", block)

    def test_render_skips_out_of_ontology(self):
        from app.agents.knowledge import render_knowledge_directive
        ks = _mini_service()
        ctx = ks.build_context(concept="量子纠缠")
        self.assertEqual(render_knowledge_directive(ctx), "")

    def test_build_directive_missing_prereq(self):
        ks = _mini_service()
        block = ks.build_directive(
            concept="积分",
            mastery_view={
                "math.function.definition": {"p_known": 0.9},
                "math.function.monotonicity": {"p_known": 0.85},
                "math.calculus.limit": {"p_known": 0.8},
                "math.calculus.derivative": {"p_known": 0.3},
            })
        self.assertIn("[知识智能·前置补缺]", block)
        self.assertEqual(ks.build_context(concept="积分", mastery_view={
            "math.function.definition": {"p_known": 0.9},
            "math.function.monotonicity": {"p_known": 0.85},
            "math.calculus.limit": {"p_known": 0.8},
            "math.calculus.derivative": {"p_known": 0.3},
        }).missing_prereqs, ["导数"])


class TestBridge(unittest.TestCase):
    def test_skill_node_extras_seeded(self):
        from app.agents.knowledge import skill_node_extras
        ks = _mini_service()
        ex = skill_node_extras(ks.graph, "导数")
        self.assertEqual(ex["node_id"], "math.calculus.derivative")
        self.assertIn("微商", ex["aliases"])
        self.assertIn("math.calculus.limit", ex["prerequisites"])

    def test_skill_node_extras_unknown(self):
        from app.agents.knowledge import skill_node_extras
        ks = _mini_service()
        self.assertEqual(skill_node_extras(ks.graph, "量子纠缠"), {})


import asyncio
import json


class _FakeValidatorLLM:
    """Async LLM stand-in returning a canned verdicts JSON for complete()."""
    def __init__(self, payload):
        self.payload = payload

    async def complete(self, messages, temperature=None, max_tokens=None):
        return self.payload, {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}


class _GraphWithFloatingNode:
    """Helper: mini graph + one new node with no prereqs."""
    @staticmethod
    def build():
        g = _mini_seed_graph()
        g.ensure_node(KnowledgeNode(
            id="math.calculus.derivative_app", name="导数应用题",
            subject="数学", difficulty=4, description="用导数解实际最值问题"))
        return g


class TestReasoner(StorageSandboxTestCase):
    """reason_about 会把 learned edge 持久化到 knowledge/graph.json（生产
    根）——必须沙箱化（历史违规：裸 TestCase 每次跑测都在生产根留一条
    合成边，此前靠字母序最后的 TestStore 收尾 clear 顺带删除而未被察觉）。"""

    def test_candidates_filter_rules(self):
        from app.agents.knowledge import DependencyReasoner
        from app.agents.knowledge.retriever import ConceptRetriever
        g = _GraphWithFloatingNode.build()
        r = DependencyReasoner(g, ConceptRetriever(g))
        node = g.match_concept("导数应用题")
        cands = r._candidates(node, "", 6)
        ids = {c.id for c in cands}
        # self excluded; cross-subject excluded; harder-than-target excluded
        self.assertNotIn("math.calculus.derivative_app", ids)
        self.assertNotIn("chemistry.atom", ids)
        self.assertIn("math.calculus.derivative", ids)  # strong candidate

    def test_validate_writes_prereq_above_threshold(self):
        from app.agents.knowledge import DependencyReasoner, persist_result
        g = _GraphWithFloatingNode.build()
        from app.agents.knowledge.retriever import ConceptRetriever
        r = DependencyReasoner(g, ConceptRetriever(g))
        payload = json.dumps({"verdicts": [
            {"id": "math.calculus.derivative", "relation": "prerequisite",
             "confidence": 0.9, "reason": "应用需先掌握定义"},
            {"id": "math.function.monotonicity", "relation": "none",
             "confidence": 0.1, "reason": "非直接前置"},
        ]}, ensure_ascii=False)
        res = asyncio.run(r.reason_about("导数应用题", grade="高中",
                                         llm=_FakeValidatorLLM(payload)))
        self.assertEqual(len(res.learned_edges), 1)
        self.assertEqual(res.learned_edges[0]["source"], "math.calculus.derivative")
        self.assertEqual(res.learned_edges[0]["weight"], 0.9)
        written = persist_result(res, g)
        self.assertEqual(written, 1)
        self.assertIn("math.calculus.derivative",
                      g.prerequisites_of("math.calculus.derivative_app"))

    def test_below_threshold_rejected(self):
        from app.agents.knowledge import DependencyReasoner
        g = _GraphWithFloatingNode.build()
        from app.agents.knowledge.retriever import ConceptRetriever
        r = DependencyReasoner(g, ConceptRetriever(g))
        # confidence 0.4 -> below 0.65 threshold -> rejected, not written
        payload = json.dumps({"verdicts": [
            {"id": "math.calculus.derivative", "relation": "prerequisite",
             "confidence": 0.4, "reason": "弱依赖"}]}, ensure_ascii=False)
        res = asyncio.run(r.reason_about("导数应用题", grade="高中",
                                         llm=_FakeValidatorLLM(payload)))
        self.assertEqual(res.learned_edges, [])
        # derivative is the candidate we scored below threshold -> rejected
        self.assertTrue(any(x.get("source") == "math.calculus.derivative"
                            for x in res.rejected))

    def test_related_relation_not_written_as_prereq(self):
        from app.agents.knowledge import DependencyReasoner
        g = _GraphWithFloatingNode.build()
        from app.agents.knowledge.retriever import ConceptRetriever
        r = DependencyReasoner(g, ConceptRetriever(g))
        payload = json.dumps({"verdicts": [
            {"id": "math.calculus.derivative", "relation": "related",
             "confidence": 0.95, "reason": "相关"}]}, ensure_ascii=False)
        res = asyncio.run(r.reason_about("导数应用题", grade="高中",
                                         llm=_FakeValidatorLLM(payload)))
        self.assertEqual(res.learned_edges, [])  # related != prerequisite

    def test_dag_safe_write_rejects_cycle(self):
        from app.agents.knowledge import DependencyReasoner
        g = _GraphWithFloatingNode.build()
        from app.agents.knowledge.retriever import ConceptRetriever
        r = DependencyReasoner(g, ConceptRetriever(g))
        _ = r  # candidates pipeline exercised above; real cycle check below
        # first add a valid prereq: derivative -> derivative_app
        from app.agents.knowledge.schema import KnowledgeEdge, EdgeType
        good = KnowledgeEdge("math.calculus.derivative",
                            "math.calculus.derivative_app",
                            EdgeType.PREREQUISITE, provenance="reasoner")
        self.assertTrue(g.add_edge(good))
        # now the reverse (derivative_app -> derivative) would close a cycle
        # since derivative_app can now reach derivative -- must be rejected
        bad = KnowledgeEdge("math.calculus.derivative_app",
                            "math.calculus.derivative", EdgeType.PREREQUISITE,
                            provenance="reasoner")
        self.assertFalse(g.add_edge(bad))

    def test_no_llm_dry_run(self):
        from app.agents.knowledge import DependencyReasoner
        g = _GraphWithFloatingNode.build()
        from app.agents.knowledge.retriever import ConceptRetriever
        r = DependencyReasoner(g, ConceptRetriever(g))
        res = asyncio.run(r.reason_about("导数应用题", grade="高中", llm=None))
        self.assertEqual(res.learned_edges, [])
        self.assertIn("no_llm", [x.get("reason") for x in res.rejected]
                      + ["无 LLM"] * (not res.rejected))

    def test_unknown_concept_no_candidates(self):
        from app.agents.knowledge import DependencyReasoner
        ks = _mini_service()
        r = DependencyReasoner(ks.graph, ks.retriever)
        res = asyncio.run(r.reason_about("量子纠缠xyz", grade="高中",
                                         llm=_FakeValidatorLLM("{}")))
        self.assertEqual(res.learned_edges, [])
        self.assertEqual(res.concept_id, "量子纠缠xyz")


class TestStore(StorageSandboxTestCase):
    """learned-edge 落盘往返：_KG_FILE 是生产 knowledge/ 根上的文件，
    必须经沙箱重定向（历史违规：裸 TestCase 直接在生产根创建/清除
    knowledge/graph.json）。"""

    def test_learned_edges_roundtrip(self):
        from app.agents.knowledge import store as _store
        _store.clear_learned_edges()
        edge = {"source": "a", "target": "b", "type": "prerequisite",
                "weight": 0.8, "provenance": "reasoner"}
        self.assertTrue(_store.append_learned_edge(edge))
        loaded = _store.load_learned_edges()
        self.assertEqual(loaded, [edge])
        # idempotent
        _store.append_learned_edge(edge)
        self.assertEqual(len(_store.load_learned_edges()), 1)
        _store.clear_learned_edges()
        self.assertEqual(_store.load_learned_edges(), [])

    def test_missing_file_returns_empty(self):
        from app.agents.knowledge import store as _store
        _store.clear_learned_edges()
        self.assertEqual(_store.load_learned_edges(), [])



if __name__ == "__main__":
    unittest.main()
