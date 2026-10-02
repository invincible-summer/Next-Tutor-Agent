"""P9 R1：查询核提取级联回归（2026-08-26 取证账号 账号取证问句）。

旧 `_core_query` 把「X讲了什么」的内容核吃成「了什」「了》」：
- 含「什么」的任意课文段落（局中人）拿 0.9 置信，真课文 chunk 被丢弃；
- 空词项让 48 个候选全部 no_absolute_evidence → 库里有全文却 NOT_FOUND。
"""
import unittest

from app.core.evidence_gate import _has_content, effective_query, normalize_query


class EffectiveQueryForensicTest(unittest.TestCase):
    """8 个取证问句的核提取逐条回归。"""

    def test_na_la_zhu_yi_question(self):
        # 旧：'了什'（terms=['了什','什']，局中人 0.901 压过真课文）
        self.assertEqual(effective_query("借鉴主义讲了什么"), "借鉴主义")
        self.assertIn("借鉴", normalize_query("借鉴主义讲了什么"))

    def test_he_tang_yue_se_bookended(self):
        # 旧：'了》' → 空词项 → 48 候选全灭 → 假 NOT_FOUND
        core = effective_query("《湖畔夜色讲了什么》")
        self.assertIn("湖畔夜色", core.replace("《", "").replace("》", ""))
        terms = normalize_query("《湖畔夜色讲了什么》")
        self.assertTrue(any("湖畔" == t or "夜色" == t for t in terms), terms)

    def test_short_title_variant_still_works(self):
        # 「《湖畔夜色讲》」7 字：不触发预检索是 material_signals 的职责，
        # 但一旦进来，核提取必须产出可用词项。
        terms = normalize_query("《湖畔夜色讲》")
        self.assertTrue(any("湖畔" == t or "夜色" == t for t in terms), terms)

    def test_physics_queries(self):
        self.assertEqual(effective_query("洛伦兹变化是什么"), "洛伦兹变化")
        self.assertEqual(effective_query("伽利略变化是什么"), "伽利略变化")

    def test_chinese_lesson_queries(self):
        core = effective_query("《我与小城》是什么主题")
        self.assertIn("小城", core)
        core = effective_query("《沁园春江畔》是什么")
        self.assertIn("沁园春", core)
        self.assertEqual(effective_query("对数运算律是什么"), "对数运算律")

    def test_verb_tail_capture_kept(self):
        # 宾语后置问句仍走尾捕获（旧路径的正确场景），且不被级联破坏。
        self.assertEqual(effective_query("这本书讲了导数吗"), "导数")
        self.assertEqual(effective_query("我的教材里有没有导数"), "导数")
        self.assertEqual(effective_query("课文是否讲到线性代数"), "讲到线性代数")

    def test_legacy_regressions(self):
        # 2026-08-15「导数高中要学点什么」与「什么是矩阵」回归不变。
        self.assertEqual(effective_query("导数高中要学点什么"), "导数")
        self.assertEqual(normalize_query("什么是矩阵"), ["矩阵"])
        # 2026-08-23「沁园春江畔在教材哪一页」：非问句核 + 短语命中路径不变。
        core = effective_query("沁园春江畔在教材哪一页")
        self.assertIn("沁园春", core)
        self.assertIn("江畔", core)

    def test_never_collapses_to_empty_terms(self):
        for query in ["？？？？", "《》讲了什么", "。", "讲了什么", "是什么"]:
            terms = normalize_query(query)
            # 纯标点/纯虚词问句允许为空，但有效查询绝不因提取 bug 坍缩。
            if query.strip("？?。"):
                self.assertTrue(terms or not _has_content(query), (query, terms))

    def test_has_content_guard(self):
        self.assertTrue(_has_content("借鉴主义"))
        self.assertTrue(_has_content("《湖畔夜色》"))
        self.assertFalse(_has_content("了什"))
        self.assertFalse(_has_content("什么》"))
        self.assertFalse(_has_content("《》"))


if __name__ == "__main__":
    unittest.main()
