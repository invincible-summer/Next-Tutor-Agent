"""P2 教材库测试：注册表 CRUD + spec_to_graph 形参化 + 章节切片 + 构建管线。

验收：
- 注册表：create/find/update/remove/textbook_for_file + 同 file_id 幂等。
- spec_to_graph 形参：max_chapters/max_concepts 截断 + level 合法学段生效/非法回退。
- 切片：PDF 书签目录精确分页；locate_chapters 确定性定位；whole_book 单章。
- 构建：mock llm 分章输出 → building→ready；LLM 故障→graph_failed；快速路径单次调用。
- 唯一性：rebuild 走质量门+原子替换，不产生不可管理的隐藏归档。
"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class _TmpStore:
    """Patch textbook + library storage dirs to a temp root."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lib_dir = self.root / "library"
        self.kg_dir = self.root / "knowledge"

    def setUp(self):
        import app.core.textbook as tb
        import app.core.library as lib
        import app.agents.knowledge.store as kgs
        self._tb = tb
        self._lib = lib
        self._kgs = kgs
        self._orig = {
            "tb._LIBRARY_DIR": tb._LIBRARY_DIR,
            "lib._LIBRARY_DIR": lib._LIBRARY_DIR,
            "kgs._KG_DIR": kgs._KG_DIR,
            "kgs._CUSTOM_DIR": kgs._CUSTOM_DIR,
        }
        tb._LIBRARY_DIR = self.lib_dir
        lib._LIBRARY_DIR = self.lib_dir
        kgs._KG_DIR = self.kg_dir
        kgs._CUSTOM_DIR = self.kg_dir / "custom"

    def tearDown(self):
        self._tb._LIBRARY_DIR = self._orig["tb._LIBRARY_DIR"]
        self._lib._LIBRARY_DIR = self._orig["lib._LIBRARY_DIR"]
        self._kgs._KG_DIR = self._orig["kgs._KG_DIR"]
        self._kgs._CUSTOM_DIR = self._orig["kgs._CUSTOM_DIR"]
        self.tmp.cleanup()


class TestTextbookRegistry(unittest.TestCase):
    def setUp(self):
        self._s = _TmpStore()
        self._s.setUp()

    def tearDown(self):
        self._s.tearDown()

    def test_create_and_find(self):
        from app.core import textbook as tb
        rec = tb.create_textbook("stu1", file_id="f1", title="高数",
                                  subject="数学", level="本科")
        self.assertEqual(rec["status"], "building")
        self.assertTrue(rec["id"].startswith("tb_"))
        self.assertEqual(rec["topic_key"], f"tb-{rec['id']}")
        self.assertEqual(tb.find_textbook("stu1", rec["id"])["title"], "高数")

    def test_create_idempotent_on_file_id(self):
        from app.core import textbook as tb
        r1 = tb.create_textbook("stu1", file_id="f1", title="A")
        r2 = tb.create_textbook("stu1", file_id="f1", title="B")  # 同 file_id
        self.assertEqual(r1["id"], r2["id"])  # 不重复创建

    def test_textbook_for_file_reverse_lookup(self):
        from app.core import textbook as tb
        tb.create_textbook("stu1", file_id="f1", title="A")
        self.assertIsNotNone(tb.textbook_for_file("stu1", "f1"))
        self.assertIsNone(tb.textbook_for_file("stu1", "f2"))
        # 隔离：他人看不见
        self.assertIsNone(tb.textbook_for_file("stu2", "f1"))

    def test_update_fields(self):
        from app.core import textbook as tb
        rec = tb.create_textbook("stu1", file_id="f1", title="A")
        updated = tb.update_textbook("stu1", rec["id"], status="ready",
                                      chapter_count=3, concept_count=10,
                                      level="高中", warnings=["w1"])
        self.assertEqual(updated["status"], "ready")
        self.assertEqual(updated["chapter_count"], 3)
        self.assertEqual(updated["level"], "高中")
        self.assertEqual(updated["warnings"], ["w1"])

    def test_update_invalid_status_ignored(self):
        from app.core import textbook as tb
        rec = tb.create_textbook("stu1", file_id="f1", title="A")
        updated = tb.update_textbook("stu1", rec["id"], status="bogus")
        self.assertEqual(updated["status"], "building")  # 非法状态被忽略

    def test_remove(self):
        from app.core import textbook as tb
        rec = tb.create_textbook("stu1", file_id="f1", title="A")
        self.assertTrue(tb.remove_textbook("stu1", rec["id"]))
        self.assertIsNone(tb.find_textbook("stu1", rec["id"]))
        self.assertFalse(tb.remove_textbook("stu1", rec["id"]))  # 再删返回 False


class TestSpecToGraphParams(unittest.TestCase):
    def _spec(self, n_chapters, n_concepts_each):
        return {
            "subject": "测试",
            "chapters": [
                {"name": f"第{i}章", "concepts": [
                    {"name": f"c{i}_{j}", "difficulty": 2}
                    for j in range(n_concepts_each)]}
                for i in range(1, n_chapters + 1)
            ]
        }

    def test_max_chapters_truncation(self):
        from app.agents.knowledge.custom_graph import spec_to_graph
        spec = self._spec(50, 2)
        data, warnings = spec_to_graph(spec, topic_key="t1", source="textbook:f",
                                        max_chapters=5)
        chapters = [n for n in data["nodes"] if n.get("kind") == "chapter"]
        self.assertEqual(len(chapters), 5)

    def test_max_concepts_truncation(self):
        from app.agents.knowledge.custom_graph import spec_to_graph, MAX_CONCEPTS
        spec = self._spec(3, 100)  # 300 概念
        data, warnings = spec_to_graph(spec, topic_key="t2", source="textbook:f",
                                        max_concepts=20)
        self.assertLessEqual(data["concept_count"], 20)
        self.assertTrue(any("超过上限" in w for w in warnings))

    def test_level_valid_stamps_nodes(self):
        from app.agents.knowledge.custom_graph import spec_to_graph
        spec = self._spec(1, 2)
        data, _ = spec_to_graph(spec, topic_key="t3", source="textbook:f",
                                 level="本科")
        self.assertEqual(data["level"], "本科")
        for n in data["nodes"]:
            self.assertEqual(n["level"], "本科")

    def test_level_invalid_falls_back_custom(self):
        from app.agents.knowledge.custom_graph import spec_to_graph, CUSTOM_LEVEL
        spec = self._spec(1, 2)
        data, _ = spec_to_graph(spec, topic_key="t4", source="textbook:f",
                                 level="研究生")  # 非法学段
        self.assertEqual(data["level"], CUSTOM_LEVEL)

    def test_defaults_preserve_old_behavior(self):
        # 不传新形参时，行为与改造前一致（MAX_CHAPTERS/MAX_CONCEPTS/CUSTOM_LEVEL）。
        from app.agents.knowledge.custom_graph import (spec_to_graph, MAX_CHAPTERS,
                                                       MAX_CONCEPTS, CUSTOM_LEVEL)
        spec = self._spec(MAX_CHAPTERS + 5, 1)
        data, _ = spec_to_graph(spec, topic_key="t5", source="material:x")
        chapters = [n for n in data["nodes"] if n.get("kind") == "chapter"]
        self.assertEqual(len(chapters), MAX_CHAPTERS)
        self.assertEqual(data["level"], CUSTOM_LEVEL)


class TestChapterSlicing(unittest.TestCase):
    def test_whole_book_chapter(self):
        from app.agents.knowledge.textbook_builder import whole_book_chapter
        slices = whole_book_chapter("一些文本内容")
        self.assertEqual(len(slices), 1)
        self.assertEqual(slices[0][0], "全册")  # 兜底章名统一短名（跨学段一致）

    def test_locate_chapters(self):
        from app.agents.knowledge.textbook_builder import locate_chapters
        text = "第一章 开始\n内容A\n第二章 继续\n内容B\n第三章 结尾\n内容C"
        slices = locate_chapters(text, ["第一章 开始", "第二章 继续", "第三章 结尾"])
        self.assertEqual(len(slices), 3)
        self.assertIn("内容A", slices[0][1])
        self.assertIn("内容B", slices[1][1])

    def test_locate_chapters_missing_name_returns_empty(self):
        from app.agents.knowledge.textbook_builder import locate_chapters
        self.assertEqual(locate_chapters("文本", ["不存在的章"]), [])

    def test_locate_chapters_tolerates_ideographic_space_and_newline(self):
        from app.agents.knowledge.textbook_builder import locate_chapters
        text = ("目录\n第一章　线性方程组\n第二章　行列式\n"
                "\f第一章\n线性方程组\n正文A\n"
                "\f第二章\t行列式\n正文B")
        slices = locate_chapters(text, ["第一章 线性方程组", "第二章 行列式"])
        self.assertEqual([x[0] for x in slices], ["第一章 线性方程组", "第二章 行列式"])
        self.assertIn("正文A", slices[0][1])
        self.assertNotIn("目录", slices[0][1])
        self.assertIn("正文B", slices[1][1])

    def test_locate_chapters_nfkc_matches_fullwidth_digits(self):
        from app.agents.knowledge.textbook_builder import locate_chapters
        text = "第１章　矩阵\n内容A\n第２章　向量\n内容B"
        slices = locate_chapters(text, ["第1章 矩阵", "第2章 向量"])
        self.assertEqual(len(slices), 2)
        self.assertIn("内容A", slices[0][1])

    def test_deterministic_toc_fallback_extracts_five_chapters(self):
        from app.agents.knowledge.textbook_builder import extract_chapters
        names = [
            "第一章 线性方程组与矩阵", "第二章 方阵的行列式",
            "第三章 向量空间与线性方程组解的结构", "第四章 相似矩阵及二次型",
            "第五章 线性空间与线性变换",
        ]
        toc = "目录\n" + "\n".join(n.replace(" ", "　") for n in names)
        bodies = "\f".join(
            f"{name.replace(' ', '　')}\n第{i}章正文内容" for i, name in enumerate(names, 1)
        )
        slices, toc_text = asyncio.run(extract_chapters(toc + "\f" + bodies, None, None))
        self.assertEqual([x[0] for x in slices], names)
        self.assertIn("第五章 线性空间与线性变换", toc_text)

    def test_long_whole_book_fallback_is_marked_degraded(self):
        from app.agents.knowledge import textbook_builder as builder

        class WholeBookLLM:
            async def complete(self, messages, **kwargs):
                prompt = messages[-1]["content"]
                if '"chapters"' in prompt and "目录或正文片段" in prompt:
                    return '{"chapters":[]}', {}
                if '"subject"' in prompt and '"level"' in prompt:
                    return '{"subject":"数学","level":"本科"}', {}
                return ('{"concepts":[{"name":"测试概念","difficulty":2,'
                        '"description":"说明","aliases":[],"prerequisites":[],'
                        '"definition":"定义","example":"例子"}]}', {})

        warnings: list[str] = []
        spec = asyncio.run(builder._full_path_spec(
            "missing", "missing", "无章节标题的长教材正文。" * 6000,
            None, "测试教材.pdf", WholeBookLLM(), warnings))
        self.assertIsNotNone(spec)
        self.assertTrue(spec["chapter_detection"]["degraded"])
        self.assertTrue(any("长教材未能定位" in warning for warning in warnings))

    def test_long_chapter_excerpt_covers_head_middle_and_tail(self):
        from app.agents.knowledge.textbook_builder import _chapter_excerpt
        text = "HEAD" + "a" * 30000 + "MIDDLE" + "b" * 30000 + "TAIL_POSITIVE_DEFINITE"
        excerpt = _chapter_excerpt(text, cap=24000)
        self.assertLessEqual(len(excerpt), 24000)
        self.assertIn("HEAD", excerpt)
        self.assertIn("MIDDLE", excerpt)
        self.assertIn("TAIL_POSITIVE_DEFINITE", excerpt)
        self.assertIn("<章节中段节选>", excerpt)
        self.assertIn("<章节末段节选>", excerpt)

    def test_extract_chapters_pdf_with_toc(self):
        # 用 fixtures 构造一个带目录的多页 PDF，验证 Tier 1 精确分页。
        from tests.support import pdf_fixtures
        raw = pdf_fixtures.with_outline(
            pdf_fixtures.make_pdf(["第一章封面", "第一章正文导数", "第二章正文积分"]),
            [(1, "第一章 导数", 1), (1, "第二章 积分", 2)])
        from app.agents.knowledge.textbook_builder import extract_chapters_pdf
        result = extract_chapters_pdf(raw)
        self.assertIsNotNone(result)
        slices, toc_text = result
        self.assertEqual(len(slices), 2)
        self.assertIn("导数", slices[0][0])
        self.assertIn("积分", slices[1][0])

    def test_extract_chapters_pdf_no_toc_returns_none(self):
        from tests.support import pdf_fixtures
        raw = pdf_fixtures.make_pdf(["无目录的PDF"])
        from app.agents.knowledge.textbook_builder import extract_chapters_pdf
        self.assertIsNone(extract_chapters_pdf(raw))

    def test_garbage_outline_titles_reject_whole_tier1(self):
        """印刷厂分段书签（34172-0-…_DJD 形态）不是教学单元边界：整个
        Tier 1 弃用，交 Tier 2 LLM 从正文定位（语文选必实测缺陷的类级回归）。"""
        from tests.support import pdf_fixtures
        raw = pdf_fixtures.with_outline(
            pdf_fixtures.make_pdf(["正文A", "正文B", "正文C"]),
            [(1, "34172-0-示范高中教科书语文选择性必修上册_DJD", 1),
             (1, "34172-1-示范高中教科书语文选择性必修上册_DJD", 2)])
        from app.agents.knowledge.textbook_builder import extract_chapters_pdf
        self.assertIsNone(extract_chapters_pdf(raw))

    def test_outline_noise_entries_dropped_but_good_kept(self):
        """混合书签（真章名 + 目录/封面噪声）：噪声条目剔除，真章保留。"""
        from tests.support import pdf_fixtures
        raw = pdf_fixtures.with_outline(
            pdf_fixtures.make_pdf(["封面页", "目录页", "第一章内容", "第二章内容"]),
            [(1, "封面", 1), (1, "目录", 2),
             (1, "第一章 静电场", 3), (1, "第二章 恒定电流", 4)])
        from app.agents.knowledge.textbook_builder import extract_chapters_pdf
        result = extract_chapters_pdf(raw)
        self.assertIsNotNone(result)
        slices, _ = result
        self.assertEqual([s[0] for s in slices], ["第一章 静电场", "第二章 恒定电流"])

    def test_outline_volume_wrapper_title_rejected_with_hint(self):
        """书签=卷文件名包装（"化学反应原理第1章" 等）在卷名提示下判为伪章；
        而剥离书名前缀后是真实标题。"""
        from tests.support import pdf_fixtures
        raw = pdf_fixtures.with_outline(
            pdf_fixtures.make_pdf(["第一章内容", "第二章内容"]),
            [(1, "化学反应原理第1章", 1), (1, "化学反应原理第2章", 2)])
        from app.agents.knowledge.textbook_builder import extract_chapters_pdf
        # 无卷名提示：无法判定 → 保留原名（兼容未知来源书签）
        self.assertIsNotNone(extract_chapters_pdf(raw))
        # 有卷名提示：剥离书名前缀后为真实章名
        result = extract_chapters_pdf(raw, volume_hint="化学反应原理.pdf")
        self.assertIsNotNone(result)
        slices, _ = result
        self.assertEqual([s[0] for s in slices], ["第1章", "第2章"])


class _MockLLM:
    """Returns canned JSON for skeleton / chapter / generate_spec prompts."""

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, **kw):
        self.calls += 1
        prompt = messages[0]["content"]
        if "subject" in prompt and "level" in prompt and "概念清单" not in prompt:
            # skeleton call
            return ('{"subject": "数学", "level": "本科"}', {})
        if "概念清单" in prompt or "核心知识点" in prompt:
            return ('{"concepts": [{"name": "极限", "difficulty": 2, '
                    '"description": "d", "prerequisites": []}, '
                    '{"name": "连续", "difficulty": 3, "prerequisites": ["极限"]}]}', {})
        # generate_spec (fast path)
        return ('{"subject": "数学", "level": "本科", "chapters": ['
                '{"name": "第一章", "concepts": [{"name": "导数", "difficulty": 3}]}]}', {})


class TestFrontMatterExclusion(unittest.TestCase):
    """前置页（封面/扉页/版权/目录）必须被排除在章节正文之外——
    否则版权页/目录文字会被概念抽取当成知识点进图谱（实测污染）。"""

    def test_page_classification(self):
        from app.agents.knowledge.textbook_builder import _page_is_front_matter
        cover = "示范高中教科书\n语文\n必修上册\n示例编写组组织编写\n示例教育出版社"
        self.assertTrue(_page_is_front_matter(cover, 1, 100))
        copyright_page = "图书在版编目（CIP）数据\nISBN 978-7-107-xxxxx-x\n版权所有 侵权必究"
        self.assertTrue(_page_is_front_matter(copyright_page, 2, 100))
        toc = "目 录\n第一单元 青春的价值 …1\n第二单元 劳动光荣 ……23\n第三单元 生命的诗意 ……47"
        self.assertTrue(_page_is_front_matter(toc, 3, 100))
        body = "第一单元 青春的价值\n《沁园春·江畔》毛泽东\n独立寒秋，湘江北去，沙洲头。看万山红遍，层林尽染……" * 3
        self.assertFalse(_page_is_front_matter(body, 4, 100))

    def test_tier1_slices_clamp_to_body(self):
        # 注：fixture 用 ASCII（Helvetica 渲染不了 CJK）
        from tests.support import pdf_fixtures
        from app.agents.knowledge.textbook_builder import extract_chapters_pdf
        raw = pdf_fixtures.with_outline(
            pdf_fixtures.make_pdf(["cover and copyright info",
                                   "CONTENTS\nUnit 1...1\nUnit 2...2",
                                   "Chapter 1 content A", "Chapter 2 content B"]),
            [(1, "第一章 导数", 3), (1, "第二章 积分", 4)])
        result = extract_chapters_pdf(raw)
        self.assertIsNotNone(result)
        slices, _ = result
        self.assertEqual(len(slices), 2)
        self.assertNotIn("copyright", slices[0][1])
        self.assertNotIn("CONTENTS", slices[0][1])
        self.assertIn("Chapter 1 content A", slices[0][1])

    def test_whole_book_fallback_excludes_front_matter(self):
        from app.agents.knowledge.textbook_builder import whole_book_chapter
        text = ("封面\n示例编写组组织编写\n示例教育出版社\n"
                "\f目 录\n第一单元 …1\n第二单元 …2\n"
                "\f真正的正文内容开始了，这里是知识点。")
        slices = whole_book_chapter(text)
        self.assertEqual(len(slices), 1)
        self.assertEqual(slices[0][0], "全册")
        self.assertNotIn("示例编写组组织编写", slices[0][1])
        self.assertNotIn("目 录", slices[0][1])
        self.assertIn("真正的正文内容", slices[0][1])

    def test_locate_chapters_clamps_toc_anchors_to_body(self):
        from app.agents.knowledge.textbook_builder import locate_chapters
        text = ("封面版权页\n"
                "\f目 录\n第一章 导数…1\n第二章 积分…2\n"
                "\f第一章 导数\n内容A\n第二章 积分\n内容B")
        slices = locate_chapters(text, ["第一章 导数", "第二章 积分"])
        self.assertEqual(len(slices), 2)
        # 锚点取第二次出现（正文）；即便只有目录出现，也被钳到正文起点
        self.assertNotIn("目 录", slices[0][1])
        self.assertIn("内容A", slices[0][1])


class TestGraphDesignPass(unittest.TestCase):
    """DS 图谱设计阶段：标签统一/同义归并/跨章继承；失败与开关关闭均降级。"""

    def _spec(self):
        return {"subject": "物理", "level": "高中", "chapters": [
            {"name": "第一章 静电场", "concepts": [
                {"name": "电荷量子化", "difficulty": 2, "prerequisites": []},
                {"name": "库仑定律", "difficulty": 3, "prerequisites": ["电荷量子化"]}]},
            {"name": "第二章 电势", "concepts": [
                {"name": "电势", "difficulty": 3, "prerequisites": []}]},
        ]}

    def test_apply_renames_merges_and_cross_prereq(self):
        import asyncio
        from app.agents.knowledge import textbook_builder as b
        spec = self._spec()
        design = {
            "chapter_labels": [{"index": 1, "name": "第2章 电势能"}],
            "concept_merges": [{"name": "电荷量子化", "into": "电荷的量子化"}],
            "cross_prereq": [{"from": "库仑定律", "to": "电势"}],
        }
        applied = b._apply_graph_design(spec, design)
        self.assertTrue(applied)
        self.assertEqual(spec["chapters"][1]["name"], "第2章 电势能")
        self.assertEqual(spec["chapters"][0]["concepts"][0]["name"], "电荷的量子化")
        self.assertIn("库仑定律", spec["chapters"][1]["concepts"][0]["prerequisites"])

    def test_apply_removes_pseudo_chapter(self):
        from app.agents.knowledge import textbook_builder as b
        spec = self._spec()
        b._apply_graph_design(spec, {"chapter_labels": [{"index": 0, "name": ""}]})
        self.assertEqual(len(spec["chapters"]), 1)
        self.assertEqual(spec["chapters"][0]["name"], "第二章 电势")

    def test_pass_failure_falls_back_silently(self):
        import asyncio
        from app.agents.knowledge import textbook_builder as b
        class BoomLLM:
            async def complete(self, messages, **kw):
                raise RuntimeError("llm down")
        design = asyncio.run(b._graph_design_pass(self._spec(), BoomLLM(), "物理"))
        self.assertEqual(design, {})

    def test_pass_skipped_when_switch_off(self):
        import asyncio
        from unittest.mock import patch
        from app.agents.knowledge import textbook_builder as b
        class SpyLLM:
            calls = 0
            async def complete(self, messages, **kw):
                SpyLLM.calls += 1
                return ('{"chapter_labels":[]}', {})
        with patch.object(b.settings, "graph_design_mode", False):
            design = asyncio.run(b._graph_design_pass(self._spec(), SpyLLM(), "物理"))
        self.assertEqual(design, {})
        self.assertEqual(SpyLLM.calls, 0)


class TestBuildPipeline(unittest.TestCase):
    def setUp(self):
        self._s = _TmpStore()
        self._s.setUp()

    def tearDown(self):
        self._s.tearDown()

    def _seed_library_file(self, student_id, file_id, text, orig_ext=".pdf", raw=b"PDF"):
        """Write a library file (text + orig) + register in library index."""
        from app.core.library import Library, save_library, library_data_dir
        lib = Library(student_id=student_id)
        data = library_data_dir(student_id)
        (data / f"{file_id}.txt").write_text(text, encoding="utf-8")
        if raw:
            (data / f"{file_id}.orig{orig_ext}").write_bytes(raw)
        lib.files.append({"id": file_id, "filename": "test.pdf", "folder_id": "",
                          "char_count": len(text), "chunk_count": 1,
                          "orig_ext": orig_ext, "kind": "textbook"})
        save_library(lib)

    def test_fast_path_small_text_builds_ready(self):
        from app.core import textbook as tb
        from app.agents.knowledge.textbook_builder import build_textbook_graph
        text = "短教材内容" * 10  # < 20000 字
        self._seed_library_file("stu1", "f1", text)
        rec = tb.create_textbook("stu1", file_id="f1", title="短教材")
        llm = _MockLLM()
        asyncio.run(build_textbook_graph("stu1", rec["id"], llm))
        out = tb.find_textbook("stu1", rec["id"])
        self.assertEqual(out["status"], "ready")
        self.assertGreaterEqual(out["concept_count"], 1)
        # 图谱写入 M5.7 store
        from app.agents.knowledge import store as kgs
        self.assertIsNotNone(kgs.load_custom_graph("stu1", rec["topic_key"]))

    def test_full_path_large_text_builds_ready(self):
        # 回归：>20000 字走 _full_path_spec，曾因引用未定义的 subject/level
        # （NameError 被外层吞掉）而恒 graph_failed。
        from app.core import textbook as tb
        from app.agents.knowledge.textbook_builder import build_textbook_graph
        self._seed_library_file("stu1", "f1", "力学 热学 极限 连续。" * 2000,
                                orig_ext="", raw=None)
        rec = tb.create_textbook("stu1", file_id="f1", title="大教材")
        llm = _MockLLM()
        asyncio.run(build_textbook_graph("stu1", rec["id"], llm))
        out = tb.find_textbook("stu1", rec["id"])
        self.assertEqual(out["status"], "ready")
        self.assertGreaterEqual(out["concept_count"], 1)
        # 记录无 subject 时骨架推断补缺
        self.assertEqual(out["subject"], "数学")

    def test_full_path_user_chosen_level_wins(self):
        # 上传时已选学段优先，骨架推断的「本科」不覆盖。
        from app.core import textbook as tb
        from app.agents.knowledge.textbook_builder import build_textbook_graph
        self._seed_library_file("stu1", "f1", "力学 热学 极限 连续。" * 2000,
                                orig_ext="", raw=None)
        rec = tb.create_textbook("stu1", file_id="f1", title="大教材", level="高中")
        asyncio.run(build_textbook_graph("stu1", rec["id"], _MockLLM()))
        out = tb.find_textbook("stu1", rec["id"])
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["level"], "高中")

    def test_graph_disabled_goes_ready_no_graph(self):
        from app.core import textbook as tb
        from app.agents.knowledge import textbook_builder
        self._seed_library_file("stu1", "f1", "教材内容")
        rec = tb.create_textbook("stu1", file_id="f1", title="A")
        with patch.object(textbook_builder.settings, "textbook_graph_enabled", False):
            asyncio.run(textbook_builder.build_textbook_graph("stu1", rec["id"], None))
        out = tb.find_textbook("stu1", rec["id"])
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["chapter_count"], 0)

    def test_llm_failure_marks_graph_failed(self):
        from app.core import textbook as tb
        from app.agents.knowledge import textbook_builder

        class FailLLM:
            async def complete(self, messages, **kw):
                raise RuntimeError("LLM down")
        # 大文本走 full path（逐章），LLM 全失败 → graph_failed
        self._seed_library_file("stu1", "f1", "x" * 25000, orig_ext="", raw=None)
        rec = tb.create_textbook("stu1", file_id="f1", title="大教材")
        # extract_chapters 无 PDF 原件 + LLM TOC 失败 → 整书单章；逐章抽取全失败
        asyncio.run(textbook_builder.build_textbook_graph("stu1", rec["id"], FailLLM()))
        out = tb.find_textbook("stu1", rec["id"])
        self.assertEqual(out["status"], "graph_failed")

    def test_rebuild_replaces_without_hidden_archive(self):
        from app.core import textbook as tb
        from app.agents.knowledge import textbook_builder, store as kgs
        self._seed_library_file("stu1", "f1", "短教材" * 10)
        rec = tb.create_textbook("stu1", file_id="f1", title="A")
        llm = _MockLLM()
        asyncio.run(textbook_builder.build_textbook_graph("stu1", rec["id"], llm))
        self.assertEqual(kgs.archive_count("stu1", rec["topic_key"]), 0)
        # rebuild：版本递增 + 原子替换，旧式 archive 不再增长
        asyncio.run(textbook_builder.build_textbook_graph("stu1", rec["id"], llm))
        self.assertEqual(kgs.archive_count("stu1", rec["topic_key"]), 0)
        self.assertEqual(kgs.load_custom_graph("stu1", rec["topic_key"])["version"], 2)


class TestParallelChapterExtraction(unittest.TestCase):
    """逐章并行抽取：结算严格按章序（chapters_out 与串行一致），并发由
    textbook_pipeline.llm_gate 限流；legacy 模式峰值=1 且准入顺序即章序。"""

    def setUp(self):
        self._s = _TmpStore()
        self._s.setUp()
        from app.core import textbook_pipeline
        self._pipeline = textbook_pipeline
        self._old_policy = textbook_pipeline._RUNTIME.policy

    def tearDown(self):
        self._pipeline._RUNTIME.policy = self._old_policy
        self._s.tearDown()

    def _build(self, mode: str, llm_limit: int):
        """跑一次 _full_path_spec；返回 (spec, 峰值并发, 章调用准入序, 章名顺序)。"""
        from app.core import textbook as tb
        from app.agents.knowledge import textbook_builder

        self._pipeline._RUNTIME.policy = {
            "mode": mode, "build_concurrency": 2, "volume_concurrency": 2,
            "llm_concurrency": llm_limit, "updated_at": 0.0, "version": 1}
        rec = tb.create_textbook("stu1", file_id="f1", title="大教材")
        n = 10
        slices = [(f"第{i}章", f"第{i}章的正文知识点内容。" * 40, (i, i + 1), [])
                  for i in range(1, n + 1)]

        class TrackLLM:
            def __init__(self):
                self.active = 0
                self.peak = 0
                self.admitted: list[int] = []

            async def complete(self, messages, **kw):
                prompt = messages[0]["content"]
                if "核心知识点" not in prompt:
                    if "subject" in prompt and "level" in prompt:
                        return ('{"subject": "数学", "level": "本科"}', {})
                    return ("{}", {})
                idx = next(i for i in range(1, n + 1)
                           if f"第{i}章的正文知识点内容" in prompt)
                llm.active += 1
                llm.peak = max(llm.peak, llm.active)
                llm.admitted.append(idx)
                # 反序延迟：后面的章先完成，检验结算仍按章序。
                await asyncio.sleep((n - idx) * 0.008)
                llm.active -= 1
                return ('{"concepts": [{"name": "C%02d", "difficulty": 2}]}' % idx, {})

        llm = TrackLLM()

        async def fake_extract_chapters(text, raw, llm_arg, volume_hint=""):
            return slices, "目录文本"

        async def drive():
            with patch.object(textbook_builder, "extract_chapters",
                              side_effect=fake_extract_chapters):
                return await textbook_builder._full_path_spec(
                    "stu1", rec["id"], "x" * 30, None, "书.pdf", llm, [])
        spec = asyncio.run(drive())
        return spec, llm.peak, llm.admitted

    def test_parallel_mode_preserves_order_and_overlaps(self):
        spec, peak, admitted = self._build("parallel", 4)
        self.assertIsNotNone(spec)
        # 结算严格按章序：概念与章一一对应（完成顺序被人为打乱仍不乱序）。
        self.assertEqual([c["name"] for c in spec["chapters"]],
                         [f"第{i}章" for i in range(1, 11)])
        self.assertEqual([c["concepts"][0]["name"] for c in spec["chapters"]],
                         [f"C{i:02d}" for i in range(1, 11)])
        self.assertGreaterEqual(peak, 2)   # 确实并行
        self.assertLessEqual(peak, 4)      # 门限生效

    def test_legacy_mode_is_strictly_serial(self):
        spec, peak, admitted = self._build("legacy", 4)
        self.assertIsNotNone(spec)
        self.assertEqual([c["concepts"][0]["name"] for c in spec["chapters"]],
                         [f"C{i:02d}" for i in range(1, 11)])
        self.assertEqual(peak, 1)
        # FIFO 准入顺序 == 任务创建顺序 == 章序（与历史串行 for 循环一致）。
        self.assertEqual(admitted, list(range(1, 11)))


class TestTextbookOutline(unittest.TestCase):
    def setUp(self):
        self._s = _TmpStore()
        self._s.setUp()

    def tearDown(self):
        self._s.tearDown()

    def test_outline_derived_from_payload(self):
        from app.core import textbook as tb
        from app.agents.knowledge import textbook_builder, store as kgs
        from app.core.library import Library, save_library, library_data_dir
        lib = Library(student_id="stu1")
        (library_data_dir("stu1") / "f1.txt").write_text("短教材" * 10, encoding="utf-8")
        lib.files.append({"id": "f1", "filename": "t", "folder_id": "",
                          "char_count": 10, "chunk_count": 1, "orig_ext": "", "kind": "textbook"})
        save_library(lib)
        rec = tb.create_textbook("stu1", file_id="f1", title="A")
        asyncio.run(textbook_builder.build_textbook_graph(
            "stu1", rec["id"], _MockLLM()))
        outline = textbook_builder.textbook_outline("stu1", rec["id"])
        self.assertIsNotNone(outline)
        self.assertGreaterEqual(len(outline), 1)
        # 每个章节项含 chapter/concept_count/concepts
        ch0 = outline[0]
        self.assertIn("chapter", ch0)
        self.assertIn("concepts", ch0)


if __name__ == "__main__":
    unittest.main()
