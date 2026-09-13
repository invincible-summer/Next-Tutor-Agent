"""Contract tests: Assessment/CAT grounding（plan.md §5, Phase 1）。

目标行为（当前 main 缺失，测试必须先失败）：
  1. AssessmentContext 携带 additive grounding 字段且 to_dict/from_dict
     round-trip（CAT session 持久化 / 重启恢复不丢证据 scope）；
  2. Question 携带 grounding_mode / grounding_tier / source_refs；
  3. /assessment/start 接受 session_id / textbook_ids / strict_textbook。

Phase 3 实现后全绿并保持回归。
"""
from __future__ import annotations

import unittest

from tests.storage_sandbox import StorageSandboxTestCase


class TestAssessmentContextGroundingContract(StorageSandboxTestCase):

    def test_context_has_grounding_fields_with_defaults(self):
        from app.agents.assessment import AssessmentContext
        ctx = AssessmentContext(concept="ZX-17 定理")
        self.assertFalse(ctx.grounding_required)
        self.assertEqual(ctx.grounding_mode, "generic")
        self.assertEqual(ctx.grounding_tier, "not_found")
        self.assertEqual(ctx.grounding_query, "")
        self.assertEqual(ctx.grounding_sources, [])

    def test_context_grounding_roundtrip(self):
        from app.agents.assessment import AssessmentContext
        sources = [{
            "file_id": "file_zx17", "chunk_id": "file_zx17#0",
            "filename": "zx17讲义.pdf", "page": 3, "printed_page": 12,
            "section_path": ["第三章", "3.2"],
            "excerpt": "ZX-17 定理的右端常数为 314159。",
            "context_hash": "hash_zx17", "confidence": 0.82,
        }]
        ctx = AssessmentContext(
            concept="ZX-17 定理", grounding_required=True,
            grounding_mode="textbook", grounding_tier="found",
            grounding_query="ZX-17 定理", grounding_sources=sources)
        d = ctx.to_dict()
        self.assertTrue(d["grounding_required"])
        self.assertEqual(d["grounding_mode"], "textbook")
        self.assertEqual(d["grounding_tier"], "found")
        self.assertEqual(d["grounding_sources"], sources)
        restored = AssessmentContext.from_dict(d)
        self.assertEqual(restored.grounding_mode, "textbook")
        self.assertTrue(restored.grounding_required)
        self.assertEqual(restored.grounding_sources, sources)
        # 旧持久化（无 grounding 字段）必须无损重建。
        legacy = AssessmentContext.from_dict({"concept": "浮力", "grade": "高中"})
        self.assertEqual(legacy.grounding_mode, "generic")
        self.assertEqual(legacy.grounding_sources, [])


class TestAssessmentQuestionGroundingContract(StorageSandboxTestCase):

    def test_question_has_grounding_fields(self):
        from app.agents.assessment.question import Question
        q = Question(id="q1", concept="ZX-17 定理", stem="s", answer="A")
        self.assertEqual(q.grounding_mode, "generic")
        self.assertEqual(q.grounding_tier, "")
        self.assertEqual(q.source_refs, [])

    def test_question_dict_roundtrip_keeps_provenance(self):
        from app.agents.assessment.question import Question
        refs = [{"file_id": "file_zx17", "chunk_id": "file_zx17#0",
                 "filename": "zx17讲义.pdf", "excerpt": "右端常数为 314159。"}]
        q = Question(id="q1", concept="ZX-17", stem="s", answer="A",
                     grounding_mode="textbook", grounding_tier="found",
                     source_refs=refs)
        d = q.to_dict()
        self.assertEqual(d["grounding_mode"], "textbook")
        self.assertEqual(d["source_refs"], refs)
        restored = Question.from_quiz_dict(d)
        self.assertEqual(restored.grounding_mode, "textbook")
        self.assertEqual(restored.grounding_tier, "found")
        self.assertEqual(restored.source_refs, refs)


class TestAssessmentStartRequestContract(StorageSandboxTestCase):

    def test_start_request_accepts_textbook_scope_fields(self):
        from app.api.v1.assessment import StartRequest
        req = StartRequest(concept="ZX-17 定理", session_id="sess_1",
                           textbook_ids=["tb_a"], strict_textbook=True)
        self.assertEqual(req.session_id, "sess_1")
        self.assertEqual(req.textbook_ids, ["tb_a"])
        self.assertTrue(req.strict_textbook)
        # 默认值：不传则不影响旧调用方。
        bare = StartRequest(concept="浮力")
        self.assertEqual(bare.session_id, "")
        self.assertEqual(bare.textbook_ids, [])
        self.assertFalse(bare.strict_textbook)


# --- API 级集成验收（plan.md §8 CAT 部分）---------------------------------

import json as _json  # noqa: E402
from unittest.mock import patch  # noqa: E402


class _GroundedGenLLM:
    """fake LLM：蓝图/生成/审题三分派，生成题携带 source_ref_ids。"""

    def __init__(self):
        self.prompts: list[str] = []

    async def complete(self, messages, temperature=None, max_tokens=None,
                       disable_thinking=False):
        prompt = str(messages[0]["content"])
        self.prompts.append(prompt)
        if "命题设计专家" in prompt:
            return _json.dumps({"blueprint": [
                {"angle": "概念本质", "idea": "考查 ZX-17 右端常数"}]},
                ensure_ascii=False), {}
        if "审题员" in prompt:
            return _json.dumps({"verdicts": [
                {"id": 1, "verdict": "correct", "reason": "一致"}]},
                ensure_ascii=False), {}
        # assessment_generate（"你是命题专家"）
        return _json.dumps({"questions": [{
            "id": 1, "type": "multiple_choice",
            "stem": "ZX-17 定理的右端常数是多少？",
            "options": {"A": "314159", "B": "1"}, "answer": "A",
            "explanation": "教材规定 ZX-17 定理右端常数为 314159，选 A。",
            "knowledge_point": "ZX-17 定理", "difficulty": "easy",
            "source_ref_ids": ["src_1"],
        }]}, ensure_ascii=False), {}


def _make_user_token(email: str):
    from app.identity import store as id_store
    from app.identity.models import UserProfile
    from app.identity.security import create_token, hash_password
    user = id_store.create_user(
        email=email, username="",
        password_hash=hash_password("secret123"),
        profile=UserProfile(name=email.split("@")[0]),
    )
    return user.id, {"Authorization": f"Bearer {create_token(user.id)}"}


def _make_textbook(sid: str, text: str, title: str = "ZX-17 讲义"):
    """真实走 Library.add_file + create_group，产出可检索的教材组。"""
    from app.core.library import Library, save_library
    from app.core import textbook as tb
    lib = Library(sid)
    meta = lib.add_file("", "zx17讲义.txt", text)
    save_library(lib)  # add_file 只写内存，index 需显式落盘
    rec = tb.create_group(sid, file_ids=[meta["id"]], title=title)
    return rec


_ZX_TEXT = (
    "第三章 ZX-17 定理\n"
    "ZX-17 定理的右端常数为 314159。\n"
    "根据 ZX-17 定理，任意封闭曲面上的通量积分右端常数恒取 314159。\n" * 8
)


class TestAssessmentGroundingAPI(StorageSandboxTestCase):

    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from app.main import create_app
        self.sid_a, self.headers_a = _make_user_token("ground-a@example.com")
        self.sid_b, self.headers_b = _make_user_token("ground-b@example.com")
        self.textbook = _make_textbook(self.sid_a, _ZX_TEXT)
        self.client = TestClient(create_app())
        llm_patch = patch("app.api.v1.assessment.get_llm",
                          lambda: self._llm)
        llm_patch.start()
        self.addCleanup(llm_patch.stop)
        self._llm = _GroundedGenLLM()

    def _start(self, *, headers, **extra):
        body = {"concept": "ZX-17 定理", **extra}
        return self.client.post("/api/v1/assessment/start", json=body,
                                headers=headers)

    def test_strict_textbook_cat_has_provenance(self):
        r = self._start(headers=self.headers_a,
                        textbook_ids=[self.textbook["id"]], strict_textbook=True)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok", body)
        q = body["question"]
        self.assertIsNotNone(q)
        self.assertEqual(q["grounding_mode"], "textbook")
        self.assertEqual(q["grounding_tier"], "found")
        refs = q["source_refs"]
        self.assertTrue(refs, "strict 教材测评题必须携带 source refs")
        self.assertTrue(refs[0]["file_id"])
        self.assertTrue(refs[0]["chunk_id"])
        # 蓝图与生成 prompt 都必须见到教材证据（plan §5.3 顺序）。
        blueprint = [p for p in self._llm.prompts if "命题设计专家" in p]
        gen = [p for p in self._llm.prompts if "命题专家" in p
               and "命题设计专家" not in p and "审题员" not in p]
        self.assertTrue(blueprint and "命题依据" in blueprint[0])
        self.assertTrue(gen and "命题事实边界" in gen[0])

    def test_foreign_textbook_404(self):
        # B 不拥有 A 的私有教材，也不得从存在性上探测它。
        r = self._start(headers=self.headers_b,
                        textbook_ids=[self.textbook["id"]], strict_textbook=True)
        self.assertEqual(r.status_code, 404)

    def test_foreign_session_404(self):
        from app.core.session import TutorSession, new_session_id, save_session
        sess = TutorSession(session_id=new_session_id("A的书房"))
        sess.student_id = self.sid_a
        save_session(sess)
        r = self._start(headers=self.headers_b, session_id=sess.session_id,
                        strict_textbook=True)
        self.assertEqual(r.status_code, 404)

    def test_strict_without_scope_400(self):
        r = self._start(headers=self.headers_a, strict_textbook=True)
        self.assertEqual(r.status_code, 400)
        self.assertIn("textbook_scope_required", r.json().get("detail", ""))

    def test_strict_not_found_returns_grounding_not_found(self):
        # scope 有效但教材里没有的知识点：不开始假教材 CAT。
        r = self.client.post(
            "/api/v1/assessment/start",
            json={"concept": "ZZZ-999 未定义概念",
                  "textbook_ids": [self.textbook["id"]], "strict_textbook": True},
            headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "grounding_not_found")
        self.assertIsNone(body["question"])
        self.assertEqual(body["grounding"]["tier"], "not_found")

    def test_non_strict_not_found_starts_generic(self):
        r = self.client.post(
            "/api/v1/assessment/start",
            json={"concept": "ZZZ-999 未定义概念",
                  "textbook_ids": [self.textbook["id"]]},
            headers=self.headers_a)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        # 非 strict：generic CAT 照常（题目无教材 refs 也不标 textbook）。
        if body.get("question"):
            self.assertNotEqual(body["question"].get("grounding_mode"),
                                "textbook")

    def test_next_question_reuses_persisted_grounding(self):
        from app.agents.assessment import get_assessment_manager
        r = self._start(headers=self.headers_a,
                        textbook_ids=[self.textbook["id"]], strict_textbook=True)
        self.assertEqual(r.json()["status"], "ok")
        self.client.post("/api/v1/assessment/answer",
                         json={"student_answer": "A"}, headers=self.headers_a)
        r2 = self.client.post("/api/v1/assessment/next", json={},
                              headers=self.headers_a)
        body = r2.json()
        self.assertEqual(body["status"], "ok", body)
        # next 继续用同一 persisted grounding scope：新题同样有 refs。
        q = body.get("question")
        self.assertIsNotNone(q)
        self.assertEqual(q["grounding_mode"], "textbook")
        self.assertTrue(q["source_refs"])

    def test_cat_reload_keeps_provenance(self):
        self._start(headers=self.headers_a,
                    textbook_ids=[self.textbook["id"]], strict_textbook=True)
        from app.agents.assessment import get_assessment_manager
        session = get_assessment_manager().get_session(self.sid_a)
        self.assertIsNotNone(session)
        # 重启模拟：重新从盘加载（get_session 每次都 load）。
        q = session.questions[-1]
        self.assertEqual(q.grounding_mode, "textbook")
        self.assertTrue(q.source_refs)
        ctx = session.ctx
        self.assertEqual(ctx.grounding_mode, "textbook")
        self.assertTrue(ctx.grounding_sources)


if __name__ == "__main__":
    unittest.main()
