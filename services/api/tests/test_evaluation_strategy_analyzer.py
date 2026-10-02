"""G5 回归：M7 策略聚合在去除数值增益后仍可用（plan §13.7 / §19.1）。

de-mastery 清理曾把 TurnTrace 的 learning_gain 字段删掉，但
strategy_analyzer/schema 里残留 avg_gain 引用（gains 未定义、to_dict
AttributeError），这些路径全部被 except Exception 吞掉，导致 M7 策略
数据静默变空。本文件锁住：
- analyze_traces/summarize/to_dict/from_dict 不再抛错；
- 序列化输出不含任何增益字段（avg_gain/learning_gain 归零）；
- 排序按 avg_success_rate。
纯函数测试，不触盘，无需沙箱。
"""
from __future__ import annotations

import unittest

from app.agents.evaluation.schema import StrategyEffectiveness, TurnTrace
from app.agents.evaluation.strategy_analyzer import analyze_traces, summarize

BANNED_KEYS = {"avg_gain", "learning_gain", "before_mastery", "after_mastery"}


def _traces() -> list[TurnTrace]:
    return [
        TurnTrace(id="a", mode="socratic", subject="物理", outcome="correct",
                  tokens_used=10),
        TurnTrace(id="b", mode="socratic", subject="物理", outcome="wrong",
                  tokens_used=20),
        TurnTrace(id="c", mode="lecture", subject="物理", outcome="engaged",
                  tokens_used=30),
    ]


class StrategyAnalyzerNoGainTest(unittest.TestCase):
    def test_analyze_sorts_by_success_rate(self) -> None:
        recs = analyze_traces(_traces())
        self.assertEqual(len(recs), 2)
        by_mode = {r.strategy: r for r in recs}
        self.assertAlmostEqual(by_mode["lecture"].avg_success_rate, 1.0)
        self.assertAlmostEqual(by_mode["socratic"].avg_success_rate, 0.5)
        self.assertEqual(by_mode["socratic"].sample_size, 2)
        # 排序：成功率降序
        self.assertEqual([r.strategy for r in recs][0], "lecture")

    def test_serialization_has_no_gain_fields(self) -> None:
        recs = analyze_traces(_traces())
        for r in recs:
            d = r.to_dict()
            self.assertFalse(BANNED_KEYS & set(d))
            back = StrategyEffectiveness.from_dict(d)
            self.assertEqual(back.strategy, r.strategy)
            self.assertAlmostEqual(back.avg_success_rate, r.avg_success_rate)

    def test_summarize_by_mode_shape(self) -> None:
        s = summarize(_traces())
        self.assertEqual(s["total"], 3)
        self.assertEqual(set(s["by_mode"]), {"socratic", "lecture"})
        for stats in s["by_mode"].values():
            self.assertEqual(set(stats), {"count", "success_rate", "avg_tokens"})
        self.assertAlmostEqual(s["by_mode"]["socratic"]["success_rate"], 0.5)
        self.assertAlmostEqual(s["avg_tokens"], 20.0)

    def test_empty_inputs(self) -> None:
        self.assertEqual(analyze_traces([]), [])
        empty = summarize([])
        self.assertEqual(empty["total"], 0)
        self.assertEqual(empty["by_mode"], {})


if __name__ == "__main__":
    unittest.main()
