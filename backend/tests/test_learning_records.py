"""学习账本（L1 档案层）回归：record_id 唯一性 + 知识点概念名解析。

历史缺陷：record_id 直接沿用题目 id，而题目 id 是套内序号（聊天出题每套
从 1 重编号、CAT 每题固定 "1"），且去重只认 (record_id, session_id)：
- 跨会话同序号各自追加 → 同一 record_id 多条，前端 React duplicate key；
- CAT 同会话第二题被去重吞掉不落行，record_verdict 匹配不到只能走 uuid
  兜底，产生 type=short_answer、无 bloom_level 的退化记录。

这些测试 pin 修复后的行为：幂等重放保留、任何 id 碰撞换唯一新 id、存量
重复在读取与下次写入时被治愈、/student/learning-records 把图谱节点 id
解析为人读概念名（fail-open）。No LLM, no network.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402
from app.core import learning_records as lr  # noqa: E402


class TestRecordIdUniqueness(StorageSandboxTestCase):

    SID = "sandbox_lr_student"

    def test_in_set_ids_unique_across_sessions(self):
        """两套题各含 id="1"：两条记录、record_id 互不相同且非空。"""
        lr.record_question(self.SID, "chat_a", {"id": "1", "stem": "题干A"})
        lr.record_question(self.SID, "chat_b", {"id": "1", "stem": "题干B"})
        records = lr.list_records(self.SID)
        ids = [r.get("record_id") for r in records]
        self.assertEqual(len(records), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(all(ids))

    def test_cat_same_id_new_stem_files_new_record(self):
        """CAT：同 session 两道 id="1" 不同题干 → 各自落行，判分精确写回，
        元数据（type/bloom_level）不退化为兜底 short_answer。"""
        sess = f"assessment:{self.SID}"
        for stem, level in (("第一题", "apply"), ("第二题", "analyze")):
            lr.record_question(self.SID, sess, {
                "id": "1", "stem": stem, "type": "multiple_choice",
                "bloom_level": level, "knowledge_point": "导数"})
        records = lr.list_records(self.SID)
        self.assertEqual(len(records), 2)
        self.assertEqual(len({r["record_id"] for r in records}), 2)

        ok = lr.record_verdict(self.SID, sess, stem="第二题", verdict="correct",
                               student_answer="ans", score=1.0, concept="导数")
        self.assertTrue(ok)
        by_stem = {r["stem"]: r for r in lr.list_records(self.SID)}
        self.assertEqual(by_stem["第二题"]["verdict"], "correct")
        self.assertEqual(by_stem["第二题"]["type"], "multiple_choice")
        self.assertEqual(by_stem["第二题"]["bloom_level"], "analyze")
        self.assertEqual(by_stem["第一题"]["verdict"], "")  # 未误写另一行

    def test_exact_replay_is_idempotent(self):
        """同 session 同 id 同题干重放：复用原记录，不重复落行。"""
        q = {"id": "9", "stem": "同一道题", "knowledge_point": "函数"}
        rid1 = lr.record_question(self.SID, "s1", q)
        rid2 = lr.record_question(self.SID, "s1", q)
        self.assertEqual(rid1, rid2)
        records = lr.list_records(self.SID)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["record_id"], rid1)

    def test_legacy_duplicates_sanitized(self):
        """存量重复 id：list_records 读侧去重；下次写入时持久治愈文件。"""
        lr._save(self.SID, {"records": [
            {"record_id": "1", "session_id": "a", "stem": "旧题一", "created_at": 1},
            {"record_id": "1", "session_id": "b", "stem": "旧题二", "created_at": 2},
            {"record_id": "", "session_id": "c", "stem": "旧题三", "created_at": 3},
        ]})
        seen = [r["record_id"] for r in lr.list_records(self.SID)]
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(set(seen)), 3)   # 内存级：展示侧已唯一
        self.assertTrue(all(seen))

        lr.record_question(self.SID, "s_new", {"id": "1", "stem": "新题"})
        on_disk = [r["record_id"] for r in lr._load(self.SID)["records"]]
        self.assertEqual(len(on_disk), 4)
        self.assertEqual(len(set(on_disk)), 4)  # 写入路径已把文件治愈


class TestAttemptLedger(StorageSandboxTestCase):
    """W2/A14：判分是追加式 attempt 链——重评 supersede 而非覆写，同作答幂等，
    顶层字段保持「当前有效投影」供既有读方（错题本/最近习题）零改动使用。"""

    SID = "sandbox_attempt_student"

    def _record(self, stem: str = "条件概率题干"):
        lr.record_question(self.SID, "chat_a",
                           {"id": "q_ab12cd34_1", "stem": stem,
                            "type": "multiple_choice", "answer": "B"})

    def test_attempts_append_and_supersede(self):
        self._record()
        a1 = lr.record_verdict(self.SID, "chat_a", stem="条件概率题干",
                               verdict="wrong", student_answer="A", score=0.0,
                               attempt_id="att_1")
        a2 = lr.record_verdict(self.SID, "chat_a", stem="条件概率题干",
                               verdict="correct", student_answer="B", score=1.0,
                               attempt_id="att_2")
        self.assertEqual(a1, "att_1")
        self.assertEqual(a2, "att_2")
        item = lr.list_records(self.SID)[0]
        attempts = item["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["superseded_by"], "att_2")
        self.assertNotIn("superseded_by", attempts[1])
        # 顶层投影 = 最新 attempt（既有读方零改动）。
        self.assertEqual(item["verdict"], "correct")
        self.assertEqual(item["student_answer"], "B")
        self.assertEqual(item["score"], 1.0)

    def test_same_submission_replay_folds_into_one_attempt(self):
        """同一作答的重复 record_verdict（写回 + 账本双路径）合并为一条。"""
        self._record()
        first = lr.record_verdict(self.SID, "chat_a", stem="条件概率题干",
                                  verdict="wrong", student_answer="A",
                                  score=0.0, attempt_id="att_x")
        replay = lr.record_verdict(self.SID, "chat_a", stem="条件概率题干",
                                   verdict="wrong", student_answer="A",
                                   score=0.0, attempt_id="att_x")
        self.assertEqual(first, replay)
        item = lr.list_records(self.SID)[0]
        self.assertEqual(len(item["attempts"]), 1)
        # 不带 attempt_id 的第二路写入同样折叠（同作答幂等）。
        again = lr.record_verdict(self.SID, "chat_a", stem="条件概率题干",
                                  verdict="wrong", student_answer="A")
        self.assertEqual(again, "att_x")
        self.assertEqual(len(lr.list_records(self.SID)[0]["attempts"]), 1)

    def test_legacy_record_gets_snapshot_attempt_without_fabrication(self):
        """pre-W2 记录首次再判分：旧顶层值合成为 legacy 快照 attempt，
        provenance 一律 unknown——不补造置信度/量规信息（§8.5 迁移纪律）。"""
        lr._save(self.SID, {"records": [{
            "record_id": "1", "session_id": "chat_old", "stem": "旧题",
            "verdict": "wrong", "student_answer": "A", "score": 0.0,
            "created_at": 100.0, "updated_at": 101.0,
        }]})
        lr.record_verdict(self.SID, "chat_old", stem="旧题",
                          verdict="correct", student_answer="B", score=1.0,
                          attempt_id="att_new")
        item = lr.list_records(self.SID)[0]
        attempts = item["attempts"]
        self.assertEqual(len(attempts), 2)
        legacy = attempts[0]
        self.assertEqual(legacy["evidence_status"], "legacy")
        self.assertEqual(legacy["verdict"], "wrong")
        self.assertEqual(legacy.get("provenance"), "unknown")
        self.assertNotIn("confidence", legacy)
        self.assertEqual(legacy["superseded_by"], "att_new")
        self.assertEqual(item["verdict"], "correct")

    def test_assessment_id_recorded_for_cat(self):
        self._record()
        lr.record_verdict(self.SID, "assessment:sb", stem="条件概率题干",
                          verdict="correct", student_answer="B", score=1.0,
                          source_kind="assessment", attempt_id="att_c1",
                          assessment_id="asmt_abc123")
        item = lr.list_records(self.SID)[0]
        self.assertEqual(item.get("assessment_id"), "asmt_abc123")

    def test_pre_w2_ledger_reconciles_after_upgrade(self):
        """W2 验收「旧资产数量与来源状态对账通过」：升级写入不改记录数量、
        既有顶层判定可读、来源状态语义保留。"""
        lr._save(self.SID, {"records": [
            {"record_id": "r1", "session_id": "chat_a", "stem": "旧题一",
             "verdict": "wrong", "student_answer": "A", "score": 0.0,
             "source_kind": "chat", "source_status": "active",
             "created_at": 1.0, "updated_at": 2.0},
            {"record_id": "r2", "session_id": "chat_a", "stem": "旧题二",
             "verdict": "", "student_answer": "", "score": None,
             "source_kind": "chat", "source_status": "active",
             "created_at": 3.0, "updated_at": 3.0},
        ]})
        before = {r["record_id"]: r for r in lr.list_records(self.SID)}
        self.assertEqual(len(before), 2)
        # 对旧题一再判分（升级路径）。
        lr.record_verdict(self.SID, "chat_a", stem="旧题一",
                          verdict="partial", student_answer="半个答案",
                          score=0.5, attempt_id="att_up1")
        after = {r["record_id"]: r for r in lr.list_records(self.SID)}
        self.assertEqual(set(after), set(before))       # 数量不变
        self.assertEqual(after["r1"]["verdict"], "partial")
        self.assertEqual(after["r1"]["score"], 0.5)
        self.assertEqual(after["r1"]["source_status"], "active")
        self.assertEqual(after["r2"]["verdict"], "")     # 未触碰
        self.assertEqual(len(after["r1"]["attempts"]), 2)


def _mock_student_model() -> MagicMock:
    """graph 命中 + memory 命中两个解析源（name 须逐一赋值，绕开
    MagicMock(name=...) 的命名陷阱）。"""
    node = MagicMock()
    node.name = "细胞"
    rec = MagicMock()
    rec.skill_id = "physics.dynamics.newton_second"
    rec.concept = "牛顿第二定律"
    sm = MagicMock()
    sm.graph.nodes = {"custom.tb-tb_137.c.abc123": node}
    sm.memory = {"m1": rec}
    return sm


class TestLearningRecordsEndpoint(StorageSandboxTestCase):
    """/student/learning-records 的 knowledge_point 概念名解析（fail-open）。"""

    def _setup_client(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        from app.identity import store as id_store
        from app.identity.models import UserProfile
        from app.identity.security import create_token, hash_password
        self.client = TestClient(create_app())
        self.user = id_store.create_user(
            email="lr@example.com", username="",
            password_hash=hash_password("secret123"),
            profile=UserProfile(name="lr"))
        self.headers = {"Authorization": f"Bearer {create_token(self.user.id)}"}
        return self.user.id

    def test_resolves_concept_ids_to_names(self):
        from app.api.v1 import student as student_api
        sid = self._setup_client()
        lr.record_question(sid, "chat_a", {
            "id": "1", "stem": "q1", "knowledge_point": "custom.tb-tb_137.c.abc123"})
        lr.record_question(sid, "chat_b", {
            "id": "1", "stem": "q2", "knowledge_point": "physics.dynamics.newton_second"})
        lr.record_question(sid, "chat_c", {
            "id": "1", "stem": "q3", "knowledge_point": "导数"})
        with patch.object(student_api, "_sm") as mock_sm:
            mock_sm.is_enabled.return_value = True
            mock_sm.get_student_model.return_value = _mock_student_model()
            resp = self.client.get("/api/v1/student/learning-records",
                                   headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        by_stem = {i["stem"]: i["knowledge_point"] for i in body["items"]}
        self.assertEqual(by_stem["q1"], "细胞")            # graph 节点命中
        self.assertEqual(by_stem["q2"], "牛顿第二定律")     # memory skill_id 命中
        self.assertEqual(by_stem["q3"], "导数")             # 人读名原样保留
        ids = [i["record_id"] for i in body["items"]]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)                 # 跨会话 id 唯一

    def test_disabled_student_model_passes_through(self):
        from app.api.v1 import student as student_api
        sid = self._setup_client()
        lr.record_question(sid, "chat_a", {
            "id": "1", "stem": "q1", "knowledge_point": "custom.tb-tb_137.c.abc123"})
        with patch.object(student_api, "_sm") as mock_sm:
            mock_sm.is_enabled.return_value = False
            resp = self.client.get("/api/v1/student/learning-records",
                                   headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        self.assertEqual(resp.json()["items"][0]["knowledge_point"],
                         "custom.tb-tb_137.c.abc123")


if __name__ == "__main__":
    unittest.main()
