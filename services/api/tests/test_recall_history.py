"""阶段D：recall_history 升级 BM25 的回归测试。

旧实现是子串匹配线性扫描（按文件顺序截断 top-k）；新实现复用
core/retriever 的 BM25（CJK 感知分词）按相关度排序。本文件验证：
  1. 相关度排序：更相关的条目排第一，而非文件顺序第一。
  2. 零命中回退原子串扫描（无分词命中的符号串仍能找到）。
  3. 完全无命中时如实返回 NOT_FOUND（语义不变）。
  4. 工具签名/SSE 行为不变：错误码、data 结构、长文截断保持一致。
  5. 注入防护：结果文本含 <history_excerpt> 定界标记。
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from app.core import context as ctx_mod  # noqa: E402
from app.tools.recall_history import RecallHistoryTool  # noqa: E402


class TestRecallHistoryBM25(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._patches = [
            patch.object(ctx_mod, "_TRANSCRIPT_DIR", Path(self._tmp.name)),
        ]
        for p in self._patches:
            p.start()
        self.session_id = "sess_recall_test"
        self.tool = RecallHistoryTool(self.session_id)

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def _seed(self, turns: list[tuple[int, str, str]]) -> None:
        """turns: [(turn_no, role, content), ...] 写入 transcript。"""
        entries = [{"role": role, "content": content}
                   for _t, role, content in turns]
        # append_transcript 按 turn 分组写入；逐条保持顺序即可
        for (tno, role, content) in turns:
            ctx_mod.append_transcript(self.session_id, tno,
                                      [{"role": role, "content": content}])

    def _run(self, **kwargs):
        return asyncio.run(self.tool.run(**kwargs))

    def test_bm25_ranks_most_relevant_first(self):
        """BM25 按相关度排序：详细讲解浮力的 assistant 条目应排在仅提及
        一次的更早 user 条目之前（旧子串逻辑会按文件顺序先返回 user）。"""
        self._seed([
            (1, "user", "浮力是什么"),
            (1, "assistant",
             "浮力是流体对物体向上的托力。阿基米德原理指出：浮力的大小等于"
             "物体排开流体所受的重力。浮力与流体密度和排开体积有关。"),
            (2, "user", "那惯性呢"),
            (2, "assistant", "惯性是物体保持原有运动状态的性质，只与质量有关。"),
        ])
        res = self._run(query="阿基米德浮力原理", max_results=2)
        self.assertFalse(res.is_error)
        results = res.data["results"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["role"], "assistant")
        self.assertIn("阿基米德", results[0]["snippet"])

    def test_substring_fallback_when_tokenizer_misses(self):
        """分词零命中（纯符号串）时回退原子串扫描。"""
        self._seed([(1, "assistant", "配方得 x^2+y^2 ==> (x+y)^2-2xy 成立")])
        res = self._run(query="===>")
        # "===>" 不含可检索 token，BM25 零命中 -> 子串兜底也应未命中（无该串）
        self.assertTrue(res.is_error)
        self._seed([(2, "assistant", "特殊标记 ===> 出现在这里")])
        res = self._run(query="===>")
        self.assertFalse(res.is_error)
        self.assertIn("===>", res.data["results"][0]["snippet"])

    def test_no_hit_returns_not_found(self):
        """完全无命中：NOT_FOUND + 如实告知（不编造）。"""
        self._seed([(1, "user", "讲一下勾股定理")])
        res = self._run(query="量子纠缠")
        self.assertTrue(res.is_error)
        self.assertEqual(res.error_code, "NOT_FOUND")
        self.assertIn("未找到", res.text)

    def test_empty_query_bad_args(self):
        res = self._run(query="  ")
        self.assertTrue(res.is_error)
        self.assertEqual(res.error_code, "BAD_ARGS")

    def test_missing_transcript_not_found(self):
        res = self._run(query="浮力")
        self.assertTrue(res.is_error)
        self.assertEqual(res.error_code, "NOT_FOUND")

    def test_long_content_snippet_truncated(self):
        """长条目截断为 280 字 + 剩余字数提示（行为与旧版一致）。"""
        self._seed([(1, "assistant", "浮力" + "详" * 800)])
        res = self._run(query="浮力")
        self.assertFalse(res.is_error)
        snippet = res.data["results"][0]["snippet"]
        self.assertIn("…[+", snippet)
        self.assertTrue(snippet.startswith("浮力"))

    def test_results_wrapped_in_delimiters(self):
        """注入防护：结果文本逐条包裹 <history_excerpt> 定界标记。"""
        self._seed([(1, "assistant", "浮力是向上的托力。")])
        res = self._run(query="浮力")
        self.assertFalse(res.is_error)
        self.assertIn("<history_excerpt>", res.text)
        self.assertIn("</history_excerpt>", res.text)

    def test_max_results_clamped(self):
        """max_results 仍夹取 1-5（参数契约不变）。"""
        self._seed([(i + 1, "user", f"浮力 第{i}次提到") for i in range(6)])
        res = self._run(query="浮力", max_results=99)
        self.assertFalse(res.is_error)
        self.assertLessEqual(len(res.data["results"]), 5)


if __name__ == "__main__":
    unittest.main()
