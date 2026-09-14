"""G5 回归：出题依据必须过证据门相关性判定（plan.md §4.6 / §26 Flow 5）。

E2E 取证：刚上传的小文件让 knowledge_search 走 allow_small_direct
（≤8 块直通，confidence 0.62，不做相关性过滤），scope 外概念
（ZX-999）借此拿到 "textbook" 依据，生成伪教材题。修复：
KnowledgeSearchQuizGroundingProvider 恒传 strict_relevance=True，
KnowledgeSearchTool 在该内部参数下禁用 allow_small_direct。

锁住三点：
- provider.resolve 把 strict_relevance=True 传给 search tool；
- strict_relevance 下小材料直通被禁用（相关性弱 -> not_found）；
- 未传 strict_relevance 的普通问答路径行为不变（直通仍可用）。
"""
from __future__ import annotations

import unittest
from typing import Any

from tests.storage_sandbox import StorageSandboxTestCase


class _RecordingSearchTool:
    """记录 kwargs 的假检索工具：按 strict_relevance 模拟两种门行为。"""

    def __init__(self, small_direct_hit: bool = True):
        self.calls: list[dict[str, Any]] = []
        self._small_direct_hit = small_direct_hit

    async def run(self, **kwargs: Any):
        self.calls.append(kwargs)
        from app.core.tool_protocol import ok, partial_result
        if kwargs.get("strict_relevance"):
            # 相关性门：ZX-999 与 fixture 无内容重合 -> 全部丢弃
            return partial_result(
                "knowledge_search",
                {"query": kwargs.get("query", ""), "results": [],
                 "count": 0, "partial": False},
                "未能通过相关性判定。")
        if self._small_direct_hit:
            return ok("knowledge_search", {
                "query": kwargs.get("query", ""),
                "results": [{"id": "f1", "file_id": "f1", "chunk_id": "c1",
                             "source": "zx17讲义.txt", "text": "第三章 ZX-17 定理"}],
                "count": 1, "partial": False})
        return ok("knowledge_search", {"query": "", "results": [], "count": 0})


class StrictRelevanceGateTest(StorageSandboxTestCase):
    def test_provider_passes_strict_relevance(self) -> None:
        from app.core.quiz_grounding import KnowledgeSearchQuizGroundingProvider
        import asyncio

        tool = _RecordingSearchTool()
        provider = KnowledgeSearchQuizGroundingProvider(
            tool, required=True, reason="pending_material_action",
            file_ids=("f1",))
        bundle = asyncio.run(provider.resolve(topic="ZX-999 未定义概念"))
        self.assertEqual(len(tool.calls), 1)
        self.assertTrue(tool.calls[0].get("strict_relevance"))
        self.assertEqual(tool.calls[0].get("file_ids"), ["f1"])
        # 相关性门丢弃 -> not_found -> strict 下不可用
        self.assertEqual(bundle.tier, "not_found")
        self.assertFalse(bundle.usable)

    def test_gate_disables_small_direct_only_under_strict_relevance(self) -> None:
        from app.core.evidence_gate import apply_evidence_gate

        candidates = [
            {"text": "第三章 ZX-17 定理", "bm25_score": 1.0,
             "source": "zx17讲义.txt"},
        ]
        # 小材料直通（旧行为，供"总结这份文件"问答）：命中即放行
        direct = apply_evidence_gate(
            "总结这份文件", candidates, 4, allow_small_direct=True)
        self.assertEqual(len(direct.selected), 1)
        # 同一调用面在 strict_relevance 语义下（quiz 路径不再传
        # allow_small_direct）：scope 外概念过不了相关性
        gated = apply_evidence_gate(
            "ZX-999 未定义概念", candidates, 4, allow_small_direct=False)
        self.assertEqual(len(gated.selected), 0)
        self.assertEqual(gated.tier, "not_found")


if __name__ == "__main__":
    unittest.main()
