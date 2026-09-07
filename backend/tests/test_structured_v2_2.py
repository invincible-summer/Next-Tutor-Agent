"""structured-v2.2 课文结构与上下文注入测试。

覆盖：断行修复（两种脚注锚点形态）/运行页眉剥离/注释与词表独立块/课题
父文档标记/检索面包屑 token/staging 保真质检（failed_garble）/表格收割
反伪造门槛。全部确定性，无 LLM、不落盘（figure/rag_index 用内存对象）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.structured_chunker import (CHUNK_SCHEMA_VERSION,  # noqa: E402
                                         chunk_text_v2)


def _types(chunks):
    return [c.metadata.get("block_types") for c in chunks]


class TestLineRepair(unittest.TestCase):
    def _page(self):
        return "\n".join([
            "示例 上册", "1 春", "a", "朱自清（作者）",
            "独立寒秋，", "湘江北去，", "沙洲", "b头。",
            "看万山红遍，", "层林尽染；"])

    def test_glued_anchor_merges_word(self):
        # 粘连形态：「沙洲」+「b头。」→ 沙洲头（bigram 洲头 保住）。
        chunks = chunk_text_v2(self._page(), source="语文.pdf", file_id="f1")
        joined = "\n".join(c.text for c in chunks)
        self.assertIn("沙洲头", joined)
        body = next(c for c in chunks if "独立寒秋" in c.text)
        self.assertIn("洲头", body.tokens)

    def test_standalone_anchor_dropped(self):
        # 独立形态：标题注锚点行「a」直接丢弃，不并词。
        chunks = chunk_text_v2(self._page(), source="语文.pdf", file_id="f1")
        self.assertNotIn("\na\n", "\n".join(c.text for c in chunks))

    def test_repeated_running_header_stripped(self):
        # 运行页眉「示例 上册」跨 ≥3 页页首出现 → 剥离（取证：52 处污染）。
        pages = [self._page() for _ in range(4)]
        chunks = chunk_text_v2("\f".join(pages), source="语文.pdf", file_id="f1")
        self.assertNotIn("示例 上册", "\n".join(c.text for c in chunks))

    def test_uppercase_single_letter_kept(self):
        # 大写单字母行（可能是答案键）不丢弃。
        chunks = chunk_text_v2("第一单元\n阅读理解\nB", source="en.pdf", file_id="f2")
        self.assertIn("B", "\n".join(c.text for c in chunks))


class TestLessonAndBlockTypes(unittest.TestCase):
    def test_annotation_blocks_grouped(self):
        page = "\n".join([
            "岳阳楼记", "予观夫巴陵胜状，此则岳阳楼之大观也。",
            "① 胜状：胜景，美好的景色。", "② 大观：雄伟景象。", "③ 迁客：被降职外调的官员。"])
        chunks = chunk_text_v2(page, source="语文.pdf", file_id="f3")
        ann = [c for c in chunks if "annotation" in (c.metadata.get("block_types") or [])]
        self.assertEqual(len(ann), 1)
        self.assertIn("迁客", ann[0].text)  # ①②③ 合为一组（NFKC 后 ③→3）

    def test_vocabulary_block(self):
        page = "\n".join([
            "severe /sɪˈvɪə(r)/ n. 严重的", "hope\talone",
            "diarrhoea /ˌdaɪəˈrɪə/ n. 腹泻",
            "Reading the passage and answer the questions below."])
        chunks = chunk_text_v2(page, source="英语.pdf", file_id="f4")
        self.assertIn(["vocabulary"], _types(chunks))
        vocab = next(c for c in chunks
                     if c.metadata.get("block_types") == ["vocabulary"])
        self.assertIn("severe", vocab.text)
        self.assertNotIn("Reading", vocab.text)  # 后续正文不稀释词表块

    def test_lesson_marking_and_propagation(self):
        pages = []
        for _ in range(2):
            pages.append("\n".join([
                "第一单元", "1 春", "朱自清（作者）", "盼望着，盼望着，东风来了。",
                "2 济南的冬天", "老舍", "对于一个在北平住惯的人，像我，冬天要是有风，"]))
        chunks = chunk_text_v2("\f".join(pages), source="语文.pdf", file_id="f5")
        lessons = {c.metadata.get("lesson") for c in chunks}
        self.assertIn("1 春", lessons)
        self.assertIn("2 济南的冬天", lessons)
        heading = next(c for c in chunks if c.metadata.get("is_lesson"))
        self.assertEqual(heading.text, "1 春")
        body = next(c for c in chunks if "盼望着" in c.text)
        self.assertEqual(body.metadata.get("lesson"), "1 春")

    def test_subsection_heading_not_lesson(self):
        # 6.1.1 是小节不是课题（数字含小数点）。
        chunks = chunk_text_v2("第3章\n6.1 平面向量\n向量是既有大小又有方向的量。",
                               source="数学.pdf", file_id="f6")
        self.assertTrue(all(not c.metadata.get("is_lesson") for c in chunks))

    def test_breadcrumb_tokens_carry_lesson(self):
        # 课题查询词面覆盖：正文 chunk 的索引 token 含课题词（湖畔夜色修复）。
        page = "\n".join(["1 湖畔夜色", "这几天心里颇不宁静。今晚在院子里坐着乘凉。",
                          "忽然想起日日走过的荷塘，在这满月的光里，总该另有一番样子吧。"])
        chunks = chunk_text_v2(page, source="语文必修上.pdf", file_id="f7")
        body = next(c for c in chunks if "颇不宁静" in c.text)
        self.assertIn("荷塘", body.tokens)
        self.assertIn("月色", body.tokens)
        # 展示文本不带面包屑头。
        self.assertTrue(body.text.startswith("这几天"))

    def test_schema_bumped(self):
        self.assertEqual(CHUNK_SCHEMA_VERSION, "structured-v2.2")


class TestStagingGarbleGate(unittest.TestCase):
    def test_failed_garble_on_mojibake(self):
        from app.core.rag_index import _validate_staged_chunks
        garble = "\f".join(["ａ１１ｘ１＋ａ１２ｘ２＝ｂ１ꎬ" * 6 + "正常" * 10
                            for _ in range(6)])
        chunks = chunk_text_v2(garble, source="线代.pdf", file_id="g1")
        quality = _validate_staged_chunks(garble, chunks)
        self.assertEqual(quality["status"], "failed_garble")
        self.assertGreater(quality["text_quality"]["corrupt"], 0)

    def test_passed_on_clean_text(self):
        from app.core.rag_index import _validate_staged_chunks
        text = "\f".join(["卷积神经网络具有局部连接和权重共享特性。" * 20
                          for _ in range(4)])
        chunks = chunk_text_v2(text, source="dl.pdf", file_id="g2")
        quality = _validate_staged_chunks(text, chunks)
        self.assertEqual(quality["status"], "passed")


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def extract(self):
        return self._rows

    def to_markdown(self):
        return "\n".join("| " + " | ".join(c or "" for c in r) + " |"
                         for r in self._rows)


class TestFakeTableGuard(unittest.TestCase):
    def test_duplicated_column_table_rejected(self):
        # 选必3 取证：问题框被伪造成两列重复的 markdown 表。
        question = "用一个大写的英文字母或一个阿拉伯数字给教室里的一个座位编号，总共能编出多少种不同的号码？"
        rows = [[question, "Col2"], ["Ꮶ", "Ꮶ"], ["", ""],
                [question, question], [question, question]]
        from app.core.figure_harvest import _table_markdown
        self.assertEqual(_table_markdown(_FakeTable(rows)), "")

    def test_genuine_table_kept(self):
        rows = [["元件制造厂", "次品率", "份额"],
                ["甲厂", "0.02", "0.15"],
                ["乙厂", "0.01", "0.80"],
                ["丙厂", "0.03", "0.05"]]
        from app.core.figure_harvest import _table_markdown
        md = _table_markdown(_FakeTable(rows))
        self.assertIn("次品率", md)
        self.assertIn("乙厂", md)

    def test_single_row_or_column_rejected(self):
        from app.core.figure_harvest import _table_markdown
        self.assertEqual(_table_markdown(_FakeTable([["只有一行", "无法成表"]])), "")
        self.assertEqual(_table_markdown(_FakeTable([["单列"], ["单列"], ["单列"]])), "")


if __name__ == "__main__":
    unittest.main()
