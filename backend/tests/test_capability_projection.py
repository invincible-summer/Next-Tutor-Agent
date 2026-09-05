"""W3 M2 v2 能力投影 + BKT 有效事件重放回归（updatePlan.md §8.5/§8.6.2）。

- 投影从 events+账本读时派生（零新存储根），概念×维度聚合：
  not_observed 默认、developing、needs_recheck（正反证据冲突）、
  demonstrated_in_scope（≥2 不同题族 met）——没有证据不宣称会；
- supersede 的 attempt 从投影与重放中排除；
- rebuild_mastery 按有效事件序列全量重放，精确等于"去掉被撤观测后的
  全新构建"（不能假装减去贝叶斯更新）；
- flag_attempt_disputed 只标记不抹除（保守）；evidence-profile 端点三态
  与身份隔离；/student/mastery 增 source/estimate_kind 兼容键。

全部用例继承 StorageSandboxTestCase。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402

from app.agents.student_model import (  # noqa: E402
    get_student_model, project_capabilities, rebuild_mastery,
    record_quiz_result)
from app.agents.student_model.mastery import Mastery  # noqa: E402
from app.core.learning_records import (  # noqa: E402
    flag_attempt_disputed, list_records, record_verdict)
from app.identity import store as id_store  # noqa: E402
from app.identity.models import UserProfile  # noqa: E402
from app.identity.security import create_token, hash_password  # noqa: E402
from app.main import create_app  # noqa: E402


def _structured(dims: dict, rubric: str):
    return {"observed_capabilities": dims, "rubric_id": rubric,
            "criterion_results": [{"criterion_id": "c1", "result": "met"}]}


def _graded(sid: str, *, verdict="correct", attempt="", rubric="q_a",
           dims=None, skill="math.prob.conditional", concept="条件概率"):
    record_quiz_result(concept=concept, correct=(verdict == "correct"),
                       skill_id=skill, knowledge_point=concept,
                       verdict=verdict, attempt_id=attempt,
                       structured=_structured(dims or {"concept": "met"},
                                              rubric) if dims is not False
                       else None,
                       student_id=sid)


class TestProjectionAggregation(StorageSandboxTestCase):

    def test_dimension_status_ladder(self):
        sid = "st_proj_ladder"
        # 1 个题族 met → developing（不宣称会）
        _graded(sid, attempt="att_1", rubric="q_a",
                dims={"concept": "met"})
        proj = project_capabilities(sid)
        entry = proj["concepts"][0]
        self.assertEqual(entry["dimensions"]["concept"]["status"], "developing")
        self.assertNotIn("concept", entry["not_observed"])
        # 第二个题族 met → demonstrated_in_scope
        _graded(sid, attempt="att_2", rubric="q_b",
                dims={"concept": "met"})
        proj = project_capabilities(sid)
        self.assertEqual(proj["concepts"][0]["dimensions"]["concept"]["status"],
                         "demonstrated_in_scope")
        # 未观测维度默认 not_observed 且列入 unknowns
        entry = proj["concepts"][0]
        self.assertEqual(entry["dimensions"]["transfer"]["status"],
                         "not_observed")
        self.assertIn("transfer", entry["not_observed"])

    def test_conflicting_evidence_needs_recheck(self):
        sid = "st_proj_conflict"
        _graded(sid, attempt="att_1", rubric="q_a", dims={"concept": "met"})
        _graded(sid, verdict="wrong", attempt="att_2", rubric="q_b",
                dims={"concept": "not_met"})
        proj = project_capabilities(sid)
        self.assertEqual(proj["concepts"][0]["dimensions"]["concept"]["status"],
                         "needs_recheck")

    def test_superseded_attempt_excluded(self):
        sid = "st_proj_sup"
        _graded(sid, attempt="att_s1", rubric="q_a", dims={"concept": "not_met"})
        # 账本：同题重答 supersede att_s1
        record_verdict(sid, "sess_p", stem="题干X", verdict="wrong",
                       student_answer="甲", concept="条件概率",
                       attempt_id="att_s1")
        record_verdict(sid, "sess_p", stem="题干X", verdict="correct",
                       student_answer="乙", concept="条件概率",
                       attempt_id="att_s2")
        # 真实路径里重答会再写一条事件（evaluate_and_record）——补上
        _graded(sid, attempt="att_s2", rubric="q_b", dims={"concept": "met"})
        proj = project_capabilities(sid)
        entry = proj["concepts"][0]
        # att_s1 的 not_met 被排除：只剩 att_s2 一个题族的 met → developing
        self.assertEqual(entry["dimensions"]["concept"]["status"], "developing")
        self.assertEqual(entry["evidence_count"], 1)


class TestMasteryReplay(StorageSandboxTestCase):

    def test_replay_equals_fresh_build_without_superseded(self):
        sid = "st_replay"
        skill = "math.prob.replay"
        record_quiz_result(concept="条件概率", correct=True, skill_id=skill,
                           student_id=sid, attempt_id="att_r0")
        record_quiz_result(concept="条件概率", correct=True, skill_id=skill,
                           student_id=sid, attempt_id="att_r1")
        record_quiz_result(concept="条件概率", correct=False, skill_id=skill,
                           note="分母算错", student_id=sid, attempt_id="att_r2")
        stored = get_student_model(sid).load().mastery.records[skill].p_known

        # 重答 supersede att_r2（错误观测被撤销）
        record_verdict(sid, "sess_r", stem="题干Y", verdict="wrong",
                       student_answer="旧", concept="条件概率",
                       attempt_id="att_r2")
        record_verdict(sid, "sess_r", stem="题干Y", verdict="correct",
                       student_answer="新", concept="条件概率",
                       attempt_id="att_r3")
        # 对应新观测也要进事件流（真实路径由 evaluate_and_record 写入）
        record_quiz_result(concept="条件概率", correct=True, skill_id=skill,
                           student_id=sid, attempt_id="att_r3")

        report = rebuild_mastery(sid, reason="test")
        replayed = get_student_model(sid).load().mastery.records[skill].p_known

        # 期望值：全新构建 r0,r1,r3 三次 correct（去掉被撤的 r2）
        fresh = Mastery(skill_id=skill)
        fresh.update_binary(True)
        fresh.update_binary(True)
        fresh.update_binary(True)
        self.assertEqual(replayed, fresh.p_known)
        self.assertGreater(replayed, stored)  # 撤销一次错误观测抬升后验
        self.assertIn(skill, report["changed"])

    def test_replay_respects_partial_dual_track(self):
        sid = "st_replay_partial"
        skill = "math.prob.part"
        record_quiz_result(concept="条件概率", correct=True, skill_id=skill,
                           student_id=sid, attempt_id="att_p0")
        p_before = get_student_model(sid).load().mastery.records[skill].p_known
        record_quiz_result(concept="条件概率", correct=False, skill_id=skill,
                           verdict="partial", student_id=sid,
                           attempt_id="att_p1")
        rebuild_mastery(sid)
        self.assertEqual(
            get_student_model(sid).load().mastery.records[skill].p_known,
            p_before)  # partial 不参与 BKT 重放（W2 分轨保持）


class TestDisputeMarker(StorageSandboxTestCase):

    def test_dispute_marks_but_keeps_evidence(self):
        sid = "st_dispute"
        _graded(sid, attempt="att_d1", rubric="q_a", dims={"concept": "met"})
        before = project_capabilities(sid)["concepts"][0]["evidence_count"]
        # att_d1 只存在于事件流、不在账本 attempts → 无法定位，返回 False
        # （不误标他人/他路径的 attempt id）
        self.assertFalse(flag_attempt_disputed(sid, "att_d1", reason="判错了"))
        after = project_capabilities(sid)["concepts"][0]["evidence_count"]
        self.assertEqual(after, before)  # 异议不抹除证据（保守）

    def test_dispute_on_ledger_attempt(self):
        sid = "st_dispute2"
        record_verdict(sid, "sess_d", stem="题干Z", verdict="wrong",
                       student_answer="x", concept="条件概率",
                       attempt_id="att_d2")
        self.assertTrue(flag_attempt_disputed(sid, "att_d2", reason="异议"))
        rec = {r["stem"]: r for r in list_records(sid)}["题干Z"]
        self.assertEqual(rec["attempts"][-1]["evidence_status"], "disputed")
        self.assertEqual(rec["attempts"][-1]["dispute_reason"], "异议")


class TestEvidenceProfileAPI(StorageSandboxTestCase):

    def setUp(self) -> None:
        super().setUp()
        self.alice = id_store.create_user(
            email="alice2@example.com", username="",
            password_hash=hash_password("secret123"),
            profile=UserProfile(name="alice"))
        self.headers = {"Authorization": f"Bearer {create_token(self.alice.id)}"}
        self.client = TestClient(create_app())

    def test_profile_ok_shape_and_isolation(self):
        _graded(self.alice.id, attempt="att_api1", rubric="q_a",
                dims={"concept": "met"})
        r = self.client.get("/api/v1/student/evidence-profile",
                            headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["count"], 1)
        entry = body["concepts"][0]
        self.assertEqual(entry["dimensions"]["concept"]["status"], "developing")
        self.assertEqual(entry["not_observed"][0], "procedure")
        # 身份隔离：游客命名空间看不到 alice 的证据
        r2 = self.client.get("/api/v1/student/evidence-profile")
        self.assertEqual(r2.json()["status"], "empty")

    def test_mastery_response_carries_estimate_kind(self):
        record_quiz_result(concept="条件概率", correct=True,
                           skill_id="math.prob.api", student_id=self.alice.id)
        r = self.client.get("/api/v1/student/mastery", headers=self.headers)
        skills = r.json()["skills"]
        self.assertTrue(skills)
        self.assertEqual(skills[0]["estimate_kind"], "uncalibrated_bayesian")
        self.assertEqual(skills[0]["source"], "m2_bkt")


if __name__ == "__main__":
    unittest.main()
