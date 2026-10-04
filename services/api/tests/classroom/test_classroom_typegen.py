"""共享契约类型生成回归：生成结果稳定且与磁盘文件一致（A01；生成链见 scripts/contracts/README.md）。

生成器已合并为 scripts/contracts/generate_types.py（唯一路径），
输出目标 packages/contracts/src/generated/*.ts。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "services" / "api"))

_SPEC = importlib.util.spec_from_file_location(
    "generate_types", _REPO / "scripts" / "contracts" / "generate_types.py")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

TARGET = _REPO / "packages" / "contracts" / "src" / "generated" / "classroom.ts"


class TypegenTests(unittest.TestCase):
    def test_generated_matches_disk(self):
        content = _MODULE.generate_target("classroom", "app.schemas.classroom")
        self.assertTrue(TARGET.exists(), "生成文件缺失，请运行 pnpm contracts:generate")
        self.assertEqual(TARGET.read_text(encoding="utf-8"), content)

    def test_union_aliases_present(self):
        content = _MODULE.generate_target("classroom", "app.schemas.classroom")
        for alias in ("InlineSpan", "DiagramSpec", "SlideBlock",
                      "SourceLocator", "EditChange", "RevisionOperation"):
            self.assertIn(f"export type {alias} = ", content)

    def test_discriminated_members_shape(self):
        content = _MODULE.generate_target("classroom", "app.schemas.classroom")
        self.assertIn('export interface SpanMath {', content)
        self.assertIn('kind?: "math";', content)
        # 可空字段生成 `?: T | null` 形式
        self.assertIn("verified_question_template?: Record<string, unknown> | null;",
                      content)

    def test_deterministic(self):
        first = _MODULE.generate_target("classroom", "app.schemas.classroom")
        second = _MODULE.generate_target("classroom", "app.schemas.classroom")
        self.assertEqual(first, second)

    def test_illustration_contracts_generated(self):
        # 工具助手与测评配图契约必须进入共享包（契约私有材料不进公开包；见 scripts/contracts/README.md）。
        content = _MODULE.generate_target("illustration", "app.schemas.illustration")
        for name in ("ToolIllustrationJob", "IllustrationSession",
                     "ScenarioRevision", "QuestionIllustration", "QuizIllustrationJob"):
            self.assertIn(f"export interface {name} ", content)
        # IllustrationMode 是 Literal 别名，以内联联合形式出现在字段上。
        self.assertIn('mode: "v1" | "v2" | "v3";', content)
        # 公开 job 阶段枚举完整（公开 stage 全集）。
        self.assertIn('"preparing" | "retrieving" | "composing" | "rendering" | "reviewing" | "ready" | "failed";',
                      content)
        # 私有创作材料绝不出现在公开契约里。
        for private in ("authoring_gold", "DiagramSourceV3", "contract_hash"):
            self.assertNotIn(private, content)


if __name__ == "__main__":
    unittest.main()
