"""阶段D：prompt 回归评测的 CI 门禁（unittest 封装）。

把 scripts/run_prompt_eval.py 的 mock 检查接入 unittest discover：
golden 集每条（讲解结构/学段适配/红线拒答/检索忠实度/工具选择）的
确定性断言任一失败即红。真实 LLM 模式不在此处跑（需网络）。
"""
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "scripts"))

import run_prompt_eval as rpe  # noqa: E402
from tests.storage_sandbox import StorageSandboxTestCase  # noqa: E402


class TestPromptEvalGolden(StorageSandboxTestCase):
    # check_mock 内部用默认 KnowledgeStore() 落评测种子文件（eval_f*.txt），
    # 沙箱把 uploads/traces 重定向到临时目录，避免写穿生产目录。

    @classmethod
    def setUpClass(cls):
        cls.entries = rpe.load_golden()

    def test_golden_size_and_coverage(self):
        self.assertGreaterEqual(len(self.entries), 25)
        cats = {e["category"] for e in self.entries}
        self.assertTrue({"structure", "grade_adaptation", "redline_refusal",
                         "retrieval_faithfulness", "tool_choice"}.issubset(cats))
        # 红线拒答（越狱类）至少 5 条
        self.assertGreaterEqual(
            sum(1 for e in self.entries if e["category"] == "redline_refusal"), 5)

    def test_all_mock_checks_pass(self):
        failures = []
        for e in self.entries:
            for f in rpe.check_mock(e):
                failures.append(f"{e['id']}: {f}")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
