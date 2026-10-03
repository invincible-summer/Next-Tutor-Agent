"""P2 治理测试：软指令仲裁 + redline_tail 归位。"""
import os
import unittest
from types import SimpleNamespace


class TestDirectiveArbitration(unittest.TestCase):
    def setUp(self):
        self.sid = "student_arb_" + os.urandom(3).hex()

    def tearDown(self):
        from app.agents.ux_intelligence.store import _STUDENTS_DIR
        for f in _STUDENTS_DIR.glob(f"{self.sid}.*"):
            try:
                f.unlink()
            except OSError:
                pass

    def _set_ux_detail(self, level: str):
        from app.agents.ux_intelligence import store
        from app.agents.ux_intelligence.schema import (DetailLevel,
                                                       InteractionStyle, UXProfile)
        p = UXProfile(student_id=self.sid,
                      style=InteractionStyle(detail_level=DetailLevel(level)))
        store.save_profile(self.sid, p)

    def test_header_states_precedence(self):
        from app.agents.supervisor import _arbitrate_directives
        out = _arbitrate_directives("[学生智能·教学策略] 模式=explanation：x", None,
                                    self.sid)
        self.assertTrue(out.startswith("[指令仲裁]"))
        self.assertIn("显式输出约束", out)
        self.assertIn("教学策略", out)

    def test_deep_vs_concise_converges(self):
        from app.agents.supervisor import _arbitrate_directives
        self._set_ux_detail("concise")
        strategy = SimpleNamespace(explanation_depth="deep")
        recap = ("[学生智能·分层教学] 该生掌握度较好，可加入深入推导与拓展联系。\n"
                 "[交互智能·表达适配] 简洁优先。")
        out = _arbitrate_directives(recap, strategy, self.sid)
        self.assertIn("点到为止", out)
        self.assertNotIn("可加入深入推导与拓展联系", out)

    def test_deep_without_concise_unchanged(self):
        from app.agents.supervisor import _arbitrate_directives
        self._set_ux_detail("detailed")
        strategy = SimpleNamespace(explanation_depth="deep")
        recap = "[学生智能·分层教学] 该生掌握度较好，可加入深入推导与拓展联系。"
        out = _arbitrate_directives(recap, strategy, self.sid)
        self.assertIn("可加入深入推导与拓展联系", out)


class TestRedlineTailPosition(unittest.TestCase):
    def test_build_context_no_longer_appends_redline(self):
        # P2：红线尾注改由 executor（plan recap 后）/ chat_agent 调用点压尾，
        # build_context 不再代压（其名义尾部之后还有 6 层软指令注入）。
        from app.core.context import build_context
        msgs = build_context("SYS", "", [], "你好", "")
        from app.prompts.registry import get
        redline = get("redline_tail").text
        self.assertTrue(all(m.get("content") != redline for m in msgs))

    def test_redline_registered_and_nonempty(self):
        from app.prompts.registry import get
        self.assertTrue(get("redline_tail").text.strip())


if __name__ == "__main__":
    unittest.main()
