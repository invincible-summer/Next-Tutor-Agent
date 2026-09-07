"""P5a 修复测试（update_plan §12.4/§12.7）：

- A1 页码对齐：OCR 合并保留空页占位 → 页序与物理页一一对应（页码不漂移）。
- A2 逐页判定：文本层达标页不被 OCR 降质覆盖，稀疏页才 OCR（混合书逐页择优）。
- A3 目录陷阱：locate_chapters 优先章节名第二次出现（正文标题）而非首次（目录）。
- A4 启动收割：reap_stale_builds 把残留 building 置 graph_failed，ready 不动。
- A5 写回：OCR 写回后 library 元数据 char_count/chunk_count 同步（原子写路径）。
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_pdf_ocr import _make_pdf


class TestPageAlignment(unittest.TestCase):
    """A1：合并结果页数 == 物理页数，空页以 "" 占位。"""

    def test_mixed_sync_keeps_empty_page_placeholder(self):
        from app.core.pdf_ocr import ocr_pdf_pages_mixed_sync
        raw = _make_pdf(["", "", ""], scanned=True)  # 3 页全空白（扫描）
        calls = [0]

        def fake_ocr(png: bytes) -> str:
            calls[0] += 1
            return "" if calls[0] == 2 else f"第{calls[0]}次识别"  # 中间页识别失败

        pages, stats = ocr_pdf_pages_mixed_sync(raw, fake_ocr)
        self.assertEqual(len(pages), 3)  # 页数不漂移
        self.assertEqual(pages[1], "")   # 失败页占位保留
        self.assertEqual("\f".join(pages).count("\f"), 2)
        self.assertEqual(stats["ocr_failed"], 1)

    def test_sync_fallback_preserves_page_count(self):
        # file_parser 同步段：3 页扫描书，中间页 OCR 失败 → 结果仍 3 页（2 个 \f）。
        from app.core import file_parser, ocr, config
        raw = _make_pdf(["", "", ""], scanned=True)
        calls = [0]

        def fake_tess(png: bytes, *, psm: int = 6) -> str:
            calls[0] += 1
            return "" if calls[0] == 2 else "识别文本"

        with patch.object(config.settings, "pdf_ocr_mode", "auto"), \
             patch("shutil.which", return_value="/usr/bin/tesseract"), \
             patch.object(ocr, "_tesseract_ocr", side_effect=fake_tess):
            text = file_parser.extract_text("s.pdf", raw)
        self.assertEqual(text.count("\f"), 2)  # 3 页对齐
        self.assertIn("识别文本", text)


class TestPerPageMixed(unittest.TestCase):
    """A2：逐页判定——达标页保留文本层，稀疏页才进 OCR。"""

    def _mixed_pdf(self) -> bytes:
        # 2 页达标 ASCII 文本 + 1 页稀疏（自动 fallback 文本 "page 3" 仅 6 字符）
        return _make_pdf(["dense ascii content for page one " * 8,
                          "dense ascii content for page two " * 8,
                          ""])  # 第三页 insert_text fallback "page 3"（< 20 字符）

    def test_sync_mixed_only_ocr_sparse_pages(self):
        from app.core.pdf_ocr import ocr_pdf_pages_mixed_sync
        raw = self._mixed_pdf()
        ocr_calls = [0]

        def fake_ocr(png: bytes) -> str:
            ocr_calls[0] += 1
            return "OCR补全的扫描页"

        pages, stats = ocr_pdf_pages_mixed_sync(raw, fake_ocr)
        self.assertEqual(ocr_calls[0], 1)          # 只 OCR 稀疏的 1 页
        self.assertEqual(stats["sparse"], 1)
        self.assertIn("dense ascii", pages[0])      # 达标页文本层原样保留
        self.assertIn("dense ascii", pages[1])
        self.assertEqual(pages[2], "OCR补全的扫描页")  # 稀疏页被 OCR 补全

    def test_async_mixed_stats(self):
        from app.core.pdf_ocr import ocr_pdf_pages_mixed
        raw = self._mixed_pdf()

        async def fake_ocr(png: bytes) -> str:
            return "异步OCR"

        pages, stats = asyncio.run(ocr_pdf_pages_mixed(raw, fake_ocr))
        self.assertEqual(len(pages), 3)
        self.assertEqual(stats, {"sparse": 1, "ocr_done": 1, "ocr_failed": 0})
        self.assertEqual(pages[2], "异步OCR")
        self.assertIn("dense ascii", pages[0])

    def test_extract_text_mixed_book_merges_both_layers(self):
        # 端到端（同步段）：混合书合并后同时包含文本层页与 OCR 页内容。
        from app.core import file_parser, ocr, config
        raw = self._mixed_pdf()
        with patch.object(config.settings, "pdf_ocr_mode", "auto"), \
             patch("shutil.which", return_value="/usr/bin/tesseract"), \
             patch.object(ocr, "_tesseract_ocr", return_value="扫描页识别结果"):
            text = file_parser.extract_text("m.pdf", raw)
        self.assertIn("dense ascii", text)      # 文本层页没丢
        self.assertIn("扫描页识别结果", text)     # 稀疏页被补全

    def test_dense_book_still_no_ocr(self):
        # 全达标页的书：auto 模式不触发任何 OCR（spy 混合函数）。
        from app.core import file_parser, pdf_ocr, config
        raw = _make_pdf(["dense ascii content for page one " * 8,
                         "dense ascii content for page two " * 8])
        with patch.object(config.settings, "pdf_ocr_mode", "auto"), \
             patch("shutil.which", return_value="/usr/bin/tesseract"), \
             patch.object(pdf_ocr, "ocr_pdf_pages_mixed_sync") as m2, \
             patch.object(pdf_ocr, "ocr_pdf_pages_sync") as m1:
            text = file_parser.extract_text("n.pdf", raw)
        self.assertIn("dense ascii", text)
        m1.assert_not_called()
        m2.assert_not_called()

    def test_mixed_respects_page_texts_override(self):
        # 重建场景：当前文本（既往 OCR 成果）已稠密 → 即使原始 PDF 无文本层，
        # 也不重复 OCR（实测发现的浪费：rebuild 曾把全书重 OCR 一遍）。
        from app.core.pdf_ocr import ocr_pdf_pages_mixed
        raw = _make_pdf(["", "", ""], scanned=True)  # 原始文本层全空
        dense = [f"已识别的第{i}页内容 " * 5 for i in range(3)]

        async def fake_ocr(png: bytes) -> str:
            raise AssertionError("不应触发 OCR")

        pages, stats = asyncio.run(ocr_pdf_pages_mixed(raw, fake_ocr, page_texts=dense))
        self.assertEqual(stats, {"sparse": 0, "ocr_done": 0, "ocr_failed": 0})
        self.assertEqual(pages, dense)  # 原样返回


class TestLocateChaptersTocTrap(unittest.TestCase):
    """A3：章节名先现于目录页时，切片锚定正文区。"""

    def test_second_occurrence_preferred(self):
        from app.agents.knowledge.textbook_builder import locate_chapters
        text = ("目  录\n第一章 导数 ...... 1\n第二章 积分 ...... 5\n"
                "\f第一章 导数\n导数正文内容\n第二章 积分\n积分正文内容")
        slices = locate_chapters(text, ["第一章 导数", "第二章 积分"])
        self.assertEqual(len(slices), 2)
        self.assertIn("导数正文内容", slices[0][1])
        self.assertNotIn("目  录", slices[0][1])  # 目录区不再混入切片
        self.assertIn("积分正文内容", slices[1][1])

    def test_single_occurrence_still_works(self):
        # 无目录的小文档：名字只出现一次，行为与旧版一致。
        from app.agents.knowledge.textbook_builder import locate_chapters
        text = "第一章 开始\n内容A\n第二章 继续\n内容B"
        slices = locate_chapters(text, ["第一章 开始", "第二章 继续"])
        self.assertEqual(len(slices), 2)
        self.assertIn("内容A", slices[0][1])


class TestReapStaleBuilds(unittest.TestCase):
    """A4：启动收割。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        import app.core.textbook as tb
        self._tb = tb
        self._orig_dir = tb._LIBRARY_DIR
        tb._LIBRARY_DIR = Path(self._tmp.name) / "library"

    def tearDown(self):
        self._tb._LIBRARY_DIR = self._orig_dir
        self._tmp.cleanup()

    def test_reap_building_marks_graph_failed(self):
        tb = self._tb
        rec1 = tb.create_textbook("stu1", file_id="f1", title="构建中的书")
        rec2 = tb.create_textbook("stu1", file_id="f2", title="已完成的书")
        tb.update_textbook("stu1", rec2["id"], status="ready",
                           chapter_count=3, concept_count=10)
        reaped = tb.reap_stale_builds()
        self.assertEqual(reaped, 1)
        out1 = tb.find_textbook("stu1", rec1["id"])
        self.assertEqual(out1["status"], "graph_failed")
        self.assertIn("重启", out1["error"])
        out2 = tb.find_textbook("stu1", rec2["id"])
        self.assertEqual(out2["status"], "ready")  # ready 不受影响

    def test_reap_idempotent_and_empty(self):
        self.assertEqual(self._tb.reap_stale_builds(), 0)  # 无记录不崩
        rec = self._tb.create_textbook("stu2", file_id="f9", title="书")
        self._tb.update_textbook("stu2", rec["id"], status="ready")
        self.assertEqual(self._tb.reap_stale_builds(), 0)  # 无 building


class TestOcrWritebackMetadata(unittest.TestCase):
    """A5：OCR 写回后 library 元数据同步（char_count/chunk_count/chunks）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        import app.core.textbook as tb
        import app.core.library as lib
        import app.agents.knowledge.store as kgs
        self._tb, self._lib, self._kgs = tb, lib, kgs
        self._orig = {
            "tb": tb._LIBRARY_DIR, "lib": lib._LIBRARY_DIR,
            "kg": kgs._KG_DIR, "custom": kgs._CUSTOM_DIR,
        }
        tb._LIBRARY_DIR = root / "library"
        lib._LIBRARY_DIR = root / "library"
        kgs._KG_DIR = root / "knowledge"
        kgs._CUSTOM_DIR = root / "knowledge" / "custom"

    def tearDown(self):
        self._tb._LIBRARY_DIR = self._orig["tb"]
        self._lib._LIBRARY_DIR = self._orig["lib"]
        self._kgs._KG_DIR = self._orig["kg"]
        self._kgs._CUSTOM_DIR = self._orig["custom"]
        self._tmp.cleanup()

    def test_mixed_textbook_ocr_writeback_merges_and_updates_meta(self):
        """混合书教材：达标页保留 + 稀疏页 OCR 补全，.txt 与元数据同步。"""
        from app.agents.knowledge import textbook_builder
        from app.core import ocr, textbook_ocr
        from app.core.library import Library, save_library, library_data_dir, load_library
        student_id, file_id = "stu1", "f1"
        dense = "dense ascii content " * 20
        raw = _make_pdf([dense, ""])  # 第 2 页稀疏（fallback "page 2"）
        lib = Library(student_id=student_id)
        data = library_data_dir(student_id)
        text_layer = dense + "\fpage 2"  # 上传时文本层（第 2 页仅 6 字符）
        (data / f"{file_id}.txt").write_text(text_layer, encoding="utf-8")
        (data / f"{file_id}.orig.pdf").write_bytes(raw)
        lib.files.append({"id": file_id, "filename": "mixed.pdf", "folder_id": "",
                          "char_count": len(text_layer), "chunk_count": 1,
                          "orig_ext": ".pdf", "kind": "textbook"})
        save_library(lib)
        rec = self._tb.create_textbook(student_id, file_id=file_id, title="混合教材")

        async def fake_ocr_page(*args, **kwargs):
            return textbook_ocr.ocr.TextbookOCRResult(
                True, text="OCR补全的第二页内容", attempt=kwargs.get("attempt", 1))

        class LLM:
            async def complete(self, messages, **kw):
                return ('{"subject":"数学","level":"本科","chapters":'
                        '[{"name":"第一章","concepts":[{"name":"极限","difficulty":2}]}]}', {})

        with patch.object(textbook_ocr.ocr, "textbook_ocr_page_api", side_effect=fake_ocr_page), \
             patch.object(textbook_builder.settings, "pdf_ocr_mode", "auto"):
            asyncio.run(textbook_builder.build_textbook_graph(student_id, rec["id"], LLM()))

        out = self._tb.find_textbook(student_id, rec["id"])
        self.assertEqual(out["status"], "ready")
        txt = (library_data_dir(student_id) / f"{file_id}.txt").read_text(encoding="utf-8")
        self.assertIn("dense ascii", txt)          # 达标页文本层保留
        self.assertIn("OCR补全的第二页内容", txt)    # 稀疏页被 OCR 补全
        meta = load_library(student_id).find_file(file_id)
        self.assertEqual(meta["char_count"], len(txt))   # 元数据同步
        self.assertGreater(meta["chunk_count"], 0)


class TestTier1TextSlicing(unittest.TestCase):
    """Tier 1 增强：扫描书用书签目录页码切（OCR）文本；章粒度层级偏好。"""

    def _pdf_with_toc(self, n_pages: int, toc: list[list]) -> bytes:
        import fitz
        doc = fitz.open()
        for _ in range(n_pages):
            doc.new_page()  # 空白页（模拟扫描件：get_text 为空）
        doc.set_toc(toc)
        raw = doc.tobytes()
        doc.close()
        return raw

    def test_scanned_book_slices_by_toc_into_provided_text(self):
        from app.agents.knowledge.textbook_builder import extract_chapters_pdf
        # 10 页空白 PDF，书签：第1章 p3、第2章 p7；OCR 文本按页构造标记
        raw = self._pdf_with_toc(10, [[1, "第1章 运动", 3], [1, "第2章 力", 7]])
        ocr_pages = [f"OCR第{i+1}页内容 " * 5 for i in range(10)]
        text = "\f".join(ocr_pages)
        out = extract_chapters_pdf(raw, text)
        self.assertIsNotNone(out)
        slices, toc_text = out
        self.assertEqual([s[0] for s in slices], ["第1章 运动", "第2章 力"])
        self.assertIn("OCR第3页", slices[0][1])    # 第1章从第 3 页开始
        self.assertNotIn("OCR第7页", slices[0][1])  # 第1章到第 6 页止
        self.assertIn("OCR第7页", slices[1][1])

    def test_chapter_granularity_level_preferred(self):
        from app.agents.knowledge.textbook_builder import extract_chapters_pdf
        # level1 是「篇」容器，level2 才是章——应选 level2。
        toc = [[1, "第1篇 力学", 1], [2, "第1章 运动", 1], [2, "第2章 力", 6],
               [1, "第2篇 热学", 8], [2, "第3章 温度", 8]]
        raw = self._pdf_with_toc(10, toc)
        out = extract_chapters_pdf(raw)
        # 空白页无文本 → None（切片为空）；用传入文本验证层级选择
        self.assertIsNone(out)
        text = "\f".join(f"第{i+1}页正文 " * 5 for i in range(10))
        out = extract_chapters_pdf(raw, text)
        self.assertIsNotNone(out)
        slices, _ = out
        titles = [s[0] for s in slices]
        self.assertEqual(titles, ["第1章 运动", "第2章 力", "第3章 温度"])

    def test_beyond_range_entries_skipped(self):
        # OCR 截断：文本只有 5 页，书签第2章在 p8 → 跳过超范围条目。
        from app.agents.knowledge.textbook_builder import extract_chapters_pdf
        raw = self._pdf_with_toc(10, [[1, "第1章 运动", 1], [1, "第2章 力", 8]])
        text = "\f".join(f"第{i+1}页正文 " * 5 for i in range(5))
        out = extract_chapters_pdf(raw, text)
        self.assertIsNone(out)  # 只剩 1 个有效切片 → 回退（len<2）


class TestOutlinePartOfCaseFix(unittest.TestCase):
    """outline 派生：payload 边类型是小写 'part_of' 时也能归组概念。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        import app.core.textbook as tb
        import app.core.library as lib
        import app.agents.knowledge.store as kgs
        self._tb, self._lib, self._kgs = tb, lib, kgs
        self._orig = {"tb": tb._LIBRARY_DIR, "lib": lib._LIBRARY_DIR,
                      "kg": kgs._KG_DIR, "custom": kgs._CUSTOM_DIR}
        tb._LIBRARY_DIR = root / "library"
        lib._LIBRARY_DIR = root / "library"
        kgs._KG_DIR = root / "knowledge"
        kgs._CUSTOM_DIR = root / "knowledge" / "custom"

    def tearDown(self):
        self._tb._LIBRARY_DIR = self._orig["tb"]
        self._lib._LIBRARY_DIR = self._orig["lib"]
        self._kgs._KG_DIR = self._orig["kg"]
        self._kgs._CUSTOM_DIR = self._orig["custom"]
        self._tmp.cleanup()

    def test_outline_groups_lowercase_part_of(self):
        from app.agents.knowledge import textbook_builder
        rec = self._tb.create_textbook("stu1", file_id="f1", title="书")
        payload = {
            "topic": "书", "topic_key": rec["topic_key"],
            "nodes": [
                {"id": "custom.x.ch1", "name": "第1章", "kind": "chapter"},
                {"id": "custom.x.c1", "name": "质点", "kind": "concept"},
                {"id": "custom.x.c2", "name": "速度", "kind": "concept"},
            ],
            "edges": [
                {"source": "custom.x.c1", "target": "custom.x.ch1", "type": "part_of"},
                {"source": "custom.x.c2", "target": "custom.x.ch1", "type": "part_of"},
            ],
            "contents": [],
        }
        self._kgs.save_custom_graph("stu1", rec["topic_key"], payload)
        outline = textbook_builder.textbook_outline("stu1", rec["id"])
        self.assertEqual(len(outline), 1)
        self.assertEqual(outline[0]["concept_count"], 2)
        self.assertIn("质点", outline[0]["concepts"])


    def test_rebuild_skips_ocr_when_text_dense(self):
        """覆盖推导：既往 OCR 已让文本稠密 → rebuild 完全跳过 OCR。"""
        from app.agents.knowledge import textbook_builder
        from app.core import ocr
        from app.core.library import Library, save_library, library_data_dir
        student_id, file_id = "stu1", "f2"
        raw = _make_pdf(["", "", ""], scanned=True)  # 原始 PDF 无文本层
        lib = Library(student_id=student_id)
        data = library_data_dir(student_id)
        dense = "\f".join(f"第{i}章 极限与连续的内容 " * 6 for i in range(1, 4))
        (data / f"{file_id}.txt").write_text(dense, encoding="utf-8")
        (data / f"{file_id}.orig.pdf").write_bytes(raw)
        lib.files.append({"id": file_id, "filename": "scan.pdf", "folder_id": "",
                          "char_count": len(dense), "chunk_count": 1,
                          "orig_ext": ".pdf", "kind": "textbook"})
        save_library(lib)
        rec = self._tb.create_textbook(student_id, file_id=file_id, title="扫描教材")

        async def no_ocr(png):
            raise AssertionError("稠密文本不应触发 OCR")

        class LLM:
            async def complete(self, messages, **kw):
                return ('{"subject":"物理","level":"本科","chapters":'
                        '[{"name":"第1章","concepts":[{"name":"极限","difficulty":2}]}]}', {})

        with patch.object(ocr, "ocr_page_image", side_effect=no_ocr), \
             patch.object(textbook_builder.settings, "pdf_ocr_mode", "auto"):
            asyncio.run(textbook_builder.build_textbook_graph(student_id, rec["id"], LLM()))
        out = self._tb.find_textbook(student_id, rec["id"])
        self.assertEqual(out["status"], "ready")


if __name__ == "__main__":
    unittest.main()
