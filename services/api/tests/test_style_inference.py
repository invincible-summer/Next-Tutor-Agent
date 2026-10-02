"""P0 个性化死链激活测试：
- style_inference：M8 反馈 → M2 learning_style 的唯一写入路径
- memory.build_directive：高置信长期事实的每轮保底注入（不依赖 BM25 命中）

（旧语义巩固 consolidation 已整簇删除——C2：MemoryService 不再派发该路径。）
"""
import os
import unittest

from app.agents.student_model.style_inference import (apply_style_inference,
                                                      infer_style_update)


class TestStyleInferenceRules(unittest.TestCase):
    def test_too_long_flips_depth_basic(self):
        out = infer_style_update(["explanation_too_long", "none",
                                  "explanation_too_long"])
        self.assertEqual(out, {"explanation_depth": "basic"})

    def test_too_short_flips_depth_deep(self):
        out = infer_style_update(["explanation_too_short"] * 2)
        self.assertEqual(out, {"explanation_depth": "deep"})

    def test_conflicting_signals_no_flip(self):
        out = infer_style_update(["explanation_too_long"] * 2
                                 + ["explanation_too_short"] * 2)
        self.assertNotIn("explanation_depth", out)

    def test_too_hard_flips_preference_step_by_step(self):
        out = infer_style_update(["explanation_too_hard"] * 3)
        self.assertEqual(out, {"preference": "step_by_step"})

    def test_below_threshold_no_change(self):
        self.assertEqual(infer_style_update(["explanation_too_long"]), {})
        self.assertEqual(infer_style_update([]), {})
        self.assertEqual(infer_style_update(None), {})

    def test_already_in_target_state_no_change(self):
        out = infer_style_update(["explanation_too_long"] * 3,
                                 current_depth="basic")
        self.assertEqual(out, {})


class TestStyleInferenceWriteback(unittest.TestCase):
    def setUp(self):
        self.sid = "student_style_" + os.urandom(3).hex()

    def tearDown(self):
        from app.agents.student_model.store import _STUDENTS_DIR
        for f in _STUDENTS_DIR.glob(f"{self.sid}.*"):
            try:
                f.unlink()
            except OSError:
                pass

    def test_flip_persists_and_reader_sees_it(self):
        from app.agents.student_model import get_student_model
        sm = get_student_model(self.sid).load()
        changed = apply_style_inference(sm, ["explanation_too_long"] * 2)
        self.assertTrue(changed)
        # 重新加载（绕过进程缓存直接读盘）验证持久化
        from app.agents.student_model.store import load_blob
        blob = load_blob(self.sid)
        self.assertEqual(blob.profile.learning_style.explanation_depth, "basic")
        # 幂等：同样信号再来一次不再翻转/写盘
        self.assertFalse(apply_style_inference(sm, ["explanation_too_long"] * 2))


class TestSemanticGlobalInjection(unittest.TestCase):
    def setUp(self):
        self.sid = "student_seminj_" + os.urandom(3).hex()

    def tearDown(self):
        from app.agents.student_model.store import _STUDENTS_DIR
        for f in _STUDENTS_DIR.glob(f"{self.sid}.*"):
            try:
                f.unlink()
            except OSError:
                pass

    def test_legacy_semantic_fact_is_not_directly_injected(self):
        from app.agents.memory import semantic, get_memory_service
        from app.agents.memory.schema import SemanticFact
        fact = SemanticFact(fact="计算容易跳步出错", category="misconception_pattern",
                            confidence=0.8, evidence_count=2)
        self.assertIsNotNone(semantic.add_or_consolidate(self.sid, fact))
        # 语义事实仍保留为学习档案/审计，但提示词只读 bounded profile。
        directive = get_memory_service().build_directive(
            student_id=self.sid, concept="光合作用", subject="生物")
        self.assertNotIn("计算容易跳步出错", directive)
        self.assertNotIn("长期事实", directive)


if __name__ == "__main__":
    unittest.main()
