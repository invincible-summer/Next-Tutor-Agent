"""P1 学段去僵化测试：自动学段（grade=""）全链路语义。

验收（update_plan §4.3 / §10.1）：
- 新会话默认自动：grade_preamble("") 无 [学段教学细则] 块、有轻约束行。
- 显式学段：preamble 注入细则（与改造前逐字一致）。
- 旧会话（已存「高中」）from_dict 行为不变。
- generate_quiz / fit_quiz 省略 grade 可出题（不再默认高中）。
- M3 学段地板自动不触发（easy 起步）；显式高中/本科 medium 起步。
- PATCH /chat/sessions/{id} 切换 grade 持久化且非法值 400。
"""
import asyncio
import json
import os
import tempfile
import unittest


from tests.storage_sandbox import StorageSandboxTestCase
class TestAutoGradeHelpers(unittest.TestCase):
    def test_is_auto_detection(self):
        from app.agents.teaching_engine.stage_profile import is_auto
        for g in ("", "自动", "  ", None):
            self.assertTrue(is_auto(g), f"{g!r} 应为自动")
        for g in ("小学", "初中", "高中", "本科"):
            self.assertFalse(is_auto(g), f"{g!r} 应为显式学段")

    def test_normalize_grade(self):
        from app.agents.teaching_engine.stage_profile import normalize_grade
        self.assertEqual(normalize_grade("自动"), "")
        self.assertEqual(normalize_grade(" 高中 "), "高中")
        self.assertEqual(normalize_grade(""), "")
        self.assertEqual(normalize_grade(None), "")


class TestGradePreambleAuto(unittest.TestCase):
    def test_auto_no_stage_block_has_light_constraint(self):
        from app.prompts.tutor import grade_preamble
        for g in ("", "自动"):
            p = grade_preamble(g, False)
            self.assertNotIn("[学段教学细则", p, f"{g!r} 不应注入学段细则块")
            self.assertIn("[学段] 学生未指定学段", p)

    def test_explicit_grade_injects_full_block(self):
        from app.prompts.tutor import grade_preamble
        for g in ("小学", "初中", "高中", "本科"):
            p = grade_preamble(g, False)
            self.assertIn(f"[学段教学细则·{g}]", p)
            self.assertNotIn("[学段] 学生未指定学段", p)

    def test_backward_compat_explicit_highschool_unchanged(self):
        # 旧会话存「高中」：preamble 注入与改造前一致（强约束不变）。
        from app.prompts.tutor import grade_preamble
        p = grade_preamble("高中", False)
        self.assertIn("[学段教学细则·高中]", p)
        self.assertIn("严格定义", p)


class TestSessionGradeDefault(unittest.TestCase):
    def test_new_session_default_empty(self):
        from app.core.session import TutorSession
        self.assertEqual(TutorSession().grade, "")

    def test_old_session_with_highschool_preserved(self):
        # 旧会话已存「高中」：from_dict 原样保留（行为不变）。
        from app.core.session import TutorSession
        d = {"session_id": "s1", "grade": "高中", "messages": []}
        s = TutorSession.from_dict(d)
        self.assertEqual(s.grade, "高中")

    def test_from_dict_missing_grade_defaults_empty(self):
        from app.core.session import TutorSession
        s = TutorSession.from_dict({"session_id": "s2", "messages": []})
        self.assertEqual(s.grade, "")


class TestQuizAutoGrade(unittest.TestCase):
    def test_generate_quiz_omitted_grade_uses_auto_prompt(self):
        from app.tools.quiz import GenerateQuizTool

        class LLM:
            def __init__(self): self.calls = []
            async def complete(self, messages, **kw):
                self.calls.append(messages[0]["content"])
                return ('{"questions": []}', {})

        llm = LLM()
        # 省略 grade：不再默认高中、不报错、走自动 prompt（无学段难度锚点行）。
        res = asyncio.run(GenerateQuizTool(llm).run(topic="浮力"))
        self.assertEqual(res.status, "partial")  # 0 题 partial，但未报 BAD_ARGS
        self.assertTrue(llm.calls)
        prompt = llm.calls[0]
        self.assertIn("按知识点本身自适应", prompt)
        self.assertNotIn("该学段难度锚点", prompt)

    def test_generate_quiz_explicit_grade_uses_anchor(self):
        from app.tools.quiz import GenerateQuizTool

        class LLM:
            def __init__(self): self.calls = []
            async def complete(self, messages, **kw):
                self.calls.append(messages[0]["content"])
                return ('{"questions": []}', {})

        llm = LLM()
        asyncio.run(GenerateQuizTool(llm).run(topic="浮力", grade="小学"))
        self.assertIn("难度锚点", llm.calls[0])

    def test_generate_quiz_invalid_grade_still_rejected(self):
        from app.tools.quiz import GenerateQuizTool

        class LLM:
            async def complete(self, messages, **kw):
                return ('{"questions": []}', {})

        res = asyncio.run(GenerateQuizTool(LLM()).run(topic="x", grade="大学"))
        self.assertEqual(res.status, "error")

    def test_fit_quiz_auto_prompt(self):
        from app.tools.fit_quiz import FitQuizTool

        class LLM:
            def __init__(self): self.calls = []
            async def complete(self, messages, **kw):
                self.calls.append(messages[0]["content"])
                return ('{"questions": []}', {})

        llm = LLM()
        res = asyncio.run(FitQuizTool(llm).run(reference="一道参考题"))
        self.assertEqual(res.status, "partial")
        self.assertIn("按知识点本身自适应", llm.calls[0])


class TestEvaluatorAutoGrade(unittest.TestCase):
    def test_grade_prompt_auto_uses_generic_mistakes(self):
        from app.agents.assessment.evaluator import grade_open_prompt
        p = grade_open_prompt(stem="?", q_type="fill_blank",
                              correct_answer="2", explanation=".",
                              student_answer="3", grade="")
        self.assertIn("未指定学段", p)
        self.assertNotIn("学段「高中」", p)

    def test_grade_prompt_auto_token_also_works(self):
        from app.agents.assessment.evaluator import grade_open_prompt
        p = grade_open_prompt(stem="?", q_type="fill_blank",
                              correct_answer="2", explanation=".",
                              student_answer="3", grade="自动")
        self.assertIn("未指定学段", p)


class TestTeachingPolicyAuto(unittest.TestCase):
    def _ctx(self, grade):
        from app.agents.teaching_engine.state import (
            TeachingContext, TeachingOutcome)
        return TeachingContext(concept="导数", subject="数学", grade=grade,
                               previous_mode=None,
                               previous_outcome=TeachingOutcome.CORRECT)

    def test_auto_no_stage_floor_easy_start(self):
        # 自动学段 + 新概念（无作答证据）：easy 起步，不触发高中/本科 medium 地板。
        from app.agents.teaching_engine.policy import compose
        from app.agents.teaching_engine.strategy import TeachingMode
        strat = compose(self._ctx(""), TeachingMode.PRACTICE)
        self.assertEqual(strat.exercise_level, "easy")

    def test_explicit_highschool_stage_floor_medium_start(self):
        # 显式高中 + 新概念：medium 起步（学段地板生效）。
        from app.agents.teaching_engine.policy import compose
        from app.agents.teaching_engine.strategy import TeachingMode
        strat = compose(self._ctx("高中"), TeachingMode.PRACTICE)
        self.assertEqual(strat.exercise_level, "medium")

    def test_auto_introduction_uses_generic_recipe(self):
        # 自动学段 INTRODUCTION：不走本科/高中/小学专属配方，用通用配方。
        from app.agents.teaching_engine.policy import compose
        from app.agents.teaching_engine.strategy import TeachingMode
        strat = compose(self._ctx(""), TeachingMode.INTRODUCTION)
        # 通用配方 focus 含「先讲直觉与生活类比」
        self.assertIn("先讲直觉与生活类比", strat.focus)


class TestPatchSessionGrade(StorageSandboxTestCase):
    """PATCH /chat/sessions/{id} grade 切换 + set_session_grade 持久化。"""

    def setUp(self):
        super().setUp()
        self._local_tmp = tempfile.mkdtemp()
        self._orig = os.environ.get("CHAT_HISTORY_DIR")
        # session.py 用模块级 _SESSIONS_DIR（项目根 chat_history）。为隔离测试，
        # 直接 patch 模块路径常量。
        import app.core.session as sess
        self._sess_mod = sess
        self._orig_dir = sess._SESSIONS_DIR
        from pathlib import Path
        sess._SESSIONS_DIR = Path(self._local_tmp)

    def tearDown(self):
        self._sess_mod._SESSIONS_DIR = self._orig_dir
        super().tearDown()

    def _make_session(self, grade=""):
        from app.core.session import TutorSession, save_session
        s = TutorSession(session_id="chat_test_auto", grade=grade)
        s.student_id = "stu1"
        save_session(s)
        return s

    def test_set_session_grade_persists(self):
        from app.core.session import set_session_grade, load_session
        self._make_session(grade="")
        self.assertTrue(set_session_grade("chat_test_auto", "初中"))
        self.assertEqual(load_session("chat_test_auto").grade, "初中")

    def test_set_session_grade_auto_token_normalized(self):
        from app.core.session import set_session_grade, load_session
        self._make_session(grade="高中")
        set_session_grade("chat_test_auto", "自动")  # 直接写原值，由 API 层归一
        # API 层归一后再调用；这里验证空串落库正确
        set_session_grade("chat_test_auto", "")
        self.assertEqual(load_session("chat_test_auto").grade, "")

    def test_patch_grade_invalid_returns_400(self):
        # 通过 API 层校验：非法 grade（非空且非四学段）→ 400。
        from app.agents.teaching_engine.stage_profile import VALID_STAGES, normalize_grade
        # 模拟 patch_session 的校验逻辑
        for bad in ("大学", "kindergarten", "xyz"):
            g = normalize_grade(bad)
            self.assertTrue(g and g not in VALID_STAGES)

    def test_patch_grade_auto_or_valid_passes(self):
        from app.agents.teaching_engine.stage_profile import VALID_STAGES, normalize_grade
        for ok in ("", "自动", "小学", "初中", "高中", "本科"):
            g = normalize_grade(ok)
            self.assertFalse(g and g not in VALID_STAGES)


class TestPatchSessionGradeAPI(unittest.TestCase):
    """End-to-end PATCH /chat/sessions/{id} grade 切换 + 非法值 400 + 隔离 404。"""

    def setUp(self) -> None:
        import sys
        from pathlib import Path
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from app.main import create_app
        from app.core import session as session_mod
        from app.identity import config as id_config
        from app.identity import store as id_store
        from app.identity.security import create_token, hash_password

        self._tmp = tempfile.TemporaryDirectory()
        self._env_old = os.environ.get("AUTH_MODE")
        os.environ["AUTH_MODE"] = "1"
        (Path(self._tmp.name) / "users").mkdir()
        self._patches = [
            patch.object(session_mod, "_SESSIONS_DIR", Path(self._tmp.name)),
            patch.object(id_config, "AUTH_JWT_SECRET", "test-secret-not-default"),
            patch.object(id_store, "_ACCOUNTS_FILE",
                         Path(self._tmp.name) / "users" / "accounts.json"),
        ]
        for p in self._patches:
            p.start()
        self._id_store = id_store
        self._create_token = create_token
        self._hash_password = hash_password
        self._session_mod = session_mod
        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        if self._env_old is None:
            os.environ.pop("AUTH_MODE", None)
        else:
            os.environ["AUTH_MODE"] = self._env_old
        self._tmp.cleanup()

    def _write_session(self, session_id: str, student_id: str, grade: str = ""):
        d = {"session_id": session_id, "title": session_id, "messages": [],
             "student_id": student_id, "grade": grade}
        (self._session_mod._SESSIONS_DIR / f"{session_id}.json").write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def test_patch_grade_persists_and_normalizes_auto(self):
        user = self._id_store.create_user(
            email="g1@example.com", username="",
            password_hash=self._hash_password("secret123"))
        tok = self._create_token(user.id)
        self._write_session("s_auto", user.id, "高中")
        h = {"Authorization": f"Bearer {tok}"}
        # 切到自动：「自动」→ 归一为 "" 落库
        r = self.client.patch("/api/v1/chat/sessions/s_auto",
                              json={"grade": "自动"}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get("grade"), "")
        # 再切到初中
        r2 = self.client.patch("/api/v1/chat/sessions/s_auto",
                               json={"grade": "初中"}, headers=h)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json().get("grade"), "初中")
        # 持久化：重新 GET 确认落库
        g = self.client.get("/api/v1/chat/sessions/s_auto", headers=h).json()
        self.assertEqual(g["grade"], "初中")

    def test_patch_invalid_grade_returns_400(self):
        user = self._id_store.create_user(
            email="g2@example.com", username="",
            password_hash=self._hash_password("secret123"))
        tok = self._create_token(user.id)
        self._write_session("s_bad", user.id)
        h = {"Authorization": f"Bearer {tok}"}
        r = self.client.patch("/api/v1/chat/sessions/s_bad",
                              json={"grade": "大学"}, headers=h)
        self.assertEqual(r.status_code, 400)

    def test_patch_foreign_session_returns_404(self):
        user = self._id_store.create_user(
            email="g3@example.com", username="",
            password_hash=self._hash_password("secret123"))
        tok = self._create_token(user.id)
        self._write_session("s_foreign", "usr_someoneelse", "高中")
        h = {"Authorization": f"Bearer {tok}"}
        r = self.client.patch("/api/v1/chat/sessions/s_foreign",
                              json={"grade": "初中"}, headers=h)
        self.assertEqual(r.status_code, 404)

    def test_patch_title_still_works_alone(self):
        # 向后兼容：只传 title（旧 rename 调用）仍能改名。
        user = self._id_store.create_user(
            email="g4@example.com", username="",
            password_hash=self._hash_password("secret123"))
        tok = self._create_token(user.id)
        self._write_session("s_rename", user.id)
        h = {"Authorization": f"Bearer {tok}"}
        r = self.client.patch("/api/v1/chat/sessions/s_rename",
                              json={"title": "新标题"}, headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("title"), "新标题")


if __name__ == "__main__":
    unittest.main()
