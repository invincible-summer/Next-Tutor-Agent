"""M7→M3 教学指导真闭环测试：提案 → 人工应用 → guidance_store → compose 消费 → 吊销回滚。

覆盖：guidance_store 读写/幂等/吊销、compose 指导转化（focus/avoid 前插、
适用范围过滤、rationale 归因、无指导时行为不变）、TeachingManager.adapt
端到端消费、API 部署函数（applied → 写入 guidance；无指导文本的旧式提案
不部署）。全部走 StorageSandboxTestCase（students/ 根重定向）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agents.evaluation.schema import ImprovementProposal
from app.agents.evaluation import store as eval_store
from app.agents.teaching_engine import guidance_store
from app.agents.teaching_engine.manager import get_teaching_manager
from app.agents.teaching_engine.policy import compose
from app.agents.teaching_engine.state import TeachingContext
from app.agents.teaching_engine.strategy import TeachingMode

from tests.storage_sandbox import StorageSandboxTestCase


def _entry(**kw):
    base = dict(
        id="tg_1", source_proposal="op_1", title="先建直觉再上公式",
        applicability="", guidance="讲解公式前先用一个具体例子建立直觉。",
        cautions=["例子不要过于简单"], confidence=0.8, applied_at=2000.0)
    base.update(kw)
    return guidance_store.GuidanceEntry(**base)


class TestGuidanceStore(StorageSandboxTestCase):
    """students/<id>.teaching_guidance.json 的读写/幂等/吊销契约。"""

    def test_entry_roundtrip(self):
        e = _entry()
        d = e.to_dict()
        e2 = guidance_store.GuidanceEntry.from_dict(d)
        self.assertEqual(e2.title, e.title)
        self.assertEqual(e2.guidance, e.guidance)
        self.assertEqual(e2.cautions, e.cautions)
        self.assertTrue(e2.active)

    def test_apply_and_load_active(self):
        self.assertTrue(guidance_store.apply_guidance("s1", _entry()))
        active = guidance_store.load_active("s1")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].source_proposal, "op_1")

    def test_apply_rejects_empty_guidance(self):
        self.assertFalse(guidance_store.apply_guidance(
            "s1", _entry(guidance="")))

    def test_reapply_same_proposal_is_idempotent(self):
        guidance_store.apply_guidance("s1", _entry())
        again = _entry(guidance="更新后的指导文本")
        self.assertTrue(guidance_store.apply_guidance("s1", again))
        active = guidance_store.load_active("s1")
        self.assertEqual(len(active), 1)            # 不重复
        self.assertEqual(active[0].guidance, "更新后的指导文本")
        self.assertEqual(active[0].applied_at, 2000.0)  # 首次应用时间锚定不变

    def test_revoke_keeps_for_audit(self):
        guidance_store.apply_guidance("s1", _entry())
        self.assertTrue(guidance_store.revoke_guidance("s1", "tg_1"))
        self.assertEqual(guidance_store.load_active("s1"), [])
        all_entries = guidance_store.load_all("s1")
        self.assertEqual(len(all_entries), 1)
        self.assertFalse(all_entries[0].active)
        self.assertGreater(all_entries[0].revoked_at, 0.0)
        # 再吊销同一Entry返回False（已非active）
        self.assertFalse(guidance_store.revoke_guidance("s1", "tg_1"))

    def test_missing_file_is_no_guidance(self):
        self.assertEqual(guidance_store.load_active("nobody"), [])
        self.assertEqual(guidance_store.load_all("nobody"), [])

    def test_corrupt_file_is_no_guidance(self):
        path = guidance_store._resolve("s_bad")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(guidance_store.load_active("s_bad"), [])


class TestComposeGuidance(StorageSandboxTestCase):
    """compose 的指导转化：进已渲染字段、适用范围过滤、归因、无指导零变化。"""

    def _ctx(self, **kw):
        base = dict(concept="浮力", subject="物理", mastery=0.3)
        base.update(kw)
        return TeachingContext(**base)

    def test_none_guidance_changes_nothing(self):
        ctx = self._ctx()
        a = compose(ctx, TeachingMode.EXPLANATION)
        b = compose(ctx, TeachingMode.EXPLANATION, guidance=None)
        c = compose(ctx, TeachingMode.EXPLANATION, guidance=[])
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertEqual(a.to_dict(), c.to_dict())

    def test_guidance_folds_into_front_of_focus_and_avoid(self):
        ctx = self._ctx()
        plain = compose(ctx, TeachingMode.EXPLANATION)
        strat = compose(ctx, TeachingMode.EXPLANATION, guidance=[_entry()])
        self.assertTrue(strat.focus[0].startswith("教学指导「先建直觉再上公式」"))
        self.assertIn("建立直觉", strat.focus[0])
        self.assertEqual(strat.avoid[0], "例子不要过于简单")
        # 模式配方仍然紧随其后（前3条渲染预算内共享）
        self.assertEqual(strat.focus[1:], plain.focus)
        self.assertEqual(strat.avoid[1:], plain.avoid)
        # rationale 注明来源提案
        self.assertIn("#op_1", strat.rationale)

    def test_applicability_subject_match(self):
        e = _entry(applicability="适用于物理的计算类概念")
        ctx = self._ctx(subject="物理")
        strat = compose(ctx, TeachingMode.EXPLANATION, guidance=[e])
        self.assertTrue(any("教学指导" in f for f in strat.focus))
        # 学科不匹配 → 不进本轮教学
        ctx2 = self._ctx(subject="语文")
        strat2 = compose(ctx2, TeachingMode.EXPLANATION, guidance=[e])
        self.assertEqual(strat2.focus, compose(ctx2, TeachingMode.EXPLANATION).focus)

    def test_applicability_concept_match(self):
        e = _entry(applicability="适用于「浮力」及相关流体概念")
        strat = compose(self._ctx(), TeachingMode.EXPLANATION, guidance=[e])
        self.assertTrue(any("教学指导" in f for f in strat.focus))

    def test_empty_applicability_is_general(self):
        e = _entry(applicability="")
        strat = compose(self._ctx(subject="历史"), TeachingMode.REVIEW, guidance=[e])
        self.assertTrue(any("教学指导" in f for f in strat.focus))

    def test_at_most_two_entries_per_turn(self):
        entries = [_entry(id="tg_1", source_proposal="op_1", guidance="第一条。"),
                   _entry(id="tg_2", source_proposal="op_2", guidance="第二条。"),
                   _entry(id="tg_3", source_proposal="op_3", guidance="第三条。")]
        strat = compose(self._ctx(), TeachingMode.EXPLANATION, guidance=entries)
        lines = [f for f in strat.focus if f.startswith("教学指导")]
        self.assertEqual(len(lines), 2)  # 取最新的两条

    def test_long_guidance_line_capped(self):
        e = _entry(guidance="很长的指导。" * 60)
        strat = compose(self._ctx(), TeachingMode.EXPLANATION, guidance=[e])
        self.assertLessEqual(len(strat.focus[0]), 110)


class TestAdaptConsumesGuidance(StorageSandboxTestCase):
    """TeachingManager.adapt(student_id=…) 端到端：读指导文件并进策略。"""

    def test_adapt_without_student_id_ignores_guidance(self):
        # 即便指导文件存在，无 student_id 也不读（保持纯函数路径）
        guidance_store.apply_guidance("s1", _entry())
        strat = get_teaching_manager().adapt(
            TeachingContext(concept="浮力", subject="物理", mastery=0.3))
        self.assertFalse(any("教学指导" in f for f in strat.focus))

    def test_adapt_with_student_id_applies_guidance(self):
        guidance_store.apply_guidance("s1", _entry())
        strat = get_teaching_manager().adapt(
            TeachingContext(concept="浮力", subject="物理", mastery=0.3),
            student_id="s1")
        self.assertTrue(any("教学指导" in f for f in strat.focus))
        self.assertIn("#op_1", strat.rationale)

    def test_adapt_after_revoke_reverts(self):
        guidance_store.apply_guidance("s1", _entry())
        before = get_teaching_manager().adapt(
            TeachingContext(concept="浮力", subject="物理", mastery=0.3),
            student_id="s1")
        self.assertTrue(any("教学指导" in f for f in before.focus))
        guidance_store.revoke_guidance("s1", "tg_1")
        after = get_teaching_manager().adapt(
            TeachingContext(concept="浮力", subject="物理", mastery=0.3),
            student_id="s1")
        self.assertFalse(any("教学指导" in f for f in after.focus))
        self.assertNotIn("#op_1", after.rationale)


class TestDeployGuidance(StorageSandboxTestCase):
    """API 部署函数：applied 提案 → guidance_store；旧式提案不部署。"""

    def test_deploy_guidance_proposal(self):
        from app.api.v1.evaluation import _deploy_guidance
        eval_store.add_proposal("s1", ImprovementProposal(
            id="op_9", title="小步教学", guidance="每步讲完立刻检测。",
            applicability="", cautions=["不要连续灌输"], confidence=0.7))
        self.assertTrue(_deploy_guidance("s1", "op_9"))
        active = guidance_store.load_active("s1")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].source_proposal, "op_9")
        self.assertEqual(active[0].title, "小步教学")

    def test_deploy_legacy_proposal_without_guidance_is_noop(self):
        from app.api.v1.evaluation import _deploy_guidance
        eval_store.add_proposal("s1", ImprovementProposal(
            id="op_10", target="prompt", change="legacy style"))
        self.assertFalse(_deploy_guidance("s1", "op_10"))
        self.assertEqual(guidance_store.load_active("s1"), [])

    def test_deploy_missing_proposal_is_noop(self):
        from app.api.v1.evaluation import _deploy_guidance
        self.assertFalse(_deploy_guidance("s1", "nonexistent"))


if __name__ == "__main__":
    unittest.main()
