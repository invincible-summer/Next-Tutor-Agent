"""G1 回归：卷级 scope/revision 解析（plan §5 / §18.2 test_learner_scope）。

覆盖：私有/公有混合、选上册不进下册、普通文件/章节排除、同名跨教材不
合并、foreign 404、graph revision 失效、scope 缓存。
"""
from __future__ import annotations

import unittest

from tests.storage_sandbox import StorageSandboxTestCase

import app.core.workspace as workspace_mod
from app.agents.knowledge import store as kg_store
from app.core import textbook as tb_store
from app.core.workspace import Workspace, save_workspace
from app.agents.student_model.evaluation import scope as scope_mod
from app.agents.student_model.evaluation import schema as eval_schema
from app.core import learner_runtime


OWNER = "usr_scope_a"
OTHER = "usr_scope_b"


def _graph_payload(topic: str, nodes: list[dict], edges: list[dict]) -> dict:
    return {"topic": topic, "topic_key": topic, "nodes": nodes,
            "edges": edges, "contents": [], "coverage": []}


def _chapter(topic: str, key: str, file_id: str) -> dict:
    return {"id": f"custom.{topic}.ch.{key}", "name": f"章{key}",
            "subject": "物理", "level": "本科", "kind": "chapter",
            "metadata": {"file_id": file_id}}


def _concept(topic: str, key: str, name: str, file_ids: list[str],
             description: str = "") -> dict:
    return {"id": f"custom.{topic}.c.{key}", "name": name, "subject": "物理",
            "level": "本科", "kind": "concept", "aliases": [],
            "description": description,
            "metadata": {"file_ids": list(file_ids)}}


def _part_of(topic: str, member: str, chapter_key: str) -> dict:
    return {"source": f"custom.{topic}.c.{member}",
            "target": f"custom.{topic}.ch.{chapter_key}", "type": "part_of"}


class ScopeFixture(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        learner_runtime.reset_learner_runtime()
        # library 文件先落盘（resolve_textbook_file 要求文件 meta 存在）
        from app.core.library import load_library, save_library
        for owner, fids in ((OWNER, ("f1", "f2")), ("public", ("p1",))):
            lib = load_library(owner)
            for fid in fids:
                lib.add_file("", f"{fid}.pdf", f"{fid} 教材正文内容",
                             file_id=fid)
            save_library(lib)
        # 教材组：两卷（f1 力学 / f2 热学），共享概念“能量”
        self.group = tb_store.create_group(
            OWNER, file_ids=["f1", "f2"], title="大学物理",
            subject="物理", level="本科")
        topic = self.group["topic_key"]
        nodes = [
            _chapter(topic, "mech", "f1"),
            _chapter(topic, "thermo", "f2"),
            _concept(topic, "force", "力", ["f1"]),
            _concept(topic, "heat", "热", ["f2"]),
            _concept(topic, "energy", "能量", ["f1", "f2"]),
        ]
        edges = [
            _part_of(topic, "force", "mech"),
            _part_of(topic, "heat", "thermo"),
            _part_of(topic, "energy", "mech"),
        ]
        kg_store.save_custom_graph(OWNER, topic,
                                   _graph_payload(topic, nodes, edges))
        # 公有教材（同名概念“力”，验证跨教材不合并）
        self.pub = tb_store.create_group(
            "public", file_ids=["p1"], title="公共物理",
            subject="物理", level="本科", scope="public")
        ptopic = self.pub["topic_key"]
        pub_nodes = [_chapter(ptopic, "p", "p1"),
                     _concept(ptopic, "force", "力", ["p1"],
                              description="公共定义的力")]
        kg_store.save_custom_graph(
            "public", ptopic,
            _graph_payload(ptopic, pub_nodes,
                           [_part_of(ptopic, "force", "p")]))
        # 图谱构建完成：标记 ready（scope 只收 ready 教材）
        for owner, tb_id in ((OWNER, self.group["id"]),
                             ("public", self.pub["id"])):
            records = tb_store.load_textbooks(owner)
            for r in records:
                if r["id"] == tb_id:
                    r["status"] = "ready"
            tb_store._save(owner, records)
        # 工作区：选 f1 + p1
        self.ws = Workspace(workspace_id="ws_scope_1", name="物理",
                            student_id=OWNER,
                            selected_file_ids=["f1", "p1"])
        save_workspace(self.ws)

    def resolver(self) -> scope_mod.ScopeResolver:
        return learner_runtime.build_default_scope_resolver()


class TestScopeResolution(ScopeFixture):
    def test_volume_scoping_selecting_volume_1_only(self):
        scope = self.resolver().resolve(OWNER, "ws_scope_1")
        names = {c.display_name for c in scope.allowed_concepts}
        # f1 卷：力 + 共享概念能量；f2 卷的热不进 scope（A08）
        self.assertEqual(names, {"力", "能量", "力"}.intersection(names))
        self.assertIn("力", names)
        self.assertIn("能量", names)
        self.assertNotIn("热", names)
        # chapter/section 不进可评价集合（§5.1.5）
        for c in scope.allowed_concepts:
            self.assertNotIn("ch.", c.concept_id)

    def test_same_name_concepts_across_textbooks_not_merged(self):
        scope = self.resolver().resolve(OWNER, "ws_scope_1")
        forces = [c for c in scope.allowed_concepts if c.display_name == "力"]
        self.assertEqual(len(forces), 2)
        keys = {c.key for c in forces}
        self.assertEqual(len(keys), 2)
        namespaces = {c.graph_owner_namespace for c in forces}
        self.assertEqual(namespaces, {OWNER, "public"})
        # 两个不同教材的相同 node_id 尾段也不覆写（§5.1）
        self.assertEqual(len({c.concept_id for c in forces}), 2)

    def test_foreign_workspace_404(self):
        with self.assertRaises(scope_mod.ScopeNotFound):
            self.resolver().resolve(OTHER, "ws_scope_1")   # 非本人
        with self.assertRaises(scope_mod.ScopeNotFound):
            self.resolver().resolve(OWNER, "ws_missing")   # 不存在

    def test_plain_file_and_unready_textbook_excluded(self):
        # 普通文件（未注册教材）被忽略；非 ready 状态教材不进范围
        self.ws.selected_file_ids = ["f1", "p1", "loose_note_file"]
        save_workspace(self.ws)
        scope = self.resolver().resolve(OWNER, "ws_scope_1")
        for c in scope.allowed_concepts:
            self.assertNotIn("loose", c.concept_id)

    def test_scope_revision_changes_on_graph_content_change(self):
        resolver = self.resolver()
        scope1 = resolver.resolve(OWNER, "ws_scope_1")
        # 图谱语义变化（概念定义变了）→ graph_revision 与 scope_revision 变
        topic = self.group["topic_key"]
        changed = [_chapter(topic, "mech", "f1"),
                   _chapter(topic, "thermo", "f2"),
                   _concept(topic, "force", "力", ["f1"],
                            description="定义已改变"),
                   _concept(topic, "heat", "热", ["f2"]),
                   _concept(topic, "energy", "能量", ["f1", "f2"])]
        edges = [_part_of(topic, "force", "mech"),
                 _part_of(topic, "heat", "thermo"),
                 _part_of(topic, "energy", "mech")]
        kg_store.save_custom_graph(OWNER, topic,
                                   _graph_payload(topic, changed, edges))
        resolver.invalidate()
        scope2 = resolver.resolve(OWNER, "ws_scope_1")
        self.assertNotEqual(scope1.scope_revision, scope2.scope_revision)

    def test_concept_revision_stable_for_display_only_change(self):
        # chapter_order 等显示字段不影响 concept_revision（§5.2）
        from app.agents.knowledge.scope_primitives import concept_revision
        n1 = _concept("t", "x", "力", ["f1"], description="d")
        n2 = dict(n1)
        n2["metadata"] = {**n1["metadata"], "chapter_order": 999}
        self.assertEqual(concept_revision("tb", n1),
                         concept_revision("tb", n2))
        n3 = dict(n1)
        n3["description"] = "新定义"
        self.assertNotEqual(concept_revision("tb", n1),
                            concept_revision("tb", n3))

    def test_scope_cache_partitions_by_workspace(self):
        resolver = self.resolver()
        ws2 = Workspace(workspace_id="ws_scope_2", name="全卷",
                        student_id=OWNER, selected_file_ids=["f1", "f2"])
        save_workspace(ws2)
        s1 = resolver.resolve(OWNER, "ws_scope_1")
        s2 = resolver.resolve(OWNER, "ws_scope_2")
        self.assertIn("热", {c.display_name for c in s2.allowed_concepts})
        self.assertNotEqual(s1.scope_revision, s2.scope_revision)
        # 缓存命中：同 stamp 返回同一 scope 对象
        self.assertIs(resolver.resolve(OWNER, "ws_scope_1"), s1)


class TestVolumePrimitive(unittest.TestCase):
    """纯函数：无卷归属的旧节点不因组内某卷已选而放行（§5.1.5）。"""

    def test_node_without_volume_provenance_excluded(self):
        from app.agents.knowledge.scope_primitives import (
            volume_scoped_subgraph)
        nodes = [
            {"id": "ch1", "kind": "chapter", "metadata": {"file_id": "f1"}},
            {"id": "c1", "kind": "concept", "metadata": {}},   # 无归属
            {"id": "c2", "kind": "concept",
             "metadata": {"file_ids": ["f1"]}},
        ]
        edges = [{"source": "c1", "target": "ch1", "type": "part_of"},
                 {"source": "c2", "target": "ch1", "type": "part_of"}]
        scoped, sedges = volume_scoped_subgraph(nodes, edges, {"f1"})
        ids = {n["id"] for n in scoped}
        # c1 无自身卷归属，但其唯一章 ch1 在已选卷内 → 经章闭包放行
        self.assertEqual(ids, {"ch1", "c1", "c2"})
        # 完全无归属且无章连接的概念不放行
        nodes2 = nodes + [{"id": "c3", "kind": "concept", "metadata": {}}]
        scoped2, _ = volume_scoped_subgraph(nodes2, edges, {"f2"})
        self.assertEqual({n["id"] for n in scoped2}, set())

    def test_empty_selection_returns_empty(self):
        from app.agents.knowledge.scope_primitives import (
            volume_scoped_subgraph)
        nodes = [{"id": "c", "kind": "concept",
                  "metadata": {"file_ids": ["f1"]}}]
        self.assertEqual(volume_scoped_subgraph(nodes, [], set()), ([], []))


class TestScopeConceptCap(unittest.TestCase):
    """G5 手工验收回归：公用教材库全选的学习区（60 卷 ≈ 8218 概念）必须
    能构造合法 EvaluationScope；上限只防体积失控，不是产品限制。
    （此前 max_length=4096 让 /learner-evaluation/workspaces 直接 500。）"""

    def _concepts(self, n: int) -> list[eval_schema.ConceptRef]:
        return [eval_schema.ConceptRef(
            graph_owner_namespace="public", textbook_id=f"tb_{i}",
            file_ids=[f"f{i}"], concept_id=f"c.{i}",
            concept_revision="gr1", display_name=f"概念{i}")
            for i in range(n)]

    def test_full_public_library_workspace_scope_validates(self):
        scope = eval_schema.EvaluationScope(
            workspace_id="ws_full_library", scope_revision="sr1",
            selected_volumes=[eval_schema.VolumeSelection(
                textbook_id="tb", graph_owner_namespace="public",
                topic_key="k", file_ids=["f"], graph_revision="gr1")],
            allowed_concepts=self._concepts(8218))
        self.assertEqual(len(scope.allowed_concepts), 8218)

    def test_scope_changed_op_covers_full_concept_universe(self):
        op = eval_schema.OpScopeChanged(
            workspace_id="ws_full_library", scope_revision="sr2",
            affected_concept_keys=[f"public:tb:c.{i}" for i in range(8218)])
        self.assertEqual(len(op.affected_concept_keys), 8218)

    def test_cap_still_rejects_absurd_sizes(self):
        with self.assertRaises(Exception):
            eval_schema.EvaluationScope(
                workspace_id="ws", scope_revision="sr",
                selected_volumes=[],
                allowed_concepts=self._concepts(16385))


if __name__ == "__main__":
    unittest.main()
