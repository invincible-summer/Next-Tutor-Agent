"""Deterministic structure-aware chunking for persisted material RAG.

The V2 chunker never calls an LLM. It keeps physical page boundaries, marks
rather than deletes front-matter/noise, retains normalized and raw source
coordinates, and emits structural parent/previous/next links.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from typing import Any

from .retriever import Chunk, retrievable_text, tokenize

CHUNK_SCHEMA_VERSION = "structured-v2.2"
TARGET_TOKEN_MIN = 220
TARGET_TOKEN_MAX = 480
TARGET_TOKEN_COUNT = 360
SOFT_TOKEN_LIMIT = 520
HARD_TOKEN_LIMIT = 650

_HEADING_RE = re.compile(
    r"^(?:第\s*[0-9０-９一二三四五六七八九十百零〇两]+\s*(?:章|单元)|"
    r"第\s*[0-9０-９一二三四五六七八九十百零〇两]+\s*(?:节|课|讲|篇)|"
    r"[0-9０-９]+(?:\.[0-9０-９]+){0,3}\s+|"
    r"chapter\s+[0-9]+|"
    r"[一二三四五六七八九十]+、)\s*\S+",
    re.I,
)
_TOC_RE = re.compile(r"(?:目录|contents|table\s+of\s+contents|索引|目次)", re.I)
_COPYRIGHT_RE = re.compile(r"(?:版权|版权所有|著作权|isbn|cip|中国版本图书馆|定价|出版发行|法律声明)", re.I)
_PREFACE_RE = re.compile(r"^(?:前\s*言|序\s*言|内容提要|编者的话|preface)", re.I)
_PAGE_RE = re.compile(r"^(?:[-—_\s]*\d{1,4}[-—_\s]*|第\s*\d+\s*页)$")
_FORMULA_RE = re.compile(r"(?:[=≤≥≈∑∫√∞→←↔]|\\frac|[a-zA-Z]\s*\^\s*\d)")
# 结构化标记（OCR prompt v2 / 原生收割产生）：印刷页码 / 图 / 表。
_PRINTED_PAGE_RE = re.compile(r"^\s*[\[［【]\s*页码\s*[=＝:：]?\s*([0-9]{1,4})\s*[\]］】]\s*$")
# 去锚点版：OCR/收割产物并不保证标记独占行——实测视觉模型会把 [页码=N] 放在
# 该页最后一行行尾（甚至正文句中）。剥离标记子串本身，行内其余文字保留。
_PRINTED_PAGE_INLINE_RE = re.compile(
    r"\s*[\[［【]\s*页码\s*[=＝:：]?\s*([0-9]{1,4})\s*[\]］】]")
_FIGURE_MARK_RE = re.compile(r"^\s*[\[［【]\s*图")
_TABLE_MARK_RE = re.compile(r"^\s*[\[［【]\s*表")
# figure/table 标记块的"从属行"：图述/图注/题目转录前缀、表格行（|/｜ 分隔）。
# 注意不含裸"图"——"图像/图中…"等正文行不能被并入标记块。
_SPECIAL_CONT_RE = re.compile(r"^\s*(?:图述|图注|题目转录)")
_PROTECTED_BLOCKS = {"figure", "formula", "table", "definition", "theorem",
                     "example", "exercise", "solution", "annotation",
                     "vocabulary"}
_TOKEN_PIECE_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|"
    r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:\.\d+)?|[^\s]"
)
# v2.2 课文结构：语文/英语教材的注释行（① 胜状：…… / 〔注〕……）与词表行
# （severe /sɪvɪə/ n. 严重的 / hope→alone 制表对）独立成块，不与正文混切。
_ANNOTATION_RE = re.compile(r"^(?:〔?注(?:释)?〕?|[①-⑳]\s*\S{0,30}[：:])")
_VOCABULARY_RE = re.compile(r"/[^/\n]{2,60}/")
_VOCAB_PAIR_RE = re.compile(r"^[A-Za-z][A-Za-z'\- ]{0,24}(?:\t|\s{2,})\S")
# 课题标题（课文级父文档锚点）：「第N课/讲」或「N 题名」（无小数点——
# 6.1.1 是小节，1 春 / 12 纪念远行者 是课题）。检索面包屑与课题分组依赖它。
_LESSON_TITLE_RE = re.compile(
    r"^(?:第\s*[0-9０-９一二三四五六七八九十]+\s*(?:课|讲)|"
    r"[0-9０-９]{1,2}\s*[.、]?\s*)\s*\S{1,28}$")
# 孤立脚注上标行（语文取证：「沙洲\nb头」——b 是脚注锚点，拆碎了词）。
_FOOTNOTE_ANCHOR_RE = re.compile(r"^[a-z]$")
# 粘连形态：锚点字母打头 + ≤2 个 CJK（可带句读）收尾的短行（「b头。」）。
_FOOTNOTE_ANCHOR_GLUED_RE = re.compile(
    r"^([a-z])([\u4e00-\u9fff]{1,2}[。！？；：，、]?)$")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).replace("\u00a0", " ").strip()


def _normalize_with_raw_map(text: str) -> tuple[str, list[int]]:
    """Return NFKC text and a best-effort map from normalized chars to raw offsets."""
    normalized: list[str] = []
    raw_offsets: list[int] = []
    for raw_idx, char in enumerate(text):
        piece = unicodedata.normalize("NFKC", char).replace("\u00a0", " ")
        normalized.extend(piece)
        raw_offsets.extend([raw_idx] * len(piece))
    return "".join(normalized), raw_offsets


def estimate_model_tokens(text: str) -> int:
    """Deterministic conservative token estimate for CJK/Latin/math text."""
    total = 0
    for piece in _TOKEN_PIECE_RE.findall(text or ""):
        if re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", piece):
            total += 1
        elif piece[0].isalpha():
            total += max(1, math.ceil(len(piece) / 4))
        elif piece[0].isdigit():
            total += max(1, math.ceil(len(piece) / 3))
        else:
            total += 1
    return total


def _block_type(text: str, heading: bool = False) -> str:
    if heading:
        return "heading"
    clean = text.lstrip()
    if _FIGURE_MARK_RE.match(clean):
        return "figure"
    if _TABLE_MARK_RE.match(clean):
        return "table"
    if _COPYRIGHT_RE.search(clean):
        return "metadata"
    if re.match(r"(?:本章小结|小结|总结|summary)", clean, re.I):
        return "summary"
    if re.match(r"(?:证明|解(?:答|析)?[：:]?|solution|proof)\b", clean, re.I):
        return "solution"
    if re.match(r"(?:定义|definition)\s*\d*", clean, re.I):
        return "definition"
    if re.match(r"(?:定理|引理|推论|性质|公理|theorem|lemma|corollary)", clean, re.I):
        return "theorem"
    if re.match(r"(?:习题|练习|exercise)", clean, re.I):
        return "exercise"
    if _ANNOTATION_RE.match(clean):
        return "annotation"
    if _is_vocabulary_line(clean):
        return "vocabulary"
    if re.match(r"(?:例\s*[题]?|example)", clean, re.I):
        return "example"
    if _FORMULA_RE.search(text) and len(text) < 500:
        return "formula"
    if "|" in text or "\t" in text:
        return "table"
    return "paragraph"


def _noise_flags(text: str, *, repeated: bool = False) -> list[str]:
    flags: list[str] = []
    compact = re.sub(r"\s+", "", text)
    if _TOC_RE.search(text) or (re.search(r"\.{2,}|…{2,}", text) and re.search(r"\d{1,4}$", text)):
        flags.append("toc")
    if _COPYRIGHT_RE.search(text):
        flags.append("copyright")
    if _PREFACE_RE.search(text.strip()):
        flags.append("preface")
    if _PAGE_RE.match(text):
        flags.append("page_number")
    if repeated:
        flags.append("header_footer")
    if len(compact) >= 8 and len(set(compact)) <= 3:
        flags.append("ocr_garble")
    if re.search(r"(.)\1{8,}", compact):
        flags.append("ocr_garble")
    return list(dict.fromkeys(flags))


def _is_heading(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 120:
        return False
    return bool(_HEADING_RE.match(line)) or (
        len(line) <= 55 and not re.search(r"[。！？；.!?]$", line)
        and (line.startswith(("第", "附录", "绪论", "概述", "摘要")) or line.isupper())
    )


def _heading_level(title: str) -> int:
    clean = _norm(title)
    if re.match(r"^(?:第\s*\S+\s*(?:章|单元)|chapter\s+\d+)", clean, re.I):
        return 1
    if re.match(r"^第\s*\S+\s*(?:节|课|讲|篇)", clean):
        return 2
    numeric = re.match(r"^([0-9]+(?:\.[0-9]+){0,3})\s+", clean)
    if numeric:
        return min(3, numeric.group(1).count(".") + 1)
    if re.match(r"^[一二三四五六七八九十]+、", clean):
        return 2
    return 3


def _sentence_units(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？；.!?])\s*|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _is_vocabulary_line(line: str) -> bool:
    """词表行（英语取证：音标全部乱码的 glossary 行）——独立成块不与正文混切。"""
    clean = line.lstrip()
    return bool((_VOCABULARY_RE.search(clean) or _VOCAB_PAIR_RE.match(clean))
                and re.match(r"^[A-Za-z〔\[]", clean))


def _repair_fragmented_lines(lines: list[str],
                             repeated_headers: set[str]) -> list[str]:
    """确定性断行修复（v2.2，语文取证：「沙洲\\nb头」被脚注上标拆碎）。

    三条高精度规则，只动行结构、不改写内容：
      1. 孤立小写字母行是脚注上标锚点：
         - 前行尾 + 后行头都是 CJK 且后行 ≤2 字符（词被拦腰截断的尾巴，
           如「头。」）→ 前后行合并、锚点丢弃（保住「沙洲头」bigram）；
         - 其余情况直接丢弃锚点行（标题注/作者注等，不动前后行）。
         只处理小写：大写单字母行更可能是选择题答案键，宁缺毋滥。
      2. 跨页重复的运行页眉行（``repeated_headers``，≥3 页页首/页尾出现，
         ≤30 字符）整行剥离——取证：「示例 上册」污染正文 52 处。
      3. 其余原样返回。合并行在 normalized_page 中 find 不到时自动落
         source_cursor 兜底（mapping_exact=False），坐标映射不炸。
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        prev_cjk = bool(out) and bool(re.search(r"[\u4e00-\u9fff]$", out[-1]))
        if _FOOTNOTE_ANCHOR_RE.match(line):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            nxt_cjk = bool(re.match(r"^[\u4e00-\u9fff]", nxt or ""))
            if prev_cjk and nxt_cjk and len(nxt) <= 2:
                out[-1] = out[-1] + nxt  # 词被锚点拦腰截断：合并保词
                i += 2
                continue
            i += 1  # 锚点行本身丢弃
            continue
        glued = _FOOTNOTE_ANCHOR_GLUED_RE.match(line)
        if glued and prev_cjk:
            out[-1] = out[-1] + glued.group(2)  # 「沙洲」+「b头。」→ 沙洲头。
            i += 1
            continue
        if line in repeated_headers and len(re.sub(r"\s+", "", line)) <= 30:
            i += 1
            continue
        out.append(line)
        i += 1
    return out


def _prefix_for_budget(text: str, budget: int) -> int:
    low, high = 1, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_model_tokens(text[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    cut = max(1, low)
    floor = max(1, int(cut * 0.65))
    boundaries = [m.end() for m in re.finditer(r"[\s，,；;。.!?！？）)】\]]", text[floor:cut])]
    if boundaries:
        cut = floor + boundaries[-1]
    return max(1, cut)


def _hard_split(text: str) -> list[str]:
    out: list[str] = []
    rest = text.strip()
    while rest and estimate_model_tokens(rest) > HARD_TOKEN_LIMIT:
        cut = _prefix_for_budget(rest, HARD_TOKEN_LIMIT)
        out.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest:
        out.append(rest)
    return [piece for piece in out if piece]


def _split_piece(text: str, block_type: str) -> list[str]:
    text = _norm(text)
    token_count = estimate_model_tokens(text)
    if not text:
        return []
    if token_count <= SOFT_TOKEN_LIMIT:
        return [text]
    if block_type in _PROTECTED_BLOCKS and token_count <= HARD_TOKEN_LIMIT:
        return [text]

    units: list[str] = []
    for unit in _sentence_units(text) or [text]:
        units.extend(_hard_split(unit))
    out: list[str] = []
    out_tokens: list[int] = []
    current = ""
    current_tokens = 0
    for unit in units:
        unit_tokens = estimate_model_tokens(unit)
        candidate_tokens = current_tokens + unit_tokens
        if (current and candidate_tokens > TARGET_TOKEN_COUNT
                and current_tokens >= TARGET_TOKEN_MIN):
            out.append(current)
            out_tokens.append(current_tokens)
            current, current_tokens = unit, unit_tokens
        elif candidate_tokens <= HARD_TOKEN_LIMIT:
            current = f"{current}\n{unit}".strip() if current else unit
            current_tokens = candidate_tokens
        else:
            if current:
                out.append(current)
                out_tokens.append(current_tokens)
            split_units = _hard_split(unit)
            for completed in split_units[:-1]:
                out.append(completed)
                out_tokens.append(estimate_model_tokens(completed))
            current = split_units[-1]
            current_tokens = estimate_model_tokens(current)
    if current:
        out.append(current)
        out_tokens.append(current_tokens)
    if len(out) >= 2 and out_tokens[-1] < TARGET_TOKEN_MIN:
        candidate_tokens = out_tokens[-2] + out_tokens[-1]
        if candidate_tokens <= SOFT_TOKEN_LIMIT:
            out[-2:] = [f"{out[-2]}\n{out[-1]}"]
            out_tokens[-2:] = [candidate_tokens]
    final: list[str] = []
    for piece in out:
        final.extend(_hard_split(piece))
    return final


def _raw_offsets(normalized_start: int, normalized_end: int,
                 raw_map: list[int], raw_length: int) -> tuple[int, int]:
    if not raw_map:
        return 0, 0
    start_idx = min(max(0, normalized_start), len(raw_map) - 1)
    end_idx = min(max(start_idx, normalized_end - 1), len(raw_map) - 1)
    return raw_map[start_idx], min(raw_length, raw_map[end_idx] + 1)


def chunk_text_v2(text: str, source: str, file_id: str = "") -> list[Chunk]:
    """Create deterministic V2 chunks, never crossing a physical page."""
    if not text or not text.strip():
        return []
    pages = text.split("\f")
    multi = len(pages) > 1
    edge_lines: list[str] = []
    for page in pages:
        lines = [_norm(x) for x in page.splitlines() if _norm(x)]
        edge_lines.extend(lines[:1] + lines[-1:])
    repeated = {line for line, count in Counter(edge_lines).items() if line and count >= 3}
    raw: list[dict[str, Any]] = []
    section_stack: list[str] = []
    toc_region = False
    preface_region = False
    structural_noise = {"toc", "copyright", "preface", "header_footer", "page_number"}
    printed_by_page: dict[int, int] = {}  # 页号 → 教材印刷页码（[页码=N] 标记）

    for page_no, page in enumerate(pages, 1):
        # 特殊块种类须在 NFKC 归一化前抓取：① 经 NFKC 变 "1"，与数字标题
        # 无法区分（取证实测：注释行「① …」归一化后被判成 heading）。
        # 归一化后文本相同的行共享种类——同文本同语义，无歧义。
        line_kinds: dict[str, str] = {}
        lines: list[str] = []
        for raw_line in page.splitlines():
            norm = _norm(raw_line)
            if not norm:
                continue
            kind = ""
            if _ANNOTATION_RE.match(raw_line.strip()):
                kind = "annotation"
            elif _is_vocabulary_line(raw_line):
                kind = "vocabulary"
            if kind:
                line_kinds[norm] = kind
            lines.append(norm)
        marker_count = page.count("􀆺") + page.count("……") + page.count("…")
        numeric_lines = sum(1 for line in lines if re.fullmatch(r"[0-9０-９]+", line))
        early_front_matter = page_no <= max(20, int(len(pages) * 0.15))
        compact_head = re.sub(r"\s+", "", "".join(lines[:8]))
        if early_front_matter and (any(_PREFACE_RE.search(line) for line in lines[:8])
                                   or compact_head.startswith(("前言", "序言", "内容提要", "编者的话"))):
            preface_region = True
        if not early_front_matter and toc_region:
            toc_region = False
        if early_front_matter and (_TOC_RE.search(page) or marker_count >= 8
                                   or (len(lines) >= 20 and numeric_lines >= 6)):
            toc_region = True
            preface_region = False
        elif toc_region and marker_count == 0 and numeric_lines <= 1 and any(len(x) > 30 for x in lines):
            toc_region = False

        blocks: list[tuple[str, str, list[str]]] = []
        normalized_page, raw_map = _normalize_with_raw_map(page)
        source_cursor = 0
        # 印刷页码标记（OCR prompt v2 / 原生收割产出）：位置容错——标记可在页首
        # 独立行、行尾甚至句中；剥离标记子串（行内其余正文保留），取该页最后
        # 一个标记数字进 metadata（printed_page），不参与切块正文。剥离后为空的
        # 行整行丢弃。
        stripped_lines: list[str] = []
        printed_marker: list[int] = []

        def _take_marker(m: "re.Match[str]") -> str:
            printed_marker.append(int(m.group(1)))
            return ""

        for line in lines:
            if _PRINTED_PAGE_RE.match(line):
                _PRINTED_PAGE_INLINE_RE.sub(_take_marker, line)
                continue
            if _PRINTED_PAGE_INLINE_RE.search(line):
                line = _PRINTED_PAGE_INLINE_RE.sub(_take_marker, line).strip()
                if not line:
                    continue
            stripped_lines.append(line)
        if printed_marker:
            printed_by_page[page_no] = printed_marker[-1]
        lines = _repair_fragmented_lines(stripped_lines, repeated)
        current: list[str] = []
        in_special = ""  # 标记块内："" / "figtab"（[图|/[表| 从属）/ "annotation" / "vocabulary"
        for line in lines:
            special_kind = ""
            if _FIGURE_MARK_RE.match(line) or _TABLE_MARK_RE.match(line):
                special_kind = "figtab"
            elif line_kinds.get(line):
                special_kind = line_kinds[line]
            # 注释/词表行绝不被当标题（归一化后「① …」形似「1 …」数字标题）。
            heading = _is_heading(line) and not special_kind
            continues_special = False
            if in_special == "figtab":
                continues_special = bool(
                    _SPECIAL_CONT_RE.match(line) or "|" in line or "｜" in line)
            elif in_special == "annotation":
                continues_special = line_kinds.get(line) == "annotation"
            elif in_special == "vocabulary":
                continues_special = line_kinds.get(line) == "vocabulary"
            # [图|/[表| 每个标记自成一块；注释/词表同种类连续行合为一块
            #（①②③… 逐行注释是一组），种类切换才开新块。
            starts_new_block = (heading
                                or special_kind == "figtab"
                                or (special_kind and special_kind != in_special)
                                or (in_special and not continues_special))
            if starts_new_block and current:
                piece = "\n".join(current).strip()
                flags = _noise_flags(piece, repeated=piece in repeated)
                if toc_region:
                    flags = list(dict.fromkeys(flags + ["toc"]))
                elif preface_region:
                    flags = list(dict.fromkeys(flags + ["preface"]))
                blocks.append((piece, "paragraph", flags))
                current = []
                in_special = ""
            if heading:
                flags = _noise_flags(line, repeated=line in repeated)
                if toc_region:
                    flags = list(dict.fromkeys(flags + ["toc"]))
                elif preface_region:
                    flags = list(dict.fromkeys(flags + ["preface"]))
                blocks.append((line, "heading", flags))
            else:
                if special_kind:
                    in_special = special_kind
                current.append(line)
        if current:
            piece = "\n".join(current).strip()
            flags = _noise_flags(piece, repeated=piece in repeated)
            if toc_region:
                flags = list(dict.fromkeys(flags + ["toc"]))
            elif preface_region:
                flags = list(dict.fromkeys(flags + ["preface"]))
            blocks.append((piece, "paragraph", flags))

        for block_text, kind, flags in blocks:
            if kind == "heading":
                title = block_text
                heading_level = _heading_level(title)
                noisy_heading = bool(set(flags) & structural_noise)
                if noisy_heading:
                    item_path = (section_stack + [title])[-3:]
                else:
                    effective_level = min(heading_level, len(section_stack) + 1)
                    section_stack = section_stack[:effective_level - 1] + [title]
                    item_path = section_stack[-3:]
                pieces = [(title, "heading", heading_level, not noisy_heading, item_path)]
            else:
                first_line = block_text.split("\n", 1)[0].strip()
                block_kind = line_kinds.get(first_line, "")
                block_type = block_kind or _block_type(block_text)
                pieces = [(piece, block_type, None, False, section_stack[-3:])
                          for piece in _split_piece(block_text, block_type)]

            for piece, block_type, heading_level, structural_heading, item_path in pieces:
                pos = normalized_page.find(piece, source_cursor)
                if pos < 0:
                    pos = normalized_page.find(piece)
                mapping_exact = pos >= 0
                if pos < 0:
                    pos = source_cursor
                normalized_end = min(len(normalized_page), pos + len(piece))
                raw_start, raw_end = _raw_offsets(pos, normalized_end, raw_map, len(page))
                source_cursor = max(source_cursor, normalized_end)
                raw.append({
                    "text": piece, "page": page_no if multi else None,
                    "block_type": block_type, "noise_flags": flags,
                    "section_path": list(item_path), "heading_level": heading_level,
                    "structural_heading": structural_heading,
                    "source_start": raw_start, "source_end": raw_end,
                    "normalized_start": pos, "normalized_end": normalized_end,
                    "mapping_exact": mapping_exact,
                    "normalization_changed": normalized_page != page,
                })

    chunks: list[Chunk] = []
    starts_new_semantic_block = {"heading", "figure", "definition", "theorem",
                                 "example", "exercise", "solution", "summary",
                                 "metadata", "annotation", "vocabulary"}
    for item in raw:
        piece = item["text"]
        if not piece:
            continue
        # 图/表块保持原子性：既不并入前块，也不接收后续段落打包。
        prev_types = set((chunks[-1].metadata.get("block_types") or [])
                         if chunks else [])
        can_pack = (chunks and chunks[-1].page == item["page"]
                    and item["block_type"] not in starts_new_semantic_block
                    and not item["noise_flags"]
                    and not prev_types & {"figure", "table", "annotation",
                                          "vocabulary"}
                    and not chunks[-1].metadata.get("hard_boundary"))
        if can_pack:
            candidate = f"{chunks[-1].text}\n{piece}"
            if estimate_model_tokens(candidate) <= SOFT_TOKEN_LIMIT:
                chunks[-1].text = candidate
                chunks[-1].tokens = tokenize(candidate)
                chunks[-1].metadata.setdefault("block_types", []).append(item["block_type"])
                chunks[-1].metadata["source_end"] = item.get("source_end")
                chunks[-1].metadata["normalized_end"] = item.get("normalized_end")
                chunks[-1].metadata["mapping_exact"] = bool(
                    chunks[-1].metadata.get("mapping_exact") and item.get("mapping_exact"))
                chunks[-1].metadata["token_estimate"] = estimate_model_tokens(candidate)
                continue
        idx = len(chunks)
        chunk_id = f"{file_id or source}#{idx}"
        chunk = Chunk(chunk_id=chunk_id, source=source, text=piece, index=idx,
                      tokens=tokenize(piece), file_id=file_id, page=item["page"])
        chunk.metadata.update({
            "chunk_schema": CHUNK_SCHEMA_VERSION,
            "block_types": [item["block_type"]],
            "noise_flags": list(item["noise_flags"]),
            "section_path": item["section_path"],
            "heading_level": item.get("heading_level"),
            "structural_heading": bool(item.get("structural_heading")),
            "page_range": ([item["page"], item["page"]] if item["page"] else None),
            "source_page": item["page"],
            "printed_page": (printed_by_page.get(int(item["page"]))
                             if item.get("page") else None),
            "source_start": item.get("source_start"),
            "source_end": item.get("source_end"),
            "normalized_start": item.get("normalized_start"),
            "normalized_end": item.get("normalized_end"),
            "mapping_basis": "nfkc_with_raw_offset_map",
            "mapping_exact": bool(item.get("mapping_exact")),
            "normalization_changed": bool(item.get("normalization_changed")),
            "normalized_nfkc": True,
            "token_estimate": estimate_model_tokens(piece),
            "hard_boundary": (item["block_type"] == "heading"
                              or item["block_type"] in {"figure", "table"}
                              or bool(item["noise_flags"])),
        })
        chunks.append(chunk)

    heading_stack: dict[int, str] = {}
    current_lesson: str | None = None
    for idx, chunk in enumerate(chunks):
        metadata = chunk.metadata
        if metadata.get("structural_heading"):
            level = int(metadata.get("heading_level") or 3)
            if level <= 2:
                current_lesson = None  # 章/单元边界：课文归属重置
            title = _norm(chunk.text)
            if current_lesson is None and _LESSON_TITLE_RE.match(title):
                # 课题标题（1 春 / 第10课 背影）＝课文级父文档锚点：本 chunk
                # 标 is_lesson，其后同课文的 chunk 都带 lesson 字段，供检索
                # 面包屑与课文级分组（父子 chunk 地基，parent_id 已有）。
                current_lesson = title
                metadata["is_lesson"] = True
            parent_levels = [depth for depth in heading_stack if depth < level]
            metadata["parent_id"] = heading_stack[max(parent_levels)] if parent_levels else None
            heading_stack = {depth: cid for depth, cid in heading_stack.items() if depth < level}
            heading_stack[level] = chunk.chunk_id
        else:
            metadata["parent_id"] = heading_stack[max(heading_stack)] if heading_stack else None
        metadata["lesson"] = current_lesson
        metadata["prev_id"] = chunks[idx - 1].chunk_id if idx else None
        metadata["next_id"] = chunks[idx + 1].chunk_id if idx + 1 < len(chunks) else None
        metadata["content_sha256"] = hashlib.sha256(chunk.text.encode()).hexdigest()
    # v2.2：索引 token 带面包屑（书名·课题·章节·页码，见 retriever.retrievable_text）
    # ——正文 chunk 对课题查询的词面覆盖从零恢复；展示文本 chunk.text 不变。
    # v2.3 准入门（P9）：乱码文本层的 chunk 不生成索引 token——mojibake 永不
    # 进入 BM25（检索侧另有同源运行时排除兜底存量索引），chunk 本体保留在
    # 存储里供 knowledge_read 显式读取与人工审计（staging 从 metadata 计数）。
    from .text_quality import text_garble_ratio
    for chunk in chunks:
        if text_garble_ratio(retrievable_text(chunk)) >= 0.05:
            chunk.tokens = []
            chunk.metadata["garble_excluded"] = True
        else:
            chunk.tokens = tokenize(retrievable_text(chunk))
    return chunks


def active_chunk_schema() -> str:
    from .config import settings
    return CHUNK_SCHEMA_VERSION if settings.rag_chunker_mode == "v2" else "legacy-v1"


def chunk_text_for_rag(text: str, source: str, file_id: str = "") -> list[Chunk]:
    from .config import settings
    if settings.rag_chunker_mode == "v2":
        return chunk_text_v2(text, source, file_id)
    from .retriever import chunk_text
    return chunk_text(text, source=source, file_id=file_id)


def chunks_from_meta(text: str, source: str, file_id: str,
                     meta: dict | None) -> list[Chunk]:
    """按文件元数据的 ``chunk_schema`` 选分块器（重载/恢复路径的统一入口）。

    标记为 structured-v* 的文件用 V2 结构化分块（页边界/标题硬边界/保护块），
    未打标的旧文件保持 V1 定长行为——禁止把已结构化的文件静默退回暴力分块。
    """
    schema = str((meta or {}).get("chunk_schema") or "")
    if schema.startswith("structured-v") and text.strip():
        return chunk_text_v2(text, source=source, file_id=file_id)
    from .retriever import chunk_text
    return chunk_text(text, source=source, file_id=file_id)
