"""R04（update_plan §4）：知识图谱/详情的完整概念键与工作区隔离。

- 带 workspace 的 graph 结构强制限制在选卷 scope；显式请求未选教材 → 404。
- 不存在/越权 workspace → 404，不退化为空 overlay 展示越权内容。
- 评价只落在概念节点（完整键命中）；chapter/section 只显示覆盖计数。
- 浏览模式（无 workspace）没有个人 overlay。
- 详情按完整身份命中；graph 与 /learner-evaluation 的 judgment_id 一致。
"""
from __future__ import annotations

import unittest

from fastapi import HTTPException

from tests.storage_sandbox import StorageSandboxTestCase

from app.api.v1 import knowledge as knowledge_api
from app.agents.knowledge import store as kg_store
from app.agents.student_model.evaluation import schema as S
from app.agents.student_model.evaluation.store import get_journal
from app.core import learner_runtime
from app.core import textbook as tb_store
from app.core.workspace import Workspace, save_workspace

SID = "usr_kgraph_a"
OTHER = "usr_kgraph_b"
WS = "ws_kgraph"


def _chapter(topic: str, key: str, file_id: str) -> dict:
    return {"id": f"custom.{topic}.ch.{key}", "name": f"章{key}",
            "subject": "物理", "level": "本科", "kind": "chapter",
            "metadata": {"file_id": file_id}}


def _concept(topic: str, key: str, name: str, file_ids: list[str]) -> dict:
    return {"id": f"custom.{topic}.c.{key}", "name": name, "subject": "物理",
            "level": "本科", "kind": "concept", "aliases": [],
            "metadata": {"file_ids": list(file_ids)}}


def _part_of(topic: str, member: str, chapter_key: str) -> dict:
    return {"source": f"custom.{topic}.c.{member}",
            "target": f"custom.{topic}.ch.{chapter_key}", "type": "part_of"}


def _payload(topic: str, nodes: list, edges: list) -> dict:
    return {"topic": topic, "topic_key": topic, "nodes": nodes,
            "edges": edges, "contents": [], "coverage": []}


class KnowledgeScopeFixture(StorageSandboxTestCase):
    def setUp(self) -> None:
        super().setUp()
        learner_runtime.reset_learner_runtime()
        from unittest.mock import patch
        self._enabled = patch.object(knowledge_api._kn, "is_enabled",
                                     return_value=True)
        self._enabled.start()
        self.addCleanup(self._enabled.stop)   # 不停止会泄漏到后续模块
        # 自有教材：f1 力学卷（力+能量） / f2 热学卷（热）
        from app.core.library import load_library, save_library
        lib = load_library(SID)
        for fid in ("f1", "f2", "f3"):
            lib.add_file("", f"{fid}.pdf", f"{fid} 教材正文", file_id=fid)
        save_library(lib)
        self.group = tb_store.create_group(
            SID, file_ids=["f1", "f2"], title="大学物理",
            subject="物理", level="本科")
        topic = self.group["topic_key"]
        nodes = [_chapter(topic, "mech", "f1"), _chapter(topic, "thermo", "f2"),
                 _concept(topic, "force", "力", ["f1"]),
                 _concept(topic, "heat", "热", ["f2"]),
                 _concept(topic, "energy", "能量", ["f1", "f2"])]
        edges = [_part_of(topic, "force", "mech"),
                 _part_of(topic, "heat", "thermo"),
                 _part_of(topic, "energy", "mech")]
        kg_store.save_custom_graph(SID, topic, _payload(topic, nodes, edges))
        records = tb_store.load_textbooks(SID)
        for r in records:
            if r["id"] == self.group["id"]:
                r["status"] = "ready"
        # 第二本教材（f3 化学卷）：在库但未被工作区选用
        self.group2 = tb_store.create_group(
            SID, file_ids=["f3"], title="无机化学",
            subject="化学", level="本科")
        topic2 = self.group2["topic_key"]
        kg_store.save_custom_graph(SID, topic2, _payload(
            topic2,
            [_chapter(topic2, "chem", "f3"),
             _concept(topic2, "bond", "化学键", ["f3"])],
            [_part_of(topic2, "bond", "chem")]))
        for r in records:
            if r["id"] == self.group2["id"]:
                r["status"] = "ready"
        tb_store._save(SID, records)
        # 工作区只选 f1
        save_workspace(Workspace(workspace_id=WS, name="力学区",
                                 student_id=SID, selected_file_ids=["f1"]))
        self.topic = topic
        # 在 f1 卷上物化一个 supported 判断（能量）
        from app.agents.student_model.evaluation import schema as eval_schema
        concept = eval_schema.ConceptRef(
            graph_owner_namespace=SID, textbook_id=self.group["id"],
            file_ids=["f1"], concept_id=f"custom.{topic}.c.energy",
            concept_revision=self._concept_revision(topic, "energy"),
            display_name="能量")
        self.judgment_id = "jdg_energy_1"
        receipt = S.SourceReceipt(
            source_id="src_energy", source_revision=1,
            kind=S.SourceKind.DIALOGUE, observed_at=S.utc_now_iso(),
            workspace_id_at_observation=WS,
            canonical_text="我能解释能量守恒", scope_revision="sr_k")
        interp = S.LearnerInterpretation(
            applicable=True, observation_claims=[
                S.ObservationClaim(
                    local_id="ob1",
                    concept_ref="c1", statement="能解释能量守恒",
                    stance=S.ClaimStance.SUPPORTS, opportunity_ref="t1")],
            concept_updates=[])
        judgment = S.ConceptJudgment(
            judgment_id=self.judgment_id, concept_ref=concept,
            workspace_id=WS, state=S.ConceptEvalState.SUPPORTED_IN_SCOPE,
            statement="在这些条件下已有支持", claims=[],
            evidence_watermark="gen:1", policy_version=S.POLICY_VERSION,
            theory_version=S.THEORY_VERSION, prompt_ref="p",
            created_at=S.utc_now_iso(), source_id="src_energy",
            scope_revision="sr_k")
        get_journal(SID).append([
            S.OpSourceRegistered(source=receipt),
            S.OpResultCommitted(
                job_id="job_energy", source_id="src_energy",
                source_revision=1, scope_revision="sr_k",
                interpretation_id="itp_energy", interpretation=interp,
                judgments=[judgment], abstained=False)])

    def _concept_revision(self, topic: str, key: str) -> str:
        from app.agents.knowledge.scope_primitives import concept_revision
        payload = kg_store.load_custom_graph(SID, self.topic)
        node = next(n for n in payload["nodes"]
                    if n["id"] == f"custom.{self.topic}.c.{key}")
        return concept_revision(self.group["id"], node)

    def graph(self, **kw):
        defaults = dict(textbook_id="", file_id="", level="", subject="",
                        view="full", chapter_id="", q="", workspace_id="",
                        student_id=SID)
        defaults.update(kw)
        return knowledge_api.knowledge_graph(**defaults)


class TestWorkspaceGraphIsolation(KnowledgeScopeFixture):
    def test_unknown_or_foreign_workspace_is_404(self):
        with self.assertRaises(HTTPException) as cm:
            self.graph(workspace_id="ws_missing")
        self.assertEqual(cm.exception.status_code, 404)
        with self.assertRaises(HTTPException) as cm:
            self.graph(workspace_id=WS, student_id=OTHER)
        self.assertEqual(cm.exception.status_code, 404)

    def test_unselected_textbook_is_404_not_silent_content(self):
        # 显式请求教材本身在库，但不在该工作区选卷内 → 404
        with self.assertRaises(HTTPException) as cm:
            self.graph(textbook_id=self.group2["id"], workspace_id=WS)
        self.assertEqual(cm.exception.status_code, 404)
        # 已选教材在 workspace 视图中可见（对照组）
        out = self.graph(textbook_id=self.group["id"], workspace_id=WS)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["nodes"])

    def test_workspace_merged_view_restricted_to_selected_volumes(self):
        out = self.graph(workspace_id=WS)
        self.assertEqual(out["status"], "ok")
        names = {n["name"] for n in out["nodes"]}
        # 只选 f1：力学章 + 力 + 能量；f2 的热学章/热不得出现
        self.assertIn("力", names)
        self.assertIn("能量", names)
        self.assertNotIn("热", names)
        self.assertNotIn("章thermo", names)

    def test_evaluation_full_key_match_and_container_counts(self):
        out = self.graph(workspace_id=WS)
        by_name = {n["name"]: n for n in out["nodes"]}
        energy = by_name["能量"]
        self.assertEqual(energy["evaluation"]["judgment_id"],
                         self.judgment_id)
        self.assertEqual(energy["evaluation"]["state"], "supported_in_scope")
        # 未评价概念：state=not_observed（不是无键）
        force = by_name["力"]
        self.assertEqual(force["evaluation"]["state"], "not_observed")
        # chapter 只有覆盖计数，不携带个人评价
        mech = by_name["章mech"]
        self.assertIsNone(mech["evaluation"])
        self.assertEqual(mech["evaluation_coverage"],
                         {"evidenced": 1, "total": 2})

    def test_browse_mode_has_no_personal_overlay(self):
        out = self.graph()      # 无 workspace：浏览模式
        self.assertEqual(out["status"], "ok")
        for n in out["nodes"]:
            if "custom." in str(n.get("id") or ""):
                self.assertIsNone(n.get("evaluation"))

    def test_workspace_textbook_view_also_restricted(self):
        # 选 f2 的新工作区：能量判断属于旧区，不得泄漏
        save_workspace(Workspace(workspace_id="ws_k2", name="热学区",
                                 student_id=SID, selected_file_ids=["f2"]))
        out = self.graph(workspace_id="ws_k2")
        names = {n["name"] for n in out["nodes"]}
        self.assertIn("热", names)
        self.assertIn("能量", names)
        by_name = {n["name"]: n for n in out["nodes"]}
        # 同一概念在另一工作区：没有 judgment（隔离合同）
        self.assertNotEqual(by_name["能量"]["evaluation"]["judgment_id"],
                            self.judgment_id)


class TestConceptDetailIdentity(KnowledgeScopeFixture):
    def detail(self, concept_id: str, **kw):
        defaults = dict(workspace_id="", student_id=SID)
        defaults.update(kw)
        return knowledge_api.knowledge_concept(concept_id, **defaults)

    def test_workspace_detail_matches_by_identity(self):
        out = self.detail(f"custom.{self.topic}.c.energy", workspace_id=WS)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["evaluation"]["judgment_id"], self.judgment_id)

    def test_workspace_detail_out_of_scope_is_404(self):
        with self.assertRaises(HTTPException) as cm:
            self.detail(f"custom.{self.topic}.c.heat", workspace_id=WS)
        self.assertEqual(cm.exception.status_code, 404)

    def test_workspace_detail_unknown_workspace_is_404(self):
        with self.assertRaises(HTTPException) as cm:
            self.detail(f"custom.{self.topic}.c.energy",
                        workspace_id="ws_missing")
        self.assertEqual(cm.exception.status_code, 404)

    def test_browse_detail_has_no_evaluation(self):
        out = self.detail(f"custom.{self.topic}.c.energy")
        self.assertEqual(out["status"], "ok")
        self.assertIsNone(out["evaluation"])


if __name__ == "__main__":
    unittest.main()
