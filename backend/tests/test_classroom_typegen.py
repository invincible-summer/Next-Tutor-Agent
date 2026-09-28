"""类型生成脚本回归：生成结果稳定且与磁盘文件一致（plan.md A01）。"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "backend"))

_SPEC = importlib.util.spec_from_file_location(
    "generate_classroom_types", _REPO / "scripts" / "generate_classroom_types.py")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

TARGET = _REPO / "frontend" / "src" / "lib" / "types-classroom.generated.ts"


class TypegenTests(unittest.TestCase):
    def test_generated_matches_disk(self):
        content = _MODULE.generate()
        self.assertTrue(TARGET.exists(), "生成文件缺失，请运行生成脚本")
        self.assertEqual(TARGET.read_text(encoding="utf-8"), content)

    def test_union_aliases_present(self):
        content = _MODULE.generate()
        for alias in ("InlineSpan", "DiagramSpec", "SlideBlock",
                      "SourceLocator", "EditChange", "RevisionOperation"):
            self.assertIn(f"export type {alias} = ", content)

    def test_discriminated_members_shape(self):
        content = _MODULE.generate()
        self.assertIn('export interface SpanMath {', content)
        self.assertIn('kind?: "math";', content)
        # 可空字段生成 `?: T | null` 形式
        self.assertIn("verified_question_template?: Record<string, unknown> | null;",
                      content)

    def test_deterministic(self):
        self.assertEqual(_MODULE.generate(), _MODULE.generate())


if __name__ == "__main__":
    unittest.main()
