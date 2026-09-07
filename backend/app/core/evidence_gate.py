"""Deterministic relevance/evidence gate for knowledge retrieval.

Ranking is allowed to recall broadly, but this module decides whether a hit is
strong enough to be shown to the learner or injected into model context.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .retriever import tokenize

_STOP = set("的了是和与及中请我你他她它吗么呢啊呀吧把被从就都也而或于")
# 「对/在」作单字是虚词，但作 bigram 首字的「对数/对称/对应/在场」是关键
# 学科术语——只参与单字过滤，不参与 bigram 边缘过滤（对数运算律取证：
# 「对数」被旧规则丢弃后，空间向量块反而压过 4.3.2 对数的运算）。
_UNIGRAM_STOP = _STOP | set("在对")
_META = re.compile(r"(?:目录|contents|copyright|版权|isbn|cip|中国版本图书馆|定价|出版|版权所有)", re.I)
_HEADER = re.compile(r"^(?:第\s*\d+\s*页|[-—_\s]*\d{1,4}[-—_\s]*)$")

# --- 标题/短语折叠归一（2026-08-23「沁园春江畔」回归）-----------------------
# 篇目/节标题里的间隔号、编号在查询与原文两侧并不一致（「沁园春江畔」vs
# 「沁园春·江畔」「1 沁园春·江畔」）。短语/bigram 匹配前把两侧都折叠成
# 无标点形式，标题类查询才能整句命中原文；词项级匹配不受影响。
_PUNCT_STRIP_RE = re.compile(
    r"[\s·・‧﹒．。、，,：:；;！!？?（）()［\[\]【】「」『』《》〈〉“”\"'‘’\-—_~～*]+")


def fold_punct(text: str) -> str:
    """NFKC + 去空白与常用标点/间隔号（短语与 bigram 匹配的统一折叠形式）。"""
    return _PUNCT_STRIP_RE.sub("", unicodedata.normalize("NFKC", str(text or "")))

# --- 自然语言问句归一（2026-08-15「导数高中要学点什么」回归）---
# 口语问句（"X 要学点什么 / X 是什么 / 怎么学 X"）不得把整句当作"专业短语"
# 参与 bigram 覆盖门——教材正文永远覆盖不了问句的疑问词尾巴，会导致
# NOT_FOUND（选必2 有 112 处「导数」仍检索不到）。这里做的是**类别级**修复：
# 问句先剥疑问尾巴与学段/必修类语境词，短语/词项一律从剩余内容词派生。
_QUESTION_MARK_RE = re.compile(
    r"什么|怎么|为什么|为啥|哪些|如何|啥|多少|哪一|几[个条道章节知识]|吗[？?]?$|呢[？?]?$")
_STAGE_WORD_RE = re.compile(
    r"小学|初中|高中|本科|大学|高[一二三]|初[一二三]|[1-9一二三]年级|"
    r"必修\s*[一二三0-9０-９]*|选必\s*修?\s*[一二三0-9０-９]*|选择性必修\s*[一二三0-9０-９]*")
# 尾巴必须以疑问成分结尾（各段均可选，但整体非空），避免匹配空串。
_QUESTION_TAIL_RE = re.compile(
    r"(?:要|想|应该|需要|可以|能)?"
    r"(?:学|了解|理解|复习|预习|掌握|讲讲?|说说?|看看?|介绍|总结|梳理|解释|弄懂|搞懂|明白|知道|考|做|练|写|提)?"
    r"(?:一?点|一些)?"
    r"(?:什么|哪些|啥|东西|内容|知识点?|要点|重点|题目|问题)"
    r"|(?:是|有|包含|包括|涉及|讲到?|提到?|讲了?|说了?)(?:的)?(?:是)?"
    r"(?:什么|哪些|啥|东西|内容|意思)"
    r"|(?:怎么|如何|怎样)(?:学|考|用|理解|记|复习|准备|办|样)")


def is_natural_question(query: str) -> bool:
    """True 当 query 含疑问标记（口语/自然语言问句而非专业短语检索）。"""
    return bool(_QUESTION_MARK_RE.search(query or ""))


def _strip_question_tails(q: str, *, rounds: int = 3) -> str:
    for _ in range(max(1, rounds)):
        stripped = _QUESTION_TAIL_RE.sub("", q).strip()
        if stripped == q:
            break
        q = stripped
    return q


def question_core(query: str) -> str:
    """问句的内容词核：剥口语尾巴 → 剥学段/必修语境词 → 剥客套。

    返回串短于 2 字（纯疑问、无内容词）时调用方应回退原 query。
    纯类别规则，不含任何具体学科词条。
    """
    q = unicodedata.normalize("NFKC", query or "").strip()
    q = _strip_question_tails(q)
    q = re.sub(r"^(?:什么是|啥是|何为|请问)", "", q)
    q = _STAGE_WORD_RE.sub(" ", q)
    q = re.sub(r"(?:请|帮我|给我|能不能|能否)", " ", q)
    return q.strip()


def _has_content(text: str) -> bool:
    """提取核去标点/书名号/停用字后至少含 2 个内容字符。

    2026-08-26 取证：「借鉴主义讲了什么」被旧 `_core_query` 提取成
    「了什」、「《湖畔夜色讲了什么》」提取成「了什么》」——空/纯虚词核让
    词项集坍缩，48 个候选全部 no_absolute_evidence（库里有全文却 NOT_FOUND）。
    任何一级提取都必须过此守卫，全败则回退原查询。
    """
    compact = fold_punct(text)
    content = "".join(ch for ch in compact if ch not in _STOP)
    return len(content) >= 2


def effective_query(query: str) -> str:
    """证据门/词项统一使用的查询基（级联，词项集永不坍缩为空）。

    顺序：① 动词尾捕获（是否讲到X / 讲了X吗 → X，剥首尾虚词）；② 问句
    内容词核；③ 原查询。「X讲了什么」类主语前置问句由 ② 兜住（① 的捕获
    「了什么」过不了 `_has_content`）；「这本书讲了导数吗」类宾语后置问句
    由 ① 命中（② 只能剥到「讲了导数」，词项被稀释）。
    """
    q = unicodedata.normalize("NFKC", query or "").strip()
    if not q:
        return q
    tail = _core_query(q)
    if tail and tail != q:
        return tail
    if is_natural_question(q):
        stripped = question_core(q)
        if _has_content(stripped):
            return stripped
    stripped = question_core(q)
    if _has_content(stripped) and stripped != q:
        return stripped
    return q


def _core_query(query: str) -> str:
    q = unicodedata.normalize("NFKC", query or "").strip()
    match = re.search(r"(?:是否|有没有|有无|讲到|讲|包含|涉及)([^？?]+)$", q)
    if not match:
        return ""
    core = re.sub(r"[吗么呢吧]$", "", match.group(1).strip())
    # 捕获组常以动词残余虚词开头（讲了X吗 → 「了X吗」）：剥掉首部虚词后
    # 若只剩纯疑问/停用成分（「了什么」→「什么」→ 空），则视为无效捕获。
    core = re.sub(r"^(?:[了的是和与及中])+", "", core)
    return core if _has_content(core) else ""


def query_phrases(query: str) -> list[str]:
    q = effective_query(query)
    q = re.sub(r"(?:请|帮我|解释|介绍|一下|这本|这套|教材|资料|文件)", "", q)
    parts = [p for p in re.split(r"[的与和及、，。！？?：:；;\s]+", q) if len(p) >= 2]
    aliases: list[str] = []
    for part in parts:
        if len(part) >= 8:
            aliases.extend([part[:8], part[-4:]])
        if "矩阵" in part:
            aliases.append(part.replace("矩阵", "阵"))
        if "判定" in part:
            aliases.append(part.replace("判定", "判别"))
        if "强度分布" in part:
            aliases.append(part.replace("强度分布", "光强分布"))
            aliases.append("光强分布")
        if part == "判定条件":
            aliases.append("充分必要条件")
    return list(dict.fromkeys(parts + aliases))


def normalize_query(query: str) -> list[str]:
    # 问句走内容词核（剥疑问尾巴/学段词），词项覆盖度才不被口语噪声稀释。
    q = unicodedata.normalize("NFKC", effective_query(query)).strip()
    terms: list[str] = []
    for tok in tokenize(q):
        if len(tok) == 1 and tok in _UNIGRAM_STOP:
            continue
        if tok not in terms:
            terms.append(tok)
    # Prefer CJK bigrams/Latin words. Generic one-character overlap (量/子/的)
    # is not evidence and caused unrelated textbook hits.
    strong = [t for t in terms
              if (len(t) >= 2 or re.fullmatch(r"[a-z0-9]+", t, re.I))
              and not (len(t) == 2 and (t[0] in _STOP or t[-1] in _STOP))]
    if strong or terms:
        return strong or terms
    # 兜底：提取核完全退化时用原查询的折叠 bigram，词项集绝不低温坍缩
    # （2026-08-26 取证：空词项让全部候选走 no_absolute_evidence → 假 NOT_FOUND）。
    return [b for b in sorted(_bigrams(fold_punct(query)))][:8]


def _bigrams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
    return {compact[i:i + 2] for i in range(max(0, len(compact) - 1))
            if not compact[i:i + 2].isspace()}


def noise_flags(text: str, item: dict[str, Any] | None = None) -> list[str]:
    flags = list((item or {}).get("noise_flags") or [])
    clean = (text or "").strip()
    if _META.search(clean):
        flags.append("metadata")
    if re.match(r"^(?:前\s*言|序\s*言|内容提要|编者的话|preface)", clean, re.I):
        flags.append("preface")
    if _HEADER.match(clean):
        flags.append("header_footer")
    if re.search(r"(?:\.{2,}|…{2,})\s*\d{1,4}\s*$", clean):
        flags.append("toc")
    # 紧凑目录行（「1 沁园春·江畔/毛泽东 2 立在山海边上放歌/郭沫若 …」）：
    # 无点线引导也全是目录——≥3 个「序号+标题/作者」模式即判 toc。
    if len(re.findall(r"\d{1,2}\s*[\u4e00-\u9fff·]{1,12}/[\u4e00-\u9fff]{1,8}", clean)) >= 3:
        flags.append("toc")
    # v2.1 分块会把目录拆成单行 chunk（整块就是「N 篇名/作者」一条）：
    # 这是导航信息不是课文内容，作为 top 证据会顶掉正文。
    elif re.fullmatch(r"\d{1,2}\s*[\u4e00-\u9fff·]{1,12}/[\u4e00-\u9fff]{1,8}", clean.strip()):
        flags.append("toc")
    return list(dict.fromkeys(flags))


def classify_query_intent(query: str) -> str:
    q = query or ""
    if re.search(r"作者|出版社|出版|版权|ISBN|CIP|目录|页码|哪一页|哪里", q, re.I):
        return "metadata" if not re.search(r"哪一页|页码|哪里", q) else "locate"
    if re.search(r"推导|证明|为什么成立|derive|proof", q, re.I):
        return "derivation"
    if re.search(r"例题|习题|练习|怎么做|求解|exercise", q, re.I):
        return "exercise"
    if re.search(r"总结|概括|小结|全章|全文|summary", q, re.I):
        return "summary"
    if re.search(r"定义|是什么|条件|判定|判别|define|what is", q, re.I):
        return "definition"
    return "content"


def _escape_prompt_delimiters(text: str) -> str:
    return re.sub(
        r"<\s*(/?)\s*(material_excerpt|ocr_material|user_input|history_excerpt|workspace_memory)[^>]*>",
        lambda match: f"［{match.group(1)}{match.group(2)}］", text, flags=re.I)


# 定义性表述标记：摘录窗口优先以这类句子为中心（「X 是什么」类查询的
# 答案句通常是定义/定理句，而非首次词面命中句）。
_DEFINITION_MARK_RE = re.compile(
    r"是指|指的是|定义为|定义是|称为|叫做|简称|定理|定义|公式|法则|性质|判别|表达式")


def _split_sentences(text: str) -> list[str]:
    """句边界切分：CJK 终止符恒切；ASCII `.!?` 仅在前后均非数字时切。

    数字间的句点是公式编号/页码的一部分（「式（8.50）」「p.280」），在那里
    切句会把公式残片「50)所表示的…」当证据注入（2026-08-25 取证）。
    """
    parts: list[str] = []
    buf: list[str] = []
    length = len(text)
    for i, ch in enumerate(text):
        buf.append(ch)
        if ch in "。！？；\n":
            parts.append("".join(buf))
            buf = []
        elif ch in ".!?":
            prev = text[i - 1] if i else ""
            nxt = text[i + 1] if i + 1 < length else ""
            if not (prev.isdigit() or nxt.isdigit()):
                parts.append("".join(buf))
                buf = []
    if buf:
        parts.append("".join(buf))
    return [s for s in (p.strip() for p in parts) if s]


def evidence_excerpt(text: str, terms: list[str], limit: int | None = None,
                     min_window: int = 400) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if limit is None:
        try:
            from .config import settings
            limit = int(settings.rag_evidence_excerpt_chars)
        except Exception:
            limit = 900
    sentences = _split_sentences(text)
    if not sentences:
        return text[:limit]
    def _hit(sentence: str) -> bool:
        return any(t and t in sentence for t in terms)
    # 中心句优先级：含定义标记且含命中词 > 含命中词 > 首句。
    candidates = [i for i, s in enumerate(sentences) if _hit(s)]
    hit = next((i for i in candidates if _DEFINITION_MARK_RE.search(sentences[i])),
               candidates[0] if candidates else 0)
    left = right = hit
    out = sentences[hit]
    while len(out) < min_window and (left > 0 or right + 1 < len(sentences)):
        if left > 0:
            left -= 1
        if len(" ".join(sentences[left:right + 1])) < min_window and right + 1 < len(sentences):
            right += 1
        out = " ".join(sentences[left:right + 1])
    return _escape_prompt_delimiters(out[:limit].rstrip())


def _simhash64(tokens: set[str]) -> int:
    weights = [0] * 64
    for token in tokens:
        value = int(hashlib.sha256(token.encode()).hexdigest()[:16], 16)
        for bit in range(64):
            weights[bit] += 1 if value & (1 << bit) else -1
    out = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            out |= 1 << bit
    return out


def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


@dataclass(frozen=True)
class GateResult:
    selected: list[dict[str, Any]]
    omitted: int
    no_hit: bool
    drop_reasons: dict[str, int]
    tier: str = "found"  # found（高置信命中）/ partial（弱信号降级返回）/ not_found

    @property
    def partial(self) -> bool:
        return self.tier == "partial"


def _title_blob(item: dict[str, Any]) -> str:
    """候选项的全部定位文本（书名/章/节/课题/标题层级），折叠后用于课题匹配。"""
    fields: list[str] = [str(item.get("source") or ""), str(item.get("filename") or ""),
                         str(item.get("chapter") or ""), str(item.get("section") or ""),
                         str(item.get("lesson") or "")]
    fields.extend(str(s) for s in (item.get("section_path") or []))
    return fold_punct(" ".join(f for f in fields if f))


def _title_match(core_folded: str, title_blob: str) -> float:
    """查询核与标题定位文本的词面契合度：整串包含 → 1.0，否则按 bigram 覆盖×0.7。

    课题类查询（「湖畔夜色讲了什么」「沁园春江畔」）的正文不含篇名，最强
    词面证据在 section/lesson 标题里——词本体 chunk（正文零命中）靠此信号
    进入候选，周边活动/单元任务块（正文提及篇名但标题不匹配）拿不到满分。
    """
    if not core_folded or len(core_folded) < 2:
        return 0.0
    if core_folded in title_blob:
        return 1.0
    if len(core_folded) >= 4:
        grams = _bigrams(core_folded)
        if grams:
            covered = sum(1 for g in grams if g in title_blob) / len(grams)
            return round(covered * 0.7, 3)
    return 0.0


def apply_evidence_gate(query: str, candidates: list[dict[str, Any]], top_k: int = 4,
                        *, allow_metadata: bool = False,
                        allow_small_direct: bool = False) -> GateResult:
    terms = normalize_query(query)
    query_intent = classify_query_intent(query)
    drops: dict[str, int] = {}
    def drop(reason: str) -> None:
        drops[reason] = drops.get(reason, 0) + 1
    if allow_metadata and re.search(r"作者|编者|谁写", query):
        terms = list(dict.fromkeys(terms + ["作者", "编者", "编写"]))
    if allow_small_direct and 0 < len(candidates) <= 8:
        selected: list[dict[str, Any]] = []
        for raw in candidates[:top_k]:
            item = dict(raw)
            item["matched_terms"] = []
            item["matched_bigrams"] = []
            item["noise_flags"] = noise_flags(str(item.get("text") or ""), item)
            item["confidence"] = 0.62
            item["signals"] = {"query_aware_direct": True}
            item["selection_reason"] = "explicit_small_material_summary"
            item["evidence_excerpt"] = evidence_excerpt(str(item.get("text") or ""), [])
            digest = hashlib.sha256(re.sub(r"\s+", "", item["evidence_excerpt"]).encode()).hexdigest()
            item["evidence_id"] = f"ev_{digest[:16]}"
            item["context_hash"] = hashlib.sha256(item["evidence_excerpt"].encode()).hexdigest()
            selected.append(item)
        return GateResult(selected, max(0, len(candidates) - len(selected)), not bool(selected), drops)
    core = effective_query(query)
    core_folded = fold_punct(core)
    natural_question = is_natural_question(query or "")
    q_bigrams = _bigrams(fold_punct(core))
    phrases = query_phrases(query)
    primary_phrase = phrases[0] if phrases else ""
    folded_phrases = [fold_punct(p) for p in phrases]
    primary_bigrams = _bigrams(fold_punct(primary_phrase)) if primary_phrase else set()
    # BM25 相对分母：与本次候选池的最高分比（饱和绝对归一对池规模敏感，
    # 单候选小池恒 0.5，造成置信度与相关性脱钩——2026-08-26 取证复算）。
    pool_max = max((float(c.get("bm25_score", c.get("score")) or 0.0) or 0.0
                    for c in candidates), default=0.0)
    from .text_quality import text_garble_ratio
    scored: list[tuple[float, dict[str, Any]]] = []
    weak: list[tuple[float, dict[str, Any]]] = []
    for item in candidates:
        text = str(item.get("text") or "")
        flags = noise_flags(text, item)
        compact = fold_punct(text)
        # P9 乱码准入门：定制字体 mojibake 文本层（犃犅/ꎬ/PUA）绝不可注入
        # 上下文——排除并计数，供 NOT_FOUND 文案给出「重建索引」指引。
        if text_garble_ratio(text) >= 0.05:
            drop("garble_text_layer")
            continue
        matched = [t for t in terms if t and (t in text or t in compact)]
        matched_bigrams = sorted({b for b in q_bigrams if b and b in compact})
        matched_phrases = [p for p, folded in zip(phrases, folded_phrases)
                           if folded and folded in compact]
        title_blob = _title_blob(item)
        title_match = _title_match(core_folded, title_blob)
        primary_coverage = (len({b for b in primary_bigrams if b in compact})
                            / max(1, len(primary_bigrams))) if primary_bigrams else 0.0
        exact = len(matched) / max(1, len(terms))
        bigram = len(matched_bigrams) / max(1, len(q_bigrams)) if q_bigrams else 0.0
        lexical = min(1.0, exact * 0.72 + bigram * 0.28)
        base = float(item.get("bm25_score", item.get("score") or 0.0) or 0.0)
        base_norm = 1.0 - (1.0 / (1.0 + max(0.0, base))) if base else 0.0
        bm25_rel = round(base / pool_max, 4) if (base > 0 and pool_max > 0) else 0.0
        try:
            vector_distance = float(item.get("vector_distance"))
        except (TypeError, ValueError):
            vector_distance = 9.0
        vector_good = vector_distance <= 0.32
        vector_strength = max(0.0, 1.0 - vector_distance) if vector_good else 0.0
        penalty = 0.42 if "toc" in flags else (0.35 if any(
            f in flags for f in ("header_footer", "page_number")) else 0.0)
        if "heading" in (item.get("block_types") or []) and len(compact) < 80:
            penalty += 0.18
        if "copyright" in flags or "metadata" in flags:
            penalty += 0.18 if allow_metadata else 0.45
        if "preface" in flags:
            penalty += 0.05 if allow_metadata else 0.22
        intent_bonus = 0.0
        block_types = set(item.get("block_types") or [])
        if query_intent == "definition":
            if block_types & {"definition", "theorem"}:
                intent_bonus += 0.12
            if len(matched) >= 2 and ("充分必要条件" in compact or "判别法" in compact):
                intent_bonus += 0.14
            if block_types & {"exercise", "example", "solution"}:
                penalty += 0.10
        elif query_intent == "derivation" and block_types & {"formula", "theorem", "solution"}:
            intent_bonus += 0.12
        elif query_intent == "exercise" and block_types & {"exercise", "example", "solution"}:
            intent_bonus += 0.12
        elif query_intent == "summary" and block_types & {"summary", "heading"}:
            intent_bonus += 0.10
        section_path = item.get("section_path") or []
        if query_intent != "summary" and (compact.startswith("本章小结")
                or any("小结" in str(x) for x in section_path)):
            penalty += 0.35
        if allow_metadata and re.search(r"作者|编者|谁写", query):
            if "编写" in compact and "统稿" in compact:
                intent_bonus += 0.22
            elif "编者" in compact or "编写" in compact:
                intent_bonus += 0.05
        phrase_bonus = min(0.18, len(matched_phrases) * 0.09)
        # P9 置信度重校准（纯 BM25 语义下的诚实刻度）：词项覆盖为主，
        # BM25 相对分 0.2；标题整串命中（查询核完整出现在课题/章节标题里）
        # 是课题类查询的最强词面证据，独立 0.5 档；部分 bigram 命中只算 0.2 档。
        title_signal = 0.50 if title_match >= 0.99 else 0.20 * title_match
        confidence = max(0.0, min(1.0, lexical * 0.45 + bm25_rel * 0.20
                                  + title_signal + vector_strength * 0.10
                                  + intent_bonus + phrase_bonus - penalty))
        enriched = dict(item)
        enriched["matched_terms"] = matched
        enriched["matched_bigrams"] = matched_bigrams
        enriched["matched_phrases"] = matched_phrases
        enriched["primary_phrase_coverage"] = round(primary_coverage, 3)
        enriched["title_match"] = title_match
        enriched["noise_flags"] = flags
        enriched["confidence"] = round(confidence, 3)
        enriched["signals"] = {
            "lexical": round(lexical, 3),
            "exact_term_coverage": round(exact, 3),
            "primary_phrase_coverage": round(primary_coverage, 3),
            "title_match": title_match,
            "bm25_raw": round(base, 4),
            "bm25_normalized": round(base_norm, 4),
            "bm25_relative": bm25_rel,
            "vector_distance": item.get("vector_distance"),
            "vector_calibrated": vector_good,
            "variant_hits": list(item.get("variant_hits") or []),
            "concept_bonus": item.get("concept_bonus", 0),
        }
        enriched["query_intent"] = query_intent
        enriched["selection_reason"] = ("lesson_title_match" if title_match >= 0.99
                                        else "exact_term" if exact >= 0.5
                                        else "professional_bigram")
        if lexical <= 0.0 and title_match <= 0.0 and not vector_good:
            drop("no_absolute_evidence")
            continue
        # For multi-part professional queries, one famous shared surname/word is
        # insufficient (e.g. Maxwell distribution vs Maxwell equations).
        # 自然语言问句不参与短语覆盖门：口语问句的短语（含疑问尾巴/学段词）
        # 不可能被教材原文整句覆盖，其相关性由词项覆盖 + 阈值门判定。
        # 强短语豁免（标题类查询）：原文完整含查询短语/主短语覆盖 ≥0.75 时，
        # 不再因噪声词（教材/哪一页/客套）稀释词项覆盖而整批丢弃——
        # 「沁园春江畔在教材哪一页」必须能命中《沁园春·江畔》原文。
        strong_primary = (primary_coverage >= 0.75 or bool(matched_phrases)
                          or title_match >= 0.99)
        if (not allow_metadata and not vector_good and not natural_question
                and not strong_primary
                and len(primary_phrase) >= 4
                and primary_coverage < 0.60):
            drop("weak_primary_phrase")
            continue
        if (not allow_metadata and not vector_good and not natural_question
                and not strong_primary
                and any(len(p) >= 4 for p in phrases)
                and not matched_phrases and primary_coverage < 0.75):
            drop("no_professional_phrase")
            continue
        if not vector_good and not strong_primary \
                and len(terms) >= 4 and not matched_phrases and exact < 0.50:
            drop("weak_term_coverage")
            if confidence >= 0.05:
                weak.append((confidence, enriched))
            continue
        threshold = 0.28 if len(terms) <= 2 else 0.18
        if confidence < threshold:
            drop("below_absolute_threshold")
            if confidence >= 0.05 and (lexical > 0 or title_match > 0):
                weak.append((confidence, enriched))
            continue
        scored.append((confidence + float(item.get("concept_bonus") or 0.0) * 0.03, enriched))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        # P9 分级：没有达标项，但存在弱信号（词项/标题部分命中）时降级返回
        # （partial）而非 NOT_FOUND——「检索器没找到足够好的证据」不等于
        # 「知识库里没有」。NOT_FOUND 留给词面/标题双零的真空。
        if weak:
            weak.sort(key=lambda x: x[0], reverse=True)
            kept_weak: list[dict[str, Any]] = []
            seen_locations: set[tuple[str, int]] = set()
            for score, item in weak[:max(2, top_k)]:
                item["confidence_tier"] = "low"
                item["partial"] = True
                item["evidence_excerpt"] = evidence_excerpt(
                    str(item.get("text") or ""), item["matched_terms"])
                body = re.sub(r"\s+", "", str(item.get("text") or ""))
                digest = hashlib.sha256(body.encode()).hexdigest()[:16]
                item["evidence_id"] = f"ev_{digest}"
                item["context_hash"] = hashlib.sha256(item["evidence_excerpt"].encode()).hexdigest()
                location = (str(item.get("file_id") or item.get("source") or ""),
                            int(item.get("page") or -1))
                if location in seen_locations:
                    continue
                seen_locations.add(location)
                kept_weak.append(item)
            if kept_weak:
                return GateResult(kept_weak, len(candidates), False, drops, tier="partial")
        return GateResult([], len(candidates), True, drops, tier="not_found")
    best = scored[0][0]
    qualified = [(score, item) for score, item in scored
                 if score >= max(0.22, best - 0.42)]
    gap_dropped = len(scored) - len(qualified)
    if gap_dropped:
        drops["relative_gap"] = drops.get("relative_gap", 0) + gap_dropped
        qualified_ids = {id(item) for _s, item in qualified}
        for score, item in scored:
            if id(item) not in qualified_ids and item.get("confidence", 0) >= 0.05:
                weak.append((score, item))
    kept: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    seen_locations: set[tuple[str, int]] = set()
    seen_token_sets: list[set[str]] = []
    seen_simhashes: list[int] = []
    remaining = list(qualified)
    while remaining and len(kept) < top_k:
        def mmr_value(entry):
            score, candidate = entry
            tokens = set(tokenize(re.sub(r"\s+", "", str(candidate.get("text") or ""))))
            redundancy = max((len(tokens & prev) / max(1, len(tokens | prev))
                              for prev in seen_token_sets), default=0.0)
            same_source = any(str(candidate.get("file_id") or candidate.get("source") or "")
                              == str(x.get("file_id") or x.get("source") or "")
                              for x in kept)
            return score - 0.22 * redundancy - (0.02 if same_source else 0.0)
        best_entry = max(remaining, key=mmr_value)
        remaining.remove(best_entry)
        score, item = best_entry
        location = (str(item.get("file_id") or item.get("source") or ""),
                    int(item.get("page") or -1))
        if location[1] > 0 and location in seen_locations:
            drop("same_page_duplicate")
            continue
        body = re.sub(r"\s+", "", str(item.get("text") or ""))
        sim_hash = hashlib.sha256(body.encode()).hexdigest()[:16]
        if sim_hash in seen_hashes:
            drop("exact_duplicate")
            continue
        token_set = set(tokenize(body))
        if any((len(token_set & previous) / max(1, len(token_set | previous))) >= 0.78
               for previous in seen_token_sets):
            drop("near_duplicate_jaccard")
            continue
        simhash = _simhash64(token_set)
        if any(_hamming(simhash, previous) <= 3 for previous in seen_simhashes):
            drop("near_duplicate_simhash")
            continue
        seen_hashes.add(sim_hash)
        seen_locations.add(location)
        seen_token_sets.append(token_set)
        seen_simhashes.append(simhash)
        item["evidence_excerpt"] = evidence_excerpt(str(item.get("text") or ""),
                                                     item["matched_terms"])
        item["evidence_id"] = f"ev_{sim_hash}"
        item["context_hash"] = hashlib.sha256(item["evidence_excerpt"].encode()).hexdigest()
        item["selection_reason"] += "+mmr"
        kept.append(item)
    # MMR/去重淘汰后不足额时，用弱信号池补位并标低置信（不静默缩量）。
    if len(kept) < top_k and weak:
        weak.sort(key=lambda x: x[0], reverse=True)
        for score, item in weak:
            if len(kept) >= top_k:
                break
            if any(str(k.get("chunk_id")) == str(item.get("chunk_id")) for k in kept):
                continue
            item["confidence_tier"] = "low"
            item["partial"] = True
            item["evidence_excerpt"] = evidence_excerpt(
                str(item.get("text") or ""), item["matched_terms"])
            body = re.sub(r"\s+", "", str(item.get("text") or ""))
            digest = hashlib.sha256(body.encode()).hexdigest()[:16]
            item["evidence_id"] = f"ev_{digest}"
            item["context_hash"] = hashlib.sha256(item["evidence_excerpt"].encode()).hexdigest()
            kept.append(item)
    tier = "found" if kept else "not_found"
    return GateResult(kept, max(0, len(candidates) - len(kept)), not bool(kept), drops,
                      tier=tier)
