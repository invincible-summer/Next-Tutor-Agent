"""P9 R2：置信度重校准、title_match、FOUND/PARTIAL 分级与乱码排除。

合成迷你语料复刻 取证的失败场景：
- 《装在套子里的人》块（含「什么」）不得再压过《借鉴主义》块；
- 词本体不含篇名的《沁园春·江畔》靠 lesson 标题信号进入候选；
- 洛伦兹变换定义块排序高于洛伦兹力应用块；
- mojibake 候选被排除且计入 garble_text_layer。
"""
import unittest

from app.core.evidence_gate import apply_evidence_gate, evidence_excerpt


def _chunk(text, *, source="教材.pdf", page=1, printed=None, lesson=None,
           is_lesson=False, section_path=(), block_types=(), score=1.0,
           chapter="", section=""):
    return {"text": text, "source": source, "filename": source,
            "file_id": "f1", "chunk_id": f"f1#{page}", "index": page,
            "page": page, "printed_page": printed, "score": score,
            "bm25_score": score, "lesson": lesson, "is_lesson": is_lesson,
            "section_path": list(section_path), "block_types": list(block_types),
            "chapter": chapter, "section": section}


TAOTAO_TEXT = ("和他那苍白的小脸上的眼镜，降服了我们，我们只好让步，"
               "减低彼得洛夫和叶果洛夫的品行分数。这种有什么样的离别呢。")
NALA_TEXT = ("中国一向是所谓「闭关主义」，自己不去，别人也不许来。"
             "我们要运用脑髓，放出眼光，自己来拿！")
QINYUAN_BODY = ("独立寒秋，湘江北去，沙洲头。看万山红遍，层林尽染；"
                "漫江碧透，百舸争流。鹰击长空，鱼翔浅底，万类霜天竞自由。")
LORENTZ_DEF = ("洛伦兹变换的定义：两个惯性参考系之间的坐标变换关系，"
               "式（8.23）给出其完整表达式 $x'=\\gamma(x-vt)$。")
LORENTZ_FORCE = ("洛伦兹力是运动电荷在磁场中受到的力，$F=qv\\times B$，"
                 "方向由左手定则确定。")
FORMULA_FRAGMENT = ("50)所表示的 $\\boldsymbol{p}$ 和 $E/c^2$ 的变换关系"
                    "和洛伦兹变换式（8.23）一致。")
GARBLED = ("犃犅犆犇犈犉犌犎犐犑犓犔犕犖犗犘犙犚犛犜犝犞犠犡犢犤犫"
           "犪犫犮犱犲犳犵犻犼犽犾犿狀狏狏犃犅犆犇犈犉")


class GateCalibrationTest(unittest.TestCase):
    def test_nalazhuyi_beats_taotaoren(self):
        candidates = [
            _chunk(TAOTAO_TEXT, page=121, printed=114, score=9.0,
                   section="装在套子里的人"),
            _chunk(NALA_TEXT, page=101, printed=94, score=8.0,
                   section="借鉴主义", lesson="借鉴主义"),
        ]
        gate = apply_evidence_gate("借鉴主义讲了什么", candidates, top_k=2)
        self.assertFalse(gate.no_hit)
        self.assertEqual(gate.tier, "found")
        self.assertIn("闭关主义", gate.selected[0]["text"],
                      "top-1 必须是《借鉴主义》正文块")
        taotao = [c for c in gate.selected if "降服" in c["text"]]
        self.assertFalse(taotao, "局中人块（含「什么」）不得再入选")

    def test_bookended_question_finds_lesson(self):
        candidates = [
            _chunk("这几天心里颇不宁静。今晚在院子里坐着乘凉，忽然想起日日走过的荷塘。",
                   page=116, printed=109, lesson="湖畔夜色", is_lesson=True,
                   section_path=["第七单元", "湖畔夜色"]),
            _chunk("单元学习任务：阅读欣赏这些作品，从你最有感触的一点出发。",
                   page=136, printed=129, score=6.0),
        ]
        gate = apply_evidence_gate("《湖畔夜色讲了什么》", candidates, top_k=2)
        self.assertFalse(gate.no_hit, "库里有课文却 NOT_FOUND（取证样本2）")
        self.assertEqual(gate.tier, "found")
        self.assertIn("荷塘", gate.selected[0]["text"] + str(gate.selected[0].get("lesson")))

    def test_poem_body_enters_via_title_match(self):
        # 词本体不含「沁园春/长沙」词面（取证样本7：42 候选全灭），
        # lesson/section 标题命中后必须进入候选且置信不低于周边活动块。
        candidates = [
            _chunk(QINYUAN_BODY, page=36, printed=2, lesson="沁园春·江畔",
                   is_lesson=True, section_path=["第一单元", "沁园春·江畔"],
                   score=1.0),
            _chunk("单元学习任务：读、欣赏这些作品，从你最有感触的一点出发。",
                   page=38, printed=4, score=5.0),
        ]
        gate = apply_evidence_gate("《沁园春江畔》是什么", candidates, top_k=2)
        self.assertFalse(gate.no_hit)
        poem = [c for c in gate.selected if "独立寒秋" in c["text"]]
        self.assertTrue(poem, "词本体必须靠标题信号入选")
        self.assertGreaterEqual(poem[0]["confidence"], 0.45,
                                "课题标题整串命中应达「中」档")

    def test_lorentz_definition_outranks_force(self):
        candidates = [
            _chunk(LORENTZ_FORCE, page=123, score=9.0, chapter="第17章 磁场和它的源"),
            _chunk(LORENTZ_DEF, page=280, score=8.0,
                   chapter="第8章 狭义相对论基础", section="洛伦兹变换",
                   section_path=["第8章 狭义相对论基础", "洛伦兹变换"],
                   block_types=["definition"]),
            _chunk(FORMULA_FRAGMENT, page=280, score=7.0,
                   chapter="第8章 狭义相对论基础"),
        ]
        gate = apply_evidence_gate("洛伦兹变化是什么", candidates, top_k=3)
        self.assertFalse(gate.no_hit)
        self.assertIn("坐标变换", gate.selected[0]["text"],
                      "定义块必须排第一（取证样本4：公式残片 top-1）")

    def test_garbled_candidates_excluded(self):
        candidates = [
            _chunk(GARBLED, page=10, score=9.0),
            _chunk(NALA_TEXT, page=101, score=2.0, lesson="借鉴主义",
                   section="借鉴主义"),
        ]
        gate = apply_evidence_gate("借鉴主义", candidates, top_k=2)
        self.assertFalse(any("犃犅" in c["text"] for c in gate.selected))
        self.assertGreaterEqual(gate.drop_reasons.get("garble_text_layer", 0), 1)

    def test_partial_tier_instead_of_not_found(self):
        # 只有弱信号候选（词项部分命中但置信低于阈值）→ partial，不再 NOT_FOUND。
        weak = _chunk("对数的运算讲述运算法则与换底公式。", page=131, score=1.0,
                      section="4.3.2 对数的运算")
        gate = apply_evidence_gate("对数运算律是什么推导过程", [weak], top_k=2)
        if gate.no_hit:
            self.fail("弱信号候选不得整批转 NOT_FOUND")
        self.assertEqual(gate.tier, "partial")
        self.assertTrue(gate.selected[0].get("partial"))
        self.assertEqual(gate.selected[0].get("confidence_tier"), "low")

    def test_true_not_found_when_zero_signal(self):
        blank = _chunk("本章小结：向量与几何的代数化。", page=65, score=1.0)
        gate = apply_evidence_gate("借鉴主义", [blank], top_k=2)
        self.assertTrue(gate.no_hit)
        self.assertEqual(gate.tier, "not_found")
        self.assertEqual(gate.drop_reasons.get("no_absolute_evidence"), 1)

    def test_formula_numbering_not_split(self):
        text = ("相对论中动量与能量的关系如式（8.50）所表示。"
                "$\\boldsymbol{p}$ 和 $E/c^2$ 的变换关系与洛伦兹变换式（8.23）一致。"
                "详见 p.280 的推导章节。")
        excerpt = evidence_excerpt(text, ["洛伦兹"])
        self.assertNotIn("50)所表示", excerpt.replace("（8.50）所表示", "@@"))
        self.assertIn("洛伦兹", excerpt)

    def test_definition_centered_excerpt(self):
        text = ("前面的章节讨论了经典力学的适用范围，这在历史上经历了很多争论。"
                "洛伦兹变换的定义：两个惯性参考系之间的坐标变换关系，保持真空光速不变。"
                "后续章节将讨论其应用场景与实验验证，此处不再展开。")
        excerpt = evidence_excerpt(text, ["洛伦兹"], limit=300)
        self.assertIn("定义", excerpt)
        self.assertIn("坐标变换", excerpt)


if __name__ == "__main__":
    unittest.main()
